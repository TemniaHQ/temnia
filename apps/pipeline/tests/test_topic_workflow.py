"""Exercise the topic program and real prompt/compiler/render seams without paid calls.

Temporal activity dispatch is replaced at its boundary; these tests do not claim
server replay or provider integration coverage.
"""

# pyright: reportPrivateUsage=false
# Renderer ownership primitives are replaced only at the test boundary.
# ruff: noqa: SLF001
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    Status2,
    TopicAssessment,
    TopicAssessmentCandidate,
    TopicEditSpec,
    TopicProposal,
    TopicRenders,
    TopicSourceReview,
)
from temnia_pipeline.harness import topic_render, topic_workflow
from temnia_pipeline.harness.editorial_policy import TOPIC_POLICY
from temnia_pipeline.harness.rendering import kept_sections
from temnia_pipeline.harness.runtime_types import (
    EvidenceResult,
    RenderRevisionRequest,
    RenderRevisionResult,
    RunSnapshot,
    StartRunRequest,
    StartRunResult,
    WorkflowIdentity,
)
from temnia_pipeline.harness.topic_activities import TopicActivities
from temnia_pipeline.harness.topic_compiler import compile_topics
from temnia_pipeline.harness.topic_editorial import cold_review_key, criteria, editorial_routes
from temnia_pipeline.harness.topic_runtime import (
    SaveTopicAssessment,
    SaveTopicProposal,
    TopicAssessmentResult,
    TopicCompilation,
    TopicContext,
    TopicProposalResult,
    TopicRenderResult,
)
from test_harness_hierarchy_workflow import (
    EVIDENCE_REF,
    SOURCE_ID,
    _request,
    _settings,
    _snapshot,
)
from test_topic_compiler import _candidate, _case
from test_topic_editorial import _cold, _criterion, _source

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

    from temnia_pipeline.harness.models import HarnessModelDeps


EVIDENCE = _case().model_copy(update={"sourceId": SOURCE_ID})


def _ref(label: str, content: object, kind: HarnessArtifactKind) -> HarnessArtifactRef:
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    return HarnessArtifactRef(
        id=uuid5(NAMESPACE_URL, f"topic-test:{label}:{digest}"),
        kind=kind,
        fingerprint=digest,
        sha256=digest,
        sizeBytes=1,
        storageKey=f"topic-tests/{label}/{digest}.json",
    )


def _proposal(*, repaired: bool = False) -> TopicProposal:
    return TopicProposal(
        version=1,
        summary="Two worthwhile discussions.",
        candidates=[_candidate("first", 0, 1), _candidate("second", 1, 3 if repaired else 2)],
    )


