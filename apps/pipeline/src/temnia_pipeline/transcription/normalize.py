"""WhisperX's output, turned into the one transcript shape Temnia knows.

This is the only place a float second becomes an integer millisecond, so no
later stage can round the same word differently. Everything the engine leaves
out is filled in here and marked, rather than being passed on as a hole for
the viewer, the cue builder, and the substrate to each guess about.

Two rules from the S2 review (2026-09-07) shape this module. The shape of the
response is checked, not tolerated: a response with no segment list, or a
segment or word that is not an object, is a contract error the runner treats
as terminal, with the raw response kept in storage for a person to read;
tolerating it made `{"unexpected": 123}` a valid empty transcript. And the
words are never reordered: the order of the words is what was said, and the
times are the estimate, so a word alignment placed before its predecessor keeps
its place and has its time repaired and flagged. Sorting by time rewrote the
sentence.

The contract's two refinements do not survive Zod's JSON Schema emission, so
they are restated at the bottom of this module and tested on both sides:
words ascend by start, and none may end more than two seconds after the media
does. The check is one-directional. A transcript that stops early is silence;
one that runs past the end is a transcript of different bytes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.contracts import (
    TranscriptProvider,
    TranscriptUtterance,
    TranscriptV1,
    TranscriptWord,
    WordTiming,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Words may end this far past the probed duration before the run is refused:
# a container's duration and the last frame of audio disagree by rounding, and
# whisperx pads the final segment. Mirrors DURATION_SLACK_MS in the contract.
DURATION_SLACK_MS = 2000

# "SPEAKER_00" is pyannote's own label. Stored as "0": the display name lives
# on the transcript row, so a rename never rewrites a revision, and a short id
# keeps a two-hour transcript's JSON small.
_SPEAKER = re.compile(r"^SPEAKER_0*(\d+)$")


class TranscriptContractError(ValueError):
    """The response, or the normalised transcript, breaks a rule the contract cannot express."""


def normalize_speaker(speaker: object) -> str | None:
    """`SPEAKER_00` becomes `"0"`; anything else is kept as it was typed."""
    if not isinstance(speaker, str) or not speaker.strip():
        return None
    matched = _SPEAKER.match(speaker.strip())
    return matched.group(1) if matched else speaker.strip()


def _seconds_to_ms(value: object) -> int | None:
    """A finite number of seconds as milliseconds; anything else is missing.

    NaN reaches here from whisperx's alignment as readily as None does, and a
    NaN that survived would poison every comparison downstream.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    return round(value * 1000)


