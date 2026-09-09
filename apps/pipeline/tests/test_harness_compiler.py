from __future__ import annotations

from fractions import Fraction
from uuid import UUID

import pytest

import temnia_pipeline.harness.evidence as evidence_module
from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterProposal,
    ChapterProposalSection,
    ChapterReviewAction,
    ChapterReviewInput,
    HarnessEvidence,
    HarnessEvidenceShot,
    Kind,
    PositiveRational,
    QuoteWordId,
    ReviewState,
    Scope,
    SignedRationalTime,
    SpeechCoverage,
    SpeechCoverageInterval,
    Status1,
    TranscriptProvider,
    TranscriptRevisionAnnotations,
    TranscriptV1,
    TranscriptWord,
    WordTiming,
)
from temnia_pipeline.harness.compiler import compile_chapters, quantize_time
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.harness.review import (
    OperationalReviewAction,
    ReviewRefused,
    apply_review,
)
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    validate_evidence,
    validate_proposal,
)
from temnia_pipeline.substrate.model import Layers, Provenance, Sentence

SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
TRANSCRIPT_ID = UUID("20000000-0000-0000-0000-000000000002")
ARTIFACT_ID = UUID("30000000-0000-0000-0000-000000000003")
RUN_ID = UUID("40000000-0000-0000-0000-000000000004")
MUTATION_ID = UUID("50000000-0000-0000-0000-000000000005")
SHA = "a" * 64


def _word(  # noqa: PLR0913
    text: str,
    start: int,
    end: int,
    *,
    speaker: str = "speaker-1",
    confidence: float = 0.99,
    timing: WordTiming = WordTiming.aligned,
) -> TranscriptWord:
    return TranscriptWord(
        confidence=confidence,
        endMs=end,
        speaker=speaker,
        startMs=start,
        text=text,
        timing=timing,
    )


def _transcript(words: list[TranscriptWord], duration: int) -> TranscriptV1:
    return TranscriptV1(
        durationMs=duration,
        language="en",
        provider=TranscriptProvider(model="fixture", name="recorded", version="1"),
        speakers=list(dict.fromkeys(word.speaker for word in words if word.speaker)),
        utterances=[],
        version=1,
        words=words,
    )


def _layers(transcript: TranscriptV1, ranges: list[tuple[int, int]]) -> Layers:
    sentences = tuple(
        Sentence(
            end_ms=max(word.endMs for word in transcript.words[first : last + 1]),
            id=index,
            is_question=False,
            speaker=transcript.words[first].speaker,
            start_ms=transcript.words[first].startMs,
            text="ignored generated text",
            word_end=last,
            word_start=first,
        )
        for index, (first, last) in enumerate(ranges)
    )
    return Layers(
        candidates=(),
        paragraphs=(),
        provenance=Provenance(
            models={"sentence": "fixture"},
            params={"threshold": 0.5},
            segmenter="fixture",
            versions={"fixture": "1"},
        ),
        sentences=sentences,
        words=transcript.words,
    )


def _clear_coverage() -> SpeechCoverage:
    return SpeechCoverage(
        detector="fixture",
        detectorHash="b" * 64,
        detectorRevision="1",
        intervals=[],
        status=Status1.clear,
        uncoveredSpeechMs=0,
        uncoveredTailMs=0,
        warnings=[],
    )


def _evidence(  # noqa: PLR0913
    transcript: TranscriptV1,
    ranges: list[tuple[int, int]],
    *,
    frame_rate: PositiveRational | None = None,
    audio_sample_rate: int | None = None,
    source_start: SignedRationalTime | None = None,
    shots: tuple[HarnessEvidenceShot, ...] = (),
    annotations: TranscriptRevisionAnnotations | None = None,
    speech_coverage: SpeechCoverage | None = None,
) -> HarnessEvidence:
    return build_evidence(
        transcript,
        _layers(transcript, ranges),
        audio_sample_rate=audio_sample_rate,
        annotations=annotations,
        frame_rate=frame_rate,
        shots=shots,
        source_fingerprint="c" * 64,
        source_id=SOURCE_ID,
        source_start=source_start or SignedRationalTime(numerator=0, denominator=1),
        speech_coverage=speech_coverage or _clear_coverage(),
        transcript_id=TRANSCRIPT_ID,
        transcript_revision=1,
        transcript_sha256=SHA,
        video_time_base=None,
    )


