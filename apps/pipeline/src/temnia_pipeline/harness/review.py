"""Pure chapter review mutations; persistence, CAS and replay live outside."""

# ruff: noqa: C901, EM101, EM102, N818, TRY003

from __future__ import annotations

import math
from fractions import Fraction
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    ChapterBoundary,
    ChapterEditSpec,
    ChapterReviewAction,
    ChapterSection,
    Kind,
    RationalTime,
    ReviewState,
)
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    rational,
    rounded_milliseconds,
    validate_edit,
    word_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from temnia_pipeline.contracts import ChapterReviewInput, HarnessEvidence


class ReviewRefused(HarnessValidationError):
    """A human command cannot produce a valid edit from this revision."""


class OperationalReviewAction(ReviewRefused):
    """The command changes workflow state or budget, not an edit body."""


def _section_index(edit: ChapterEditSpec, section_id: str | None) -> int:
    if section_id is None:
        raise ReviewRefused("this action requires a section id")
    matches = [index for index, section in enumerate(edit.sections) if section.id == section_id]
    if len(matches) != 1:
        raise ReviewRefused("section is absent from the current edit")
    return matches[0]


def _grid_rate(evidence: HarnessEvidence) -> Fraction:
    if evidence.frameRate is not None:
        return Fraction(evidence.frameRate.numerator, evidence.frameRate.denominator)
    if evidence.audioSampleRate is not None:
        return Fraction(evidence.audioSampleRate)
    return Fraction(1000)


