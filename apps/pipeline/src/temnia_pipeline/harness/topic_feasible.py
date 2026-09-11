"""Reproducible physical candidates for every lexical sentence transition.

The versioned candidate ID carries the exact rational grid instant; timeMs remains
its public display approximation. This avoids rounding a safe audio sample out of
a submillisecond feasible region. All such candidates are reproduced during evidence
validation, so the ID is not permission to introduce an arbitrary time.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction
from itertools import pairwise
from math import ceil, floor
from typing import TYPE_CHECKING, Never

from temnia_pipeline.contracts import HarnessBoundaryCandidate, Kind2
from temnia_pipeline.harness.evidence import _uncertainty_reasons
from temnia_pipeline.speech.coverage import merge_intervals

if TYPE_CHECKING:
    from collections.abc import Iterator

    from temnia_pipeline.contracts import HarnessEvidence

DERIVATION_VERSION = "topic-feasible-grid/2"
CONFIG_KEY = "topicBoundaryDerivation"
PREFIX = "topic-grid-v2:"


def _invalid(message: str) -> Never:
    raise ValueError(message)


def _rounded_ms(instant: Fraction) -> int:
    value = instant * 1000
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + int(remainder * 2 >= value.denominator)


def _grid_rate(evidence: HarnessEvidence) -> Fraction:
    if evidence.frameRate is not None:
        return Fraction(evidence.frameRate.numerator, evidence.frameRate.denominator)
    return Fraction(evidence.audioSampleRate or 1000)


def derived_candidate_time(
    evidence: HarnessEvidence,
    candidate: HarnessBoundaryCandidate,
) -> Fraction | None:
    """Read a reproducibly validated grid candidate, or defer to legacy quantization."""
    if not candidate.id.startswith(PREFIX):
        return None
    if evidence.config.get(CONFIG_KEY) != DERIVATION_VERSION:
        _invalid("derived topic candidate has no matching evidence derivation")
    numerator, denominator = candidate.id.removeprefix(PREFIX).split(":")
    instant = Fraction(int(numerator), int(denominator))
    if candidate.id != f"{PREFIX}{instant.numerator}:{instant.denominator}":
        _invalid("derived topic candidate time is not canonical")
    return instant


def _regions(
    lower: int,
    upper: int,
    speech: list[tuple[int, int]],
    speech_ends: list[int],
) -> Iterator[tuple[Fraction, Fraction]]:
    if lower > upper:
        return
    cursor = lower
    for index in range(bisect_left(speech_ends, lower), len(speech)):
        start, end = speech[index]
        if start > upper:
            break
        if start >= cursor:
            yield Fraction(cursor, 1000), Fraction(min(start, upper), 1000)
        cursor = max(cursor, end)
        if cursor > upper:
            return
    if cursor <= upper:
        yield Fraction(cursor, 1000), Fraction(upper, 1000)


def _derive(evidence: HarnessEvidence) -> list[HarnessBoundaryCandidate]:
    words = evidence.words
    if not words:
        return []
    prefix_ends: list[int] = []
    end = 0
    for word in words:
        end = max(end, word.endMs)
        prefix_ends.append(end)
    suffix_starts = [evidence.durationMs] * len(words)
    start = evidence.durationMs
    for index in range(len(words) - 1, -1, -1):
        start = min(start, words[index].startMs)
        suffix_starts[index] = start
    speech = merge_intervals(
        [(word.startMs, word.endMs) for word in words]
        + [(row.startMs, row.endMs) for row in evidence.speechCoverage.intervals],
        evidence.durationMs,
    )
    sorted_words = sorted(words, key=lambda word: (word.startMs, word.wordIndex))
    starts = [word.startMs for word in sorted_words]
    speech_ends = [end for _start, end in speech]
    rate = _grid_rate(evidence)
    candidates: dict[str, HarnessBoundaryCandidate] = {}
    word_index = 0
    for previous, following in pairwise(evidence.sentences):
        word_index += len(previous.wordIds)
        lower, upper = prefix_ends[word_index - 1], suffix_starts[word_index]
        for left, right in _regions(lower, upper, speech, speech_ends):
            first = ceil(left * rate)
            last = floor(right * rate)
            if first > last:
                continue
            for index in sorted({first, (first + last) // 2, last}):
                instant = Fraction(index) / rate
                time_ms = _rounded_ms(instant)
                identifier = f"{PREFIX}{instant.numerator}:{instant.denominator}"
                reasons = {DERIVATION_VERSION, *_uncertainty_reasons(time_ms, sorted_words, starts)}
                if str(evidence.speechCoverage.status) != "clear":
                    reasons.add(f"speech_coverage_{evidence.speechCoverage.status}")
                uncertainty = reasons - {DERIVATION_VERSION}
                clearance = max(0, int(min(instant - left, right - instant) * 1000))
                candidates.setdefault(
                    identifier,
                    HarnessBoundaryCandidate(
                        id=identifier,
                        timeMs=time_ms,
                        kind=Kind2.pause,
                        score=1.0,
                        sentenceId=following.id,
                        clearanceMs=clearance,
                        requiresReview=bool(uncertainty),
                        reasons=sorted(reasons),
                    ),
                )
    return sorted(candidates.values(), key=lambda candidate: (candidate.timeMs, candidate.id))


def validate_topic_derivation(evidence: HarnessEvidence) -> None:
    """Reject altered, missing, or foreign derived candidates without changing evidence."""
    actual = [candidate for candidate in evidence.boundaries if candidate.id.startswith(PREFIX)]
    version = evidence.config.get(CONFIG_KEY)
    if version is None and not actual:
        return
    if version != DERIVATION_VERSION:
        _invalid("unsupported topic boundary derivation")
    expected = _derive(evidence)
    if sorted(actual, key=lambda candidate: (candidate.timeMs, candidate.id)) != expected:
        _invalid("topic boundary candidates differ from their reproducible source derivation")


def augment_topic_evidence(evidence: HarnessEvidence) -> HarnessEvidence:
    """Create a new immutable evidence value; original source observations are preserved."""
    if CONFIG_KEY in evidence.config or any(c.id.startswith(PREFIX) for c in evidence.boundaries):
        validate_topic_derivation(evidence)
        return evidence
    return evidence.model_copy(
        update={
            "config": {**evidence.config, CONFIG_KEY: DERIVATION_VERSION},
            "boundaries": sorted(
                [*evidence.boundaries, *_derive(evidence)],
                key=lambda candidate: (candidate.timeMs, candidate.id),
            ),
        }
    )