def test_evidence_carries_explicit_identity_lineage_and_speaker_labels() -> None:
    transcript = _transcript([_word("one", 0, 100)], 100)
    annotations = TranscriptRevisionAnnotations.model_validate(
        {
            "speakerIdentities": {
                "speaker-1": {
                    "identityId": "speaker-stable",
                    "label": "Alex",
                    "parentIdentityIds": ["speaker-old"],
                }
            },
            "version": 1,
            "wordIdentities": [
                {
                    "id": "word-stable",
                    "parentIds": ["word-old"],
                    "timingOrigin": "manual",
                }
            ],
        }
    )
    evidence = _evidence(transcript, [(0, 0)], annotations=annotations)
    assert [item.root for item in evidence.words[0].lineageIds] == [
        "word-stable",
        "word-old",
    ]
    assert evidence.config["speakerIdentities"]["speaker-1"]["label"] == "Alex"


def test_evidence_derives_matching_legacy_ids_and_rejects_bad_mapping() -> None:
    transcript = _transcript([_word("one", 0, 100)], 100)
    evidence = _evidence(transcript, [(0, 0)])
    assert [item.root for item in evidence.words[0].lineageIds] == [f"{TRANSCRIPT_ID}:1:word:0"]
    invalid = TranscriptRevisionAnnotations.model_validate(
        {
            "speakerIdentities": {
                "speaker-1": {
                    "identityId": "speaker-stable",
                    "label": "Alex",
                    "parentIdentityIds": [],
                }
            },
            "version": 1,
            "wordIdentities": [],
        }
    )
    with pytest.raises(ValueError, match="word annotations"):
        _evidence(transcript, [(0, 0)], annotations=invalid)


def test_evidence_derives_product_numeric_speaker_labels() -> None:
    transcript = _transcript(
        [_word("zero", 0, 100, speaker="0"), _word("one", 110, 200, speaker="1")],
        200,
    )
    evidence = _evidence(transcript, [(0, 1)])
    identities = evidence.config["speakerIdentities"]
    assert identities["0"]["label"] == "Speaker 1"
    assert identities["1"]["label"] == "Speaker 2"


def _proposal(evidence: HarnessEvidence, kinds: tuple[Kind, ...] | None = None) -> ChapterProposal:
    selected = kinds or tuple(Kind.keep for _ in evidence.sentences)
    return ChapterProposal(
        sections=[
            ChapterProposalSection(
                firstSentenceId=sentence.id,
                id=f"chapter-{index}",
                kind=selected[index],
                lastSentenceId=sentence.id,
                quoteWordIds=[],
                reason="fixture reason",
                title=f"Chapter {index}",
            )
            for index, sentence in enumerate(evidence.sentences)
        ],
        summary="fixture",
        version=1,
    )


def _command(  # noqa: PLR0913
    action: ChapterReviewAction,
    *,
    section_id: str | None = None,
    other_section_id: str | None = None,
    boundary_id: str | None = None,
    target_time_ms: int | None = None,
    reason: str = "",
    base_revision: int = 3,
    target_revision: int | None = None,
) -> ChapterReviewInput:
    return ChapterReviewInput(
        action=action,
        baseRevision=base_revision,
        boundaryId=boundary_id,
        budgetMicros=None,
        mutationKey=MUTATION_ID,
        otherSectionId=other_section_id,
        reason=reason,
        runId=RUN_ID,
        scope=Scope(
            organizationId=UUID("60000000-0000-0000-0000-000000000006"),
            userId=UUID("70000000-0000-0000-0000-000000000007"),
        ),
        sectionId=section_id,
        sourceId=SOURCE_ID,
        targetRevision=target_revision,
        targetTimeMs=target_time_ms,
    )


