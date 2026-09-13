"""The worker admits provider calls per route: bounded in flight, spaced, keyed by route."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.models import ModelRuntime, RouteGate


async def test_gate_bounds_in_flight_calls_and_spaces_dispatches() -> None:
    gate = RouteGate(max_in_flight=2, min_interval_seconds=0.05)
    loop = asyncio.get_running_loop()
    in_flight = 0
    peak = 0
    starts: list[float] = []

    async def call() -> None:
        nonlocal in_flight, peak
        await gate.acquire()
        try:
            in_flight += 1
            peak = max(peak, in_flight)
            starts.append(loop.time())
            # Longer than the spacing, so admitted calls overlap up to the slot count.
            await asyncio.sleep(0.2)
        finally:
            in_flight -= 1
            gate.release()

    await asyncio.gather(*(call() for _ in range(4)))
    assert peak == 2
    gaps = [later - earlier for earlier, later in zip(starts, sorted(starts)[1:], strict=False)]
    assert all(gap >= 0.045 for gap in gaps)


async def test_runtime_keeps_one_gate_per_route_with_its_limits(tmp_path: Any) -> None:  # noqa: ANN401
    runtime = ModelRuntime(
        database_url="unused",
        store=cast("Any", None),
        cassette_store=CassetteStore(tmp_path),
        max_in_flight_per_route=3,
        min_dispatch_interval_seconds=0.5,
    )
    first = runtime.route_gate("route-a")
    assert runtime.route_gate("route-a") is first
    assert runtime.route_gate("route-b") is not first
    assert first.max_in_flight == 3
    assert first.min_interval_seconds == 0.5
    with pytest.raises(ValueError, match="positive slot count"):
        RouteGate(max_in_flight=0, min_interval_seconds=0)
