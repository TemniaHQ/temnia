"""Scoring a segmentation: how close a hypothesis is to a reference.

Pure functions. Nothing here loads a model or reads a file; the runner feeds it
millisecond boundaries and gets numbers back.

**Why more than F1.** "When F1 Fails" (arXiv 2512.17083) shows that boundary F1
on dialogue segmentation tracks boundary *density* more than boundary quality:
a segmenter that proposes thirty boundaries an hour beats one that proposes six
on F1 while being worse at the job. So every row here carries the density of
both sides, and the tolerant precision and recall are reported under their own
names (`purity` for precision, `coverage` for recall), so a lopsided pair is
visible rather than averaged away.

**Why Pk and WindowDiff as well.** Both are window metrics and tolerate a
boundary that is nearly right, which plain F1 does not. Pk under-penalises a
missed boundary and WindowDiff corrects for that, so the pair is reported
rather than either alone. Both are computed on a *unit grid* the caller
supplies.

**The unit grid, and why the caller chooses it.** Pk and WindowDiff need both
sides expressed over the same sequence of units. Sentences are the obvious
choice and the wrong one here, because every segmenter has its own sentences
and two rows would not be comparable. The runner passes the word start times:
the words are the one grid every segmenter in a comparison shares, so the
numbers in a table mean the same thing in every row.

**The window-tolerant matching** pairs each reference boundary with at most one
hypothesis boundary within the tolerance, and pairs as many as the tolerance
allows: every reference is an interval on the line and every hypothesis a
point, and the earliest-deadline greedy over intervals is optimal in the
number matched. Nearest-first was not (S2 review, I13).

**Segments, not boundaries.** `score_segments` is the VidChapters convention:
F1 over temporal IoU thresholds 0.5 to 0.95 in steps of 0.05, averaged, plus
the mean IoU of the pairs that match at all. It answers a different question
from the boundary metrics: whether the *spans* line up, not whether the cut
points do. A chapter cut is judged on both.
"""

from __future__ import annotations

import bisect
import heapq
from dataclasses import dataclass
from typing import TYPE_CHECKING

from temnia_pipeline.evals.nltk_metrics import BOUNDARY, ghd, pk, windowdiff

if TYPE_CHECKING:
    from collections.abc import Sequence

MS_PER_HOUR = 3_600_000

#: Fifteen seconds. A chapter boundary a viewer would not notice being moved by
#: that much is the same boundary; the legacy's own review UI nudged at this
#: scale. It is a parameter because a moment lane would want a much smaller one.
DEFAULT_TOLERANCE_MS = 15_000

#: The VidChapters convention.
TIOU_THRESHOLDS = tuple(round(0.5 + 0.05 * step, 2) for step in range(10))

Segment = tuple[int, int]


@dataclass(frozen=True, slots=True)
class BoundaryScores:
    """One segmenter's boundaries against one reference."""

    units: int
    reference: int
    hypothesis: int
    matched: int
    #: Window-tolerant precision: the share of proposed boundaries that are real.
    purity: float
    #: Window-tolerant recall: the share of real boundaries that were proposed.
    coverage: float
    wf1: float
    pk: float
    windowdiff: float
    ghd: float
    window: int
    reference_per_hour: float
    hypothesis_per_hour: float
    tolerance_ms: int


@dataclass(frozen=True, slots=True)
class SegmentScores:
    """One segmenter's spans against a reference's spans."""

    reference: int
    hypothesis: int
    #: F1 averaged over the ten tIoU thresholds.
    tiou_f1: float
    #: Mean tIoU of the pairs that overlap at all, under the same greedy match.
    mean_tiou: float
    per_threshold: tuple[tuple[float, float], ...]


def density_per_hour(count: int, duration_ms: int) -> float:
    """Boundaries an hour. Zero-length media has no density rather than an error."""
    if duration_ms <= 0:
        return 0.0
    return count * MS_PER_HOUR / duration_ms


def boundary_string(unit_starts_ms: Sequence[int], boundaries_ms: Sequence[int]) -> str:
    """The nltk segmentation string over a unit grid.

    A boundary in milliseconds becomes the unit it opens, and the character set
    is the one *before* it, because nltk's convention marks the unit a segment
    ends at. A boundary before the first unit or after the last opens nothing
    and is dropped: the start and the end of the media are not cuts.
    """
    units = len(unit_starts_ms)
    if units == 0:
        return ""
    marks = [False] * units
    for ms in boundaries_ms:
        index = bisect.bisect_left(unit_starts_ms, ms)
        if 1 <= index <= units - 1:
            marks[index - 1] = True
    return "".join(BOUNDARY if mark else "0" for mark in marks)


def window_size(reference: str) -> int:
    """Half the mean segment length of the reference: the Pk paper's window.

    The string marks internal cuts only (`boundary_string` drops the media's
    edges), so a reference with k marks has k + 1 segments. nltk's default
    divides by the mark count because its strings carry a terminal mark;
    applied to these strings it doubled the window of a one-cut reference and
    inflated every sparse chapter score (S2 review, I12). A reference with no
    marks is one segment, and the window is half the episode.
    """
    units = len(reference)
    if units == 0:
        return 1
    segments = reference.count(BOUNDARY) + 1
    return max(1, min(units, round(units / (2 * segments))))


