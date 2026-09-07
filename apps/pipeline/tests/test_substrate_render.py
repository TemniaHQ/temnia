"""The rendering S4's prompts quote.

Two gates. The committed `<name>.layers.*.txt` files are the `legacy`
segmenter's output byte for byte, so a change to the format is a diff in a
review rather than a surprise in a prompt; `scripts/dump_layers.py --check`
is the same comparison and is what the gate runs. And a parser here reads the
rendering back into ids, times and glyphs, so the format is asserted to be
machine-readable rather than merely looking right, including for SaT and the
change-point layer, whose bytes move with their models and are therefore
checked by properties.
"""

import re
from collections.abc import Callable

import pytest

from conftest import SUBSTRATE_FIXTURE_DIR, SubstrateFixture
from temnia_pipeline.substrate.changepoint import EmbeddingChangePointSegmenter
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter
from temnia_pipeline.substrate.model import (
    BoundaryCandidate,
    Layers,
    Paragraph,
    Provenance,
    Sentence,
)
from temnia_pipeline.substrate.render import (
    id_width,
    render_coarse,
    render_fine,
    stamp_hms,
)

COARSE_LINE = re.compile(r"^P(\d+) (\d\d:\d\d:\d\d) (S\S+): (.*)$")
FINE_LINE = re.compile(r"^s(\d+) (\d\d:\d\d:\d\d) (S\S+): (.{4}) (.{5}) (.*)$")


def parse_fine(line: str) -> tuple[int, int, str, set[str], float | None, str]:
    """Read one fine line back: id, seconds, speaker, glyphs, topic score, text."""
    match = FINE_LINE.match(line)
    assert match is not None, f"unparsable fine line: {line!r}"
    hours, minutes, seconds = (int(part) for part in match.group(2).split(":"))
    topic = match.group(5).strip()
    return (
        int(match.group(1)),
        (hours * 3600 + minutes * 60 + seconds) * 1000,
        match.group(3),
        set(match.group(4).strip()),
        float(topic.lstrip("§")) if topic else None,
        match.group(6),
    )


def parse_coarse(line: str) -> tuple[int, int, str, str]:
    """Read one coarse line back: id, seconds, speaker, text."""
    match = COARSE_LINE.match(line)
    assert match is not None, f"unparsable coarse line: {line!r}"
    hours, minutes, seconds = (int(part) for part in match.group(2).split(":"))
    return (
        int(match.group(1)),
        (hours * 3600 + minutes * 60 + seconds) * 1000,
        match.group(3),
        match.group(4),
    )


def test_the_stamp_is_hours_minutes_seconds_truncated() -> None:
    assert stamp_hms(0) == "00:00:00"
    assert stamp_hms(999) == "00:00:00"
    assert stamp_hms(1000) == "00:00:01"
    assert stamp_hms(59_999) == "00:00:59"
    assert stamp_hms(3_600_000) == "01:00:00"
    assert stamp_hms(4_442_000) == "01:14:02"
    assert stamp_hms(360_000_000) == "100:00:00"


def test_the_id_width_is_the_legacys_until_the_count_needs_more() -> None:
    assert id_width(0, 3) == 3
    assert id_width(1000, 3) == 3
    assert id_width(1001, 3) == 4
    assert id_width(9999, 4) == 4
    assert id_width(10_001, 4) == 5


def test_the_committed_renderings_are_what_the_legacy_segmenter_produces(
    substrate: SubstrateFixture, substrate_name: str
) -> None:
    transcript, shots = substrate
    layers = LegacyRulesSegmenter().segment(transcript.words, shot_times_ms=shots)
    coarse = (SUBSTRATE_FIXTURE_DIR / f"{substrate_name}.layers.coarse.txt").read_bytes()
    fine = (SUBSTRATE_FIXTURE_DIR / f"{substrate_name}.layers.fine.txt").read_bytes()
    assert render_coarse(layers).encode() == coarse
    assert render_fine(layers, shots).encode() == fine


def test_the_rendering_parses_back_to_the_same_ids_and_times(
    substrate: SubstrateFixture,
) -> None:
    transcript, shots = substrate
    layers = LegacyRulesSegmenter().segment(transcript.words, shot_times_ms=shots)
    _assert_round_trip(layers, shots)


