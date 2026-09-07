"""The sentence and paragraph grid, ported from `lib/intelligence/grid.ts`.

The addressable timeline every clip pass selects from: the model never emits a
millisecond, it selects enumerated sentence and paragraph ids from the
renderings built here, and `resolve_span` maps ids back to exact word times.

Two granularities, as in the TypeScript:

- SENTENCES (fine) are the Cutter's coordinate system, each carrying turn,
  question, pause and shot annotations so the narrative backstops can be
  semantic;
- PARAGRAPHS (coarse) are the rough passes' coordinate system, so a chapter cut
  proposed at paragraph altitude resists beat-slicing by construction.

Every function names the TypeScript function it mirrors and the line it sits at
in the frozen copy under `tools/legacy-reference/lib/`. Where the two languages
disagree about arithmetic (rounding, string-to-number, `toFixed`) the JavaScript
answer is the right one and comes from `temnia_pipeline.jsnum`: the renderings
are compared byte for byte against that copy's own output.

Words arrive as `TranscriptWord`, the generated contract model, so the fields
read here are the wire's camelCase. Everything this module defines is
snake_case; `tests/test_substrate_parity.py` converts at the boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from temnia_pipeline.jsnum import is_integer, js_number, js_round, to_fixed

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


class GridWord(Protocol):
    """The word shape the substrate reads: `TranscriptWord`, structurally.

    A protocol rather than the model itself so the eval scorers can pass the
    snapshot's own word objects through the same helpers. The names are the
    contract's camelCase, which is why this module carries an `N815` exemption.
    """

    endMs: int  # noqa: N815
    speaker: str | None
    startMs: int  # noqa: N815
    text: str


# TS moments.ts:36 — /[.!?]["”'’)\]]*$/u, anchored at the true end of the
# string, so `\Z` and not `$` (Python's `$` also matches before a newline).
SENTENCE_TERMINAL = re.compile(r'[.!?]["”\'’)\]]*\Z')

# TS moments.ts:39 — an inter-word gap this long reads as a pause a cut can
# land on.
DEFAULT_PAUSE_GAP_MS = 700

# TS moments.ts:114 — the caps that make a preceding turn a setup rather than
# that speaker's own moment.
LEAD_IN_MAX_TURN_MS = 20_000
LEAD_IN_MAX_GAP_MS = 3000

# TS grid.ts:29 — a sentence whose display text ends with '?' (before closing
# quotes) is question-shaped.
QUESTION_TERMINAL = re.compile(r'\?["”\'’)\]]*\Z')

# TS grid.ts:33 — paragraph packing mirrors the viewer's grouping (speaker
# change, long silence, size cap) but tiles SENTENCES, never splitting one.
PARAGRAPH_GAP_MS = 2500
PARAGRAPH_MAX_WORDS = 120

# TS grid.ts:40 — the Cutter window: rough span ± margin, then expanded to
# whole turns.
CUTTER_MARGIN_MS = 90_000
CUTTER_PRECEDING_TURNS = 2
CUTTER_FOLLOWING_TURNS = 1

# TS grid.ts:46 — a boundary landing within this of a shot change moves ONTO
# it, never near it, and only over silence.
SHOT_SNAP_MS = 500

# TS grid.ts:368 — the second lead-in turn joins only as a short fragment,
# and the walk is bounded at two turns, always.
LEAD_IN_FRAGMENT_MS = 6000
LEAD_IN_MAX_TURNS = 2


# ---- Word-timeline helpers (TS moments.ts) --------------------------------


def sentence_starts(words: Sequence[GridWord]) -> list[int]:
    """Word indices where a sentence begins (TS moments.ts:73).

    The first word, and every word whose predecessor ends with terminal
    punctuation.
    """
    starts: list[int] = []
    for index in range(len(words)):
        if index == 0:
            starts.append(0)
            continue
        if SENTENCE_TERMINAL.search(words[index - 1].text):
            starts.append(index)
    return starts


def pause_boundaries(
    words: Sequence[GridWord], min_gap_ms: int = DEFAULT_PAUSE_GAP_MS
) -> list[int]:
    """Word indices preceded by a silence of at least `min_gap_ms` (TS moments.ts:88)."""
    boundaries: list[int] = []
    for index in range(1, len(words)):
        if words[index].startMs - words[index - 1].endMs >= min_gap_ms:
            boundaries.append(index)
    return boundaries


def sentence_start_times(words: Sequence[GridWord]) -> list[int]:
    """Times a sentence starts playing: its first word's start (TS moments.ts:112)."""
    return [words[index].startMs for index in sentence_starts(words)]


