"""Connected selection behaviors with real prompts/grounding and mocked I/O boundaries.

These exercise no paid model and do not establish actual editorial quality or server replay.
"""

# Test doubles replace persistence and model transport, not editorial validators.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from harness_fixtures import EVIDENCE_REF, SOURCE_ID, _request, _settings, _snapshot
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    TopicEditorialRubric,
    TopicOpportunity,
    TopicPortfolioReview,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSourceIndex,
)
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness import topic_selection_workflow as module
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3
from temnia_pipeline.harness.gateway import parse_retry_after
from temnia_pipeline.harness.ledger import BudgetExceeded, OutcomeUnknown, operation_identity
from temnia_pipeline.harness.routes import select_route
from temnia_pipeline.harness.runtime_types import EvidenceResult, RunSnapshot, StartRunResult
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence
from temnia_pipeline.harness.topic_runtime import TopicCompilation, TopicContext, TopicRenderResult
from temnia_pipeline.harness.topic_selection import (
    content_hash,
    make_rubric,
)
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.topic_selection_workflow import (
    TopicSelectionWorkflow,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_compiler import _candidate, _case, _span
from topic_fixtures import _cold, _criterion, _source

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import BaseModel

    from temnia_pipeline.harness.activities import HarnessActivities
    from temnia_pipeline.harness.models import HarnessModelDeps
    from temnia_pipeline.harness.topic_selection_runtime import (
        SelectionCallPlan,
        SelectionContext,
        SelectionRejection,
    )


EVIDENCE = augment_topic_evidence(_case().model_copy(update={"sourceId": SOURCE_ID}))
CANDIDATE = _candidate("discussion", 0, 3)


class UnexpectedModelBehavior(Exception):  # noqa: N818
    """A local double for the retained invalid-output failure name."""


async def _inspection_none(*_args: object, **_kwargs: object) -> None:
    return None


def opportunity(*, selected: bool) -> TopicOpportunity:
    return TopicOpportunity.model_validate(
        {
            "id": "useful-discussion",
            "candidateIds": [CANDIDATE.id] if selected else [],
            "completionSpans": [_span(3)],
            "coreSpans": [_span(1)],
            "disposition": "proposed" if selected else "needs_evidence",
            "dispositionReason": "The source develops a useful explanation.",
            "meaningChangingFollowups": [],
            "requiredContextSpans": [_span(0)],
            "valueEvidenceSpans": [_span(1, 3)],
            "viewerPurpose": "Understand the explanation.",
        }
    )


def draft(*, selected: bool) -> TopicSelectionDraft:
    return TopicSelectionDraft(
        proposal=TopicProposal(
            version=1,
            summary="Independent editorial opportunities.",
            candidates=[CANDIDATE] if selected else [],
        ),
        opportunities=[opportunity(selected=True)] if selected else [],
    )


def cold() -> TopicSelectionColdReview:
    return TopicSelectionColdReview.model_validate(
        {
            **_cold(CANDIDATE.id).model_dump(mode="json"),
            "value": {
                "reconstructedPurpose": "Understand the explanation.",
                "reconstructedTakeaway": "The qualification completes the claim.",
                **{
                    name: _criterion().model_dump(mode="json")
                    for name in (
                        "viewerReasonToWatch",
                        "deliveredValue",
                        "openingEffectiveness",
                        "focusedDevelopment",
                    )
                },
            },
        }
    )


def portfolio(*, selected: bool, missing: bool = False, weak: bool = False) -> TopicPortfolioReview:
    finding = {
        "id": "missing" if missing else "weak",
        "kind": "missed_opportunity" if missing else "weak_viewer_value",
        "affectedCandidateIds": [] if missing else [CANDIDATE.id],
        "evidenceSpans": [_span(1)],
        "opportunityIds": ["useful-discussion"],
        "reason": "The source supports this finding.",
        "severity": "required",
    }
    return TopicPortfolioReview.model_validate(
        {
            "candidates": [_source(CANDIDATE.id)] if selected else [],
            "findings": [finding] if missing or weak else [],
            "missingOpportunities": [opportunity(selected=False)] if missing else [],
            "opportunities": [
                {
                    "opportunityId": "useful-discussion",
                    "candidateIds": [CANDIDATE.id],
                    "evidenceSpans": [_span(1)],
                    "reason": "This treatment contains the core.",
                    "status": "unresolved" if weak else "represented",
                }
            ]
            if selected
            else [],
            "selection": [
                {
                    "candidateId": CANDIDATE.id,
                    "disposition": "decline" if weak else "select",
                    "evidenceSpans": [_span(1)],
                    "reason": "Value assessed against the brief.",
                }
            ]
            if selected
            else [],
            "summary": "Source opportunities and selected treatments were independently assessed.",
        }
    )


def v3_portfolio(
    *, selected: bool, missing: bool = False, weak: bool = False
) -> TopicPortfolioReviewV4:
    """V3 source review decides the portfolio without repeating cold reviews."""
    return TopicPortfolioReviewV4.model_validate(
        {
            **portfolio(selected=selected, missing=missing, weak=weak).model_dump(mode="json"),
            "candidates": [],
            "overlaps": [],
            "handoffs": [],
        }
    )


class AgentDouble:
    def __init__(self, name: str, outputs: list[object], call_order: list[str]) -> None:
        self.name = name
        self.outputs = outputs
        self.calls: list[HarnessModelDeps] = []
        self.prompts: list[str] = []
        self.call_order = call_order

    async def run(self, prompt: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.call_order.append(self.name)
        self.calls.append(kwargs["deps"])
        self.prompts.append(prompt)
        assert self.outputs, "unexpected extra editorial call"
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if callable(output):
            output = output(json.loads(prompt.split("SOURCE DATA\n", 1)[1]))
        return SimpleNamespace(output=output, all_messages=list)


class Program:
    def __init__(  # noqa: PLR0913
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        initial: TopicSelectionDraft,
        sources: list[object],
        patches: list[object],
        colds: list[object] | None = None,
        inventory: object | None = None,
        policy: str = TOPIC_SELECTION_POLICY_V3,
        workflow_type: type[TopicSelectionWorkflow] = TopicSelectionWorkflow,
    ) -> None:
        self.policy = policy
        self.workflow_type = workflow_type
        self.call_order: list[str] = []
        _, self.routes = _settings()
        self.request = _request().model_copy(update={"brief": "A specialist audience."})
        self.request = self.request.model_copy(
            update={"config": self.request.config.model_copy(update={"maxRepairs": 2})}
        )
        start = module.StartRunRequest(
            request=self.request,
            editorial_policy=cast("Any", policy),
            workflow=module.WorkflowIdentity(workflow_id="selection", workflow_run_id="run"),
        )
        self.run = _snapshot(start, self.routes).model_copy(update={"editorial_policy": policy})
        self.objects: dict[UUID, BaseModel] = {}
        self.records: dict[UUID, SimpleNamespace] = {}
        self.prepared_contexts: list[SelectionContext] = []
        self.saved: list[tuple[str, HarnessArtifactRef]] = []
        self.compiled: TopicCompilation | None = None
        self.final_context: SelectionContext | None = None
        self.render_count = 0
        self.inventory = AgentDouble(
            "inventory",
            [inventory if inventory is not None else draft(selected=False)],
            self.call_order,
        )
        self.author = AgentDouble("author", [initial], self.call_order)
        self.cold = AgentDouble("cold", colds if colds is not None else [cold()], self.call_order)
        self.source = AgentDouble("source", sources, self.call_order)
        self.patch = AgentDouble("patch", patches, self.call_order)
        self.activities = TopicSelectionActivities(
            cast(
                "HarnessActivities",
                SimpleNamespace(
                    _recorded_output=lambda _name: None,  # pyright: ignore[reportUnknownLambdaType]
                    _artifact_ref=lambda record: record.ref,  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
                    ctx=SimpleNamespace(settings=SimpleNamespace(database_url="test-database")),
                ),
            )
        )
        monkeypatch.setattr(self.activities, "load", self.load)
        monkeypatch.setattr(self.activities, "read", self.read)
        monkeypatch.setattr(artifacts, "_artifact_for_read", self.artifact_for_read)
        monkeypatch.setattr(self.activities, "response_ref", self.response_ref)
        monkeypatch.setattr(
            self.activities,
            "inspection_ref",
            _inspection_none,
        )
        monkeypatch.setattr(self.activities.topics, "publish", self.publish)
        monkeypatch.setattr(workflow_type, "author_agent", self.author)
        monkeypatch.setattr(workflow_type, "cold_agent", self.cold)
        monkeypatch.setattr(workflow_type, "source_agent", self.source)
        monkeypatch.setattr(workflow_type, "patch_agent", self.patch)
        if workflow_type is TopicSelectionWorkflow:
            monkeypatch.setattr(module, "topic_opportunity_inventory_v3", self.inventory)
        monkeypatch.setattr(module.workflow, "execute_activity", self.execute)
        monkeypatch.setattr(
            module.workflow,
            "info",
            lambda: SimpleNamespace(
                workflow_id="selection", run_id="run", task_queue="selection-tests"
            ),
        )

    async def load(
        self, context: SelectionContext
    ) -> tuple[
        RunSnapshot,
        HarnessEvidence,
        TopicEditorialRubric | None,
        TopicSelectionRecord | None,
    ]:
        return (
            self.run,
            EVIDENCE,
            cast("TopicEditorialRubric", self.objects[context.rubric.id])
            if context.rubric
            else None,
            cast("TopicSelectionRecord", self.objects[context.selection.id])
            if context.selection
            else None,
        )

    async def read(self, _context: SelectionContext, ref: HarnessArtifactRef) -> object:
        return self.objects[ref.id].model_dump(mode="json")

    async def artifact_for_read(
        self, _database_url: str, *, artifact_id: UUID, **_kwargs: object
    ) -> SimpleNamespace:
        return self.records[artifact_id]

    async def publish(
        self,
        context: TopicContext,
        *,
        content: BaseModel,
        format_name: str,
        kind: str,
        dependencies: Sequence[HarnessArtifactRef] = (),
        **_kwargs: object,
    ) -> HarnessArtifactRef:
        digest = content_hash(content)
        ref = HarnessArtifactRef(
            id=uuid5(NAMESPACE_URL, format_name + digest),
            fingerprint=digest,
            sha256=digest,
            kind=HarnessArtifactKind(kind),
            sizeBytes=1,
            storageKey=f"tests/{digest}.json",
        )
        self.objects[ref.id] = content
        self.records[ref.id] = SimpleNamespace(
            ref=ref,
            metadata={"format": format_name, "runId": str(context.run.run_id)},
            dependency_ids=[item.id for item in dependencies],
        )
        self.saved.append((format_name, ref))
        return ref

    async def response_ref(
        self, context: SelectionContext, plan: SelectionCallPlan, _output: BaseModel | None
    ) -> HarnessArtifactRef:
        return await self.publish(
            TopicContext(run=context.run, evidence=context.evidence),
            content=make_rubric(plan.stage),
            kind="model_response",
            format_name="test-paid-response",
        )

    async def execute(self, name: str, value: Any, **_kwargs: object) -> object:  # noqa: ANN401, C901, PLR0911
        if name == "start_chapter_run":
            assert value.editorial_policy == self.policy
            return StartRunResult(run=self.run, created=True)
        if name == "build_chapter_evidence":
            return EvidenceResult(
                artifact=EVIDENCE_REF,
                sentence_count=4,
                word_count=4,
                duration_ms=4000,
                lexical_state="present",
            )
        if name == "build_topic_source_index":
            index = TopicSourceIndex.model_validate(
                {
                    "format": "topic-source-index/1",
                    "evidenceSha256": EVIDENCE_REF.sha256,
                    "sourceId": str(EVIDENCE.sourceId),
                    "transcriptId": str(EVIDENCE.transcriptId),
                    "transcriptRevision": EVIDENCE.transcriptRevision,
                    "embeddingModel": "test/encoder",
                    "embeddingRevision": "a" * 40,
                    "embeddingDimensions": 1,
                    "regionMaxSentences": 32,
                    "regionMaxCharacters": 8000,
                    "sentences": [
                        {
                            "id": sentence.id,
                            "startMs": sentence.startMs,
                            "endMs": sentence.endMs,
                            "speakers": sentence.speakers,
                            "text": sentence.text,
                        }
                        for sentence in EVIDENCE.sentences
                    ],
                    "regions": [
                        {
                            "id": "r1",
                            "ordinal": 0,
                            "firstSentenceId": EVIDENCE.sentences[0].id,
                            "lastSentenceId": EVIDENCE.sentences[-1].id,
                            "startMs": EVIDENCE.sentences[0].startMs,
                            "endMs": EVIDENCE.sentences[-1].endMs,
                            "sentenceCount": len(EVIDENCE.sentences),
                            "keywords": ["fixture"],
                            "preview": "fixture source",
                            "embedding": [1.0],
                        }
                    ],
                }
            )
            return await self.publish(
                TopicContext(run=value.run, evidence=value.evidence),
                content=index,
                format_name=index.format,
                kind="checks",
                dependencies=(value.evidence,),
            )
        if name == "claim_chapter_repair":
            self.run = self.run.model_copy(update={"repair_count": self.run.repair_count + 1})
            return self.run
        operations: dict[str, Callable[..., Any]] = {
            "prepare_topic_selection_rubric": self.activities.rubric,
            "prepare_topic_selection_call": self.activities.prepare,
            "save_topic_opportunity_inventory_v3": self.activities.save_inventory,
            "save_topic_selection": self.activities.save,
            "save_topic_selection_assessment": self.activities.save_assessment,
            "stop_topic_selection": self.activities.stop,
        }
        if name in operations:
            if name == "prepare_topic_selection_call":
                self.prepared_contexts.append(value)
            return await operations[name](value)
        if name == "compile_topic_selection":
            self.final_context = value
            self.compiled = await self.activities.compile(value)
            return self.compiled
        if name == "accept_initial_chapter_revision":
            self.run = self.run.model_copy(update={"current_revision": 1})
            return self.run
        if name == "render_topic_revision":
            assert self.compiled is not None
            self.render_count = len(self.compiled.edit.videos)
            return TopicRenderResult(
                count=self.render_count, technical_passed=True, descriptor=self.compiled.artifact
            )
        if name == "update_chapter_run_stage":
            self.run = self.run.model_copy(update={"status": value.status})
            return self.run
        message = f"unexpected activity: {name}"
        raise AssertionError(message)


def add_patch(payload: dict[str, Any]) -> TopicSelectionPatchV3:
    return TopicSelectionPatchV3.model_validate(
        {
            **{
                name: payload[name]
                for name in ("baseSelectionSha256", "evidenceSha256", "rubricSha256")
            },
            "summary": "A missed worthwhile discussion now has a treatment.",
            "operations": [
                {
                    "id": "add",
                    "affectedCandidateIds": [],
                    "findingIds": ["source:missing"],
                    "kind": "add_opportunity",
                    "opportunities": [opportunity(selected=True)],
                    "reason": "Recover the independently found discussion.",
                    "replacementCandidates": [CANDIDATE.model_copy(deep=True)],
                }
            ],
        }
    )


async def test_empty_author_is_challenged_then_missing_discussion_is_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[v3_portfolio(selected=False, missing=True), v3_portfolio(selected=True)],
        patches=[add_patch],
    )
    result = await TopicSelectionWorkflow().program(run.request)
    assert result.revision == 1
    assert len(run.author.calls) == 1
    assert len(run.source.calls) == 2
    assert len(run.patch.calls) == len(run.cold.calls) == 1
    assert run.render_count == 1
    assert run.final_context is not None
    assessed = await run.activities.assessment(run.final_context)
    assert assessed is not None
    assert str(assessed.executionStatus) == "complete"
    assert all(
        call.program_version == TOPIC_SELECTION_POLICY_V3
        for call in (*run.author.calls, *run.source.calls)
    )
    assert run.author.calls[0].route.family != run.source.calls[0].route.family


async def test_empty_source_assessment_can_confirm_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[v3_portfolio(selected=False)],
        patches=[],
    )
    result = await TopicSelectionWorkflow().program(run.request)
    assert run.render_count == 0
    assert len(run.source.calls) == 1
    assert not run.cold.calls
    assert not run.patch.calls
    assert "selected no standalone video" in (result.errorMessage or "")


