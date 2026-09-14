"""Deterministic bounded ownership and complete-manifest admission for source review."""

# Refusal text is part of the immutable editorial boundary.
# ruff: noqa: C901, EM101, PLR0912, PLR0913, TRY003

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    TopicCandidateHandoffJudgment,
    TopicCandidateOverlapJudgment,
    TopicOpportunity,
    TopicOpportunityJudgment,
    TopicPortfolioReviewV4,
    TopicSelectionDecision,
    TopicSelectionDraft,
    TopicSelectionFinding,
    TopicSentenceSpan,
    TopicSourceIndex,
    TopicSourceIndexNode,
    TopicSourceReviewHandoffTask,
    TopicSourceReviewManifest,
    TopicSourceReviewOverlapTask,
    TopicSourceReviewPlan,
    TopicSourceReviewShard,
    TopicSourceReviewWorkItem,
)
from temnia_pipeline.harness.topic_selection import (
    CandidateHandoffRow,
    CandidateOverlapRow,
    candidate_handoff_rows,
    candidate_overlap_rows,
    content_hash,
    validate_portfolio_review,
)
from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


SOURCE_REVIEW_PLAN_FORMAT = "topic-source-review-plan/1"
SOURCE_REVIEW_SHARD_FORMAT = "topic-source-review-shard/1"
SOURCE_REVIEW_MANIFEST_FORMAT = "topic-source-review-manifest/1"
MAX_LOCAL_CANDIDATES = 4
MAX_LOCAL_OPPORTUNITIES = 12
MAX_RELATIONSHIP_PAIRS = 2
MAX_INSPECTION_CANDIDATES = 16
MAX_CONTEXT_OPPORTUNITIES = 48


def _root(value: object) -> str:
    return str(getattr(value, "root", value))


def _kind(value: object) -> str:
    return str(getattr(value, "value", value))


def _positions(index: TopicSourceIndex) -> dict[str, int]:
    return {sentence.id: offset for offset, sentence in enumerate(index.sentences)}


def _section_nodes(index: TopicSourceIndex) -> list[TopicSourceIndexNode]:
    return sorted(
        (node for node in index.nodes if _kind(node.kind) == "section"),
        key=lambda node: node.ordinal,
    )


def _region_nodes(index: TopicSourceIndex, section_id: str) -> list[TopicSourceIndexNode]:
    return sorted(
        (
            node
            for node in index.nodes
            if _kind(node.kind) == "region" and node.parentId == section_id
        ),
        key=lambda node: node.ordinal,
    )


def _section_for_sentence(index: TopicSourceIndex, sentence_id: str) -> str:
    positions = _positions(index)
    try:
        target = positions[sentence_id]
    except KeyError as error:
        raise HarnessValidationError("source review ownership names an unknown sentence") from error
    matches = [
        node.id
        for node in _section_nodes(index)
        if positions[node.firstSentenceId] <= target <= positions[node.lastSentenceId]
    ]
    if len(matches) != 1:
        raise HarnessValidationError("source review sentence has no unique section owner")
    return matches[0]


