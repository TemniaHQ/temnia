"""Deterministic bounded work ownership and assembly for author packaging."""

# Refusal text is part of the immutable admission boundary.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    TopicAuthorPackagingManifest,
    TopicAuthorPackagingPlan,
    TopicAuthorPackagingShard,
    TopicAuthorWorkItem,
    TopicCandidate,
    TopicOpportunity,
    TopicProposal,
    TopicSelectionDraft,
)
from temnia_pipeline.harness.topic_selection import (
    content_hash,
    validate_selection,
    validate_selection_against_inventory,
)
from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.contracts import TopicOpportunityInventoryPlan


AUTHOR_PLAN_FORMAT = "topic-author-packaging-plan/1"
AUTHOR_SHARD_FORMAT = "topic-author-packaging-shard/1"
AUTHOR_MANIFEST_FORMAT = "topic-author-packaging-manifest/1"
MAX_AUTHOR_OPPORTUNITIES = 12


def _root(value: object) -> str:
    return str(getattr(value, "root", value))


def _kind(value: object) -> str:
    return str(getattr(value, "value", value))


def build_author_plan(
    inventory_plan: TopicOpportunityInventoryPlan,
    inventory: TopicSelectionDraft,
    *,
    index_sha256: str,
    inventory_sha256: str,
) -> TopicAuthorPackagingPlan:
    """Partition each section's ordered opportunities into finite author work items."""
    if inventory_plan.indexSha256 != index_sha256:
        raise HarnessValidationError("author plan inventory belongs to another source index")
    section_ids = [section.sectionId for section in inventory_plan.sections]
    by_section: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for opportunity in inventory.opportunities:
        if opportunity.id in seen:
            raise HarnessValidationError("author plan inventory contains duplicate opportunity IDs")
        seen.add(opportunity.id)
        matches = [
            section_id for section_id in section_ids if opportunity.id.startswith(f"{section_id}:")
        ]
        if len(matches) != 1:
            message = f"author plan cannot resolve section ownership for {opportunity.id}"
            raise HarnessValidationError(message)
        by_section[matches[0]].append(opportunity.id)
    work_items: list[TopicAuthorWorkItem] = []
    for section in inventory_plan.sections:
        identifiers = by_section[section.sectionId]
        for batch_ordinal, start in enumerate(range(0, len(identifiers), MAX_AUTHOR_OPPORTUNITIES)):
            work_items.append(
                TopicAuthorWorkItem.model_validate(
                    {
                        "workItemId": (f"{section.sectionId}:author-{batch_ordinal + 1:04d}"),
                        "ordinal": len(work_items),
                        "sectionId": section.sectionId,
                        "batchOrdinal": batch_ordinal,
                        "opportunityIds": identifiers[start : start + MAX_AUTHOR_OPPORTUNITIES],
                    }
                )
            )
    return TopicAuthorPackagingPlan(
        format=AUTHOR_PLAN_FORMAT,
        indexSha256=index_sha256,
        inventorySha256=inventory_sha256,
        maxOpportunitiesPerWorkItem=MAX_AUTHOR_OPPORTUNITIES,
        workItems=work_items,
    )


def author_work_item(plan: TopicAuthorPackagingPlan, work_item_id: str) -> TopicAuthorWorkItem:
    """Resolve one exact work item and refuse an absent or duplicated identity."""
    found = [item for item in plan.workItems if item.workItemId == work_item_id]
    if len(found) != 1:
        raise HarnessValidationError("author work item is absent or duplicated in its plan")
    return found[0]


def inventory_for_work_item(
    inventory: TopicSelectionDraft, work_item: TopicAuthorWorkItem
) -> TopicSelectionDraft:
    """Project the immutable inventory to one ordered author assignment."""
    by_id = {opportunity.id: opportunity for opportunity in inventory.opportunities}
    try:
        opportunities = [by_id[_root(identifier)] for identifier in work_item.opportunityIds]
    except KeyError as error:
        raise HarnessValidationError("author work item names an unavailable opportunity") from error
    return TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=[],
            summary=f"Unpackaged inventory for {work_item.workItemId}.",
        ),
    )


