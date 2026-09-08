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

    def test_a_word_the_alignment_moved_keeps_its_place_and_is_flagged(
        self, edge: dict[str, Any]
    ) -> None:
        """ "That is" stays "That is": the order is what was said, the time is the estimate.

        Sorting by time turned it into "is That" and a test once expected that
        (S2 review, I09). The moved word takes its predecessor's start, so the
        contract's ascending starts still hold for the binary searches.
        """
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        that = next(index for index, word in enumerate(result.words) if word.text == "That")
        moved = result.words[that + 1]
        assert moved.text == "is"
        assert result.words[that].timing == WordTiming.aligned
        assert moved.timing == WordTiming.interpolated
        assert moved.startMs == result.words[that].startMs
        assert moved.endMs >= moved.startMs
        starts = [word.startMs for word in result.words]
        assert starts == sorted(starts)

    def test_a_whitespace_only_word_is_dropped(self, edge: dict[str, Any]) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert [w.text for w in result.words if w.startMs >= 9600] == ["Right."]

    def test_an_unpadded_speaker_label_normalises_too(self, edge: dict[str, Any]) -> None:
        result = normalize_whisperx(edge, 12_000, PROVIDER)
        assert next(w for w in result.words if w.text == "Right.").speaker == "1"


def _word(text: str, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    word: dict[str, Any] = {"word": text}
    if start is not None:
        word["start"] = start
    if end is not None:
        word["end"] = end
    return word


def _segment(
    words: list[dict[str, Any]], start: float | None = 0.0, end: float | None = 3.0
) -> dict[str, Any]:
    segment: dict[str, Any] = {"words": words}
    if start is not None:
        segment["start"] = start
    if end is not None:
        segment["end"] = end
    return segment


def _times(segments: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    result = normalize_whisperx({"segments": segments}, 60_000, PROVIDER)
    return [(word.text, word.startMs, word.endMs) for word in result.words]


class TestTheShape:
    """A response that is not whisperx's shape is refused, never quietly emptied (review I07)."""

    @pytest.mark.parametrize(
        "raw",
        [
            {"unexpected": 123},
            {"segments": None},
            {"segments": "hello"},
            {"segments": [1]},
            {"segments": [{"words": "x"}]},
            {"segments": [{"words": [1]}]},
        ],
    )
    def test_a_response_that_is_not_a_transcript_is_a_contract_error(
        self, raw: dict[str, Any]
    ) -> None:
        with pytest.raises(TranscriptContractError):
            normalize_whisperx(raw, 10_000, PROVIDER)

    def test_a_segment_with_text_and_no_word_list_keeps_its_words(self) -> None:
        """Alignment never saw it; a person heard it. The words are kept, untimed and flagged."""
        result = normalize_whisperx(
            {"segments": [{"start": 0.0, "end": 2.0, "text": "hello there"}]}, 12_000, PROVIDER
        )
        assert [(w.text, w.startMs, w.endMs, w.timing) for w in result.words] == [
            ("hello", 0, 1000, WordTiming.interpolated),
            ("there", 1000, 2000, WordTiming.interpolated),
        ]

    def test_a_segment_with_an_empty_word_list_is_music_or_silence(self) -> None:
        result = normalize_whisperx(
            {"segments": [{"start": 0.0, "end": 2.0, "text": "♪", "words": []}]}, 12_000, PROVIDER
        )
        assert result.words == []


class TestTheFill:
    """Holes are filled over whole runs, and the evidence's own zeros and crossings are honoured."""

    def test_a_run_of_untimed_words_shares_the_span_by_length(self) -> None:
        # The review's reproduction: the second word came out as [2500, 2500].
        assert _times(
            [_segment([_word("a", 0, 0.5), _word("bb"), _word("cc"), _word("d", 2.5, 3.0)])]
        ) == [("a", 0, 500), ("bb", 500, 1500), ("cc", 1500, 2500), ("d", 2500, 3000)]
        assert _times(
            [_segment([_word("a", 0, 0.5), _word("b"), _word("cccc"), _word("d", 2.5, 3.0)])]
        ) == [
            ("a", 0, 500),
            ("b", 500, 900),
            ("cccc", 900, 2500),
            ("d", 2500, 3000),
        ]

    def test_a_word_missing_one_time_takes_the_nearest_anchor_on_that_side(self) -> None:
        assert _times([_segment([_word("planned", 7.14), _word("for.", 8.6, 8.7)])]) == [
            ("planned", 7140, 8600),
            ("for.", 8600, 8700),
        ]
        assert _times([_segment([_word("a", 0, 0.5), _word("b"), _word("c", None, 1.1)])]) == [
            ("a", 0, 500),
            ("b", 500, 800),
            ("c", 800, 1100),
        ]

    def test_a_zero_is_an_anchor(self) -> None:
        assert _times([_segment([_word("a", 0, 0), _word("b"), _word("c", 0.1, 0.2)])]) == [
            ("a", 0, 0),
            ("b", 0, 100),
            ("c", 100, 200),
        ]

    def test_anchors_that_cross_invent_no_duration(self) -> None:
        result = normalize_whisperx(
            {"segments": [_segment([_word("a", 0, 0.9), _word("b"), _word("c", 0.6, 0.7)])]},
            60_000,
            PROVIDER,
        )
        assert [(w.text, w.startMs, w.endMs) for w in result.words] == [
            ("a", 0, 900),
            ("b", 900, 900),
            ("c", 900, 900),
        ]
        assert [w.timing for w in result.words] == [
            WordTiming.aligned,
            WordTiming.interpolated,
            WordTiming.interpolated,
        ]

    def test_a_leading_run_takes_the_segment_start_and_a_trailing_one_its_end(self) -> None:
        assert _times([_segment([_word("a"), _word("b", 2.0, 3.0)], start=1.0)]) == [
            ("a", 1000, 2000),
            ("b", 2000, 3000),
        ]
        assert _times([_segment([_word("a", 0, 1.0), _word("b")], end=3.0)]) == [
            ("a", 0, 1000),
            ("b", 1000, 3000),
        ]

    def test_words_with_no_timing_anywhere_start_at_zero_and_last_nothing(self) -> None:
        assert _times([_segment([_word("x"), _word("y")], start=None, end=None)]) == [
            ("x", 0, 0),
            ("y", 0, 0),
        ]


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


@pytest.mark.parametrize(
    "word", [{}, {"text": "speech"}, {"word": None}, {"word": 3}, {"word": {}}]
)
def test_malformed_word_fields_are_terminal_contract_errors(word: dict[str, object]) -> None:
    with pytest.raises(TranscriptContractError, match="string word field"):
        normalize_whisperx({"segments": [{"words": [word]}]}, 4000, PROVIDER)


@pytest.mark.parametrize("segment", [{}, {"words": None}, {"text": None}, {"text": 3, "words": []}])
def test_a_segment_must_supply_valid_words_or_text(segment: dict[str, object]) -> None:
    with pytest.raises(TranscriptContractError):
        normalize_whisperx({"segments": [segment]}, 4000, PROVIDER)


@pytest.mark.parametrize("empty", [None, []])
def test_empty_alignment_preserves_nonempty_speech(empty: object) -> None:
    result = normalize_whisperx(
        {"segments": [{"start": 1, "end": 3, "text": "actual speech", "words": empty}]},
        4000,
        PROVIDER,
    )
    assert [(w.text, w.startMs, w.endMs) for w in result.words] == [
        ("actual", 1000, 2000),
        ("speech", 2000, 3000),
    ]
    assert all(w.timing == WordTiming.interpolated for w in result.words)


@pytest.mark.parametrize("text", ["", "  ", "♪ ♫", "[Music]", "[silence]", "(no speech)"])
def test_explicit_nonspeech_does_not_become_fabricated_words(text: str) -> None:
    empty: list[object] = []
    for words in (None, empty):
        result = normalize_whisperx(
            {"segments": [{"start": 0, "end": 3, "text": text, "words": words}]},
            4000,
            PROVIDER,
        )
        assert result.words == []


def test_the_spoken_word_music_is_preserved() -> None:
    result = normalize_whisperx(
        {"segments": [{"start": 0, "end": 3, "text": "music", "words": []}]},
        4000,
        PROVIDER,
    )
    assert [word.text for word in result.words] == ["music"]


def test_a_middle_segment_never_interpolates_across_its_neighbours_pauses() -> None:
    assert _times(
        [
            _segment([_word("before", 0, 1)], start=0, end=1),
            {"start": 10, "end": 12, "text": "hello there"},
            _segment([_word("after", 30, 31)], start=30, end=31),
        ]
    ) == [
        ("before", 0, 1000),
        ("hello", 10000, 11000),
        ("there", 11000, 12000),
        ("after", 30000, 31000),
    ]


def test_adjacent_alignment_failures_keep_separate_segment_bounds() -> None:
    assert _times(
        [
            {"start": 0, "end": 2, "text": "first words", "words": []},
            {"start": 20, "end": 22, "text": "later words", "words": []},
        ]
    ) == [
        ("first", 0, 1000),
        ("words", 1000, 2000),
        ("later", 20000, 21000),
        ("words", 21000, 22000),
    ]


def test_partial_word_anchors_in_middle_segments_respect_their_own_bounds() -> None:
    assert _times(
        [
            _segment([_word("before", 0, 1)]),
            _segment([_word("start"), _word("middle", 11, 12), _word("end")], start=10, end=13),
            _segment([_word("after", 30, 31)]),
        ]
    ) == [
        ("before", 0, 1000),
        ("start", 10000, 11000),
        ("middle", 11000, 12000),
        ("end", 12000, 13000),
        ("after", 30000, 31000),
    ]


def test_overlapping_segments_preserve_aligned_evidence_and_bound_missing_words() -> None:
    result = normalize_whisperx(
        {
            "segments": [
                _segment([_word("held", 0, 10)], start=0, end=10),
                _segment([_word("yes"), _word("indeed", 6, 7)], start=5, end=7),
            ]
        },
        12000,
        PROVIDER,
    )
    assert [(w.text, w.startMs, w.endMs) for w in result.words] == [
        ("held", 0, 10000),
        ("yes", 5000, 6000),
        ("indeed", 6000, 7000),
    ]
    assert [w.timing for w in result.words] == [
        WordTiming.aligned,
        WordTiming.interpolated,
        WordTiming.aligned,
    ]


@pytest.mark.parametrize("blank", ["", "  "])
def test_only_blank_alignment_tokens_cannot_erase_segment_speech(blank: str) -> None:
    result = normalize_whisperx(
        {"segments": [{"start": 1, "end": 3, "text": "actual speech", "words": [{"word": blank}]}]},
        4000,
        PROVIDER,
    )
    assert [word.text for word in result.words] == ["actual", "speech"]
    assert all(word.timing == WordTiming.interpolated for word in result.words)
