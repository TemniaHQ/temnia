"""Temporal and real-Postgres proof for reusable summary label normalization."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import os
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore
from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, PydanticAIWorkflow
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterReviewAction,
    ChapterReviewInput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    Scope,
)
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER, CassetteStore
from temnia_pipeline.harness.models import (
    HarnessModelDeps,
    ModelRuntime,
    chapter_summarize_v1,
    clear_model_runtime,
    configure_model_runtime,
)
from temnia_pipeline.harness.prompts import PromptSentence, PromptWindow, render_summary_prompt
from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
)
from temnia_pipeline.harness.runs import apply_operational_review, update_stage
from temnia_pipeline.harness.runtime_types import (
    GlobalProposalPlan,
    PinnedSource,
    PinnedTranscript,
    PlanningWindow,
    PrepareGlobalProposalRequest,
    RunRef,
    StageUpdate,
    ValidatedSummary,
    ValidateSummaryRequest,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.summary_grounding import (
    SummaryGroundingReportV2,
    read_summary_grounding_report,
)

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store
    from pydantic_ai.messages import ModelMessage


SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def _pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required for the real persistence boundary")
    return url


async def _run_case(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with db.scoped(url, SCOPE) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SCOPE.organizationId, f"summary-normalization-{uuid.uuid4()}"),
            )
        ).fetchone()
        assert project is not None
        source = await (
            await conn.execute(
                """
                INSERT INTO source
                    (organization_id, project_id, title, original_filename, content_type,
                     size_bytes, master_key, status)
                VALUES (%s, %s, 'summary normalization', 'source.mp4', 'video/mp4',
                        1, %s, 'ready')
                RETURNING id
                """,
                (
                    SCOPE.organizationId,
                    project["id"],
                    f"summary-normalization/{uuid.uuid4()}/master.mp4",
                ),
            )
        ).fetchone()
        assert source is not None
        run = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (organization_id, source_id, request_key, lane, budget_micros,
                     config, route_snapshot, status, stage)
                VALUES (%s, %s, %s, 'chapters', 1000000,
                        '{}'::jsonb, '{}'::jsonb, 'running', 'planning')
                RETURNING id
                """,
                (SCOPE.organizationId, source["id"], str(uuid.uuid4())),
            )
        ).fetchone()
        assert run is not None
    return source["id"], run["id"]


@workflow.defn
class SummaryNormalizationWorkflow(PydanticAIWorkflow):
    """Exercise the actual PydanticAI model activity from Temporal."""

    __pydantic_ai_agents__ = (chapter_summarize_v1,)

    @workflow.run
    async def run(self, deps: HarnessModelDeps) -> dict[str, Any]:
        result = await chapter_summarize_v1.run(
            "Return the summary.", deps=deps, model_settings={"max_tokens": 1024}
        )
        return result.output.model_dump(mode="json")


class GroundingWorkflowInput(BaseModel):
    """One actual summary call followed by the production grounding activities."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deps: HarnessModelDeps
    validation: ValidateSummaryRequest


class GroundingWorkflowResult(BaseModel):
    """Portable identities returned from the actual Temporal activity path."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    validated: ValidatedSummary
    global_plan: GlobalProposalPlan


class PlanningRecoveryInput(BaseModel):
    """All retained first-level windows for one same-run continuation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deps: tuple[HarnessModelDeps, ...]
    validations: tuple[ValidateSummaryRequest, ...]


class PlanningRecoveryResult(BaseModel):
    """The exact grounded lineage delivered to the next planning step."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    validations: tuple[ValidatedSummary, ...]
    global_plan: GlobalProposalPlan


@workflow.defn
class SummaryGroundingWorkflow(PydanticAIWorkflow):
    """Exercise retained response reuse and the grounding artifact activities."""

    __pydantic_ai_agents__ = (chapter_summarize_v1,)

    @workflow.run
    async def run(self, value: GroundingWorkflowInput) -> GroundingWorkflowResult:
        result = await chapter_summarize_v1.run(
            value.validation.window.prompt,
            deps=value.deps,
            model_settings={"max_tokens": 1024},
        )
        validated = await workflow.execute_activity(
            "validate_chapter_summary",
            value.validation.model_copy(update={"summary": result.output.model_dump(mode="json")}),
            start_to_close_timeout=timedelta(minutes=2),
            result_type=ValidatedSummary,
        )
        assert validated.summary is not None
        assert validated.artifact is not None
        global_plan = await workflow.execute_activity(
            "prepare_global_chapter_proposal",
            PrepareGlobalProposalRequest(
                run=value.validation.run,
                evidence=value.validation.evidence,
                windows=(value.validation.window,),
                summaries=(validated.summary,),
                grounding_artifacts=(validated.artifact,),
            ),
            start_to_close_timeout=timedelta(minutes=2),
            result_type=GlobalProposalPlan,
        )
        return GroundingWorkflowResult(validated=validated, global_plan=global_plan)


