"""Display paragraphs, ported from the legacy `tests/transcript-paragraphs.test.ts`."""

from temnia_pipeline.contracts import TranscriptWord, WordTiming
from temnia_pipeline.substrate.paragraphs import (
    build_paragraphs,
    find_paragraph_index_for_word,
    find_word_index_at_time,
)


def word(start_ms: int, end_ms: int, speaker: str | None, text: str = "w") -> TranscriptWord:
    return TranscriptWord(
        confidence=0.9,
        endMs=end_ms,
        speaker=speaker,
        startMs=start_ms,
        text=text,
        timing=WordTiming.aligned,
    )


TWO_SPEAKERS = [
    word(0, 400, "0"),
    word(400, 800, "0"),
    word(900, 1300, "1"),
    word(1300, 1700, "1"),
]


def test_splits_on_a_speaker_change() -> None:
    paragraphs = build_paragraphs(TWO_SPEAKERS)
    assert len(paragraphs) == 2
    assert paragraphs[0].speaker == "0"
    assert paragraphs[1].speaker == "1"
    assert paragraphs[1].word_offset == 2


def test_splits_on_a_long_silence_within_one_speaker() -> None:
    paragraphs = build_paragraphs([word(0, 400, "0"), word(400, 800, "0"), word(5000, 5400, "0")])
    assert len(paragraphs) == 2
    assert paragraphs[1].start_ms == 5000


def test_caps_paragraph_size_for_a_monologue() -> None:
    words = [word(index * 100, index * 100 + 80, "0") for index in range(300)]
    paragraphs = build_paragraphs(words)
    assert len(paragraphs) > 1
    assert all(len(paragraph.words) <= 120 for paragraph in paragraphs)
    # The offsets tile the word array exactly, and the cap is 120, so the first
    # paragraph is 120 words and the second starts right after it.
    assert sum(len(paragraph.words) for paragraph in paragraphs) == 300
    assert paragraphs[1].word_offset == 120


def test_handles_an_empty_transcript() -> None:
    assert build_paragraphs([]) == []


def test_finds_the_word_at_a_time() -> None:
    words = [word(0, 400, "0"), word(500, 900, "0"), word(1000, 1400, "0")]
    assert find_word_index_at_time(words, -50) == -1
    assert find_word_index_at_time(words, 600) == 1
    # A word stays active through the silence after it.
    assert find_word_index_at_time(words, 950) == 1
    assert find_word_index_at_time(words, 99_999) == 2
    assert find_word_index_at_time([], 0) == -1


def test_maps_global_word_indices_to_their_paragraph() -> None:
    paragraphs = build_paragraphs(TWO_SPEAKERS)
    assert [find_paragraph_index_for_word(paragraphs, index) for index in range(4)] == [
        0,
        0,
        1,
        1,
    ]
    assert find_paragraph_index_for_word(paragraphs, -1) == -1