def _round_half_earlier(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + int(remainder * 2 > value.denominator)


def _manual_time(
    evidence: HarnessEvidence,
    *,
    requested_ms: int,
    left: Fraction,
    right: Fraction,
) -> Fraction:
    rate = _grid_rate(evidence)
    first = math.floor(left * rate) + 1
    last = math.ceil(right * rate) - 1
    if first > last:
        raise ReviewRefused("adjacent cuts leave no media-grid point for a nudge")
    requested_index = _round_half_earlier(Fraction(requested_ms, 1000) * rate)
    return Fraction(min(max(requested_index, first), last), 1) / rate


def _replace_section(
    sections: Sequence[ChapterSection], index: int, replacement: ChapterSection
) -> list[ChapterSection]:
    return [
        replacement if item_index == index else section
        for item_index, section in enumerate(sections)
    ]


def _accept(edit: ChapterEditSpec, command: ChapterReviewInput) -> ChapterEditSpec:
    index = _section_index(edit, command.sectionId)
    section = edit.sections[index]
    unsafe = edit.boundaries[index].requiresReview or edit.boundaries[index + 1].requiresReview
    if (section.kind == Kind.drop or unsafe) and not command.reason.strip():
        raise ReviewRefused("accepting a drop or unsafe boundary requires a reason")
    flags = list(section.flags)
    if command.reason.strip():
        flags.append(f"accepted_reason:{command.reason.strip()}")
    replacement = section.model_copy(
        update={"flags": list(dict.fromkeys(flags)), "reviewState": ReviewState.accepted}
    )
    return edit.model_copy(update={"sections": _replace_section(edit.sections, index, replacement)})


def _reject(edit: ChapterEditSpec, command: ChapterReviewInput) -> ChapterEditSpec:
    if not command.reason.strip():
        raise ReviewRefused("reject requires a reason")
    index = _section_index(edit, command.sectionId)
    section = edit.sections[index]
    flags = [*section.flags, f"rejected_reason:{command.reason.strip()}"]
    replacement = section.model_copy(
        update={"flags": list(dict.fromkeys(flags)), "reviewState": ReviewState.rejected}
    )
    return edit.model_copy(update={"sections": _replace_section(edit.sections, index, replacement)})


def _restore(edit: ChapterEditSpec, command: ChapterReviewInput) -> ChapterEditSpec:
    index = _section_index(edit, command.sectionId)
    section = edit.sections[index]
    if section.kind != Kind.drop:
        raise ReviewRefused("restore applies only to a deliberate drop")
    flags = [*section.flags, f"restored_reason:{command.reason.strip()}"]
    replacement = section.model_copy(
        update={
            "flags": list(dict.fromkeys(flags)),
            "kind": Kind.keep,
            "reviewState": ReviewState.proposed,
        }
    )
    return edit.model_copy(update={"sections": _replace_section(edit.sections, index, replacement)})


def _nudge(
    evidence: HarnessEvidence,
    edit: ChapterEditSpec,
    command: ChapterReviewInput,
) -> ChapterEditSpec:
    if command.boundaryId is None or command.targetTimeMs is None:
        raise ReviewRefused("nudge requires a boundary id and requested time")
    indices = [
        index for index, boundary in enumerate(edit.boundaries) if boundary.id == command.boundaryId
    ]
    if len(indices) != 1 or indices[0] in {0, len(edit.boundaries) - 1}:
        raise ReviewRefused("nudge requires one shared internal boundary")
    index = indices[0]
    left = rational(edit.boundaries[index - 1].time)
    right = rational(edit.boundaries[index + 1].time)
    time = _manual_time(
        evidence,
        requested_ms=command.targetTimeMs,
        left=left,
        right=right,
    )
    reason = command.reason.strip() or "manual boundary adjustment"
    replacement = ChapterBoundary(
        candidateId=None,
        id=edit.boundaries[index].id,
        reasons=[
            f"manual_nudge:{reason}",
            f"manual_requested_ms:{command.targetTimeMs}",
        ],
        requiresReview=True,
        time=RationalTime(numerator=time.numerator, denominator=time.denominator),
        timeMs=rounded_milliseconds(time),
    )
    boundaries = list(edit.boundaries)
    boundaries[index] = replacement
    sections = list(edit.sections)
    for section_index in (index - 1, index):
        section = sections[section_index]
        sections[section_index] = section.model_copy(
            update={
                "flags": list(dict.fromkeys([*section.flags, "manual_boundary_changed"])),
                "reviewState": ReviewState.proposed,
            }
        )
    return edit.model_copy(update={"boundaries": boundaries, "sections": sections})


def _merge(edit: ChapterEditSpec, command: ChapterReviewInput) -> ChapterEditSpec:
    first_index = _section_index(edit, command.sectionId)
    second_index = _section_index(edit, command.otherSectionId)
    if second_index != first_index + 1:
        raise ReviewRefused("merge requires two adjacent sections in timeline order")
    first = edit.sections[first_index]
    second = edit.sections[second_index]
    if first.kind != Kind.keep or second.kind != Kind.keep:
        raise ReviewRefused("merge applies only to adjacent keep sections")
    merged_id = f"merge-{command.mutationKey.hex}"
    quotes = list(first.quoteWordIds)
    seen = {word_id(value) for value in quotes}
    for value in second.quoteWordIds:
        identifier = word_id(value)
        if identifier not in seen:
            quotes.append(value)
            seen.add(identifier)
    merged = ChapterSection(
        endBoundaryId=second.endBoundaryId,
        flags=list(dict.fromkeys([*first.flags, *second.flags, "manual_merge"])),
        id=merged_id,
        kind=Kind.keep,
        quoteWordIds=quotes,
        reason=command.reason.strip() or f"Merged {first.id} and {second.id}",
        reviewState=ReviewState.proposed,
        startBoundaryId=first.startBoundaryId,
        title=f"{first.title} / {second.title}".strip(" /"),
    )
    sections = [
        *edit.sections[:first_index],
        merged,
        *edit.sections[second_index + 1 :],
    ]
    boundaries = [
        *edit.boundaries[: first_index + 1],
        *edit.boundaries[first_index + 2 :],
    ]
    return edit.model_copy(update={"boundaries": boundaries, "sections": sections})


def apply_review(
    evidence: HarnessEvidence,
    current_edit: ChapterEditSpec,
    command: ChapterReviewInput,
    previous_edits: Mapping[int, ChapterEditSpec],
) -> ChapterEditSpec:
    """Apply one validated human edit command and return immutable new content."""
    validate_edit(
        evidence,
        current_edit,
        expected_evidence_sha256=current_edit.evidenceSha256,
    )
    if command.sourceId != current_edit.sourceId:
        raise ReviewRefused("review command belongs to a different source")
    if command.action != ChapterReviewAction.undo and command.targetRevision is not None:
        raise ReviewRefused("target revision is only valid for undo")
    if command.action == ChapterReviewAction.accept:
        result = _accept(current_edit, command)
    elif command.action == ChapterReviewAction.reject:
        result = _reject(current_edit, command)
    elif command.action == ChapterReviewAction.restore:
        result = _restore(current_edit, command)
    elif command.action == ChapterReviewAction.nudge:
        result = _nudge(evidence, current_edit, command)
    elif command.action == ChapterReviewAction.merge:
        result = _merge(current_edit, command)
    elif command.action == ChapterReviewAction.undo:
        if command.targetRevision is None or command.targetRevision >= command.baseRevision:
            raise ReviewRefused("undo requires a named earlier target revision")
        try:
            result = previous_edits[command.targetRevision].model_copy(deep=True)
        except KeyError as error:
            raise ReviewRefused("named undo revision is unavailable") from error
    else:
        raise OperationalReviewAction(
            f"{command.action.value} is an operational command, not an edit mutation"
        )
    validate_edit(evidence, result, expected_evidence_sha256=current_edit.evidenceSha256)
    return result
