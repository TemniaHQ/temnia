"""Three segmentation metrics adapted from NLTK 3.10.3.

This is a narrow adaptation of ``nltk.metrics.segmentation`` from NLTK 3.10.3
(source SHA-256 ``6455fea9e1880aee0951c3c76927d0307d2ea48f110ccaaee2a7e81382d5d18e``).
Copyright (C) 2001-2026 NLTK Project, licensed under Apache License 2.0; see
``apps/pipeline/THIRD_PARTY_NOTICES.md`` and ``apps/pipeline/LICENSES/NLTK-3.10.3.txt``.

Temnia changed the public surface to typed binary strings with explicit window
sizes, rejects invalid costs and bounds, keeps Pk/WindowDiff boundary counts
incremental, and stores only two GHD dynamic-programming rows. No NLTK model,
data, downloader, classifier, or package-initialization code is included.
"""

from __future__ import annotations

import math
from typing import NoReturn

BOUNDARY = "1"
_UNITS = frozenset({"0", BOUNDARY})


class SegmentationInputError(ValueError):
    """One metric input lies outside the frozen binary-string contract."""


def _invalid(message: str) -> NoReturn:
    raise SegmentationInputError(message)


def _validate_pair(reference: object, hypothesis: object) -> tuple[str, str]:
    if not isinstance(reference, str) or not isinstance(hypothesis, str):
        _invalid("segmentations must be strings")
    if len(reference) != len(hypothesis):
        _invalid("segmentations have unequal length")
    if not set(reference).issubset(_UNITS) or not set(hypothesis).issubset(_UNITS):
        _invalid("segmentations must contain only '0' and '1'")
    return reference, hypothesis


def _validate_window(window: object, length: int) -> int:
    if isinstance(window, bool) or not isinstance(window, int):
        _invalid("window width must be an integer")
    if not 1 <= window <= length:
        _invalid("window width must be between 1 and the segmentation length")
    return window


def pk(reference: str, hypothesis: str, window: int) -> float:
    """Return Beeferman's Pk disagreement rate for one explicit window."""
    reference, hypothesis = _validate_pair(reference, hypothesis)
    window = _validate_window(window, len(reference))
    errors = 0
    reference_count = reference[:window].count(BOUNDARY)
    hypothesis_count = hypothesis[:window].count(BOUNDARY)
    for index in range(len(reference) - window + 1):
        if index > 0:
            entering = index + window - 1
            reference_count += (reference[entering] == BOUNDARY) - (
                reference[index - 1] == BOUNDARY
            )
            hypothesis_count += (hypothesis[entering] == BOUNDARY) - (
                hypothesis[index - 1] == BOUNDARY
            )
        if (reference_count > 0) != (hypothesis_count > 0):
            errors += 1
    return errors / (len(reference) - window + 1.0)


def windowdiff(reference: str, hypothesis: str, window: int) -> float:
    """Return Pevzner and Hearst's unweighted WindowDiff rate."""
    reference, hypothesis = _validate_pair(reference, hypothesis)
    window = _validate_window(window, len(reference))
    errors = 0
    reference_count = reference[:window].count(BOUNDARY)
    hypothesis_count = hypothesis[:window].count(BOUNDARY)
    for index in range(len(reference) - window + 1):
        if index > 0:
            entering = index + window - 1
            reference_count += (reference[entering] == BOUNDARY) - (
                reference[index - 1] == BOUNDARY
            )
            hypothesis_count += (hypothesis[entering] == BOUNDARY) - (
                hypothesis[index - 1] == BOUNDARY
            )
        errors += min(1, abs(reference_count - hypothesis_count))
    return errors / (len(reference) - window + 1.0)


GHD_INSERT_COST = 2.0
GHD_DELETE_COST = 2.0
GHD_SHIFT_COEFFICIENT = 1.0


def _cost(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _invalid(f"{name} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        _invalid(f"{name} must be a finite nonnegative number")
    return result


def ghd(
    reference: str,
    hypothesis: str,
    insert_cost: float = GHD_INSERT_COST,
    delete_cost: float = GHD_DELETE_COST,
    shift_coefficient: float = GHD_SHIFT_COEFFICIENT,
) -> float:
    """Return Generalized Hamming Distance using two DP rows.

    The recurrence transforms hypothesis boundaries into reference boundaries
    with insertion, deletion, or a position-weighted shift, matching NLTK's
    row/column orientation and asymmetric-cost behavior.
    """
    reference, hypothesis = _validate_pair(reference, hypothesis)
    insertion = _cost(insert_cost, "insert cost")
    deletion = _cost(delete_cost, "delete cost")
    shift = _cost(shift_coefficient, "shift coefficient")
    reference_boundaries = [index for index, value in enumerate(reference) if value == BOUNDARY]
    hypothesis_boundaries = [index for index, value in enumerate(hypothesis) if value == BOUNDARY]
    if not reference_boundaries:
        return len(hypothesis_boundaries) * deletion
    if not hypothesis_boundaries:
        return len(reference_boundaries) * insertion

    previous = [insertion * index for index in range(len(reference_boundaries) + 1)]
    for row_index, hypothesis_position in enumerate(hypothesis_boundaries, start=1):
        current = [deletion * row_index, *([0.0] * len(reference_boundaries))]
        for column_index, reference_position in enumerate(reference_boundaries, start=1):
            shift_cost = (
                shift * abs(hypothesis_position - reference_position) + previous[column_index - 1]
            )
            if hypothesis_position == reference_position:
                transform_cost = previous[column_index - 1]
            elif hypothesis_position > reference_position:
                transform_cost = deletion + previous[column_index]
            else:
                transform_cost = insertion + current[column_index - 1]
            current[column_index] = min(transform_cost, shift_cost)
        previous = current
    return previous[-1]
