"""The v8 parent workflow: order of stages, gap tolerance, and typed stops that propagate."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from temporalio.exceptions import ActivityError, ApplicationError, RetryState

from harness_fixtures import EVIDENCE_REF, _request, _settings, _snapshot
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessRunStatus,
    TopicSelectionAssessment,
    TopicSelectionDraft,
)
from temnia_pipeline.harness import topic_windows_workflow as module
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V8
from temnia_pipeline.harness.runtime_types import (
    StartRunRequest,
    StartRunResult,
    WorkflowIdentity,
)
from temnia_pipeline.harness.topic_decisions import (
    AuthorAssemblyV8,
    AuthorPlanResultV8,
    DecisionRequest,
    DecisionResult,
    InventoryAssemblyV8,
    PlanResultV8,
    RepairAssemblyV8,
    RepairPlanResultV8,
    ReviewPlanResultV8,
)
from temnia_pipeline.harness.topic_selection_runtime import (
    SelectionAssessmentResult,
    SourceIndexBuildResult,
)
from temnia_pipeline.harness.topic_windows import CoverageGap
from temnia_pipeline.harness.topic_windows_workflow import TopicSelectionWorkflowV8

_COUNTER = {"value": 0}


def _ref(kind: HarnessArtifactKind = HarnessArtifactKind.checks) -> HarnessArtifactRef:
    _COUNTER["value"] += 1
    return HarnessArtifactRef(
        id=uuid.uuid4(),
        kind=kind,
        fingerprint=f"{_COUNTER['value']:064x}",
        sha256=f"{_COUNTER['value']:064x}",
        sizeBytes=1,
        storageKey=f"artifact/{_COUNTER['value']}",
    )


def _assessment(*, complete: bool, actionable: bool) -> SelectionAssessmentResult:
    assessment = TopicSelectionAssessment.model_validate(
        {
            "format": "topic-selection-assessment/2",
            "runId": str(uuid.uuid4()),
            "selectionSha256": "a" * 64,
            "evidenceSha256": "b" * 64,
            "rubricSha256": "c" * 64,
            "proposerFamily": "author",
            "verifierFamily": "verifier",
            "coldReviews": [],
            "portfolioReview": None,
            "findings": (
                [
                    {
                        "id": "f1",
                        "kind": "missing_setup",
                        "severity": "required",
                        "affectedCandidateIds": [],
                        "opportunityIds": [],
                        "evidenceSpans": [
                            {"firstSentenceId": "s000000", "lastSentenceId": "s000001"}
                        ],
                        "reason": "fixture",
                    }
                ]
                if actionable
                else []
            ),
            "executionStatus": "complete" if complete else "needs_review",
            "responseArtifacts": [],
            "reasons": [],
        }
    )
    return SelectionAssessmentResult(artifact=_ref(), assessment=assessment, actionable=actionable)


def _activity_error(error_type: str, message: str) -> ActivityError:
    failure = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="fixture",
        activity_type="run_topic_decision_v8",
        activity_id="1",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    failure.__cause__ = ApplicationError(message, type=error_type, non_retryable=True)
    return failure


class Fake:
    """Answers every activity by name; records the order the workflow asked for them."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        decisions: dict[str, Any],
        assessments: list[SelectionAssessmentResult],
        repairs: list[RepairAssemblyV8] | None = None,
        max_repairs: int = 3,
    ) -> None:
        settings, routes = _settings()
        self.request = _request().model_copy(
            update={"config": _request().config.model_copy(update={"maxRepairs": max_repairs})}
        )
        start = StartRunRequest(
            request=self.request,
            editorial_policy=cast("Any", TOPIC_SELECTION_POLICY_V8),
            workflow=WorkflowIdentity(workflow_id="v8", workflow_run_id="run"),
        )
        self.run = _snapshot(start, routes).model_copy(
            update={"editorial_policy": TOPIC_SELECTION_POLICY_V8}
        )
        self.decisions = decisions
        self.assessments = assessments
        self.repairs = repairs or []
        self.calls: list[str] = []
        self.decision_requests: list[DecisionRequest] = []
        self.stop_reasons: tuple[str, ...] = ()
        self.render_reasons: tuple[str, ...] = ()
        monkeypatch.setattr(module.workflow, "execute_activity", self.execute)
        monkeypatch.setattr(
            module.workflow,
            "info",
            lambda: SimpleNamespace(workflow_id="v8", run_id="run", task_queue="tests"),
        )
        _ = settings

    async def execute(self, name: str, payload: Any, **_kwargs: object) -> Any:  # noqa: ANN401, C901, PLR0911, PLR0912
        self.calls.append(name)
        if name == "start_chapter_run":
            return StartRunResult(run=self.run, created=True)
        if name == "build_chapter_evidence":
            return SimpleNamespace(lexical_state="ready", artifact=EVIDENCE_REF)
        if name == "prepare_topic_selection_rubric":
            return _ref()
        if name == "build_topic_source_index":
            return SourceIndexBuildResult(artifact=_ref(), use_record=_ref(), reused=False)
        if name == "prepare_topic_plan_v8":
            return PlanResultV8(
                inventory_plan=_ref(),
                section_ids=("section-0001", "section-0002"),
                projection=_ref(),
                projected_calls=12,
                projected_cost_micros=1_000_000,
                sentence="Projected about 12 model calls.",
            )
        if name == "run_topic_decision_v8":
            request = cast("DecisionRequest", payload)
            self.decision_requests.append(request)
            outcome = self.decisions.get(f"{request.kind}:{request.item_id}")
            if isinstance(outcome, Exception):
                raise outcome
            if outcome == "gap":
                return DecisionResult(
                    kind=request.kind,
                    item_id=request.item_id,
                    stage="s",
                    gap=CoverageGap(
                        kind=request.kind, itemId=request.item_id, reason="no answer", stage="s"
                    ),
                )
            return DecisionResult(
                kind=request.kind, item_id=request.item_id, stage="s", artifact=_ref(), family="f"
            )
        if name == "assemble_topic_inventory_v8":
            gaps = tuple(r.gap for r in payload.results if r.gap is not None)
            return InventoryAssemblyV8(artifact=_ref(), gaps=gaps, opportunity_count=3)
        if name == "prepare_topic_author_plan_v8":
            return AuthorPlanResultV8(artifact=_ref(), work_item_ids=("section-0001:author-0001",))
        if name == "assemble_topic_author_v8":
            gaps = tuple(r.gap for r in payload.results if r.gap is not None)
            draft = TopicSelectionDraft.model_validate(
                {
                    "proposal": {
                        "version": 1,
                        "summary": "s",
                        "candidates": [
                            {
                                "id": "section-0001:author-0001:candidate:one",
                                "title": "One",
                                "purpose": "p",
                                "reason": "r",
                                "firstSentenceId": "s000000",
                                "lastSentenceId": "s000001",
                                "coreSpans": [
                                    {"firstSentenceId": "s000000", "lastSentenceId": "s000001"}
                                ],
                                "completionSpans": [
                                    {"firstSentenceId": "s000001", "lastSentenceId": "s000001"}
                                ],
                                "requiredContextSpans": [],
                                "meaningChangingFollowups": [],
                            }
                        ],
                    },
                    "opportunities": [],
                }
            )
            return AuthorAssemblyV8(
                selection=_ref(HarnessArtifactKind.proposal),
                manifest=_ref(),
                draft=draft,
                semantic_key="key-0",
                families=("author",),
                gaps=gaps,
            )
        if name == "prepare_topic_review_plan_v8":
            return ReviewPlanResultV8(
                artifact=_ref(),
                work_item_ids=(
                    "section-0001:source-local-0001",
                    "section-0001:source-omission-0001",
                ),
            )
        if name == "assemble_topic_assessment_v8":
            return self.assessments.pop(0)
        if name == "claim_chapter_repair":
            self.run = self.run.model_copy(update={"repair_count": self.run.repair_count + 1})
            return self.run
        if name == "prepare_topic_repair_plan_v8":
            return RepairPlanResultV8(artifact=_ref(), work_item_ids=("repair-component-0001",))
        if name == "assemble_topic_repair_v8":
            return self.repairs.pop(0)
        if name == "stop_topic_selection":
            self.stop_reasons = payload.reasons
            return (
                self.assessments[-1]
                if self.assessments
                else _assessment(complete=False, actionable=False)
            )
        if name == "compile_topic_selection":
            return SimpleNamespace(
                artifact=_ref(HarnessArtifactKind.edit),
                refusals=(),
                edit=SimpleNamespace(videos=[1]),
            )
        if name == "accept_initial_chapter_revision":
            return self.run.model_copy(update={"current_revision": 1})
        if name == "render_topic_revision":
            return SimpleNamespace(count=1, technical_passed=True)
        if name == "update_chapter_run_stage":
            self.render_reasons = tuple(
                (payload.error_message or "").split("; ") if payload.error_message else ()
            )
            return self.run.model_copy(update={"status": HarnessRunStatus.needs_review})
        message = f"unexpected activity {name}"
        raise AssertionError(message)


