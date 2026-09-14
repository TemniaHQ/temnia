"""Bounded independent source-review ownership, admission and complete assembly."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    TopicCandidate,
    TopicOpportunity,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicSelectionDraft,
    TopicSentenceSpan,
    TopicSourceReviewShard,
)
from temnia_pipeline.harness.source_index import build_topic_source_index, source_index_map
from temnia_pipeline.harness.topic_selection import (
    content_hash,
    make_rubric,
    source_review_shard_prompt,
)
from temnia_pipeline.harness.topic_source_review import (
    MAX_LOCAL_CANDIDATES,
    MAX_LOCAL_OPPORTUNITIES,
    MAX_RELATIONSHIP_PAIRS,
    admit_source_review_shard,
    assemble_source_review_manifest,
    build_source_review_plan,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_source_index import FixtureEncoder, _long_evidence

if TYPE_CHECKING:
    from pydantic import BaseModel

    from temnia_pipeline.contracts import (
        HarnessEvidence,
        TopicSourceIndex,
        TopicSourceReviewWorkItem,
    )


def _reference(value: BaseModel, *, kind: str = "checks") -> HarnessArtifactRef:
    digest = content_hash(value)
    return HarnessArtifactRef(
        id=uuid5(NAMESPACE_URL, f"{kind}:{digest}"),
        fingerprint=digest,
        sha256=digest,
        kind=HarnessArtifactKind(kind),
        sizeBytes=1,
        storageKey=f"tests/{digest}.json",
    )


def _response(identifier: str) -> HarnessArtifactRef:
    digest = identifier.encode().hex().ljust(64, "0")[:64]
    return HarnessArtifactRef(
        id=uuid5(NAMESPACE_URL, identifier),
        fingerprint=digest,
        sha256=digest,
        kind=HarnessArtifactKind.model_response,
        sizeBytes=1,
        storageKey=f"tests/{identifier}.json",
    )


def _selection(evidence: HarnessEvidence, count: int) -> TopicSelectionDraft:
    candidates: list[TopicCandidate] = []
    opportunities: list[TopicOpportunity] = []
    for ordinal in range(count):
        sentence = evidence.sentences[ordinal * 2]
        span = TopicSentenceSpan(
            firstSentenceId=sentence.id,
            lastSentenceId=sentence.id,
        )
        candidate_id = f"author-{ordinal:04d}:candidate:{ordinal:04d}"
        candidates.append(
            TopicCandidate(
                id=candidate_id,
                title=f"Discussion {ordinal}",
                purpose=f"Understand discussion {ordinal}.",
                reason="One exact test sentence.",
                firstSentenceId=sentence.id,
                lastSentenceId=sentence.id,
                requiredContextSpans=[],
                coreSpans=[span],
                completionSpans=[span],
                meaningChangingFollowups=[],
            )
        )
        opportunities.append(
            TopicOpportunity.model_validate(
                {
                    "id": f"opportunity-{ordinal:04d}",
                    "candidateIds": [candidate_id],
                    "coreSpans": [span],
                    "valueEvidenceSpans": [span],
                    "requiredContextSpans": [],
                    "completionSpans": [span],
                    "meaningChangingFollowups": [],
                    "viewerPurpose": f"Understand discussion {ordinal}.",
                    "disposition": "proposed",
                    "dispositionReason": "The discussion has one candidate.",
                }
            )
        )
    return TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=candidates,
            summary="Chronological bounded-review test selection.",
        ),
    )


def _case(count: int = 1) -> tuple[HarnessEvidence, TopicSourceIndex, TopicSelectionDraft]:
    evidence = _long_evidence(max(80, count * 2 + 2))
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    return evidence, index, _selection(evidence, count)


def _review(draft: TopicSelectionDraft, item: TopicSourceReviewWorkItem) -> TopicPortfolioReviewV4:
    candidates = {candidate.id: candidate for candidate in draft.proposal.candidates}
    opportunities = {opportunity.id: opportunity for opportunity in draft.opportunities}
    return TopicPortfolioReviewV4.model_validate(
        {
            "candidates": [],
            "findings": [],
            "missingOpportunities": [],
            "selection": [
                {
                    "candidateId": identifier.root,
                    "disposition": "select",
                    "evidenceSpans": [
                        {
                            "firstSentenceId": candidates[identifier.root].firstSentenceId,
                            "lastSentenceId": candidates[identifier.root].lastSentenceId,
                        }
                    ],
                    "reason": "The candidate is one focused complete discussion.",
                }
                for identifier in item.candidateIds
            ],
            "opportunities": [
                {
                    "opportunityId": identifier.root,
                    "candidateIds": opportunities[identifier.root].candidateIds,
                    "evidenceSpans": opportunities[identifier.root].coreSpans,
                    "reason": "The exact candidate represents the opportunity.",
                    "status": "represented",
                }
                for identifier in item.opportunityIds
            ],
            "overlaps": [
                {
                    **task.model_dump(mode="json"),
                    "classification": "unresolved",
                    "reason": "The fixture retains uncertainty for this overlap.",
                }
                for task in item.overlaps
            ],
            "handoffs": [
                {
                    **task.model_dump(mode="json"),
                    "classification": "clean_handoff",
                    "recommendedLeftLastSentenceId": None,
                    "recommendedRightFirstSentenceId": None,
                    "reason": "The discussions have separate exact sentence extents.",
                }
                for task in item.handoffs
            ],
            "summary": f"Complete review of {item.workItemId}.",
        }
    )


def test_four_hour_review_plan_and_prompts_have_finite_owned_shapes() -> None:
    evidence = _long_evidence(2_400)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    draft = _selection(evidence, 1_200)
    plan = build_source_review_plan(
        evidence,
        index,
        draft,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
    )

    assert evidence.durationMs == 4 * 60 * 60 * 1_000
    assert all(len(item.candidateIds) <= MAX_LOCAL_CANDIDATES for item in plan.workItems)
    assert all(len(item.opportunityIds) <= MAX_LOCAL_OPPORTUNITIES for item in plan.workItems)
    assert all(
        len(item.overlaps) <= MAX_RELATIONSHIP_PAIRS
        and len(item.handoffs) <= MAX_RELATIONSHIP_PAIRS
        for item in plan.workItems
    )
    omission_items = [item for item in plan.workItems if item.discoverMissingOpportunities]
    assert Counter(item.kind.value for item in plan.workItems) == {
        "local": 403,
        "omission": 75,
        "handoff": 600,
    }
    assert len(omission_items) == 75
    assert all(item.kind.value == "omission" and item.sourceSpan for item in omission_items)
    assert all(
        len(item.inspectionCandidateIds) <= 16 and len(item.contextOpportunityIds) <= 48
        for item in omission_items
    )
    rubric = make_rubric("A technical audience.")
    source_map = source_index_map(index, index_sha256="c" * 64)
    prompt = source_review_shard_prompt(
        draft,
        rubric,
        source_map,
        plan.workItems[0],
    )
    payload = json.loads(prompt.split("SOURCE DATA\n", 1)[1])
    assert len(prompt.encode()) < 100_000
    assert len(payload["contextCandidatesWithoutAuthorRationale"]) <= 4
    assert len(payload["contextOpportunitiesWithoutAuthorRationale"]) <= 48
    assert "Source discussion sentence 1000" not in prompt

    omission_prompt = source_review_shard_prompt(
        draft,
        rubric,
        source_map,
        omission_items[0],
    )
    omission_payload = json.loads(omission_prompt.split("SOURCE DATA\n", 1)[1])
    assert omission_payload["workItem"]["sourceSpan"] == {
        "firstSentenceId": "long-sentence-0000",
        "lastSentenceId": "long-sentence-0031",
    }
    assert len(omission_payload["contextCandidatesWithoutAuthorRationale"]) == 16
    prompt_sizes = [
        len(
            source_review_shard_prompt(
                draft,
                rubric,
                source_map,
                item,
            ).encode()
        )
        for item in plan.workItems
    ]
    assert max(prompt_sizes) == 20_242
    assert sum(prompt_sizes) // len(prompt_sizes) == 9_605


def test_every_candidate_requires_one_internal_structure_decision() -> None:
    evidence, index, draft = _case()
    plan = build_source_review_plan(
        evidence,
        index,
        draft,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
    )
    candidate_item = next(item for item in plan.workItems if item.candidateIds)
    omitted = _review(draft, candidate_item).model_copy(update={"selection": []})

    with pytest.raises(HarnessValidationError, match="candidate decision assignment"):
        admit_source_review_shard(
            evidence,
            index,
            draft,
            plan,
            plan_sha256="e" * 64,
            work_item_id=candidate_item.workItemId,
            review=omitted,
            reviewer_family="reviewer",
            response_artifact=_response("omitted"),
            inspection_artifact=None,
        )


def test_opportunity_judgment_cannot_name_an_uninspected_candidate() -> None:
    evidence, index, draft = _case(count=20)
    plan = build_source_review_plan(
        evidence,
        index,
        draft,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
    )
    opportunity_item = next(
        item
        for item in plan.workItems
        if item.opportunityIds
        and len(item.inspectionCandidateIds) < len(draft.proposal.candidates)
    )
    review_data = _review(draft, opportunity_item).model_dump(mode="json")
    permitted = {identifier.root for identifier in opportunity_item.inspectionCandidateIds}
    outside = next(
        candidate.id for candidate in draft.proposal.candidates if candidate.id not in permitted
    )
    review_data["opportunities"][0]["candidateIds"] = [outside]
    review = TopicPortfolioReviewV4.model_validate(review_data)

    with pytest.raises(HarnessValidationError, match="candidate inspection scope"):
        admit_source_review_shard(
            evidence,
            index,
            draft,
            plan,
            plan_sha256="e" * 64,
            work_item_id=opportunity_item.workItemId,
            review=review,
            reviewer_family="reviewer",
            response_artifact=_response("uninspected-candidate"),
            inspection_artifact=None,
        )


def test_complete_manifest_replays_every_exact_shard_and_refuses_partial_input() -> None:
    evidence, index, draft = _case()
    plan = build_source_review_plan(
        evidence,
        index,
        draft,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
    )
    shards: list[TopicSourceReviewShard] = []
    references: list[HarnessArtifactRef] = []
    for item in plan.workItems:
        shard = admit_source_review_shard(
            evidence,
            index,
            draft,
            plan,
            plan_sha256="e" * 64,
            work_item_id=item.workItemId,
            review=_review(draft, item),
            reviewer_family="reviewer",
            response_artifact=_response(item.workItemId),
            inspection_artifact=None,
        )
        shards.append(shard)
        references.append(_reference(shard))

    manifest = assemble_source_review_manifest(
        evidence,
        index,
        draft,
        plan,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        plan_sha256="e" * 64,
        shards=shards,
        shard_artifacts=references,
    )

    assert manifest.complete
    assert [item.candidateId for item in manifest.review.selection] == [
        draft.proposal.candidates[0].id
    ]
    assert [item.opportunityId for item in manifest.review.opportunities] == [
        draft.opportunities[0].id
    ]
    with pytest.raises(HarnessValidationError, match="requires every planned shard"):
        assemble_source_review_manifest(
            evidence,
            index,
            draft,
            plan,
            index_sha256="c" * 64,
            selection_sha256="d" * 64,
            plan_sha256="e" * 64,
            shards=shards[:-1],
            shard_artifacts=references[:-1],
        )