def test_evidence_preserves_words_and_derives_only_positive_union_gaps() -> None:
    transcript = _transcript(
        [
            _word("one", 0, 1000),
            _word("overlap", 500, 1500),
            _word("after", 2000, 2500, timing=WordTiming.interpolated),
        ],
        3000,
    )
    evidence = _evidence(transcript, [(0, 0), (1, 1), (2, 2)])

    assert [word.id for word in evidence.words] == ["w000000", "w000001", "w000002"]
    assert [sentence.text for sentence in evidence.sentences] == ["one", "overlap", "after"]
    assert [(pause.startMs, pause.endMs) for pause in evidence.pauses] == [(1500, 2000)]
    overlap_boundary = next(item for item in evidence.boundaries if item.timeMs == 500)
    assert overlap_boundary.clearanceMs == 0
    assert "inside_spoken_word" in overlap_boundary.reasons


def test_duplicate_word_times_do_not_manufacture_silence() -> None:
    transcript = _transcript(
        [_word("same", 100, 500), _word("time", 100, 500)],
        1000,
    )
    evidence = _evidence(transcript, [(0, 0), (1, 1)])
    assert evidence.pauses == []
    assert len([item for item in evidence.boundaries if item.timeMs == 100]) == 1


def test_evidence_overflow_is_refused_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = _transcript(
        [_word("one", 0, 10), _word("two", 10, 20), _word("three", 20, 30)],
        30,
    )
    monkeypatch.setattr(evidence_module, "MAX_EVIDENCE_WORDS", 2)
    with pytest.raises(ValueError, match="build a hierarchy instead of truncating"):
        _evidence(transcript, [(0, 2)])


def test_validator_rejects_missing_references_and_nonfinite_values() -> None:
    evidence = _evidence(_transcript([_word("one", 0, 1000)], 1000), [(0, 0)])
    bad_sentence = evidence.sentences[0].model_copy(
        update={"wordIds": [QuoteWordId(root="missing")]}
    )
    with pytest.raises(HarnessValidationError, match="invalid word reference"):
        validate_evidence(evidence.model_copy(update={"sentences": [bad_sentence]}))
    with pytest.raises(HarnessValidationError, match="finite JSON"):
        validate_evidence(evidence.model_copy(update={"config": {"bad": float("nan")}}))


def test_proposal_requires_exact_sentence_cover_and_grounded_quotes() -> None:
    evidence = _evidence(
        _transcript([_word("one", 0, 900), _word("two", 1100, 2000)], 2000),
        [(0, 0), (1, 1)],
    )
    proposal = _proposal(evidence)
    missing = proposal.model_copy(update={"sections": proposal.sections[:1]})
    with pytest.raises(HarnessValidationError, match="cover every"):
        validate_proposal(evidence, missing)
    foreign_quote = proposal.sections[0].model_copy(
        update={"quoteWordIds": [QuoteWordId(root="w000001")]}
    )
    with pytest.raises(HarnessValidationError, match="belong"):
        validate_proposal(
            evidence,
            proposal.model_copy(update={"sections": [foreign_quote, proposal.sections[1]]}),
        )


def test_empty_evidence_is_valid_but_paid_planning_is_refused() -> None:
    evidence = _evidence(_transcript([], 1000), [])
    validate_evidence(evidence)
    proposal = ChapterProposal(
        sections=[
            ChapterProposalSection(
                firstSentenceId="missing",
                id="chapter-0",
                kind=Kind.keep,
                lastSentenceId="missing",
                quoteWordIds=[],
                reason="fixture",
                title="Fixture",
            )
        ],
        summary="",
        version=1,
    )
    with pytest.raises(HarnessValidationError, match="paid chapter planning cannot run"):
        validate_proposal(evidence, proposal)


def test_exact_ntsc_and_sample_quantization_are_source_relative() -> None:
    ntsc = _evidence(
        _transcript([_word("one", 0, 2000)], 2000),
        [(0, 0)],
        frame_rate=PositiveRational(numerator=30000, denominator=1001),
    )
    assert quantize_time(ntsc, 1000) == Fraction(1001, 1000)

    sample = _evidence(
        _transcript([_word("one", 0, 2000)], 2000),
        [(0, 0)],
        audio_sample_rate=48_000,
        source_start=SignedRationalTime(numerator=1, denominator=96_000),
    )
    assert quantize_time(sample, 1001) == Fraction(1001, 1000)


