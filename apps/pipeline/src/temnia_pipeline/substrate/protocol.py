"""The seam every segmenter sits behind.

One method, one output shape. The eval runner holds a list of these and scores
them against the same gold with the same metrics, which is the whole point of
the S2 substrate decision: the legacy rules are one candidate in that list, not
the definition of the answer.

Implementations are constructed by
:func:`temnia_pipeline.substrate.factory.make_segmenter`, never imported by a
caller that wants to stay indifferent to which one it has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.grid import GridWord
    from temnia_pipeline.substrate.model import Layers


@runtime_checkable
class Segmenter(Protocol):
    """Words in, layers out.

    `shot_times_ms` is the shot grid the ingest wrote (`shots/shots.json`,
    already filtered to the file's own decision threshold). No segmenter here
    moves a boundary onto a shot (that is the legacy `shot_snap` backstop, and it
    belongs to the cut rather than to the substrate), but a segmenter may record
    the shots it was given in its provenance, and the fine rendering marks
    them, so the parameter rides through the seam rather than around it.
    """

    #: Stable, lowercase, the name `make_segmenter` and the eval runner use.
    name: str

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        """Build the layers for one word timeline."""
        ...
