"""Pure audience, value and compound selection authority invariants."""

# Shared source construction is deterministic; no activity, provider or model is involved.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from temnia_pipeline.contracts import (
    TopicBoundaryIssue,
    TopicCandidate,
    TopicOpportunity,
    TopicPortfolioReview,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionFinding,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
)
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence
from temnia_pipeline.harness.topic_selection import (
    apply_selection_patch,
    assess_selection,
    candidate_handoff_rows,
    candidate_overlap_rows,
    content_hash,
    make_rubric,
    selection_candidates_for_render,
    selection_cold_key,
    selection_cold_prompt,
    selection_patch_prompt_v3,
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


def _source_v3(record: TopicSelectionRecord) -> TopicPortfolioReviewV4:
    payload = _source(record).model_dump(mode="json")
    payload["candidates"] = []
    payload["overlaps"] = [
        {
            **row,
            "classification": "necessary_shared_context",
            "reason": "Both standalone treatments independently require this shared setup.",
        }
        for row in candidate_overlap_rows(EVIDENCE, record.draft)
    ]
    payload["handoffs"] = [
        {
            **row,
            "classification": "clean_handoff",
            "recommendedLeftLastSentenceId": None,
            "recommendedRightFirstSentenceId": None,
            "reason": "The neighbouring topics have a clean semantic handoff.",
        }
        for row in candidate_handoff_rows(EVIDENCE, record.draft)
    ]
    return TopicPortfolioReviewV4.model_validate(payload)


def _assess(
    record: TopicSelectionRecord,
    *,
    cold: list[TopicSelectionColdReview] | None = None,
    source: TopicPortfolioReviewV4 | None = None,
) -> TopicSelectionAssessment:
    return assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=cold
        if cold is not None
        else [_cold(c) for c in record.draft.proposal.candidates],
        source_review=source if source is not None else _source_v3(record),
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


def test_inventory_first_source_review_uses_compact_portfolio_contract() -> None:
    candidate = _candidate("useful", 0, 3)
    record = _record(candidate)
    prompt = selection_source_prompt(
        EVIDENCE,
        record.draft,
        record.rubric,
    )
    instruction, payload_text = prompt.split("SOURCE DATA\n", 1)
    payload = json.loads(payload_text)
    assert "Leave candidates as an empty array" in instruction
    assert "misallocated extent" in instruction
    assert "selectionWithoutAuthorRationale" in payload
    assert "reason" not in payload["selectionWithoutAuthorRationale"]["candidates"][0]
    assert payload["candidateOverlaps"] == []

    compact = _source_v3(record)
    assessment = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(candidate)],
        source_review=compact,
        author_family="author",
        verifier_family="reviewer",
    )
    assert str(assessment.executionStatus) == "complete"
    assert selection_candidates_for_render(
        record, assessment, require_complete_review=True
    ).candidates == [candidate]
    author_instruction = selection_prompt(
        EVIDENCE,
        record.rubric,
        source_inventory=record.draft,
    ).split("SOURCE DATA\n", 1)[0]
    assert "one clear candidate owner" in author_instruction
    assert "self-contained statement of the new topic" in author_instruction
    assert "copy viewerPurpose" in author_instruction
    assert "do not rewrite inventoried evidence" in author_instruction
    repair_instruction = selection_patch_prompt_v3(
        EVIDENCE,
        record,
        assessment,
        content_hash(record),
    ).split("SOURCE DATA\n", 1)[0]
    assert "coordinated" in repair_instruction
    assert "trim the earlier candidate" in repair_instruction
    assert "copy every candidate field exactly" in repair_instruction
    assert "replace_candidate" in repair_instruction
    assert "content and title or purpose" in repair_instruction
    assert "not the number of findings" in repair_instruction
    assert "trim the connective or anaphoric runway" in repair_instruction


