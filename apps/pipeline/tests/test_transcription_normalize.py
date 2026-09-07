"""The normaliser, on the recorded speech response and on the edge fixture.

Both fixtures are files an engine could really have produced, not shapes
invented to match this code. The edge one exists because every branch in
`normalize.py` is there for something WhisperX actually emits, and a synthetic
mock would have agreed with whatever the normaliser happened to do.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, cast

import pytest

from temnia_pipeline.contracts import TranscriptProvider, WordTiming
from temnia_pipeline.transcription.normalize import (
    DURATION_SLACK_MS,
    TranscriptContractError,
    assert_contract,
    normalize_speaker,
    normalize_whisperx,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
SPEECH = FIXTURES / "speech-40s.whisperx.json"
EDGE = FIXTURES / "edge-cases.whisperx.json"

# The measured duration of apps/web/e2e/fixtures/speech-40s.mp4.
SPEECH_DURATION_MS = 40_116

PROVIDER = TranscriptProvider(name="whisperx", model="large-v3", version="3.8.6")


def load(path: Path) -> dict[str, Any]:
    """Python's json reads the bare NaN a whisperx score can be; a browser's would not."""
    loaded: object = json.loads(path.read_text())
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


@pytest.fixture(scope="module")
def speech() -> dict[str, Any]:
    return load(SPEECH)


@pytest.fixture(scope="module")
def edge() -> dict[str, Any]:
    return load(EDGE)


class TestTheRecordedSpeechResponse:
    def test_every_word_survives_with_integer_milliseconds(self, speech: dict[str, Any]) -> None:
        result = normalize_whisperx(speech, SPEECH_DURATION_MS, PROVIDER)
        assert result.version == 1
        assert result.language == "en"
        assert result.durationMs == SPEECH_DURATION_MS
        assert result.provider == PROVIDER
        assert len(result.words) == 93
        assert all(isinstance(word.startMs, int) for word in result.words)
        assert result.words[0].text == "Welcome"
        assert result.words[0].startMs == 500
        assert result.words[-1].text == "today."

    def test_pyannote_labels_become_short_ids_in_first_appearance_order(
        self, speech: dict[str, Any]
    ) -> None:
        result = normalize_whisperx(speech, SPEECH_DURATION_MS, PROVIDER)
        assert result.speakers == ["0", "1"]

    def test_the_turns_are_the_seven_the_script_has(self, speech: dict[str, Any]) -> None:
        """Consecutive words of one speaker collapse into one utterance, not one per segment."""
        result = normalize_whisperx(speech, SPEECH_DURATION_MS, PROVIDER)
        assert len(result.utterances) == 7
        assert [turn.speaker for turn in result.utterances] == ["0", "1", "0", "1", "0", "1", "0"]
        assert result.utterances[0].startMs == 500

    def test_every_word_is_aligned(self, speech: dict[str, Any]) -> None:
        assert all(
            word.timing == WordTiming.aligned
            for word in normalize_whisperx(speech, SPEECH_DURATION_MS, PROVIDER).words
        )

    def test_a_transcript_that_stops_early_is_accepted(self, speech: dict[str, Any]) -> None:
        """Silence at the end of a recording is not truncation."""
        result = normalize_whisperx(speech, SPEECH_DURATION_MS * 200, PROVIDER)
        assert result.words[-1].endMs < result.durationMs

    def test_a_transcript_of_other_bytes_is_refused(self, speech: dict[str, Any]) -> None:
        """Words past the end plus the slack mean this is not our media."""
        with pytest.raises(TranscriptContractError, match="not of this media"):
            normalize_whisperx(speech, 10_000, PROVIDER)

    def test_the_slack_is_exactly_two_seconds(self, speech: dict[str, Any]) -> None:
        last: float = max(
            word["end"] for segment in speech["segments"] for word in segment["words"]
        )
        just_inside = round(last * 1000) - DURATION_SLACK_MS
        assert normalize_whisperx(speech, just_inside, PROVIDER).durationMs == just_inside
        with pytest.raises(TranscriptContractError):
            normalize_whisperx(speech, just_inside - 1, PROVIDER)


class TestTheEdgeFixture:
    def test_a_word_with_no_timestamps_is_interpolated_and_flagged(
        self, edge: dict[str, Any]
    ) -> None:
        """Digits come back unaligned; dropping them would lose text the user hears."""
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        digits = next(word for word in result.words if word.text == "1998")
        assert digits.timing == WordTiming.interpolated
        # Between "recorded" (ends 1.31) and "hours" (starts 2.05).
        assert digits.startMs == 1310
        assert digits.endMs == 2050

    def test_a_word_missing_only_its_end_takes_the_next_start(self, edge: dict[str, Any]) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        planned = next(word for word in result.words if word.text == "planned")
        assert planned.timing == WordTiming.interpolated
        assert planned.startMs == 7140
        assert planned.endMs == 8600

    def test_a_nan_score_becomes_no_confidence(self, edge: dict[str, Any]) -> None:
        """NaN reaches the normaliser as readily as None and would poison every comparison."""
        raw_score: float = edge["segments"][0]["words"][4]["score"]
        assert math.isnan(raw_score)
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert next(w for w in result.words if w.text == "last").confidence is None

    def test_a_word_without_a_speaker_takes_the_enclosing_segment(
        self, edge: dict[str, Any]
    ) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert next(w for w in result.words if w.text == "month.").speaker == "0"
        assert next(w for w in result.words if w.text == "than").speaker == "1"
        assert next(w for w in result.words if w.text == "we").speaker == "1"

    def test_an_empty_segment_is_dropped_rather_than_becoming_a_word(
        self, edge: dict[str, Any]
    ) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert all(word.text.strip() for word in result.words)
        assert not any(3600 <= word.startMs < 5400 for word in result.words)

    def test_words_the_alignment_moved_are_sorted(self, edge: dict[str, Any]) -> None:
        """Everything downstream binary-searches the array; the sort happens once, here."""
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        starts = [word.startMs for word in result.words]
        assert starts == sorted(starts)
        assert [w.text for w in result.words if w.startMs in {5400, 5620}] == ["is", "That"]

    def test_a_whitespace_only_word_is_dropped(self, edge: dict[str, Any]) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert [w.text for w in result.words if w.startMs >= 9600] == ["Right."]

    def test_an_unpadded_speaker_label_normalises_too(self, edge: dict[str, Any]) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert next(w for w in result.words if w.text == "Right.").speaker == "1"


class TestTheContractCheck:
    def test_speaker_labels(self) -> None:
        assert normalize_speaker("SPEAKER_00") == "0"
        assert normalize_speaker("SPEAKER_07") == "7"
        assert normalize_speaker("SPEAKER_12") == "12"
        assert normalize_speaker("Rajesh") == "Rajesh"
        assert normalize_speaker("") is None
        assert normalize_speaker(None) is None
        assert normalize_speaker(3) is None

    def test_no_speech_is_a_real_transcript_not_an_error(self) -> None:
        result = normalize_whisperx({"language": "en", "segments": []}, 24_000, PROVIDER)
        assert result.words == []
        assert result.utterances == []
        assert result.speakers == []

    def test_an_undetected_language_is_recorded_rather_than_guessed(self) -> None:
        assert normalize_whisperx({"segments": []}, 1000, PROVIDER).language == "und"

    def test_the_sort_rule_is_checked_and_not_merely_documented(
        self, speech: dict[str, Any]
    ) -> None:
        result = normalize_whisperx(speech, SPEECH_DURATION_MS, PROVIDER)
        result.words = [result.words[5], *result.words[:5], *result.words[6:]]
        with pytest.raises(TranscriptContractError, match="sorted by startMs"):
            assert_contract(result)
