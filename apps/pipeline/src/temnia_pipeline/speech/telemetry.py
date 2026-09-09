"""Best-effort resource sampling around synchronous native model inference."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from temnia_pipeline.speech.contracts import (
    ProgressTransportTelemetry,
    ProgressValue,
    StageTelemetry,
)

MAX_CALLBACK_PROGRESS_PERCENT = 100.0
MAX_RECORDED_PROGRESS_PERCENT = 99.0
MAX_GPU_UTILIZATION_PERCENT = 100

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from temnia_pipeline.speech.progress_transport import ProgressPublishDiagnostics

_MIB = 1024 * 1024
_MICROSECONDS_PER_SECOND = 1_000_000
_CGROUP_CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")


@dataclass(frozen=True, slots=True)
class _CgroupCpuSnapshot:
    usage_microseconds: int
    throttled_microseconds: int
    throttled_count: int


def _read_cgroup_cpu() -> _CgroupCpuSnapshot | None:
    try:
        fields = {
            name: int(value)
            for name, value in (
                line.split(maxsplit=1) for line in _CGROUP_CPU_STAT.read_text().splitlines()
            )
        }
        snapshot = _CgroupCpuSnapshot(
            usage_microseconds=fields["usage_usec"],
            throttled_microseconds=fields["throttled_usec"],
            throttled_count=fields["nr_throttled"],
        )
        if (
            min(
                snapshot.usage_microseconds,
                snapshot.throttled_microseconds,
                snapshot.throttled_count,
            )
            < 0
        ):
            return None
    except (OSError, KeyError, ValueError):
        return None
    else:
        return snapshot


class TelemetryRecorder:
    """Collect timings, callbacks, and resource peaks without failing inference."""

    def __init__(
        self,
        *,
        requested_batch: int | None = None,
        interval: float = 1.0,
        on_progress: Callable[[float], None] | None = None,
    ) -> None:
        self._started_wall = datetime.now(UTC)
        self._started = time.monotonic()
        self._process_cpu_started = time.process_time()
        self._cgroup_cpu_started = _read_cgroup_cpu()
        self._interval = interval
        self._requested_batch = requested_batch
        self._effective_batch = requested_batch
        self._phase_seconds: dict[str, float] = {}
        self._progress: float | None = None
        self._rss: int | None = None
        self._peak_rss: int | None = None
        self._gpu_used: int | None = None
        self._gpu_total: int | None = None
        self._gpu_utilization: int | None = None
        self._pid_gpu: int | None = None
        self._gpu_name: str | None = None
        self._torch_alloc: int | None = None
        self._torch_reserved: int | None = None
        self._samples = 0
        self._unavailable: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_progress = on_progress
        self._progress_transport: ProgressTransportTelemetry | None = None
        self._inference_completed = False
        if self._cgroup_cpu_started is None:
            self._unavailable.add("cgroup_cpu")

    @property
    def progress_callback(self) -> Any:  # noqa: ANN401
        """WhisperX callback that accepts its documented float percentage."""

        def update(value: float) -> None:
            if self._on_progress is not None:
                try:
                    self._on_progress(value)
                except Exception:  # noqa: BLE001 -- telemetry cannot fail inference
                    self._unavailable.add("progress_transport_callback")
            try:
                measured = float(value)
            except (TypeError, ValueError, OverflowError):
                self._unavailable.add("invalid_progress_callback")
                return
            if not math.isfinite(measured) or not 0.0 <= measured <= MAX_CALLBACK_PROGRESS_PERCENT:
                self._unavailable.add("invalid_progress_callback")
                return
            self._progress = max(
                self._progress or 0.0,
                min(measured, MAX_RECORDED_PROGRESS_PERCENT),
            )

        return update

    @property
    def inference_completed(self) -> bool:
        """Whether the inference phase returned normally before any later failure."""
        return self._inference_completed

    @contextmanager
    def phase(self, name: str) -> Generator[None]:
        """Measure one named phase even when it raises."""
        started = time.monotonic()
        completed = False
        try:
            yield
            completed = True
        finally:
            self._phase_seconds[name] = self._phase_seconds.get(name, 0.0) + (
                time.monotonic() - started
            )
            if name == "inference" and completed:
                self._inference_completed = True

    def start(self, torch_module: Any | None = None) -> None:  # noqa: ANN401
        """Reset Torch peaks and start a daemon sampler."""
        if torch_module is not None:
            try:
                torch_module.cuda.reset_peak_memory_stats()
            except Exception:  # noqa: BLE001
                self._unavailable.add("torch_peak_reset")
        self._thread = threading.Thread(
            target=self._sample_loop,
            args=(torch_module,),
            name="speech-resource-sampler",
            daemon=True,
        )
        self._thread.start()

    def mark_unavailable(self, reason: str) -> None:
        """Explain why a metric envelope cannot describe original execution."""
        self._unavailable.add(reason)

    def set_progress_callback(self, callback: Callable[[float], None] | None) -> None:
        """Attach progress delivery after admission checks have accepted execution."""
        self._on_progress = callback

    def attach_progress_diagnostics(self, diagnostics: ProgressPublishDiagnostics) -> None:
        """Attach the publisher's frozen sanitized diagnostics before finishing."""
        self._progress_transport = ProgressTransportTelemetry.model_validate(asdict(diagnostics))

    def stop(self) -> None:
        """Stop the sampler promptly."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self._interval * 2))
            if self._thread.is_alive():
                self._unavailable.add("sampler_stop_timeout")

    def _sample_loop(self, torch_module: Any | None) -> None:  # noqa: ANN401
        while not self._stop.is_set():
            self._sample_once(torch_module)
            self._stop.wait(self._interval)

    def _sample_once(self, torch_module: Any | None) -> None:  # noqa: ANN401
        self._samples += 1
        try:
            status = open("/proc/self/status", encoding="utf-8").read()  # noqa: PTH123, SIM115
            fields = dict(
                line.split(":", 1)
                for line in status.splitlines()
                if line.startswith(("VmRSS:", "VmHWM:"))
            )
            self._rss = int(fields["VmRSS"].split()[0]) * 1024
            self._peak_rss = max(self._peak_rss or 0, int(fields["VmHWM"].split()[0]) * 1024)
        except Exception:  # noqa: BLE001
            self._unavailable.add("process_rss")
        try:
            result = subprocess.run(
                [
                    "/usr/bin/nvidia-smi",
                    "--query-gpu=memory.used,memory.total,utilization.gpu,name",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            used, total, utilization, name = result.stdout.strip().split(",", 3)
            self._gpu_used = max(self._gpu_used or 0, int(used.strip()) * _MIB)
            self._gpu_total = int(total.strip()) * _MIB
            self._gpu_name = name.strip()
            try:
                measured_utilization = int(utilization.strip())
                if not 0 <= measured_utilization <= MAX_GPU_UTILIZATION_PERCENT:
                    raise ValueError  # noqa: TRY301
                self._gpu_utilization = max(
                    self._gpu_utilization or 0,
                    measured_utilization,
                )
            except ValueError:
                self._unavailable.add("nvidia_smi_gpu_utilization")
        except Exception:  # noqa: BLE001
            self._unavailable.add("nvidia_smi_gpu")
            self._unavailable.add("nvidia_smi_gpu_utilization")
        try:
            result = subprocess.run(
                [
                    "/usr/bin/nvidia-smi",
                    "--query-compute-apps=pid,used_gpu_memory",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            matched = [
                int(memory.strip()) * _MIB
                for line in result.stdout.splitlines()
                for pid, memory in [line.split(",", 1)]
                if int(pid.strip()) == os.getpid()
            ]
            if matched:
                self._pid_gpu = max(self._pid_gpu or 0, *matched)
            else:
                self._unavailable.add("nvidia_smi_pid")
        except Exception:  # noqa: BLE001
            self._unavailable.add("nvidia_smi_pid")
        if torch_module is not None:
            try:
                self._torch_alloc = max(
                    self._torch_alloc or 0, int(torch_module.cuda.max_memory_allocated())
                )
                self._torch_reserved = max(
                    self._torch_reserved or 0, int(torch_module.cuda.max_memory_reserved())
                )
            except Exception:  # noqa: BLE001
                self._unavailable.add("torch_peaks")
        else:
            self._unavailable.add("torch_peaks")

    def finish(self, *, complete: bool) -> StageTelemetry:
        """Take a final sample and freeze the small telemetry envelope."""
        self.stop()
        elapsed = time.monotonic() - self._started
        process_cpu_seconds = max(0.0, time.process_time() - self._process_cpu_started)
        cgroup_cpu_usage_seconds: float | None = None
        cgroup_cpu_throttled_seconds: float | None = None
        cgroup_cpu_throttled_count: int | None = None
        cgroup_cpu_finished = _read_cgroup_cpu()
        if self._cgroup_cpu_started is not None and cgroup_cpu_finished is not None:
            usage = (
                cgroup_cpu_finished.usage_microseconds - self._cgroup_cpu_started.usage_microseconds
            )
            throttled = (
                cgroup_cpu_finished.throttled_microseconds
                - self._cgroup_cpu_started.throttled_microseconds
            )
            throttled_count = (
                cgroup_cpu_finished.throttled_count - self._cgroup_cpu_started.throttled_count
            )
            if min(usage, throttled, throttled_count) >= 0:
                cgroup_cpu_usage_seconds = usage / _MICROSECONDS_PER_SECOND
                cgroup_cpu_throttled_seconds = throttled / _MICROSECONDS_PER_SECOND
                cgroup_cpu_throttled_count = throttled_count
            else:
                self._unavailable.add("cgroup_cpu_counter_reset")
        else:
            self._unavailable.add("cgroup_cpu")
        progress = ProgressValue(
            percent=round(self._progress) if self._progress is not None else None,
            source="whisperx_callback" if self._progress is not None else "none",
        )
        return StageTelemetry(
            started_at=self._started_wall,
            finished_at=datetime.now(UTC),
            elapsed_seconds=elapsed,
            phase_seconds=self._phase_seconds,
            requested_batch=self._requested_batch,
            effective_batch=self._effective_batch,
            progress=progress,
            process_rss_bytes=self._rss,
            process_peak_rss_bytes=self._peak_rss,
            process_cpu_seconds=process_cpu_seconds,
            cgroup_cpu_usage_seconds=cgroup_cpu_usage_seconds,
            cgroup_cpu_throttled_seconds=cgroup_cpu_throttled_seconds,
            cgroup_cpu_throttled_count=cgroup_cpu_throttled_count,
            gpu_used_peak_bytes=self._gpu_used,
            gpu_total_bytes=self._gpu_total,
            gpu_utilization_peak_percent=self._gpu_utilization,
            pid_gpu_used_peak_bytes=self._pid_gpu,
            torch_allocated_peak_bytes=self._torch_alloc,
            torch_reserved_peak_bytes=self._torch_reserved,
            gpu_name=self._gpu_name,
            sample_interval_seconds=self._interval,
            sample_count=self._samples,
            metrics_unavailable=sorted(self._unavailable),
            progress_transport=self._progress_transport,
            complete=complete,
        )