@workflow.defn
class PlanningSeedWorkflow(PydanticAIWorkflow):
    """Persist all responses and accept every window before the final refusal."""

    __pydantic_ai_agents__ = (chapter_summarize_v1,)

    @workflow.run
    async def run(self, value: PlanningRecoveryInput) -> int:
        accepted = 0
        for index, (deps, validation) in enumerate(zip(value.deps, value.validations, strict=True)):
            result = await chapter_summarize_v1.run(
                validation.window.prompt,
                deps=deps,
                model_settings={"max_tokens": 1024},
            )
            if index == len(value.validations) - 1:
                continue
            validated = await workflow.execute_activity(
                "validate_chapter_summary",
                validation.model_copy(update={"summary": result.output.model_dump(mode="json")}),
                start_to_close_timeout=timedelta(minutes=2),
                result_type=ValidatedSummary,
            )
            assert validated.artifact is not None
            accepted += 1
        return accepted


@workflow.defn
class PlanningRecoveryWorkflow(PydanticAIWorkflow):
    """Mirror the production retained-summary revalidation before proposal."""

    __pydantic_ai_agents__ = (chapter_summarize_v1,)

    @workflow.run
    async def run(self, value: PlanningRecoveryInput) -> PlanningRecoveryResult:
        validated: list[ValidatedSummary] = []
        for deps, validation in zip(value.deps, value.validations, strict=True):
            result = await chapter_summarize_v1.run(
                validation.window.prompt,
                deps=deps,
                model_settings={"max_tokens": 1024},
            )
            accepted = await workflow.execute_activity(
                "validate_chapter_summary",
                validation.model_copy(update={"summary": result.output.model_dump(mode="json")}),
                start_to_close_timeout=timedelta(minutes=2),
                result_type=ValidatedSummary,
            )
            assert accepted.summary is not None
            assert accepted.artifact is not None
            validated.append(accepted)
        first = value.validations[0]
        global_plan = await workflow.execute_activity(
            "prepare_global_chapter_proposal",
            PrepareGlobalProposalRequest(
                run=first.run,
                evidence=first.evidence,
                windows=tuple(item.window for item in value.validations),
                summaries=tuple(cast("dict[str, Any]", item.summary) for item in validated),
                grounding_artifacts=tuple(
                    cast("HarnessArtifactRef", item.artifact) for item in validated
                ),
            ),
            start_to_close_timeout=timedelta(minutes=2),
            result_type=GlobalProposalPlan,
        )
        return PlanningRecoveryResult(validations=tuple(validated), global_plan=global_plan)


def _grounding_evidence(source_id: uuid.UUID) -> HarnessEvidence:
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {},
            "durationMs": 2000,
            "frameRate": None,
            "modelVersions": {},
            "pauses": [],
            "sentences": [
                {
                    "endMs": 900,
                    "id": "s0",
                    "speakers": ["speaker-a"],
                    "startMs": 0,
                    "text": "Exact first source sentence.",
                    "wordIds": ["w0"],
                },
                {
                    "endMs": 1900,
                    "id": "s1",
                    "speakers": ["speaker-a"],
                    "startMs": 1000,
                    "text": "Exact second source sentence.",
                    "wordIds": ["w1"],
                },
            ],
            "shots": [],
            "sourceFingerprint": "a" * 64,
            "sourceId": str(source_id),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": None,
                "detectorHash": None,
                "detectorRevision": None,
                "intervals": [],
                "status": "unknown",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(uuid.uuid4()),
            "transcriptRevision": 1,
            "transcriptSha256": "b" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": [
                {
                    "confidence": 1.0,
                    "endMs": 900,
                    "id": "w0",
                    "lineageIds": [],
                    "speaker": "speaker-a",
                    "startMs": 0,
                    "text": "first",
                    "timing": "aligned",
                    "wordIndex": 0,
                },
                {
                    "confidence": 1.0,
                    "endMs": 1900,
                    "id": "w1",
                    "lineageIds": [],
                    "speaker": "speaker-a",
                    "startMs": 1000,
                    "text": "second",
                    "timing": "aligned",
                    "wordIndex": 1,
                },
            ],
        }
    )


def _coverage_recovery_evidence(source_id: uuid.UUID) -> HarnessEvidence:
    value = _grounding_evidence(source_id).model_dump(mode="json")
    value["durationMs"] = 30_000
    value["sentences"] = [
        {
            "endMs": index * 1000 + 900,
            "id": f"s{index:04d}",
            "speakers": ["speaker-a"],
            "startMs": index * 1000,
            "text": f"Exact source sentence {index}.",
            "wordIds": [f"w{index:04d}"],
        }
        for index in range(30)
    ]
    value["words"] = [
        {
            "confidence": 1.0,
            "endMs": index * 1000 + 900,
            "id": f"w{index:04d}",
            "lineageIds": [],
            "speaker": "speaker-a",
            "startMs": index * 1000,
            "text": f"word-{index}",
            "timing": "aligned",
            "wordIndex": index,
        }
        for index in range(30)
    ]
    return HarnessEvidence.model_validate(value)


