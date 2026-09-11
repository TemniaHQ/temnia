"""Atomic source-bound human corrections, independent of model authoring authority."""

# Refusals are editorial feedback; generated enum/root types are normalized at this boundary.
# ruff: noqa: EM101, EM102, TRY003
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import TopicOpportunity, TopicProposal, TopicSelectionDraft
from temnia_pipeline.harness.review import ReviewRefused
from temnia_pipeline.harness.topic_compiler import validate_topic_proposal

if TYPE_CHECKING:
    from temnia_pipeline.contracts import HarnessEvidence, TopicCandidate, TopicEditorialPatchInput


MIN_COMPOUND_MEMBERS = 2
MAX_IDENTIFIER_LENGTH = 256


@dataclass(frozen=True)
class HumanTopicPatch:
    """Exact mutation membership also supplies retained parent/child feedback."""

    proposal: TopicProposal
    affected_ids: frozenset[str]
    replacement_ids: frozenset[str]
    lineage: dict[str, tuple[str, ...]]


def apply_human_topic_patch(  # noqa: C901, PLR0912
    evidence: HarnessEvidence, previous: TopicProposal, command: TopicEditorialPatchInput
) -> HumanTopicPatch:
    """Validate every operation before returning a complete replacement proposal."""
    validate_topic_proposal(evidence, previous)
    if command.sourceId != evidence.sourceId or not command.reason.strip():
        raise ReviewRefused(
            "The correction must name this source and explain the editorial change."
        )
    seconds, method = command.correctionActiveSeconds, command.correctionMeasurementMethod
    if (
        (seconds is None) != (method is None)
        or (seconds is not None and (not math.isfinite(seconds) or seconds < 0))
        or (method is not None and not method.strip())
    ):
        raise ReviewRefused(
            "Correction time requires a finite duration and its measurement method."
        )
    current = {candidate.id: candidate for candidate in previous.candidates}
    affected: set[str] = set()
    operation_ids: set[str] = set()
    replacements: list[TopicCandidate] = []
    lineage: dict[str, tuple[str, ...]] = {}
    for operation in command.operations:
        parents = tuple(item for item in operation.affectedCandidateIds)
        children = operation.replacementCandidates
        kind = operation.kind.value
        if operation.operationId in operation_ids or not operation.operationId.strip():
            raise ReviewRefused("Every correction operation needs a unique nonblank identity.")
        operation_ids.add(operation.operationId)
        if len(set(parents)) != len(parents) or set(parents) - current.keys():
            raise ReviewRefused("A correction names duplicate or absent parent videos.")
        if affected.intersection(parents):
            raise ReviewRefused("A video may be affected by only one operation in a transaction.")
        counts = len(parents), len(children)
        valid_count = {
            "adjust_extent": counts == (1, 1),
            "retitle": counts == (1, 1),
            "add": counts[0] == 0 and counts[1] == 1,
            "drop": counts == (1, 0),
            "merge": counts[0] >= MIN_COMPOUND_MEMBERS and counts[1] == 1,
            "split": counts[0] == 1 and counts[1] >= MIN_COMPOUND_MEMBERS,
        }[kind]
        if not valid_count:
            raise ReviewRefused(f"The {kind} operation has invalid parent/replacement counts.")
        if kind in {"retitle", "adjust_extent"}:
            prior, child = current[parents[0]], children[0]
            if child.id != prior.id:
                raise ReviewRefused(
                    "An extent or title correction must preserve the video identity."
                )
            if kind == "retitle" and child.model_dump(exclude={"title"}) != prior.model_dump(
                exclude={"title"}
            ):
                raise ReviewRefused("A title correction cannot change content or annotations.")
            if child == prior:
                raise ReviewRefused("The correction does not change this video.")
        elif any(child.id in current for child in children):
            raise ReviewRefused("Added, merged and split videos require new stable identities.")
        for child in children:
            if child.id in lineage:
                raise ReviewRefused("Replacement video identities must be unique.")
            lineage[child.id] = parents
        affected.update(parents)
        replacements.extend(children)
    output = [candidate for candidate in previous.candidates if candidate.id not in affected]
    output.extend(replacements)
    positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    # Reference validation precedes ordering; no invalid ID is guessed or silently omitted.
    proposal = TopicProposal(version=1, summary=previous.summary, candidates=output)
    validate_topic_proposal(evidence, proposal)
    proposal = proposal.model_copy(
        update={
            "candidates": sorted(
                output, key=lambda item: (positions[item.firstSentenceId], item.id)
            )
        }
    )
    return HumanTopicPatch(proposal, frozenset(affected), frozenset(lineage), lineage)


def remap_human_opportunities(
    previous: TopicSelectionDraft, patch: HumanTopicPatch, command: TopicEditorialPatchInput
) -> TopicSelectionDraft:
    """Keep unselected opportunities visible and label new treatments as human hypotheses."""
    remaining = {candidate.id for candidate in patch.proposal.candidates}
    opportunities: list[TopicOpportunity] = []
    for opportunity in previous.opportunities:
        ids = [
            item
            for item in opportunity.candidateIds
            if item in remaining and item not in patch.affected_ids
        ]
        changed = any(item in patch.affected_ids for item in opportunity.candidateIds)
        body = opportunity.model_dump(mode="json")
        if changed:
            body.update(
                candidateIds=ids,
                disposition="proposed" if ids else "needs_evidence",
                dispositionReason=(
                    "Human-corrected treatment; portfolio representation needs review."
                ),
            )
        opportunities.append(TopicOpportunity.model_validate(body))
    used_ids = {item.id for item in opportunities}
    for candidate in patch.proposal.candidates:
        if candidate.id not in patch.replacement_ids:
            continue
        opportunity_id = f"human:{command.mutationKey}:{candidate.id}"
        if len(opportunity_id) > MAX_IDENTIFIER_LENGTH:
            opportunity_id = "human:" + hashlib.sha256(opportunity_id.encode()).hexdigest()
        if opportunity_id in used_ids:
            raise ReviewRefused("A new human opportunity identity collides with an existing one.")
        opportunities.append(
            TopicOpportunity.model_validate(
                {
                    "id": opportunity_id,
                    "candidateIds": [candidate.id],
                    "viewerPurpose": candidate.purpose,
                    "coreSpans": [span.model_dump() for span in candidate.coreSpans],
                    "valueEvidenceSpans": [span.model_dump() for span in candidate.coreSpans],
                    "completionSpans": [span.model_dump() for span in candidate.completionSpans],
                    "requiredContextSpans": [
                        span.model_dump() for span in candidate.requiredContextSpans
                    ],
                    "meaningChangingFollowups": [
                        span.model_dump() for span in candidate.meaningChangingFollowups
                    ],
                    "disposition": "proposed",
                    "dispositionReason": (
                        "Human-specified treatment; value and completeness remain unassessed."
                    ),
                }
            )
        )
    return TopicSelectionDraft(proposal=patch.proposal, opportunities=opportunities)