def test_inventory_first_review_must_classify_every_exact_candidate_overlap() -> None:
    earlier = _candidate("prayer", 0, 4)
    later = _candidate("mantra", 3, 7)
    record = _record(earlier, later)
    expected_overlap = _span(3, 4)
    prompt_payload = json.loads(
        selection_source_prompt(EVIDENCE, record.draft, record.rubric).split("SOURCE DATA\n", 1)[1]
    )
    assert prompt_payload["candidateOverlaps"] == [
        {
            "candidateIds": [earlier.id, later.id],
            "overlapSpan": expected_overlap.model_dump(mode="json"),
        }
    ]

    omitted = _source_v3(record).model_copy(update={"overlaps": []})
    unavailable = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=omitted,
        author_family="author",
        verifier_family="reviewer",
    )
    assert unavailable.portfolioReview is None
    assert "must assess every supplied pair exactly once" in " ".join(unavailable.reasons)

    unsupported_shared = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=_source_v3(record),
        author_family="author",
        verifier_family="reviewer",
    )
    assert unsupported_shared.portfolioReview is None
    assert "explicit required context for both" in " ".join(unsupported_shared.reasons)

    review = _source_v3(record)
    review.overlaps[0] = review.overlaps[0].model_copy(
        update={
            "classification": "misallocated_topic_extent",
            "reason": "The later topic's opening premise remains in the earlier video's tail.",
        }
    )
    missing_finding = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=review,
        author_family="author",
        verifier_family="reviewer",
    )
    assert missing_finding.portfolioReview is None
    assert "lacks its required two-candidate finding" in " ".join(missing_finding.reasons)
    review.findings = [
        TopicSelectionFinding.model_validate(
            {
                "id": "prayer-mantra-handoff",
                "kind": "unfocused_extent",
                "severity": "required",
                "affectedCandidateIds": [earlier.id, later.id],
                "opportunityIds": [item.id for item in record.draft.opportunities],
                "evidenceSpans": [expected_overlap],
                "reason": "Trim prayer before the mantra premise and let mantra own it.",
            }
        )
    ]
    assessed = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=review,
        author_family="author",
        verifier_family="reviewer",
    )
    assert assessed.portfolioReview is not None
    assert [(str(item.kind), item.affectedCandidateIds) for item in assessed.findings] == [
        ("unfocused_extent", [earlier.id, later.id])
    ]

    shared_earlier = earlier.model_copy(update={"requiredContextSpans": [expected_overlap]})
    shared_later = later.model_copy(update={"requiredContextSpans": [expected_overlap]})
    shared_record = _record(shared_earlier, shared_later)
    accepted_shared = assess_selection(
        EVIDENCE,
        shared_record,
        content_hash(shared_record),
        cold_reviews=[_cold(shared_earlier), _cold(shared_later)],
        source_review=_source_v3(shared_record),
        author_family="author",
        verifier_family="reviewer",
    )
    assert accepted_shared.portfolioReview is not None


def test_inventory_first_review_must_classify_every_adjacent_handoff() -> None:
    earlier = _candidate("prayer", 0, 3)
    later = _candidate("mantra", 5, 7)
    record = _record(earlier, later)
    expected = candidate_handoff_rows(EVIDENCE, record.draft)
    prompt_payload = json.loads(
        selection_source_prompt(EVIDENCE, record.draft, record.rubric).split("SOURCE DATA\n", 1)[1]
    )
    assert prompt_payload["candidateHandoffs"] == expected

    omitted = _source_v3(record).model_copy(update={"handoffs": []})
    unavailable = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=omitted,
        author_family="author",
        verifier_family="reviewer",
    )
    assert unavailable.portfolioReview is None
    assert "must assess every supplied pair exactly once in order" in " ".join(unavailable.reasons)

    first = _candidate("prayer", 0, 1)
    second = _candidate("mantra", 3, 4)
    third = _candidate("practice", 6, 7)
    three = _record(first, second, third)
    reordered_source = _source_v3(three)
    reordered = reordered_source.model_copy(
        update={"handoffs": list(reversed(reordered_source.handoffs))}
    )
    unavailable = assess_selection(
        EVIDENCE,
        three,
        content_hash(three),
        cold_reviews=[_cold(first), _cold(second), _cold(third)],
        source_review=reordered,
        author_family="author",
        verifier_family="reviewer",
    )
    assert unavailable.portfolioReview is None
    assert "must assess every supplied pair exactly once in order" in " ".join(unavailable.reasons)