async def test_weak_selection_is_dropped_with_its_audience_disposition_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def drop(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        excluded = opportunity(selected=False).model_dump(mode="json")
        excluded.update(
            disposition="not_useful_for_audience",
            dispositionReason="This explanation is too elementary for the specialist brief.",
        )
        return TopicSelectionPatchV3.model_validate(
            {
                **{
                    name: payload[name]
                    for name in ("baseSelectionSha256", "evidenceSha256", "rubricSha256")
                },
                "summary": "The opportunity remains recorded after dropping its weak treatment.",
                "operations": [
                    {
                        "id": "drop-weak",
                        "affectedCandidateIds": [CANDIDATE.id],
                        "findingIds": ["source:weak"],
                        "kind": "drop",
                        "opportunities": [excluded],
                        "reason": "The independent review found no value for this audience.",
                        "replacementCandidates": [],
                    }
                ],
            }
        )

    confirmed = v3_portfolio(selected=False).model_dump(mode="json")
    confirmed["opportunities"] = [
        {
            "opportunityId": "useful-discussion",
            "candidateIds": [],
            "evidenceSpans": [_span(1).model_dump(mode="json")],
            "reason": "The explanation adds no value for the specified specialist audience.",
            "status": "not_useful_for_audience",
        }
    ]
    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        sources=[
            v3_portfolio(selected=True, weak=True),
            TopicPortfolioReviewV4.model_validate(confirmed),
        ],
        patches=[drop],
    )
    result = await TopicSelectionWorkflow().program(run.request)
    assert run.render_count == 0
    assert len(run.cold.calls) == 1
    assert len(run.source.calls) == 2
    assert len(run.patch.calls) == 1
    assert run.final_context is not None
    assert run.final_context.selection is not None
    selection = TopicSelectionRecord.model_validate(
        await run.activities.read(run.final_context, run.final_context.selection)
    )
    assert not selection.draft.proposal.candidates
    assert str(selection.draft.opportunities[0].disposition) == "not_useful_for_audience"
    assessment = await run.activities.assessment(run.final_context)
    assert assessment is not None
    assert str(assessment.executionStatus) == "complete"
    assert "selected no standalone video" in (result.errorMessage or "")


