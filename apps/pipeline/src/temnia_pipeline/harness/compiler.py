"""Deterministic compilation of model sentence ranges into exact media cuts."""

# ruff: noqa: C901, D105, EM101, PLR0912, PLR0913, PLR2004, TRY003

from __future__ import annotations

import bisect
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    ChapterBoundary,
    ChapterEditSpec,
    ChapterSection,
    Kind2,
    RationalTime,
    ReviewState,
)
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    rounded_milliseconds,
    validate_edit,
    validate_proposal,
    word_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from temnia_pipeline.contracts import (
        ChapterProposal,
        ChapterProposalSection,
        HarnessBoundaryCandidate,
        HarnessEvidence,
    )

MAX_SAFE_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class CompilerConfig:
    """Versioned, transparent provisional boundary-selection parameters."""

    version: str = "chapter-compiler/1"
    candidate_radius_ms: int = 1500
    max_candidates_per_layer: int = 64
    preferred_clearance_ms: int = 250
    displacement_weight: int = 100
    clearance_weight: int = 20
    review_penalty: int = 1_000_000
    widening_penalty: int = 10_000

    def __post_init__(self) -> None:
        if self.candidate_radius_ms < 0:
            raise ValueError("candidate radius must be nonnegative")
        if not 4 <= self.max_candidates_per_layer <= 256:
            raise ValueError("candidate cap must be between 4 and 256")
        if self.preferred_clearance_ms < 0:
            raise ValueError("preferred clearance must be nonnegative")


@dataclass(frozen=True, slots=True)
class _Choice:
    candidate: HarnessBoundaryCandidate
    cost: int
    reasons: tuple[str, ...]
    time: Fraction


@dataclass(frozen=True, slots=True)
class _Path:
    choices: tuple[_Choice, ...]
    cost: int

    def key(self) -> tuple[int, tuple[Fraction, ...], tuple[str, ...]]:
        return (
            self.cost,
            tuple(choice.time for choice in self.choices),
            tuple(choice.candidate.id for choice in self.choices),
        )


@dataclass(frozen=True, slots=True)
class _IntervalIndex:
    starts: tuple[Fraction, ...]
    ends: tuple[Fraction, ...]

    @classmethod
    def build(cls, intervals: Sequence[tuple[int, int]]) -> _IntervalIndex:
        merged: list[tuple[Fraction, Fraction]] = []
        for start_ms, end_ms in sorted(intervals):
            start = Fraction(start_ms, 1000)
            end = Fraction(end_ms, 1000)
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return cls(
            starts=tuple(start for start, _end in merged),
            ends=tuple(end for _start, end in merged),
        )

    def inside(self, time: Fraction) -> bool:
        index = bisect.bisect_right(self.starts, time) - 1
        return index >= 0 and self.starts[index] < time < self.ends[index]

    def clearance_ms(self, time: Fraction) -> int:
        if not self.starts:
            return rounded_milliseconds(time)
        index = bisect.bisect_right(self.starts, time) - 1
        if index >= 0 and self.starts[index] < time < self.ends[index]:
            return 0
        left = time - self.ends[index] if index >= 0 else None
        right_index = index + 1
        right = self.starts[right_index] - time if right_index < len(self.starts) else None
        distances = [distance for distance in (left, right) if distance is not None]
        if not distances:
            return rounded_milliseconds(time)
        return max(0, int(min(distances) * 1000))


