"""Actual Temporal coverage for the multi-window chapter hierarchy path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from pydantic_ai.exceptions import UnexpectedModelBehavior
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from temnia_pipeline.contracts import (
    ChapterProposal,
    ChapterRunInput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    Scope,
)
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness import workflows as workflows_module
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.models import CompactChapterProposal, HierarchicalSummaryV1
from temnia_pipeline.harness.queues import control_task_queue
from temnia_pipeline.harness.routes import ContextWindowExceeded, RouteSnapshot, select_route
from temnia_pipeline.harness.runtime_types import (
    BuildEvidenceRequest,
    ClaimRepairRequest,
    CompileProposalRequest,
    CompileProposalResult,
    EvidenceResult,
    MarkRunFailedRequest,
    PinnedSource,
    PinnedTranscript,
    PlanningWindow,
    PreparePlanningRequest,
    ProposalDiagnostic,
    ProposalDiagnosticIssue,
    ProposalDiagnosticRequest,
    ProposalPlan,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
    ValidatedSummary,
    ValidateSummaryRequest,
    WorkflowIdentity,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.summary_grounding import SummaryGroundingReport
from temnia_pipeline.harness.workflows import ChapterRunWorkflow

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / "fixtures/harness"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
EVIDENCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")
REQUEST_KEY = uuid.UUID("0192e8a0-0000-7000-8000-000000000444")
TRANSCRIPT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000555")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def _settings() -> tuple[HarnessSettings, RouteSnapshot]:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_MAX_REPAIRS": "0",
            "HARNESS_RECORDED_FIXTURE_PATH": str(FIXTURES / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(FIXTURES / "routes.synthetic.json"),
        }
    )
    assert settings.validate_boot() == routes
    return settings, routes


def _evidence() -> HarnessEvidence:
    words: list[dict[str, object]] = []
    sentences: list[dict[str, object]] = []
    for index in range(4):
        words.append(
            {
                "confidence": 0.99,
                "endMs": index * 1000 + 900,
                "id": f"w{index:06d}",
                "lineageIds": [],
                "speaker": "speaker-a",
                "startMs": index * 1000,
                "text": f"word-{index}",
                "timing": "aligned",
                "wordIndex": index,
            }
        )
        sentences.append(
            {
                "endMs": index * 1000 + 900,
                "id": f"s{index:06d}",
                "speakers": ["speaker-a"],
                "startMs": index * 1000,
                "text": f"Original sentence {index}.",
                "wordIds": [f"w{index:06d}"],
            }
        )
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {},
            "durationMs": 4000,
            "frameRate": None,
            "modelVersions": {"sentence": "fixture"},
            "pauses": [],
            "sentences": sentences,
            "shots": [],
            "sourceFingerprint": "a" * 64,
            "sourceId": str(SOURCE_ID),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": "fixture",
                "detectorHash": "b" * 64,
                "detectorRevision": "1",
                "intervals": [],
                "status": "clear",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(TRANSCRIPT_ID),
            "transcriptRevision": 1,
            "transcriptSha256": "c" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": words,
        }
    )


EVIDENCE = _evidence()
EVIDENCE_REF = HarnessArtifactRef(
    fingerprint="d" * 64,
    id=EVIDENCE_ID,
    kind=HarnessArtifactKind.evidence,
    sha256="e" * 64,
    sizeBytes=1,
    storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/evidence.json",
)


def _snapshot(
    request: StartRunRequest,
    routes: RouteSnapshot,
    *,
    status: HarnessRunStatus = HarnessRunStatus.running,
) -> RunSnapshot:
    return RunSnapshot(
        accepted_revision=None,
        brief=request.request.brief or "",
        budget_micros=request.request.budgetMicros,
        config=request.request.config,
        current_revision=0,
        dispatch_count=0,
        error_message=None,
        evidence_artifact_id=EVIDENCE_ID,
        id=request.request.runId,
        initial_budget_micros=request.request.budgetMicros,
        repair_count=0,
        request_key=request.request.requestKey,
        reserved_micros=0,
        route_snapshot=routes,
        source=PinnedSource(
            duration_ms=4000,
            size_bytes=1,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/master.mp4",
        ),
        source_id=request.request.sourceId,
        spent_micros=0,
        stage="planning",
        status=status,
        transcript=PinnedTranscript(
            revision=1,
            sha256="c" * 64,
            size_bytes=1,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/transcript.json",
            transcript_id=TRANSCRIPT_ID,
        ),
        workflow_id=request.workflow.workflow_id,
        workflow_run_id=request.workflow.workflow_run_id,
    )


class FakeSummaryAgent:
    """Deterministic structured output at the exact model call sites in the workflow."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.malformed = False
        self.stages: list[str] = []
        self.unexpected = False

    async def run(self, _prompt: str, **kwargs: object) -> SimpleNamespace:
        deps = cast("Any", kwargs["deps"])
        self.stages.append(str(deps.stage))
        self.events.append(f"model:{deps.stage}")
        if self.unexpected:
            message = "fixture response has an invalid schema"
            raise UnexpectedModelBehavior(message)
        first = str(deps.operation_inputs["firstSentenceId"])
        last = str(deps.operation_inputs["lastSentenceId"])
        if self.malformed:
            first = "s999999"
        output = HierarchicalSummaryV1.model_validate(
            {
                "units": [
                    {
                        "firstSentenceId": first,
                        "id": f"summary-{deps.stage}",
                        "lastSentenceId": last,
                        "quoteWordIds": [f"w{int(last[1:]):06d}"],
                        "text": f"Grounded range {first} through {last}.",
                    }
                ],
                "version": 1,
            }
        )
        return SimpleNamespace(output=output)


