"""Bounded author work ownership and complete deterministic assembly."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    TopicAuthorPackagingShard,
    TopicCandidate,
    TopicOpportunity,
    TopicProposal,
    TopicSelectionDraft,
    TopicSentenceSpan,
    TopicSourceIndex,
)
from temnia_pipeline.harness.source_index import build_topic_source_index, source_index_map
from temnia_pipeline.harness.topic_author_packaging import (
    MAX_AUTHOR_OPPORTUNITIES,
    admit_author_shard,
    assemble_author_manifest,
    build_author_plan,
    inventory_for_work_item,
)
from temnia_pipeline.harness.topic_inventory import build_inventory_plan
from temnia_pipeline.harness.topic_selection import (
    author_packaging_shard_prompt,
    content_hash,
    make_rubric,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_source_index import FixtureEncoder, _long_evidence

if TYPE_CHECKING:
    from pydantic import BaseModel


def _index(sentence_count: int = 600) -> tuple[HarnessEvidence, TopicSourceIndex]:
    evidence = _long_evidence(sentence_count)
    return evidence, build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )


def _inventory(evidence: HarnessEvidence, section_id: str, count: int) -> TopicSelectionDraft:
    opportunities: list[TopicOpportunity] = []
    for ordinal in range(count):
        sentence = evidence.sentences[ordinal]
        span = TopicSentenceSpan(
            firstSentenceId=sentence.id,
            lastSentenceId=sentence.id,
        )
        opportunities.append(
            TopicOpportunity.model_validate(
                {
                    "id": f"{section_id}:opportunity-{ordinal + 1:04d}",
                    "candidateIds": [],
                    "completionSpans": [span],
                    "coreSpans": [span],
                    "disposition": "needs_evidence",
                    "dispositionReason": "Packaging is unresolved.",
                    "meaningChangingFollowups": [],
                    "requiredContextSpans": [],
                    "valueEvidenceSpans": [span],
                    "viewerPurpose": f"Understand discussion {ordinal + 1}.",
                }
            )
        )
    return TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=[],
            summary="Independent opportunity inventory before author packaging.",
        ),
    )


def _packaged(
    assignment: TopicSelectionDraft, work_item_id: str
) -> TopicSelectionDraft:
    candidates: list[TopicCandidate] = []
    opportunities: list[TopicOpportunity] = []
    for ordinal, opportunity in enumerate(assignment.opportunities):
        span = opportunity.coreSpans[0]
        candidate_id = f"{work_item_id}:candidate:{ordinal + 1:04d}"
        candidates.append(
            TopicCandidate(
                id=candidate_id,
                title=f"Discussion {ordinal + 1}",
                purpose=opportunity.viewerPurpose,
                firstSentenceId=span.firstSentenceId,
                lastSentenceId=span.lastSentenceId,
                requiredContextSpans=[],
                coreSpans=[span],
                completionSpans=[span],
                meaningChangingFollowups=[],
                reason="This exact sentence carries the test discussion.",
            )
        )
        opportunities.append(
            TopicOpportunity.model_validate(
                {
                    **opportunity.model_dump(mode="json"),
                    "candidateIds": [candidate_id],
                    "disposition": "proposed",
                    "dispositionReason": "The assigned discussion is packaged.",
                }
            )
        )
    return TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=candidates,
            summary=f"Complete bounded packaging for {work_item_id}.",
        ),
    )


def _reference(value: BaseModel) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=uuid4(),
        kind=HarnessArtifactKind.proposal,
        fingerprint="d" * 64,
        sha256=content_hash(value),
        sizeBytes=1,
        storageKey=f"author/{uuid4()}.json",
    )


def test_author_plan_chunks_a_busy_section_without_losing_order() -> None:
    evidence, index = _index()
    inventory_plan = build_inventory_plan(index, index_sha256="c" * 64)
    section = inventory_plan.sections[0]
    inventory = _inventory(evidence, section.sectionId, 25)

    plan = build_author_plan(
        inventory_plan,
        inventory,
        index_sha256="c" * 64,
        inventory_sha256="e" * 64,
    )

    assert [len(item.opportunityIds) for item in plan.workItems] == [12, 12, 1]
    assert plan.maxOpportunitiesPerWorkItem == MAX_AUTHOR_OPPORTUNITIES
    assert [item.ordinal for item in plan.workItems] == [0, 1, 2]
    assert [item.batchOrdinal for item in plan.workItems] == [0, 1, 2]
    assert [value.root for item in plan.workItems for value in item.opportunityIds] == [
        item.id for item in inventory.opportunities
    ]


def test_four_hour_author_request_contains_only_one_bounded_assignment() -> None:
    evidence, index = _index(2_400)
    inventory_plan = build_inventory_plan(index, index_sha256="c" * 64)
    positions = {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}
    opportunities: list[TopicOpportunity] = []
    for section in inventory_plan.sections:
        first = positions[section.ownershipSpan.firstSentenceId]
        last = positions[section.ownershipSpan.lastSentenceId]
        for ordinal in range(first, last + 1):
            sentence = evidence.sentences[ordinal]
            span = TopicSentenceSpan(
                firstSentenceId=sentence.id,
                lastSentenceId=sentence.id,
            )
            opportunities.append(
                TopicOpportunity.model_validate(
                    {
                        "id": f"{section.sectionId}:opportunity-{ordinal + 1:04d}",
                        "candidateIds": [],
                        "completionSpans": [span],
                        "coreSpans": [span],
                        "disposition": "needs_evidence",
                        "dispositionReason": "Packaging is unresolved.",
                        "meaningChangingFollowups": [],
                        "requiredContextSpans": [],
                        "valueEvidenceSpans": [span],
                        "viewerPurpose": f"Understand discussion {ordinal + 1}.",
                    }
                )
            )
    inventory = TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=[],
            summary="Pathological one-opportunity-per-sentence scale fixture.",
        ),
    )
    plan = build_author_plan(
        inventory_plan,
        inventory,
        index_sha256="c" * 64,
        inventory_sha256="e" * 64,
    )
    assignment = inventory_for_work_item(inventory, plan.workItems[0])
    prompt = author_packaging_shard_prompt(
        source_index_map(index, index_sha256="c" * 64),
        make_rubric("Find worthwhile discussions."),
        plan.workItems[0],
        assignment,
    )

    assert len(plan.workItems) == 206
    assert max(len(item.opportunityIds) for item in plan.workItems) == 12
    assert len(assignment.opportunities) == 12
    assert opportunities[-1].id not in prompt
    assert "Source discussion sentence 2399" not in prompt
    assert len(prompt.encode()) < 30_000


def test_author_shard_refuses_foreign_candidate_or_opportunity() -> None:
    evidence, index = _index()
    inventory_plan = build_inventory_plan(index, index_sha256="c" * 64)
    inventory = _inventory(evidence, inventory_plan.sections[0].sectionId, 2)
    plan = build_author_plan(
        inventory_plan,
        inventory,
        index_sha256="c" * 64,
        inventory_sha256="e" * 64,
    )
    item = plan.workItems[0]
    assignment = inventory_for_work_item(inventory, item)
    valid = _packaged(assignment, item.workItemId)

    foreign_candidate = valid.model_copy(deep=True)
    foreign_candidate.proposal.candidates[0].id = "foreign:candidate"
    foreign_candidate.opportunities[0].candidateIds[0] = "foreign:candidate"
    with pytest.raises(HarnessValidationError, match="ownership prefix"):
        admit_author_shard(
            evidence,
            inventory,
            plan,
            plan_sha256="f" * 64,
            work_item_id=item.workItemId,
            draft=foreign_candidate,
            generator_family="author-family",
        )

    missing = TopicSelectionDraft(
        opportunities=valid.opportunities[:-1],
        proposal=TopicProposal(
            version=1,
            candidates=valid.proposal.candidates[:-1],
            summary=valid.proposal.summary,
        ),
    )
    with pytest.raises(HarnessValidationError, match="every assigned opportunity"):
        admit_author_shard(
            evidence,
            inventory,
            plan,
            plan_sha256="f" * 64,
            work_item_id=item.workItemId,
            draft=missing,
            generator_family="author-family",
        )


def test_author_manifest_requires_every_exact_shard_in_plan_order() -> None:
    evidence, index = _index()
    inventory_plan = build_inventory_plan(index, index_sha256="c" * 64)
    inventory = _inventory(evidence, inventory_plan.sections[0].sectionId, 13)
    plan = build_author_plan(
        inventory_plan,
        inventory,
        index_sha256="c" * 64,
        inventory_sha256="e" * 64,
    )
    shards: list[TopicAuthorPackagingShard] = []
    for item in plan.workItems:
        assignment = inventory_for_work_item(inventory, item)
        shards.append(
            admit_author_shard(
                evidence,
                inventory,
                plan,
                plan_sha256="f" * 64,
                work_item_id=item.workItemId,
                draft=_packaged(assignment, item.workItemId),
                generator_family="author-family",
            )
        )
    refs = [_reference(shard) for shard in shards]

    manifest = assemble_author_manifest(
        evidence,
        inventory,
        plan,
        index_sha256="c" * 64,
        inventory_sha256="e" * 64,
        plan_sha256="f" * 64,
        shards=shards,
        shard_artifacts=refs,
    )

    assert manifest.complete is True
    assert [value.root for value in manifest.generatorFamilies] == ["author-family"]
    assert len(manifest.selection.opportunities) == 13
    assert len(manifest.selection.proposal.candidates) == 13
    with pytest.raises(HarnessValidationError, match="one artifact per planned"):
        assemble_author_manifest(
            evidence,
            inventory,
            plan,
            index_sha256="c" * 64,
            inventory_sha256="e" * 64,
            plan_sha256="f" * 64,
            shards=shards[:-1],
            shard_artifacts=refs[:-1],
        )
    with pytest.raises(HarnessValidationError, match="identity or order"):
        assemble_author_manifest(
            evidence,
            inventory,
            plan,
            index_sha256="c" * 64,
            inventory_sha256="e" * 64,
            plan_sha256="f" * 64,
            shards=list(reversed(shards)),
            shard_artifacts=list(reversed(refs)),
        )
