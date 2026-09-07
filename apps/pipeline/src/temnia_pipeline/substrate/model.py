"""One output shape for every segmenter.

The substrate is the addressable timeline the harness cuts from, and S2's
design decision (AGENTS.md, 2026-09-07) is that several implementations may
produce it: the legacy rules, Segment-any-Text, embedding change-point
detection, and whatever scores better later. They all answer in the shapes
here, so the renderer, the eval runner and S4's prompts are written once.

Three layers over the word timeline that `TranscriptV1` already carries:

- :class:`Sentence`, the fine unit, the Cutter's coordinate system.
- :class:`Paragraph`, the medium unit, a run of whole sentences.
- :class:`BoundaryCandidate`, a proposed cut with a score and the reason it
  was proposed, which is what the S4 Director reads instead of guessing.

Ids are 0-based integers everywhere; the zero padding that makes them look
like `P042` and `s0417` belongs to :mod:`temnia_pipeline.substrate.render`,
because a number is what a metric and a lookup want.

Times are integer milliseconds, the contract's unit. A layer never invents a
time: every one of them is some word's `startMs` or `endMs`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from temnia_pipeline.substrate.grid import GridWord

#: Why a boundary was proposed. `turn` and `pause` are the mechanical reasons
#: the legacy rules give; `topic` is a semantic one and is the only kind that
#: carries a meaningful score.
BoundaryKind = Literal["topic", "turn", "pause"]


@dataclass(frozen=True, slots=True)
class Sentence:
    """One sentence: a contiguous run of words, addressed by `id`.

    `word_start` and `word_end` are inclusive indices into the same word list
    the layers were built from, which is what lets a caller go from an id back
    to exact times without trusting the text.
    """

    id: int
    start_ms: int
    end_ms: int
    word_start: int
    word_end: int
    speaker: str | None
    text: str
    is_question: bool


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A run of whole sentences, addressed by `id`.

    Sentence-indexed rather than word-indexed on purpose: a paragraph that
    could split a sentence would let a rough pass propose a cut the fine pass
    cannot address.
    """

    id: int
    start_ms: int
    end_ms: int
    sentence_start: int
    sentence_end: int
    speaker: str | None


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    """A proposed cut point: where, how good, and why.

    `ms` is the sentence's own start, so a candidate is always a time the
    timeline can actually be addressed at. `score` is in 0..1 and is only
    comparable within one segmenter's own list: the mechanical kinds score 1.0
    because the rule either fired or did not.
    """

    sentence_id: int
    ms: int
    score: float
    kind: BoundaryKind


@dataclass(frozen=True, slots=True)
class Provenance:
    """What produced a set of layers, in enough detail to reproduce it.

    Printed by the eval runner beside every row, because a metric without the
    model and the parameters that produced it is not evidence.
    """

    segmenter: str
    models: Mapping[str, str] = field(default_factory=dict[str, str])
    params: Mapping[str, object] = field(default_factory=dict[str, object])
    versions: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class Layers:
    """The substrate over one word timeline.

    An empty transcript gives empty layers, never an error: the 360-degree view
    for S2 says so, and every consumer here tolerates it.
    """

    words: Sequence[GridWord]
    sentences: tuple[Sentence, ...]
    paragraphs: tuple[Paragraph, ...]
    candidates: tuple[BoundaryCandidate, ...]
    provenance: Provenance

    def boundaries_ms(self) -> tuple[int, ...]:
        """The candidate times, ascending and de-duplicated.

        The hypothesis a segmenter is scored on. Two candidates can share a
        time when two rules fire on one sentence; a boundary is a place, so the
        duplicate is dropped rather than counted twice against the density.
        """
        return tuple(sorted({candidate.ms for candidate in self.candidates}))

    def sentence_starts_ms(self) -> tuple[int, ...]:
        """Every sentence's start, ascending: the fine unit grid."""
        return tuple(sentence.start_ms for sentence in self.sentences)
