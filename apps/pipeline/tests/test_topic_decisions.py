"""One decision activity end to end: plan, project, dispatch through the ledger, admit, reuse."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.exceptions import ApplicationError

from qualification_fixtures import (
    _FixtureEncoder,  # pyright: ignore[reportPrivateUsage]
    _published_reviewer_refs,  # pyright: ignore[reportPrivateUsage]
)
from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterRunInput,
    ChapterRunRoutePreferences,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
)
from temnia_pipeline.harness import artifacts, gateway, models, receipts, runs, topic_decisions
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V8
from temnia_pipeline.harness.gateway import CostObservation, GatewayConfig
from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy
from temnia_pipeline.harness.models import ModelRuntime
from temnia_pipeline.harness.qualification_topic_selection import (
    topic_selection_qualification_case,
)
from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    SeatRoutePool,
)
from temnia_pipeline.harness.runtime_types import RunRef, StartRunRequest, WorkflowIdentity
from temnia_pipeline.harness.source_index import build_topic_source_index
from temnia_pipeline.harness.source_index_artifacts import (
    DEFAULT_EMBEDDING_REVISION,
    source_index_artifact_identity,
    source_index_artifact_metadata,
)
from temnia_pipeline.harness.topic_decisions import (
    AssembleRequestV8,
    DecisionRecordV8,
    DecisionRequest,
    TopicDecisionActivities,
)
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.topic_selection_runtime import SelectionContext
from test_harness_model_transport import snapshot
from test_harness_runs import SEEDED, pipeline_url, ready_source, settings

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store


def _route(identifier: str, family: str) -> RouteEntry:
    return RouteEntry(
        id=identifier,
        gateway_model=f"{family}/model",
        family=family,
        provider="fixture-provider",
        open_weight=True,
        context_tokens=200_000,
        max_output_tokens=16_384,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 11),
        ),
        prices=RoutePrices(input=1_000_000, output=2_000_000),
    )


async def _reusable_index(
    url: str, *, source_id: uuid.UUID, store: S3Store, evidence_ref: HarnessArtifactRef
) -> HarnessArtifactRef:
    """Publish the index exactly as the worker would, so the reuse loader accepts it."""
    evidence = HarnessEvidence.model_validate(
        await artifacts.read_artifact_json(
            url, scope=SEEDED, source_id=source_id, store=store, artifact_id=evidence_ref.id
        )
    )
    index = build_topic_source_index(
        evidence,
        evidence_sha256=evidence_ref.sha256,
        encoder=_FixtureEncoder(),
        embedding_revision=DEFAULT_EMBEDDING_REVISION,
    )
    accepted = await artifacts.publish_json(
        url,
        scope=SEEDED,
        source_id=source_id,
        store=store,
        identity=source_index_artifact_identity(evidence_ref, evidence),
        content=index.model_dump(mode="json"),
        metadata=source_index_artifact_metadata(evidence_ref, evidence),
        dependency_ids=(evidence_ref.id,),
    )
    return HarnessArtifactRef(
        id=accepted.id,
        kind=HarnessArtifactKind.checks,
        fingerprint=accepted.fingerprint,
        sha256=accepted.sha256,
        sizeBytes=accepted.size_bytes,
        storageKey=accepted.storage_key,
    )


def _draft(opportunity_id: str, first: str, last: str) -> dict[str, Any]:
    span = {"firstSentenceId": first, "lastSentenceId": last}
    return {
        "proposal": {"version": 1, "summary": "Fixture inventory.", "candidates": []},
        "opportunities": [
            {
                "id": opportunity_id,
                "viewerPurpose": "Explain the fixture discussion.",
                "coreSpans": [span],
                "completionSpans": [],
                "requiredContextSpans": [],
                "meaningChangingFollowups": [],
                "valueEvidenceSpans": [span],
                "candidateIds": [],
                "disposition": "needs_evidence",
                "dispositionReason": "Fixture inventory leaves packaging unresolved.",
            }
        ],
    }


async def test_inventory_decision_projects_dispatches_admits_and_reuses(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    author = _route("fixture-author", "family-author")
    verifier = _route("fixture-verifier", "family-verifier")
    spare = _route("fixture-spare", "family-spare")
    routes = snapshot(
        (author, verifier, spare),
        {
            "propose": SeatRoutePool(route_ids=(spare.id, author.id)),
            "verify": SeatRoutePool(route_ids=(spare.id, verifier.id)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    configuration = replace(settings(routes), backend="gateway", max_run_budget_micros=50_000_000)
    source_id = await ready_source(url)
    run_id = uuid.uuid4()
    request = ChapterRunInput(
        brief="Fixture brief.",
        budgetMicros=5_000_000,
        config=configuration.allowed_config(),
        requestKey=uuid.uuid4(),
        runId=run_id,
        scope=SEEDED,
        sourceId=source_id,
        # The user picked the second route of each pool; the worker freezes that order.
        routes=ChapterRunRoutePreferences(author=author.id, verifier=verifier.id),
    )
    start = StartRunRequest(
        request=request,
        editorial_policy=TOPIC_SELECTION_POLICY_V8,
        workflow=WorkflowIdentity(workflow_id="decision-test", workflow_run_id="run-1"),
    )
    started = await runs.start_or_refetch_run(
        url, start=start, settings=configuration, route_snapshot=routes
    )
    assert started.run.route_preferences == {"author": author.id, "verifier": verifier.id}
    store = cast("S3Store", MemoryStore())
    evidence_ref, _, _ = await _published_reviewer_refs(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, store=store
    )
    index_ref = await _reusable_index(
        url, source_id=source_id, store=store, evidence_ref=evidence_ref
    )
    await runs.attach_evidence(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, artifact_id=evidence_ref.id
    )
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    first = evidence.sentences[0].id
    last = evidence.sentences[1].id
    answers: list[dict[str, Any]] = [_draft("section-0001:fixture", first, last)]
    served: list[str] = []

    async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = info
        prompt = messages[0].parts[0]
        served.append(getattr(prompt, "content", ""))
        return ModelResponse(
            parts=[TextPart(json.dumps(answers[min(len(served), len(answers)) - 1]))],
            model_name="fixture/model",
            provider_name="fixture",
        )

    def factory(route: RouteEntry) -> FunctionModel:
        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    ctx = SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)
    owner = HarnessActivities(cast("Any", ctx), configuration, routes)
    selection = TopicSelectionActivities(owner)
    decisions = TopicDecisionActivities(owner)
    run_ref = RunRef(
        scope_organization_id=SEEDED.organizationId,
        scope_user_id=SEEDED.userId,
        source_id=source_id,
        run_id=run_id,
    )
    context = SelectionContext(
        run=run_ref, evidence=evidence_ref, program_version=TOPIC_SELECTION_POLICY_V8
    )
    rubric = await selection.rubric(context)
    context = context.model_copy(update={"rubric": rubric, "source_index": index_ref})
    models.configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=store,
            cassette_store=CassetteStore(tmp_path),
            model_factory=factory,
            allow_outside_activity=True,
        )
    )
    try:
        planned = await decisions.prepare_plan(context)
        assert planned.section_ids == ("section-0001",)
        assert planned.projected_calls > 0
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.projection is not None
        assert row.projection["authorRouteId"] == author.id
        assert row.projection["verifierRouteId"] == verifier.id
        assert "Projected about" in str(row.projection["sentence"])
        context = context.model_copy(update={"inventory_plan": planned.inventory_plan})

        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.gap is None
        assert result.artifact is not None
        assert result.family == verifier.family
        assert "section-0001" in served[0]
        assert "Fixture brief." in served[0]
        record = DecisionRecordV8.model_validate(await selection.read(context, result.artifact))
        assert record.kind == "inventory"
        assert record.shard is not None
        assert record.output["opportunities"][0]["id"] == "section-0001:fixture"
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.dispatch_count == 1

        # The same decision under a new execution reuses the admitted record without a call.
        again = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert again.reused is True
        assert again.artifact == result.artifact
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.dispatch_count == 1

        assembled = await decisions.assemble_inventory(
            AssembleRequestV8(context=context, results=(result,))
        )
        assert assembled.gaps == ()
        assert assembled.opportunity_count == 1
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_ungrounded_answer_is_corrected_once_then_becomes_a_gap(tmp_path: Path) -> None:
    url = pipeline_url()
    author = _route("fixture-author-2", "family-author")
    verifier = _route("fixture-verifier-2", "family-verifier")
    routes = snapshot(
        (author, verifier),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(verifier.id,)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    configuration = replace(settings(routes), backend="gateway", max_run_budget_micros=50_000_000)
    source_id = await ready_source(url)
    run_id = uuid.uuid4()
    request = ChapterRunInput(
        brief="Fixture brief.",
        budgetMicros=5_000_000,
        config=configuration.allowed_config(),
        requestKey=uuid.uuid4(),
        runId=run_id,
        scope=SEEDED,
        sourceId=source_id,
    )
    start = StartRunRequest(
        request=request,
        editorial_policy=TOPIC_SELECTION_POLICY_V8,
        workflow=WorkflowIdentity(workflow_id="decision-test-2", workflow_run_id="run-1"),
    )
    await runs.start_or_refetch_run(url, start=start, settings=configuration, route_snapshot=routes)
    store = cast("S3Store", MemoryStore())
    evidence_ref, _, _ = await _published_reviewer_refs(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, store=store
    )
    index_ref = await _reusable_index(
        url, source_id=source_id, store=store, evidence_ref=evidence_ref
    )
    await runs.attach_evidence(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, artifact_id=evidence_ref.id
    )
    prompts: list[str] = []

    async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = info
        prompts.append(str(getattr(messages[0].parts[0], "content", "")))
        # Cites a sentence that does not exist, twice; the diagnostic must reach the retry.
        return ModelResponse(
            parts=[TextPart(json.dumps(_draft("section-0001:ghost", "s999990", "s999991")))],
            model_name="fixture/model",
            provider_name="fixture",
        )

    def factory(route: RouteEntry) -> FunctionModel:
        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    ctx = SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)
    owner = HarnessActivities(cast("Any", ctx), configuration, routes)
    selection = TopicSelectionActivities(owner)
    decisions = TopicDecisionActivities(owner)
    run_ref = RunRef(
        scope_organization_id=SEEDED.organizationId,
        scope_user_id=SEEDED.userId,
        source_id=source_id,
        run_id=run_id,
    )
    context = SelectionContext(
        run=run_ref, evidence=evidence_ref, program_version=TOPIC_SELECTION_POLICY_V8
    )
    rubric = await selection.rubric(context)
    context = context.model_copy(update={"rubric": rubric, "source_index": index_ref})
    models.configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=store,
            cassette_store=CassetteStore(tmp_path),
            model_factory=factory,
            allow_outside_activity=True,
        )
    )
    try:
        planned = await decisions.prepare_plan(context)
        context = context.model_copy(update={"inventory_plan": planned.inventory_plan})
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.artifact is None
        assert result.gap is not None
        assert "do not exist" in result.gap.reason
        assert len(result.gap.retainedArtifacts) == 2
        assert len(prompts) == 2
        assert prompts[1].startswith("RECOVERY:")
        assert "do not exist" in prompts[1]
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.dispatch_count == 2
        assert row.status.value == "running"
    finally:
        models.clear_model_runtime()
        await db.close_pool()


@pytest.mark.parametrize("seat", ["author", "verifier"])
def test_route_preferences_must_name_a_pool_member(seat: str) -> None:
    author = _route("pref-author", "family-author")
    verifier = _route("pref-verifier", "family-verifier")
    routes = snapshot(
        (author, verifier),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(verifier.id,)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    with pytest.raises(runs.IdentityConflict, match="not in the frozen snapshot"):
        runs.route_preferences(routes, ChapterRunRoutePreferences(**{seat: "missing-route"}))
    ordered = runs.apply_route_preferences(routes, {"author": author.id, "verifier": verifier.id})
    assert ordered.seats["propose"].route_ids[0] == author.id
    assert ordered.seats["verify"].route_ids[0] == verifier.id


async def _v8_run(
    url: str,
    tmp_path: Path,
    routes: Any,  # noqa: ANN401
    factory: Any,  # noqa: ANN401
    *,
    request_routes: ChapterRunRoutePreferences | None = None,
) -> tuple[TopicDecisionActivities, SelectionContext, uuid.UUID, uuid.UUID]:
    """One planned v8 run against the database: evidence, index, rubric and inventory plan."""
    configuration = replace(
        settings(routes),
        backend="gateway",
        max_run_budget_micros=50_000_000,
        max_output_tokens=65_536,
    )
    source_id = await ready_source(url)
    run_id = uuid.uuid4()
    request = ChapterRunInput(
        brief="Fixture brief.",
        budgetMicros=5_000_000,
        config=configuration.allowed_config(),
        requestKey=uuid.uuid4(),
        runId=run_id,
        scope=SEEDED,
        sourceId=source_id,
        **({"routes": request_routes} if request_routes is not None else {}),
    )
    start = StartRunRequest(
        request=request,
        editorial_policy=TOPIC_SELECTION_POLICY_V8,
        workflow=WorkflowIdentity(workflow_id=f"decision-{run_id}", workflow_run_id="run-1"),
    )
    await runs.start_or_refetch_run(url, start=start, settings=configuration, route_snapshot=routes)
    store = cast("S3Store", MemoryStore())
    evidence_ref, _, _ = await _published_reviewer_refs(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, store=store
    )
    index_ref = await _reusable_index(
        url, source_id=source_id, store=store, evidence_ref=evidence_ref
    )
    await runs.attach_evidence(
        url, scope=SEEDED, source_id=source_id, run_id=run_id, artifact_id=evidence_ref.id
    )
    ctx = SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)
    owner = HarnessActivities(cast("Any", ctx), configuration, routes)
    selection = TopicSelectionActivities(owner)
    decisions = TopicDecisionActivities(owner)
    context = SelectionContext(
        run=RunRef(
            scope_organization_id=SEEDED.organizationId,
            scope_user_id=SEEDED.userId,
            source_id=source_id,
            run_id=run_id,
        ),
        evidence=evidence_ref,
        program_version=TOPIC_SELECTION_POLICY_V8,
    )
    rubric = await selection.rubric(context)
    context = context.model_copy(update={"rubric": rubric, "source_index": index_ref})
    models.configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=store,
            cassette_store=CassetteStore(tmp_path),
            model_factory=factory,
            allow_outside_activity=True,
        )
    )
    planned = await decisions.prepare_plan(context)
    context = context.model_copy(update={"inventory_plan": planned.inventory_plan})
    return decisions, context, source_id, run_id


async def test_throttled_route_is_retried_then_replaced_by_the_next_eligible_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    author = _route("fb-author", "family-author")
    throttled = _route("fb-throttled", "family-throttled")
    fallback = _route("fb-fallback", "family-fallback")
    routes = snapshot(
        (author, throttled, fallback),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(throttled.id, fallback.id)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            if route.id == throttled.id:
                raise ModelHTTPError(status_code=429, model_name=route.gateway_model, body=None)
            return ModelResponse(
                parts=[TextPart(json.dumps(good))], model_name="fixture", provider_name="fixture"
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    monkeypatch.setattr(topic_decisions, "TRANSIENT_BACKOFF_SECONDS", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(topic_decisions, "POOL_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(models, "DEFAULT_THROTTLE_SECONDS", 0.0)
    try:
        decisions, context, source_id, run_id = await _v8_run(url, tmp_path, routes, factory)
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.gap is None
        assert result.artifact is not None
        assert result.family == fallback.family
        assert result.verifier_index == 1
        # One attempt plus the four-step ladder on the throttled route, then the fallback.
        assert calls == [throttled.id] * 5 + [fallback.id]
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "running"
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_every_route_throttled_is_a_typed_stop_not_a_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    author = _route("ex-author", "family-author")
    first = _route("ex-first", "family-first")
    second = _route("ex-second", "family-second")
    routes = snapshot(
        (author, first, second),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(first.id, second.id)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            raise ModelHTTPError(status_code=503, model_name=route.gateway_model, body=None)

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    monkeypatch.setattr(topic_decisions, "TRANSIENT_BACKOFF_SECONDS", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(topic_decisions, "POOL_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(models, "DEFAULT_THROTTLE_SECONDS", 0.0)
    try:
        decisions, context, _, _ = await _v8_run(url, tmp_path, routes, factory)
        with pytest.raises(ApplicationError) as raised:
            await decisions.run_decision(
                DecisionRequest(context=context, kind="inventory", item_id="section-0001")
            )
        assert raised.value.type == "DecisionRoutesExhausted"
        # Five attempts per route, both routes, then one pool pause and the same again.
        assert calls == ([first.id] * 5 + [second.id] * 5) * 2
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_conclusive_provider_rejection_moves_to_the_next_route_once(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    author = _route("rj-author", "family-author")
    rejecting = _route("rj-rejecting", "family-rejecting")
    fallback = _route("rj-fallback", "family-fallback")
    routes = snapshot(
        (author, rejecting, fallback),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(rejecting.id, fallback.id)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            if route.id == rejecting.id:
                raise ModelHTTPError(status_code=400, model_name=route.gateway_model, body=None)
            return ModelResponse(
                parts=[TextPart(json.dumps(good))], model_name="fixture", provider_name="fixture"
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    try:
        decisions, context, _, _ = await _v8_run(url, tmp_path, routes, factory)
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.artifact is not None
        assert result.family == fallback.family
        assert calls == [rejecting.id, fallback.id]
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def _dropped_stream_run(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, recover_after: int
) -> tuple[TopicDecisionActivities, SelectionContext, uuid.UUID, uuid.UUID, list[str]]:
    """A verifier whose first stream drops after announcing its generation id."""
    author = _route("ou-author", "family-author")
    verifier = _route("ou-verifier", "family-verifier")
    routes = snapshot(
        (author, verifier),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(verifier.id,)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    calls: list[str] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages, info
            calls.append(route.id)
            if len(calls) == 1:
                await gateway.notify_observed_generation("gen-dropped-1")
                raise ModelAPIError(model_name=route.gateway_model, message="stream ended")
            return ModelResponse(
                parts=[TextPart(json.dumps(good))], model_name="fixture", provider_name="fixture"
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    lookups: list[str] = []

    async def receipt(*_args: object, **kwargs: object) -> CostObservation:
        handle = str(kwargs["generation_id"])
        lookups.append(handle)
        if len(lookups) < recover_after:
            return CostObservation(
                status="pending",
                actual_cost_micros=None,
                components={"generationId": handle, "totalCost": None},
            )
        return CostObservation(
            status="reported", actual_cost_micros=5, components={"generationId": handle}
        )

    async def still_pending(*_args: object, **_kwargs: object) -> CostObservation:
        return CostObservation(status="pending", actual_cost_micros=None, components={})

    # The model's own 20 s receipt wait finds nothing; the decision's ladder does.
    monkeypatch.setattr(models, "observe_generation_cost", still_pending)
    monkeypatch.setattr(receipts, "lookup_generation", receipt)
    monkeypatch.setattr(topic_decisions, "RECEIPT_WAIT_SECONDS", (0.0,) * 6)
    monkeypatch.setattr(topic_decisions, "TRANSIENT_BACKOFF_SECONDS", (0.0, 0.0, 0.0, 0.0))
    decisions, context, source_id, run_id = await _v8_run(url, tmp_path, routes, factory)
    runtime = models.current_runtime()
    assert runtime is not None
    models.configure_model_runtime(replace(runtime, gateway=GatewayConfig(api_key="test-key")))
    return decisions, context, source_id, run_id, calls


async def test_a_dropped_stream_waits_for_its_receipt_then_continues_on_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    try:
        decisions, context, source_id, run_id, calls = await _dropped_stream_run(
            url, tmp_path, monkeypatch, recover_after=2
        )
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.gap is None
        assert result.artifact is not None
        assert result.verifier_index == 0
        assert calls == ["ou-verifier", "ou-verifier"]
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "running"
        # The dropped stream's reservation became its settled charge.
        assert row.spent_micros >= 5
        async with db.scoped(url, SEEDED) as conn:
            attempts = await (
                await conn.execute(
                    "SELECT state, cost_status, actual_cost_micros, error_code"
                    " FROM harness_attempt WHERE run_id = %s ORDER BY created_at",
                    (run_id,),
                )
            ).fetchall()
        settled = [dict(item) for item in attempts if item["error_code"] is not None]
        assert settled == [
            {
                "state": "failed_known",
                "cost_status": "reported",
                "actual_cost_micros": 5,
                "error_code": "provider-stream-failure",
            }
        ]
        assert attempts[-1]["state"] == "succeeded"
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_receipt_that_never_settles_is_a_typed_stop_after_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    try:
        decisions, context, source_id, run_id, calls = await _dropped_stream_run(
            url, tmp_path, monkeypatch, recover_after=100
        )
        with pytest.raises(ApplicationError) as raised:
            await decisions.run_decision(
                DecisionRequest(context=context, kind="inventory", item_id="section-0001")
            )
        assert raised.value.type == "OutcomeUnknown"
        assert "did not settle" in raised.value.message
        assert calls == ["ou-verifier"]
        row = await runs.get_run(url, scope=SEEDED, source_id=source_id, run_id=run_id)
        assert row.status.value == "outcome_unknown"
        assert row.reserved_micros > 0
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_a_cut_off_answer_gets_a_larger_allowance_and_is_then_admitted(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    author = _route("tr-author", "family-author")
    verifier = _route("tr-verifier", "family-verifier").model_copy(
        update={
            "max_output_tokens": 65_536,
            "provider_accounting_name": "fixture",
            "accounting_model": "family-verifier/model-20260911",
            "transport": GatewayTransportPolicy(
                version="gateway-transport/2",
                gateway="openrouter",
                mode="streaming",
                request_timeout_seconds=300.0,
                total_timeout_seconds=540.0,
                output_token_parameter="max_tokens",  # noqa: S106
            ),
        }
    )
    routes = snapshot(
        (author, verifier),
        {
            "propose": SeatRoutePool(route_ids=(author.id,)),
            "verify": SeatRoutePool(route_ids=(verifier.id,)),
            "summary": SeatRoutePool(route_ids=(author.id,)),
        },
    )
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    good = _draft("section-0001:fixture", evidence.sentences[0].id, evidence.sentences[1].id)
    allowances: list[int | None] = []

    def factory(route: RouteEntry) -> FunctionModel:
        async def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages
            allowances.append((info.model_settings or {}).get("max_tokens"))
            finish = "length" if len(allowances) == 1 else "stop"
            return ModelResponse(
                parts=[TextPart(json.dumps(good))],
                model_name="fixture",
                provider_name="fixture",
                finish_reason=finish,
            )

        return FunctionModel(answer, model_name=f"fixture:{route.id}")

    try:
        decisions, context, _, _ = await _v8_run(url, tmp_path, routes, factory)
        result = await decisions.run_decision(
            DecisionRequest(context=context, kind="inventory", item_id="section-0001")
        )
        assert result.artifact is not None
        assert result.gap is None
        assert len(allowances) == 2
        first_allowance, second_allowance = allowances
        assert first_allowance is not None
        assert second_allowance == 2 * first_allowance
    finally:
        models.clear_model_runtime()
        await db.close_pool()


async def test_route_cooldown_holds_every_dispatch_on_that_route() -> None:
    gate = models.RouteGate(max_in_flight=2, min_interval_seconds=0.0)
    loop = asyncio.get_running_loop()
    gate.throttle(0.3)
    assert gate.cooldown_remaining() > 0.2
    started = loop.time()
    await gate.acquire()
    gate.release()
    assert loop.time() - started >= 0.25
    assert gate.cooldown_remaining() == 0.0