def _snapshot(route: RouteEntry) -> RouteSnapshot:
    candidate = RouteSnapshot.model_construct(
        snapshot_id="c" * 64,
        routes=(route,),
        seats={
            "propose": SeatRoutePool(route_ids=(route.id,)),
            "summary": SeatRoutePool(route_ids=(route.id,)),
            "verify": SeatRoutePool(route_ids=(route.id,)),
        },
        synthetic=True,
        version=1,
    )
    return RouteSnapshot.model_validate(
        candidate.model_dump(mode="python") | {"snapshot_id": candidate.computed_id()}
    )


async def test_temporal_summary_boundary_reuses_raw_response_without_dispatch(
    tmp_path: Path,
) -> None:
    url = _pipeline_url()
    source_id, run_id = await _run_case(url)
    calls = 0

    async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        _ = messages, info
        calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    '{"units":[{"firstSentenceId":"s0","id":"",'
                    '"lastSentenceId":"s0","quoteWordIds":["w0"],'
                    '"text":"Original source sentence."}],"version":1}'
                )
            ],
            model_name="summary-model",
            provider_name="summary-provider",
            provider_response_id="summary-generation",
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        _ = route
        return FunctionModel(response, model_name="summary-model")

    route = RouteEntry(
        id="summary-route",
        gateway_model="fixture/summary",
        family="summary-family",
        provider="summary-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=1024,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="b" * 64,
            probed_at=date(2026, 9, 9),
        ),
        prices=RoutePrices(input=0, output=0),
    )
    deps = HarnessModelDeps(
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        stage="summary:window-0006",
        program_version="chapter-workflow/1",
        prompt_version="chapter-summarize-v3",
        schema_version="hierarchical-summary/1",
        route=route,
        operation_inputs={"windowId": "window-0006"},
        operation_config={"hierarchyLevel": 1, "maxOutputTokens": 1024},
        dispatch_limit=8,
    )
    store = MemoryStore()
    configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=cast("S3Store", store),
            cassette_store=CassetteStore(tmp_path),
            model_factory=model_factory,
        )
    )
    queue = f"summary-normalization-{uuid.uuid4()}"
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[SummaryNormalizationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            first = await environment.client.execute_workflow(
                SummaryNormalizationWorkflow.run,
                deps,
                id=f"summary-normalization-first-{uuid.uuid4()}",
                task_queue=queue,
            )
            second = await environment.client.execute_workflow(
                SummaryNormalizationWorkflow.run,
                deps,
                id=f"summary-normalization-second-{uuid.uuid4()}",
                task_queue=queue,
            )
        assert first == second
        assert first["units"][0]["id"].startswith("summary-0000-")
        assert calls == 1
        async with db.scoped(url, SCOPE) as conn:
            facts = await (
                await conn.execute(
                    """
                    SELECT o.result_artifact_id,
                           (SELECT count(*)::int FROM harness_operation
                             WHERE run_id = %s) AS operation_count,
                           (SELECT count(*)::int FROM harness_attempt
                             WHERE run_id = %s) AS attempt_count
                      FROM harness_operation o
                     WHERE o.run_id = %s AND o.stage = 'summary:window-0006'
                    """,
                    (run_id, run_id, run_id),
                )
            ).fetchone()
        assert facts is not None
        assert facts["operation_count"] == 1
        assert facts["attempt_count"] == 1
        raw_response = await artifacts.read_artifact_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            artifact_id=facts["result_artifact_id"],
        )
        assert isinstance(raw_response, dict)
        retained = MODEL_RESPONSE_ADAPTER.validate_python(raw_response)
        assert isinstance(retained.parts[0], TextPart)
        assert json.loads(retained.parts[0].content)["units"][0]["id"] == ""
    finally:
        clear_model_runtime()
        await db.close_pool()


