"""Topic candidates from change-point detection over sentence embeddings.

`two-topics` is the fixture with a known answer: forty sentences on coaching a
forward pack, then forty on compound interest, with the gap at the join kept
under the legacy rule's 2500 ms so the pause rule is not handed the boundary
for free. If this layer works at all it finds sentence 40, and both the top
candidate's position and the number of candidates are asserted here.

The model-loading tests carry `@pytest.mark.models`; the target arithmetic and
the empty cases are pure and run always.
"""

from collections.abc import Callable, Sequence

import pytest

from conftest import SubstrateFixture
from temnia_pipeline.substrate.backends import MAX_CHANGE_POINT_UNITS
from temnia_pipeline.substrate.changepoint import (
    EmbeddingChangePointSegmenter,
    target_count,
)
from temnia_pipeline.substrate.factory import make_segmenter
from temnia_pipeline.substrate.grid import GridWord
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter
from temnia_pipeline.substrate.model import Layers, Provenance, Sentence

GOLD_SENTENCE_TOLERANCE = 2


def _sentence(id_: int, start_ms: int, end_ms: int) -> Sentence:
    return Sentence(
        id=id_,
        start_ms=start_ms,
        end_ms=end_ms,
        word_start=id_,
        word_end=id_,
        speaker="0",
        text="x",
        is_question=False,
    )


def test_the_target_is_the_rate_over_the_episodes_own_span() -> None:
    hour = [_sentence(0, 0, 10), _sentence(1, 3_600_000 - 10, 3_600_000)]
    assert target_count(hour, 6.0) == 6
    assert target_count(hour, 0.0) == 0
    # A forty-second clip asks for none: six an hour does not mean at least one.
    assert target_count([_sentence(0, 0, 40_000)], 6.0) == 0
    assert target_count([], 6.0) == 0


def test_a_transcript_too_short_to_split_needs_no_model() -> None:
    segmenter = EmbeddingChangePointSegmenter(LegacyRulesSegmenter(), min_sentences=4)
    analysis = segmenter.analyse([])
    assert analysis.fitted is None
    assert analysis.candidates() == ()
    assert analysis.candidates_at(1.0) == ()
    layers = analysis.layers()
    assert layers.candidates == ()
    assert layers.provenance.segmenter == "changepoint"
    assert layers.provenance.params["sentences_from"] == "legacy"


def test_the_factory_refuses_a_typo_and_a_self_reference() -> None:
    with pytest.raises(ValueError, match="takes no parameter target_per_hout"):
        make_segmenter("changepoint", target_per_hout=6)
    with pytest.raises(ValueError, match="cannot take its sentences from changepoint"):
        make_segmenter("changepoint", sentences_from="changepoint")
    with pytest.raises(ValueError, match="unknown segmenter"):
        make_segmenter("kmeans")
    with pytest.raises(TypeError, match="target_per_hour must be a number"):
        make_segmenter("changepoint", target_per_hour="six")


def test_the_factory_refuses_a_target_that_is_not_positive_and_the_dead_jump_knob() -> None:
    """An explicit zero used to become six and be recorded as six (S2 review, I14)."""
    with pytest.raises(ValueError, match="target_per_hour must be a positive number"):
        make_segmenter("changepoint", target_per_hour=0)
    with pytest.raises(ValueError, match="target_per_hour must be a positive number"):
        make_segmenter("changepoint", target_per_hour=-3)
    # KernelCPD ignores `jump`; a knob that is recorded and not applied is refused.
    with pytest.raises(ValueError, match="takes no parameter jump"):
        make_segmenter("changepoint", jump=2)


class _ManySentences:
    """A base segmenter answering with more sentences than the kernel can take."""

    name = "many"

    def __init__(self, count: int) -> None:
        self.count = count

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        _ = shot_times_ms
        sentences = tuple(_sentence(i, i * 1000, i * 1000 + 900) for i in range(self.count))
        return Layers(
            words=words,
            sentences=sentences,
            paragraphs=(),
            candidates=(),
            provenance=Provenance(segmenter=self.name),
        )


