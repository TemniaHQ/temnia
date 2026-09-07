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


def _segment_words(position: int, segment: dict[str, Any]) -> list[object]:
    """The word objects of one segment, or the words its text splits into.

    A segment with a word list is what alignment produces, empty for music and
    silence. One with text and no word list at all is a segment alignment never
    saw; its text is kept as untimed words for the fill below rather than
    disappearing, because a person heard those words.
    """
    raw_words = segment.get("words")
    if raw_words is None:
        text = segment.get("text")
        if isinstance(text, str):
            return [{"word": piece} for piece in text.split()]
        return []
    if not isinstance(raw_words, list):
        msg = f"segment {position} has words of type {type(raw_words).__name__}, not a list"
        raise TranscriptContractError(msg)
    return cast("list[object]", raw_words)


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
            text = str(word.get("word", "")).strip()
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


def _fill(drafts: Sequence[_Draft]) -> list[TranscriptWord]:
    """Give every word a start and an end, in the order it was spoken.

    The words' starts and ends are one ascending sequence of time points, two
    per word. Every run of missing points between two known ones shares the
    span between them in proportion to the words' lengths in characters (the
    gap between two words weighs nothing, so untimed neighbours abut). The
    known time on either side is the nearest timed point, or the segment's own
    bound, or zero and then nothing. Filling one word and using it as the next
    word's anchor gave the second of two untimed words no duration (review
    finding I08); a run is filled as a whole.

    Then, in lexical order, a word that starts before its predecessor takes the
    predecessor's start, and an end before its own start is raised to it. That
    keeps the contract's ascending starts without reordering a single word.
    Every word a rule touched is `interpolated`, which the viewer shows as
    approximate.
    """
    count = len(drafts)
    points: list[int | None] = []
    for draft in drafts:
        points.extend((draft.start_ms, draft.end_ms))
    # Weight of the stretch from point i to point i + 1: a word's own length in
    # characters, and nothing between one word's end and the next word's start.
    weights = [0] * max(2 * count - 1, 0)
    for index, draft in enumerate(drafts):
        weights[2 * index] = max(len(draft.text), 1)
    touched = [False] * count

    index = 0
    while index < len(points):
        if points[index] is not None:
            index += 1
            continue
        first = index
        while index < len(points) and points[index] is None:
            index += 1
        last = index
        earlier = points[first - 1] if first > 0 else drafts[first // 2].segment[0]
        later = points[last] if last < len(points) else drafts[(last - 1) // 2].segment[1]
        before = earlier if earlier is not None else (later if later is not None else 0)
        after = later if later is not None else before
        _spread(points, weights, (first, last), (before, after))
        for point in range(first, last):
            touched[point // 2] = True

    filled: list[TranscriptWord] = []
    previous_start = 0
    for word_index, draft in enumerate(drafts):
        start = cast("int", points[2 * word_index])
        end = cast("int", points[2 * word_index + 1])
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
