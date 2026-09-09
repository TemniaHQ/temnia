"""Temporal-only liveness supervision for long speech activities."""

# ruff: noqa: BLE001, C901, PLR0912

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any, cast

from temporalio import activity

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

HEARTBEAT_INTERVAL_SECONDS = 2.0
INVALID_HEARTBEAT_INTERVAL = "activity heartbeat interval must be positive"
EARLY_HEARTBEAT_STOP = "activity heartbeat stopped before its operation"


async def _heartbeat_until_stopped(
    stop: asyncio.Event,
    details: object,
    *,
    interval_seconds: float,
) -> None:
    """Heartbeat immediately and periodically without waiting on external I/O."""
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError(INVALID_HEARTBEAT_INTERVAL)
    while not stop.is_set():
        activity.heartbeat(details)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def _drain_owned(tasks: tuple[asyncio.Task[Any], ...], *, cancel: bool) -> bool:
    """Drain owned tasks and report cancellation delivered during the drain."""
    if cancel:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def gather() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    drain = asyncio.create_task(gather())
    current = asyncio.current_task()
    cancelled = False
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            cancelled = True
            if current is not None:
                current.uncancel()
    await drain
    return cancelled


async def run_with_activity_heartbeat[T](
    operation: Callable[[], Coroutine[Any, Any, T]],
    *,
    details: object,
    interval_seconds: float | None = None,
) -> T:
    """Run one operation under an owned, Temporal-only heartbeat loop.

    The operation is an owned child so activity cancellation can drain its
    provider and ledger finalizers before this function returns. A heartbeat
    failure cancels and drains the operation rather than leaving it detached.
    """
    interval = HEARTBEAT_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
    stop = asyncio.Event()
    operation_task: asyncio.Task[T] = asyncio.create_task(operation())
    heartbeat_task = asyncio.create_task(
        _heartbeat_until_stopped(stop, details, interval_seconds=interval)
    )
    owned: tuple[asyncio.Task[Any], ...] = (operation_task, heartbeat_task)
    result: T | None = None
    failure: BaseException | None = None
    try:
        done, _ = await asyncio.wait(owned, return_when=asyncio.FIRST_COMPLETED)
        if operation_task in done:
            try:
                result = operation_task.result()
            except BaseException as error:  # cancellation must retain its exact type
                failure = error
            if heartbeat_task in done:
                try:
                    heartbeat_task.result()
                except BaseException as error:
                    if failure is None:
                        failure = error
        else:
            try:
                heartbeat_task.result()
            except BaseException as error:
                failure = error
            else:
                failure = RuntimeError(EARLY_HEARTBEAT_STOP)
            if not operation_task.done() and not operation_task.cancelling():
                operation_task.cancel()
    except BaseException as error:  # cancellation is drained before it escapes
        failure = error
        if not operation_task.done() and not operation_task.cancelling():
            operation_task.cancel()
    finally:
        cancelled_during_operation_drain = await _drain_owned((operation_task,), cancel=False)
        stop.set()
        cancelled_during_heartbeat_drain = await _drain_owned((heartbeat_task,), cancel=False)
    if (cancelled_during_operation_drain or cancelled_during_heartbeat_drain) and failure is None:
        failure = asyncio.CancelledError()
    if heartbeat_task.done():
        try:
            heartbeat_task.result()
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure
    return cast("T", result)
