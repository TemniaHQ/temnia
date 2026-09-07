"""The shared shape, and the legacy rules dressed in it.

The port itself is covered by `test_substrate_grid.py`,
`test_substrate_paragraphs.py` and the byte-parity gate in
`test_substrate_parity.py`. What is new here is the adapter: that
`LegacyRulesSegmenter` says exactly what `build_cut_grid` says, and that
`pack_paragraphs` — the legacy paragraph rule restated over the shared
`Sentence` so `SaTSegmenter` can reuse it — has not drifted from the port's own
packing on any fixture.
"""

from collections.abc import Callable

from conftest import SubstrateFixture
from temnia_pipeline.substrate.grid import build_cut_grid
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter
from temnia_pipeline.substrate.model import Layers
from temnia_pipeline.substrate.protocol import Segmenter


def test_the_adapter_reports_the_ports_own_sentences(substrate: SubstrateFixture) -> None:
    transcript, _ = substrate
    grid = build_cut_grid(transcript.words)
    layers = LegacyRulesSegmenter().segment(transcript.words)
    assert [
        (
            sentence.id,
            sentence.start_ms,
            sentence.end_ms,
            sentence.word_start,
            sentence.word_end,
            sentence.speaker,
            sentence.text,
            sentence.is_question,
        )
        for sentence in layers.sentences
    ] == [
        (
            sentence.id,
            sentence.start_ms,
            sentence.end_ms,
            sentence.start_word,
            sentence.end_word,
            sentence.speaker,
            sentence.text,
            sentence.question,
        )
        for sentence in grid.sentences
    ]


def test_the_restated_paragraph_rule_has_not_drifted_from_the_port(
    substrate: SubstrateFixture,
) -> None:
    transcript, _ = substrate
    grid = build_cut_grid(transcript.words)
    layers = LegacyRulesSegmenter().segment(transcript.words)
    assert [
        (
            paragraph.id,
            paragraph.start_ms,
            paragraph.end_ms,
            paragraph.sentence_start,
            paragraph.sentence_end,
            paragraph.speaker,
        )
        for paragraph in layers.paragraphs
    ] == [
        (
            paragraph.id,
            paragraph.start_ms,
            paragraph.end_ms,
            paragraph.start_sentence,
            paragraph.end_sentence,
            paragraph.speaker,
        )
        for paragraph in grid.paragraphs
    ]


def test_every_candidate_is_a_paragraph_start_that_a_rule_caused(
    substrate: SubstrateFixture,
) -> None:
    transcript, _ = substrate
    layers = LegacyRulesSegmenter().segment(transcript.words)
    starts = {paragraph.sentence_start for paragraph in layers.paragraphs}
    for candidate in layers.candidates:
        assert candidate.sentence_id in starts
        assert candidate.sentence_id != 0, "the first paragraph opens the media"
        assert candidate.kind in {"turn", "pause"}
        assert candidate.score == 1.0
        assert candidate.ms == layers.sentences[candidate.sentence_id].start_ms
    # A paragraph that broke only on the 120-word cap is not a candidate, so
    # there are never more candidates than paragraph starts after the first.
    assert len(layers.candidates) <= max(len(layers.paragraphs) - 1, 0)


def test_boundaries_are_ascending_and_inside_the_media(substrate: SubstrateFixture) -> None:
    transcript, _ = substrate
    layers = LegacyRulesSegmenter().segment(transcript.words)
    boundaries = layers.boundaries_ms()
    assert list(boundaries) == sorted(set(boundaries))
    for ms in boundaries:
        assert 0 <= ms <= transcript.durationMs


def test_a_cap_break_makes_a_paragraph_and_no_candidate(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("synthetic-edges")
    layers = LegacyRulesSegmenter().segment(transcript.words)
    proposed = {candidate.sentence_id for candidate in layers.candidates}
    cap_only = [
        paragraph for paragraph in layers.paragraphs[1:] if paragraph.sentence_start not in proposed
    ]
    assert cap_only, "synthetic-edges packs a paragraph to exactly the 120-word cap"


def test_an_empty_transcript_gives_empty_layers() -> None:
    layers = LegacyRulesSegmenter().segment([])
    assert layers.sentences == ()
    assert layers.paragraphs == ()
    assert layers.candidates == ()
    assert layers.boundaries_ms() == ()
    assert layers.sentence_starts_ms() == ()


def test_the_adapter_satisfies_the_seam() -> None:
    segmenter: Segmenter = LegacyRulesSegmenter()
    assert isinstance(segmenter, Segmenter)
    assert segmenter.name == "legacy"
    layers = segmenter.segment([])
    assert isinstance(layers, Layers)
    assert layers.provenance.segmenter == "legacy"
    assert layers.provenance.versions["legacy_oracle"] == "b642b77"