async def test_source_invalid_patch_retains_the_prior_assessed_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        patch = add_patch(payload)
        patch.operations[0].replacementCandidates[0].firstSentenceId = "foreign-sentence"
        return patch

    run = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[v3_portfolio(selected=False, missing=True)],
        patches=[invalid, invalid, invalid],
    )
    run.request = run.request.model_copy(
        update={"config": run.request.config.model_copy(update={"maxRepairs": 3})}
    )
    result = await TopicSelectionWorkflow().program(run.request)
    selections = [ref for format_name, ref in run.saved if format_name == "topic-selection/2"]
    assert len(selections) == 1
    assert run.final_context is not None
    assert run.final_context.selection == selections[0]
    rejections = [ref for name, ref in run.saved if name == "topic-selection-rejection/2"]
    assert len(rejections) == 3
    assert run.call_order == ["inventory", "author", "source", "patch", "patch", "patch"]
    assert run.run.repair_count == 3
    assert "repair allowance ended" in (result.errorMessage or "")
    assert "The last repair was refused" in (result.errorMessage or "")
    assert "unknown or reversed source sentence span" in (result.errorMessage or "")
    assert [call.stage for call in run.patch.calls] == [
        "repair:selection:1",
        "repair:selection:2",
        "repair:selection:3",
    ]
    # Identical refused outputs and diagnostics produce identical correction prompts;
    # the new immutable rejection dependency still makes each paid operation distinct.
    assert run.patch.prompts[1] == run.patch.prompts[2]
    identities = [
        operation_identity(
            run_id=call.run_id,
            kind="model",
            inputs=call.operation_inputs,
            config=call.operation_config,
        )[0]
        for call in run.patch.calls
    ]
    assert len(set(identities)) == 3
    for call, rejection in zip(run.patch.calls[1:], rejections, strict=False):
        assert str(rejection.id) in {
            item["id"] for item in cast("list[dict[str, str]]", call.operation_inputs["artifacts"])
        }
    assert run.render_count == 0