def _round_half_earlier(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + int(remainder * 2 > value.denominator)


def quantize_time(evidence: HarnessEvidence, time_ms: int) -> Fraction:
    """Quantize to an exact frame, then sample, then millisecond grid."""
    source_time = Fraction(time_ms, 1000)
    if evidence.frameRate is not None:
        fps = Fraction(evidence.frameRate.numerator, evidence.frameRate.denominator)
        frame_index = _round_half_earlier(source_time * fps)
        return Fraction(frame_index, 1) / fps
    if evidence.audioSampleRate is not None:
        sample_index = _round_half_earlier(source_time * evidence.audioSampleRate)
        return Fraction(sample_index, evidence.audioSampleRate)
    return source_time


def _public_time(value: Fraction) -> RationalTime:
    if value < 0 or value.numerator > MAX_SAFE_INTEGER or value.denominator > MAX_SAFE_INTEGER:
        raise HarnessValidationError("quantized rational time exceeds public safe integers")
    return RationalTime(numerator=value.numerator, denominator=value.denominator)


def _candidate_cost(
    candidate: HarnessBoundaryCandidate,
    *,
    desired_ms: int,
    widened: bool,
    config: CompilerConfig,
) -> int:
    displacement = abs(candidate.timeMs - desired_ms) * config.displacement_weight
    clearance = (
        max(0, config.preferred_clearance_ms - candidate.clearanceMs) * config.clearance_weight
    )
    review = config.review_penalty if candidate.requiresReview else 0
    widening = config.widening_penalty if widened else 0
    preference = {
        Kind2.edge: 0,
        Kind2.pause: -100,
        Kind2.turn: -50,
        Kind2.shot: -25,
        Kind2.sentence: 0,
    }[candidate.kind]
    bounded_score = max(-1.0, min(1.0, candidate.score))
    return displacement + clearance + review + widening + preference - int(bounded_score * 100)


def _cap_candidates(
    candidates: Sequence[HarnessBoundaryCandidate],
    *,
    desired_ms: int,
    limit: int,
) -> list[HarnessBoundaryCandidate]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            abs(candidate.timeMs - desired_ms),
            candidate.timeMs,
            candidate.id,
        ),
    )
    preserved: list[HarnessBoundaryCandidate] = []
    for kind in (Kind2.pause, Kind2.turn, Kind2.shot, Kind2.sentence):
        safe = next(
            (
                candidate
                for candidate in ranked
                if candidate.kind == kind and not candidate.requiresReview
            ),
            None,
        )
        if safe is not None and safe not in preserved:
            preserved.append(safe)
    selected = preserved[:limit]
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _layer_choices(  # noqa: PLR0915
    evidence: HarnessEvidence,
    *,
    previous_sentence_start: int,
    previous_sentence_end: int,
    next_sentence_start: int,
    next_sentence_end: int,
    desired_ms: int,
    config: CompilerConfig,
    lexical: _IntervalIndex,
    detected: _IntervalIndex | None,
) -> list[_Choice]:
    local_candidates = [
        candidate
        for candidate in evidence.boundaries
        if candidate.kind != Kind2.edge
        and previous_sentence_start <= candidate.timeMs <= next_sentence_end
    ]
    if not local_candidates:
        raise HarnessValidationError(
            "no grounded internal boundary candidate exists for a proposal transition"
        )
    quantized_times: dict[str, Fraction] = {}
    effective: list[HarnessBoundaryCandidate] = []
    for candidate in local_candidates:
        quantized = quantize_time(evidence, candidate.timeMs)
        quantized_times[candidate.id] = quantized
        reasons = set(candidate.reasons)
        requires_review = candidate.requiresReview
        if lexical.inside(quantized):
            reasons.add("compiler_quantized_inside_lexical_span")
            requires_review = True
        if detected is not None and detected.inside(quantized):
            reasons.add("compiler_quantized_inside_detected_speech")
            requires_review = True
        if quantized != Fraction(candidate.timeMs, 1000):
            reasons.add("compiler_quantized_to_media_grid")
        effective.append(
            candidate.model_copy(
                update={
                    "clearanceMs": lexical.clearance_ms(quantized),
                    "reasons": sorted(reasons),
                    "requiresReview": requires_review,
                    "timeMs": rounded_milliseconds(quantized),
                }
            )
        )
    lower = min(previous_sentence_end, next_sentence_start) - config.candidate_radius_ms
    upper = max(previous_sentence_end, next_sentence_start) + config.candidate_radius_ms
    initial = [candidate for candidate in effective if lower <= candidate.timeMs <= upper]
    widened_ids: set[str] = set()
    selected = list(initial)
    if not any(not candidate.requiresReview for candidate in selected):
        ordered = sorted(effective, key=lambda candidate: (candidate.timeMs, candidate.id))
        bisect_index = 0
        while bisect_index < len(ordered) and ordered[bisect_index].timeMs < lower:
            bisect_index += 1
        left = bisect_index - 1
        right = bisect_index
        while left >= 0 or right < len(ordered):
            options: list[tuple[HarnessBoundaryCandidate, str]] = []
            if left >= 0:
                options.append((ordered[left], "left"))
            if right < len(ordered):
                options.append((ordered[right], "right"))
            next_candidate, side = min(
                options,
                key=lambda option: (
                    abs(option[0].timeMs - desired_ms),
                    option[0].timeMs,
                    option[0].id,
                ),
            )
            if next_candidate not in selected:
                selected.append(next_candidate)
                widened_ids.add(next_candidate.id)
            if side == "left":
                left -= 1
            else:
                right += 1
            if not next_candidate.requiresReview:
                break
            if len(selected) >= config.max_candidates_per_layer:
                break
    selected = _cap_candidates(
        selected,
        desired_ms=desired_ms,
        limit=config.max_candidates_per_layer,
    )
    choices: list[_Choice] = []
    for candidate in selected:
        quantized = quantized_times[candidate.id]
        reasons = set(candidate.reasons)
        widened = candidate.id in widened_ids
        if widened:
            reasons.add("compiler_window_widened")
        choices.append(
            _Choice(
                candidate=candidate,
                cost=_candidate_cost(
                    candidate,
                    desired_ms=desired_ms,
                    widened=widened,
                    config=config,
                ),
                reasons=tuple(sorted(reasons)),
                time=quantized,
            )
        )
    return sorted(choices, key=lambda choice: (choice.time, choice.candidate.id))


