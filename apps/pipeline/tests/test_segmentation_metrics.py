"""The segmentation metrics, against nltk's published examples and by hand.

The three window metrics are nltk's, so the first thing asserted is that our
wrapper reproduces the examples in nltk's own docstrings exactly, including the
GHD ones from the Kulyukin implementation. Everything above them (the unit
grid, the tolerant matching, the densities, the tIoU convention) is ours and
is asserted on hand-built cases, with the edges the 360-degree view names: an
empty hypothesis, a single boundary, and boundaries sitting on the media's own
start and end.
"""

import random

import pytest

from temnia_pipeline.evals.nltk_metrics import ghd, pk, windowdiff
from temnia_pipeline.evals.segmentation import (
    TIOU_THRESHOLDS,
    boundary_string,
    density_per_hour,
    match_boundaries,
    score_boundaries,
    score_segments,
    segments_from_boundaries,
    temporal_iou,
    window_size,
)

SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
# One unit per second for an hour: a grid that makes the arithmetic readable.
UNITS = tuple(index * SECOND for index in range(3600))


def test_pk_matches_nltks_published_examples() -> None:
    assert f"{pk('0100' * 100, '1' * 400, 2):.2f}" == "0.50"
    assert f"{pk('0100' * 100, '0' * 400, 2):.2f}" == "0.50"
    assert f"{pk('0100' * 100, '0100' * 100, 2):.2f}" == "0.00"


def test_windowdiff_matches_nltks_published_examples() -> None:
    first, second, third = "000100000010", "000010000100", "100000010000"
    assert f"{windowdiff(first, first, 3):.2f}" == "0.00"
    assert f"{windowdiff(first, second, 3):.2f}" == "0.30"
    assert f"{windowdiff(second, third, 3):.2f}" == "0.80"


def test_ghd_matches_nltks_published_examples() -> None:
    assert ghd("1100100000", "1100010000", 1.0, 1.0, 0.5) == 0.5
    assert ghd("1100100000", "1100000001", 1.0, 1.0, 0.5) == 2.0
    assert ghd("011", "110", 1.0, 1.0, 0.5) == 1.0
    assert ghd("1", "0", 1.0, 1.0, 0.5) == 1.0
    assert ghd("111", "000", 1.0, 1.0, 0.5) == 3.0
    assert ghd("000", "111", 1.0, 2.0, 0.5) == 6.0


def test_a_boundary_becomes_the_unit_it_opens() -> None:
    units = (0, 1000, 2000, 3000, 4000)
    assert boundary_string(units, [2000]) == "01000"
    assert boundary_string(units, [1000, 4000]) == "10010"
    # Between two units it opens the next one, so it marks the one before it.
    assert boundary_string(units, [2400]) == "00100"


def test_the_media_edges_are_not_boundaries() -> None:
    units = (0, 1000, 2000)
    assert boundary_string(units, [0]) == "000"
    assert boundary_string(units, [-5000]) == "000"
    assert boundary_string(units, [9_000_000]) == "000"
    assert boundary_string((), [1000]) == ""


def test_the_window_is_half_the_mean_segment_length() -> None:
    assert window_size("0" * 100) == 50
    assert window_size("") == 1
    # The strings come from `boundary_string`, which marks internal cuts only,
    # so k marks are k + 1 segments. Three cuts in a hundred units are four
    # segments of 25: half is 12, Python rounding the tie to even.
    grid = UNITS[:100]
    assert window_size(boundary_string(grid, [25 * SECOND, 50 * SECOND, 75 * SECOND])) == 12
    # One cut in a hundred units: two segments of fifty, half is 25. Dividing
    # by the mark count, nltk's default for strings with a terminal mark,
    # answered 50 here and inflated every sparse chapter score (S2 review, I12).
    assert window_size(boundary_string(grid, [50 * SECOND])) == 25
    assert window_size(boundary_string(UNITS[:80], [40 * SECOND])) == 20


def test_the_matching_is_one_to_one_and_as_full_as_the_tolerance_allows() -> None:
    # Two hypotheses inside the tolerance of one reference: one is matched and
    # the other stays unmatched, so precision falls rather than recall rising.
    assert match_boundaries([100], [90, 130], 50) == ((0, 0),)
    assert match_boundaries([100, 200], [205, 105], 50) == ((0, 1), (1, 0))
    assert match_boundaries([100], [400], 50) == ()
    assert match_boundaries([], [100], 50) == ()
    assert match_boundaries([100], [], 50) == ()
    # Nearest pairs first paired 20 with 19 and stranded both others: one
    # match where two exist (S2 review, I13).
    assert match_boundaries([10_000, 20_000], [19_000, 29_000], 10_000) == ((0, 0), (1, 1))


def _largest_pairing(
    reference: list[int], hypothesis: list[int], tolerance: int, index: int = 0, used: int = 0
) -> int:
    """Every one-to-one pairing within tolerance, by brute force; the size of the largest."""
    if index == len(reference):
        return 0
    best = _largest_pairing(reference, hypothesis, tolerance, index + 1, used)
    for position, time in enumerate(hypothesis):
        if not used & (1 << position) and abs(reference[index] - time) <= tolerance:
            best = max(
                best,
                1
                + _largest_pairing(
                    reference, hypothesis, tolerance, index + 1, used | (1 << position)
                ),
            )
    return best


