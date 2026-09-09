import asyncio
import threading
import time

import pytest

from temnia_pipeline.speech.progress_transport import (
    CoalescedProgressPublisher,
    SynchronousProgressPublisher,
)


def test_no_progress_and_repeated_start_close_are_safe() -> None:
    published: list[int] = []

    async def publish(percent: int) -> None:
        published.append(percent)

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=60,
        rpc_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.2,
    )
    publisher.start()
    publisher.start()
    diagnostics = publisher.close()
    assert published == []
    assert diagnostics.callback_count == 0
    assert diagnostics.publish_attempt_count == 0
    assert diagnostics.final_flush_completed
    assert not diagnostics.shutdown_timed_out
    assert publisher.close().publish_attempt_count == 0


def test_close_before_start_never_opens_a_transport() -> None:
    called = False

    async def publish(_percent: int) -> None:
        nonlocal called
        called = True

    publisher = CoalescedProgressPublisher(publish)
    publisher.callback(12)
    diagnostics = publisher.close()
    publisher.start()
    publisher.callback(13)
    diagnostics = publisher.close()
    assert not called
    assert diagnostics.callback_count == 2
    assert diagnostics.callback_after_close_count == 1
    assert diagnostics.pending_percent == 12
    assert not diagnostics.final_flush_completed


def test_publisher_does_not_depend_on_the_callers_blocked_event_loop() -> None:
    published = threading.Event()
    publisher_thread_id: int | None = None
    callback_thread_id: int | None = None

    async def publish(_percent: int) -> None:
        nonlocal publisher_thread_id
        publisher_thread_id = threading.get_ident()
        published.set()

    async def run() -> None:
        nonlocal callback_thread_id
        publisher = CoalescedProgressPublisher(
            publish,
            cadence_seconds=0.01,
            rpc_timeout_seconds=0.1,
            shutdown_timeout_seconds=0.2,
        )
        publisher.start()
        callback_thread_id = threading.get_ident()
        publisher.callback(25)
        time.sleep(0.1)  # noqa: ASYNC251 -- deliberately block the caller's event loop
        assert published.is_set()
        assert publisher.close().publish_success_count == 1

    asyncio.run(run())
    assert publisher_thread_id is not None
    assert publisher_thread_id != callback_thread_id


def test_callback_coalesces_without_waiting_for_an_active_rpc() -> None:
    first_started = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    async def publish(percent: int) -> None:
        nonlocal active, maximum_active
        with state_lock:
            calls.append(percent)
            active += 1
            maximum_active = max(maximum_active, active)
        first_started.set()
        await asyncio.to_thread(release.wait)
        with state_lock:
            active -= 1

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=0.01,
        rpc_timeout_seconds=1,
        shutdown_timeout_seconds=1,
    )
    publisher.start()
    publisher.callback(10)
    assert first_started.wait(0.5)
    producer = threading.Thread(
        target=lambda: [publisher.callback(value) for value in range(11, 101)],
        daemon=True,
    )
    producer.start()
    producer.join(timeout=0.5)
    assert not producer.is_alive()
    assert calls == [10]
    release.set()
    diagnostics = publisher.close()
    assert calls == [10, 99]
    assert maximum_active == 1
    assert diagnostics.callback_count == 91
    assert diagnostics.publish_success_count == 2
    assert diagnostics.pending_percent is None


def test_progress_never_regresses_or_claims_completion() -> None:
    published: list[int] = []

    async def publish(percent: int) -> None:
        published.append(percent)

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=60,
        rpc_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.2,
    )
    publisher.start()
    for value in (40.0, 20.0, 99.6, 100.0, 98.0):
        publisher.callback(value)
    diagnostics = publisher.close()
    assert published == [99]
    assert diagnostics.accepted_callback_count == 3
    assert diagnostics.regressed_callback_count == 2
    assert diagnostics.last_published_percent == 99
    assert all(percent < 100 for percent in published)


