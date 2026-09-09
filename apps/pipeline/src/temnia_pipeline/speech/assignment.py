"""CPU speaker assignment compatible with WhisperX 3.8.6."""

# ruff: noqa: C901, EM101, EM102, PLR0913, PLR1704, TC001, TRY003

from __future__ import annotations

import bisect
import copy
import math
from dataclasses import dataclass
from typing import Any, cast

from temnia_pipeline.speech.contracts import CheckpointSource, normalize_payload
from temnia_pipeline.speech.contracts_v2 import (
    ArtifactRefV2,
    ExecutionTopology,
    SpeakerAssignmentConfig,
    SpeechAssignmentV1,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """One validated speaker interval in seconds."""

    start: float
    end: float
    speaker: str


def parse_speaker_turns(value: object) -> list[SpeakerTurn]:
    """Reject malformed or nonfinite model output before assignment."""
    if not isinstance(value, list):
        raise TypeError("diarization must be a list")
    rows = cast("list[object]", value)
    turns: list[SpeakerTurn] = []
    for index, value in enumerate(rows):
        if not isinstance(value, dict):
            raise TypeError(f"diarization row {index} is not an object")
        row = cast("dict[object, object]", value)
        start, end, speaker = row.get("start"), row.get("end"), row.get("speaker")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int | float)
            or not isinstance(end, int | float)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(end) <= float(start)
            or not isinstance(speaker, str)
            or not speaker.strip()
        ):
            raise ValueError(f"diarization row {index} has invalid interval identity")
        turns.append(SpeakerTurn(float(start), float(end), speaker))
    return turns


class _TurnIndex:
    """Stable interval queries with WhisperX's first-row tie behavior."""

    def __init__(self, turns: list[SpeakerTurn]) -> None:
        self.turns = sorted(turns, key=lambda turn: turn.start)
        size = 1
        while size < len(self.turns):
            size *= 2
        self._size = size
        self._max_end = [float("-inf")] * (2 * size)
        for index, turn in enumerate(self.turns):
            self._max_end[size + index] = turn.end
        for index in range(size - 1, 0, -1):
            self._max_end[index] = max(self._max_end[2 * index], self._max_end[2 * index + 1])

        midpoint_first: dict[float, tuple[int, str]] = {}
        for index, turn in enumerate(self.turns):
            midpoint_first.setdefault((turn.start + turn.end) / 2, (index, turn.speaker))
        self._midpoints = sorted(midpoint_first)
        self._midpoint_first = midpoint_first

    def overlaps(self, start: float, end: float) -> list[tuple[str, float]]:
        found: list[tuple[str, float]] = []

        def visit(node: int, left: int, right: int) -> None:
            if left >= len(self.turns) or self._max_end[node] <= start:
                return
            if self.turns[left].start >= end:
                return
            if right - left == 1:
                turn = self.turns[left]
                intersection = min(turn.end, end) - max(turn.start, start)
                if intersection > 0:
                    found.append((turn.speaker, intersection))
                return
            middle = (left + right) // 2
            visit(node * 2, left, middle)
            visit(node * 2 + 1, middle, right)

        if self.turns:
            visit(1, 0, self._size)
        return found

    def nearest(self, point: float) -> str | None:
        if not self._midpoints:
            return None
        position = bisect.bisect_left(self._midpoints, point)
        candidates = self._midpoints[max(0, position - 1) : min(len(self._midpoints), position + 1)]
        midpoint = min(
            candidates,
            key=lambda item: (abs(item - point), self._midpoint_first[item][0]),
        )
        return self._midpoint_first[midpoint][1]