def test_one_source_handoff_finding_can_move_both_candidate_edges_atomically() -> None:
    earlier = _candidate("earlier", 0, 4).model_copy(
        update={"coreSpans": [_span(0, 3)], "completionSpans": [_span(3)]}
    )
    later = _candidate("later", 5, 7)
    record = _record(earlier, later)
    later_opportunity = record.draft.opportunities[1].model_copy(
        update={"requiredContextSpans": [_span(4)]}
    )
    record = record.model_copy(
        update={
            "draft": record.draft.model_copy(
                update={"opportunities": [record.draft.opportunities[0], later_opportunity]}
            )
        }
    )
    selection_sha = content_hash(record)
    source = _source_v3(record).model_copy(
        update={
            "candidates": [],
            "findings": [
                TopicSelectionFinding.model_validate(
                    {
                        "id": "topic-handoff",
                        "kind": "unfocused_extent",
                        "severity": "required",
                        "affectedCandidateIds": [earlier.id, later.id],
                        "opportunityIds": [
                            record.draft.opportunities[0].id,
                            later_opportunity.id,
                        ],
                        "evidenceSpans": [_span(4)],
                        "reason": "The later discussion's premise is stranded in the earlier tail.",
                    }
                )
            ],
            "handoffs": [
                _source_v3(record)
                .handoffs[0]
                .model_copy(
                    update={
                        "classification": "misallocated_topic_extent",
                        "recommendedLeftLastSentenceId": "s000003",
                        "recommendedRightFirstSentenceId": "s000004",
                        "reason": (
                            "Trim the earlier bridge and give its premise to the later topic."
                        ),
                    }
                )
            ],
        }
    )
    assessment = assess_selection(
        EVIDENCE,
        record,
        selection_sha,
        cold_reviews=[_cold(earlier), _cold(later)],
        source_review=source,
        author_family="author",
        verifier_family="reviewer",
    )
    repaired_earlier = earlier.model_copy(update={"lastSentenceId": "s000003"})
    repaired_later = later.model_copy(
        update={
            "firstSentenceId": "s000004",
            "requiredContextSpans": [_span(4)],
        }
    )
    patch = TopicSelectionPatchV3.model_validate(
        {
            "baseSelectionSha256": selection_sha,
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Give the complete later discussion one candidate owner.",
            "operations": [
                {
                    "id": "trim-earlier",
                    "kind": "replace_extent",
                    "findingIds": ["source:topic-handoff"],
                    "affectedCandidateIds": [earlier.id],
                    "replacementCandidates": [repaired_earlier],
                    "opportunities": [],
                    "reason": "End before the later topic begins.",
                },
                {
                    "id": "extend-later",
                    "kind": "replace_extent",
                    "findingIds": ["source:topic-handoff"],
                    "affectedCandidateIds": [later.id],
                    "replacementCandidates": [repaired_later],
                    "opportunities": [],
                    "reason": "Start with the premise that makes the later discussion complete.",
                },
            ],
        }
    )
    repaired = apply_selection_patch(EVIDENCE, record, selection_sha, assessment, patch)
    by_id = {candidate.id: candidate for candidate in repaired.proposal.candidates}
    assert by_id[earlier.id].lastSentenceId == "s000003"
    assert by_id[later.id].firstSentenceId == "s000004"

    wrong_later = repaired_later.model_copy(update={"firstSentenceId": "s000003"})
    wrong_patch = patch.model_copy(
        update={
            "operations": [
                patch.operations[0],
                patch.operations[1].model_copy(update={"replacementCandidates": [wrong_later]}),
            ]
        }
    )
    with pytest.raises(HarnessValidationError, match="reviewed handoff boundaries exactly"):
        apply_selection_patch(EVIDENCE, record, selection_sha, assessment, wrong_patch)


