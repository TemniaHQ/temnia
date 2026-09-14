"""Pure construction of immutable, version-scoped chapter evidence."""

# ruff: noqa: C901, EM101, PLR0912, PLR0913, PLR0915, TRY003

from __future__ import annotations

import bisect
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.contracts import (
    HarnessBoundaryCandidate,
    HarnessEvidence,
    HarnessEvidencePause,
    HarnessEvidenceSentence,
    HarnessEvidenceShot,
    HarnessEvidenceWord,
    Kind2,
    LineageId,
    PositiveRational,
    SignedRationalTime,
    SpeechCoverage,
    Status1,
    Timing,
    TranscriptRevisionAnnotations,
    TranscriptSpeakerIdentity,
    TranscriptV1,
    WordId,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from temnia_pipeline.substrate.model import Layers

LOW_CONFIDENCE = 0.5


@dataclass(slots=True)
class _Candidate:
    kinds: set[Kind2] = field(default_factory=set[Kind2])
    reasons: set[str] = field(default_factory=set[str])
    score: float = 0.0
    sentence_id: str | None = None


@dataclass(frozen=True, slots=True)
class _SpokenInterval:
    start: int
    end: int
    left_word_id: str
    right_word_id: str


def _unknown_coverage() -> SpeechCoverage:
    return SpeechCoverage(
        detector=None,
        detectorHash=None,
        detectorRevision=None,
        intervals=[],
        status=Status1.unknown,
        uncoveredSpeechMs=0,
        uncoveredTailMs=0,
        warnings=["independent speech coverage was not run"],
    )


def _validate_json(value: object, name: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        msg = f"{name} must contain only finite JSON values"
        raise ValueError(msg) from error


def _verify_layers(transcript: TranscriptV1, layers: Layers) -> None:
    if len(layers.words) != len(transcript.words):
        raise ValueError("layers were built from a different word count")
    for index, (layer_word, word) in enumerate(zip(layers.words, transcript.words, strict=True)):
        if (
            layer_word.text != word.text
            or layer_word.startMs != word.startMs
            or layer_word.endMs != word.endMs
            or layer_word.speaker != word.speaker
        ):
            msg = f"layers word {index} does not match the immutable transcript"
            raise ValueError(msg)

    expected_start = 0
    for sentence_index, sentence in enumerate(layers.sentences):
        if sentence.id != sentence_index:
            raise ValueError("layer sentence ids must be contiguous zero-based integers")
        if sentence.word_start != expected_start or sentence.word_end < sentence.word_start:
            raise ValueError("layer sentence word ranges must exactly cover lexical order")
        if sentence.word_end >= len(transcript.words):
            raise ValueError("layer sentence word range lies outside the transcript")
        owned = transcript.words[sentence.word_start : sentence.word_end + 1]
        start_ms = owned[0].startMs
        end_ms = max(word.endMs for word in owned)
        if sentence.start_ms != start_ms or sentence.end_ms != end_ms:
            raise ValueError("layer sentence time does not match its owned source words")
        expected_start = sentence.word_end + 1
    if expected_start != len(transcript.words):
        raise ValueError("layer sentences do not own every transcript word exactly once")


def _spoken_intervals(words: Sequence[HarnessEvidenceWord]) -> list[_SpokenInterval]:
    if not words:
        return []
    ordered = sorted(words, key=lambda word: (word.startMs, word.endMs, word.wordIndex))
    first = ordered[0]
    intervals: list[_SpokenInterval] = []
    current = _SpokenInterval(first.startMs, first.endMs, first.id, first.id)
    for word in ordered[1:]:
        if word.startMs > current.end:
            intervals.append(current)
            current = _SpokenInterval(word.startMs, word.endMs, word.id, word.id)
        elif word.endMs > current.end:
            current = _SpokenInterval(current.start, word.endMs, current.left_word_id, word.id)
    intervals.append(current)
    return intervals


def _clearance_ms(
    time_ms: int,
    intervals: Sequence[_SpokenInterval],
    interval_starts: Sequence[int],
) -> int:
    if not intervals:
        return time_ms
    index = bisect.bisect_right(interval_starts, time_ms) - 1
    if index >= 0 and time_ms < intervals[index].end:
        return 0
    left = time_ms - intervals[index].end if index >= 0 else math.inf
    right_index = index + 1
    right = intervals[right_index].start - time_ms if right_index < len(intervals) else math.inf
    distance = min(left, right)
    return time_ms if math.isinf(distance) else max(0, int(distance))


def _inside_spoken(
    time_ms: int,
    intervals: Sequence[_SpokenInterval],
    interval_starts: Sequence[int],
) -> bool:
    index = bisect.bisect_right(interval_starts, time_ms) - 1
    return index >= 0 and intervals[index].start < time_ms < intervals[index].end


def _detected_intervals(coverage: SpeechCoverage) -> tuple[list[tuple[int, int]], list[int]]:
    merged: list[tuple[int, int]] = []
    for interval in sorted(coverage.intervals, key=lambda item: (item.startMs, item.endMs)):
        if interval.endMs <= interval.startMs:
            continue
        if merged and interval.startMs <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], interval.endMs))
        else:
            merged.append((interval.startMs, interval.endMs))
    return merged, [start for start, _end in merged]


