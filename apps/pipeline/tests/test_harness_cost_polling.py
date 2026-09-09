"""A delayed charge is polled finitely without another inference request."""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001

from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import httpx
import pytest
from pydantic_ai import ModelResponse, TextPart

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import models
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.gateway import (
    GatewayConfig,
    GenerationIdentityError,
    observe_generation_cost,
)
from temnia_pipeline.harness.routes import RouteEligibility, RouteEntry, RoutePrices

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store


def _route() -> RouteEntry:
    return RouteEntry(
        id="test-route",
        gateway_model="test/model",
        family="test-family",
        provider="test-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=8192,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 9),
        ),
        prices=RoutePrices(input=1, output=1),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


def _receipt(**changes: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "id": "known-generation",
                "model": "test/model",
                "provider_name": "test-provider",
                "is_byok": False,
                "total_cost": "0.0005739",
                **changes,
            }
        },
    )


async def test_pending_charge_is_observed_using_only_exact_generation_gets() -> None:
    calls: list[httpx.Request] = []
    clock = Clock()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404) if len(calls) == 1 else _receipt()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        observed = await observe_generation_cost(
            client,
            config=GatewayConfig(api_key="test-only"),
            route=_route(),
            generation_id="known-generation",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert observed.status == "reported"
    assert observed.actual_cost_micros == 574
    assert clock.sleeps == [2.0]
    assert len(calls) == 2
    assert all(request.method == "GET" for request in calls)
    assert all(request.url.path == "/v1/generation" for request in calls)
    assert all(request.url.params["id"] == "known-generation" for request in calls)


@pytest.mark.parametrize(("wait", "expected_calls"), [(0.0, 1), (3.0, 2)])
async def test_unavailable_charge_remains_unknown_at_deadline(
    wait: float, expected_calls: int
) -> None:
    calls = 0
    clock = Clock()

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        observed = await observe_generation_cost(
            client,
            config=GatewayConfig(api_key="test-only"),
            route=_route(),
            generation_id="known-generation",
            wait_seconds=wait,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert observed.status == "pending"
    assert observed.actual_cost_micros is None
    assert calls == expected_calls
    assert clock.now == wait


@pytest.mark.parametrize("status", ["byok", "wrong-identity", "http-error"])
async def test_only_pending_receipts_permit_polling(status: str) -> None:
    calls = 0
    clock = Clock()

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if status == "http-error":
            return httpx.Response(503)
        return _receipt(is_byok=True) if status == "byok" else _receipt(id="foreign-generation")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        operation = observe_generation_cost(
            client,
            config=GatewayConfig(api_key="test-only"),
            route=_route(),
            generation_id="known-generation",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        if status == "byok":
            observed = await operation
            assert observed.status == "byok_unknown"
            assert observed.actual_cost_micros is None
        else:
            error_type = (
                GenerationIdentityError if status == "wrong-identity" else httpx.HTTPStatusError
            )
            with pytest.raises(error_type):
                await operation
    assert calls == 1
    assert not clock.sleeps


async def test_pending_poll_deadline_cancels_a_stalled_lookup() -> None:
    calls = 0
    drained = asyncio.Event()

    async def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(404)
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()
        pytest.fail("stalled request escaped cancellation")

    async def immediate_sleep(_duration: float) -> None:
        return

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        observed = await observe_generation_cost(
            client,
            config=GatewayConfig(api_key="test-only"),
            route=_route(),
            generation_id="known-generation",
            wait_seconds=0.03,
            sleep=immediate_sleep,
        )
    assert calls == 2
    assert drained.is_set()
    assert observed.status == "pending"
    assert observed.actual_cost_micros is None


async def test_cancellation_does_not_start_another_lookup() -> None:
    calls = 0
    started = asyncio.Event()
    drained = asyncio.Event()

    async def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()
        pytest.fail("cancelled request returned")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        pending = asyncio.create_task(
            observe_generation_cost(
                client,
                config=GatewayConfig(api_key="test-only"),
                route=_route(),
                generation_id="known-generation",
            )
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert calls == 1
    assert drained.is_set()


async def test_first_lookup_has_a_total_timeout_and_is_not_retried() -> None:
    calls = 0
    drained = asyncio.Event()

    async def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()
        pytest.fail("timed-out first lookup returned")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout, match="bounded time limit"):
            await observe_generation_cost(
                client,
                config=GatewayConfig(api_key="test-only", lookup_timeout_seconds=0.03),
                route=_route(),
                generation_id="known-generation",
            )
    assert calls == 1
    assert drained.is_set()


async def test_first_lookup_elapsed_time_is_part_of_total_wait() -> None:
    clock = Clock()
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock.now += 2.0
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        observed = await observe_generation_cost(
            client,
            config=GatewayConfig(api_key="test-only"),
            route=_route(),
            generation_id="known-generation",
            wait_seconds=3.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert observed.status == "pending"
    assert calls == 1
    assert clock.sleeps == [1.0]
    assert clock.now == 3.0


@pytest.mark.parametrize("failure", ["http", "identity"])
async def test_runtime_retains_safe_lookup_failure_without_accepting_a_charge(
    tmp_path: Path, failure: str
) -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(503, text="private-provider-detail")
            if failure == "http"
            else _receipt(id="foreign-generation")
        )

    deps = models.HarnessModelDeps(
        scope=Scope(organizationId=uuid4(), userId=uuid4()),
        source_id=uuid4(),
        run_id=uuid4(),
        stage="summary:test",
        program_version="test-v1",
        prompt_version="test-v1",
        schema_version="hierarchical-summary/1",
        route=_route(),
        operation_inputs={},
        operation_config={},
        dispatch_limit=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        observed = await models._observe_cost(
            models.ModelRuntime(
                database_url="unused",
                store=cast("S3Store", None),
                cassette_store=CassetteStore(tmp_path),
                gateway=GatewayConfig(api_key="test-only"),
                lookup_client=client,
            ),
            deps,
            ModelResponse(
                parts=[TextPart("retained response")], provider_response_id="known-generation"
            ),
        )
    assert observed is not None
    assert observed.status == "pending"
    assert observed.actual_cost_micros is None
    assert observed.components == {
        "generationId": "known-generation",
        "reason": "lookup_error",
        "errorType": "HTTPStatusError" if failure == "http" else "GenerationIdentityError",
    }
    assert calls == 1