def _confidence(value: object) -> float | None:
    """A score in 0..1, or None. NaN and out-of-range scores become None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        return None
    return float(value)


@dataclass(slots=True)
class _Draft:
    """One word before its holes are filled."""

    text: str
    start_ms: int | None
    end_ms: int | None
    speaker: str | None
    confidence: float | None
    segment: tuple[int | None, int | None]
    segment_index: int


# Only explicit non-speech annotations, never ordinary words such as "music".
_NO_SPEECH = re.compile(
    r"(?:[♪♫♬♩\s]+|\[(?:music|silence|no speech)\]|\((?:music|silence|no speech)\))",
    re.IGNORECASE,
)


def _blank_word(value: object) -> bool:
    """Only a valid word object with empty text counts as a blank alignment token."""
    if not isinstance(value, dict):
        return False
    word = cast("dict[str, Any]", value).get("word")
    return isinstance(word, str) and not word.strip()


def _segment_words(position: int, segment: dict[str, Any]) -> list[object]:
    """Keep spoken text when alignment is absent or empty; refuse malformed fields."""
    raw_words = segment.get("words")
    if raw_words is not None and not isinstance(raw_words, list):
        msg = f"segment {position} has words of type {type(raw_words).__name__}, not a list"
        raise TranscriptContractError(msg)
    text = segment.get("text")
    if "text" in segment and not isinstance(text, str):
        msg = f"segment {position} has a non-string text field"
        raise TranscriptContractError(msg)
    if raw_words:
        word_objects = cast("list[object]", raw_words)
        if not all(_blank_word(word) for word in word_objects):
            return word_objects
    if isinstance(text, str):
        stripped = text.strip()
        if not stripped or _NO_SPEECH.fullmatch(stripped):
            return []
        return [{"word": piece} for piece in text.split()]
    if isinstance(raw_words, list):
        return []
    msg = f"segment {position} has neither a word list nor text"
    raise TranscriptContractError(msg)


def _drafts(segments: Sequence[object]) -> list[_Draft]:
    """Every word in every segment, with the segment's own bounds carried along.

    A whitespace-only word is dropped: whisperx emits them for music and
    silence, and an empty word would put nothing on screen under a timestamp.
    Anything that is not the shape whisperx emits is a contract error, never a
    silent omission.
    """
    drafts: list[_Draft] = []
    for position, found in enumerate(segments):
        if not isinstance(found, dict):
            msg = f"segment {position} is {type(found).__name__}, not an object"
            raise TranscriptContractError(msg)
        segment = cast("dict[str, Any]", found)
        bounds = (_seconds_to_ms(segment.get("start")), _seconds_to_ms(segment.get("end")))
        segment_speaker = normalize_speaker(segment.get("speaker"))
        for index, raw in enumerate(_segment_words(position, segment)):
            if not isinstance(raw, dict):
                msg = f"word {index} of segment {position} is {type(raw).__name__}, not an object"
                raise TranscriptContractError(msg)
            word = cast("dict[str, Any]", raw)
            value = word.get("word")
            if not isinstance(value, str):
                msg = f"word {index} of segment {position} has no string word field"
                raise TranscriptContractError(msg)
            text = value.strip()
            if not text:
                continue
            drafts.append(
                _Draft(
                    text=text,
                    start_ms=_seconds_to_ms(word.get("start")),
                    end_ms=_seconds_to_ms(word.get("end")),
                    # A word with no diarization overlap takes the enclosing
                    # segment's speaker; a segment with none leaves it null,
                    # which the viewer renders as an unattributed run.
                    speaker=normalize_speaker(word.get("speaker")) or segment_speaker,
                    confidence=_confidence(word.get("score")),
                    segment=bounds,
                    segment_index=position,
                )
            )
    return drafts


def _spread(
    points: list[int | None],
    weights: Sequence[int],
    window: tuple[int, int],
    anchors: tuple[int, int],
) -> None:
    """Fill the points in `window` between the two `anchors`, in proportion to the weights.

    `weights[i]` is the weight of the stretch from point `i` to point `i + 1`.
    Anchors that cross (the time after is before the time before) leave no
    span to share, and every point takes the earlier one: no duration is
    invented where the evidence has none.
    """
    first, last = window
    before, after = anchors
    span = max(after - before, 0)
    lead = weights[first - 1] if first > 0 else 0
    total = lead + sum(weights[first:last])
    run = 0
    for index in range(first, last):
        run += weights[index - 1] if index > 0 else 0
        points[index] = before + (round(span * run / total) if total > 0 else 0)


def _timeline(drafts: Sequence[_Draft]) -> tuple[list[int | None], list[int], list[int]]:
    """Word points with segment bounds inserted as anchors and word offsets retained."""
    points: list[int | None] = []
    weights: list[int] = []
    offsets: list[int] = []

    def append(value: int | None, weight: int = 0) -> None:
        if points:
            weights.append(weight)
        points.append(value)

    for index, draft in enumerate(drafts):
        if index == 0 or draft.segment_index != drafts[index - 1].segment_index:
            if index:
                append(drafts[index - 1].segment[1])
            append(draft.segment[0])
        offsets.append(len(points))
        append(draft.start_ms)
        append(draft.end_ms, max(len(draft.text), 1))
    if drafts:
        append(drafts[-1].segment[1])
    return points, weights, offsets


def _fill(drafts: Sequence[_Draft]) -> list[TranscriptWord]:
    """Fill missing runs within segment evidence, then repair in lexical order.

    Segment bounds participate in the same point sequence as word times. A
    known segment start/end therefore stops a missing run, including in the
    middle of a transcript. Unknown bounds can borrow neighbouring evidence;
    known bounds never disappear into a long pause between other segments.
    Character weights divide only missing word spans, with no invented gaps.
    Valid aligned times and overlaps remain intact. Repairs are flagged.
    """
    points, weights, offsets = _timeline(drafts)
    touched = [draft.start_ms is None or draft.end_ms is None for draft in drafts]
    index = 0
    while index < len(points):
        if points[index] is not None:
            index += 1
            continue
        first = index
        while index < len(points) and points[index] is None:
            index += 1
        last = index
        earlier = points[first - 1] if first > 0 else None
        later = points[last] if last < len(points) else None
        before = earlier if earlier is not None else (later if later is not None else 0)
        after = later if later is not None else before
        _spread(points, weights, (first, last), (before, after))

    filled: list[TranscriptWord] = []
    previous_start = 0
    for word_index, (draft, offset) in enumerate(zip(drafts, offsets, strict=True)):
        start = cast("int", points[offset])
        end = cast("int", points[offset + 1])
        if start < previous_start:
            start = previous_start
            touched[word_index] = True
        if end < start:
            end = start
            touched[word_index] = True
        previous_start = start
        filled.append(
            TranscriptWord(
                text=draft.text,
                startMs=start,
                endMs=end,
                speaker=draft.speaker,
                confidence=draft.confidence,
                timing=WordTiming.interpolated if touched[word_index] else WordTiming.aligned,
            )
        )
    return filled


def _utterances(words: Sequence[TranscriptWord]) -> list[TranscriptUtterance]:
    """Consecutive words of one speaker, collapsed into turns."""
    turns: list[TranscriptUtterance] = []
    for word in words:
        last = turns[-1] if turns else None
        if last is not None and last.speaker == word.speaker:
            turns[-1] = TranscriptUtterance(
                startMs=last.startMs, endMs=max(last.endMs, word.endMs), speaker=last.speaker
            )
            continue
        turns.append(
            TranscriptUtterance(startMs=word.startMs, endMs=word.endMs, speaker=word.speaker)
        )
    return turns


def assert_contract(transcript: TranscriptV1) -> TranscriptV1:
    """The two rules the pydantic model cannot carry, checked once.

    Zod drops refinements when it emits JSON Schema, so the generated model
    knows the shape and not these; without this the seam would be a type check
    on one side and a promise on the other.
    """
    starts = [word.startMs for word in transcript.words]
    if any(later < earlier for earlier, later in pairwise(starts)):
        msg = "words must be sorted by startMs"
        raise TranscriptContractError(msg)
    limit = transcript.durationMs + DURATION_SLACK_MS
    overrun = next((word for word in transcript.words if word.endMs > limit), None)
    if overrun is not None:
        msg = (
            f"a word ends at {overrun.endMs} ms, after the recording's "
            f"{transcript.durationMs} ms plus {DURATION_SLACK_MS} ms of slack; "
            "the transcript is not of this media"
        )
        raise TranscriptContractError(msg)
    return transcript


def normalize_whisperx(
    raw: dict[str, Any], duration_ms: int, provider: TranscriptProvider
) -> TranscriptV1:
    """WhisperX's response as a `TranscriptV1`, or a contract error.

    `provider` is the engine that produced `raw`; it rides on the transcript so
    a revision says what made it, which is what makes an S12 calibration round
    comparable against what it replaces.
    """
    found = raw.get("segments")
    if not isinstance(found, list):
        msg = (
            "the engine's response has no segments list "
            f"(got {type(found).__name__}); it is not a transcript"
        )
        raise TranscriptContractError(msg)
    words = _fill(_drafts(cast("list[object]", found)))
    speakers: list[str] = []
    for word in words:
        if word.speaker is not None and word.speaker not in speakers:
            speakers.append(word.speaker)
    language = raw.get("language")
    return assert_contract(
        TranscriptV1(
            version=1,
            language=str(language) if isinstance(language, str) and language else "und",
            durationMs=duration_ms,
            provider=provider,
            speakers=speakers,
            words=words,
            utterances=_utterances(words),
        )
    )