def _inside_detected(
    time_ms: int, intervals: Sequence[tuple[int, int]], starts: Sequence[int]
) -> bool:
    index = bisect.bisect_right(starts, time_ms) - 1
    return index >= 0 and intervals[index][0] < time_ms < intervals[index][1]


def _candidate_kind(kinds: set[Kind2]) -> Kind2:
    for kind in (Kind2.edge, Kind2.pause, Kind2.turn, Kind2.shot, Kind2.sentence):
        if kind in kinds:
            return kind
    return Kind2.sentence


def _legacy_speaker_label(raw: str) -> str:
    """Match the product's legacy numeric speaker display fallback."""
    try:
        return f"Speaker {int(raw, 10) + 1}"
    except ValueError:
        return raw


def _add_candidate(
    candidates: dict[int, _Candidate],
    *,
    time_ms: int,
    kind: Kind2,
    score: float,
    reason: str,
    sentence_id: str | None = None,
) -> None:
    if not math.isfinite(score):
        raise ValueError("boundary candidate score must be finite")
    candidate = candidates.setdefault(time_ms, _Candidate())
    candidate.kinds.add(kind)
    candidate.reasons.add(reason)
    candidate.score = max(candidate.score, score)
    if sentence_id is not None:
        candidate.sentence_id = sentence_id


def _uncertainty_reasons(
    time_ms: int,
    words_by_start: Sequence[HarnessEvidenceWord],
    word_starts: Sequence[int],
) -> set[str]:
    index = bisect.bisect_left(word_starts, time_ms)
    nearby = words_by_start[max(0, index - 1) : min(len(words_by_start), index + 1)]
    reasons: set[str] = set()
    if any(word.timing == Timing.interpolated for word in nearby):
        reasons.add("adjacent_word_timing_interpolated")
    if any(word.confidence is not None and word.confidence < LOW_CONFIDENCE for word in nearby):
        reasons.add("adjacent_word_confidence_low")
    return reasons