def _assert_round_trip(layers: Layers, shots: list[int]) -> None:
    fine = render_fine(layers, shots)
    lines = fine.split("\n") if fine else []
    assert len(lines) == len(layers.sentences)
    starts = {paragraph.sentence_start for paragraph in layers.paragraphs}
    topics = {
        candidate.sentence_id: candidate.score
        for candidate in layers.candidates
        if candidate.kind == "topic"
    }
    for line, sentence in zip(lines, layers.sentences, strict=True):
        id_, stamp_ms, speaker, glyphs, score, text = parse_fine(line)
        assert id_ == sentence.id
        assert stamp_ms == sentence.start_ms - sentence.start_ms % 1000
        assert speaker.startswith("S")
        assert text == sentence.text
        assert ("¶" in glyphs) == (sentence.id in starts)
        expected = topics.get(sentence.id)
        assert (score is None) == (expected is None)
        if expected is not None and score is not None:
            assert abs(score - expected) < 0.005

    coarse = render_coarse(layers)
    coarse_lines = coarse.split("\n") if coarse else []
    assert len(coarse_lines) == len(layers.paragraphs)
    for line, paragraph in zip(coarse_lines, layers.paragraphs, strict=True):
        id_, stamp_ms, _, text = parse_coarse(line)
        assert id_ == paragraph.id
        assert stamp_ms == paragraph.start_ms - paragraph.start_ms % 1000
        assert text == " ".join(
            sentence.text
            for sentence in layers.sentences[paragraph.sentence_start : paragraph.sentence_end + 1]
        )


def test_a_shot_inside_the_snap_window_is_marked_and_one_outside_is_not(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, shots = load_substrate("synthetic-edges")
    layers = LegacyRulesSegmenter().segment(transcript.words, shot_times_ms=shots)
    marked = {
        parse_fine(line)[0]
        for line in render_fine(layers, shots).split("\n")
        if "⌖" in parse_fine(line)[3]
    }
    expected = {
        sentence.id
        for sentence in layers.sentences
        if any(abs(shot - sentence.start_ms) <= 500 for shot in shots)
    }
    assert marked == expected
    assert marked, "synthetic-edges carries a shot inside the snap window"
    assert "⌖" not in render_fine(layers)


def test_empty_layers_render_to_nothing() -> None:
    layers = LegacyRulesSegmenter().segment([])
    assert render_coarse(layers) == ""
    assert render_fine(layers) == ""


def test_the_window_bounds_do_not_move_the_ids(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("two-topics")
    layers = LegacyRulesSegmenter().segment(transcript.words)
    window = render_fine(layers, (), 40, 44).split("\n")
    assert len(window) == 5
    assert [parse_fine(line)[0] for line in window] == [40, 41, 42, 43, 44]
    assert window[0] in render_fine(layers).split("\n")


def test_a_topic_candidate_prints_its_score() -> None:
    sentence = Sentence(
        id=0,
        start_ms=4_442_000,
        end_ms=4_445_000,
        word_start=0,
        word_end=1,
        speaker="1",
        text="A topic turns here.",
        is_question=False,
    )
    layers = Layers(
        words=[],
        sentences=(sentence,),
        paragraphs=(
            Paragraph(
                id=0,
                start_ms=sentence.start_ms,
                end_ms=sentence.end_ms,
                sentence_start=0,
                sentence_end=0,
                speaker="1",
            ),
        ),
        candidates=(
            BoundaryCandidate(sentence_id=0, ms=sentence.start_ms, score=0.834, kind="topic"),
        ),
        provenance=Provenance(segmenter="test"),
    )
    line = render_fine(layers)
    assert line == "s0000 01:14:02 S2: ¶|   §0.83 A topic turns here."
    assert parse_fine(line)[4] == 0.83


@pytest.mark.models
def test_the_change_point_rendering_parses_back_too(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("two-topics")
    layers = EmbeddingChangePointSegmenter(LegacyRulesSegmenter()).segment(transcript.words)
    assert layers.candidates
    _assert_round_trip(layers, [])
    assert "§" in render_fine(layers)
