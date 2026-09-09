"""Compare independent speech evidence with recognition span coverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Interval = tuple[int, int]
CoverageStatus = Literal["unknown", "clear", "needs_review"]


@dataclass(frozen=True, slots=True)
class CoverageThresholds:
    """Versioned provisional review routing thresholds, pending qualification."""

    version: str = "speech-coverage-thresholds/1-provisional"
    empty_transcript_speech_ms: int = 10_000
    largest_uncovered_ms: int = 10_000
    total_uncovered_ms: int = 30_000
    uncovered_ratio: float = 0.15
    tail_uncovered_ms: int = 5_000


DEFAULT_THRESHOLDS = CoverageThresholds()


def _empty_warnings() -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class CoverageAssessment:
    """Detector agreement evidence in the shared status vocabulary."""

    status: CoverageStatus
    intervals: list[Interval]
    detected_speech_ms: int
    recognized_span_ms: int
    overlap_ms: int
    uncovered_speech_ms: int
    uncovered_tail_ms: int
    largest_uncovered_ms: int
    recognition_outside_detector_ms: int
    threshold_version: str
    warnings: list[str] = field(default_factory=_empty_warnings)


def merge_intervals(intervals: list[Interval], duration_ms: int) -> list[Interval]:
    """Clamp and union half-open millisecond ranges."""
    cleaned = sorted(
        (max(0, start), min(duration_ms, end))
        for start, end in intervals
        if end > start and end > 0 and start < duration_ms
    )
    merged: list[Interval] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(source: list[Interval], covered: list[Interval]) -> list[Interval]:
    """Return the portions of a union not covered by another union."""
    result: list[Interval] = []
    covered_index = 0
    for start, end in source:
        cursor = start
        while covered_index < len(covered) and covered[covered_index][1] <= cursor:
            covered_index += 1
        index = covered_index
        while index < len(covered):
            cover_start, cover_end = covered[index]
            if cover_end <= cursor:
                index += 1
                continue
            if cover_start >= end:
                break
            if cover_start > cursor:
                result.append((cursor, min(cover_start, end)))
            cursor = max(cursor, cover_end)
            if cursor >= end:
                break
            index += 1
        if cursor < end:
            result.append((cursor, end))
    return result


def _length(intervals: list[Interval]) -> int:
    return sum(end - start for start, end in intervals)


def assess_coverage(
    detected: list[Interval] | None,
    recognized: list[Interval],
    duration_ms: int,
    *,
    detector_error: str | None = None,
    thresholds: CoverageThresholds = DEFAULT_THRESHOLDS,
) -> CoverageAssessment:
    """Route material detector disagreement to review; failures stay explicitly unknown."""
    recognition = merge_intervals(recognized, duration_ms)
    if detected is None or detector_error is not None:
        reason = detector_error or "independent speech evidence is unavailable"
        return CoverageAssessment(
            status="unknown",
            intervals=[],
            detected_speech_ms=0,
            recognized_span_ms=_length(recognition),
            overlap_ms=0,
            uncovered_speech_ms=0,
            uncovered_tail_ms=0,
            largest_uncovered_ms=0,
            recognition_outside_detector_ms=0,
            threshold_version=thresholds.version,
            warnings=[reason],
        )
    speech = merge_intervals(detected, duration_ms)
    if not speech and not recognition:
        return CoverageAssessment(
            status="unknown",
            intervals=[],
            detected_speech_ms=0,
            recognized_span_ms=0,
            overlap_ms=0,
            uncovered_speech_ms=0,
            uncovered_tail_ms=0,
            largest_uncovered_ms=0,
            recognition_outside_detector_ms=0,
            threshold_version=thresholds.version,
            warnings=["detector and recognition are both empty; this is not proof of silence"],
        )
    if not speech and recognition:
        return CoverageAssessment(
            status="unknown",
            intervals=[],
            detected_speech_ms=0,
            recognized_span_ms=_length(recognition),
            overlap_ms=0,
            uncovered_speech_ms=0,
            uncovered_tail_ms=0,
            largest_uncovered_ms=0,
            recognition_outside_detector_ms=_length(recognition),
            threshold_version=thresholds.version,
            warnings=["recognition contains speech but the independent detector found none"],
        )
    uncovered = subtract_intervals(speech, recognition)
    outside = subtract_intervals(recognition, speech)
    detected_ms = _length(speech)
    uncovered_ms = _length(uncovered)
    recognized_ms = _length(recognition)
    overlap_ms = detected_ms - uncovered_ms
    last_recognized = recognition[-1][1] if recognition else 0
    tail_ms = _length(
        [(max(start, last_recognized), end) for start, end in uncovered if end > last_recognized]
    )
    largest = max((end - start for start, end in uncovered), default=0)
    ratio = uncovered_ms / detected_ms if detected_ms else 0
    review = (
        (not recognition and detected_ms >= thresholds.empty_transcript_speech_ms)
        or largest >= thresholds.largest_uncovered_ms
        or uncovered_ms >= thresholds.total_uncovered_ms
        or (detected_ms > 0 and ratio >= thresholds.uncovered_ratio)
        or tail_ms >= thresholds.tail_uncovered_ms
    )
    warning = (
        "detector and recognition disagree materially; listen and review"
        if review
        else "clear means detector agreement only; it is not a transcription-accuracy claim"
    )
    return CoverageAssessment(
        status="needs_review" if review else "clear",
        intervals=speech,
        detected_speech_ms=detected_ms,
        recognized_span_ms=recognized_ms,
        overlap_ms=overlap_ms,
        uncovered_speech_ms=uncovered_ms,
        uncovered_tail_ms=tail_ms,
        largest_uncovered_ms=largest,
        recognition_outside_detector_ms=_length(outside),
        threshold_version=thresholds.version,
        warnings=[warning],
    )