async def test_settled_bad_hash_patch_is_corrected_before_fresh_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def retitle(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        return TopicSelectionPatchV3.model_validate(
            {
                **{
                    name: payload[name]
                    for name in ("baseSelectionSha256", "evidenceSha256", "rubricSha256")
                },
                "summary": "Correct the unsupported title.",
                "operations": [
                    {
                        "id": "retitle",
                        "affectedCandidateIds": [CANDIDATE.id],
                        "findingIds": [f"cold:{CANDIDATE.id}:titleFaithful"],
                        "kind": "retitle",
                        "opportunities": [],
                        "reason": "The replacement accurately describes the existing discussion.",
                        "replacementCandidates": [
                            CANDIDATE.model_copy(update={"title": "A faithful replacement title"})
                        ],
                    }
                ],
            }
        )

    def bad_hash(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        return retitle(payload).model_copy(update={"baseSelectionSha256": "0" * 64})

    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        colds=[cold().model_copy(update={"titleFaithful": _criterion("fail")}), cold()],
        sources=[v3_portfolio(selected=True), v3_portfolio(selected=True)],
        patches=[bad_hash, retitle],
    )
    await TopicSelectionWorkflow().program(run.request)
    assert run.call_order == [
        "inventory",
        "author",
        "cold",
        "source",
        "patch",
        "patch",
        "cold",
        "source",
    ]
    patch_contexts = [context for context in run.prepared_contexts if context.assessment]
    assert len(patch_contexts) == 2
    first, correction = patch_contexts
    assert first.selection == correction.selection
    assert first.assessment == correction.assessment
    assert correction.iteration == first.iteration + 1
    assert correction.rejection is not None
    refusal = cast("SelectionRejection", run.objects[correction.rejection.id])
    payload = json.loads(run.patch.prompts[1].split("SOURCE DATA\n", 1)[1])
    assert refusal.patch is not None
    assert payload["rejectedPatch"] == refusal.patch.model_dump(mode="json")
    assert payload["rejectedPatch"]["baseSelectionSha256"] == "0" * 64
    assert payload["validationDiagnostics"] == list(refusal.diagnostics)
    assert "patch base, evidence, rubric or assessment identity differs" in refusal.diagnostics
    assert first.assessment is not None
    assert first.selection is not None
    assert first.rubric is not None
    assert {
        first.evidence.id,
        first.rubric.id,
        first.selection.id,
        first.assessment.id,
        refusal.response.id,
    } <= set(run.records[correction.rejection.id].dependency_ids)
    assert str(correction.rejection.id) in {
        item["id"]
        for item in cast("list[dict[str, str]]", run.patch.calls[1].operation_inputs["artifacts"])
    }
    assert run.final_context is not None
    assert run.final_context.rejection is None
    assert run.final_context.selection != first.selection
    assert run.final_context.assessment != first.assessment
    assert run.render_count == 1


@pytest.mark.parametrize(
    "stale_part", ["run", "evidence", "rubric", "selection", "assessment", "response", "stage"]
)
async def test_patch_correction_refuses_stale_rejection_provenance(
    monkeypatch: pytest.MonkeyPatch, stale_part: str
) -> None:
    def invalid(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        return add_patch(payload).model_copy(update={"baseSelectionSha256": "0" * 64})

    run = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[v3_portfolio(selected=False, missing=True)],
        patches=[invalid],
    )
    run.request = run.request.model_copy(
        update={"config": run.request.config.model_copy(update={"maxRepairs": 1})}
    )
    await TopicSelectionWorkflow().program(run.request)
    patch_context = next(context for context in run.prepared_contexts if context.assessment)
    rejection = next(ref for name, ref in run.saved if name == "topic-selection-rejection/2")
    correction = patch_context.model_copy(
        update={"rejection": rejection, "iteration": patch_context.iteration + 1}
    )
    # The unmodified artifact is from precisely this assessed request.
    await run.activities.prepare(correction)
    row = run.records[rejection.id]
    refusal = cast("SelectionRejection", run.objects[rejection.id])
    if stale_part == "run":
        row.metadata["runId"] = str(uuid5(NAMESPACE_URL, "different-run"))
    elif stale_part == "stage":
        other = refusal.model_copy(update={"stage": "proposal:selection:1"})
        other_ref = await run.publish(
            TopicContext(run=correction.run, evidence=correction.evidence),
            content=other,
            kind="checks",
            format_name=other.format,
            dependencies=[
                run.records[identifier].ref
                for identifier in row.dependency_ids
                if identifier != EVIDENCE_REF.id
            ]
            + [EVIDENCE_REF],
        )
        correction = correction.model_copy(update={"rejection": other_ref})
    else:
        ref = refusal.response if stale_part == "response" else getattr(correction, stale_part)
        assert ref is not None
        row.dependency_ids.remove(ref.id)
        row.dependency_ids.append(uuid5(NAMESPACE_URL, "unrelated-dependency"))
    with pytest.raises(HarnessValidationError, match=r"dependencies|preceding typed refusal"):
        await run.activities.prepare(correction)


async def test_unknown_outcome_during_patch_correction_stays_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(payload: dict[str, Any]) -> TopicSelectionPatchV3:
        return add_patch(payload).model_copy(update={"baseSelectionSha256": "0" * 64})

    run = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[v3_portfolio(selected=False, missing=True)],
        patches=[invalid, OutcomeUnknown("unknown corrective request")],
    )
    with pytest.raises(OutcomeUnknown, match="unknown corrective request"):
        await TopicSelectionWorkflow().program(run.request)
    assert run.call_order == ["inventory", "author", "source", "patch", "patch"]
    assert run.compiled is None