def _time(value: object, *, path: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{path} must be a finite number")
    return float(value)


def _speaker_for(index: _TurnIndex, start: float, end: float, *, fill_nearest: bool) -> str | None:
    intersections: dict[str, float] = {}
    for speaker, duration in index.overlaps(start, end):
        intersections[speaker] = intersections.get(speaker, 0.0) + duration
    if intersections:
        return max(intersections.items(), key=lambda item: item[1])[0]
    return index.nearest((start + end) / 2) if fill_nearest else None


def assign_word_speakers_v386(
    transcript: dict[str, object],
    diarization: object,
    *,
    fill_nearest: bool = True,
) -> dict[str, object]:
    """Apply the tagged WhisperX 3.8.6 overlap and nearest-turn rules."""
    assigned = copy.deepcopy(transcript)
    segments_value = assigned.get("segments", [])
    if not isinstance(segments_value, list):
        raise TypeError("aligned transcript segments must be a list")
    segments = cast("list[object]", segments_value)
    turns = parse_speaker_turns(diarization)
    if not segments or not turns:
        return assigned
    index = _TurnIndex(turns)
    for segment_index, value in enumerate(segments):
        if not isinstance(value, dict):
            raise TypeError(f"aligned segment {segment_index} is not an object")
        segment = cast("dict[str, object]", value)
        start = _time(segment.get("start", 0.0), path=f"segments/{segment_index}/start")
        end = _time(segment.get("end", 0.0), path=f"segments/{segment_index}/end")
        speaker = _speaker_for(index, start, end, fill_nearest=fill_nearest)
        if speaker is not None:
            segment["speaker"] = speaker
        words_value = segment.get("words")
        if words_value is None:
            continue
        if not isinstance(words_value, list):
            raise TypeError(f"aligned segment {segment_index} words must be a list")
        for word_index, word_value in enumerate(cast("list[object]", words_value)):
            if not isinstance(word_value, dict):
                raise TypeError(
                    f"aligned segment {segment_index} word {word_index} is not an object"
                )
            word = cast("dict[str, object]", word_value)
            if "start" not in word:
                continue
            word_start = _time(
                word["start"], path=f"segments/{segment_index}/words/{word_index}/start"
            )
            word_end = _time(
                word.get("end", word_start),
                path=f"segments/{segment_index}/words/{word_index}/end",
            )
            word_speaker = _speaker_for(
                index,
                word_start,
                word_end,
                fill_nearest=fill_nearest,
            )
            if word_speaker is not None:
                word["speaker"] = word_speaker
    return assigned


def assignment_for(
    *,
    build: str,
    source: CheckpointSource,
    alignment_checkpoint: ArtifactRefV2,
    speaker_turns_checkpoint: ArtifactRefV2,
    resource_profile: SpeechResourceProfile,
    model_manifest: SpeechModelManifest,
    execution_topology: ExecutionTopology,
    aligned_payload: dict[str, object],
    diarization: object,
    configuration: SpeakerAssignmentConfig | None = None,
) -> SpeechAssignmentV1:
    """Construct strict canonical CPU-join content over both immutable inputs."""
    config = configuration or SpeakerAssignmentConfig()
    turns = parse_speaker_turns(diarization)
    rows = [{"start": turn.start, "end": turn.end, "speaker": turn.speaker} for turn in turns]
    raw = assign_word_speakers_v386(
        aligned_payload,
        rows,
        fill_nearest=config.fill_nearest,
    )
    normalized, diagnostics = normalize_payload({"raw": raw, "diarization": rows})
    if not isinstance(normalized, dict):
        raise TypeError("assignment payload must be an object")
    normalized_map = cast("dict[str, object]", normalized)
    raw_value, rows_value = normalized_map.get("raw"), normalized_map.get("diarization")
    if not isinstance(raw_value, dict) or not isinstance(rows_value, list):
        raise TypeError("assignment payload normalization changed its shape")
    return SpeechAssignmentV1(
        build=build,
        source=source,
        alignment_checkpoint=alignment_checkpoint,
        speaker_turns_checkpoint=speaker_turns_checkpoint,
        configuration=config,
        configuration_sha256=config.sha256,
        resource_profile=resource_profile,
        model_manifest=model_manifest,
        execution_topology=execution_topology,
        raw=cast("dict[str, Any]", raw_value),
        diarization=cast("list[dict[str, object]]", rows_value),
        payload_diagnostics=diagnostics,
    )