class _Agent:
    def __init__(self, outputs: Sequence[object]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, HarnessModelDeps]] = []

    async def run(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((prompt, cast("HarnessModelDeps", kwargs["deps"])))
        assert self.outputs, "unexpected additional model dispatch"
        return SimpleNamespace(output=self.outputs.pop(0))


class _Program:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, mode: str) -> None:
        _, self.routes = _settings()
        initial = _request()
        self.request = initial.model_copy(
            update={"brief": "", "config": initial.config.model_copy(update={"maxRepairs": 3})}
        )
        self.run = _snapshot(
            StartRunRequest(
                request=self.request,
                editorial_policy=TOPIC_POLICY,
                workflow=WorkflowIdentity(workflow_id="topic-test", workflow_run_id="topic-run"),
            ),
            self.routes,
        ).model_copy(update={"editorial_policy": TOPIC_POLICY})
        self.events: list[tuple[str, object]] = []
        self.proposals: dict[UUID, TopicProposal] = {}
        self.assessments: dict[UUID, TopicAssessment] = {}
        self.saved_assessments: list[SaveTopicAssessment] = []
        self.saved_proposals: list[HarnessArtifactRef] = []
        self.compiled_context: TopicContext | None = None
        self.portfolio: TopicEditSpec | None = None
        first = _proposal()
        second = _proposal(repaired=True)
        if mode == "cosmetic":
            second = first.model_copy(deep=True)
            second.summary = "Different prose, exactly the same editorial selections."
            second.candidates[
                1
            ].reason = "A rewritten rationale does not repair the selected video."
        unknown = mode == "unknown"
        self.author = _Agent([first] if unknown else [first, second])
        self.cold = _Agent(
            [
                _cold("first", "unknown" if unknown else "pass"),
                _cold("second", "unknown" if unknown else "pass"),
                _cold("second"),
            ]
        )
        first_source = TopicSourceReview(
            candidates=[
                _source("first", "unknown" if unknown else "pass"),
                _source("second", "unknown" if unknown else "pass"),
            ],
            summary="The complete source is available for comparison.",
        )
        if not unknown:
            first_source.candidates[1].completeContext = _criterion("fail", first=3)
        second_source = TopicSourceReview(
            candidates=[_source("first"), _source("second")],
            summary="Required completion retained.",
        )
        self.source = _Agent([first_source, second_source])
        self.activities = TopicActivities(
            cast("Any", SimpleNamespace(_recorded_output=self.recorded_output))
        )
        monkeypatch.setattr(self.activities, "load", self.load)
        monkeypatch.setattr(self.activities, "assessment", self.assessment)
        monkeypatch.setattr(topic_workflow, "topic_propose_v1", self.author)
        monkeypatch.setattr(topic_workflow, "topic_cold_review_v1", self.cold)
        monkeypatch.setattr(topic_workflow, "topic_source_review_v1", self.source)
        monkeypatch.setattr(topic_workflow.workflow, "execute_activity", self.execute)
        monkeypatch.setattr(topic_workflow.workflow, "info", self.info)

    @staticmethod
    def info() -> SimpleNamespace:
        return SimpleNamespace(
            workflow_id="topic-test", run_id="topic-run", task_queue="topic-tests"
        )

    @staticmethod
    def recorded_output(_seat: str) -> None:
        return None

    async def load(
        self, context: TopicContext
    ) -> tuple[RunSnapshot, HarnessEvidence, TopicProposal | None]:
        return (
            self.run,
            EVIDENCE,
            self.proposals.get(context.proposal.id) if context.proposal else None,
        )

    async def assessment(self, context: TopicContext) -> TopicAssessment | None:
        return self.assessments.get(context.assessment.id) if context.assessment else None

    def save_assessment(self, request: SaveTopicAssessment) -> TopicAssessmentResult:
        self.saved_assessments.append(request)
        assert request.context.proposal is not None
        proposal = self.proposals[request.context.proposal.id]
        cold_by_id = {row.candidateId: row for row in request.cold_reviews}
        source_by_id = (
            {row.candidateId: row for row in request.source_review.candidates}
            if request.source_review
            else {}
        )
        results: list[TopicAssessmentCandidate] = []
        for candidate in proposal.candidates:
            cold, source = cold_by_id.get(candidate.id), source_by_id.get(candidate.id)
            passed = (
                cold is not None
                and source is not None
                and all(
                    value.status.value == "pass"
                    for review in (cold, source)
                    for value in criteria(review)
                )
            )
            results.append(
                TopicAssessmentCandidate(
                    candidateId=candidate.id,
                    status=Status2.passed if passed else Status2.needs_review,
                    coldReview=cold,
                    sourceReview=source,
                    reasons=[] if passed else ["Retained editorial finding."],
                    physicalBoundaryIssues=[],
                )
            )
        assessment = TopicAssessment(
            format="topic-assessment/1",
            runId=self.request.runId,
            proposalSha256=request.context.proposal.sha256,
            evidenceSha256=EVIDENCE_REF.sha256,
            candidates=results,
            summary="Independent retained findings.",
            proposerFamily=request.author_family,
            verifierFamily=request.verifier_family,
        )
        ref = _ref("assessment", assessment.model_dump(mode="json"), HarnessArtifactKind.checks)
        self.assessments[ref.id] = assessment
        return TopicAssessmentResult(
            artifact=ref,
            assessment=assessment,
            all_passed=all(row.status == Status2.passed for row in results),
        )

    async def execute(  # noqa: C901, PLR0911
        self, name: str, request: object, **_kwargs: object
    ) -> object:
        self.events.append((name, request))
        if name == "start_chapter_run":
            return StartRunResult(created=True, run=self.run)
        if name == "build_chapter_evidence":
            return EvidenceResult(
                artifact=EVIDENCE_REF,
                duration_ms=4000,
                lexical_state="present",
                sentence_count=4,
                word_count=4,
            )
        if name == "prepare_topic_plan":
            return await self.activities.prepare(cast("TopicContext", request))
        if name == "save_topic_proposal":
            message = cast("SaveTopicProposal", request)
            ref = _ref(
                "proposal", message.proposal.model_dump(mode="json"), HarnessArtifactKind.proposal
            )
            self.proposals[ref.id] = message.proposal
            self.saved_proposals.append(ref)
            return TopicProposalResult(artifact=ref)
        if name == "save_topic_assessment":
            return self.save_assessment(cast("SaveTopicAssessment", request))
        if name == "claim_chapter_repair":
            self.run = self.run.model_copy(update={"repair_count": self.run.repair_count + 1})
            return self.run
        if name == "compile_topic_portfolio":
            context = cast("TopicContext", request)
            assert context.proposal is not None
            self.compiled_context = context
            self.portfolio = compile_topics(
                EVIDENCE,
                self.proposals[context.proposal.id],
                evidence_artifact_id=EVIDENCE_REF.id,
                evidence_sha256=EVIDENCE_REF.sha256,
            )
            return TopicCompilation(
                artifact=_ref(
                    "edit", self.portfolio.model_dump(mode="json"), HarnessArtifactKind.edit
                ),
                edit=self.portfolio,
            )
        if name == "accept_initial_chapter_revision":
            self.run = self.run.model_copy(update={"current_revision": 1, "stage": "render"})
            return self.run
        if name == "render_topic_revision":
            assert self.portfolio is not None
            return TopicRenderResult(
                descriptor=_ref("render", {}, HarnessArtifactKind.render),
                technical_passed=True,
                count=len(self.portfolio.videos),
            )
        if name == "update_chapter_run_stage":
            update = cast("Any", request)
            self.run = self.run.model_copy(
                update={"stage": update.next_stage, "status": update.status}
            )
            return self.run
        if name in {"cleanup_chapter_source_cache", "mark_chapter_run_failed"}:
            return True
        pytest.fail(f"unexpected activity: {name}")


