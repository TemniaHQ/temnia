"""A partial repair must not forget other worthwhile discussions already discovered."""

# pyright: reportPrivateUsage=false

from uuid import uuid4

import pytest

from temnia_pipeline.contracts import (
    TopicSelectionAssessment,
    TopicSelectionPatch,
    TopicSelectionRecord,
)
from temnia_pipeline.harness.topic_selection import (
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_compiler import _candidate, _span
from test_topic_selection_workflow import CANDIDATE, EVIDENCE, draft, opportunity, portfolio


def test_partial_omission_patch_retains_every_discovered_opportunity() -> None:
    rubric = make_rubric("Find worthwhile independent discussions.")
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": uuid4(),
            "origin": "model",
            "parentSelectionSha256": None,
            "rubric": rubric,
            "rubricSha256": content_hash(rubric),
            "evidenceSha256": content_hash(EVIDENCE),
            "draft": draft(selected=False),
        }
    )
    selection_sha = content_hash(record)
    source_review = portfolio(selected=False, missing=True)
    second = opportunity(selected=False).model_copy(
        update={
            "id": "second-opportunity",
            "viewerPurpose": "Understand another worthwhile source detail.",
        }
    )
    second_finding = source_review.findings[0].model_copy(
        update={
            "id": "second-omission",
            "opportunityIds": [second.id],
        }
    )
    source_review = source_review.model_copy(
        update={
            "missingOpportunities": [*source_review.missingOpportunities, second],
            "findings": [*source_review.findings, second_finding],
        }
    )
    assessment = assess_selection(
        EVIDENCE,
        record,
        selection_sha,
        cold_reviews=[],
        source_review=source_review,
        author_family="author",
        verifier_family="reviewer",
    )
    assert assessment.portfolioReview is not None
    patch = TopicSelectionPatch.model_validate(
        {
            "baseSelectionSha256": selection_sha,
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Treat the first omission.",
            "operations": [
                {
                    "id": "add-first",
                    "kind": "add_opportunity",
                    "findingIds": ["source:missing"],
                    "affectedCandidateIds": [],
                    "replacementCandidates": [CANDIDATE],
                    "opportunities": [opportunity(selected=True)],
                    "reason": "The supplied core and completion support this treatment.",
                }
            ],
        }
    )
    output = apply_selection_patch(EVIDENCE, record, selection_sha, assessment, patch)
    assert {item.id for item in output.opportunities} == {"useful-discussion", second.id}
    retained = next(item for item in output.opportunities if item.id == second.id)
    assert retained == second
    assert retained.candidateIds == []
    assert str(retained.disposition) == "needs_evidence"
    # A declined/no-op repair does not manufacture progress or discard its retained assessment.
    noop = patch.model_copy(update={"operations": []})
    assert apply_selection_patch(EVIDENCE, record, selection_sha, assessment, noop) == record.draft


def test_physical_only_extension_keeps_original_semantic_annotations() -> None:
    rubric = make_rubric("Find worthwhile independent discussions.")
    original = _candidate("discussion", 1, 3)
    selected = draft(selected=True)
    selected.proposal.candidates = [original]
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": uuid4(),
            "origin": "model",
            "parentSelectionSha256": None,
            "rubric": rubric,
            "rubricSha256": content_hash(rubric),
            "evidenceSha256": content_hash(EVIDENCE),
            "draft": selected,
        }
    )
    selection_sha = content_hash(record)
    assessment = TopicSelectionAssessment.model_validate(
        {
            "format": "topic-selection-assessment/2",
            "runId": record.runId,
            "selectionSha256": selection_sha,
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "coldReviews": [],
            "portfolioReview": None,
            "proposerFamily": "author",
            "verifierFamily": None,
            "executionStatus": "needs_review",
            "reasons": [],
            "responseArtifacts": [],
            "findings": [
                {
                    "id": "physical:discussion:opening",
                    "kind": "physical_boundary_constraint",
                    "severity": "required",
                    "affectedCandidateIds": ["discussion"],
                    "opportunityIds": ["useful-discussion"],
                    "evidenceSpans": [_span(0, 1)],
                    "reason": "The opening requires a wider physical extent.",
                }
            ],
        }
    )
    extended = original.model_copy(update={"firstSentenceId": "s000000"})
    patch = TopicSelectionPatch.model_validate(
        {
            "baseSelectionSha256": selection_sha,
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Widen the physical opening.",
            "operations": [
                {
                    "id": "extend",
                    "kind": "extend_start",
                    "findingIds": ["physical:discussion:opening"],
                    "affectedCandidateIds": ["discussion"],
                    "replacementCandidates": [extended],
                    "opportunities": [],
                    "reason": "Include the prior sentence to obtain a safe opening.",
                }
            ],
        }
    )
    output = apply_selection_patch(EVIDENCE, record, selection_sha, assessment, patch)
    assert output.proposal.candidates[0] == extended
    rewritten = extended.model_copy(update={"completionSpans": [_span(0)]})
    corrupt = patch.model_copy(
        update={
            "operations": [
                patch.operations[0].model_copy(
                    update={
                        "replacementCandidates": [rewritten],
                    }
                )
            ]
        }
    )
    with pytest.raises(HarnessValidationError, match="physical-only extension changed semantic"):
        apply_selection_patch(EVIDENCE, record, selection_sha, assessment, corrupt)
