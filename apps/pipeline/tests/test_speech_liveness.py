"""Lifecycle guarantees for the speech activity heartbeat supervisor."""

# ruff: noqa: EM101, TRY003

from __future__ import annotations

import asyncio

import pytest

from temnia_pipeline.speech import liveness


async def test_activity_heartbeat_covers_quiet_tail_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[object] = []
    monkeypatch.setattr(liveness.activity, "heartbeat", beats.append)

    async def quiet_tail() -> str:
        await asyncio.sleep(0.08)
        return "ready"

    result = await liveness.run_with_activity_heartbeat(
        quiet_tail,
        details={"stage": "checkpointed_transcribe_v2"},
        interval_seconds=0.01,
    )
    count_at_return = len(beats)
    await asyncio.sleep(0.03)

    assert result == "ready"
    assert count_at_return >= 4
    assert len(beats) == count_at_return


async def test_activity_heartbeat_failure_cancels_and_drains_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned = asyncio.Event()

    def fail_heartbeat(_details: object) -> None:
        raise RuntimeError("heartbeat transport failed")

    monkeypatch.setattr(liveness.activity, "heartbeat", fail_heartbeat)

    async def operation() -> None:
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    with pytest.raises(RuntimeError, match="heartbeat transport failed"):
        await liveness.run_with_activity_heartbeat(
            operation,
            details="live",
            interval_seconds=0.01,
        )
    assert cleaned.is_set()


async def test_simultaneous_success_never_hides_heartbeat_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_heartbeat(_details: object) -> None:
        raise RuntimeError("heartbeat transport failed")

    monkeypatch.setattr(liveness.activity, "heartbeat", fail_heartbeat)

    async def immediate_success() -> str:
        return "must not escape"

    with pytest.raises(RuntimeError, match="heartbeat transport failed"):
        await liveness.run_with_activity_heartbeat(
            immediate_success,
            details="live",
            interval_seconds=0.01,
        )


async def test_operation_failure_precedes_simultaneous_heartbeat_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_heartbeat(_details: object) -> None:
        raise RuntimeError("heartbeat transport failed")

    monkeypatch.setattr(liveness.activity, "heartbeat", fail_heartbeat)

    async def immediate_failure() -> None:
        raise ValueError("operation failed")

    with pytest.raises(ValueError, match="operation failed"):
        await liveness.run_with_activity_heartbeat(
            immediate_failure,
            details="live",
            interval_seconds=0.01,
        )


async def test_activity_cancellation_drains_operation_after_repeated_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[object] = []

    def heartbeat(_details: object) -> None:
        beats.append(_details)

    monkeypatch.setattr(liveness.activity, "heartbeat", heartbeat)
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()

    async def operation() -> None:
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    supervised = asyncio.create_task(
        liveness.run_with_activity_heartbeat(
            operation,
            details="live",
            interval_seconds=0.01,
        )
    )
    await asyncio.sleep(0)
    supervised.cancel()
    await cleaning.wait()
    beats_at_cleanup = len(beats)
    await asyncio.sleep(0.04)
    assert len(beats) > beats_at_cleanup
    supervised.cancel()
    await asyncio.sleep(0)
    assert not supervised.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await supervised
    assert cleaned.is_set()


async def test_activity_heartbeat_stops_after_operation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[object] = []
    monkeypatch.setattr(liveness.activity, "heartbeat", beats.append)

    async def fail() -> None:
        await asyncio.sleep(0.03)
        raise ValueError("known failure")

    with pytest.raises(ValueError, match="known failure"):
        await liveness.run_with_activity_heartbeat(
            fail,
            details="live",
            interval_seconds=0.005,
        )
    count_at_failure = len(beats)
    await asyncio.sleep(0.02)
    assert len(beats) == count_at_failure


async def test_cancellation_during_normal_heartbeat_drain_replaces_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draining = asyncio.Event()
    release = asyncio.Event()

    async def delayed_stop(
        stop: asyncio.Event,
        _details: object,
        *,
        interval_seconds: float,
    ) -> None:
        del interval_seconds
        await stop.wait()
        draining.set()
        await release.wait()

    monkeypatch.setattr(liveness, "_heartbeat_until_stopped", delayed_stop)

    async def success() -> str:
        return "must be cancelled"

    supervised = asyncio.create_task(
        liveness.run_with_activity_heartbeat(
            success,
            details="live",
            interval_seconds=0.01,
        )
    )
    await draining.wait()
    supervised.cancel()
    await asyncio.sleep(0)
    assert not supervised.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await supervised


async def test_late_heartbeat_failure_replaces_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_on_stop(
        stop: asyncio.Event,
        _details: object,
        *,
        interval_seconds: float,
    ) -> None:
        del interval_seconds
        await stop.wait()
        raise RuntimeError("late heartbeat failure")

    monkeypatch.setattr(liveness, "_heartbeat_until_stopped", fail_on_stop)

    async def success() -> str:
        return "must not escape"

    with pytest.raises(RuntimeError, match="late heartbeat failure"):
        await liveness.run_with_activity_heartbeat(
            success,
            details="live",
            interval_seconds=0.01,
        )