async def test_topic_workflow_preserves_policy_generic_brief_receipts_and_reserved_critic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Program(monkeypatch, mode="repair")
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    start = cast("StartRunRequest", shell.events[0][1])
    assert start.editorial_policy == TOPIC_POLICY
    assert start.request.brief == ""
    payload = json.loads(shell.author.calls[0][0].split("\nSOURCE DATA\n", 1)[1])
    assert payload["userInstructions"] == ""
    assert len(payload["sourceSentences"]) == 4
    assert "independently understandable" in shell.author.calls[0][0]
    assert len(shell.author.calls) == 2
    assert len(shell.cold.calls) == 3
    assert len(shell.source.calls) == 2
    author, verifier = editorial_routes(shell.routes)
    for _, deps in [*shell.author.calls, *shell.cold.calls, *shell.source.calls]:
        assert deps.operation_config["reservedVerifierFamily"] == verifier.family
        assert deps.program_version == TOPIC_POLICY
    assert all(
        deps.route.family == author.family != verifier.family for _, deps in shell.author.calls
    )
    assert all(
        deps.route.family == verifier.family for _, deps in [*shell.cold.calls, *shell.source.calls]
    )
    for prompt, deps in shell.cold.calls:
        assert deps.input_artifact_ids == (EVIDENCE_REF.id,)
        assert deps.operation_inputs["artifacts"] == [
            {"id": str(EVIDENCE_REF.id), "sha256": EVIDENCE_REF.sha256}
        ]
        assert "previousProposal" not in prompt
        assert "sourceSentences" not in prompt
        assert "requiredContextSpans" not in prompt
    first, repaired = shell.saved_assessments
    assert repaired.cold_stages[0] == first.cold_stages[0]
    assert repaired.cold_reviews[0] == first.cold_reviews[0]
    assert repaired.cold_stages[1] != first.cold_stages[1]
    assert (
        repaired.cold_stages[0] == f"verify:topic:cold:{cold_review_key(_proposal().candidates[0])}"
    )
    assert output.status == HarnessRunStatus.needs_review
    assert shell.run.accepted_revision is None
    assert shell.events[-1][0] == "cleanup_chapter_source_cache"
    assert shell.portfolio is not None
    assert [
        (kept_sections(video.edit)[0].start, kept_sections(video.edit)[0].end)
        for video in shell.portfolio.videos
    ] == [(0, 2), (1, 4)]


async def test_unknown_only_findings_stop_without_spending_on_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Program(monkeypatch, mode="unknown")
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert len(shell.author.calls) == 1
    assert len(shell.saved_assessments) == 1
    assert not any(name == "claim_chapter_repair" for name, _ in shell.events)
    assert output.status == HarnessRunStatus.needs_review
    assert shell.run.accepted_revision is None


async def test_cosmetic_reproposal_retains_original_assessment_without_another_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Program(monkeypatch, mode="cosmetic")
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert len(shell.saved_proposals) == 2
    assert shell.saved_proposals[0].sha256 != shell.saved_proposals[1].sha256
    assert len(shell.cold.calls) == 2
    assert len(shell.source.calls) == 1
    assert len(shell.saved_assessments) == 1
    assert shell.compiled_context is not None
    assert shell.compiled_context.proposal == shell.saved_proposals[0]
    assert shell.compiled_context.assessment is not None
    assert output.status == HarnessRunStatus.needs_review