def _quote_centers(
    evidence: HarnessEvidence, section: ChapterProposalSection
) -> tuple[Fraction, ...]:
    words = {word.id: word for word in evidence.words}
    centers: list[Fraction] = []
    for value in section.quoteWordIds:
        word = words[word_id(value)]
        centers.append(Fraction(word.startMs + word.endMs, 2000))
    return tuple(centers)


def _owns_quotes(start: Fraction, end: Fraction, centers: Sequence[Fraction]) -> bool:
    return all(start <= center < end for center in centers)


def _choose_path(
    evidence: HarnessEvidence,
    proposal: ChapterProposal,
    layers: Sequence[Sequence[_Choice]],
) -> tuple[_Choice, ...]:
    if not layers:
        return ()
    starts = Fraction(0)
    first_quotes = _quote_centers(evidence, proposal.sections[0])
    states: dict[str, _Path] = {}
    for choice in layers[0]:
        if choice.time > starts and _owns_quotes(starts, choice.time, first_quotes):
            states[choice.candidate.id] = _Path((choice,), choice.cost)
    for layer_index, choices in enumerate(layers[1:], start=1):
        next_states: dict[str, _Path] = {}
        centers = _quote_centers(evidence, proposal.sections[layer_index])
        for choice in choices:
            compatible = [
                _Path((*path.choices, choice), path.cost + choice.cost)
                for path in states.values()
                if path.choices[-1].time < choice.time
                and _owns_quotes(path.choices[-1].time, choice.time, centers)
            ]
            if compatible:
                next_states[choice.candidate.id] = min(compatible, key=_Path.key)
        states = next_states
    end = Fraction(evidence.durationMs, 1000)
    final_quotes = _quote_centers(evidence, proposal.sections[-1])
    complete = [
        path
        for path in states.values()
        if path.choices[-1].time < end and _owns_quotes(path.choices[-1].time, end, final_quotes)
    ]
    if not complete:
        raise HarnessValidationError(
            "no monotonic grounded candidate path preserves positive sections and quote ownership"
        )
    return min(complete, key=_Path.key).choices


