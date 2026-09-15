"""The decision activity on direct vendor routes: usage settles, drops retry, crashes recover."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from temporalio.exceptions import ApplicationError

from temnia_pipeline import db
from temnia_pipeline.harness import models, runs, topic_decisions
from temnia_pipeline.harness.qualification_topic_selection import (
    topic_selection_qualification_case,
)
from temnia_pipeline.harness.routes import (
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
    VendorAccountTerms,
)
from temnia_pipeline.harness.topic_decisions import DecisionRequest
from temnia_pipeline.harness.vendors import VendorKeys
from test_harness_runs import SEEDED, pipeline_url
from test_topic_decisions import _draft, _v8_run

if TYPE_CHECKING:
    from pathlib import Path

TERMS = VendorAccountTerms(zero_data_retention=True, recorded_at=date(2026, 9, 15), reference="t")


class _Crash(BaseException):
    """A worker death mid-call: not an Exception, so no handler settles the attempt."""


def _vendor_route(identifier: str, vendor: str, family: str) -> RouteEntry:
    return RouteEntry(
        id=identifier,
        gateway_model=f"{vendor}/{identifier}",
        family=family,
        provider=vendor,
        open_weight=False,
        context_tokens=200_000,
        max_output_tokens=16_384,
        prices=RoutePrices(input=1_000_000, output=2_000_000, cache_read=100_000),
        vendor=cast("Any", vendor),
        account=TERMS,
    )


def _snapshot(routes: tuple[RouteEntry, ...], seats: dict[str, SeatRoutePool]) -> RouteSnapshot:
    draft = RouteSnapshot.model_construct(
        version=1, snapshot_id="0" * 64, signature=None, synthetic=False, routes=routes, seats=seats
    )
    return RouteSnapshot(
        version=1,
        snapshot_id=draft.computed_id(),
        signature=None,
        synthetic=False,
        routes=routes,
        seats=seats,
    )


def _direct_snapshot() -> tuple[RouteSnapshot, RouteEntry, RouteEntry, RouteEntry]:
    author = _vendor_route("opus", "anthropic", "anthropic")
    verifier = _vendor_route("terra", "openai", "openai")
    inventory = _vendor_route("sonnet", "anthropic", "anthropic")
    flash = _vendor_route("flash", "google", "google")
    snapshot = _snapshot(
        (author, verifier, inventory, flash),
        {
            "propose": SeatRoutePool(route_ids=(author.id, flash.id)),
            "verify": SeatRoutePool(route_ids=(verifier.id, flash.id)),
            "inventory": SeatRoutePool(route_ids=(inventory.id, flash.id)),
        },
    )
    return snapshot, author, verifier, inventory


async def _attempts(url: str, run_id: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    async with db.scoped(url, SEEDED) as conn:
        rows = await (
            await conn.execute(
                """SELECT state, cost_status, estimated_cost_micros, actual_cost_micros, usage,
                          error_code
                     FROM harness_attempt WHERE run_id = %s ORDER BY created_at, id""",
                (run_id,),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def test_inventory_runs_on_its_seat_and_settles_from_usage(tmp_path: Path) -> None:
    url = pipeline_url()
    snapshot, _, _, inventory = _direct_snapshot()
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            return ModelResponse(
                parts=[TextPart(json.dumps(good))],
                model_name="fixture",
                provider_name="fixture",
                usage=RequestUsage(input_tokens=1000, cache_read_tokens=400, output_tokens=100),
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    try:
        decisions, context, source_id, run_id = await _v8_run(url, tmp_path, snapshot, factory)
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.gap is None
        assert result.artifact is not None
        assert calls == [inventory.id]
        assert result.family == inventory.family
        attempts = await _attempts(url, run_id)
        assert [item["state"] for item in attempts] == ["succeeded"]
        # 600 uncached at $1, 400 cache reads at $0.10, 100 output at $2 per million tokens.
        assert attempts[0]["actual_cost_micros"] == 600 + 40 + 200
        assert attempts[0]["usage"]["settlement"]["settlement"] == "usage"
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "running"
        assert row.spent_micros == 840
        assert row.reserved_micros == 0
        projection = row.route_snapshot.model_dump(mode="json")
        _ = projection
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_dropped_stream_settles_at_the_estimate_and_retries_without_a_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    snapshot, _, _, inventory = _direct_snapshot()
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            if len(calls) == 1:
                raise ModelAPIError(model_name=route.gateway_model, message="stream ended")
            return ModelResponse(
                parts=[TextPart(json.dumps(good))],
                model_name="fixture",
                provider_name="fixture",
                usage=RequestUsage(input_tokens=10, output_tokens=10),
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    monkeypatch.setattr(topic_decisions, "TRANSIENT_BACKOFF_SECONDS", (0.0, 0.0, 0.0, 0.0))
    try:
        decisions, context, source_id, run_id = await _v8_run(url, tmp_path, snapshot, factory)
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.artifact is not None
        assert calls == [inventory.id, inventory.id]
        attempts = await _attempts(url, run_id)
        assert [item["state"] for item in attempts] == ["failed_known", "succeeded"]
        dropped = attempts[0]
        assert dropped["error_code"] == "transport-dropped"
        assert dropped["actual_cost_micros"] == dropped["estimated_cost_micros"] > 0
        assert dropped["usage"]["settlement"] == "estimate"
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "running"
        assert row.reserved_micros == 0
        assert row.spent_micros == dropped["actual_cost_micros"] + attempts[1]["actual_cost_micros"]
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_dead_execution_is_abandoned_at_its_estimate_and_dispatched_again(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    snapshot, _, _, inventory = _direct_snapshot()
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            if len(calls) == 1:
                raise _Crash
            return ModelResponse(
                parts=[TextPart(json.dumps(good))],
                model_name="fixture",
                provider_name="fixture",
                usage=RequestUsage(input_tokens=10, output_tokens=10),
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    try:
        decisions, context, source_id, run_id = await _v8_run(url, tmp_path, snapshot, factory)
        request = DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        with pytest.raises(_Crash):
            await decisions.run_decision(request)
        stale = await _attempts(url, run_id)
        assert [item["state"] for item in stale] == ["dispatching"]
        result = await decisions.run_decision(request)
        assert result.artifact is not None
        assert calls == [inventory.id, inventory.id]
        attempts = await _attempts(url, run_id)
        assert [item["state"] for item in attempts] == ["failed_known", "succeeded"]
        assert attempts[0]["error_code"] == "abandoned-after-dispatch"
        assert attempts[0]["cost_status"] == "estimated"
        assert attempts[0]["actual_cost_micros"] == attempts[0]["estimated_cost_micros"]
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "running"
        assert row.reserved_micros == 0
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_keyless_vendor_is_skipped_then_named_when_no_route_remains(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    snapshot, _, verifier, _ = _direct_snapshot()
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            return ModelResponse(
                parts=[TextPart(json.dumps(good))],
                model_name="fixture",
                provider_name="fixture",
                usage=RequestUsage(input_tokens=10, output_tokens=10),
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    try:
        decisions, context, _, _ = await _v8_run(url, tmp_path, snapshot, factory)
        runtime = models.current_runtime()
        assert runtime is not None
        # No Anthropic key: inventory falls through to Gemini, the second route of its seat.
        models.configure_model_runtime(
            replace(runtime, vendor_keys=VendorKeys(openai="sk-o", google="sk-g"))
        )
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.artifact is not None
        assert calls == ["flash"]
        assert result.family == "google"
        # Without the OpenAI key either, no reviewer is left and the stop names the variable.
        models.configure_model_runtime(replace(runtime, vendor_keys=VendorKeys(google="sk-g")))
        with pytest.raises(ApplicationError) as stop:
            await decisions.run_decision(
                DecisionRequest(context=context, kind="inventory", item_id="section-0002")
            )
        assert stop.value.type == "NoEligibleRoute"
        assert "OPENAI_API_KEY" in stop.value.message
        _ = verifier
    finally:
        models.clear_model_runtime()
        await db.close_pool()