def test_one_replace_candidate_can_change_extent_and_title_atomically() -> None:
    candidate = _candidate("combined", 0, 3)
    record = _record(candidate)
    findings = [
        TopicSelectionFinding.model_validate(
            {
                "id": "cold:combined:completeDiscussion",
                "kind": "unfinished_discussion",
                "severity": "required",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [record.draft.opportunities[0].id],
                "evidenceSpans": [_span(3, 4)],
                "reason": "The answer completes one sentence later.",
            }
        ),
        TopicSelectionFinding.model_validate(
            {
                "id": "cold:combined:titleFaithful",
                "kind": "unsupported_title",
                "severity": "required",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [record.draft.opportunities[0].id],
                "evidenceSpans": [_span(1, 4)],
                "reason": "The title must describe the completed claim.",
            }
        ),
    ]
    assessment = _assess(record).model_copy(
        update={"executionStatus": "needs_review", "findings": findings}
    )
    replacement = candidate.model_copy(
        update={
            "title": "The completed source explanation",
            "lastSentenceId": "s000004",
            "completionSpans": [_span(4)],
            "reason": "The expanded treatment includes the final answer.",
        }
    )
    patch = TopicSelectionPatchV3.model_validate(
        {
            "baseSelectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Apply the coupled extent and title correction once.",
            "operations": [
                {
                    "id": "combined-correction",
                    "kind": "replace_candidate",
                    "affectedCandidateIds": [candidate.id],
                    "findingIds": [item.id for item in findings],
                    "opportunities": [],
                    "replacementCandidates": [replacement],
                    "reason": "Both findings concern one inseparable candidate correction.",
                }
            ],
        }
    )
    updated = apply_selection_patch(EVIDENCE, record, content_hash(record), assessment, patch)
    assert updated.proposal.candidates == [replacement]

    extent_only_assessment = assessment.model_copy(update={"findings": [findings[0]]})
    unauthorized_title_patch = patch.model_copy(
        update={
            "operations": [patch.operations[0].model_copy(update={"findingIds": [findings[0].id]})]
        }
    )
    with pytest.raises(HarnessValidationError, match="unsupported-title finding"):
        apply_selection_patch(
            EVIDENCE,
            record,
            content_hash(record),
            extent_only_assessment,
            unauthorized_title_patch,
        )


def test_extent_only_replace_candidate_keeps_the_whole_patch_and_raw_output() -> None:
    """The measured two-edge/annotation label mistake must not discard another repair."""
    first = _candidate("first", 1, 3).model_copy(update={"coreSpans": [_span(1, 2)]})
    second = _candidate("second", 5, 6)
    record = _record(first, second)
    findings = [
        TopicSelectionFinding.model_validate(
            {
                "id": f"focus:{candidate.id}",
                "kind": "unfocused_extent",
                "severity": "required",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [f"opportunity-{candidate.id}"],
                "evidenceSpans": [_span(0, 7)],
                "reason": "Correct this candidate's extent and its evidence annotations.",
            }
        )
        for candidate in (first, second)
    ]
    assessment = _assess(record).model_copy(
        update={"executionStatus": "needs_review", "findings": findings}
    )
    replacements = [
        first.model_copy(
            update={
                "firstSentenceId": "s000000",
                "lastSentenceId": "s000002",
                "requiredContextSpans": [_span(0)],
                "coreSpans": [_span(1, 2)],
                "completionSpans": [_span(2)],
            }
        ),
        second.model_copy(update={"lastSentenceId": "s000007", "completionSpans": [_span(7)]}),
    ]
    patch = TopicSelectionPatchV3.model_validate(
        {
            "baseSelectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Correct both candidates in one transaction.",
            "operations": [
                {
                    "id": f"repair:{replacement.id}",
                    "kind": "replace_candidate",
                    "affectedCandidateIds": [replacement.id],
                    "findingIds": [finding.id],
                    "opportunities": [],
                    "replacementCandidates": [replacement],
                    "reason": "Apply the cited extent correction.",
                }
                for replacement, finding in zip(replacements, findings, strict=True)
            ],
        }
    )
    original_record, raw_patch = record.model_dump_json(), patch.model_dump_json()
    output = apply_selection_patch(EVIDENCE, record, content_hash(record), assessment, patch)
    assert output.proposal.candidates == replacements
    assert record.model_dump_json() == original_record
    assert patch.model_dump_json() == raw_patch

    unauthorized = patch.model_copy(deep=True)
    unauthorized.operations[1].replacementCandidates[0].title = "Unreviewed new claim"
    with pytest.raises(HarnessValidationError, match="unsupported-title finding"):
        apply_selection_patch(EVIDENCE, record, content_hash(record), assessment, unauthorized)
    assert record.model_dump_json() == original_record

    annotation_only = patch.model_copy(deep=True)
    annotated = first.model_copy(
        update={"requiredContextSpans": [_span(1)], "coreSpans": [_span(2)]}
    )
    annotation_only.operations[0].replacementCandidates = [annotated]
    admitted = apply_selection_patch(
        EVIDENCE, record, content_hash(record), assessment, annotation_only
    )
    assert admitted.proposal.candidates[0] == annotated


