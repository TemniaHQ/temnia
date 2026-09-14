"""Deterministic section ownership and assembly for bounded opportunity inventory."""

# Refusal text is part of the immutable admission boundary.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    SectionId,
    TopicInventorySection,
    TopicOpportunity,
    TopicOpportunityInventoryManifest,
    TopicOpportunityInventoryPlan,
    TopicOpportunityInventoryShard,
    TopicProposal,
    TopicSelectionDraft,
    TopicSentenceSpan,
    TopicSourceIndex,
)
from temnia_pipeline.harness.topic_selection import content_hash, validate_opportunity_inventory
from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence


INVENTORY_PLAN_FORMAT = "topic-opportunity-inventory-plan/1"
INVENTORY_SHARD_FORMAT = "topic-opportunity-inventory-shard/1"
INVENTORY_MANIFEST_FORMAT = "topic-opportunity-inventory-manifest/1"


def _ids(values: Sequence[object]) -> list[str]:
    return [str(getattr(value, "root", value)) for value in values]


def _kind(value: object) -> str:
    return str(getattr(value, "value", value))


def build_inventory_plan(
    index: TopicSourceIndex, *, index_sha256: str
) -> TopicOpportunityInventoryPlan:
    """Turn exact root children into one ordered, neighbour-aware ownership plan."""
    nodes = {node.id: node for node in index.nodes}
    root = nodes.get(index.rootNodeId)
    if root is None or _kind(root.kind) != "episode":
        raise HarnessValidationError("inventory plan requires the source-index episode root")
    section_ids = _ids(root.childIds)
    sections: list[TopicInventorySection] = []
    for ordinal, section_id in enumerate(section_ids):
        node = nodes.get(section_id)
        if (
            node is None
            or _kind(node.kind) != "section"
            or node.ordinal != ordinal
            or str(getattr(node.parentId, "root", node.parentId)) != index.rootNodeId
        ):
            raise HarnessValidationError("inventory plan requires ordered source-index sections")
        sections.append(
            TopicInventorySection(
                sectionId=section_id,
                ordinal=ordinal,
                ownershipSpan=TopicSentenceSpan(
                    firstSentenceId=node.firstSentenceId,
                    lastSentenceId=node.lastSentenceId,
                ),
                previousSectionId=section_ids[ordinal - 1] if ordinal else None,
                nextSectionId=(
                    section_ids[ordinal + 1] if ordinal + 1 < len(section_ids) else None
                ),
            )
        )
    if not sections:
        raise HarnessValidationError("inventory plan requires at least one source-index section")
    return TopicOpportunityInventoryPlan(
        format=INVENTORY_PLAN_FORMAT,
        indexSha256=index_sha256,
        sections=sections,
    )


def inventory_section(
    plan: TopicOpportunityInventoryPlan, section_id: str
) -> TopicInventorySection:
    """Resolve one exact work item and refuse an unplanned section."""
    found = [section for section in plan.sections if section.sectionId == section_id]
    if len(found) != 1:
        raise HarnessValidationError("inventory section is absent or duplicated in its plan")
    return found[0]


def _positions(evidence: HarnessEvidence) -> dict[str, int]:
    return {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}


def _span_positions(
    positions: dict[str, int], span: TopicSentenceSpan, *, label: str
) -> tuple[int, int]:
    first = positions.get(span.firstSentenceId)
    last = positions.get(span.lastSentenceId)
    if first is None or last is None or first > last:
        message = f"{label} is not an ordered evidence span"
        raise HarnessValidationError(message)
    return first, last


def admit_inventory_shard(
    evidence: HarnessEvidence,
    plan: TopicOpportunityInventoryPlan,
    *,
    plan_sha256: str,
    section_id: str,
    inventory: TopicSelectionDraft,
) -> TopicOpportunityInventoryShard:
    """Admit only opportunities anchored by their earliest core in the owned section."""
    validate_opportunity_inventory(evidence, inventory)
    section = inventory_section(plan, section_id)
    positions = _positions(evidence)
    owned_first, owned_last = _span_positions(
        positions, section.ownershipSpan, label="inventory ownership"
    )
    prefix = f"{section.sectionId}:"
    for opportunity in inventory.opportunities:
        if not opportunity.id.startswith(prefix):
            message = f"inventory opportunity {opportunity.id} lacks its {prefix} ownership prefix"
            raise HarnessValidationError(message)
        anchor = min(
            _span_positions(positions, span, label=f"opportunity {opportunity.id} core")[0]
            for span in opportunity.coreSpans
        )
        if not owned_first <= anchor <= owned_last:
            message = (
                f"inventory opportunity {opportunity.id} is anchored outside {section.sectionId}"
            )
            raise HarnessValidationError(message)
    return TopicOpportunityInventoryShard(
        format=INVENTORY_SHARD_FORMAT,
        indexSha256=plan.indexSha256,
        planSha256=plan_sha256,
        section=section,
        inventory=inventory,
    )


def assemble_inventory_manifest(  # noqa: PLR0913
    evidence: HarnessEvidence,
    plan: TopicOpportunityInventoryPlan,
    *,
    index_sha256: str,
    plan_sha256: str,
    shards: Sequence[TopicOpportunityInventoryShard],
    shard_artifacts: Sequence[HarnessArtifactRef],
) -> TopicOpportunityInventoryManifest:
    """Assemble one whole-source inventory only from the exact complete ordered shard set."""
    if plan.indexSha256 != index_sha256:
        raise HarnessValidationError("inventory plan belongs to another source index")
    if len(shards) != len(plan.sections) or len(shard_artifacts) != len(shards):
        raise HarnessValidationError("inventory manifest requires one artifact per planned section")
    opportunities: list[TopicOpportunity] = []
    for expected, shard, reference in zip(plan.sections, shards, shard_artifacts, strict=True):
        if (
            shard.indexSha256 != index_sha256
            or shard.planSha256 != plan_sha256
            or shard.section != expected
            or _kind(reference.kind) != "proposal"
            or reference.sha256 != content_hash(shard)
        ):
            raise HarnessValidationError("inventory shard identity or order differs from its plan")
        # Re-run ownership admission so assembly never trusts a parsed artifact by shape alone.
        admitted = admit_inventory_shard(
            evidence,
            plan,
            plan_sha256=plan_sha256,
            section_id=expected.sectionId,
            inventory=shard.inventory,
        )
        if admitted != shard:
            raise HarnessValidationError("inventory shard content changed after admission")
        opportunities.extend(shard.inventory.opportunities)
    identifiers = [item.id for item in opportunities]
    if len(identifiers) != len(set(identifiers)):
        raise HarnessValidationError("inventory manifest contains duplicate opportunity IDs")
    inventory = TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=[],
            summary=(
                f"Independent opportunity inventory assembled from {len(shards)} complete "
                "source-index sections; packaging remains unresolved."
            ),
        ),
    )
    validate_opportunity_inventory(evidence, inventory)
    return TopicOpportunityInventoryManifest(
        format=INVENTORY_MANIFEST_FORMAT,
        complete=True,
        indexSha256=index_sha256,
        planSha256=plan_sha256,
        sectionIds=[SectionId(root=section.sectionId) for section in plan.sections],
        shardArtifacts=list(shard_artifacts),
        inventory=inventory,
    )
