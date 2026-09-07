# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""`nltk.metrics.segmentation` behind a typed surface.

nltk ships no type information and the pipeline is on pyright strict, so the
three segmentation metrics are called from here and nowhere else and the
suppressions are at the top of this one small file rather than scattered
through the module that uses them. Everything below returns a plain `float`.

Why nltk and not `segeval`. `segeval` is the reference implementation of these
metrics and has been unmaintained since 2013; nltk 3.10 carries the same three
and is maintained. The numbers are checked against the published examples in
`tests/test_segmentation_metrics.py`, so the choice of library is a
maintenance decision and not a numerical one.

The strings are nltk's convention: one character per unit, `1` where a segment
ends at that unit and `0` elsewhere.
"""

from __future__ import annotations

from nltk.metrics import segmentation

BOUNDARY = "1"


def pk(reference: str, hypothesis: str, window: int) -> float:
    """Beeferman's Pk: how often a randomly placed window disagrees.

    Lower is better and 0 is agreement. `window` is passed explicitly rather
    than left to nltk's default, which divides by the reference's boundary
    count and so raises on a reference that has none.
    """
    return float(segmentation.pk(reference, hypothesis, k=window, boundary=BOUNDARY))


def windowdiff(reference: str, hypothesis: str, window: int) -> float:
    """Pevzner and Hearst's WindowDiff. Lower is better; 0 is agreement.

    Pk's known weakness is that it under-penalises a missed boundary.
    WindowDiff compares the number of boundaries inside the window instead,
    which is why both are reported rather than one of them.
    """
    return float(segmentation.windowdiff(reference, hypothesis, window, boundary=BOUNDARY))


#: nltk's own signature defaults. Its docstring recommends a shift coefficient
#: of 2 with insertion and deletion set to the reference's mean segment length,
#: which is data-dependent; the runner reports GHD on one source at a time and
#: uses the fixed defaults so two rows are comparable.
GHD_INSERT_COST = 2.0
GHD_DELETE_COST = 2.0
GHD_SHIFT_COEFFICIENT = 1.0


def ghd(
    reference: str,
    hypothesis: str,
    insert_cost: float = GHD_INSERT_COST,
    delete_cost: float = GHD_DELETE_COST,
    shift_coefficient: float = GHD_SHIFT_COEFFICIENT,
) -> float:
    """Generalized Hamming Distance: insertions, deletions and shifts, costed.

    The cost of turning the hypothesis into the reference. Unlike Pk and
    WindowDiff this is a distance rather than a rate, so it grows with the
    length of the episode and is comparable only between rows on one source.
    The costs are parameters because nltk's published examples are computed
    with a different set, and the tests check those.
    """
    return float(
        segmentation.ghd(
            reference,
            hypothesis,
            insert_cost,
            delete_cost,
            shift_coefficient,
            boundary=BOUNDARY,
        )
    )
