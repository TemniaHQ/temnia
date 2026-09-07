"""WhisperX's output, turned into the one transcript shape Temnia knows.

This is the only place a float second becomes an integer millisecond, so no
later stage can round the same word differently. Everything the engine leaves
out is filled in here and marked, rather than being passed on as a hole for
the viewer, the cue builder, and the substrate to each guess about.

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
    from collections.abc import Iterable, Sequence

# Words may end this far past the probed duration before the run is refused:
# a container's duration and the last frame of audio disagree by rounding, and
# whisperx pads the final segment. Mirrors DURATION_SLACK_MS in the contract.
DURATION_SLACK_MS = 2000

# "SPEAKER_00" is pyannote's own label. Stored as "0": the display name lives
# on the transcript row, so a rename never rewrites a revision, and a short id
# keeps a two-hour transcript's JSON small.
_SPEAKER = re.compile(r"^SPEAKER_0*(\d+)$")


class TranscriptContractError(ValueError):
    """The normalised transcript breaks a rule the contract cannot express."""


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


def _drafts(segments: Iterable[object]) -> list[_Draft]:
    """Every word in every segment, with the segment's own bounds carried along.

    A segment with no words at all is dropped rather than turned into one long
    word: whisperx emits them for music and silence, and an empty segment that
    became a word would put text on screen that nobody said.
    """
    drafts: list[_Draft] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        raw_words = segment.get("words")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(raw_words, list):
            continue
        bounds = (_seconds_to_ms(segment.get("start")), _seconds_to_ms(segment.get("end")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        segment_speaker = normalize_speaker(segment.get("speaker"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        for raw in raw_words:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("word", "")).strip()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            if not text:
                continue
            drafts.append(
                _Draft(
                    text=text,
                    start_ms=_seconds_to_ms(raw.get("start")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    end_ms=_seconds_to_ms(raw.get("end")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    # A word with no diarization overlap takes the enclosing
                    # segment's speaker; a segment with none leaves it null,
                    # which the viewer renders as an unattributed run.
                    speaker=normalize_speaker(raw.get("speaker")) or segment_speaker,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    confidence=_confidence(raw.get("score")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    segment=bounds,
                )
            )
    return drafts


def _anchor_before(drafts: Sequence[_Draft], index: int) -> int | None:
    """The last known time at or before `index`, from a word or its segment."""
    for previous in reversed(range(index)):
        candidate = drafts[previous].end_ms or drafts[previous].start_ms
        if candidate is not None:
            return candidate
    return drafts[index].segment[0]


def _anchor_after(drafts: Sequence[_Draft], index: int) -> int | None:
    """The next known time after `index`, from a word or its segment."""
    for following in range(index + 1, len(drafts)):
        candidate = drafts[following].start_ms or drafts[following].end_ms
        if candidate is not None:
            return candidate
    return drafts[index].segment[1]


def _fill(drafts: Sequence[_Draft]) -> list[TranscriptWord]:
    """Give every word a start and an end, marking the ones we invented.

    Numbers and symbols come back from whisperx without timestamps because the
    alignment model has no phonemes for them. Dropping those words would lose
    text the user can see in the audio; leaving them at zero would send a click
    to the top of the recording. They are interpolated between their neighbours
    and flagged, so the surface can show them as approximate.
    """
    filled: list[TranscriptWord] = []
    for index, draft in enumerate(drafts):
        timing = (
            WordTiming.aligned
            if draft.start_ms is not None and draft.end_ms is not None
            else WordTiming.interpolated
        )
        start = draft.start_ms
        end = draft.end_ms
        if start is None:
            before = _anchor_before(drafts, index)
            start = before if before is not None else (end if end is not None else 0)
        if end is None:
            after = _anchor_after(drafts, index)
            end = after if after is not None else start
        start = max(start, 0)
        end = max(end, start)
        draft.start_ms = start
        draft.end_ms = end
        filled.append(
            TranscriptWord(
                text=draft.text,
                startMs=start,
                endMs=end,
                speaker=draft.speaker,
                confidence=draft.confidence,
                timing=timing,
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
    drafts = _drafts(cast("list[Any]", found) if isinstance(found, list) else [])
    words = _fill(drafts)
    # Alignment can move a word before the one that preceded it in the
    # segment; the contract promises ascending starts to everything that reads
    # a transcript, so the sort happens here rather than in each reader.
    words.sort(key=lambda word: (word.startMs, word.endMs))
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