def test_low_fps_quantization_into_word_is_recosted_for_review() -> None:
    evidence = _evidence(
        _transcript([_word("first", 0, 540), _word("second", 560, 1500)], 2000),
        [(0, 0), (1, 1)],
        frame_rate=PositiveRational(numerator=2, denominator=1),
    )
    edit = compile_chapters(
        evidence,
        _proposal(evidence),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    boundary = edit.boundaries[1]
    assert boundary.timeMs == 500
    assert boundary.requiresReview
    assert "compiler_quantized_inside_lexical_span" in boundary.reasons


def test_dp_prefers_safe_post_quantization_alternative() -> None:
    evidence = _evidence(
        _transcript([_word("first", 0, 540), _word("second", 560, 1500)], 2000),
        [(0, 0), (1, 1)],
        frame_rate=PositiveRational(numerator=2, denominator=1),
        shots=(HarnessEvidenceShot(score=1.0, timeMs=1500),),
    )
    edit = compile_chapters(
        evidence,
        _proposal(evidence),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    assert edit.boundaries[1].timeMs == 1500
    assert not edit.boundaries[1].requiresReview


def test_detected_speech_inside_lexical_gap_is_visible_disagreement() -> None:
    coverage = SpeechCoverage(
        detector="silero",
        detectorHash="b" * 64,
        detectorRevision="fixture",
        intervals=[SpeechCoverageInterval(startMs=450, endMs=550)],
        status=Status1.needs_review,
        uncoveredSpeechMs=100,
        uncoveredTailMs=0,
        warnings=["detector and recognition disagree materially; listen and review"],
    )
    evidence = _evidence(
        _transcript([_word("first", 0, 400), _word("second", 600, 1000)], 1000),
        [(0, 0), (1, 1)],
        speech_coverage=coverage,
    )
    boundary = next(item for item in evidence.boundaries if item.timeMs == 500)
    assert boundary.clearanceMs == 100
    assert boundary.requiresReview
    assert "detector_recognition_gap_disagreement" in boundary.reasons
    assert "speech_coverage_needs_review" in boundary.reasons


def test_compiler_does_not_widen_to_an_unrelated_safe_gap() -> None:
    transcript = _transcript(
        [_word("first", 0, 1500), _word("second", 1000, 2000)],
        10_000,
    )
    evidence = _evidence(
        transcript,
        [(0, 0), (1, 1)],
        shots=(HarnessEvidenceShot(score=1.0, timeMs=9000),),
    )
    edit = compile_chapters(
        evidence,
        _proposal(evidence),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    assert edit.boundaries[1].timeMs == 1000
    assert edit.boundaries[1].requiresReview is True


def test_one_sentence_all_drop_compiles_an_exact_cover() -> None:
    evidence = _evidence(_transcript([_word("one", 0, 1000)], 1000), [(0, 0)])
    edit = compile_chapters(
        evidence,
        _proposal(evidence, (Kind.drop,)),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    assert [boundary.timeMs for boundary in edit.boundaries] == [0, 1000]
    assert edit.sections[0].kind == Kind.drop


def test_joint_dp_chooses_compatible_alternative_when_local_choices_collide() -> None:
    transcript = _transcript(
        [
            _word("first", 0, 1500),
            _word("second", 1000, 1500),
            _word("third", 1000, 3000),
        ],
        3000,
    )
    evidence = _evidence(
        transcript,
        [(0, 0), (1, 1), (2, 2)],
        shots=(HarnessEvidenceShot(score=1.0, timeMs=1500),),
    )
    edit = compile_chapters(
        evidence,
        _proposal(evidence),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    assert [boundary.timeMs for boundary in edit.boundaries] == [0, 1000, 1500, 3000]


def test_compiler_refuses_quote_ownership_that_no_path_can_preserve() -> None:
    transcript = _transcript(
        [_word("first", 0, 2500), _word("second", 1000, 3000)],
        3000,
    )
    evidence = _evidence(transcript, [(0, 0), (1, 1)])
    proposal = _proposal(evidence)
    first = proposal.sections[0].model_copy(update={"quoteWordIds": [QuoteWordId(root="w000000")]})
    proposal = proposal.model_copy(update={"sections": [first, proposal.sections[1]]})
    with pytest.raises(HarnessValidationError, match="quote ownership"):
        compile_chapters(
            evidence,
            proposal,
            evidence_artifact_id=ARTIFACT_ID,
            evidence_sha256=SHA,
        )


def _review_fixture(
    kinds: tuple[Kind, Kind] = (Kind.keep, Kind.keep),
) -> tuple[HarnessEvidence, ChapterEditSpec]:
    evidence = _evidence(
        _transcript([_word("one", 0, 900), _word("two", 1100, 2000)], 2000),
        [(0, 0), (1, 1)],
    )
    edit = compile_chapters(
        evidence,
        _proposal(evidence, kinds),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    return evidence, edit


def test_accept_keep_and_reasoned_drop_and_reject() -> None:
    evidence, keep_edit = _review_fixture()
    accepted = apply_review(
        evidence,
        keep_edit,
        _command(ChapterReviewAction.accept, section_id="chapter-0"),
        {},
    )
    assert accepted.sections[0].reviewState == ReviewState.accepted

    evidence, drop_edit = _review_fixture((Kind.drop, Kind.keep))
    with pytest.raises(ReviewRefused, match="requires a reason"):
        apply_review(
            evidence,
            drop_edit,
            _command(ChapterReviewAction.accept, section_id="chapter-0"),
            {},
        )
    accepted_drop = apply_review(
        evidence,
        drop_edit,
        _command(
            ChapterReviewAction.accept,
            section_id="chapter-0",
            reason="deliberate pre-roll removal",
        ),
        {},
    )
    assert accepted_drop.sections[0].reviewState == ReviewState.accepted

    with pytest.raises(ReviewRefused, match="reject requires a reason"):
        apply_review(
            evidence,
            drop_edit,
            _command(ChapterReviewAction.reject, section_id="chapter-1"),
            {},
        )


def test_nudge_restore_merge_and_named_undo_are_validated() -> None:
    evidence, edit = _review_fixture()
    nudged = apply_review(
        evidence,
        edit,
        _command(
            ChapterReviewAction.nudge,
            boundary_id=edit.boundaries[1].id,
            target_time_ms=1200,
            reason="breath ends here",
        ),
        {},
    )
    assert nudged.boundaries[1].candidateId is None
    assert nudged.boundaries[1].timeMs == 1200
    assert [section.reviewState for section in nudged.sections] == [
        ReviewState.proposed,
        ReviewState.proposed,
    ]

    merged = apply_review(
        evidence,
        edit,
        _command(
            ChapterReviewAction.merge,
            section_id="chapter-0",
            other_section_id="chapter-1",
        ),
        {},
    )
    assert len(merged.sections) == 1
    assert merged.sections[0].id == f"merge-{MUTATION_ID.hex}"

    evidence, drop_edit = _review_fixture((Kind.drop, Kind.keep))
    restored = apply_review(
        evidence,
        drop_edit,
        _command(
            ChapterReviewAction.restore,
            section_id="chapter-0",
            reason="keep context",
        ),
        {},
    )
    assert restored.sections[0].kind == Kind.keep

    undone = apply_review(
        evidence,
        restored,
        _command(ChapterReviewAction.undo, target_revision=1),
        {1: drop_edit},
    )
    assert undone == drop_edit
    with pytest.raises(ReviewRefused, match="unavailable"):
        apply_review(
            evidence,
            restored,
            _command(ChapterReviewAction.undo, target_revision=2),
            {},
        )


def test_retry_is_explicitly_operational() -> None:
    evidence, edit = _review_fixture()
    with pytest.raises(OperationalReviewAction, match="operational command"):
        apply_review(evidence, edit, _command(ChapterReviewAction.retry), {})
    with pytest.raises(ReviewRefused, match="only valid for undo"):
        apply_review(
            evidence,
            edit,
            _command(ChapterReviewAction.accept, section_id="chapter-0", target_revision=1),
            {},
        )