def _chunks(values: Sequence[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _linked_opportunity_ids(draft: TopicSelectionDraft, candidate_ids: Iterable[str]) -> list[str]:
    selected = set(candidate_ids)
    return [
        opportunity.id
        for opportunity in draft.opportunities
        if selected.intersection(_root(value) for value in opportunity.candidateIds)
    ]


def _linked_candidate_ids(draft: TopicSelectionDraft, opportunity_ids: Iterable[str]) -> list[str]:
    selected = set(opportunity_ids)
    candidate_order = [candidate.id for candidate in draft.proposal.candidates]
    referenced = {
        _root(value)
        for opportunity in draft.opportunities
        if opportunity.id in selected
        for value in opportunity.candidateIds
    }
    return [identifier for identifier in candidate_order if identifier in referenced]


def _intersects(
    positions: dict[str, int], first: str, last: str, scan_first: str, scan_last: str
) -> bool:
    return positions[last] >= positions[scan_first] and positions[first] <= positions[scan_last]


def _omission_context(
    index: TopicSourceIndex,
    draft: TopicSelectionDraft,
    region: TopicSourceIndexNode,
) -> tuple[list[str], list[str]]:
    """Return every candidate and opportunity that can represent one exact scan window."""
    positions = _positions(index)
    direct_candidates = {
        candidate.id
        for candidate in draft.proposal.candidates
        if _intersects(
            positions,
            candidate.firstSentenceId,
            candidate.lastSentenceId,
            region.firstSentenceId,
            region.lastSentenceId,
        )
    }
    direct_opportunities = {
        opportunity.id
        for opportunity in draft.opportunities
        if any(
            _intersects(
                positions,
                span.firstSentenceId,
                span.lastSentenceId,
                region.firstSentenceId,
                region.lastSentenceId,
            )
            for span in (
                *opportunity.coreSpans,
                *opportunity.requiredContextSpans,
                *opportunity.completionSpans,
                *opportunity.meaningChangingFollowups,
            )
        )
    }
    opportunity_ids = [
        opportunity.id
        for opportunity in draft.opportunities
        if opportunity.id in direct_opportunities
        or direct_candidates.intersection(_root(value) for value in opportunity.candidateIds)
    ]
    linked_candidates = set(_linked_candidate_ids(draft, opportunity_ids))
    candidate_ids = [
        candidate.id
        for candidate in draft.proposal.candidates
        if candidate.id in direct_candidates or candidate.id in linked_candidates
    ]
    return candidate_ids, opportunity_ids


def _append_work_item(
    work_items: list[TopicSourceReviewWorkItem],
    *,
    section_id: str,
    kind: str,
    batch_ordinal: int,
    candidate_ids: Sequence[str] = (),
    opportunity_ids: Sequence[str] = (),
    context_opportunity_ids: Sequence[str] = (),
    inspection_candidate_ids: Sequence[str] = (),
    overlaps: Sequence[TopicSourceReviewOverlapTask] = (),
    handoffs: Sequence[TopicSourceReviewHandoffTask] = (),
    discover_missing: bool = False,
    source_span: TopicSentenceSpan | None = None,
) -> None:
    suffix = {
        "local": "local",
        "omission": "omission",
        "overlap": "overlap",
        "handoff": "handoff",
    }[kind]
    work_items.append(
        TopicSourceReviewWorkItem.model_validate(
            {
                "workItemId": f"{section_id}:source-{suffix}-{batch_ordinal + 1:04d}",
                "ordinal": len(work_items),
                "sectionId": section_id,
                "batchOrdinal": batch_ordinal,
                "kind": kind,
                "candidateIds": list(candidate_ids),
                "opportunityIds": list(opportunity_ids),
                "contextOpportunityIds": list(context_opportunity_ids),
                "inspectionCandidateIds": list(inspection_candidate_ids),
                "discoverMissingOpportunities": discover_missing,
                "overlaps": [item.model_dump(mode="json") for item in overlaps],
                "handoffs": [item.model_dump(mode="json") for item in handoffs],
                "sourceSpan": (
                    source_span.model_dump(mode="json") if source_span is not None else None
                ),
            }
        )
    )


def build_source_review_plan(
    evidence: HarnessEvidence,
    index: TopicSourceIndex,
    draft: TopicSelectionDraft,
    *,
    index_sha256: str,
    selection_sha256: str,
    paged_context: bool = False,
) -> TopicSourceReviewPlan:
    """Freeze bounded candidate, opportunity, overlap, handoff and omission work."""
    candidate_by_section: dict[str, list[str]] = defaultdict(list)
    for candidate in draft.proposal.candidates:
        candidate_by_section[_section_for_sentence(index, candidate.firstSentenceId)].append(
            candidate.id
        )
    opportunity_by_section: dict[str, list[str]] = defaultdict(list)
    for opportunity in draft.opportunities:
        first_core = opportunity.coreSpans[0].firstSentenceId
        opportunity_by_section[_section_for_sentence(index, first_core)].append(opportunity.id)

    overlap_by_section: dict[str, list[TopicSourceReviewOverlapTask]] = defaultdict(list)
    candidate_map = {candidate.id: candidate for candidate in draft.proposal.candidates}
    overlap_rows = candidate_overlap_rows(evidence, draft)
    handoff_rows = candidate_handoff_rows(evidence, draft)
    for row in overlap_rows:
        pair = [str(value) for value in row["candidateIds"]]
        owner = _section_for_sentence(index, candidate_map[pair[0]].firstSentenceId)
        overlap_by_section[owner].append(TopicSourceReviewOverlapTask.model_validate(row))
    handoff_by_section: dict[str, list[TopicSourceReviewHandoffTask]] = defaultdict(list)
    for row in handoff_rows:
        pair = [str(value) for value in row["candidateIds"]]
        owner = _section_for_sentence(index, candidate_map[pair[0]].firstSentenceId)
        handoff_by_section[owner].append(TopicSourceReviewHandoffTask.model_validate(row))

    work_items: list[TopicSourceReviewWorkItem] = []
    for section in _section_nodes(index):
        local_batch = 0
        candidates = candidate_by_section[section.id]
        for chunk in _chunks(candidates, MAX_LOCAL_CANDIDATES):
            context_ids = _linked_opportunity_ids(draft, chunk)
            if not paged_context and len(context_ids) > MAX_CONTEXT_OPPORTUNITIES:
                raise HarnessValidationError(
                    "one candidate review batch exceeds the bounded opportunity context"
                )
            _append_work_item(
                work_items,
                section_id=section.id,
                kind="local",
                batch_ordinal=local_batch,
                candidate_ids=chunk,
                context_opportunity_ids=context_ids,
                inspection_candidate_ids=chunk,
            )
            local_batch += 1
        opportunities = opportunity_by_section[section.id]
        for chunk in _chunks(opportunities, MAX_LOCAL_OPPORTUNITIES):
            inspection_ids = _linked_candidate_ids(draft, chunk)
            if not paged_context and len(inspection_ids) > MAX_INSPECTION_CANDIDATES:
                raise HarnessValidationError(
                    "one opportunity review batch exceeds the bounded candidate inspection set"
                )
            _append_work_item(
                work_items,
                section_id=section.id,
                kind="local",
                batch_ordinal=local_batch,
                opportunity_ids=chunk,
                context_opportunity_ids=chunk,
                inspection_candidate_ids=inspection_ids,
            )
            local_batch += 1
        for batch, region in enumerate(_region_nodes(index, section.id)):
            inspection_ids, context_ids = _omission_context(index, draft, region)
            if not paged_context and len(inspection_ids) > MAX_INSPECTION_CANDIDATES:
                raise HarnessValidationError(
                    "one omission scan exceeds the bounded candidate inspection set"
                )
            if not paged_context and len(context_ids) > MAX_CONTEXT_OPPORTUNITIES:
                raise HarnessValidationError(
                    "one omission scan exceeds the bounded opportunity context"
                )
            _append_work_item(
                work_items,
                section_id=section.id,
                kind="omission",
                batch_ordinal=batch,
                context_opportunity_ids=context_ids,
                inspection_candidate_ids=inspection_ids,
                discover_missing=True,
                source_span=TopicSentenceSpan(
                    firstSentenceId=region.firstSentenceId,
                    lastSentenceId=region.lastSentenceId,
                ),
            )

        for batch, start in enumerate(
            range(0, len(overlap_by_section[section.id]), MAX_RELATIONSHIP_PAIRS)
        ):
            overlap_tasks = overlap_by_section[section.id][start : start + MAX_RELATIONSHIP_PAIRS]
            inspection_ids = list(
                dict.fromkeys(
                    _root(identifier) for task in overlap_tasks for identifier in task.candidateIds
                )
            )
            context_ids = _linked_opportunity_ids(draft, inspection_ids)
            if not paged_context and len(context_ids) > MAX_CONTEXT_OPPORTUNITIES:
                raise HarnessValidationError(
                    "one overlap batch exceeds the bounded opportunity context"
                )
            _append_work_item(
                work_items,
                section_id=section.id,
                kind="overlap",
                batch_ordinal=batch,
                context_opportunity_ids=context_ids,
                inspection_candidate_ids=inspection_ids,
                overlaps=overlap_tasks,
            )
        for batch, start in enumerate(
            range(0, len(handoff_by_section[section.id]), MAX_RELATIONSHIP_PAIRS)
        ):
            handoff_tasks = handoff_by_section[section.id][start : start + MAX_RELATIONSHIP_PAIRS]
            inspection_ids = list(
                dict.fromkeys(
                    _root(identifier) for task in handoff_tasks for identifier in task.candidateIds
                )
            )
            context_ids = _linked_opportunity_ids(draft, inspection_ids)
            if not paged_context and len(context_ids) > MAX_CONTEXT_OPPORTUNITIES:
                raise HarnessValidationError(
                    "one handoff batch exceeds the bounded opportunity context"
                )
            _append_work_item(
                work_items,
                section_id=section.id,
                kind="handoff",
                batch_ordinal=batch,
                context_opportunity_ids=context_ids,
                inspection_candidate_ids=inspection_ids,
                handoffs=handoff_tasks,
            )
    return TopicSourceReviewPlan(
        format=SOURCE_REVIEW_PLAN_FORMAT,
        indexSha256=index_sha256,
        selectionSha256=selection_sha256,
        maxCandidatesPerLocalWorkItem=MAX_LOCAL_CANDIDATES,
        maxOpportunitiesPerLocalWorkItem=MAX_LOCAL_OPPORTUNITIES,
        maxPairsPerRelationshipWorkItem=MAX_RELATIONSHIP_PAIRS,
        workItems=work_items,
    )


def source_review_work_item(
    plan: TopicSourceReviewPlan, work_item_id: str
) -> TopicSourceReviewWorkItem:
    """Resolve one exact source-review work item from its immutable plan."""
    found = [item for item in plan.workItems if item.workItemId == work_item_id]
    if len(found) != 1:
        raise HarnessValidationError("source review work item is absent or duplicated")
    return found[0]


def _padded_review(
    draft: TopicSelectionDraft,
    review: TopicPortfolioReviewV4,
    work_item: TopicSourceReviewWorkItem,
    *,
    overlap_rows: Sequence[CandidateOverlapRow],
    handoff_rows: Sequence[CandidateHandoffRow],
) -> TopicPortfolioReviewV4:
    decision_map = {decision.candidateId: decision for decision in review.selection}
    represented = {
        _root(candidate_id)
        for judgment in review.opportunities
        if _kind(judgment.status) == "represented"
        for candidate_id in judgment.candidateIds
    }
    decisions: list[TopicSelectionDecision] = []
    for candidate in draft.proposal.candidates:
        decisions.append(  # noqa: PERF401 - fallback construction is clearer
            decision_map.get(candidate.id)
            or TopicSelectionDecision.model_validate(
                {
                    "candidateId": candidate.id,
                    "disposition": "select" if candidate.id in represented else "unresolved",
                    "evidenceSpans": [
                        {
                            "firstSentenceId": candidate.firstSentenceId,
                            "lastSentenceId": candidate.lastSentenceId,
                        }
                    ],
                    "reason": "Synthetic admission context for a decision owned by another shard.",
                }
            )
        )
    judgment_map = {judgment.opportunityId: judgment for judgment in review.opportunities}
    judgments = [
        judgment_map.get(opportunity.id)
        or TopicOpportunityJudgment.model_validate(
            {
                "opportunityId": opportunity.id,
                "candidateIds": [],
                "evidenceSpans": opportunity.coreSpans,
                "reason": "Synthetic admission context for a judgment owned by another shard.",
                "status": "unresolved",
            }
        )
        for opportunity in draft.opportunities
    ]
    overlap_map = {
        (_root(judgment.candidateIds[0]), _root(judgment.candidateIds[1])): judgment
        for judgment in review.overlaps
    }
    overlaps = [
        overlap_map.get((row["candidateIds"][0], row["candidateIds"][1]))
        or TopicCandidateOverlapJudgment.model_validate(
            {
                **row,
                "classification": "unresolved",
                "reason": "Synthetic admission context for a relationship owned elsewhere.",
            }
        )
        for row in overlap_rows
    ]
    handoff_map = {
        (_root(judgment.candidateIds[0]), _root(judgment.candidateIds[1])): judgment
        for judgment in review.handoffs
    }
    handoffs = [
        handoff_map.get((row["candidateIds"][0], row["candidateIds"][1]))
        or TopicCandidateHandoffJudgment.model_validate(
            {
                **row,
                "classification": "unresolved",
                "recommendedLeftLastSentenceId": None,
                "recommendedRightFirstSentenceId": None,
                "reason": "Synthetic admission context for a relationship owned elsewhere.",
            }
        )
        for row in handoff_rows
    ]
    return TopicPortfolioReviewV4(
        candidates=[],
        findings=review.findings,
        missingOpportunities=review.missingOpportunities,
        opportunities=judgments,
        selection=decisions,
        summary=f"Admission projection for {work_item.workItemId}.",
        overlaps=overlaps,
        handoffs=handoffs,
    )


def admit_source_review_shard(
    evidence: HarnessEvidence,
    index: TopicSourceIndex,
    draft: TopicSelectionDraft,
    plan: TopicSourceReviewPlan,
    *,
    plan_sha256: str,
    work_item_id: str,
    review: TopicPortfolioReviewV4,
    reviewer_family: str,
    response_artifact: HarnessArtifactRef,
    inspection_artifact: HarnessArtifactRef | None,
) -> TopicSourceReviewShard:
    """Admit only the exact judgments owned by one bounded review work item."""
    if not reviewer_family.strip():
        raise HarnessValidationError("source review shard requires its reviewer family")
    work_item = source_review_work_item(plan, work_item_id)
    if review.candidates:
        raise HarnessValidationError("source review shard must not repeat cold candidate judgments")
    if [item.candidateId for item in review.selection] != [
        _root(value) for value in work_item.candidateIds
    ]:
        raise HarnessValidationError(
            "source review shard changed its candidate decision assignment"
        )
    if [item.opportunityId for item in review.opportunities] != [
        _root(value) for value in work_item.opportunityIds
    ]:
        raise HarnessValidationError("source review shard changed its opportunity assignment")
    expected_overlaps = [task.model_dump(mode="json") for task in work_item.overlaps]
    actual_overlaps = [
        {
            "candidateIds": [_root(value) for value in item.candidateIds],
            "overlapSpan": item.overlapSpan.model_dump(mode="json"),
        }
        for item in review.overlaps
    ]
    if actual_overlaps != expected_overlaps:
        raise HarnessValidationError("source review shard changed its overlap assignment")
    expected_handoffs = [task.model_dump(mode="json") for task in work_item.handoffs]
    actual_handoffs = [
        {
            "candidateIds": [_root(value) for value in item.candidateIds],
            "leftContextSpan": item.leftContextSpan.model_dump(mode="json"),
            "rightContextSpan": item.rightContextSpan.model_dump(mode="json"),
        }
        for item in review.handoffs
    ]
    if actual_handoffs != expected_handoffs:
        raise HarnessValidationError("source review shard changed its handoff assignment")
    prefix = f"{work_item.workItemId}:"
    if any(not item.id.startswith(f"{prefix}missing:") for item in review.missingOpportunities):
        raise HarnessValidationError(
            "source review shard missing opportunity lacks work-item ownership"
        )
    if review.missingOpportunities and not work_item.discoverMissingOpportunities:
        raise HarnessValidationError("relationship or continuation shard invented an omission")
    if any(not finding.id.startswith(f"{prefix}finding:") for finding in review.findings):
        raise HarnessValidationError("source review shard finding lacks work-item ownership")
    permitted_candidates = {_root(value) for value in work_item.inspectionCandidateIds}
    missing_ids = {item.id for item in review.missingOpportunities}
    permitted_opportunities = {
        *(_root(value) for value in work_item.contextOpportunityIds),
        *missing_ids,
    }
    for judgment in review.opportunities:
        if not {_root(value) for value in judgment.candidateIds} <= permitted_candidates:
            raise HarnessValidationError(
                "source review shard opportunity judgment exceeds its candidate inspection scope"
            )
    for finding in review.findings:
        if (
            not set(finding.affectedCandidateIds) <= permitted_candidates
            or not set(finding.opportunityIds) <= permitted_opportunities
        ):
            raise HarnessValidationError("source review shard finding exceeds its owned context")
    if work_item.discoverMissingOpportunities:
        if work_item.sourceSpan is None:
            raise HarnessValidationError("omission scan lacks its exact source span")
        positions = _positions(index)
        scan_first = positions[work_item.sourceSpan.firstSentenceId]
        scan_last = positions[work_item.sourceSpan.lastSentenceId]
        for opportunity in review.missingOpportunities:
            anchor = positions[opportunity.coreSpans[0].firstSentenceId]
            if not scan_first <= anchor <= scan_last:
                raise HarnessValidationError(
                    "missing opportunity core belongs to another omission scan"
                )
    overlap_rows = candidate_overlap_rows(evidence, draft)
    handoff_rows = candidate_handoff_rows(evidence, draft)
    validate_portfolio_review(
        evidence,
        draft,
        _padded_review(
            draft,
            review,
            work_item,
            overlap_rows=overlap_rows,
            handoff_rows=handoff_rows,
        ),
    )
    return TopicSourceReviewShard(
        format=SOURCE_REVIEW_SHARD_FORMAT,
        indexSha256=plan.indexSha256,
        selectionSha256=plan.selectionSha256,
        planSha256=plan_sha256,
        workItem=work_item,
        reviewerFamily=reviewer_family,
        responseArtifact=response_artifact,
        inspectionArtifact=inspection_artifact,
        review=review,
    )


def assemble_source_review_manifest(
    evidence: HarnessEvidence,
    index: TopicSourceIndex,
    draft: TopicSelectionDraft,
    plan: TopicSourceReviewPlan,
    *,
    index_sha256: str,
    selection_sha256: str,
    plan_sha256: str,
    shards: Sequence[TopicSourceReviewShard],
    shard_artifacts: Sequence[HarnessArtifactRef],
) -> TopicSourceReviewManifest:
    """Assemble one review only when every exact work item has an admitted shard."""
    if plan.indexSha256 != index_sha256 or plan.selectionSha256 != selection_sha256:
        raise HarnessValidationError("source review plan belongs to another index or selection")
    if len(shards) != len(plan.workItems) or len(shard_artifacts) != len(shards):
        raise HarnessValidationError("source review manifest requires every planned shard")
    decisions: dict[str, TopicSelectionDecision] = {}
    judgments: dict[str, TopicOpportunityJudgment] = {}
    overlaps: dict[tuple[str, str], TopicCandidateOverlapJudgment] = {}
    handoffs: dict[tuple[str, str], TopicCandidateHandoffJudgment] = {}
    missing: list[TopicOpportunity] = []
    findings: list[TopicSelectionFinding] = []
    families: list[str] = []
    responses: list[HarnessArtifactRef] = []
    inspections: list[HarnessArtifactRef] = []
    for expected, shard, reference in zip(plan.workItems, shards, shard_artifacts, strict=True):
        if (
            shard.indexSha256 != index_sha256
            or shard.selectionSha256 != selection_sha256
            or shard.planSha256 != plan_sha256
            or shard.workItem != expected
            or _kind(reference.kind) != "checks"
            or reference.sha256 != content_hash(shard)
        ):
            raise HarnessValidationError("source review shard identity or order differs from plan")
        admitted = admit_source_review_shard(
            evidence,
            index,
            draft,
            plan,
            plan_sha256=plan_sha256,
            work_item_id=expected.workItemId,
            review=shard.review,
            reviewer_family=shard.reviewerFamily,
            response_artifact=shard.responseArtifact,
            inspection_artifact=shard.inspectionArtifact,
        )
        if admitted != shard:
            raise HarnessValidationError("source review shard content changed after admission")
        for item in shard.review.selection:
            if item.candidateId in decisions:
                raise HarnessValidationError(
                    "candidate decision is duplicated across review shards"
                )
            decisions[item.candidateId] = item
        for item in shard.review.opportunities:
            if item.opportunityId in judgments:
                raise HarnessValidationError(
                    "opportunity judgment is duplicated across review shards"
                )
            judgments[item.opportunityId] = item
        for item in shard.review.overlaps:
            key = (_root(item.candidateIds[0]), _root(item.candidateIds[1]))
            if key in overlaps:
                raise HarnessValidationError("overlap judgment is duplicated across review shards")
            overlaps[key] = item
        for item in shard.review.handoffs:
            key = (_root(item.candidateIds[0]), _root(item.candidateIds[1]))
            if key in handoffs:
                raise HarnessValidationError("handoff judgment is duplicated across review shards")
            handoffs[key] = item
        missing.extend(shard.review.missingOpportunities)
        findings.extend(shard.review.findings)
        if shard.reviewerFamily not in families:
            families.append(shard.reviewerFamily)
        responses.append(shard.responseArtifact)
        if shard.inspectionArtifact is not None:
            inspections.append(shard.inspectionArtifact)
    overlap_rows = candidate_overlap_rows(evidence, draft)
    handoff_rows = candidate_handoff_rows(evidence, draft)
    review = TopicPortfolioReviewV4(
        candidates=[],
        findings=findings,
        missingOpportunities=missing,
        opportunities=[judgments[item.id] for item in draft.opportunities],
        selection=[decisions[item.id] for item in draft.proposal.candidates],
        summary=f"Assembled from {len(shards)} complete bounded source-review work items.",
        overlaps=[
            overlaps[(row["candidateIds"][0], row["candidateIds"][1])] for row in overlap_rows
        ],
        handoffs=[
            handoffs[(row["candidateIds"][0], row["candidateIds"][1])] for row in handoff_rows
        ],
    )
    validate_portfolio_review(evidence, draft, review)
    return TopicSourceReviewManifest.model_validate(
        {
            "format": SOURCE_REVIEW_MANIFEST_FORMAT,
            "complete": True,
            "indexSha256": index_sha256,
            "selectionSha256": selection_sha256,
            "planSha256": plan_sha256,
            "workItemIds": [item.workItemId for item in plan.workItems],
            "shardArtifacts": [item.model_dump(mode="json") for item in shard_artifacts],
            "reviewerFamilies": families,
            "responseArtifacts": [item.model_dump(mode="json") for item in responses],
            "inspectionArtifacts": [item.model_dump(mode="json") for item in inspections],
            "review": review.model_dump(mode="json"),
        }
    )