async def test_review_execution_limit_preserves_unknown_not_false_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Program(
        monkeypatch, initial=draft(selected=True), sources=[BudgetExceeded("limited")], patches=[]
    )
    await TopicSelectionWorkflow().program(run.request)
    assert run.final_context is not None
    assessment = await run.activities.assessment(run.final_context)
    assert assessment is not None
    assert str(assessment.executionStatus) == "execution_limited"
    assert assessment.portfolioReview is None
    # The select-only gate withholds every unreviewed candidate; nothing is rendered as a pass.
    assert run.render_count == 0
    assert not run.patch.calls


async def test_unknown_provider_outcome_never_continues_to_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Program(
        monkeypatch, initial=draft(selected=True), sources=[OutcomeUnknown("unknown")], patches=[]
    )
    with pytest.raises(OutcomeUnknown):
        await TopicSelectionWorkflow().program(run.request)
    assert run.compiled is None
    assert not run.patch.calls


async def test_cold_review_with_foreign_candidate_id_is_retained_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_target = cold().model_copy(update={"candidateId": "foreign-candidate"})
    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        colds=[wrong_target],
        sources=[v3_portfolio(selected=True)],
        patches=[],
    )
    await TopicSelectionWorkflow().program(run.request)
    assert run.final_context is not None
    assessment = await run.activities.assessment(run.final_context)
    assert assessment is not None
    assert not assessment.coldReviews
    assert assessment.portfolioReview is not None
    assert len(assessment.responseArtifacts) == 2
    assert str(assessment.executionStatus) == "needs_review"
    assert any("different candidate" in reason for reason in assessment.reasons)
    assert run.render_count == 1