def test_the_sentence_ceiling_is_checked_before_any_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused before the minute of CPU the embeddings would cost (S2 review, I18)."""

    def never(*args: object, **kwargs: object) -> object:
        _ = (args, kwargs)
        msg = "an encoder was loaded for a transcript over the ceiling"
        raise AssertionError(msg)

    monkeypatch.setattr("temnia_pipeline.substrate.changepoint.load_encoder", never)
    segmenter = EmbeddingChangePointSegmenter(_ManySentences(MAX_CHANGE_POINT_UNITS + 1))
    with pytest.raises(ValueError, match=f"over the {MAX_CHANGE_POINT_UNITS} ceiling"):
        segmenter.analyse([])


@pytest.mark.models
@pytest.mark.parametrize("base", ["legacy", "sat"])
def test_the_top_candidate_is_the_gold_boundary(
    base: str,
    load_substrate: Callable[[str], SubstrateFixture],
    load_substrate_gold: Callable[[str], dict[str, object]],
) -> None:
    transcript, _ = load_substrate("two-topics")
    gold = load_substrate_gold("two-topics")
    boundaries = gold["boundariesMs"]
    assert isinstance(boundaries, list)
    segmenter = make_segmenter("changepoint", sentences_from=base)
    layers = segmenter.segment(transcript.words)
    assert layers.candidates, "the change-point layer proposed nothing on two-topics"
    top = max(layers.candidates, key=lambda candidate: candidate.score)
    gold_ids = [sentence.id for sentence in layers.sentences if sentence.start_ms == boundaries[0]]
    assert gold_ids, "the gold boundary is not a sentence start for this segmenter"
    assert abs(top.sentence_id - gold_ids[0]) <= GOLD_SENTENCE_TOLERANCE
    assert top.kind == "topic"
    assert 0.0 <= top.score <= 1.0


@pytest.mark.models
def test_the_candidate_count_is_the_target_exactly(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    """The design asked for a bisection landing within one; the solver is exact."""
    transcript, _ = load_substrate("two-topics")
    segmenter = EmbeddingChangePointSegmenter(LegacyRulesSegmenter())
    analysis = segmenter.analyse(transcript.words)
    assert analysis.target == 1
    assert len(analysis.candidates()) == analysis.target
    previous = analysis.target
    for per_hour in (30.0, 60.0):
        wider = EmbeddingChangePointSegmenter(LegacyRulesSegmenter(), target_per_hour=per_hour)
        wider_analysis = wider.analyse(transcript.words)
        assert wider_analysis.target == target_count(analysis.base.sentences, per_hour)
        assert wider_analysis.target > previous
        assert len(wider_analysis.candidates()) == wider_analysis.target
        previous = wider_analysis.target


@pytest.mark.models
def test_the_penalty_curve_is_available_and_ranked(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("two-topics")
    analysis = EmbeddingChangePointSegmenter(LegacyRulesSegmenter()).analyse(transcript.words)
    loose = analysis.candidates_at(0.5)
    tight = analysis.candidates_at(2.0)
    assert len(loose) > len(tight), "a bigger penalty must buy fewer boundaries"
    for candidate in loose:
        assert 0.0 <= candidate.score <= 1.0
        assert candidate.ms == analysis.base.sentences[candidate.sentence_id].start_ms
    ids = [candidate.sentence_id for candidate in loose]
    assert ids == sorted(set(ids))


@pytest.mark.models
def test_the_base_segmenters_own_candidates_are_replaced_not_merged(
    load_substrate: Callable[[str], SubstrateFixture],
) -> None:
    transcript, _ = load_substrate("two-topics")
    base = LegacyRulesSegmenter().segment(transcript.words)
    layers = EmbeddingChangePointSegmenter(LegacyRulesSegmenter()).segment(transcript.words)
    assert len(base.candidates) > len(layers.candidates)
    assert {candidate.kind for candidate in layers.candidates} == {"topic"}
    assert layers.sentences == base.sentences
    assert layers.paragraphs == base.paragraphs