def test_timeout_and_error_are_sanitized_and_the_latest_value_retries() -> None:
    class CredentialBearingRpcError(RuntimeError):
        pass

    success = threading.Event()
    calls: list[int] = []

    async def publish(percent: int) -> None:
        calls.append(percent)
        if len(calls) == 1:
            await asyncio.sleep(1)
        elif len(calls) == 2:
            message = "secret-token-must-not-escape"
            raise CredentialBearingRpcError(message)
        else:
            success.set()

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=0.01,
        rpc_timeout_seconds=0.02,
        shutdown_timeout_seconds=0.2,
    )
    publisher.start()
    publisher.callback(50)
    assert success.wait(0.5)
    diagnostics = publisher.close()
    assert calls == [50, 50, 50]
    assert diagnostics.publish_timeout_count == 1
    assert diagnostics.publish_error_count == 1
    assert diagnostics.publish_success_count == 1
    assert diagnostics.last_error == "publish_error:CredentialBearingRpcError"
    assert "secret" not in diagnostics.last_error


def test_close_flushes_immediately_and_ignores_invalid_or_late_values() -> None:
    published: list[int] = []

    async def publish(percent: int) -> None:
        published.append(percent)

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=60,
        rpc_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.2,
    )
    publisher.start()
    for value in (float("nan"), float("inf"), -1.0, 101.0):
        publisher.callback(value)
    publisher.callback(67)
    diagnostics = publisher.close()
    publisher.callback(80)
    diagnostics = publisher.close()
    assert published == [67]
    assert diagnostics.invalid_callback_count == 4
    assert diagnostics.callback_after_close_count == 1
    assert diagnostics.final_flush_completed
    assert diagnostics.callback_elapsed_seconds >= 0
    assert diagnostics.publish_elapsed_seconds >= 0
    assert diagnostics.elapsed_seconds >= 0


def test_shutdown_is_bounded_when_rpc_suppresses_cancellation() -> None:
    entered = threading.Event()
    release = threading.Event()

    async def publish(_percent: int) -> None:
        entered.set()
        while not release.is_set():
            try:
                await asyncio.sleep(0.002)
            except asyncio.CancelledError:
                continue

    publisher = CoalescedProgressPublisher(
        publish,
        cadence_seconds=0.01,
        rpc_timeout_seconds=0.02,
        shutdown_timeout_seconds=0.05,
    )
    publisher.start()
    publisher.callback(42)
    assert entered.wait(0.5)
    started = time.monotonic()
    diagnostics = publisher.close()
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert diagnostics.shutdown_timed_out
    release.set()
    publisher.close()


@pytest.mark.parametrize(
    "argument",
    [
        {"cadence_seconds": 0},
        {"rpc_timeout_seconds": -1},
        {"shutdown_timeout_seconds": float("nan")},
    ],
)
def test_timing_bounds_must_be_positive_and_finite(argument: dict[str, float]) -> None:
    async def publish(_percent: int) -> None:
        return

    with pytest.raises(ValueError, match="finite and greater than zero"):
        CoalescedProgressPublisher(publish, **argument)


def test_synchronous_control_records_inline_blocking_cost() -> None:
    published: list[int] = []

    def publish(percent: int) -> None:
        time.sleep(0.02)
        published.append(percent)

    publisher = SynchronousProgressPublisher(publish)
    publisher.start()
    publisher.start()
    started = time.monotonic()
    publisher.callback(25)
    callback_elapsed = time.monotonic() - started
    publisher.callback(10)
    publisher.callback(100)
    publisher.callback(float("nan"))
    diagnostics = publisher.close()
    publisher.callback(50)
    diagnostics = publisher.close()
    assert callback_elapsed >= 0.018
    assert published == [25, 99]
    assert diagnostics.callback_count == 5
    assert diagnostics.regressed_callback_count == 1
    assert diagnostics.invalid_callback_count == 1
    assert diagnostics.callback_after_close_count == 1
    assert diagnostics.publish_attempt_count == 2
    assert diagnostics.publish_success_count == 2
    assert diagnostics.publish_elapsed_seconds >= 0.036
    assert diagnostics.callback_elapsed_seconds >= diagnostics.publish_elapsed_seconds


def test_synchronous_control_sanitizes_timeout_and_error() -> None:
    class RpcWithCredentialsError(RuntimeError):
        pass

    attempts = 0

    def publish(_percent: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError
        message = "credential-value"
        raise RpcWithCredentialsError(message)

    publisher = SynchronousProgressPublisher(publish)
    publisher.start()
    publisher.callback(10)
    publisher.callback(20)
    diagnostics = publisher.close()
    assert diagnostics.publish_timeout_count == 1
    assert diagnostics.publish_error_count == 1
    assert diagnostics.last_error == "publish_error:RpcWithCredentialsError"
    assert "credential-value" not in diagnostics.last_error