def test_the_matching_is_maximum_against_a_brute_force_oracle() -> None:
    generator = random.Random(7)  # noqa: S311 - a seeded sweep, not a secret
    for _ in range(400):
        reference = [generator.randint(0, 100) for _ in range(generator.randint(0, 6))]
        hypothesis = [generator.randint(0, 100) for _ in range(generator.randint(0, 6))]
        tolerance = generator.randint(0, 30)
        matched = match_boundaries(reference, hypothesis, tolerance)
        assert len(matched) == _largest_pairing(reference, hypothesis, tolerance)
        assert len({r for r, _ in matched}) == len(matched)
        assert len({h for _, h in matched}) == len(matched)
        assert all(abs(reference[r] - hypothesis[h]) <= tolerance for r, h in matched)


def test_a_perfect_hypothesis_scores_perfectly() -> None:
    reference = [10 * MINUTE, 20 * MINUTE, 30 * MINUTE]
    scores = score_boundaries(reference, list(reference), unit_starts_ms=UNITS, duration_ms=HOUR)
    assert scores.purity == 1.0
    assert scores.coverage == 1.0
    assert scores.wf1 == 1.0
    assert scores.pk == 0.0
    assert scores.windowdiff == 0.0
    assert scores.ghd == 0.0
    assert scores.reference_per_hour == 3.0
    assert scores.hypothesis_per_hour == 3.0


def test_an_empty_hypothesis_scores_zero_without_raising() -> None:
    scores = score_boundaries(
        [10 * MINUTE, 20 * MINUTE], [], unit_starts_ms=UNITS, duration_ms=HOUR
    )
    assert scores.hypothesis == 0
    assert scores.matched == 0
    assert scores.purity == 0.0
    assert scores.coverage == 0.0
    assert scores.wf1 == 0.0
    assert scores.hypothesis_per_hour == 0.0
    assert scores.pk > 0.0


def test_an_empty_reference_scores_without_raising() -> None:
    scores = score_boundaries([], [10 * MINUTE], unit_starts_ms=UNITS, duration_ms=HOUR)
    assert scores.reference == 0
    assert scores.coverage == 0.0
    assert scores.wf1 == 0.0
    assert scores.window == len(UNITS) // 2
    assert scores.windowdiff > 0.0


def test_a_single_boundary_slightly_off_is_tolerated_but_not_forgotten() -> None:
    inside = score_boundaries(
        [30 * MINUTE], [30 * MINUTE + 14 * SECOND], unit_starts_ms=UNITS, duration_ms=HOUR
    )
    assert inside.matched == 1
    assert inside.wf1 == 1.0
    assert inside.pk > 0.0, "the window metrics still see the shift"
    outside = score_boundaries(
        [30 * MINUTE], [30 * MINUTE + 16 * SECOND], unit_starts_ms=UNITS, duration_ms=HOUR
    )
    assert outside.matched == 0
    assert outside.wf1 == 0.0


def test_density_is_what_separates_a_dense_hypothesis_from_a_good_one() -> None:
    """The 'When F1 Fails' case: thirty an hour beats six on recall alone."""
    reference = [index * 10 * MINUTE for index in range(1, 6)]
    dense = [index * 2 * MINUTE for index in range(1, 30)]
    scores = score_boundaries(dense, reference, unit_starts_ms=UNITS, duration_ms=HOUR)
    generous = score_boundaries(reference, dense, unit_starts_ms=UNITS, duration_ms=HOUR)
    assert generous.coverage == 1.0, "every real boundary is proposed"
    assert generous.purity < 0.2, "and so is everything else"
    assert generous.hypothesis_per_hour == 29.0
    assert scores.reference_per_hour == 29.0


def test_density_of_nothing() -> None:
    assert density_per_hour(3, 0) == 0.0
    assert density_per_hour(0, HOUR) == 0.0


def test_temporal_iou_is_intersection_over_union() -> None:
    assert temporal_iou((0, 100), (0, 100)) == 1.0
    assert temporal_iou((0, 100), (50, 150)) == pytest.approx(50 / 150)
    assert temporal_iou((0, 100), (100, 200)) == 0.0
    assert temporal_iou((0, 100), (200, 300)) == 0.0


def test_segments_come_from_boundaries_bracketed_by_the_media() -> None:
    assert segments_from_boundaries([2000], start_ms=0, end_ms=5000) == ((0, 2000), (2000, 5000))
    assert segments_from_boundaries([], start_ms=0, end_ms=5000) == ((0, 5000),)
    # The edges and anything outside are not cuts, and a repeat is one cut.
    assert segments_from_boundaries([0, 5000, 9000, 2000, 2000], start_ms=0, end_ms=5000) == (
        (0, 2000),
        (2000, 5000),
    )
    assert segments_from_boundaries([1000], start_ms=5000, end_ms=5000) == ()


def test_identical_segments_score_one_at_every_threshold() -> None:
    spans = ((0, 10_000), (10_000, 25_000), (25_000, 40_000))
    scores = score_segments(spans, spans)
    assert scores.tiou_f1 == 1.0
    assert scores.mean_tiou == 1.0
    assert [threshold for threshold, _ in scores.per_threshold] == list(TIOU_THRESHOLDS)


def test_a_shifted_segmentation_loses_the_strict_thresholds_first() -> None:
    reference = ((0, 10_000), (10_000, 20_000))
    shifted = ((0, 11_000), (11_000, 20_000))
    scores = score_segments(reference, shifted)
    loose = dict(scores.per_threshold)
    assert loose[0.5] == 1.0
    assert loose[0.95] == 0.0
    assert 0.0 < scores.tiou_f1 < 1.0
    assert 0.8 < scores.mean_tiou < 1.0


def test_segments_that_do_not_overlap_at_all() -> None:
    scores = score_segments(((0, 10_000),), ((50_000, 60_000),))
    assert scores.tiou_f1 == 0.0
    assert scores.mean_tiou == 0.0
    assert score_segments((), ()).tiou_f1 == 0.0