def sentence_end_times(words: Sequence[GridWord]) -> list[int]:
    """Times a sentence stops playing (TS moments.ts:118).

    The word before each subsequent sentence start, plus the final word.
    """
    times: list[int] = []
    for start in sentence_starts(words):
        if start == 0:
            continue
        times.append(words[start - 1].endMs)
    if words:
        times.append(words[-1].endMs)
    return times


def speaker_turn_start_times(words: Sequence[GridWord]) -> list[int]:
    """Times a speaker turn begins: the first word, and every change (TS moments.ts:159)."""
    times: list[int] = []
    for index, word in enumerate(words):
        if index == 0 or words[index - 1].speaker != word.speaker:
            times.append(word.startMs)
    return times


@dataclass(frozen=True, slots=True)
class MsRange:
    """A span on the millisecond timeline (TS moments.ts:92)."""

    end_ms: int
    start_ms: int


def capture_lead_in(range_: MsRange, words: Sequence[GridWord]) -> MsRange:
    """Grow a span back over the short other-speaker turn before it (TS moments.ts:126).

    The Brett Lee finding: a moment opening on an answer owes its meaning to
    the interviewer's setup. The two-turn version in `capture_lead_in_two_turn`
    supersedes this one for grid-addressed spans; it is kept because it is the
    rule the word timeline (rather than the sentence grid) states.
    """
    opener_index = next(
        (index for index, word in enumerate(words) if word.startMs >= range_.start_ms),
        -1,
    )
    if opener_index <= 0:
        return range_
    opener = words[opener_index]
    previous = words[opener_index - 1]
    # Same speaker before the opener is a mid-monologue start (snapping's job),
    # and unknown speakers give the rule nothing to reason with.
    if opener.speaker is None or previous.speaker is None or previous.speaker == opener.speaker:
        return range_
    if opener.startMs - previous.endMs > LEAD_IN_MAX_GAP_MS:
        return range_
    # Walk back to where the preceding speaker's turn began.
    turn_start = opener_index - 1
    while turn_start > 0 and words[turn_start - 1].speaker == previous.speaker:
        turn_start -= 1
    first = words[turn_start]
    # A long preceding turn is the other speaker's own moment, not a setup.
    if previous.endMs - first.startMs > LEAD_IN_MAX_TURN_MS:
        return range_
    return MsRange(end_ms=range_.end_ms, start_ms=first.startMs)


# ---- The grid (TS grid.ts) -------------------------------------------------


@dataclass(slots=True)
class GridSentence:
    """One sentence of the fine grid (TS grid.ts:48)."""

    end_ms: int
    #: Position of this sentence within its speaker turn.
    ends_turn: bool
    end_word: int
    id: int
    opens_turn: bool
    #: Silence after this sentence's last word (0 for the final sentence).
    pause_after_ms: int
    question: bool
    speaker: str | None
    start_ms: int
    start_word: int
    text: str


@dataclass(slots=True)
class GridParagraph:
    """One paragraph of the coarse grid: a run of sentences (TS grid.ts:64)."""

    end_ms: int
    end_sentence: int
    id: int
    speaker: str | None
    start_ms: int
    start_sentence: int


@dataclass(slots=True)
class CutGrid:
    """Both granularities over one word timeline (TS grid.ts:73)."""

    paragraphs: list[GridParagraph]
    sentences: list[GridSentence]
    words: Sequence[GridWord]


