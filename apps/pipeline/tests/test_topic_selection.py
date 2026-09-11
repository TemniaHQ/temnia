"""Pure audience, value and compound selection authority invariants."""

# Shared source construction is deterministic; no activity, provider or model is involved.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from temnia_pipeline.contracts import (
    TopicOpportunity,
    TopicPortfolioReview,
    TopicProposal,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatch,
    TopicSelectionRecord,
)
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence
from temnia_pipeline.harness.topic_selection import (
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    selection_cold_key,
    selection_cold_prompt,
    selection_prompt,
    selection_source_prompt,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_compiler import _evidence, _transcript, _word
from test_topic_compiler import _candidate, _span

if TYPE_CHECKING:
    from temnia_pipeline.contracts import TopicCandidate, TopicSelectionAssessment

EVIDENCE = augment_topic_evidence(
    _evidence(
        _transcript(
            [_word(f"Source statement {i}.", i * 1000 + 100, i * 1000 + 900) for i in range(8)],
            8000,
        ),
        [(i, i) for i in range(8)],
    )
)


def _opportunity(candidate: TopicCandidate) -> TopicOpportunity:
    return TopicOpportunity.model_validate(
        {
            "id": f"opportunity-{candidate.id}",
            "candidateIds": [candidate.id],
            "coreSpans": candidate.coreSpans,
            "completionSpans": candidate.completionSpans,
            "requiredContextSpans": [],
            "meaningChangingFollowups": [],
            "valueEvidenceSpans": candidate.coreSpans,
            "viewerPurpose": candidate.purpose,
            "disposition": "proposed",
            "dispositionReason": "This hypothesis has a source-grounded treatment.",
        }
    )


def _record(*candidates: TopicCandidate) -> TopicSelectionRecord:
    rubric = make_rubric("Useful independent discussions for viewers unfamiliar with this episode.")
    return TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": UUID(int=1),
            "origin": "model",
            "parentSelectionSha256": None,
            "rubric": rubric,
            "rubricSha256": content_hash(rubric),
            "evidenceSha256": content_hash(EVIDENCE),
            "draft": TopicSelectionDraft(
                proposal=TopicProposal(
                    version=1, summary="Source discussions.", candidates=list(candidates)
                ),
                opportunities=[_opportunity(candidate) for candidate in candidates],
            ),
        }
    )


def _criterion(candidate: TopicCandidate) -> dict[str, Any]:
    return {
        "status": "pass",
        "reason": "The supplied selected passage supports this independent judgment.",
        "evidenceSpans": [candidate.coreSpans[0].model_dump(mode="json")],
    }


def _cold(candidate: TopicCandidate) -> TopicSelectionColdReview:
    criterion = _criterion(candidate)
    return TopicSelectionColdReview.model_validate(
        {
            "candidateId": candidate.id,
            **dict.fromkeys(
                ("intelligibleBeginning", "coherentTopic", "completeDiscussion", "titleFaithful"),
                criterion,
            ),
            "value": {
                "reconstructedPurpose": "Understand the selected explanation.",
                "reconstructedTakeaway": "The explanation develops a useful conclusion.",
                **dict.fromkeys(
                    (
                        "viewerReasonToWatch",
                        "deliveredValue",
                        "openingEffectiveness",
                        "focusedDevelopment",
                    ),
                    criterion,
                ),
            },
        }
    )


def _source(record: TopicSelectionRecord) -> TopicPortfolioReview:
    candidates = record.draft.proposal.candidates
    return TopicPortfolioReview.model_validate(
        {
            "summary": "Independent source and opportunity assessment.",
            "candidates": [
                {
                    "candidateId": candidate.id,
                    **dict.fromkeys(
                        ("faithfulMeaning", "completeContext", "distinctPurpose"),
                        _criterion(candidate),
                    ),
                }
                for candidate in candidates
            ],
            "selection": [
                {
                    "candidateId": candidate.id,
                    "disposition": "select",
                    "evidenceSpans": candidate.coreSpans,
                    "reason": "The treatment represents the source opportunity.",
                }
                for candidate in candidates
            ],
            "opportunities": [
                {
                    "opportunityId": opportunity.id,
                    "candidateIds": opportunity.candidateIds,
                    "status": "represented",
                    "evidenceSpans": opportunity.coreSpans,
                    "reason": "The selected treatment includes this core evidence.",
                }
                for opportunity in record.draft.opportunities
            ],
            "findings": [],
            "missingOpportunities": [],
        }
    )


