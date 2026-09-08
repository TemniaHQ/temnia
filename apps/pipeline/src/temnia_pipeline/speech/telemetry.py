"""Best-effort resource sampling around synchronous native model inference."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from temnia_pipeline.speech.contracts import ProgressValue, StageTelemetry

MAX_PROGRESS_PERCENT = 100.0

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

_MIB = 1024 * 1024


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
        self._interval = interval
        self._requested_batch = requested_batch
        self._effective_batch = requested_batch
        self._phase_seconds: dict[str, float] = {}
        self._progress: float | None = None
        self._rss: int | None = None
        self._peak_rss: int | None = None
        self._gpu_used: int | None = None
        self._gpu_total: int | None = None
        self._pid_gpu: int | None = None
        self._gpu_name: str | None = None
        self._torch_alloc: int | None = None
        self._torch_reserved: int | None = None
        self._samples = 0
        self._unavailable: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_progress = on_progress
        self._inference_completed = False

    @property
    def progress_callback(self) -> Any:  # noqa: ANN401
        """WhisperX callback that accepts its documented float percentage."""

        def update(value: float) -> None:
            measured = float(value)
            if not math.isfinite(measured) or not 0.0 <= measured <= MAX_PROGRESS_PERCENT:
                message = f"WhisperX returned invalid progress {value!r}"
                raise ValueError(message)
            self._progress = max(self._progress or 0.0, measured)
            if self._on_progress is not None:
                self._on_progress(self._progress)

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
                    "--query-gpu=memory.used,memory.total,name",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            used, total, name = result.stdout.strip().split(",", 2)
            self._gpu_used = max(self._gpu_used or 0, int(used.strip()) * _MIB)
            self._gpu_total = int(total.strip()) * _MIB
            self._gpu_name = name.strip()
        except Exception:  # noqa: BLE001
            self._unavailable.add("nvidia_smi_gpu")
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
            gpu_used_peak_bytes=self._gpu_used,
            gpu_total_bytes=self._gpu_total,
            pid_gpu_used_peak_bytes=self._pid_gpu,
            torch_allocated_peak_bytes=self._torch_alloc,
            torch_reserved_peak_bytes=self._torch_reserved,
            gpu_name=self._gpu_name,
            sample_interval_seconds=self._interval,
            sample_count=self._samples,
            metrics_unavailable=sorted(self._unavailable),
            complete=complete,
        )
