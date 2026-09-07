"""Segment-any-Text sentences.

The model's own output moves with its version, so nothing here asserts bytes.
What is asserted is the contract the rest of the substrate depends on: the
sentences tile the words in order with no gaps and no overlaps, every one of
them carries a speaker and a time that belongs to a real word, and the
character mapping that produced them survived the round trip.

Everything that loads the model carries `@pytest.mark.models` and runs only
when `TEMNIA_MODEL_TESTS=1` (conftest.py). The mapping helpers are pure and are
tested without one.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from conftest import SubstrateFixture
from temnia_pipeline.contracts import TranscriptWord, WordTiming
from temnia_pipeline.substrate.model import Layers
from temnia_pipeline.substrate.protocol import Segmenter
from temnia_pipeline.substrate.sat import SaTSegmenter, word_char_starts, word_groups

MIN_SENTENCES = 5
MAX_SENTENCES = 20


@dataclass
class FakeWord:
    """A `GridWord` carrying only what the character mapping reads."""

    text: str
    startMs: int = 0  # noqa: N815
    endMs: int = 0  # noqa: N815
    speaker: str | None = None


def test_the_word_offsets_are_the_offsets_of_the_joined_text() -> None:
    words = [FakeWord("Hello"), FakeWord("there,"), FakeWord("friend.")]
    text, starts = word_char_starts(words)
    assert text == "Hello there, friend."
    assert starts == [0, 6, 13]
    for word, start in zip(words, starts, strict=True):
        assert text[start : start + len(word.text)] == word.text


def test_a_word_belongs_to_the_segment_holding_its_first_character() -> None:
    # "Hello there, friend." split after "Hello ": the second segment opens
    # inside no word, and a boundary two characters into "there," would still
    # leave that word in the first segment.
    starts = [0, 6, 13]
    assert word_groups(["Hello ", "there, friend."], starts) == [[0], [1, 2]]
    assert word_groups(["Hello th", "ere, friend."], starts) == [[0, 1], [2]]


def test_a_segment_holding_no_word_is_dropped() -> None:
    assert word_groups(["Hello ", "there, friend.", ""], [0, 6, 13]) == [[0], [1, 2], []]


@pytest.mark.models
def test_the_sentences_tile_the_words_in_order(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("speech-40s")
    layers = SaTSegmenter().segment(transcript.words)
    assert MIN_SENTENCES <= len(layers.sentences) <= MAX_SENTENCES
    expected_start = 0
    for sentence in layers.sentences:
        assert sentence.word_start == expected_start
        assert sentence.word_end >= sentence.word_start
        expected_start = sentence.word_end + 1
    assert expected_start == len(transcript.words)


@pytest.mark.models
def test_every_sentence_has_a_speaker_and_real_times(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("speech-40s")
    layers = SaTSegmenter().segment(transcript.words)
    for sentence in layers.sentences:
        assert sentence.speaker is not None
        assert sentence.start_ms == transcript.words[sentence.word_start].startMs
        assert sentence.end_ms == max(
            word.endMs for word in transcript.words[sentence.word_start : sentence.word_end + 1]
        )
        assert sentence.text == " ".join(
            word.text for word in transcript.words[sentence.word_start : sentence.word_end + 1]
        )


@pytest.mark.models
def test_the_paragraphs_tile_the_sentences_and_the_provenance_says_which_rule(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("speech-40s")
    for paragraphs, source in ((False, "legacy_rule"), (True, "sat")):
        layers = SaTSegmenter(paragraphs=paragraphs).segment(transcript.words)
        assert layers.provenance.params["paragraph_source"] == source
        assert layers.provenance.models["sat"] == "sat-3l-sm"
        expected_start = 0
        for paragraph in layers.paragraphs:
            assert paragraph.sentence_start == expected_start
            expected_start = paragraph.sentence_end + 1
        assert expected_start == len(layers.sentences)
        for candidate in layers.candidates:
            assert candidate.ms == layers.sentences[candidate.sentence_id].start_ms


@pytest.mark.models
def test_a_transcript_with_no_punctuation_still_gets_sentences(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    """The point of the layer: the legacy rule would return one sentence."""
    transcript, _ = load_substrate("speech-40s")
    stripped = [
        word.model_copy(update={"text": word.text.strip(".,!?")}) for word in transcript.words
    ]
    layers = SaTSegmenter().segment(stripped)
    assert len(layers.sentences) > 1


def test_an_empty_transcript_needs_no_model() -> None:
    segmenter = SaTSegmenter()
    assert isinstance(segmenter, Segmenter)
    layers = segmenter.segment([])
    assert isinstance(layers, Layers)
    assert layers.sentences == ()
    assert layers.paragraphs == ()
    assert layers.provenance.segmenter == "sat"
    assert layers.provenance.params["paragraph_source"] == "none"


def test_an_adapter_needs_a_style_and_a_language_together() -> None:
    word = TranscriptWord(
        confidence=None, endMs=10, speaker="0", startMs=0, text="Hello.", timing=WordTiming.aligned
    )
    with pytest.raises(ValueError, match="style_or_domain and language together"):
        SaTSegmenter(style_or_domain="ted").segment([word])
