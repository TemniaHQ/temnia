"""Actual Temporal coverage for the multi-window chapter hierarchy path."""

from __future__ import annotations

import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

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
from temnia_pipeline.harness.models import HierarchicalSummaryV1
from temnia_pipeline.harness.queues import control_task_queue
from temnia_pipeline.harness.routes import RouteSnapshot, select_route
from temnia_pipeline.harness.runtime_types import (
    BuildEvidenceRequest,
    CompileProposalRequest,
    CompileProposalResult,
    EvidenceResult,
    MarkRunFailedRequest,
    PinnedSource,
    PinnedTranscript,
    PlanningWindow,
    PreparePlanningRequest,
    ProposalPlan,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
    WorkflowIdentity,
)
from temnia_pipeline.harness.settings import HarnessSettings
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
        brief=request.request.brief,
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
        self.malformed = False

    async def run(self, _prompt: str, **kwargs: object) -> SimpleNamespace:
        deps = cast("Any", kwargs["deps"])
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

    async def run(self, prompt: str, **_kwargs: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        output = ChapterProposal.model_validate(
            {
                "sections": [
                    {
                        "firstSentenceId": "s000000",
                        "id": "section-a",
                        "kind": "keep",
                        "lastSentenceId": "s000001",
                        "quoteWordIds": ["w000000"],
                        "reason": "First grounded range.",
                        "title": "First",
                    },
                    {
                        "firstSentenceId": "s000002",
                        "id": "section-b",
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
        )
        return SimpleNamespace(output=output)


class HierarchyActivities:
    """Small activity shell around the real hierarchy-validation activity."""

    def __init__(self, routes: RouteSnapshot, settings: HarnessSettings) -> None:
        self.routes = routes
        self.settings = settings
        self.start_count = 0
        self.compiled: list[ChapterProposal] = []
        self.failures: list[MarkRunFailedRequest] = []

    @activity.defn(name="start_chapter_run")
    async def start(self, request: StartRunRequest) -> StartRunResult:
        self.start_count += 1
        return StartRunResult(
            created=self.start_count == 1,
            run=_snapshot(request, self.routes),
        )

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
            hierarchy.prepare_global_chapter_proposal,
            self.compile,
            self.cleanup_source_cache,
        )

    def control(self) -> Sequence[Any]:
        return (self.start, self.update, self.mark_failed)


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

    async def fake_get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(brief="Preserve every original sentence.", route_snapshot=routes)

    async def fake_read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return cast("dict[str, object]", EVIDENCE.model_dump(mode="json"))

    monkeypatch.setattr(activities_module.runs, "get_run", fake_get_run)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", fake_read)
    monkeypatch.setattr(workflows_module, "chapter_summarize_v1", summary)
    monkeypatch.setattr(workflows_module, "chapter_propose_v1", proposal)
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
        result = cast("Any", await _run(environment, queue, _request()))
    assert result.status == HarnessRunStatus.needs_review
    assert len(shell.compiled) == 1
    assert [
        (section.firstSentenceId, section.lastSentenceId) for section in shell.compiled[0].sections
    ] == [("s000000", "s000001"), ("s000002", "s000003")]
    assert len(proposal.prompts) == 1
    assert "s000000" in proposal.prompts[0]
    assert "s000003" in proposal.prompts[0]


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
            "The chapter workflow stopped after a known activity failure."
        )

        summary.malformed = False
        recovered = cast("Any", await _run(environment, queue, _request()))
    assert recovered.status == HarnessRunStatus.needs_review
    assert shell.start_count == 2
    assert len(shell.compiled) == 1