def build_evidence(
    transcript: TranscriptV1,
    layers: Layers,
    *,
    source_id: UUID,
    transcript_id: UUID,
    transcript_revision: int,
    transcript_sha256: str,
    source_fingerprint: str,
    frame_rate: PositiveRational | None,
    video_time_base: PositiveRational | None,
    audio_sample_rate: int | None,
    source_start: SignedRationalTime,
    annotations: TranscriptRevisionAnnotations | None = None,
    machine_revision: int | None = None,
    legacy_speaker_labels: Mapping[str, str] | None = None,
    speech_coverage: SpeechCoverage | None = None,
    assess_source_edges: bool = False,
    shots: Sequence[HarnessEvidenceShot] = (),
    config: Mapping[str, Any] | None = None,
) -> HarnessEvidence:
    """Build one deterministic evidence body from a transcript and verified layers."""
    _verify_layers(transcript, layers)
    if transcript_revision <= 0:
        raise ValueError("transcript revision must be positive")
    if annotations is None:
        origin_revision = machine_revision or transcript_revision
        prefix = f"{transcript_id}:{origin_revision}"
        annotations = TranscriptRevisionAnnotations.model_validate(
            {
                "version": 1,
                "wordIdentities": [
                    {
                        "id": f"{prefix}:word:{index}",
                        "parentIds": [],
                        "timingOrigin": "provider",
                    }
                    for index in range(len(transcript.words))
                ],
                "speakerIdentities": {
                    speaker: {
                        "identityId": f"{prefix}:speaker:{index}",
                        "label": (legacy_speaker_labels or {}).get(
                            speaker, _legacy_speaker_label(speaker)
                        ),
                        "parentIdentityIds": [],
                    }
                    for index, speaker in enumerate(transcript.speakers)
                },
            }
        )
    if len(annotations.wordIdentities) != len(transcript.words):
        raise ValueError("word annotations do not match the immutable transcript")
    identity_ids = [identity.id for identity in annotations.wordIdentities]
    if len(set(identity_ids)) != len(identity_ids):
        raise ValueError("word annotation identity ids must be unique")
    speaker_annotations = cast(
        "dict[str, TranscriptSpeakerIdentity]",
        annotations.speakerIdentities,  # pyright: ignore[reportUnknownMemberType]
    )
    speaker_ids = [identity.identityId for identity in speaker_annotations.values()]
    if len(set(speaker_ids)) != len(speaker_ids):
        raise ValueError("speaker annotation identity ids must be unique")
    if set(speaker_annotations) != set(transcript.speakers):
        raise ValueError("speaker annotations do not match the immutable transcript")
    effective_config: dict[str, Any] = {
        "segmenter": layers.provenance.segmenter,
        "segmenterParams": dict(layers.provenance.params),
        "speakerIdentities": {
            raw: identity.model_dump(mode="json") for raw, identity in speaker_annotations.items()
        },
        **dict(config or {}),
    }
    if assess_source_edges:
        effective_config["sourceEdgeAssessment"] = "source-edges/1"
    model_versions = {
        **{f"model:{key}": value for key, value in layers.provenance.models.items()},
        **{f"library:{key}": value for key, value in layers.provenance.versions.items()},
    }
    _validate_json(effective_config, "evidence config")
    _validate_json(model_versions, "model versions")

    words = [
        HarnessEvidenceWord(
            confidence=word.confidence,
            endMs=word.endMs,
            id=f"w{index:06d}",
            lineageIds=[
                LineageId(root=lineage_id)
                for lineage_id in dict.fromkeys(
                    [
                        annotations.wordIdentities[index].id,
                        *(parent.root for parent in annotations.wordIdentities[index].parentIds),
                    ]
                )
            ],
            speaker=word.speaker,
            startMs=word.startMs,
            text=word.text,
            timing=Timing(word.timing.value),
            wordIndex=index,
        )
        for index, word in enumerate(transcript.words)
    ]
    sentences: list[HarnessEvidenceSentence] = []
    for sentence in layers.sentences:
        owned = words[sentence.word_start : sentence.word_end + 1]
        speakers = list(dict.fromkeys(word.speaker for word in owned if word.speaker is not None))
        sentences.append(
            HarnessEvidenceSentence(
                endMs=max(word.endMs for word in owned),
                id=f"s{sentence.id:06d}",
                speakers=speakers,
                startMs=owned[0].startMs,
                text=" ".join(word.text for word in owned),
                wordIds=[WordId(root=word.id) for word in owned],
            )
        )

    intervals = _spoken_intervals(words)
    interval_starts = [interval.start for interval in intervals]
    words_by_start = sorted(words, key=lambda word: (word.startMs, word.wordIndex))
    word_starts = [word.startMs for word in words_by_start]
    pauses = [
        HarnessEvidencePause(
            endMs=right.start,
            id=f"p{index:06d}",
            leftWordId=left.right_word_id,
            rightWordId=right.left_word_id,
            startMs=left.end,
        )
        for index, (left, right) in enumerate(itertools.pairwise(intervals))
        if right.start > left.end
    ]

    coverage = speech_coverage or _unknown_coverage()
    detected_intervals, detected_starts = _detected_intervals(coverage)
    candidates: dict[int, _Candidate] = {}
    _add_candidate(candidates, time_ms=0, kind=Kind2.edge, score=1.0, reason="source_start")
    _add_candidate(
        candidates,
        time_ms=transcript.durationMs,
        kind=Kind2.edge,
        score=1.0,
        reason="source_end",
    )
    for index, sentence in enumerate(sentences[1:], start=1):
        _add_candidate(
            candidates,
            time_ms=sentence.startMs,
            kind=Kind2.sentence,
            score=0.5,
            reason="sentence_transition",
            sentence_id=sentence.id,
        )
        previous = sentences[index - 1]
        if previous.speakers != sentence.speakers:
            _add_candidate(
                candidates,
                time_ms=sentence.startMs,
                kind=Kind2.turn,
                score=0.8,
                reason="speaker_transition",
                sentence_id=sentence.id,
            )
    sentence_starts = [sentence.startMs for sentence in sentences]
    for pause in pauses:
        midpoint = (pause.startMs + pause.endMs) // 2
        sentence_index = min(
            max(bisect.bisect_left(sentence_starts, midpoint), 0),
            max(len(sentences) - 1, 0),
        )
        _add_candidate(
            candidates,
            time_ms=midpoint,
            kind=Kind2.pause,
            score=1.0,
            reason=f"positive_speech_gap:{pause.id}",
            sentence_id=sentences[sentence_index].id if sentences else None,
        )
    for layer_candidate in layers.candidates:
        if not (0 <= layer_candidate.sentence_id < len(sentences)):
            raise ValueError("layer candidate refers to an unknown sentence")
        kind = Kind2(layer_candidate.kind) if layer_candidate.kind != "topic" else Kind2.sentence
        reason = (
            f"topic_score:{layer_candidate.score:.12g}"
            if layer_candidate.kind == "topic"
            else f"segmenter_{layer_candidate.kind}"
        )
        _add_candidate(
            candidates,
            time_ms=layer_candidate.ms,
            kind=kind,
            score=layer_candidate.score,
            reason=reason,
            sentence_id=sentences[layer_candidate.sentence_id].id,
        )
    validated_shots = [HarnessEvidenceShot.model_validate(shot) for shot in shots]
    for shot in validated_shots:
        _add_candidate(
            candidates,
            time_ms=shot.timeMs,
            kind=Kind2.shot,
            score=shot.score,
            reason="supplied_shot",
        )

    boundary_candidates: list[HarnessBoundaryCandidate] = []
    for candidate_index, (time_ms, aggregate) in enumerate(sorted(candidates.items())):
        if not (0 <= time_ms <= transcript.durationMs):
            raise ValueError("boundary candidate lies outside the source duration")
        clearance = _clearance_ms(time_ms, intervals, interval_starts)
        reasons = set(aggregate.reasons)
        is_edge = Kind2.edge in aggregate.kinds
        assess_candidate = not is_edge or assess_source_edges
        if _inside_spoken(time_ms, intervals, interval_starts) and assess_candidate:
            reasons.add("inside_spoken_word")
        if assess_candidate:
            reasons.update(_uncertainty_reasons(time_ms, words_by_start, word_starts))
            if coverage.status == Status1.unknown:
                reasons.add("speech_coverage_unknown")
            if coverage.status == Status1.needs_review:
                reasons.add("speech_coverage_needs_review")
            if clearance > 0 and _inside_detected(time_ms, detected_intervals, detected_starts):
                reasons.add("detector_recognition_gap_disagreement")
        if is_edge and assess_source_edges:
            # Endpoint contact is a risk observation, not proof that the source
            # cuts a word or a thought: alignment and VAD padding can touch it.
            if any(interval.start <= time_ms <= interval.end for interval in intervals):
                reasons.add("source_edge_lexical_contact")
            if any(start <= time_ms <= end for start, end in detected_intervals):
                reasons.add("source_edge_detected_speech")
        review_reasons = {
            "inside_spoken_word",
            "adjacent_word_timing_interpolated",
            "adjacent_word_confidence_low",
            "speech_coverage_unknown",
            "speech_coverage_needs_review",
            "detector_recognition_gap_disagreement",
            "source_edge_lexical_contact",
            "source_edge_detected_speech",
        }
        boundary_candidates.append(
            HarnessBoundaryCandidate(
                clearanceMs=clearance,
                id=f"b{candidate_index:06d}",
                kind=_candidate_kind(aggregate.kinds),
                reasons=sorted(reasons),
                requiresReview=bool(reasons & review_reasons),
                score=aggregate.score,
                sentenceId=aggregate.sentence_id,
                timeMs=time_ms,
            )
        )

    return HarnessEvidence(
        audioSampleRate=audio_sample_rate,
        boundaries=boundary_candidates,
        config=effective_config,
        durationMs=transcript.durationMs,
        frameRate=frame_rate,
        modelVersions=model_versions,
        pauses=pauses,
        sentences=sentences,
        shots=validated_shots,
        sourceFingerprint=source_fingerprint,
        sourceId=source_id,
        sourceStart=source_start,
        speechCoverage=coverage,
        transcriptId=transcript_id,
        transcriptRevision=transcript_revision,
        transcriptSha256=transcript_sha256,
        version=1,
        videoTimeBase=video_time_base,
        words=words,
    )
