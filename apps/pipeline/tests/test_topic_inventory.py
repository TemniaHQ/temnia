"""Bounded section inventory ownership and deterministic whole-source assembly."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    TopicOpportunity,
    TopicProposal,
    TopicSelectionDraft,
    TopicSentenceSpan,
    TopicSourceIndex,
)
from temnia_pipeline.harness.source_index import build_topic_source_index
from temnia_pipeline.harness.topic_inventory import (
    admit_inventory_shard,
    assemble_inventory_manifest,
    build_inventory_plan,
)
from temnia_pipeline.harness.topic_selection import content_hash
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_source_index import FixtureEncoder, _long_evidence

if TYPE_CHECKING:
    from pydantic import BaseModel


def _draft(
    section_id: str, first_sentence_id: str, last_sentence_id: str | None = None
) -> TopicSelectionDraft:
    span = TopicSentenceSpan(
        firstSentenceId=first_sentence_id,
        lastSentenceId=last_sentence_id or first_sentence_id,
    )
    return TopicSelectionDraft(
        opportunities=[
            TopicOpportunity.model_validate(
                {
                    "id": f"{section_id}:topic",
                    "candidateIds": [],
                    "coreSpans": [span],
                    "completionSpans": [span],
                    "requiredContextSpans": [],
                    "meaningChangingFollowups": [],
                    "valueEvidenceSpans": [span],
                    "viewerPurpose": f"Understand the discussion owned by {section_id}.",
                    "disposition": "needs_evidence",
                    "dispositionReason": "Packaging follows the independent inventory.",
                }
            )
        ],
        proposal=TopicProposal(
            version=1,
            candidates=[],
            summary="This section records opportunities before packaging.",
        ),
    )


def _reference(value: BaseModel) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=uuid4(),
        kind=HarnessArtifactKind.proposal,
        fingerprint="d" * 64,
        sha256=content_hash(value),
        sizeBytes=1,
        storageKey=f"inventory/{uuid4()}.json",
    )


def _index(sentence_count: int = 600) -> tuple[HarnessEvidence, TopicSourceIndex]:
    evidence = _long_evidence(sentence_count)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    return evidence, index


def test_four_hour_inventory_plan_is_one_ordered_unit_per_section() -> None:
    _, index = _index(2_400)
    plan = build_inventory_plan(index, index_sha256="c" * 64)

    assert len(plan.sections) == 10
    assert [section.sectionId for section in plan.sections] == [
        f"section-{ordinal:04d}" for ordinal in range(1, 11)
    ]
    assert plan.sections[0].previousSectionId is None
    assert plan.sections[0].nextSectionId == "section-0002"
    assert plan.sections[-1].previousSectionId == "section-0009"
    assert plan.sections[-1].nextSectionId is None
    assert (
        max(
            next(node.sentenceCount for node in index.nodes if node.id == section.sectionId)
            for section in plan.sections
        )
        <= 256
    )


def test_shard_owns_by_earliest_core_but_may_follow_completion_across_edge() -> None:
    evidence, index = _index()
    plan = build_inventory_plan(index, index_sha256="c" * 64)
    left = plan.sections[0]
    right = plan.sections[1]
    draft = _draft(
        left.sectionId, left.ownershipSpan.lastSentenceId, right.ownershipSpan.firstSentenceId
    )

    shard = admit_inventory_shard(
        evidence,
        plan,
        plan_sha256="e" * 64,
        section_id=left.sectionId,
        inventory=draft,
    )

    assert shard.section == left
    assert shard.inventory == draft


@pytest.mark.parametrize("failure", ["prefix", "anchor"])
def test_shard_refuses_ambiguous_or_wrong_ownership(failure: str) -> None:
    evidence, index = _index()
    plan = build_inventory_plan(index, index_sha256="c" * 64)
    left, right = plan.sections[:2]
    draft = _draft(
        "wrong" if failure == "prefix" else left.sectionId,
        left.ownershipSpan.firstSentenceId
        if failure == "prefix"
        else right.ownershipSpan.firstSentenceId,
    )

    with pytest.raises(HarnessValidationError, match=r"prefix|anchored outside"):
        admit_inventory_shard(
            evidence,
            plan,
            plan_sha256="e" * 64,
            section_id=left.sectionId,
            inventory=draft,
        )


def test_manifest_requires_every_exact_shard_in_plan_order() -> None:
    evidence, index = _index()
    plan = build_inventory_plan(index, index_sha256="c" * 64)
    shards = [
        admit_inventory_shard(
            evidence,
            plan,
            plan_sha256="e" * 64,
            section_id=section.sectionId,
            inventory=_draft(section.sectionId, section.ownershipSpan.firstSentenceId),
        )
        for section in plan.sections
    ]
    references = [_reference(shard) for shard in shards]

    manifest = assemble_inventory_manifest(
        evidence,
        plan,
        index_sha256="c" * 64,
        plan_sha256="e" * 64,
        shards=shards,
        shard_artifacts=references,
    )

    assert manifest.complete is True
    assert len(manifest.inventory.opportunities) == len(plan.sections)
    assert [section_id.root for section_id in manifest.sectionIds] == [
        section.sectionId for section in plan.sections
    ]
    assert manifest.shardArtifacts == references

    with pytest.raises(HarnessValidationError, match="one artifact per planned section"):
        assemble_inventory_manifest(
            evidence,
            plan,
            index_sha256="c" * 64,
            plan_sha256="e" * 64,
            shards=shards[:-1],
            shard_artifacts=references[:-1],
        )
    with pytest.raises(HarnessValidationError, match="identity or order"):
        assemble_inventory_manifest(
            evidence,
            plan,
            index_sha256="c" * 64,
            plan_sha256="e" * 64,
            shards=[shards[1], shards[0], *shards[2:]],
            shard_artifacts=[references[1], references[0], *references[2:]],
        )