def admit_author_shard(  # noqa: PLR0913
    evidence: HarnessEvidence,
    inventory: TopicSelectionDraft,
    plan: TopicAuthorPackagingPlan,
    *,
    plan_sha256: str,
    work_item_id: str,
    draft: TopicSelectionDraft,
    generator_family: str,
) -> TopicAuthorPackagingShard:
    """Admit only a complete packaging decision for one assigned opportunity batch."""
    if not generator_family.strip():
        raise HarnessValidationError("author shard requires its generator family")
    validate_selection(evidence, draft)
    work_item = author_work_item(plan, work_item_id)
    target = inventory_for_work_item(inventory, work_item)
    expected_ids = [_root(value) for value in work_item.opportunityIds]
    actual_ids = [item.id for item in draft.opportunities]
    if actual_ids != expected_ids:
        raise HarnessValidationError(
            "author shard must decide every assigned opportunity exactly once in order"
        )
    validate_selection_against_inventory(target, draft)
    candidate_ids = [candidate.id for candidate in draft.proposal.candidates]
    prefix = f"{work_item.workItemId}:candidate:"
    if any(not identifier.startswith(prefix) for identifier in candidate_ids):
        raise HarnessValidationError("author shard candidate lacks its work-item ownership prefix")
    referenced = {
        _root(identifier)
        for opportunity in draft.opportunities
        for identifier in opportunity.candidateIds
    }
    if referenced != set(candidate_ids):
        raise HarnessValidationError(
            "author shard candidates and assigned opportunity mappings differ"
        )
    return TopicAuthorPackagingShard(
        format=AUTHOR_SHARD_FORMAT,
        indexSha256=plan.indexSha256,
        inventorySha256=plan.inventorySha256,
        planSha256=plan_sha256,
        workItem=work_item,
        generatorFamily=generator_family,
        draft=draft,
    )


def assemble_author_manifest(  # noqa: PLR0913
    evidence: HarnessEvidence,
    inventory: TopicSelectionDraft,
    plan: TopicAuthorPackagingPlan,
    *,
    index_sha256: str,
    inventory_sha256: str,
    plan_sha256: str,
    shards: Sequence[TopicAuthorPackagingShard],
    shard_artifacts: Sequence[HarnessArtifactRef],
) -> TopicAuthorPackagingManifest:
    """Publish a whole selection only from the exact complete ordered author shard set."""
    if plan.indexSha256 != index_sha256 or plan.inventorySha256 != inventory_sha256:
        raise HarnessValidationError("author plan belongs to another index or inventory")
    if len(shards) != len(plan.workItems) or len(shard_artifacts) != len(shards):
        raise HarnessValidationError("author manifest requires one artifact per planned work item")
    candidates: list[TopicCandidate] = []
    opportunities: list[TopicOpportunity] = []
    families: list[str] = []
    for expected, shard, reference in zip(plan.workItems, shards, shard_artifacts, strict=True):
        if (
            shard.indexSha256 != index_sha256
            or shard.inventorySha256 != inventory_sha256
            or shard.planSha256 != plan_sha256
            or shard.workItem != expected
            or _kind(reference.kind) != "proposal"
            or reference.sha256 != content_hash(shard)
        ):
            raise HarnessValidationError("author shard identity or order differs from its plan")
        admitted = admit_author_shard(
            evidence,
            inventory,
            plan,
            plan_sha256=plan_sha256,
            work_item_id=expected.workItemId,
            draft=shard.draft,
            generator_family=shard.generatorFamily,
        )
        if admitted != shard:
            raise HarnessValidationError("author shard content changed after admission")
        candidates.extend(shard.draft.proposal.candidates)
        opportunities.extend(shard.draft.opportunities)
        if shard.generatorFamily not in families:
            families.append(shard.generatorFamily)
    if len({candidate.id for candidate in candidates}) != len(candidates):
        raise HarnessValidationError("author manifest contains duplicate candidate IDs")
    selection = TopicSelectionDraft(
        opportunities=opportunities,
        proposal=TopicProposal(
            version=1,
            candidates=candidates,
            summary=(f"Author packaging assembled from {len(shards)} complete bounded work items."),
        ),
    )
    validate_selection(evidence, selection)
    validate_selection_against_inventory(inventory, selection)
    if [item.id for item in selection.opportunities] != [
        item.id for item in inventory.opportunities
    ]:
        raise HarnessValidationError("author manifest changed inventory opportunity order")
    return TopicAuthorPackagingManifest.model_validate(
        {
            "format": AUTHOR_MANIFEST_FORMAT,
            "complete": True,
            "indexSha256": index_sha256,
            "inventorySha256": inventory_sha256,
            "planSha256": plan_sha256,
            "workItemIds": [item.workItemId for item in plan.workItems],
            "shardArtifacts": [item.model_dump(mode="json") for item in shard_artifacts],
            "generatorFamilies": families,
            "selection": selection.model_dump(mode="json"),
        }
    )
