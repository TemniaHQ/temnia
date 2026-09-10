"""Editorial input isolation and repair authority are enforced before paid execution."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import (
    SpeechCoverageInterval,
    TopicAssessment,
    TopicCandidate,
    TopicColdReview,
    TopicCriterion,
    TopicProposal,
    TopicSourceJudgment,
)
from temnia_pipeline.harness.routes import NoEligibleRoute, RouteSnapshot, SeatRoutePool
from temnia_pipeline.harness.topic_compiler import compile_topics, topic_boundary_issues
from temnia_pipeline.harness.topic_editorial import (
    assessment_has_grounded_failure,
    cold_prompt,
    cold_review_key,
    editorial_routes,
    ground_review,
    plan_prompt,
    sentence_rows,
    source_prompt,
    topic_semantic_key,
    validate_assessment,
    validate_preserved_candidates,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_compiler import ARTIFACT_ID, SHA
from test_topic_compiler import _candidate, _case, _span


def _criterion(status: str = "pass", *, first: int = 1) -> TopicCriterion:
    return TopicCriterion.model_validate(
        {
            "status": status,
            "reason": "This supplied passage contains the supporting evidence.",
            "evidenceSpans": [_span(first).model_dump()],
        }
    )


def _cold(identifier: str, status: str = "pass") -> TopicColdReview:
    return TopicColdReview.model_validate(
        {
            "candidateId": identifier,
            **{
                field: _criterion(status).model_dump()
                for field in (
                    "intelligibleBeginning",
                    "coherentTopic",
                    "completeDiscussion",
                    "titleFaithful",
                )
            },
        }
    )


def _source(identifier: str, status: str = "pass") -> TopicSourceJudgment:
    return TopicSourceJudgment.model_validate(
        {
            "candidateId": identifier,
            **{
                field: _criterion(status).model_dump()
                for field in ("faithfulMeaning", "completeContext", "distinctPurpose")
            },
        }
    )


def _proposal(*candidates: TopicCandidate) -> TopicProposal:
    return TopicProposal(
        candidates=list(candidates), summary="Useful source discussions.", version=1
    )


def _assessment(proposal: TopicProposal, status: str = "pass") -> TopicAssessment:
    return TopicAssessment.model_validate(
        {
            "format": "topic-assessment/1",
            "runId": str(uuid4()),
            "proposalSha256": "a" * 64,
            "evidenceSha256": "b" * 64,
            "proposerFamily": "author-fixture",
            "verifierFamily": "critic-fixture",
            "summary": "Independent textual assessment.",
            "candidates": [
                {
                    "candidateId": candidate.id,
                    "status": "passed" if status == "pass" else "needs_review",
                    "coldReview": _cold(candidate.id, status).model_dump(),
                    "sourceReview": _source(candidate.id, status).model_dump(),
                    "reasons": [] if status == "pass" else ["A criterion needs review."],
                    "physicalBoundaryIssues": [],
                }
                for candidate in proposal.candidates
            ],
        }
    )


def _payload(prompt: str) -> dict[str, object]:
    return json.loads(prompt.split("\nSOURCE DATA\n", 1)[1])


def test_cold_input_excludes_outside_text_planner_annotations_and_source_clock() -> None:
    evidence = _case()
    candidate = _candidate("middle", 1, 2)
    candidate.purpose = "PLAN PURPOSE MUST NOT REACH COLD JUDGE"
    candidate.reason = "PLAN REASON MUST NOT REACH COLD JUDGE"
    payload = _payload(cold_prompt(evidence, candidate))
    assert payload == {
        "candidateId": "middle",
        "title": "Topic middle",
        "clipSentences": [
            {"id": sentence.id, "speakers": sentence.speakers, "text": sentence.text}
            for sentence in evidence.sentences[1:3]
        ],
    }


def test_cold_key_tracks_exact_review_content_not_invisible_planning_annotations() -> None:
    evidence = _case()
    candidate = _candidate("middle", 1, 2)
    changed = candidate.model_copy(
        update={"purpose": "Changed plan", "reason": "New reasoning", "coreSpans": [_span(2)]}
    )
    assert cold_review_key(candidate) == cold_review_key(changed)
    assert cold_prompt(evidence, candidate) == cold_prompt(evidence, changed)
    for field, value in (
        ("id", "new-id"),
        ("title", "Changed title"),
        ("firstSentenceId", "s000000"),
        ("lastSentenceId", "s000003"),
    ):
        assert cold_review_key(candidate) != cold_review_key(
            candidate.model_copy(update={field: value})
        )


def test_semantic_key_ignores_only_summary_and_candidate_reason() -> None:
    candidate = _candidate("middle", 1, 2)
    proposal = _proposal(candidate)
    cosmetic = proposal.model_copy(
        update={
            "summary": "Different narration of the same result.",
            "candidates": [candidate.model_copy(update={"reason": "New rationale only."})],
        }
    )
    assert topic_semantic_key(proposal) == topic_semantic_key(cosmetic)
    for field, value in (
        ("purpose", "A different viewer purpose"),
        ("title", "A different promise"),
        ("id", "new-topic"),
        ("firstSentenceId", "s000000"),
        ("coreSpans", [_span(1)]),
        ("requiredContextSpans", [_span(1)]),
        ("completionSpans", [_span(1)]),
        ("meaningChangingFollowups", [_span(2)]),
    ):
        changed = _proposal(candidate.model_copy(update={field: value}))
        assert topic_semantic_key(proposal) != topic_semantic_key(changed)


def test_global_author_and_source_review_keep_the_complete_original_sentence_text() -> None:
    evidence = _case()
    proposal = _proposal(_candidate("middle", 1, 2))
    expected = sentence_rows(evidence)
    author_input = _payload(plan_prompt(evidence, "For interested newcomers."))
    assert author_input["sourceSentences"] == expected
    assert author_input["userInstructions"] == "For interested newcomers."
    assert _payload(source_prompt(evidence, proposal))["sourceSentences"] == expected
    assert "previousProposal" not in author_input


@pytest.mark.parametrize(("first", "last"), [(3, 1), (99, 100)])
def test_invalid_extent_cannot_become_an_empty_cold_review(first: int, last: int) -> None:
    with pytest.raises(HarnessValidationError, match="unknown or reversed"):
        cold_prompt(_case(), _candidate("bad", first, last))


@pytest.mark.parametrize("status", ["pass", "fail"])
def test_decisive_judgments_need_cited_evidence(status: str) -> None:
    review = _cold("middle", status)
    review.completeDiscussion.evidenceSpans = []
    with pytest.raises(HarnessValidationError, match="needs source evidence"):
        ground_review(_case(), _candidate("middle", 1, 2), review, cold=True)


def test_unknown_can_report_missing_evidence_but_still_needs_a_reason() -> None:
    review = _cold("middle", "unknown")
    review.completeDiscussion.evidenceSpans = []
    ground_review(_case(), _candidate("middle", 1, 2), review, cold=True)
    review.completeDiscussion.reason = "   "
    with pytest.raises(HarnessValidationError, match="nonblank reason"):
        ground_review(_case(), _candidate("middle", 1, 2), review, cold=True)


def test_source_judge_can_cite_omitted_correction_that_cold_judge_cannot_see() -> None:
    evidence = _case()
    candidate = _candidate("claim", 1, 1)
    source = _source(candidate.id)
    source.faithfulMeaning = _criterion("fail", first=2)
    ground_review(evidence, candidate, source, cold=False)
    cold = _cold(candidate.id)
    cold.completeDiscussion = _criterion("fail", first=2)
    with pytest.raises(HarnessValidationError, match="unavailable or reversed"):
        ground_review(evidence, candidate, cold, cold=True)


def test_source_pass_cannot_be_anchored_only_to_unselected_material() -> None:
    source = _source("middle")
    source.faithfulMeaning = _criterion(first=0)
    with pytest.raises(HarnessValidationError, match="must cite the selected content"):
        ground_review(_case(), _candidate("middle", 1, 2), source, cold=False)


def test_review_ownership_and_modality_are_not_interchangeable() -> None:
    with pytest.raises(HarnessValidationError, match="exact clip"):
        ground_review(_case(), _candidate("middle", 1, 2), _cold("other"), cold=True)
    with pytest.raises(HarnessValidationError, match="modality"):
        ground_review(_case(), _candidate("middle", 1, 2), _source("middle"), cold=True)


@pytest.mark.parametrize(
    "invalid", ["missing", "duplicate", "foreign", "self-review", "false-pass"]
)
def test_assessment_must_cover_exact_candidates_with_real_independent_passing_reviews(
    invalid: str,
) -> None:
    proposal = _proposal(_candidate("middle", 1, 2))
    assessment = _assessment(proposal)
    if invalid == "missing":
        assessment.candidates = []
    elif invalid == "duplicate":
        assessment.candidates.append(assessment.candidates[0])
    elif invalid == "foreign":
        assessment.candidates[0].candidateId = "other"
    elif invalid == "self-review":
        assessment.verifierFamily = assessment.proposerFamily
    else:
        assessment.candidates[0].sourceReview = None
    with pytest.raises(HarnessValidationError):
        validate_assessment(_case(), proposal, assessment)


def test_repair_preserves_passing_topic_but_may_revise_a_grounded_failed_topic() -> None:
    first, second = _candidate("passed", 1, 2), _candidate("failed", 1, 2)
    proposal = _proposal(first, second)
    assessment = _assessment(proposal)
    assessment.candidates[1] = _assessment(_proposal(second), "fail").candidates[0]
    assert assessment_has_grounded_failure(assessment)
    changed_second = second.model_copy(update={"firstSentenceId": "s000000"})
    replacement = _proposal(first, changed_second)
    validate_preserved_candidates(proposal, replacement, assessment, evidence=_case())
    replacement.candidates[0] = first.model_copy(update={"title": "Unnecessary rewrite"})
    with pytest.raises(HarnessValidationError, match="no grounded editorial failure"):
        validate_preserved_candidates(proposal, replacement, assessment, evidence=_case())


def test_unknown_only_review_does_not_authorize_rewriting_or_removing_a_topic() -> None:
    candidate = _candidate("middle", 1, 2)
    proposal = _proposal(candidate)
    assessment = _assessment(proposal, "unknown")
    assert not assessment_has_grounded_failure(assessment)
    validate_preserved_candidates(proposal, proposal, assessment, evidence=_case())
    with pytest.raises(HarnessValidationError, match="no grounded editorial failure"):
        validate_preserved_candidates(proposal, _proposal(), assessment, evidence=_case())


def test_physical_constraint_can_expand_only_affected_edge_without_fabricating_model_failure() -> (
    None
):
    evidence = _case()
    evidence.speechCoverage.intervals = [SpeechCoverageInterval(startMs=900, endMs=1200)]
    candidate = _candidate("middle", 1, 2)
    proposal = _proposal(candidate)
    assessment = _assessment(proposal, "unknown")
    item = assessment.candidates[0]
    item.coldReview = _cold(candidate.id)
    item.sourceReview = _source(candidate.id)
    item.physicalBoundaryIssues = list(topic_boundary_issues(evidence, candidate))
    assert [str(issue.edge) for issue in item.physicalBoundaryIssues] == ["opening"]
    assert [str(value.root) for value in item.physicalBoundaryIssues[0].supportingSentenceIds] == [
        "s000000",
        "s000001",
    ]
    validate_assessment(evidence, proposal, assessment)
    assert assessment_has_grounded_failure(assessment)
    revised = candidate.model_copy(update={"firstSentenceId": "s000000"})
    validate_preserved_candidates(proposal, _proposal(revised), assessment, evidence=evidence)
    result = compile_topics(
        evidence, _proposal(revised), evidence_artifact_id=ARTIFACT_ID, evidence_sha256=SHA
    )
    assert len(result.videos) == 1
    for updates in (
        {"firstSentenceId": "s000002"},
        {"lastSentenceId": "s000003"},
        {"title": "Unrelated title change"},
    ):
        with pytest.raises(HarnessValidationError, match="physical-only repair"):
            validate_preserved_candidates(
                proposal,
                _proposal(candidate.model_copy(update=updates)),
                assessment,
                evidence=evidence,
            )


def test_unmeasured_physical_issue_cannot_authorize_a_rewrite() -> None:
    evidence = _case()
    candidate = _candidate("middle", 1, 2)
    proposal = _proposal(candidate)
    measured = evidence.model_copy(deep=True)
    measured.speechCoverage.intervals = [SpeechCoverageInterval(startMs=900, endMs=1200)]
    assessment = _assessment(proposal, "unknown")
    assessment.candidates[0].physicalBoundaryIssues = list(
        topic_boundary_issues(measured, candidate)
    )
    with pytest.raises(HarnessValidationError, match="physical boundary facts differ"):
        validate_assessment(evidence, proposal, assessment)


def test_repair_prompt_requires_the_exact_complete_assessment_set() -> None:
    proposal = _proposal(_candidate("middle", 1, 2))
    with pytest.raises(HarnessValidationError, match="both the previous proposal"):
        plan_prompt(_case(), "", proposal=proposal)
    assessment = _assessment(proposal, "fail")
    payload = _payload(plan_prompt(_case(), "", proposal=proposal, assessment=assessment))
    assert payload["previousProposal"] == proposal.model_dump(mode="json")
    assert payload["independentAssessment"] == assessment.model_dump(mode="json")
    assessment.candidates = []
    with pytest.raises(HarnessValidationError, match="every previous topic"):
        plan_prompt(_case(), "", proposal=proposal, assessment=assessment)


def test_reserved_reviewer_is_excluded_even_when_first_in_the_author_pool() -> None:
    fixture = Path(__file__).parent / "fixtures" / "harness" / "routes.synthetic.json"
    snapshot = RouteSnapshot.model_validate_json(fixture.read_bytes())
    original_author, verifier = editorial_routes(snapshot)
    changed = snapshot.model_copy(
        update={
            "seats": {
                **snapshot.seats,
                "propose": SeatRoutePool(route_ids=(verifier.id, original_author.id)),
            }
        }
    )
    changed = changed.model_copy(update={"snapshot_id": changed.computed_id()})
    changed = RouteSnapshot.model_validate(changed.model_dump())
    author, reserved = editorial_routes(changed)
    assert author.id == original_author.id
    assert reserved.id == verifier.id
    assert author.family != reserved.family
    no_author = changed.model_copy(
        update={"seats": {**changed.seats, "propose": SeatRoutePool(route_ids=(verifier.id,))}}
    )
    with pytest.raises(NoEligibleRoute, match="reserved independent reviewer"):
        editorial_routes(no_author)
