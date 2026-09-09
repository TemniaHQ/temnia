"""Bounded best-effort publication for synchronous speech progress callbacks."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

MAX_PUBLISHED_PERCENT = 99
MAX_CALLBACK_PERCENT = 100
_MAX_ERROR_TYPE_LENGTH = 48

type AsyncPublish = Callable[[int], Awaitable[None]]
type SyncPublish = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ProgressPublishDiagnostics:
    """Small sanitized account of callback and publication behavior."""

    callback_count: int
    accepted_callback_count: int
    regressed_callback_count: int
    invalid_callback_count: int
    callback_after_close_count: int
    callback_elapsed_seconds: float
    publish_attempt_count: int
    publish_success_count: int
    publish_timeout_count: int
    publish_error_count: int
    publish_elapsed_seconds: float
    elapsed_seconds: float
    last_callback_seconds: float | None
    last_publish_seconds: float | None
    last_published_percent: int | None
    pending_percent: int | None
    last_error: str | None
    final_flush_completed: bool
    shutdown_timed_out: bool


class CoalescedProgressPublisher:
    """Publish the latest measured progress away from a blocked inference loop."""

    def __init__(
        self,
        publish: AsyncPublish,
        *,
        cadence_seconds: float = 5.0,
        rpc_timeout_seconds: float = 2.0,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        for name, value in (
            ("cadence_seconds", cadence_seconds),
            ("rpc_timeout_seconds", rpc_timeout_seconds),
            ("shutdown_timeout_seconds", shutdown_timeout_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                message = f"{name} must be finite and greater than zero"
                raise ValueError(message)
        self._publish = publish
        self._cadence_seconds = cadence_seconds
        self._rpc_timeout_seconds = rpc_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._created = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._callback_count = 0
        self._accepted_callback_count = 0
        self._regressed_callback_count = 0
        self._invalid_callback_count = 0
        self._callback_after_close_count = 0
        self._callback_elapsed_seconds = 0.0
        self._publish_attempt_count = 0
        self._publish_success_count = 0
        self._publish_timeout_count = 0
        self._publish_error_count = 0
        self._publish_elapsed_seconds = 0.0
        self._last_callback_seconds: float | None = None
        self._last_publish_seconds: float | None = None
        self._last_published_percent: int | None = None
        self._inflight_percent: int | None = None
        self._highest_accepted_percent: int | None = None
        self._pending_percent: int | None = None
        self._last_error: str | None = None
        self._final_flush_completed = False
        self._shutdown_timed_out = False

    def start(self) -> None:
        """Start the one publisher thread; repeated calls are harmless."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                name="speech-progress-publisher",
                daemon=True,
            )
            self._thread.start()

    def callback(self, value: object) -> None:
        """Retain one monotonic value without performing or awaiting network I/O."""
        callback_started = time.monotonic()
        with self._lock:
            self._callback_count += 1
            self._last_callback_seconds = callback_started - self._created
            if self._closed:
                self._callback_after_close_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self._invalid_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            measured = float(value)
            if not math.isfinite(measured) or not 0 <= measured <= MAX_CALLBACK_PERCENT:
                self._invalid_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            normalized = min(MAX_PUBLISHED_PERCENT, round(measured))
            if (
                self._highest_accepted_percent is not None
                and normalized < self._highest_accepted_percent
            ):
                self._regressed_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            self._accepted_callback_count += 1
            self._highest_accepted_percent = normalized
            already_accounted_for = (
                max(
                    value
                    for value in (
                        self._last_published_percent,
                        self._inflight_percent,
                        self._pending_percent,
                    )
                    if value is not None
                )
                if (
                    self._last_published_percent is not None
                    or self._inflight_percent is not None
                    or self._pending_percent is not None
                )
                else None
            )
            if already_accounted_for is None or normalized > already_accounted_for:
                self._pending_percent = normalized
            self._callback_elapsed_seconds += time.monotonic() - callback_started

    def close(self) -> ProgressPublishDiagnostics:
        """Request one final flush and return within the configured shutdown bound."""
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
            if not self._started:
                self._final_flush_completed = self._pending_percent is None
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=self._shutdown_timeout_seconds)
        with self._lock:
            if thread is not None and thread.is_alive():
                self._shutdown_timed_out = True
            return self._diagnostics_locked()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            while not self._stop.wait(self._cadence_seconds):
                self._publish_pending(loop)
            self._publish_pending(loop)
            with self._lock:
                self._final_flush_completed = self._pending_percent is None
        except Exception as error:  # noqa: BLE001 -- telemetry cannot fail inference
            with self._lock:
                self._publish_error_count += 1
                self._last_error = _error_code("publisher_error", error)
        finally:
            loop.close()

    def _publish_pending(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            percent = self._pending_percent
            self._pending_percent = None
            if percent is None:
                return
            self._inflight_percent = percent
            self._publish_attempt_count += 1
        started = time.monotonic()
        error_code: str | None = None
        timed_out = False
        try:
            loop.run_until_complete(
                asyncio.wait_for(
                    self._publish(percent),
                    timeout=self._rpc_timeout_seconds,
                )
            )
        except TimeoutError:
            timed_out = True
            error_code = "publish_timeout"
        except asyncio.CancelledError as error:
            error_code = _error_code("publish_error", error)
        except Exception as error:  # noqa: BLE001 -- network failures are diagnostic
            error_code = _error_code("publish_error", error)
        elapsed = time.monotonic() - started
        with self._lock:
            self._inflight_percent = None
            self._publish_elapsed_seconds += elapsed
            self._last_publish_seconds = time.monotonic() - self._created
            if error_code is None:
                self._publish_success_count += 1
                self._last_published_percent = max(
                    self._last_published_percent or 0,
                    percent,
                )
                return
            if timed_out:
                self._publish_timeout_count += 1
            else:
                self._publish_error_count += 1
            self._last_error = error_code
            if self._pending_percent is None or percent > self._pending_percent:
                self._pending_percent = percent

    def _diagnostics_locked(self) -> ProgressPublishDiagnostics:
        return ProgressPublishDiagnostics(
            callback_count=self._callback_count,
            accepted_callback_count=self._accepted_callback_count,
            regressed_callback_count=self._regressed_callback_count,
            invalid_callback_count=self._invalid_callback_count,
            callback_after_close_count=self._callback_after_close_count,
            callback_elapsed_seconds=self._callback_elapsed_seconds,
            publish_attempt_count=self._publish_attempt_count,
            publish_success_count=self._publish_success_count,
            publish_timeout_count=self._publish_timeout_count,
            publish_error_count=self._publish_error_count,
            publish_elapsed_seconds=self._publish_elapsed_seconds,
            elapsed_seconds=time.monotonic() - self._created,
            last_callback_seconds=self._last_callback_seconds,
            last_publish_seconds=self._last_publish_seconds,
            last_published_percent=self._last_published_percent,
            pending_percent=self._pending_percent,
            last_error=self._last_error,
            final_flush_completed=self._final_flush_completed,
            shutdown_timed_out=self._shutdown_timed_out,
        )


class SynchronousProgressPublisher:
    """Deliberately blocking publisher used only as an explicit benchmark control."""

    def __init__(self, publish: SyncPublish) -> None:
        self._publish = publish
        self._created = time.monotonic()
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._callback_count = 0
        self._accepted_callback_count = 0
        self._regressed_callback_count = 0
        self._invalid_callback_count = 0
        self._callback_after_close_count = 0
        self._callback_elapsed_seconds = 0.0
        self._publish_attempt_count = 0
        self._publish_success_count = 0
        self._publish_timeout_count = 0
        self._publish_error_count = 0
        self._publish_elapsed_seconds = 0.0
        self._last_callback_seconds: float | None = None
        self._last_publish_seconds: float | None = None
        self._last_published_percent: int | None = None
        self._highest_accepted_percent: int | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        """Enable publication; repeated starts are harmless."""
        with self._lock:
            if not self._closed:
                self._started = True

    def callback(self, value: object) -> None:
        """Perform the bounded injected call inline to measure its inference cost."""
        callback_started = time.monotonic()
        with self._lock:
            self._callback_count += 1
            self._last_callback_seconds = callback_started - self._created
            if self._closed:
                self._callback_after_close_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            if not self._started or isinstance(value, bool) or not isinstance(value, (int, float)):
                self._invalid_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            measured = float(value)
            if not math.isfinite(measured) or not 0 <= measured <= MAX_CALLBACK_PERCENT:
                self._invalid_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            normalized = min(MAX_PUBLISHED_PERCENT, round(measured))
            if (
                self._highest_accepted_percent is not None
                and normalized < self._highest_accepted_percent
            ):
                self._regressed_callback_count += 1
                self._callback_elapsed_seconds += time.monotonic() - callback_started
                return
            self._accepted_callback_count += 1
            self._highest_accepted_percent = normalized
            self._publish_attempt_count += 1
        publish_started = time.monotonic()
        error_code: str | None = None
        timed_out = False
        try:
            self._publish(normalized)
        except TimeoutError:
            timed_out = True
            error_code = "publish_timeout"
        except Exception as error:  # noqa: BLE001 -- control failures are diagnostic
            error_code = _error_code("publish_error", error)
        publish_elapsed = time.monotonic() - publish_started
        with self._lock:
            self._publish_elapsed_seconds += publish_elapsed
            self._last_publish_seconds = time.monotonic() - self._created
            if error_code is None:
                self._publish_success_count += 1
                self._last_published_percent = max(
                    self._last_published_percent or 0,
                    normalized,
                )
            elif timed_out:
                self._publish_timeout_count += 1
                self._last_error = error_code
            else:
                self._publish_error_count += 1
                self._last_error = error_code
            self._callback_elapsed_seconds += time.monotonic() - callback_started

    def close(self) -> ProgressPublishDiagnostics:
        """Close without additional publication and return compatible diagnostics."""
        with self._lock:
            self._closed = True
            return ProgressPublishDiagnostics(
                callback_count=self._callback_count,
                accepted_callback_count=self._accepted_callback_count,
                regressed_callback_count=self._regressed_callback_count,
                invalid_callback_count=self._invalid_callback_count,
                callback_after_close_count=self._callback_after_close_count,
                callback_elapsed_seconds=self._callback_elapsed_seconds,
                publish_attempt_count=self._publish_attempt_count,
                publish_success_count=self._publish_success_count,
                publish_timeout_count=self._publish_timeout_count,
                publish_error_count=self._publish_error_count,
                publish_elapsed_seconds=self._publish_elapsed_seconds,
                elapsed_seconds=time.monotonic() - self._created,
                last_callback_seconds=self._last_callback_seconds,
                last_publish_seconds=self._last_publish_seconds,
                last_published_percent=self._last_published_percent,
                pending_percent=None,
                last_error=self._last_error,
                final_flush_completed=True,
                shutdown_timed_out=False,
            )


def _error_code(prefix: str, error: BaseException) -> str:
    error_type = "".join(
        character
        for character in type(error).__name__
        if character.isascii() and (character.isalnum() or character == "_")
    )[:_MAX_ERROR_TYPE_LENGTH]
    return f"{prefix}:{error_type or 'Error'}"