def test_single_axis_replace_candidate_needs_that_axis_finding() -> None:
    """A purpose-only or annotation-only correction is representable under its own finding."""
    candidate = _candidate("axis", 0, 3)
    record = _record(candidate)

    def apply(replacement: TopicCandidate, kind: str) -> TopicProposal:
        finding = TopicSelectionFinding.model_validate(
            {
                "id": f"cold:axis:{kind}",
                "kind": kind,
                "severity": "required",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [f"opportunity-{candidate.id}"],
                "evidenceSpans": [_span(0, 3)],
                "reason": "Correct this axis.",
            }
        )
        assessment = _assess(record).model_copy(
            update={"executionStatus": "needs_review", "findings": [finding]}
        )
        patch = TopicSelectionPatchV3.model_validate(
            {
                "baseSelectionSha256": content_hash(record),
                "evidenceSha256": record.evidenceSha256,
                "rubricSha256": record.rubricSha256,
                "summary": "Correct one axis.",
                "operations": [
                    {
                        "id": "axis",
                        "kind": "replace_candidate",
                        "affectedCandidateIds": [candidate.id],
                        "findingIds": [finding.id],
                        "opportunities": [],
                        "replacementCandidates": [replacement],
                        "reason": "Apply the cited correction.",
                    }
                ],
            }
        )
        return apply_selection_patch(
            EVIDENCE, record, content_hash(record), assessment, patch
        ).proposal

    repurposed = candidate.model_copy(
        update={"purpose": "Show why the discussion matters to a first-time viewer."}
    )
    assert apply(repurposed, "weak_viewer_value").candidates == [repurposed]
    with pytest.raises(HarnessValidationError, match="purpose without a value or focus finding"):
        apply(repurposed, "unfinished_discussion")

    annotated = candidate.model_copy(
        update={"requiredContextSpans": [_span(0)], "coreSpans": [_span(1, 3)]}
    )
    assert apply(annotated, "unfinished_discussion").candidates == [annotated]
    with pytest.raises(HarnessValidationError, match="content without an extent-related finding"):
        apply(annotated, "unsupported_title")

    reworded = candidate.model_copy(update={"reason": "Only the explanation changed."})
    with pytest.raises(HarnessValidationError, match="changed nothing but prose"):
        apply(reworded, "unfocused_extent")


def test_title_only_replace_candidate_is_the_equivalent_retitle() -> None:
    """A title-only correction is judged as a retitle whichever label it wears."""
    candidate = _candidate("titled", 0, 3)
    record = _record(candidate)
    title_finding = TopicSelectionFinding.model_validate(
        {
            "id": "cold:titled:titleFaithful",
            "kind": "unsupported_title",
            "severity": "required",
            "affectedCandidateIds": [candidate.id],
            "opportunityIds": [f"opportunity-{candidate.id}"],
            "evidenceSpans": [_span(0, 3)],
            "reason": "The title promises more than the discussion delivers.",
        }
    )
    focus_finding = title_finding.model_copy(
        update={"id": "cold:titled:coherentTopic", "kind": "unfocused_extent"}
    )
    retitled = candidate.model_copy(update={"title": "A faithful title"})

    def patch(finding: TopicSelectionFinding, kind: str) -> TopicSelectionPatchV3:
        return TopicSelectionPatchV3.model_validate(
            {
                "baseSelectionSha256": content_hash(record),
                "evidenceSha256": record.evidenceSha256,
                "rubricSha256": record.rubricSha256,
                "summary": "Correct the title.",
                "operations": [
                    {
                        "id": "title",
                        "kind": kind,
                        "affectedCandidateIds": [candidate.id],
                        "findingIds": [finding.id],
                        "opportunities": [],
                        "replacementCandidates": [retitled],
                        "reason": "Name what the discussion actually delivers.",
                    }
                ],
            }
        )

    def assessment(finding: TopicSelectionFinding) -> TopicSelectionAssessment:
        return _assess(record).model_copy(
            update={"executionStatus": "needs_review", "findings": [finding]}
        )

    admitted = apply_selection_patch(
        EVIDENCE,
        record,
        content_hash(record),
        assessment(title_finding),
        patch(title_finding, "replace_candidate"),
    )
    assert admitted.proposal.candidates == [retitled]
    for kind in ("retitle", "replace_candidate"):
        with pytest.raises(HarnessValidationError, match="unsupported-title finding"):
            apply_selection_patch(
                EVIDENCE,
                record,
                content_hash(record),
                assessment(focus_finding),
                patch(focus_finding, kind),
            )


