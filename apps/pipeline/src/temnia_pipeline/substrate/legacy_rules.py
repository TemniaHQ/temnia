"""The legacy rules as one segmenter among several: the baseline to beat.

Nothing here changes what the port does. `grid.py` and `paragraphs.py` stay
byte-parity tested against the frozen oracle in `tools/legacy-reference/`, and
this module only re-dresses `build_cut_grid`'s answer in the shapes of
`model.py` so the eval runner can score it beside the model-based segmenters.
That is the S2 decision: the legacy behaviour is a candidate with a number, not
the definition of the answer.

Two things are worth reading twice.

`pack_paragraphs` is the legacy paragraph rule (speaker change, a gap over
2500 ms, a 120-word cap, and never mid-sentence) stated over
:class:`~.model.Sentence` instead of over the port's `GridSentence`, because
`SaTSegmenter` needs the same rule over sentences the port never saw. It is a
second statement of one rule, so `tests/test_substrate_layers.py` pins it to
`build_cut_grid`'s own packing on every fixture; drift fails the gate.

The candidates are the paragraph starts, and a paragraph that broke only on
the word cap produces no candidate. The cap is a size rule for a reader, not a
place the episode turns, and a boundary proposed there would be scored against
gold as if the rule had meant something.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from temnia_pipeline.substrate.grid import (
    PARAGRAPH_GAP_MS,
    PARAGRAPH_MAX_WORDS,
    build_cut_grid,
)
from temnia_pipeline.substrate.model import (
    BoundaryCandidate,
    Layers,
    Paragraph,
    Provenance,
    Sentence,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.grid import GridSentence, GridWord

#: The commit `tools/legacy-reference/` was copied at. Recorded in provenance
#: so a baseline row names the exact behaviour it is a baseline of.
LEGACY_ORACLE_COMMIT = "b642b77"

#: A mechanical rule either fired or it did not; there is nothing to rank.
RULE_SCORE = 1.0


def sentence_from_grid(sentence: GridSentence) -> Sentence:
    """One `GridSentence` in the shared shape."""
    return Sentence(
        id=sentence.id,
        start_ms=sentence.start_ms,
        end_ms=sentence.end_ms,
        word_start=sentence.start_word,
        word_end=sentence.end_word,
        speaker=sentence.speaker,
        text=sentence.text,
        is_question=sentence.question,
    )


@dataclass(frozen=True, slots=True)
class PackedParagraphs:
    """What the paragraph rule produced, and where it said the episode turns."""

    paragraphs: tuple[Paragraph, ...]
    candidates: tuple[BoundaryCandidate, ...]


def pack_paragraphs(
    sentences: Sequence[Sentence], *, whole_extent: bool = False
) -> PackedParagraphs:
    """Group whole sentences into paragraphs by the legacy rule.

    A paragraph breaks on a speaker change, on a gap longer than
    `PARAGRAPH_GAP_MS`, or when the next sentence would take it past
    `PARAGRAPH_MAX_WORDS`. The first two are boundary candidates (`turn` and
    `pause`, speaker change first when both fired); the third is not, and the
    module docstring says why. The first paragraph opens the media rather than
    cutting it, so it is never a candidate either.

    `whole_extent` ends a paragraph at the latest end of any sentence in it
    rather than at its last sentence's end, which differ when speech overlaps.
    The legacy took the last sentence's end and its parity oracle records
    that, so the legacy segmenter keeps the default; the SaT segmenter, which
    is production, asks for the whole extent (S2 review, I11).
    """
    paragraphs: list[Paragraph] = []
    candidates: list[BoundaryCandidate] = []
    start = 0
    words_in_paragraph = 0

    def close(end: int) -> None:
        paragraphs.append(
            Paragraph(
                id=len(paragraphs),
                start_ms=sentences[start].start_ms,
                end_ms=(
                    max(sentence.end_ms for sentence in sentences[start : end + 1])
                    if whole_extent
                    else sentences[end].end_ms
                ),
                sentence_start=start,
                sentence_end=end,
                speaker=sentences[start].speaker,
            )
        )

    for index, sentence in enumerate(sentences):
        length = sentence.word_end - sentence.word_start + 1
        if index == 0:
            words_in_paragraph = length
            continue
        previous = sentences[index - 1]
        turn = sentence.speaker != sentences[start].speaker
        pause = sentence.start_ms - previous.end_ms > PARAGRAPH_GAP_MS
        if not (turn or pause or words_in_paragraph + length > PARAGRAPH_MAX_WORDS):
            words_in_paragraph += length
            continue
        close(index - 1)
        if turn or pause:
            candidates.append(
                BoundaryCandidate(
                    sentence_id=sentence.id,
                    ms=sentence.start_ms,
                    score=RULE_SCORE,
                    kind="turn" if turn else "pause",
                )
            )
        start = index
        words_in_paragraph = length

    if sentences:
        close(len(sentences) - 1)
    return PackedParagraphs(paragraphs=tuple(paragraphs), candidates=tuple(candidates))


class LegacyRulesSegmenter:
    """`build_cut_grid`, in the shared shape. The scored baseline."""

    name = "legacy"

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        """Sentences from Whisper's punctuation, paragraphs from the three rules."""
        grid = build_cut_grid(words)
        sentences = tuple(sentence_from_grid(sentence) for sentence in grid.sentences)
        packed = pack_paragraphs(sentences)
        return Layers(
            words=words,
            sentences=sentences,
            paragraphs=packed.paragraphs,
            candidates=packed.candidates,
            provenance=Provenance(
                segmenter=self.name,
                params={
                    "paragraph_gap_ms": PARAGRAPH_GAP_MS,
                    "paragraph_max_words": PARAGRAPH_MAX_WORDS,
                    "shots": len(shot_times_ms),
                },
                versions={"legacy_oracle": LEGACY_ORACLE_COMMIT},
            ),
        )