def _sentence_ranges(words: Sequence[GridWord]) -> list[tuple[int, int]]:
    """(start, end) word indices per sentence, inclusive (TS grid.ts:79)."""
    starts = sentence_starts(words)
    return [
        (start, (starts[index + 1] if index + 1 < len(starts) else len(words)) - 1)
        for index, start in enumerate(starts)
    ]


def _sentence(words: Sequence[GridWord], index: int, start: int, end: int) -> GridSentence:
    first = words[start]
    last = words[end]
    before = words[start - 1] if start - 1 >= 0 else None
    after = words[end + 1] if end + 1 < len(words) else None
    return GridSentence(
        end_ms=last.endMs,
        ends_turn=after is None or after.speaker != last.speaker,
        end_word=end,
        id=index,
        opens_turn=before is None or before.speaker != first.speaker,
        pause_after_ms=max(0, after.startMs - last.endMs) if after else 0,
        question=QUESTION_TERMINAL.search(last.text) is not None,
        speaker=first.speaker,
        start_ms=first.startMs,
        start_word=start,
        text=" ".join(word.text for word in words[start : end + 1]),
    )


def build_cut_grid(words: Sequence[GridWord]) -> CutGrid:
    """Tile the words into annotated sentences, then pack them into paragraphs.

    TS grid.ts:89. A paragraph breaks on a speaker change, on a gap longer than
    `PARAGRAPH_GAP_MS`, or when the next sentence would take it past
    `PARAGRAPH_MAX_WORDS` — and never mid-sentence.
    """
    sentences = [
        _sentence(words, index, start, end)
        for index, (start, end) in enumerate(_sentence_ranges(words))
    ]

    paragraphs: list[GridParagraph] = []
    current: GridParagraph | None = None
    current_words = 0
    for sentence in sentences:
        sentence_words = sentence.end_word - sentence.start_word + 1
        # `current` is None only on the first sentence, so id - 1 below is a
        # real index and never Python's wrap-around to the last sentence.
        gap_ms = 0 if current is None else sentence.start_ms - sentences[sentence.id - 1].end_ms
        breaks = (
            current is None
            or sentence.speaker != current.speaker
            or gap_ms > PARAGRAPH_GAP_MS
            or current_words + sentence_words > PARAGRAPH_MAX_WORDS
        )
        # `or current is None` is the TypeScript's, and it is not redundant to a
        # type checker: it is what says the else branch has a paragraph open.
        if breaks or current is None:
            current = GridParagraph(
                end_ms=sentence.end_ms,
                end_sentence=sentence.id,
                id=len(paragraphs),
                speaker=sentence.speaker,
                start_ms=sentence.start_ms,
                start_sentence=sentence.id,
            )
            paragraphs.append(current)
            current_words = sentence_words
        else:
            current.end_ms = sentence.end_ms
            current.end_sentence = sentence.id
            current_words += sentence_words

    return CutGrid(paragraphs=paragraphs, sentences=sentences, words=words)


@dataclass(frozen=True, slots=True)
class ResolvedSpan:
    """A span resolved from ids (TS grid.ts:153).

    `clamped` is true when an out-of-range id had to be pulled into range:
    surfaced as a flag, never a silent repair.
    """

    clamped: bool
    end_ms: int
    start_ms: int


def _clamp_id(grid: CutGrid, id_: float) -> int:
    """TS grid.ts:159."""
    return max(0, min(js_round(id_), len(grid.sentences) - 1))


def resolve_span(grid: CutGrid, in_id: float, out_id: float) -> ResolvedSpan:
    """Sentence ids to exact word times (TS grid.ts:166).

    A lookup, not a search: the span plays from the in sentence's first word to
    the out sentence's last. Invalid or inverted ids clamp and flag; the model
    proposes, the code disposes.
    """
    safe_in = _clamp_id(grid, in_id)
    safe_out = max(safe_in, _clamp_id(grid, out_id))
    clamped = safe_in != in_id or safe_out != out_id
    return ResolvedSpan(
        clamped=clamped,
        end_ms=grid.sentences[safe_out].end_ms if grid.sentences else 0,
        start_ms=grid.sentences[safe_in].start_ms if grid.sentences else 0,
    )