def _edge(evidence: HarnessEvidence, time_ms: int) -> HarnessBoundaryCandidate:
    matches = [
        candidate
        for candidate in evidence.boundaries
        if candidate.kind == Kind2.edge and candidate.timeMs == time_ms
    ]
    if len(matches) != 1:
        raise HarnessValidationError("evidence must contain one exact candidate per source edge")
    return matches[0]


def compile_chapters(
    evidence: HarnessEvidence,
    proposal: ChapterProposal,
    *,
    evidence_artifact_id: UUID,
    evidence_sha256: str,
    config: CompilerConfig | None = None,
) -> ChapterEditSpec:
    """Compile a validated sentence proposal through a joint monotonic DP."""
    config = config or CompilerConfig()
    validate_proposal(evidence, proposal)
    sentence_by_id = {sentence.id: sentence for sentence in evidence.sentences}
    lexical = _IntervalIndex.build([(word.startMs, word.endMs) for word in evidence.words])
    detected = (
        _IntervalIndex.build(
            [(interval.startMs, interval.endMs) for interval in evidence.speechCoverage.intervals]
        )
        if evidence.speechCoverage.detector is not None and evidence.speechCoverage.intervals
        else None
    )
    transition_layers: list[list[_Choice]] = []
    for previous, following in zip(proposal.sections, proposal.sections[1:], strict=False):
        previous_sentence = sentence_by_id[previous.lastSentenceId]
        next_sentence = sentence_by_id[following.firstSentenceId]
        desired_ms = (previous_sentence.endMs + next_sentence.startMs) // 2
        transition_layers.append(
            _layer_choices(
                evidence,
                previous_sentence_start=previous_sentence.startMs,
                previous_sentence_end=previous_sentence.endMs,
                next_sentence_start=next_sentence.startMs,
                next_sentence_end=next_sentence.endMs,
                desired_ms=desired_ms,
                config=config,
                lexical=lexical,
                detected=detected,
            )
        )
    choices = _choose_path(evidence, proposal, transition_layers)
    start_candidate = _edge(evidence, 0)
    end_candidate = _edge(evidence, evidence.durationMs)
    compiled: list[tuple[HarnessBoundaryCandidate, Fraction, tuple[str, ...]]] = [
        (start_candidate, Fraction(0), tuple(start_candidate.reasons)),
        *[(choice.candidate, choice.time, choice.reasons) for choice in choices],
        (
            end_candidate,
            Fraction(evidence.durationMs, 1000),
            tuple(end_candidate.reasons),
        ),
    ]
    boundaries = [
        ChapterBoundary(
            candidateId=candidate.id,
            id=f"c{index:06d}",
            reasons=list(reasons),
            requiresReview=candidate.requiresReview,
            time=_public_time(time),
            timeMs=rounded_milliseconds(time),
        )
        for index, (candidate, time, reasons) in enumerate(compiled)
    ]
    sections = [
        ChapterSection(
            endBoundaryId=boundaries[index + 1].id,
            flags=[],
            id=proposal_section.id,
            kind=proposal_section.kind,
            quoteWordIds=list(proposal_section.quoteWordIds),
            reason=proposal_section.reason,
            reviewState=ReviewState.proposed,
            startBoundaryId=boundaries[index].id,
            title=proposal_section.title,
        )
        for index, proposal_section in enumerate(proposal.sections)
    ]
    edit = ChapterEditSpec(
        boundaries=boundaries,
        compilerVersion=config.version,
        durationMs=evidence.durationMs,
        evidenceArtifactId=evidence_artifact_id,
        evidenceSha256=evidence_sha256,
        sections=sections,
        sourceAudioSampleRate=evidence.audioSampleRate,
        sourceFrameRate=evidence.frameRate,
        sourceId=evidence.sourceId,
        version=1,
    )
    validate_edit(evidence, edit, expected_evidence_sha256=evidence_sha256)
    return edit