def _assess(
    record: TopicSelectionRecord,
    *,
    cold: list[TopicSelectionColdReview] | None = None,
    source: TopicPortfolioReview | None = None,
) -> TopicSelectionAssessment:
    return assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=cold
        if cold is not None
        else [_cold(c) for c in record.draft.proposal.candidates],
        source_review=source if source is not None else _source(record),
        author_family="author",
        verifier_family="reviewer",
    )


def _finding(
    identifier: str, kind: str, candidates: list[str], opportunities: list[str]
) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": kind,
        "severity": "required",
        "affectedCandidateIds": candidates,
        "opportunityIds": opportunities,
        "evidenceSpans": [_span(0, 7)],
        "reason": "The cited original source supports this scoped editorial correction.",
    }


def test_frozen_audience_refinements_reach_each_stage_without_author_leakage() -> None:
    brief = (
        "Experts in irrigation. Preserve the Hindi-English code-switching.\n"
        "No introductory definitions."
    )
    rubric = make_rubric(brief)
    candidate = _candidate("middle", 2, 3)
    changed = candidate.model_copy(
        update={"purpose": "SECRET AUTHOR PURPOSE", "reason": "SECRET AUTHOR REASON"}
    )
    record = _record(candidate)
    cold = json.loads(selection_cold_prompt(EVIDENCE, changed, rubric).split("SOURCE DATA\n", 1)[1])
    author = json.loads(selection_prompt(EVIDENCE, rubric).split("SOURCE DATA\n", 1)[1])
    source = json.loads(
        selection_source_prompt(EVIDENCE, record.draft, rubric).split("SOURCE DATA\n", 1)[1]
    )
    assert rubric.originalInstructions == brief
    assert cold["rubric"] == author["rubric"] == source["rubric"] == rubric.model_dump(mode="json")
    assert set(cold) == {"candidateId", "title", "rubric", "clipSentences"}
    assert cold["clipSentences"] == [
        {"id": row.id, "text": row.text, "speakers": row.speakers}
        for row in EVIDENCE.sentences[2:4]
    ]
    assert selection_cold_key(candidate, content_hash(rubric)) == selection_cold_key(
        changed, content_hash(rubric)
    )
    assert selection_cold_prompt(EVIDENCE, candidate, rubric) == selection_cold_prompt(
        EVIDENCE, changed, rubric
    )
    other_audience = make_rubric("Beginning gardeners who need introductory definitions.")
    assert selection_cold_key(candidate, content_hash(rubric)) != selection_cold_key(
        candidate, content_hash(other_audience)
    )


def test_coherent_complete_speech_can_still_fail_viewer_value() -> None:
    candidate = _candidate("coherent", 0, 3)
    cold = _cold(candidate)
    cold.value.deliveredValue = cold.value.deliveredValue.model_copy(update={"status": "fail"})
    assessment = _assess(_record(candidate), cold=[cold])
    assert str(assessment.coldReviews[0].coherentTopic.status) == "pass"
    assert str(assessment.coldReviews[0].completeDiscussion.status) == "pass"
    assert str(assessment.executionStatus) == "needs_review"
    assert [(str(f.kind), str(f.severity)) for f in assessment.findings] == [
        ("weak_viewer_value", "required")
    ]


def test_opening_preference_does_not_erase_an_otherwise_complete_selection() -> None:
    candidate = _candidate("useful", 0, 3)
    cold = _cold(candidate)
    cold.value.openingEffectiveness = cold.value.openingEffectiveness.model_copy(
        update={"status": "fail"}
    )
    assessment = _assess(_record(candidate), cold=[cold])
    assert str(assessment.executionStatus) == "complete"
    assert [str(f.severity) for f in assessment.findings] == ["preference"]


def test_unknown_source_finding_neither_completes_selection_nor_authorizes_a_patch() -> None:
    candidate = _candidate("uncertain", 0, 3)
    record = _record(candidate)
    source = _source(record).model_dump(mode="json")
    source["findings"] = [
        {
            **_finding(
                "uncertain-value",
                "weak_viewer_value",
                [candidate.id],
                [record.draft.opportunities[0].id],
            ),
            "severity": "unknown",
        }
    ]
    assessment = _assess(record, source=TopicPortfolioReview.model_validate(source))
    assert str(assessment.executionStatus) == "needs_review"
    patch = TopicSelectionPatch.model_validate(
        {
            "baseSelectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Attempted title change.",
            "operations": [
                {
                    "id": "title",
                    "kind": "retitle",
                    "affectedCandidateIds": [candidate.id],
                    "findingIds": ["source:uncertain-value"],
                    "opportunities": [],
                    "replacementCandidates": [
                        candidate.model_copy(update={"title": "A changed title"})
                    ],
                    "reason": "This unknown observation must not grant edit authority.",
                }
            ],
        }
    )
    with pytest.raises(HarnessValidationError, match="unavailable or unknown-only"):
        apply_selection_patch(EVIDENCE, record, content_hash(record), assessment, patch)


