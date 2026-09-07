"""The rendering a model reads: one line per unit, with the address first.

This is the format S4's prompts quote, so it is specified here rather than
implied by the code.

**Coarse**, one line per paragraph::

    P042 01:14:02 S2: the paragraph's sentences, joined by single spaces

**Fine**, one line per sentence, with a fixed-width glyph column between the
speaker and the text so the text starts at the same offset on every line::

    s0417 01:14:02 S2: ¶|·⌖ §0.83 the sentence's words, joined by single spaces
    s0418 01:14:11 S2:            the next sentence, with nothing to mark

The address block is `s`, the zero-padded id, a space, `HH:MM:SS`, a space, the
speaker tag, and a colon. Then eleven characters of column:

===========  =====  ===================================================
Position     Glyph  Meaning
===========  =====  ===================================================
1            ``¶``  this sentence starts a paragraph
2            ``|``  this sentence opens a speaker turn
3            ``·``  the silence after it reaches the legacy pause
                    threshold (700 ms)
4            ``⌖``  a shot change lands within the snap window (500 ms)
                    of its start
6 to 10      ``§``  a topic candidate on this sentence, and its score to
                    two decimals
===========  =====  ===================================================

Every position is a space when its mark does not apply, so the columns line up
and a reader (or a regular expression) can find any of them by offset.

Three decisions worth stating.

*Ids are the coordinate, times are for the reader.* M1 failed at 18% on
boundaries the model named in free-form milliseconds, so the answer space is
the enumerated ids and `resolve_span` maps them back to exact word times.
`HH:MM:SS` is on every line because it is the format the fine-tuned chaptering
models were trained on, and which of the two a frontier model addresses better
is an S4 cassette experiment.

*The padding is the width the count needs, with the legacy's widths as the
floor.* Paragraph ids are at least three digits and sentence ids at least four,
so `P042` and `s0417` mean the same thing they meant in the legacy renderings,
and an episode with more units than that widens rather than wrapping around.

*The legacy renderers in `grid.py` are untouched.* They are byte-compared
against the frozen oracle; this is a different format for a different set of
layers, and the two are not asked to agree.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from temnia_pipeline.jsnum import to_fixed
from temnia_pipeline.substrate.grid import DEFAULT_PAUSE_GAP_MS, SHOT_SNAP_MS, speaker_tag

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.model import Layers

#: The legacy's widths, kept as a floor so `P042` and `s0417` still read the
#: way they read in the TypeScript renderings.
MIN_PARAGRAPH_DIGITS = 3
MIN_SENTENCE_DIGITS = 4

PARAGRAPH_GLYPH = "¶"
TURN_GLYPH = "|"
PAUSE_GLYPH = "·"
SHOT_GLYPH = "⌖"
TOPIC_GLYPH = "§"

#: `§0.83` is five characters, and so is `§1.00`.
TOPIC_WIDTH = 5


def stamp_hms(ms: int) -> str:
    """`HH:MM:SS`, hours zero-padded to two and unbounded above.

    Truncated, not rounded: the stamp names the second the unit starts in, and
    a stamp a second later than the id it labels would be a lie a reader could
    act on.
    """
    total_seconds = math.floor(max(ms, 0) / 1000)
    return f"{total_seconds // 3600:02d}:{total_seconds // 60 % 60:02d}:{total_seconds % 60:02d}"


def id_width(count: int, minimum: int) -> int:
    """Digits enough for the largest id, never fewer than the legacy's."""
    return max(minimum, len(str(max(count - 1, 0))))


def render_coarse(layers: Layers) -> str:
    """One line per paragraph. No trailing newline; the empty layers render empty."""
    width = id_width(len(layers.paragraphs), MIN_PARAGRAPH_DIGITS)
    lines: list[str] = []
    for paragraph in layers.paragraphs:
        text = " ".join(
            sentence.text
            for sentence in layers.sentences[paragraph.sentence_start : paragraph.sentence_end + 1]
        )
        lines.append(
            f"P{paragraph.id:0{width}d} {stamp_hms(paragraph.start_ms)}"
            f" {speaker_tag(paragraph.speaker)}: {text}"
        )
    return "\n".join(lines)


def render_fine(
    layers: Layers,
    shot_times_ms: Sequence[int] = (),
    from_sentence: int = 0,
    to_sentence: int | None = None,
) -> str:
    """One line per sentence, with the glyph column. Bounds default to everything.

    The bounds are here because the Cutter reads one window at a time and an
    episode's fine rendering is tens of thousands of lines; the ids do not
    change with the window, which is the point of them.
    """
    last = len(layers.sentences) - 1 if to_sentence is None else to_sentence
    width = id_width(len(layers.sentences), MIN_SENTENCE_DIGITS)
    starts = {paragraph.sentence_start for paragraph in layers.paragraphs}
    topics = {
        candidate.sentence_id: candidate.score
        for candidate in layers.candidates
        if candidate.kind == "topic"
    }
    lines: list[str] = []
    for id_ in range(from_sentence, last + 1):
        if not 0 <= id_ < len(layers.sentences):
            continue
        sentence = layers.sentences[id_]
        previous = layers.sentences[id_ - 1] if id_ > 0 else None
        following = layers.sentences[id_ + 1] if id_ + 1 < len(layers.sentences) else None
        pause_after_ms = max(0, following.start_ms - sentence.end_ms) if following else 0
        glyphs = "".join(
            (
                PARAGRAPH_GLYPH if id_ in starts else " ",
                TURN_GLYPH if previous is None or previous.speaker != sentence.speaker else " ",
                PAUSE_GLYPH if pause_after_ms >= DEFAULT_PAUSE_GAP_MS else " ",
                SHOT_GLYPH
                if any(abs(shot - sentence.start_ms) <= SHOT_SNAP_MS for shot in shot_times_ms)
                else " ",
            )
        )
        score = topics.get(id_)
        topic = f"{TOPIC_GLYPH}{to_fixed(score, 2)}" if score is not None else " " * TOPIC_WIDTH
        lines.append(
            f"s{sentence.id:0{width}d} {stamp_hms(sentence.start_ms)}"
            f" {speaker_tag(sentence.speaker)}: {glyphs} {topic} {sentence.text}"
        )
    return "\n".join(lines)