def test_render_gate_withholds_unreviewed_or_known_invalid_candidates() -> None:
    candidate = _candidate("useful", 0, 3)
    record = _record(candidate)
    incomplete = _assess(record).model_copy(update={"portfolioReview": None})
    assert (
        selection_candidates_for_render(record, incomplete, require_complete_review=True).candidates
        == []
    )

    source = _source_v3(record).model_copy(
        update={
            "findings": [
                TopicSelectionFinding.model_validate(
                    {
                        "id": "compound",
                        "kind": "duplicate_core",
                        "severity": "required",
                        "affectedCandidateIds": [candidate.id],
                        "opportunityIds": [record.draft.opportunities[0].id],
                        "evidenceSpans": candidate.coreSpans,
                        "reason": "The candidate duplicates another treatment's core value.",
                    }
                )
            ]
        }
    )
    blocked = _assess(record, source=source)
    assert (
        selection_candidates_for_render(record, blocked, require_complete_review=True).candidates
        == []
    )


def test_physical_findings_unwrap_strict_supporting_sentence_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate("unsafe-edge", 0, 3)
    record = _record(candidate)
    issue = TopicBoundaryIssue.model_validate(
        {
            "code": "no-safe-cut",
            "edge": "opening",
            "reason": "No safe opening cut exists before the selected speech.",
            "supportingSentenceIds": ["s000000", "s000001"],
        }
    )

    def boundary_issues(_evidence: object, _candidate: object) -> tuple[TopicBoundaryIssue, ...]:
        return (issue,)

    monkeypatch.setattr(
        "temnia_pipeline.harness.topic_compiler.topic_boundary_issues_v2",
        boundary_issues,
    )
    assessment = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(candidate)],
        source_review=_source_v3(record),
        author_family="author",
        verifier_family="reviewer",
    )
    physical = next(
        finding
        for finding in assessment.findings
        if str(finding.kind) == "physical_boundary_constraint"
    )
    assert physical.evidenceSpans[0].firstSentenceId == "s000000"
    assert physical.evidenceSpans[1].lastSentenceId == "s000001"


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


def test_source_unfocused_extent_is_a_required_publication_correction() -> None:
    candidate = _candidate("sprawling", 0, 3)
    record = _record(candidate)
    source = _source_v3(record)
    source.findings = [
        TopicSelectionFinding.model_validate(
            {
                "id": "off-purpose-tail",
                "kind": "unfocused_extent",
                "severity": "preference",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [record.draft.opportunities[0].id],
                "evidenceSpans": [_span(3)],
                "reason": "The final sentence starts a separate discussion.",
            }
        )
    ]
    source.candidates = []
    assessment = assess_selection(
        EVIDENCE,
        record,
        content_hash(record),
        cold_reviews=[_cold(candidate)],
        source_review=source,
        author_family="author",
        verifier_family="reviewer",
    )
    assert str(assessment.executionStatus) == "needs_review"
    assert [(str(f.kind), str(f.severity)) for f in assessment.findings] == [
        ("unfocused_extent", "required")
    ]


def test_unknown_source_finding_neither_completes_selection_nor_authorizes_a_patch() -> None:
    candidate = _candidate("uncertain", 0, 3)
    record = _record(candidate)
    source = _source_v3(record).model_dump(mode="json")
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
    assessment = _assess(record, source=TopicPortfolioReviewV4.model_validate(source))
    assert str(assessment.executionStatus) == "needs_review"
    patch = TopicSelectionPatchV3.model_validate(
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


def _compound() -> tuple[TopicSelectionRecord, TopicSelectionAssessment, TopicSelectionPatchV3]:
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
    source = _source_v3(record).model_dump(mode="json")
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
    assessment = _assess(record, source=TopicPortfolioReviewV4.model_validate(source))
    patch = TopicSelectionPatchV3.model_validate(
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
                    "affectedCandidateIds": [added.id],
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