async def test_gaps_are_recorded_and_the_run_still_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake(
        monkeypatch,
        decisions={
            "inventory:section-0002": "gap",
            "review:section-0001:source-omission-0001": "gap",
        },
        assessments=[_assessment(complete=False, actionable=False)],
    )
    result = await TopicSelectionWorkflowV8().program(fake.request)
    assert str(result.status) == "needs_review"
    kinds = [f"{r.kind}:{r.item_id}" for r in fake.decision_requests]
    assert kinds == [
        "inventory:section-0001",
        "inventory:section-0002",
        "author:section-0001:author-0001",
        "cold:section-0001:author-0001:candidate:one",
        "review:section-0001:source-local-0001",
        "review:section-0001:source-omission-0001",
    ]
    assert "assemble_topic_inventory_v8" in fake.calls
    assert "compile_topic_selection" in fake.calls
    assert "render_topic_revision" in fake.calls
    assert any(
        "Inventory of section-0002 is unavailable" in reason for reason in fake.render_reasons
    )
    assert not any(call == "stop_topic_selection" for call in fake.calls)


async def test_repair_loop_ends_at_the_allowance_and_reviews_the_repaired_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = Fake(
        monkeypatch,
        decisions={},
        assessments=[
            _assessment(complete=False, actionable=True),
            _assessment(complete=False, actionable=True),
        ],
        repairs=[
            RepairAssemblyV8(
                selection=_ref(HarnessArtifactKind.proposal),
                manifest=_ref(),
                draft=None,
                semantic_key="key-1",
                families=("author",),
            )
        ],
        max_repairs=1,
    )
    # The repaired draft must exist for the loop to continue; reuse the author draft shape.
    original_execute = fake.execute

    async def execute(name: str, payload: Any, **kwargs: object) -> Any:  # noqa: ANN401
        result = await original_execute(name, payload, **kwargs)
        if name == "assemble_topic_repair_v8":
            draft = TopicSelectionDraft.model_validate(
                {"proposal": {"version": 1, "summary": "s", "candidates": []}, "opportunities": []}
            )
            return result.model_copy(update={"draft": draft})
        return result

    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    result = await TopicSelectionWorkflowV8().program(fake.request)
    assert str(result.status) == "needs_review"
    assert fake.calls.count("assemble_topic_assessment_v8") == 2
    assert fake.calls.count("claim_chapter_repair") == 1
    assert any("repair allowance" in reason for reason in fake.stop_reasons)
    assert fake.calls.index("stop_topic_selection") < fake.calls.index("compile_topic_selection")


async def test_a_typed_stop_from_one_decision_propagates_after_siblings_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = Fake(
        monkeypatch,
        decisions={
            "inventory:section-0001": _activity_error(
                "BudgetExceeded", "The run budget cannot cover the next qualified operation."
            )
        },
        assessments=[],
    )
    with pytest.raises(ActivityError):
        await TopicSelectionWorkflowV8().program(fake.request)
    kinds = [f"{r.kind}:{r.item_id}" for r in fake.decision_requests]
    assert kinds == ["inventory:section-0001", "inventory:section-0002"]
    assert "assemble_topic_inventory_v8" not in fake.calls