def test_empty_selection_requires_independent_source_audit_before_completion() -> None:
    record = _record()
    unreviewed = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[],
        source_review=None,
        author_family="author",
        verifier_family=None,
    )
    assert str(unreviewed.executionStatus) == "needs_review"
    assert str(_assess(record).executionStatus) == "complete"


def _compound() -> tuple[TopicSelectionRecord, TopicSelectionAssessment, TopicSelectionPatch]:
    left, right, protected = (
        _candidate("left", 0, 1),
        _candidate("right", 2, 3),
        _candidate("protected", 4, 5),
    )
    merged, added = _candidate("merged", 0, 3), _candidate("added", 6, 7)
    record = _record(left, right, protected)
    missing = TopicOpportunity.model_validate(
        {
            **_opportunity(added).model_dump(mode="json"),
            "candidateIds": [],
            "disposition": "needs_evidence",
        }
    )
    source = _source(record).model_dump(mode="json")
    source["missingOpportunities"] = [missing.model_dump(mode="json")]
    source["findings"] = [
        _finding(
            "compound",
            "unfocused_extent",
            [left.id, right.id],
            [o.id for o in record.draft.opportunities[:2]],
        ),
        _finding("omission", "missed_opportunity", [], [missing.id]),
    ]
    assessment = _assess(record, source=TopicPortfolioReview.model_validate(source))
    patch = TopicSelectionPatch.model_validate(
        {
            "baseSelectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Merge incomplete treatments and recover a separate opportunity.",
            "operations": [
                {
                    "id": "merge",
                    "kind": "merge",
                    "affectedCandidateIds": [left.id, right.id],
                    "findingIds": ["source:compound"],
                    "replacementCandidates": [merged],
                    "opportunities": [
                        o.model_copy(update={"candidateIds": [merged.id]})
                        for o in record.draft.opportunities[:2]
                    ],
                    "reason": "A shared discussion needs both existing treatments together.",
                },
                {
                    "id": "add",
                    "kind": "add_opportunity",
                    "affectedCandidateIds": [],
                    "findingIds": ["source:omission"],
                    "replacementCandidates": [added],
                    "opportunities": [_opportunity(added)],
                    "reason": "Recover the independently found discussion.",
                },
            ],
        }
    )
    return record, assessment, patch


def test_grounded_merge_and_add_are_atomic_and_preserve_unaffected_content() -> None:
    record, assessment, patch = _compound()
    original_sha = content_hash(record)
    result = apply_selection_patch(EVIDENCE, record, original_sha, assessment, patch)
    assert {c.id for c in result.proposal.candidates} == {"protected", "merged", "added"}
    assert (
        next(c for c in result.proposal.candidates if c.id == "protected")
        == record.draft.proposal.candidates[2]
    )
    assert (
        next(o for o in result.opportunities if o.id == "opportunity-protected")
        == record.draft.opportunities[2]
    )
    assert {o.id: o.candidateIds for o in result.opportunities} == {
        "opportunity-left": ["merged"],
        "opportunity-right": ["merged"],
        "opportunity-protected": ["protected"],
        "opportunity-added": ["added"],
    }
    assert content_hash(record) == original_sha


@pytest.mark.parametrize(
    "tamper", ["protected_candidate", "opportunity_evidence", "foreign_source"]
)
def test_malformed_compound_patch_preserves_the_entire_previous_selection(tamper: str) -> None:
    record, assessment, patch = _compound()
    original_sha = content_hash(record)
    if tamper == "protected_candidate":
        patch.operations[0].affectedCandidateIds.append("protected")
        expected = "outside its grounded affected set"
    elif tamper == "opportunity_evidence":
        patch.operations[0].opportunities[0].coreSpans = [_span(2)]
        expected = "rewrite opportunity evidence"
    else:
        patch.operations[1].replacementCandidates[0].lastSentenceId = "foreign-sentence"
        expected = "unknown|unavailable"
    with pytest.raises(HarnessValidationError, match=expected):
        apply_selection_patch(EVIDENCE, record, original_sha, assessment, patch)
    assert content_hash(record) == original_sha