async def test_v3_inventory_precedes_author_and_source_review_hides_rationale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = TopicSelectionDraft(
        proposal=TopicProposal(version=1, candidates=[], summary="One source opportunity."),
        opportunities=[opportunity(selected=False)],
    )
    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        inventory=inventory,
        sources=[v3_portfolio(selected=True)],
        patches=[],
        policy=TOPIC_SELECTION_POLICY_V3,
        workflow_type=TopicSelectionWorkflow,
    )
    result = await TopicSelectionWorkflow().program(run.request)
    assert result.revision == 1
    assert run.call_order == ["inventory", "author", "cold", "source"]
    assert run.inventory.calls[0].route.family == run.source.calls[0].route.family
    assert run.inventory.calls[0].route.family != run.author.calls[0].route.family
    author_payload = json.loads(run.author.prompts[0].split("SOURCE DATA\n", 1)[1])
    assert author_payload["independentSourceOpportunityInventory"] == inventory.model_dump(
        mode="json"
    )
    source_payload = json.loads(run.source.prompts[0].split("SOURCE DATA\n", 1)[1])
    assert "selectionWithoutAuthorRationale" in source_payload
    assert "Leave candidates as an empty array" in run.source.prompts[0]
    projected = source_payload["selectionWithoutAuthorRationale"]
    assert "reason" not in projected["candidates"][0]
    assert "dispositionReason" not in projected["opportunities"][0]
    assert run.render_count == 1


