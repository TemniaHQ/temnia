"""Independent topic execution must preserve selected speech and historical contracts."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from fractions import Fraction
from uuid import uuid4

import pytest
from pydantic import ValidationError

from temnia_pipeline.contracts import (
    HarnessEvidence,
    PositiveRational,
    QuoteWordId,
    RationalTime,
    SignedRationalTime,
    SpeechCoverageInterval,
    Status1,
    TopicCandidate,
    TopicEditSpec,
    TopicProposal,
    TopicSentenceSpan,
)
from temnia_pipeline.harness.rendering import kept_sections
from temnia_pipeline.harness.topic_compiler import (
    augment_topic_evidence,
    compile_topics_v3,
    topic_execution_proposal,
    topic_source_usage,
    validate_topic_edit,
    validate_topic_proposal,
)
from temnia_pipeline.harness.validators import HarnessValidationError, rational, validate_edit
from test_harness_compiler import ARTIFACT_ID, SHA, _clear_coverage, _evidence, _transcript, _word


def _case() -> HarnessEvidence:
    return _evidence(
        _transcript(
            [
                _word("Setup.", 100, 900),
                _word("Claim.", 1100, 1900),
                _word("Qualification.", 2100, 2900),
                _word("Completion.", 3100, 3900),
            ],
            4000,
        ),
        [(0, 0), (1, 1), (2, 2), (3, 3)],
    )


def _span(first: int, last: int | None = None) -> TopicSentenceSpan:
    return TopicSentenceSpan(
        firstSentenceId=f"s{first:06d}",
        lastSentenceId=f"s{last if last is not None else first:06d}",
    )


def _candidate(identifier: str, first: int, last: int) -> TopicCandidate:
    return TopicCandidate(
        completionSpans=[_span(last)],
        coreSpans=[_span(first, last)],
        firstSentenceId=f"s{first:06d}",
        id=identifier,
        lastSentenceId=f"s{last:06d}",
        meaningChangingFollowups=[],
        purpose="Explain the selected source discussion.",
        reason="The selected discussion supports an independently reviewed topic.",
        requiredContextSpans=[],
        title=f"Topic {identifier}",
    )


def _compile(evidence: HarnessEvidence, *candidates: TopicCandidate) -> TopicEditSpec:
    return compile_topics_v3(
        augment_topic_evidence(evidence),
        TopicProposal(
            candidates=list(candidates), summary="Selected useful discussions.", version=1
        ),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )


def test_overlapping_videos_have_independent_edges_and_reuse_renderer() -> None:
    evidence = _case()
    first, second = _candidate("claim", 0, 2), _candidate("qualification", 1, 3)
    original = evidence.model_dump_json()
    portfolio = _compile(evidence, first, second)
    ranges = [kept_sections(video.edit)[0] for video in portfolio.videos]
    assert [(item.start, item.end) for item in ranges] == [
        (Fraction(0), Fraction(31, 10)),
        (Fraction(11, 10), Fraction(4)),
    ]
    for video in portfolio.videos:
        validate_edit(augment_topic_evidence(evidence), video.edit, expected_evidence_sha256=SHA)
        assert len(kept_sections(video.edit)) == 1
    assert evidence.model_dump_json() == original
    assert _compile(evidence, first).videos[0] == portfolio.videos[0]
    assert _compile(evidence, second, first).videos[1] == portfolio.videos[0]
    usage = topic_source_usage(portfolio)
    assert usage.used_intervals == ((Fraction(0), Fraction(4)),)
    assert usage.used_duration == 4
    assert usage.unused_duration == 0
    assert usage.repeated_duration == 2


def test_v3_assigns_the_complete_available_pause_to_the_preceding_utterance() -> None:
    evidence = augment_topic_evidence(
        _evidence(
            _transcript(
                [
                    _word("Previous thought.", 100, 400),
                    _word("Selected thought.", 1000, 1400),
                    _word("Following thought.", 3017, 3400),
                ],
                4000,
            ),
            [(0, 0), (1, 1), (2, 2)],
            frame_rate=PositiveRational(numerator=25, denominator=1),
        )
    )
    proposal = TopicProposal(
        candidates=[_candidate("selected", 1, 1)],
        summary="Select the middle discussion.",
        version=1,
    )
    owned = compile_topics_v3(
        evidence,
        proposal,
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    owned_range = kept_sections(owned.videos[0].edit)[0]

    assert owned.compilerVersion == "topic-compiler/3"
    assert owned_range.start == 1
    assert owned_range.end == 3
    assert Fraction(0) <= Fraction(3017, 1000) - owned_range.end < Fraction(1, 25)


def test_omissions_remain_outside_execution_drops_and_usage_is_separate() -> None:
    portfolio = _compile(_case(), _candidate("middle", 1, 1))
    video = portfolio.videos[0]
    assert [section.kind.value for section in video.edit.sections] == ["drop", "keep", "drop"]
    assert video.keptSectionId == "middle"
    assert [(item.start, item.end) for item in kept_sections(video.edit)] == [
        (Fraction(11, 10), Fraction(21, 10))
    ]
    usage = topic_source_usage(portfolio)
    assert usage.used_intervals == ((Fraction(11, 10), Fraction(21, 10)),)
    assert usage.unused_intervals == (
        (Fraction(0), Fraction(11, 10)),
        (Fraction(21, 10), Fraction(4)),
    )
    assert usage.unused_duration == 3


def test_zero_candidates_do_not_manufacture_a_video() -> None:
    result = _compile(_case())
    assert result.videos == []
    usage = topic_source_usage(result)
    assert usage.used_duration == usage.repeated_duration == 0
    assert usage.unused_duration == 4


def test_empty_portfolio_still_validates_hash_and_compiler_identity() -> None:
    evidence = _case()
    result = _compile(evidence)
    with pytest.raises(ValidationError):
        validate_topic_edit(evidence, result.model_copy(update={"evidenceSha256": "invalid"}))
    with pytest.raises(HarnessValidationError, match="unsupported compiler"):
        validate_topic_edit(evidence, result.model_copy(update={"compilerVersion": "foreign/1"}))


@pytest.mark.parametrize(
    "field", ["coreSpans", "requiredContextSpans", "completionSpans", "meaningChangingFollowups"]
)
def test_every_required_evidence_role_must_lie_inside_candidate(field: str) -> None:
    candidate = _candidate("dependent", 0, 1).model_copy(update={field: [_span(2)]})
    with pytest.raises(HarnessValidationError, match=rf"dependent\.{field}\[0\].*outside"):
        _compile(_case(), candidate)


@pytest.mark.parametrize("invalid", ["unknown", "reversed", "duplicate", "blank"])
def test_proposal_reports_specific_reference_and_identity_errors(invalid: str) -> None:
    candidates = [_candidate("one", 1, 2)]
    updates = {
        "unknown": {"firstSentenceId": "not-in-source"},
        "reversed": {"lastSentenceId": "s000000"},
        "blank": {"purpose": "   "},
        "duplicate": {},
    }
    candidates[0] = candidates[0].model_copy(update=updates[invalid])
    if invalid == "duplicate":
        candidates.append(candidates[0])
    expected = {
        "unknown": "unknown sentence not-in-source",
        "reversed": "lastSentenceId precedes",
        "blank": r"one\.purpose: must not be blank",
        "duplicate": "duplicate topic candidate id: one",
    }[invalid]
    with pytest.raises(HarnessValidationError, match=expected):
        validate_topic_proposal(
            _case(), TopicProposal(candidates=candidates, summary="Test.", version=1)
        )


def test_quotes_are_derived_and_stable_even_when_candidate_id_resembles_a_drop() -> None:
    evidence = _case()
    candidate = _candidate("outside-before", 1, 2)
    proposal = topic_execution_proposal(evidence, candidate)
    assert [section.id for section in proposal.sections] == [
        "outside-before-context",
        "outside-before",
        "outside-after",
    ]
    assert proposal.sections[1].quoteWordIds == [
        QuoteWordId(root="w000001"),
        QuoteWordId(root="w000002"),
    ]
    video = _compile(evidence, candidate).videos[0]
    assert video.edit.sections[1].quoteWordIds == proposal.sections[1].quoteWordIds
    assert topic_execution_proposal(evidence, candidate) == proposal


def test_quantization_cannot_remove_selected_word_even_if_center_would_survive() -> None:
    evidence = _evidence(
        _transcript([_word("Before.", 0, 540), _word("Required.", 560, 1500)], 2000),
        [(0, 0), (1, 1)],
        frame_rate=PositiveRational(numerator=2, denominator=1),
    )
    with pytest.raises(HarnessValidationError, match=r"selected\.opening: no grounded cut"):
        _compile(evidence, _candidate("selected", 1, 1))


def test_detected_speech_blocks_a_word_gap_without_inventing_a_silent_cut() -> None:
    evidence = _evidence(
        _transcript([_word("Before.", 0, 400), _word("Required.", 600, 1000)], 1200),
        [(0, 0), (1, 1)],
        speech_coverage=_clear_coverage().model_copy(
            update={"intervals": [SpeechCoverageInterval(startMs=350, endMs=650)]}
        ),
    )
    with pytest.raises(HarnessValidationError, match=r"selected\.opening: no grounded cut"):
        _compile(evidence, _candidate("selected", 1, 1))


def test_unknown_detector_evidence_remains_reviewable_not_silence() -> None:
    evidence = _case()
    evidence.speechCoverage = evidence.speechCoverage.model_copy(update={"status": Status1.unknown})
    result = _compile(evidence, _candidate("middle", 1, 2))
    assert all(boundary.requiresReview for boundary in result.videos[0].edit.boundaries)
    assert all(
        "topic_speech_evidence_unavailable" in boundary.reasons
        for boundary in result.videos[0].edit.boundaries
    )
    result.videos[0].edit.boundaries[1].requiresReview = False
    with pytest.raises(HarnessValidationError, match="unknown speech evidence hidden"):
        validate_topic_edit(augment_topic_evidence(evidence), result)


def test_fixed_source_edges_retain_risk_without_inventing_missing_context() -> None:
    evidence = _evidence(
        _transcript([_word("Discussion.", 0, 1000)], 1000),
        [(0, 0)],
        speech_coverage=_clear_coverage().model_copy(
            update={"intervals": [SpeechCoverageInterval(startMs=0, endMs=1000)]}
        ),
        assess_source_edges=True,
    )
    portfolio = _compile(evidence, _candidate("whole", 0, 0))
    assert [rational(boundary.time) for boundary in portfolio.videos[0].edit.boundaries] == [0, 1]
    assert all(boundary.requiresReview for boundary in portfolio.videos[0].edit.boundaries)


def test_source_offset_does_not_shift_the_output_frame_phase() -> None:
    evidence = _case()
    evidence.sourceStart = SignedRationalTime(numerator=7, denominator=13)
    evidence.frameRate = PositiveRational(numerator=30000, denominator=1001)
    video = _compile(evidence, _candidate("middle", 1, 2)).videos[0]
    selected = kept_sections(video.edit)[0]
    assert selected.start == Fraction(2002, 1875)
    assert selected.end == Fraction(23023, 7500)
    assert selected.start <= Fraction(1100, 1000)
    assert selected.end >= Fraction(2900, 1000)


@pytest.mark.parametrize("side", ["start", "end"])
def test_final_validation_catches_tampered_cut_that_old_quote_center_allows(side: str) -> None:
    evidence = augment_topic_evidence(_case())
    result = _compile(evidence, _candidate("middle", 1, 2))
    boundary = result.videos[0].edit.boundaries[1 if side == "start" else 2]
    time_ms = 1200 if side == "start" else 2800
    boundary.time = RationalTime(numerator=time_ms, denominator=1000)
    boundary.timeMs = time_ms
    validate_edit(evidence, result.videos[0].edit)
    with pytest.raises(HarnessValidationError, match="removes selected speech"):
        validate_topic_edit(evidence, result)


@pytest.mark.parametrize("invalid", ["source", "hash", "artifact", "title", "quotes"])
def test_portfolio_refuses_foreign_lineage_or_changed_execution(invalid: str) -> None:
    evidence = _case()
    result = _compile(evidence, _candidate("middle", 1, 2))
    if invalid == "source":
        result.sourceId = uuid4()
    elif invalid == "hash":
        result.evidenceSha256 = "d" * 64
    elif invalid == "artifact":
        result.videos[0].edit.evidenceArtifactId = uuid4()
    elif invalid == "title":
        result.videos[0].edit.sections[1].title = "Changed promise"
    else:
        result.videos[0].edit.sections[1].quoteWordIds = []
    with pytest.raises(HarnessValidationError):
        validate_topic_edit(evidence, result, expected_evidence_sha256=SHA)