async def test_temporal_grounding_reuses_raw_response_and_audit_artifact(  # noqa: PLR0915
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _pipeline_url()
    source_id, run_id = await _run_case(url)
    store = MemoryStore()
    evidence = _grounding_evidence(source_id)
    async with db.scoped(url, SCOPE) as conn:
        await conn.execute(
            """
            INSERT INTO transcript
                (id, organization_id, source_id, current_revision, status)
            VALUES (%s, %s, %s, 1, 'ready')
            """,
            (evidence.transcriptId, SCOPE.organizationId, source_id),
        )
        await conn.execute(
            """
            INSERT INTO transcript_revision
                (organization_id, transcript_id, revision, kind, storage_key,
                 size_bytes, word_count)
            VALUES (%s, %s, 1, 'machine', %s, 1, 2)
            """,
            (
                SCOPE.organizationId,
                evidence.transcriptId,
                f"summary-grounding/{source_id}/transcript.json",
            ),
        )
    evidence_fingerprint = artifacts.fingerprint_for(
        kind=HarnessArtifactKind.evidence,
        inputs={"fixture": "summary-grounding", "sourceId": str(source_id)},
        config={"format": "harness-evidence/1"},
    )
    accepted_evidence = await artifacts.publish_json(
        url,
        scope=SCOPE,
        source_id=source_id,
        store=cast("S3Store", store),
        identity=artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint=evidence_fingerprint,
            transcript_id=evidence.transcriptId,
            transcript_revision=evidence.transcriptRevision,
        ),
        content=evidence.model_dump(mode="json"),
        metadata={"format": "harness-evidence/1"},
    )
    evidence_ref = HarnessArtifactRef(
        fingerprint=accepted_evidence.fingerprint,
        id=accepted_evidence.id,
        kind=HarnessArtifactKind.evidence,
        sha256=accepted_evidence.sha256,
        sizeBytes=accepted_evidence.size_bytes,
        storageKey=accepted_evidence.storage_key,
    )
    async with db.scoped(url, SCOPE) as conn:
        await conn.execute(
            "UPDATE harness_run SET evidence_artifact_id = %s WHERE id = %s",
            (evidence_ref.id, run_id),
        )

    calls = 0
    raw_summary = {
        "units": [
            {
                "firstSentenceId": "s0",
                "id": "first",
                "lastSentenceId": "s0",
                "quoteWordIds": ["w1"],
                "text": "Generated text grounded in the neighbouring unit.",
            },
            {
                "firstSentenceId": "s1",
                "id": "second",
                "lastSentenceId": "s1",
                "quoteWordIds": ["w1"],
                "text": "Generated second unit.",
            },
        ],
        "version": 1,
    }

    async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        _ = messages, info
        calls += 1
        return ModelResponse(
            parts=[TextPart(json.dumps(raw_summary))],
            model_name="summary-model",
            provider_name="summary-provider",
            provider_response_id="summary-grounding-generation",
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        _ = route
        return FunctionModel(response, model_name="summary-model")

    route = RouteEntry(
        id="summary-route",
        gateway_model="fixture/summary",
        family="summary-family",
        provider="summary-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=1024,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="d" * 64,
            probed_at=date(2026, 9, 9),
        ),
        prices=RoutePrices(input=0, output=0),
    )
    route_snapshot = _snapshot(route)
    settings = HarnessSettings(
        enabled=True,
        backend="gateway",
        route_snapshot_id=route_snapshot.snapshot_id,
        route_snapshot_path=None,
        allow_recorded=False,
        max_run_budget_micros=1_000_000,
        max_dispatches=8,
        max_repairs=1,
        max_output_tokens=1024,
        evidence_window_sentences=80,
        max_render_concurrency=1,
        gateway_api_key="fixture-key",
        recorded_fixture_path=None,
    )
    context = SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)
    activities = HarnessActivities(cast("Any", context), settings, route_snapshot)

    async def get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            brief="Preserve every original sentence.",
            evidence_artifact_id=evidence_ref.id,
            route_snapshot=route_snapshot,
        )

    monkeypatch.setattr(activities_module.runs, "get_run", get_run)
    window = PromptWindow(
        sourceId=source_id,
        evidenceSha256=evidence_ref.sha256,
        windowId="window-0000",
        firstSentenceId="s0",
        lastSentenceId="s1",
        sentences=(
            PromptSentence(
                id="s0",
                text="Exact first source sentence.",
                firstWordId="w0",
                lastWordId="w0",
                speakers=("speaker-a",),
            ),
            PromptSentence(
                id="s1",
                text="Exact second source sentence.",
                firstWordId="w1",
                lastWordId="w1",
                speakers=("speaker-a",),
            ),
        ),
    )
    planning_window = PlanningWindow(
        id=window.windowId,
        first_sentence_id=window.firstSentenceId,
        last_sentence_id=window.lastSentenceId,
        sentence_count=2,
        prompt=render_summary_prompt(window),
    )
    run_ref = RunRef(
        scope_organization_id=SCOPE.organizationId,
        scope_user_id=SCOPE.userId,
        source_id=source_id,
        run_id=run_id,
    )
    deps = HarnessModelDeps(
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        stage="summary:window-0000",
        program_version="chapter-workflow/1",
        prompt_version="chapter-summarize-v3",
        schema_version="hierarchical-summary/1",
        route=route,
        operation_inputs={
            "evidenceArtifactId": str(evidence_ref.id),
            "evidenceSha256": evidence_ref.sha256,
            "firstSentenceId": "s0",
            "lastSentenceId": "s1",
            "windowId": "window-0000",
        },
        operation_config={"hierarchyLevel": 1, "maxOutputTokens": 1024},
        input_artifact_ids=(evidence_ref.id,),
        dispatch_limit=8,
    )
    value = GroundingWorkflowInput(
        deps=deps,
        validation=ValidateSummaryRequest(
            run=run_ref,
            evidence=evidence_ref,
            window=planning_window,
            summary={},
            model_stage=deps.stage,
        ),
    )
    configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=cast("S3Store", store),
            cassette_store=CassetteStore(tmp_path),
            model_factory=model_factory,
        )
    )
    queue = f"summary-grounding-{uuid.uuid4()}"
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[SummaryGroundingWorkflow],
                activities=[
                    activities.validate_chapter_summary,
                    activities.prepare_global_chapter_proposal,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            first = await environment.client.execute_workflow(
                SummaryGroundingWorkflow.run,
                value,
                id=f"summary-grounding-first-{uuid.uuid4()}",
                task_queue=queue,
            )
            second = await environment.client.execute_workflow(
                SummaryGroundingWorkflow.run,
                value,
                id=f"summary-grounding-second-{uuid.uuid4()}",
                task_queue=queue,
            )
        assert first == second
        assert first.validated.artifact is not None
        assert first.validated.summary is not None
        accepted_summary = cast("dict[str, Any]", first.validated.summary)
        accepted_units = cast("list[dict[str, Any]]", accepted_summary["units"])
        assert accepted_units[0]["text"] == "Exact first source sentence."
        assert first.global_plan.input_artifacts == (first.validated.artifact,)
        assert calls == 1

        async with db.scoped(url, SCOPE) as conn:
            facts = await (
                await conn.execute(
                    """
                    SELECT r.dispatch_count, r.repair_count, r.reserved_micros, r.spent_micros,
                           o.result_artifact_id,
                           (SELECT count(*)::int FROM harness_operation WHERE run_id = %s)
                               AS operation_count,
                           (SELECT count(*)::int FROM harness_attempt WHERE run_id = %s)
                               AS attempt_count
                      FROM harness_run r
                      JOIN harness_operation o ON o.run_id = r.id
                     WHERE r.id = %s AND o.stage = 'summary:window-0000'
                    """,
                    (run_id, run_id, run_id),
                )
            ).fetchone()
            dependencies = await (
                await conn.execute(
                    """
                    SELECT input_artifact_id FROM harness_artifact_dependency
                     WHERE artifact_id = %s ORDER BY input_artifact_id
                    """,
                    (first.validated.artifact.id,),
                )
            ).fetchall()
            grounding_count = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count FROM harness_artifact
                     WHERE source_id = %s AND kind = 'checks'
                       AND metadata->>'format' = 'chapter-summary-grounding/1'
                    """,
                    (source_id,),
                )
            ).fetchone()
        assert facts is not None
        assert facts["operation_count"] == 1
        assert facts["attempt_count"] == 1
        assert facts["dispatch_count"] == 1
        assert facts["repair_count"] == 0
        assert facts["reserved_micros"] == 0
        assert facts["spent_micros"] == 0
        assert grounding_count == {"count": 1}
        assert {row["input_artifact_id"] for row in dependencies} == {
            evidence_ref.id,
            facts["result_artifact_id"],
        }
        raw_response = await artifacts.read_artifact_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            artifact_id=facts["result_artifact_id"],
        )
        retained = MODEL_RESPONSE_ADAPTER.validate_python(raw_response)
        assert isinstance(retained.parts[0], TextPart)
        assert json.loads(retained.parts[0].content) == raw_summary
    finally:
        clear_model_runtime()
        await db.close_pool()


async def test_planning_retry_reuses_fifteen_responses_and_fourteen_v1_reports(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    url = _pipeline_url()
    source_id, run_id = await _run_case(url)
    store = MemoryStore()
    evidence = _coverage_recovery_evidence(source_id)
    transcript_key = f"summary-recovery/{source_id}/transcript.json"
    async with db.scoped(url, SCOPE) as conn:
        await conn.execute(
            """
            INSERT INTO transcript
                (id, organization_id, source_id, current_revision, status)
            VALUES (%s, %s, %s, 1, 'ready')
            """,
            (evidence.transcriptId, SCOPE.organizationId, source_id),
        )
        await conn.execute(
            """
            INSERT INTO transcript_revision
                (organization_id, transcript_id, revision, kind, storage_key,
                 size_bytes, word_count)
            VALUES (%s, %s, 1, 'machine', %s, 1, 30)
            """,
            (
                SCOPE.organizationId,
                evidence.transcriptId,
                transcript_key,
            ),
        )
    evidence_fingerprint = artifacts.fingerprint_for(
        kind=HarnessArtifactKind.evidence,
        inputs={"fixture": "summary-coverage-recovery", "sourceId": str(source_id)},
        config={"format": "harness-evidence/1"},
    )
    accepted_evidence = await artifacts.publish_json(
        url,
        scope=SCOPE,
        source_id=source_id,
        store=cast("S3Store", store),
        identity=artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint=evidence_fingerprint,
            transcript_id=evidence.transcriptId,
            transcript_revision=evidence.transcriptRevision,
        ),
        content=evidence.model_dump(mode="json"),
        metadata={"format": "harness-evidence/1"},
    )
    evidence_ref = HarnessArtifactRef(
        fingerprint=accepted_evidence.fingerprint,
        id=accepted_evidence.id,
        kind=HarnessArtifactKind.evidence,
        sha256=accepted_evidence.sha256,
        sizeBytes=accepted_evidence.size_bytes,
        storageKey=accepted_evidence.storage_key,
    )
    async with db.scoped(url, SCOPE) as conn:
        await conn.execute(
            "UPDATE harness_run SET evidence_artifact_id = %s WHERE id = %s",
            (evidence_ref.id, run_id),
        )

    route = RouteEntry(
        id="summary-route",
        gateway_model="fixture/summary",
        family="summary-family",
        provider="summary-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=1024,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="d" * 64,
            probed_at=date(2026, 9, 10),
        ),
        prices=RoutePrices(input=0, output=0),
    )
    route_snapshot = _snapshot(route)
    settings = HarnessSettings(
        enabled=True,
        backend="gateway",
        route_snapshot_id=route_snapshot.snapshot_id,
        route_snapshot_path=None,
        allow_recorded=False,
        max_run_budget_micros=1_000_000,
        max_dispatches=32,
        max_repairs=1,
        max_output_tokens=1024,
        evidence_window_sentences=2,
        max_render_concurrency=1,
        gateway_api_key="fixture-key",
        recorded_fixture_path=None,
    )
    async with db.scoped(url, SCOPE) as conn:
        source = await (
            await conn.execute(
                "SELECT master_key, size_bytes FROM source WHERE id = %s",
                (source_id,),
            )
        ).fetchone()
        assert source is not None
        await conn.execute(
            "UPDATE source SET duration_ms = 30000 WHERE id = %s",
            (source_id,),
        )
        frozen_envelope = {
            "initialBudgetMicros": 1_000_000,
            "pinnedSource": PinnedSource(
                storage_key=str(source["master_key"]),
                size_bytes=int(source["size_bytes"]),
                duration_ms=30_000,
            ).model_dump(mode="json"),
            "pinnedTranscript": PinnedTranscript(
                transcript_id=evidence.transcriptId,
                revision=1,
                storage_key=transcript_key,
                size_bytes=1,
                sha256=evidence.transcriptSha256,
                machine_revision=1,
            ).model_dump(mode="json"),
            "snapshot": route_snapshot.model_dump(mode="json"),
        }
        await conn.execute(
            """
            UPDATE harness_run
               SET brief = 'Preserve every original sentence.', config = %s::jsonb,
                   route_snapshot = %s::jsonb, workflow_id = %s, workflow_run_id = %s
             WHERE id = %s
            """,
            (
                json.dumps(settings.allowed_config().model_dump(mode="json")),
                json.dumps(frozen_envelope),
                f"chapter-{run_id}",
                f"initial-{run_id}",
                run_id,
            ),
        )
    context = SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)
    harness_activities = HarnessActivities(cast("Any", context), settings, route_snapshot)
    run_ref = RunRef(
        scope_organization_id=SCOPE.organizationId,
        scope_user_id=SCOPE.userId,
        source_id=source_id,
        run_id=run_id,
    )
    values: list[GroundingWorkflowInput] = []
    for index in range(15):
        first = index * 2
        last = first + 1
        window = PromptWindow(
            sourceId=source_id,
            evidenceSha256=evidence_ref.sha256,
            windowId=f"window-{index:04d}",
            firstSentenceId=f"s{first:04d}",
            lastSentenceId=f"s{last:04d}",
            sentences=tuple(
                PromptSentence(
                    id=f"s{sentence:04d}",
                    text=f"Exact source sentence {sentence}.",
                    firstWordId=f"w{sentence:04d}",
                    lastWordId=f"w{sentence:04d}",
                    speakers=("speaker-a",),
                )
                for sentence in (first, last)
            ),
        )
        planning_window = PlanningWindow(
            id=window.windowId,
            first_sentence_id=window.firstSentenceId,
            last_sentence_id=window.lastSentenceId,
            sentence_count=2,
            prompt=render_summary_prompt(window),
        )
        deps = HarnessModelDeps(
            scope=SCOPE,
            source_id=source_id,
            run_id=run_id,
            stage=f"summary:{window.windowId}",
            program_version="chapter-workflow/1",
            prompt_version="chapter-summarize-v3",
            schema_version="hierarchical-summary/1",
            route=route,
            operation_inputs={
                "evidenceArtifactId": str(evidence_ref.id),
                "evidenceSha256": evidence_ref.sha256,
                "firstSentenceId": window.firstSentenceId,
                "lastSentenceId": window.lastSentenceId,
                "windowId": window.windowId,
            },
            operation_config={"hierarchyLevel": 1, "maxOutputTokens": 1024},
            input_artifact_ids=(evidence_ref.id,),
            dispatch_limit=32,
        )
        values.append(
            GroundingWorkflowInput(
                deps=deps,
                validation=ValidateSummaryRequest(
                    run=run_ref,
                    evidence=evidence_ref,
                    window=planning_window,
                    summary={},
                    model_stage=deps.stage,
                ),
            )
        )

    provider_calls = 0

    async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal provider_calls
        _ = messages, info
        index = provider_calls
        provider_calls += 1
        first = index * 2
        last = first + 1
        if index == 14:
            first = last
        summary = {
            "units": [
                {
                    "firstSentenceId": f"s{first:04d}",
                    "id": f"unit-{index:04d}",
                    "lastSentenceId": f"s{last:04d}",
                    "quoteWordIds": [f"w{first:04d}", f"w{last:04d}"],
                    "text": f"Synthetic summary {index}.",
                }
            ],
            "version": 1,
        }
        return ModelResponse(
            parts=[TextPart(json.dumps(summary))],
            model_name="summary-model",
            provider_name="summary-provider",
            provider_response_id=f"summary-generation-{index}",
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        _ = route
        return FunctionModel(response, model_name="summary-model")

    configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=cast("S3Store", store),
            cassette_store=CassetteStore(tmp_path),
            model_factory=model_factory,
        )
    )
    queue = f"summary-recovery-seed-{uuid.uuid4()}"
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[PlanningSeedWorkflow],
                activities=[
                    harness_activities.validate_chapter_summary,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            seeded = await environment.client.execute_workflow(
                PlanningSeedWorkflow.run,
                PlanningRecoveryInput(
                    deps=tuple(value.deps for value in values),
                    validations=tuple(value.validation for value in values),
                ),
                id=f"summary-recovery-seed-{uuid.uuid4()}",
                task_queue=queue,
            )
        assert seeded == 14
        assert provider_calls == 15

        async with db.scoped(url, SCOPE) as conn:
            unsettled = await (
                await conn.execute(
                    """
                    SELECT id, operation_id, owner_token, usage
                      FROM harness_attempt
                     WHERE run_id = %s ORDER BY created_at, id
                    """,
                    (run_id,),
                )
            ).fetchall()
        assert len(unsettled) == 15
        for index, attempt in enumerate(unsettled, start=1):
            await ledger.reconcile_cost(
                url,
                scope=SCOPE,
                source_id=source_id,
                run_id=run_id,
                operation_id=attempt["operation_id"],
                attempt_id=attempt["id"],
                owner_token=str(attempt["owner_token"]),
                observed_cost_micros=index,
                usage=cast("dict[str, Any]", attempt["usage"]),
            )

        async with db.scoped(url, SCOPE) as conn:
            prior_reports = await (
                await conn.execute(
                    """
                    SELECT id, fingerprint, sha256, size_bytes, storage_key, metadata
                      FROM harness_artifact
                     WHERE source_id = %s AND kind = 'checks'
                       AND metadata->>'format' = 'chapter-summary-grounding/1'
                     ORDER BY metadata->>'windowId'
                    """,
                    (source_id,),
                )
            ).fetchall()
            prior_attempts = await (
                await conn.execute(
                    """
                    SELECT a.id, a.operation_id, a.attempt_number, a.state,
                           a.estimated_cost_micros, a.actual_cost_micros, a.cost_status,
                           a.request_hash, a.result_artifact_id, a.usage,
                           r.id AS reservation_id, r.state AS reservation_state,
                           r.amount_micros, r.settled_micros, r.settled_at
                      FROM harness_attempt a
                      JOIN harness_reservation r ON r.attempt_id = a.id
                     WHERE a.run_id = %s
                     ORDER BY a.created_at, a.id
                    """,
                    (run_id,),
                )
            ).fetchall()
        assert len(prior_reports) == 14
        prior_identities = [dict(row) for row in prior_reports]
        assert len(prior_attempts) == 15
        prior_attempt_identities = [dict(row) for row in prior_attempts]

        await update_stage(
            url,
            StageUpdate(
                scope_organization_id=SCOPE.organizationId,
                scope_user_id=SCOPE.userId,
                source_id=source_id,
                run_id=run_id,
                expected_stage="planning",
                expected_revision=0,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message="The summary units do not exactly cover their source window.",
            ),
        )
        retry = await apply_operational_review(
            url,
            request=ChapterReviewInput(
                action=ChapterReviewAction.retry,
                baseRevision=0,
                boundaryId=None,
                budgetMicros=None,
                mutationKey=uuid.uuid4(),
                otherSectionId=None,
                reason="Revalidate the saved summary responses.",
                runId=run_id,
                scope=SCOPE,
                sectionId=None,
                sourceId=source_id,
                targetRevision=None,
                targetTimeMs=None,
            ),
            max_run_budget_micros=1_000_000,
        )
        assert retry.state == "applied"

        trapped_calls = 0

        async def forbidden_response(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            nonlocal trapped_calls
            _ = messages, info
            trapped_calls += 1
            message = "same-run planning recovery attempted another provider dispatch"
            raise AssertionError(message)

        def refusing_factory(route: RouteEntry) -> FunctionModel:
            _ = route
            return FunctionModel(forbidden_response, model_name="forbidden-summary-model")

        configure_model_runtime(
            ModelRuntime(
                database_url=url,
                store=cast("S3Store", store),
                cassette_store=CassetteStore(tmp_path),
                model_factory=refusing_factory,
            )
        )
        recovery_queue = f"summary-recovery-resume-{uuid.uuid4()}"
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            Worker(
                environment.client,
                task_queue=recovery_queue,
                workflows=[PlanningRecoveryWorkflow],
                activities=[
                    harness_activities.validate_chapter_summary,
                    harness_activities.prepare_global_chapter_proposal,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            recovered = await environment.client.execute_workflow(
                PlanningRecoveryWorkflow.run,
                PlanningRecoveryInput(
                    deps=tuple(value.deps for value in values),
                    validations=tuple(value.validation for value in values),
                ),
                id=f"summary-recovery-resume-{uuid.uuid4()}",
                task_queue=recovery_queue,
            )
        assert trapped_calls == 0
        assert recovered.global_plan.prompt is not None
        assert len(recovered.global_plan.input_artifacts) == 15

        async with db.scoped(url, SCOPE) as conn:
            after_reports = await (
                await conn.execute(
                    """
                    SELECT id, fingerprint, sha256, size_bytes, storage_key, metadata
                      FROM harness_artifact
                     WHERE source_id = %s AND kind = 'checks'
                       AND metadata->>'format' = 'chapter-summary-grounding/1'
                     ORDER BY metadata->>'windowId'
                    """,
                    (source_id,),
                )
            ).fetchall()
            facts = await (
                await conn.execute(
                    """
                    SELECT dispatch_count, repair_count, spent_micros, reserved_micros,
                           status, stage,
                           (SELECT count(*)::int FROM harness_operation WHERE run_id = %s)
                               AS operation_count,
                           (SELECT count(*)::int FROM harness_attempt WHERE run_id = %s)
                               AS attempt_count
                      FROM harness_run WHERE id = %s
                    """,
                    (run_id, run_id, run_id),
                )
            ).fetchone()
            after_attempts = await (
                await conn.execute(
                    """
                    SELECT a.id, a.operation_id, a.attempt_number, a.state,
                           a.estimated_cost_micros, a.actual_cost_micros, a.cost_status,
                           a.request_hash, a.result_artifact_id, a.usage,
                           r.id AS reservation_id, r.state AS reservation_state,
                           r.amount_micros, r.settled_micros, r.settled_at
                      FROM harness_attempt a
                      JOIN harness_reservation r ON r.attempt_id = a.id
                     WHERE a.run_id = %s
                     ORDER BY a.created_at, a.id
                    """,
                    (run_id,),
                )
            ).fetchall()
        assert len(after_reports) == 15
        assert [dict(row) for row in after_reports[:14]] == prior_identities
        assert [dict(row) for row in after_attempts] == prior_attempt_identities
        assert facts == {
            "attempt_count": 15,
            "dispatch_count": 15,
            "operation_count": 15,
            "repair_count": 0,
            "reserved_micros": 0,
            "spent_micros": 120,
            "stage": "evidence",
            "status": "pending",
        }
        recovered_report_body = await artifacts.read_artifact_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            artifact_id=after_reports[14]["id"],
        )
        recovered_report = read_summary_grounding_report(recovered_report_body)
        assert isinstance(recovered_report, SummaryGroundingReportV2)
        assert recovered_report.coverageDiagnostic.gapSentenceCount == 1
        assert recovered_report.coverageDiagnostic.overlapSentenceCount == 0
        assert recovered_report.coverageDiagnostic.orderingViolationCount == 0
        assert recovered_report.coverageFallback.firstSentenceId == "s0028"
        assert recovered_report.coverageFallback.lastSentenceId == "s0029"
    finally:
        clear_model_runtime()
        await db.close_pool()