async def test_v3_invalid_repair_withholds_the_known_invalid_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = TopicSelectionDraft(
        proposal=TopicProposal(version=1, candidates=[], summary="One source opportunity."),
        opportunities=[opportunity(selected=False)],
    )
    unresolved_payload = v3_portfolio(selected=True, weak=True).model_dump(mode="json")
    unresolved_payload["selection"][0]["disposition"] = "unresolved"
    unresolved = TopicPortfolioReviewV4.model_validate(unresolved_payload)
    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        inventory=inventory,
        sources=[unresolved],
        patches=[UnexpectedModelBehavior("truncated patch")],
        policy=TOPIC_SELECTION_POLICY_V3,
        workflow_type=TopicSelectionWorkflow,
    )
    result = await TopicSelectionWorkflow().program(run.request)
    assert run.call_order == ["inventory", "author", "cold", "source", "patch"]
    assert run.render_count == 0
    assert "prior assessed selection is retained" in (result.errorMessage or "")


class TransientProviderFailure(Exception):  # noqa: N818
    """A local double for the settled-charge transient failure name."""


async def _no_sleep(_delay: object) -> None:
    return None


async def test_settled_transient_failure_is_retried_and_the_run_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Program(
        monkeypatch,
        initial=draft(selected=True),
        sources=[TransientProviderFailure("settled"), v3_portfolio(selected=True)],
        patches=[],
    )
    monkeypatch.setattr(module.workflow, "sleep", _no_sleep)
    await TopicSelectionWorkflow().program(run.request)
    assert run.call_order.count("source") == 2
    assert run.compiled is not None
    assert run.render_count == 1