def match_boundaries(
    reference_ms: Sequence[int], hypothesis_ms: Sequence[int], tolerance_ms: int
) -> tuple[tuple[int, int], ...]:
    """Pair each reference boundary with at most one hypothesis boundary.

    As many pairs as the tolerance allows. Every reference is an interval on
    the line (its time plus or minus the tolerance) and every hypothesis a
    point; walking the hypotheses in time order and giving each to the open
    interval that closes soonest is the greedy that is optimal for intervals
    on a line. Nearest pairs first is not: with references at 10 and 20
    seconds, hypotheses at 19 and 29, and a ten-second tolerance it paired 20
    with 19 and left both others alone, one match where two exist (S2 review,
    I13). Ties break by reference index and then hypothesis index, so the
    answer is deterministic.
    """
    references = sorted((time, index) for index, time in enumerate(reference_ms))
    hypotheses = sorted((time, index) for index, time in enumerate(hypothesis_ms))
    # Intervals open and not yet used: (the time they close, reference index).
    opening: list[tuple[int, int]] = []
    matched: list[tuple[int, int]] = []
    next_reference = 0
    for time, hypothesis_index in hypotheses:
        while (
            next_reference < len(references)
            and references[next_reference][0] - tolerance_ms <= time
        ):
            reference_time, reference_index = references[next_reference]
            heapq.heappush(opening, (reference_time + tolerance_ms, reference_index))
            next_reference += 1
        while opening and opening[0][0] < time:
            heapq.heappop(opening)
        if opening:
            _, reference_index = heapq.heappop(opening)
            matched.append((reference_index, hypothesis_index))
    return tuple(sorted(matched))


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score_boundaries(
    reference_ms: Sequence[int],
    hypothesis_ms: Sequence[int],
    *,
    unit_starts_ms: Sequence[int],
    duration_ms: int,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> BoundaryScores:
    """Every boundary metric for one hypothesis against one reference."""
    reference_string = boundary_string(unit_starts_ms, reference_ms)
    hypothesis_string = boundary_string(unit_starts_ms, hypothesis_ms)
    window = window_size(reference_string)
    matched = match_boundaries(reference_ms, hypothesis_ms, tolerance_ms)
    purity = len(matched) / len(hypothesis_ms) if hypothesis_ms else 0.0
    coverage = len(matched) / len(reference_ms) if reference_ms else 0.0
    units = len(reference_string)
    return BoundaryScores(
        units=units,
        reference=len(reference_ms),
        hypothesis=len(hypothesis_ms),
        matched=len(matched),
        purity=purity,
        coverage=coverage,
        wf1=_f1(purity, coverage),
        pk=pk(reference_string, hypothesis_string, window) if units else 0.0,
        windowdiff=windowdiff(reference_string, hypothesis_string, window) if units else 0.0,
        ghd=ghd(reference_string, hypothesis_string) if units else 0.0,
        window=window,
        reference_per_hour=density_per_hour(len(reference_ms), duration_ms),
        hypothesis_per_hour=density_per_hour(len(hypothesis_ms), duration_ms),
        tolerance_ms=tolerance_ms,
    )


def temporal_iou(first: Segment, second: Segment) -> float:
    """Intersection over union of two spans; 0 when they do not overlap."""
    overlap = min(first[1], second[1]) - max(first[0], second[0])
    if overlap <= 0:
        return 0.0
    union = max(first[1], second[1]) - min(first[0], second[0])
    return overlap / union if union > 0 else 0.0


def _greedy_pairs(
    reference: Sequence[Segment], hypothesis: Sequence[Segment], threshold: float
) -> list[float]:
    """The tIoU of each matched pair, greedily by descending overlap."""
    candidates = sorted(
        (
            (-temporal_iou(one, other), reference_index, hypothesis_index)
            for reference_index, one in enumerate(reference)
            for hypothesis_index, other in enumerate(hypothesis)
            if temporal_iou(one, other) >= threshold
        )
    )
    used_reference: set[int] = set()
    used_hypothesis: set[int] = set()
    scores: list[float] = []
    for negative, reference_index, hypothesis_index in candidates:
        if reference_index in used_reference or hypothesis_index in used_hypothesis:
            continue
        used_reference.add(reference_index)
        used_hypothesis.add(hypothesis_index)
        scores.append(-negative)
    return scores


def score_segments(reference: Sequence[Segment], hypothesis: Sequence[Segment]) -> SegmentScores:
    """F1 averaged over tIoU 0.5 to 0.95, and the mean tIoU of overlapping pairs."""
    per_threshold: list[tuple[float, float]] = []
    for threshold in TIOU_THRESHOLDS:
        matched = len(_greedy_pairs(reference, hypothesis, threshold))
        precision = matched / len(hypothesis) if hypothesis else 0.0
        recall = matched / len(reference) if reference else 0.0
        per_threshold.append((threshold, _f1(precision, recall)))
    overlapping = _greedy_pairs(reference, hypothesis, 1e-9)
    return SegmentScores(
        reference=len(reference),
        hypothesis=len(hypothesis),
        tiou_f1=sum(score for _, score in per_threshold) / len(per_threshold),
        mean_tiou=sum(overlapping) / len(overlapping) if overlapping else 0.0,
        per_threshold=tuple(per_threshold),
    )


def segments_from_boundaries(
    boundaries_ms: Sequence[int], *, start_ms: int, end_ms: int
) -> tuple[Segment, ...]:
    """Turn cut points into the spans they cut, clipped to the media.

    The media's own start and end are not boundaries, so they bracket the list
    rather than appearing in it. A boundary outside the media is dropped, and
    duplicates collapse: a span of zero length is not a chapter.
    """
    if end_ms <= start_ms:
        return ()
    inside = sorted({ms for ms in boundaries_ms if start_ms < ms < end_ms})
    edges = [start_ms, *inside, end_ms]
    return tuple((edges[index], edges[index + 1]) for index in range(len(edges) - 1))