def resolve_paragraph_span(grid: CutGrid, start_pid: float, end_pid: float) -> ResolvedSpan:
    """Paragraph ids to exact word times (TS grid.ts:181)."""
    max_pid = len(grid.paragraphs) - 1
    safe_start = max(0, min(js_round(start_pid), max_pid))
    safe_end = max(safe_start, min(js_round(end_pid), max_pid))
    return ResolvedSpan(
        clamped=safe_start != start_pid or safe_end != end_pid,
        end_ms=grid.paragraphs[safe_end].end_ms if grid.paragraphs else 0,
        start_ms=grid.paragraphs[safe_start].start_ms if grid.paragraphs else 0,
    )


def sentence_at_ms(grid: CutGrid, ms: int) -> GridSentence | None:
    """The last sentence that had started by `ms` (TS grid.ts:196).

    Before the first sentence starts, the first sentence: a time inside the
    grid always resolves to somewhere the grid can address.
    """
    low = 0
    high = len(grid.sentences) - 1
    result: GridSentence | None = None
    while low <= high:
        mid = (low + high) // 2
        sentence = grid.sentences[mid]
        if sentence.start_ms <= ms:
            result = sentence
            low = mid + 1
        else:
            high = mid - 1
    if result is not None:
        return result
    return grid.sentences[0] if grid.sentences else None


# ---- Renderings (TS grid.ts:216) ------------------------------------------