async def test_transient_failures_fall_back_through_the_verify_pool_then_end_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each route gets two tries; the seat then moves on; exhaustion names every route."""
    _, snapshot = _settings()
    author = select_route(snapshot, "propose")
    eligible = [
        route_id
        for route_id in snapshot.seats["verify"].route_ids
        if snapshot.route(route_id).family != author.family
    ]
    attempts = module.SAME_ROUTE_ATTEMPTS * len(eligible)
    sources: list[object] = [TransientProviderFailure("settled")] * attempts
    sources.append(v3_portfolio(selected=True))
    run = Program(monkeypatch, initial=draft(selected=True), sources=sources, patches=[])
    monkeypatch.setattr(module.workflow, "sleep", _no_sleep)
    with pytest.raises(module.SeatRoutesExhausted, match=", ".join(eligible)):
        await TopicSelectionWorkflow().program(run.request)
    assert run.call_order.count("source") == attempts
    assert run.compiled is None


def test_the_provider_pause_advice_extends_the_backoff_but_never_shortens_it() -> None:
    floor = module.SAME_ROUTE_BACKOFF
    assert module.advised_pause("nothing", floor) == floor
    assert module.advised_pause("The provider asked for a pause of 5 s.", floor) == floor
    assert module.advised_pause("pause of 90 s", floor).total_seconds() == 90
    assert parse_retry_after("17") == 17
    assert parse_retry_after(" 3.5 ") == 3.5
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after("100000") == 120


async def test_a_transient_failure_on_one_route_moves_the_seat_to_the_next_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources: list[object] = [TransientProviderFailure("settled")] * module.SAME_ROUTE_ATTEMPTS
    sources.append(v3_portfolio(selected=True))
    run = Program(monkeypatch, initial=draft(selected=True), sources=sources, patches=[])
    monkeypatch.setattr(module.workflow, "sleep", _no_sleep)
    await TopicSelectionWorkflow().program(run.request)
    assert run.call_order.count("source") == module.SAME_ROUTE_ATTEMPTS + 1
    assert run.final_context is not None
    assert run.final_context.verifier_index == 1
    assert run.compiled is not None
