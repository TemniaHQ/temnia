"""Display paragraphs, ported from `lib/transcription/paragraphs.ts`.

The legacy viewer's grouping: a paragraph never spans two speakers, never
bridges a long silence, and never grows past a size cap, because a row taller
than the pane cannot be scrolled to.

These are not the grid's paragraphs. `substrate.grid` tiles SENTENCES and never
splits one, which is what a rough pass addresses; these tile WORDS, which is
what a reader sees. They agree often and are not the same function, and the S2
plan keeps them as separate implementations on purpose. The web app has a third
(`apps/web/lib/transcript/paragraphs.ts`), built out of the turns so a speaker
chip can edit one; this is the legacy rule, which is what the parity fixtures
were dumped from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.grid import GridWord

# TS paragraphs.ts:16 — a new paragraph starts on a speaker change, a long
# silence, or when the current one grows past a size cap.
PARAGRAPH_GAP_MS = 2500
PARAGRAPH_MAX_WORDS = 120


@dataclass(slots=True)
class TranscriptParagraph:
    """One display paragraph (TS paragraphs.ts:19)."""

    end_ms: int
    speaker: str | None
    start_ms: int
    #: Index of `words[0]` in the flat transcript word array: the bridge
    #: between paragraph-local and transcript-global word indices.
    word_offset: int
    words: list[GridWord] = field(default_factory=list["GridWord"])


def _starts_new_paragraph(current: TranscriptParagraph | None, word: GridWord) -> bool:
    """TS paragraphs.ts:29."""
    if current is None or not current.words:
        return True
    previous = current.words[-1]
    return (
        word.speaker != current.speaker
        or word.startMs - previous.endMs > PARAGRAPH_GAP_MS
        or len(current.words) >= PARAGRAPH_MAX_WORDS
    )


def build_paragraphs(words: Sequence[GridWord]) -> list[TranscriptParagraph]:
    """Group a flat word array into display paragraphs (TS paragraphs.ts:47).

    The TypeScript takes the whole `TranscriptData`; it reads only the words,
    so this takes the words.
    """
    paragraphs: list[TranscriptParagraph] = []
    current: TranscriptParagraph | None = None
    for index, word in enumerate(words):
        if _starts_new_paragraph(current, word):
            current = TranscriptParagraph(
                end_ms=word.endMs,
                speaker=word.speaker,
                start_ms=word.startMs,
                word_offset=index,
                words=[word],
            )
            paragraphs.append(current)
        elif current is not None:
            current.words.append(word)
            current.end_ms = word.endMs
    return paragraphs


def find_word_index_at_time(words: Sequence[GridWord], time_ms: int) -> int:
    """The last word whose start is at or before `time_ms` (TS paragraphs.ts:75).

    Binary search; words are start-ordered by contract. -1 before the first
    word. A word deliberately stays active through the silence after it until
    the next word starts: a highlight that blinks off in every gap reads as
    flicker, not precision.
    """
    low = 0
    high = len(words) - 1
    result = -1
    while low <= high:
        mid = (low + high) // 2
        if words[mid].startMs <= time_ms:
            result = mid
            low = mid + 1
        else:
            high = mid - 1
    return result


def find_paragraph_index_for_word(
    paragraphs: Sequence[TranscriptParagraph], word_index: int
) -> int:
    """The paragraph holding a global word index (TS paragraphs.ts:99)."""
    if word_index < 0:
        return -1
    low = 0
    high = len(paragraphs) - 1
    result = -1
    while low <= high:
        mid = (low + high) // 2
        if paragraphs[mid].word_offset <= word_index:
            result = mid
            low = mid + 1
        else:
            high = mid - 1
    return result