def stamp_ms(ms: int) -> str:
    """`mm:ss`, minutes unbounded (TS grid.ts:218).

    The contract keeps every time a non-negative integer, which is what lets
    the floor and the remainder here match JavaScript's, whose `%` takes the
    sign of the dividend.
    """
    total_seconds = math.floor(ms / 1000)
    minutes = math.floor(total_seconds / 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def speaker_tag(speaker: str | None) -> str:
    """`S1`, `S2`, … for numeric diarization ids; `S?` for none (TS grid.ts:225).

    A non-numeric id is passed through as it stands (`Sguest`). `Number()`, not
    `int()`: the tag for the id "1.0" is `S2` in the TypeScript, and anything
    `int()` would raise on has an answer here.
    """
    if speaker is None:
        return "S?"
    numeric = js_number(speaker)
    if is_integer(numeric):
        return f"S{int(numeric) + 1}"
    return f"S{speaker}"


def render_coarse(grid: CutGrid) -> str:
    """The screening rendering: one line per paragraph, id first (TS grid.ts:236).

    What the Director and both rough passes read. `mm:ss` stays visible for
    pacing but is never the output coordinate.
    """
    lines: list[str] = []
    for paragraph in grid.paragraphs:
        text = " ".join(
            sentence.text
            for sentence in grid.sentences[paragraph.start_sentence : paragraph.end_sentence + 1]
        )
        lines.append(
            f"P{paragraph.id:03d} [{stamp_ms(paragraph.start_ms)}]"
            f" {speaker_tag(paragraph.speaker)}: {text}"
        )
    return "\n".join(lines)


def render_fine(
    grid: CutGrid,
    shot_times_ms: Sequence[int] = (),
    from_sentence: int = 0,
    to_sentence: int | None = None,
) -> str:
    """The reel rendering: one line per sentence, with its glyphs (TS grid.ts:250).

    What the Cutter reads, built per window on demand — hence the sentence
    bounds, which default to the whole grid.
    """
    last = len(grid.sentences) - 1 if to_sentence is None else to_sentence
    lines: list[str] = []
    for id_ in range(from_sentence, last + 1):
        if not 0 <= id_ < len(grid.sentences):
            continue
        sentence = grid.sentences[id_]
        marks: list[str] = []
        if sentence.opens_turn:
            marks.append("⟲turn")
        if sentence.question:
            marks.append("·q")
        if sentence.pause_after_ms >= DEFAULT_PAUSE_GAP_MS:
            marks.append(f"¶{to_fixed(sentence.pause_after_ms / 1000, 1)}s")
        if any(abs(shot - sentence.start_ms) <= SHOT_SNAP_MS for shot in shot_times_ms):
            marks.append("·cut")
        suffix = f"  {' '.join(marks)}" if marks else ""
        lines.append(
            f"s{sentence.id:04d} [{stamp_ms(sentence.start_ms)}]"
            f" {speaker_tag(sentence.speaker)}: {sentence.text}{suffix}"
        )
    return "\n".join(lines)


# ---- The Cutter window (TS grid.ts:286) -----------------------------------


@dataclass(frozen=True, slots=True)
class CutterWindow:
    """The sentence range the Cutter is shown (TS grid.ts:288)."""

    from_sentence: int
    to_sentence: int


def _turn_start_sentence(grid: CutGrid, id_: int) -> int:
    """TS grid.ts:293."""
    cursor = id_
    while cursor > 0 and not grid.sentences[cursor].opens_turn:
        cursor -= 1
    return cursor


def _turn_end_sentence(grid: CutGrid, id_: int) -> int:
    """TS grid.ts:301."""
    cursor = id_
    while cursor < len(grid.sentences) - 1 and not grid.sentences[cursor].ends_turn:
        cursor += 1
    return cursor


def cutter_window(
    grid: CutGrid, range_: MsRange, margin_ms: int = CUTTER_MARGIN_MS
) -> CutterWindow:
    """The rough span ± margin, expanded to whole turns (TS grid.ts:316).

    Then widened to at least `CUTTER_PRECEDING_TURNS` complete turns before and
    `CUTTER_FOLLOWING_TURNS` after: the failure shapes M1 measured live were in
    exactly that neighbourhood.
    """
    first = sentence_at_ms(grid, max(0, range_.start_ms - margin_ms))
    last = sentence_at_ms(grid, range_.end_ms + margin_ms)
    from_ = _turn_start_sentence(grid, first.id if first else 0)
    to = _turn_end_sentence(grid, last.id if last else len(grid.sentences) - 1)

    anchor = sentence_at_ms(grid, range_.start_ms)
    cursor = _turn_start_sentence(grid, anchor.id if anchor else 0)
    turns = 0
    while turns < CUTTER_PRECEDING_TURNS and cursor > 0:
        cursor = _turn_start_sentence(grid, cursor - 1)
        turns += 1
    from_ = min(from_, cursor)

    anchor_end = sentence_at_ms(grid, max(range_.end_ms - 1, range_.start_ms))
    end_cursor = _turn_end_sentence(grid, anchor_end.id if anchor_end else 0)
    turns = 0
    while turns < CUTTER_FOLLOWING_TURNS and end_cursor < len(grid.sentences) - 1:
        end_cursor = _turn_end_sentence(grid, end_cursor + 1)
        turns += 1
    to = max(to, end_cursor)

    return CutterWindow(from_sentence=from_, to_sentence=to)


# ---- Deterministic narrative backstops (TS grid.ts:354) -------------------


@dataclass(frozen=True, slots=True)
class BackstopResult:
    """What a backstop did: the range it returns, and why (TS grid.ts:356)."""

    flags: list[str]
    range: MsRange


def _capturable_turn_start(
    grid: CutGrid, cursor: int, opener_speaker: str, captured: int
) -> int | None:
    """One backward step of the lead-in walk (TS grid.ts:375).

    The start sentence of the turn preceding `cursor`, or None when that turn
    does not qualify. The first captured turn must be the OTHER speaker's short
    setup (a same-speaker predecessor is mid-monologue, which is snapping's
    job, and a long turn is that speaker's own moment); the second joins only
    as a short fragment.
    """
    if not 0 <= cursor < len(grid.sentences) or cursor - 1 < 0:
        return None
    boundary = grid.sentences[cursor]
    previous = grid.sentences[cursor - 1]
    if previous.speaker is None:
        return None
    if boundary.start_ms - previous.end_ms > LEAD_IN_MAX_GAP_MS:
        return None
    turn_start = _turn_start_sentence(grid, previous.id)
    turn_first = grid.sentences[turn_start]
    turn_length_ms = previous.end_ms - turn_first.start_ms
    if captured == 0:
        if previous.speaker == opener_speaker or turn_length_ms > LEAD_IN_MAX_TURN_MS:
            return None
    elif turn_length_ms > LEAD_IN_FRAGMENT_MS:
        return None
    return turn_start


def capture_lead_in_two_turn(range_: MsRange, grid: CutGrid) -> BackstopResult:
    """Lead-in capture v2: up to TWO turns back (TS grid.ts:408).

    Closes the documented single-turn limit of `capture_lead_in`, the M1
    two-turn setup gap. A span opening on a turn start grows backward over the
    immediately preceding other-speaker turn when it is short and close, and
    then over one more short fragment turn when that too is close. Bounded at
    two turns, always.
    """
    opener = sentence_at_ms(grid, range_.start_ms)
    if opener is None or opener.start_ms < range_.start_ms or opener.speaker is None:
        return BackstopResult(flags=[], range=range_)
    # A span opening mid-turn is a mid-monologue start — snapping's job, not
    # lead-in's; walking to the turn start would teleport past the speaker's
    # own build-up.
    if not opener.opens_turn:
        return BackstopResult(flags=[], range=range_)
    cursor = opener.id
    captured_starts: list[int] = []
    captured_question = False
    while len(captured_starts) < LEAD_IN_MAX_TURNS:
        turn_start = _capturable_turn_start(grid, cursor, opener.speaker, len(captured_starts))
        if turn_start is None:
            break
        # The turn's final sentence is the one adjacent to the previous
        # boundary — question-shape there marks a real setup.
        turn_final = grid.sentences[cursor - 1] if cursor - 1 >= 0 else None
        if turn_final is not None and turn_final.question:
            captured_question = True
        cursor = turn_start
        captured_starts.append(turn_start)
    # Walking past the immediate setup turn is only safe when a QUESTION was
    # found among the captured turns — otherwise two adjacent short remarks
    # would swallow unrelated material (and, on dense timelines, collapse
    # distinct candidates into dedupe unions). Without one, fall back to the
    # single-turn capture.
    if len(captured_starts) == LEAD_IN_MAX_TURNS and not captured_question:
        cursor = captured_starts[0]
    if not captured_starts:
        return BackstopResult(flags=[], range=range_)
    first = grid.sentences[cursor]
    if first.start_ms >= range_.start_ms:
        return BackstopResult(flags=[], range=range_)
    return BackstopResult(
        flags=["lead_in_captured"],
        range=MsRange(end_ms=range_.end_ms, start_ms=first.start_ms),
    )


def lead_out_trim(range_: MsRange, grid: CutGrid) -> BackstopResult:
    """Trim a span that has run into the next question (TS grid.ts:466).

    A span whose FINAL sentence opens a different-speaker question turn has run
    into the next exchange; trim back to the previous sentence's end. A clip
    never ends on the next question.
    """
    last = sentence_at_ms(grid, max(range_.end_ms - 1, range_.start_ms))
    if last is None or last.end_ms > range_.end_ms + 1:
        return BackstopResult(flags=[], range=range_)
    if last.id - 1 < 0:
        return BackstopResult(flags=[], range=range_)
    previous = grid.sentences[last.id - 1]
    if previous.start_ms < range_.start_ms:
        return BackstopResult(flags=[], range=range_)
    different_speaker = (
        last.speaker is not None
        and previous.speaker is not None
        and last.speaker != previous.speaker
    )
    if not (different_speaker and last.opens_turn and last.question):
        return BackstopResult(flags=[], range=range_)
    return BackstopResult(
        flags=["lead_out_trimmed"],
        range=MsRange(end_ms=previous.end_ms, start_ms=range_.start_ms),
    )


def stale_open_flag(range_: MsRange, grid: CutGrid) -> bool:
    """Flag a span opening on the tail of the previous answer (TS grid.ts:491).

    The exact Preity Zinta shape: the span's FIRST sentence is the final
    sentence of its speaker's turn. A flag, never an auto-reject.
    """
    opener = sentence_at_ms(grid, range_.start_ms)
    if opener is None or abs(opener.start_ms - range_.start_ms) > 1:
        return False
    return opener.ends_turn and not opener.opens_turn


def pause_air_extend(range_: MsRange, grid: CutGrid) -> MsRange:
    """Give the payoff its air (TS grid.ts:502).

    Extend the end into the silence after the final sentence, up to one pause
    gap. Never past the next word's start; `pause_after_ms` stops there by
    construction.
    """
    last = sentence_at_ms(grid, max(range_.end_ms - 1, range_.start_ms))
    if last is None or abs(last.end_ms - range_.end_ms) > 1:
        return range_
    air = min(last.pause_after_ms, DEFAULT_PAUSE_GAP_MS)
    if air <= 0:
        return range_
    return MsRange(end_ms=range_.end_ms + air, start_ms=range_.start_ms)


def shot_snap(range_: MsRange, grid: CutGrid, shot_times_ms: Sequence[int]) -> BackstopResult:
    """Move a boundary ONTO a shot change in the dead zone (TS grid.ts:518).

    Only over silence: never across a sentence boundary, never over speech.
    In-points snap within the pause BEFORE the in sentence, out-points within
    the pause AFTER the out sentence.
    """
    if not shot_times_ms:
        return BackstopResult(flags=[], range=range_)
    start_ms = range_.start_ms
    end_ms = range_.end_ms
    flags: list[str] = []

    opener = sentence_at_ms(grid, start_ms)
    if opener is not None and abs(opener.start_ms - start_ms) <= 1:
        previous = grid.sentences[opener.id - 1] if opener.id - 1 >= 0 else None
        silence_from = previous.end_ms if previous else 0
        shot = _first(
            time
            for time in shot_times_ms
            if abs(time - start_ms) <= SHOT_SNAP_MS
            and time >= silence_from
            and time <= opener.start_ms
        )
        if shot is not None and shot != start_ms:
            start_ms = shot
            flags.append("shot_snapped")

    last = sentence_at_ms(grid, max(end_ms - 1, start_ms))
    if last is not None:
        silence_to = last.end_ms + last.pause_after_ms
        shot = _first(
            time
            for time in shot_times_ms
            if abs(time - end_ms) <= SHOT_SNAP_MS and time >= last.end_ms and time <= silence_to
        )
        if shot is not None and shot != end_ms:
            end_ms = shot
            if "shot_snapped" not in flags:
                flags.append("shot_snapped")
    return BackstopResult(flags=flags, range=MsRange(end_ms=end_ms, start_ms=start_ms))


def _first(times: Iterable[int]) -> int | None:
    """`Array.prototype.find`: the first match, or None."""
    return next(iter(times), None)


# ---- The shot grid --------------------------------------------------------


class Shot(BaseModel):
    """One scene-change candidate, as `derive.write_shots` records it."""

    score: float
    t: float


class ShotGrid(BaseModel):
    """The ingest's `shots/shots.json`: every candidate above the emit floor.

    The file carries the floor and the decision threshold with the candidates
    precisely so the consumer decides; `snap_times_ms` is that decision.
    """

    decision_threshold: float
    shots: list[Shot]

    def snap_times_ms(self) -> list[int]:
        """The times a boundary may snap onto, in integer milliseconds.

        The seconds are rounded the way `Math.round` rounds, because the
        TypeScript oracle that dumped the fixtures rounds them that way and the
        renderings are compared byte for byte.
        """
        return [
            js_round(shot.t * 1000) for shot in self.shots if shot.score >= self.decision_threshold
        ]