async def test_real_topic_renderer_uses_distinct_executions_under_one_source_lease(  # noqa: C901, PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Program(monkeypatch, mode="repair")
    portfolio = compile_topics(
        EVIDENCE,
        _proposal(repaired=True),
        evidence_artifact_id=EVIDENCE_REF.id,
        evidence_sha256=EVIDENCE_REF.sha256,
    )
    portfolio_ref = _ref("portfolio", portfolio.model_dump(mode="json"), HarnessArtifactKind.edit)
    rendered_requests: list[RenderRevisionRequest] = []
    published: dict[UUID, object] = {}
    lifecycle: list[str] = []
    owner = SimpleNamespace(
        ctx=SimpleNamespace(settings=SimpleNamespace(database_url="unused"), store=None)
    )

    def enabled() -> None:
        pass

    async def active(_run: object, _revision: int) -> RunSnapshot:
        return shell.run.model_copy(update={"current_revision": 1, "stage": "render"})

    def artifact_ref(_row: object) -> HarnessArtifactRef:
        return EVIDENCE_REF

    def cancel_expiry(_run_id: UUID) -> None:
        lifecycle.append("cancel_expiry")

    def cleanup(_run_id: UUID) -> None:
        lifecycle.append("cleanup")

    def arm_expiry(_run_id: UUID) -> None:
        lifecycle.append("arm_expiry")

    @asynccontextmanager
    async def lease(_run_id: UUID) -> AsyncGenerator[None]:
        lifecycle.append("lease_enter")
        yield
        lifecycle.append("lease_exit")

    async def render_execution(
        request: RenderRevisionRequest, _run: RunSnapshot, *, retain_source_cache: bool
    ) -> RenderRevisionResult:
        assert retain_source_cache
        rendered_requests.append(request)
        return RenderRevisionResult(
            descriptor=_ref("rendered", request.edit.sha256, HarnessArtifactKind.render),
            technical_report=(),
            technical_passed=True,
        )

    owner._require_enabled = enabled
    owner._assert_render_active = active
    owner._artifact_ref = artifact_ref
    owner._cancel_source_cache_expiry = cancel_expiry
    owner._cleanup_source_cache_held = cleanup
    owner._arm_source_cache_expiry = arm_expiry
    owner._source_cache_lease = lease
    owner._render_chapter_revision_locked = render_execution
    renderer = topic_render.TopicRenderActivities(cast("Any", owner))
    renderer.topics = shell.activities

    class Connection:
        async def execute(self, _sql: str, _params: object) -> Connection:
            return self

        async def fetchone(self) -> dict[str, UUID]:
            return {"artifact_id": portfolio_ref.id}

    @asynccontextmanager
    async def scoped(_database_url: str, _scope: object) -> AsyncGenerator[Connection]:
        yield Connection()

    async def read_artifact(*_args: object, **_kwargs: object) -> object:
        return portfolio.model_dump(mode="json")

    async def artifact_record(*_args: object, **_kwargs: object) -> object:
        return EVIDENCE_REF

    async def read(_context: TopicContext, _reference: HarnessArtifactRef) -> object:
        return portfolio.model_dump(mode="json")

    async def publish(_context: TopicContext, **kwargs: object) -> HarnessArtifactRef:
        content = cast("Any", kwargs["content"])
        reference = _ref(
            str(kwargs["format_name"]),
            content.model_dump(mode="json"),
            HarnessArtifactKind(str(kwargs["kind"])),
        )
        published[reference.id] = content
        return reference

    async def heartbeat(
        operation: Callable[[], Awaitable[TopicRenderResult]], **_kwargs: object
    ) -> TopicRenderResult:
        return await operation()

    monkeypatch.setattr(topic_render.db, "scoped", scoped)
    monkeypatch.setattr(topic_render.artifacts, "read_artifact_json", read_artifact)
    monkeypatch.setattr(topic_render.artifacts, "_artifact_for_read", artifact_record)
    monkeypatch.setattr(renderer.topics, "read", read)
    monkeypatch.setattr(renderer.topics, "publish", publish)
    monkeypatch.setattr(topic_render, "run_with_activity_heartbeat", heartbeat)
    result = await renderer.render(
        RenderRevisionRequest(
            run=topic_workflow.TopicRunWorkflow.ref(shell.request), edit=portfolio_ref, revision=1
        )
    )
    assert result.count == 2
    assert result.technical_passed
    assert len({request.edit.id for request in rendered_requests}) == 2
    assert all(request.edit.id != portfolio_ref.id for request in rendered_requests)
    descriptor = cast("TopicRenders", published[result.descriptor.id])
    assert [video.candidateId for video in descriptor.videos] == ["first", "second"]
    assert len({video.execution.id for video in descriptor.videos}) == 2
    assert len({video.descriptor.id for video in descriptor.videos}) == 2
    assert lifecycle == ["cancel_expiry", "lease_enter", "cleanup", "lease_exit", "arm_expiry"]