class FakeProposalAgent:
    """Return one exact-cover proposal while retaining the global prompt for assertion."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.deps: list[Any] = []
        self.unexpected_count = 0
        self.raised_error: Exception | None = None

    async def run(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.deps.append(kwargs["deps"])
        if self.raised_error is not None:
            raise self.raised_error
        if self.unexpected_count > 0:
            self.unexpected_count -= 1
            message = "fixture strict output failure"
            raise UnexpectedModelBehavior(message)
        output = CompactChapterProposal.model_validate_json(
            json.dumps(
                {
                    "sections": [
                        {
                            "firstSentenceId": "s000000",
                            "kind": "keep",
                            "lastSentenceId": "s000001",
                            "quoteWordIds": ["w000000"],
                            "reason": "First grounded range.",
                            "title": "First",
                        },
                        {
                            "firstSentenceId": "s000002",
                            "kind": "keep",
                            "lastSentenceId": "s000003",
                            "quoteWordIds": ["w000003"],
                            "reason": "Second grounded range.",
                            "title": "Second",
                        },
                    ],
                    "summary": "Complete original-ID partition.",
                    "version": 1,
                }
            ),
            strict=True,
        )
        return SimpleNamespace(output=output)


class HierarchyActivities:
    """Small activity shell around the real hierarchy-validation activity."""

    def __init__(self, routes: RouteSnapshot, settings: HarnessSettings) -> None:
        self.routes = routes
        self.settings = settings
        self.start_count = 0
        self.repair_count = 0
        self.compiled: list[ChapterProposal] = []
        self.failures: list[MarkRunFailedRequest] = []
        self.summary_validations: list[ValidateSummaryRequest] = []
        self.summary_validation_refusal: str | None = None
        self.summary_events: list[str] = []
        self.diagnostics: list[ProposalDiagnosticRequest] = []

    @activity.defn(name="start_chapter_run")
    async def start(self, request: StartRunRequest) -> StartRunResult:
        self.start_count += 1
        return StartRunResult(
            created=self.start_count == 1,
            run=_snapshot(request, self.routes).model_copy(
                update={"repair_count": self.repair_count}
            ),
        )

    @activity.defn(name="claim_chapter_repair")
    async def claim_repair(self, request: ClaimRepairRequest) -> RunSnapshot:
        assert request.expected_repair_count == self.repair_count
        self.repair_count += 1
        start = StartRunRequest(
            request=_request(),
            workflow=request.workflow,
        )
        return _snapshot(start, self.routes).model_copy(update={"repair_count": self.repair_count})

    @activity.defn(name="build_chapter_evidence")
    async def build(self, _request: BuildEvidenceRequest) -> EvidenceResult:
        return EvidenceResult(
            artifact=EVIDENCE_REF,
            duration_ms=EVIDENCE.durationMs,
            lexical_state="present",
            sentence_count=len(EVIDENCE.sentences),
            word_count=len(EVIDENCE.words),
        )

    @activity.defn(name="prepare_chapter_proposal")
    async def prepare(self, _request: PreparePlanningRequest) -> ProposalPlan:
        return ProposalPlan(
            route=select_route(self.routes, "propose"),
            summary_route=select_route(self.routes, "summary"),
            windows=(
                PlanningWindow(
                    first_sentence_id="s000000",
                    id="window-0",
                    last_sentence_id="s000001",
                    prompt="Summarize original sentences s000000 through s000001.",
                    sentence_count=2,
                ),
                PlanningWindow(
                    first_sentence_id="s000002",
                    id="window-1",
                    last_sentence_id="s000003",
                    prompt="Summarize original sentences s000002 through s000003.",
                    sentence_count=2,
                ),
            ),
        )

    @activity.defn(name="compile_chapter_proposal")
    async def compile(self, request: CompileProposalRequest) -> CompileProposalResult:
        self.compiled.append(request.proposal)
        return CompileProposalResult(refusal="Captured the grounded global proposal.")

    @activity.defn(name="diagnose_chapter_proposal")
    async def diagnose(self, request: ProposalDiagnosticRequest) -> ProposalDiagnostic:
        self.diagnostics.append(request)
        suffix = request.model_stage.replace(":", "-")
        compiler_failure = request.compiler_refusal is not None
        return ProposalDiagnostic(
            artifact=HarnessArtifactRef(
                fingerprint="4" * 64,
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"diagnostic:{suffix}"),
                kind=HarnessArtifactKind.checks,
                sha256="5" * 64,
                sizeBytes=1,
                storageKey=f"diagnostics/{suffix}.json",
            ),
            response=HarnessArtifactRef(
                fingerprint="6" * 64,
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"response:{suffix}"),
                kind=HarnessArtifactKind.model_response,
                sha256="7" * 64,
                sizeBytes=1,
                storageKey=f"responses/{suffix}.json",
            ),
            code="compiler_refusal" if compiler_failure else "invalid_schema",
            message=(
                "The proposal does not exactly cover the source."
                if compiler_failure
                else "The proposal response has invalid fields."
            ),
            issues=(ProposalDiagnosticIssue(path="sections", code="incomplete_source_cover"),),
            compiler_code="incomplete_source_cover" if compiler_failure else None,
        )

    @activity.defn(name="validate_chapter_summary")
    async def validate_summary(self, request: ValidateSummaryRequest) -> ValidatedSummary:
        self.summary_validations.append(request)
        self.summary_events.append(f"validate:{request.model_stage}")
        if self.summary_validation_refusal is not None:
            return ValidatedSummary(refusal=self.summary_validation_refusal)
        reference = HarnessArtifactRef(
            fingerprint=uuid.uuid5(uuid.NAMESPACE_URL, f"fingerprint:{request.model_stage}").hex
            * 2,
            id=uuid.uuid5(uuid.NAMESPACE_URL, request.model_stage),
            kind=HarnessArtifactKind.checks,
            sha256=uuid.uuid5(uuid.NAMESPACE_DNS, request.model_stage).hex * 2,
            sizeBytes=1,
            storageKey=f"grounding/{request.model_stage}.json",
        )
        return ValidatedSummary(summary=request.summary, artifact=reference)

    @activity.defn(name="update_chapter_run_stage")
    async def update(self, request: StageUpdate) -> RunSnapshot:
        start = StartRunRequest(
            request=_request(),
            workflow=WorkflowIdentity(workflow_id="test-owner", workflow_run_id="test-owner-run"),
        )
        return _snapshot(start, self.routes, status=request.status).model_copy(
            update={"error_message": request.error_message, "stage": request.next_stage}
        )

    @activity.defn(name="mark_chapter_run_failed")
    async def mark_failed(self, request: MarkRunFailedRequest) -> bool:
        self.failures.append(request)
        return True

    @activity.defn(name="cleanup_chapter_source_cache")
    async def cleanup_source_cache(self, _request: RunRef) -> bool:
        return True

    def heavy(self, hierarchy: HarnessActivities) -> Sequence[Any]:
        return (
            self.build,
            self.prepare,
            self.validate_summary,
            hierarchy.prepare_global_chapter_proposal,
            self.compile,
            self.diagnose,
            self.cleanup_source_cache,
        )

    def control(self) -> Sequence[Any]:
        return (self.start, self.claim_repair, self.update, self.mark_failed)


def _request() -> ChapterRunInput:
    settings, _ = _settings()
    return ChapterRunInput(
        brief="Preserve every original sentence.",
        budgetMicros=1_000_000,
        config=settings.allowed_config(),
        requestKey=REQUEST_KEY,
        runId=RUN_ID,
        scope=SCOPE,
        sourceId=SOURCE_ID,
    )


async def _run(
    environment: WorkflowEnvironment,
    queue: str,
    request: ChapterRunInput,
) -> object:
    return await environment.client.execute_workflow(
        ChapterRunWorkflow.run,
        request,
        id=f"hierarchy-{uuid.uuid4()}",
        task_queue=queue,
    )


async def _run_with_history(
    environment: WorkflowEnvironment,
    queue: str,
    request: ChapterRunInput,
) -> tuple[object, WorkflowHistory]:
    handle = await environment.client.start_workflow(
        ChapterRunWorkflow.run,
        request,
        id=f"hierarchy-history-{uuid.uuid4()}",
        task_queue=queue,
    )
    result = await handle.result()
    return result, await handle.fetch_history()


@pytest.fixture
def hierarchy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent]:
    settings, routes = _settings()
    shell = HierarchyActivities(routes, settings)
    context = SimpleNamespace(settings=SimpleNamespace(database_url="unused"), store=None)
    hierarchy = HarnessActivities(cast("Any", context), settings, routes)
    summary = FakeSummaryAgent()
    proposal = FakeProposalAgent()
    summary.events = shell.summary_events

    async def fake_get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            brief="Preserve every original sentence.",
            evidence_artifact_id=EVIDENCE_ID,
            route_snapshot=routes,
        )

    async def fake_read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return cast("dict[str, object]", EVIDENCE.model_dump(mode="json"))

    async def fake_find(*_args: object, **_kwargs: object) -> object:
        return activities_module.artifacts.HarnessArtifact(
            id=EVIDENCE_REF.id,
            organization_id=SCOPE.organizationId,
            source_id=SOURCE_ID,
            kind=EVIDENCE_REF.kind.value,
            fingerprint=EVIDENCE_REF.fingerprint,
            storage_key=EVIDENCE_REF.storageKey,
            sha256=EVIDENCE_REF.sha256,
            size_bytes=EVIDENCE_REF.sizeBytes,
            metadata={"format": "chapter-evidence/1"},
            transcript_id=TRANSCRIPT_ID,
            transcript_revision=1,
            dependency_ids=(),
        )

    monkeypatch.setattr(activities_module.runs, "get_run", fake_get_run)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", fake_read)
    monkeypatch.setattr(activities_module.artifacts, "find_artifact", fake_find)

    async def fake_grounding_report(
        *, scope: object, source_id: object, reference: HarnessArtifactRef
    ) -> SummaryGroundingReport:
        del scope, source_id
        validation = next(
            request
            for request in reversed(shell.summary_validations)
            if uuid.uuid5(uuid.NAMESPACE_URL, request.model_stage) == reference.id
        )
        return SummaryGroundingReport(
            runId=RUN_ID,
            hierarchyLevel=validation.hierarchy_level,
            modelStage=validation.model_stage,
            windowId=validation.window.id,
            firstSentenceId=validation.window.first_sentence_id,
            lastSentenceId=validation.window.last_sentence_id,
            windowSentenceCount=validation.window.sentence_count,
            windowPromptSha256=hashlib.sha256(validation.window.prompt.encode()).hexdigest(),
            evidence=validation.evidence,
            rawResponse=HarnessArtifactRef(
                fingerprint="1" * 64,
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"raw:{validation.model_stage}"),
                kind=HarnessArtifactKind.model_response,
                sha256="2" * 64,
                sizeBytes=1,
                storageKey=f"responses/{validation.model_stage}.json",
            ),
            inputArtifacts=validation.input_artifacts,
            sourceSummarySha256="3" * 64,
            normalizedSummary=HierarchicalSummaryV1.model_validate(validation.summary),
        )

    monkeypatch.setattr(hierarchy, "_grounding_report", fake_grounding_report)
    monkeypatch.setattr(workflows_module, "chapter_summarize_v1", summary)
    monkeypatch.setattr(workflows_module, "chapter_propose_v2", proposal)
    return shell, hierarchy, summary, proposal


async def test_temporal_multi_window_hierarchy_reaches_original_id_proposal(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, _, proposal = hierarchy_runtime
    queue = f"chapter-hierarchy-valid-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        raw_result, history = await _run_with_history(environment, queue, _request())
        result = cast("Any", raw_result)
    assert result.status == HarnessRunStatus.needs_review
    assert len(shell.compiled) == 1
    assert [
        (section.firstSentenceId, section.lastSentenceId) for section in shell.compiled[0].sections
    ] == [("s000000", "s000001"), ("s000002", "s000003")]
    assert len(proposal.prompts) == 1
    assert "s000000" in proposal.prompts[0]
    assert "s000003" in proposal.prompts[0]
    assert len(shell.summary_validations) == 2
    assert len(proposal.deps[0].input_artifact_ids) == 3
    assert len(proposal.deps[0].operation_inputs["groundingArtifacts"]) == 2
    activity_names = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in cast("Any", history).events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    marker_names = [
        event.marker_recorded_event_attributes.marker_name
        for event in cast("Any", history).events
        if event.HasField("marker_recorded_event_attributes")
    ]
    assert marker_names == ["core_patch", "core_patch", "core_patch"]
    assert activity_names.count("validate_chapter_summary") == 2
    assert shell.summary_events == [
        "model:summary:window-0",
        "validate:summary:window-0",
        "model:summary:window-1",
        "validate:summary:window-1",
    ]
    await Replayer(
        workflows=[ChapterRunWorkflow],
        data_converter=pydantic_data_converter,
        plugins=[PydanticAIPlugin()],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_temporal_summary_refusal_stops_before_the_next_model_call(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, summary, proposal = hierarchy_runtime
    shell.summary_validation_refusal = "The summary window cannot be grounded."
    queue = f"chapter-hierarchy-refusal-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        result = cast("Any", await _run(environment, queue, _request()))
    assert result.status == HarnessRunStatus.needs_review
    assert result.errorMessage == "The summary window cannot be grounded."
    assert summary.stages == ["summary:window-0"]
    assert len(shell.summary_validations) == 1
    assert proposal.prompts == []


async def test_temporal_proposal_repair_uses_diagnostic_and_stops_after_bound(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, _, proposal = hierarchy_runtime
    request = _request()
    request = request.model_copy(
        update={"config": request.config.model_copy(update={"maxRepairs": 1})}
    )
    queue = f"chapter-proposal-repair-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        result = cast("Any", await _run(environment, queue, request))

    assert result.status == HarnessRunStatus.needs_review
    assert result.errorMessage == "The proposal does not exactly cover the source."
    assert shell.repair_count == 1
    assert len(shell.compiled) == 2
    assert len(shell.diagnostics) == 2
    assert [item.model_stage for item in shell.diagnostics] == [
        "proposal:v2:global",
        "proposal:v2:repair:1",
    ]
    assert [deps.stage for deps in proposal.deps] == [
        "proposal:v2:global",
        "proposal:v2:repair:1",
    ]
    assert proposal.deps[0].route.family != proposal.deps[1].route.family
    assert "REPAIR_FEEDBACK_JSON" not in proposal.prompts[0]
    assert "REPAIR_FEEDBACK_JSON" in proposal.prompts[1]
    assert "incomplete_source_cover" in proposal.prompts[1]
    assert len(proposal.deps[1].operation_inputs["repairArtifacts"]) == 2
    assert len(proposal.deps[1].input_artifact_ids) == 5


async def test_temporal_strict_proposal_failure_is_diagnosed_at_repair_limit(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, _, proposal = hierarchy_runtime
    proposal.unexpected_count = 1
    queue = f"chapter-proposal-strict-diagnostic-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        result = cast("Any", await _run(environment, queue, _request()))

    assert result.status == HarnessRunStatus.needs_review
    assert result.errorMessage == "The proposal response has invalid fields."
    assert len(shell.diagnostics) == 1
    assert shell.diagnostics[0].compiler_refusal is None
    assert shell.diagnostics[0].model_stage == "proposal:v2:global"
    assert shell.compiled == []
    assert shell.repair_count == 0


async def test_temporal_repair_context_refuses_before_claim(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, hierarchy, _, proposal = hierarchy_runtime
    request = _request()
    request = request.model_copy(
        update={"config": request.config.model_copy(update={"maxRepairs": 1})}
    )

    def refuse_repair(*_args: object, **_kwargs: object) -> str:
        message = "fixture bounded context"
        raise ContextWindowExceeded(message)

    monkeypatch.setattr(workflows_module, "render_proposal_repair_prompt", refuse_repair)
    queue = f"chapter-proposal-repair-context-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        result = cast("Any", await _run(environment, queue, request))

    assert result.status == HarnessRunStatus.needs_review
    assert result.errorMessage == "The diagnosed proposal repair exceeds the bounded model context."
    assert len(shell.diagnostics) == 1
    assert len(proposal.prompts) == 1
    assert shell.repair_count == 0


async def test_temporal_unknown_proposal_outcome_never_diagnoses_or_repairs(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, _, proposal = hierarchy_runtime
    unknown = ApplicationError(
        "fixture provider outcome is unknown",
        type="OutcomeUnknown",
        non_retryable=True,
    )
    activity_error = ActivityError(
        "model activity outcome is unknown",
        scheduled_event_id=1,
        started_event_id=2,
        identity="fixture-worker",
        activity_type="run_agent",
        activity_id="fixture-activity",
        retry_state=None,
    )
    activity_error.__cause__ = unknown
    proposal.raised_error = activity_error
    request = _request()
    request = request.model_copy(
        update={"config": request.config.model_copy(update={"maxRepairs": 1})}
    )
    queue = f"chapter-proposal-unknown-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(_run(environment, queue, request), timeout=10)

    assert len(proposal.prompts) == 1
    assert shell.diagnostics == []
    assert shell.compiled == []
    assert shell.repair_count == 0


async def test_temporal_malformed_summary_refuses_without_another_model_call(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, summary, proposal = hierarchy_runtime
    summary.unexpected = True
    queue = f"chapter-hierarchy-schema-refusal-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        result = cast("Any", await _run(environment, queue, _request()))
    assert result.status == HarnessRunStatus.needs_review
    assert result.errorMessage == "A summary response did not match the required structure."
    assert summary.stages == ["summary:window-0"]
    assert shell.summary_validations == []
    assert proposal.prompts == []


async def test_temporal_foreign_summary_fails_visibly_then_recovers(
    hierarchy_runtime: tuple[
        HierarchyActivities, HarnessActivities, FakeSummaryAgent, FakeProposalAgent
    ],
) -> None:
    shell, hierarchy, summary, _ = hierarchy_runtime
    summary.malformed = True
    queue = f"chapter-hierarchy-recovery-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterRunWorkflow],
                activities=list(shell.heavy(hierarchy)),
                workflow_runner=UnsandboxedWorkflowRunner(),
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=list(shell.control()),
            )
        )
        with pytest.raises(WorkflowFailureError):
            await _run(environment, queue, _request())
        assert len(shell.failures) == 1
        assert shell.failures[0].status == HarnessRunStatus.failed
        assert shell.failures[0].error_message == (
            "The run stopped after an activity failure: RuntimeError."
        )

        summary.malformed = False
        recovered = cast("Any", await _run(environment, queue, _request()))
    assert recovered.status == HarnessRunStatus.needs_review
    assert shell.start_count == 2
    assert len(shell.compiled) == 1
