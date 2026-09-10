"""Source-grid speech evidence with immutable media and detector ownership."""

# ruff: noqa: EM101, PLR0913, TRY003

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.contracts import SpeechCoverage, SpeechCoverageInterval, Status1
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.harness.rendering import timeline_identity
from temnia_pipeline.media.speech_pcm import (
    SAMPLE_RATE,
    SOURCE_PCM_VERSION,
    source_pcm_sample_count,
    stream_pcm_windows,
)
from temnia_pipeline.speech.coverage import DEFAULT_THRESHOLDS, assess_coverage
from temnia_pipeline.speech.silero import (
    DEFAULT_VAD_CONFIG,
    SILERO_DETECTOR,
    SILERO_REVISION,
    SILERO_SHA256,
    SileroOnnx,
    detect_speech,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from uuid import UUID

    from obstore.store import S3Store

    from temnia_pipeline.contracts import Scope, TranscriptV1
    from temnia_pipeline.media.chapters import MediaTimelineFacts
    from temnia_pipeline.speech.silero import SpeechEvidence

FORMAT = "chapter-source-speech/1"
DECODE_TIMEOUT_SECONDS = 3600


class _SourceSpeech(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["chapter-source-speech/1"] = FORMAT
    binding: dict[str, Any]
    status: Literal["measured", "unknown"]
    intervals: list[SpeechCoverageInterval]
    duration_ms: Annotated[int, Field(ge=0)]
    pcm_sample_count: Annotated[int, Field(ge=0)]
    window_count: Annotated[int, Field(ge=0)]
    error: str | None


@dataclass(frozen=True, slots=True)
class ChapterSpeechCoverage:
    """Coverage and the exact immutable detector artifact it was derived from."""

    coverage: SpeechCoverage
    artifact_id: UUID
    provenance: Mapping[str, Any]


def source_speech_binding(
    *, scope: Scope, source_id: UUID, source_object: Mapping[str, Any], timeline: MediaTimelineFacts
) -> dict[str, Any]:
    """Bind detector samples to the verified master, selected stream and common origin."""
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    key = source_object.get("key")
    sha = source_object.get("sha256")
    size = source_object.get("sizeBytes")
    if (
        not isinstance(key, str)
        or not key.startswith(prefix)
        or ".." in key
        or not isinstance(sha, str)
        or len(sha) != artifacts.SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in sha)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise IdentityConflict("source speech requires a scoped hash-verified master identity")
    return {
        "organizationId": str(scope.organizationId),
        "sourceId": str(source_id),
        "sourceObject": dict(source_object),
        "sourceTimeline": timeline_identity(timeline),
        "pcm": {
            "policy": SOURCE_PCM_VERSION,
            "sampleRate": SAMPLE_RATE,
            "sampleCount": source_pcm_sample_count(timeline),
            "sampleZero": "sourceStart",
            "timestampGaps": "fill-or-trim-without-time-stretch",
        },
        "detector": {
            "name": SILERO_DETECTOR,
            "revision": SILERO_REVISION,
            "sha256": SILERO_SHA256,
            "config": asdict(DEFAULT_VAD_CONFIG),
        },
    }


def coverage_from_source_speech(
    content: object, *, binding: Mapping[str, Any], transcript: TranscriptV1
) -> SpeechCoverage:
    """Refuse foreign/unmapped evidence; compare only an exactly owned source grid."""
    saved = _SourceSpeech.model_validate(content, strict=True)
    if saved.binding != binding:
        raise IdentityConflict("speech evidence does not name the selected master and timeline")
    if saved.status == "measured":
        if (
            saved.error is not None
            or saved.duration_ms != transcript.durationMs
            or saved.pcm_sample_count != binding["pcm"]["sampleCount"]
        ):
            raise ValueError("measured speech does not cover the complete selected source grid")
        last_end = 0
        for interval in saved.intervals:
            if not 0 <= last_end <= interval.startMs < interval.endMs <= transcript.durationMs:
                raise ValueError("speech intervals must be ordered and inside their source grid")
            last_end = interval.endMs
        detected = [(row.startMs, row.endMs) for row in saved.intervals]
    else:
        if not saved.error or saved.intervals or saved.pcm_sample_count or saved.window_count:
            raise ValueError("unknown speech evidence cannot claim measured coverage")
        detected = None
    assessment = assess_coverage(
        detected,
        [(word.startMs, word.endMs) for word in transcript.words],
        transcript.durationMs,
        detector_error=saved.error,
    )
    return SpeechCoverage(
        detector=SILERO_DETECTOR,
        detectorHash=SILERO_SHA256,
        detectorRevision=SILERO_REVISION,
        intervals=[
            SpeechCoverageInterval(startMs=start, endMs=end) for start, end in assessment.intervals
        ],
        status=Status1(assessment.status),
        uncoveredSpeechMs=assessment.uncovered_speech_ms,
        uncoveredTailMs=assessment.uncovered_tail_ms,
        warnings=assessment.warnings,
    )


def _require_measurement_source(timeline: MediaTimelineFacts, transcript: TranscriptV1) -> None:
    if not timeline.has_audio:
        raise ValueError("selected source has no audio stream; speech coverage is unknown")
    if round(timeline.duration * 1000) != transcript.durationMs:
        raise ValueError("source and transcript durations have no exact millisecond mapping")


def _require_detector_identity(measured: SpeechEvidence) -> None:
    if (measured.detector, measured.detector_revision, measured.detector_sha256) != (
        SILERO_DETECTOR,
        SILERO_REVISION,
        SILERO_SHA256,
    ):
        raise ValueError("source speech detector returned an unexpected model identity")


async def build_source_speech_coverage(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    source_path: Path,
    source_object: Mapping[str, Any],
    timeline: MediaTimelineFacts,
    transcript: TranscriptV1,
    detector_path: Path,
    ffmpeg: str,
) -> ChapterSpeechCoverage:
    """Reuse or measure CPU VAD under the caller's verified source lease and heartbeat.

    The master path/hash are already verified by chapter evidence construction.
    Older audio.m4a coverage has no retained master mapping and is not projected.
    Cancellation propagates and closes the PCM subprocess; decode failure remains
    an immutable unknown result, never a silence or editorial-quality assertion.
    """
    binding = source_speech_binding(
        scope=scope, source_id=source_id, source_object=source_object, timeline=timeline
    )
    identity = artifacts.ArtifactIdentity(
        kind="speech_checkpoint",
        fingerprint=artifacts.fingerprint_for(
            kind=FORMAT, inputs=binding, config={"format": FORMAT}
        ),
    )
    existing = await artifacts.find_artifact(
        database_url, scope=scope, source_id=source_id, identity=identity
    )
    if existing is not None:
        if existing.organization_id != scope.organizationId or existing.source_id != source_id:
            raise IdentityConflict("speech artifact is outside the selected organization/source")
        accepted = existing
        content = await artifacts.read_artifact_json(
            database_url, scope=scope, source_id=source_id, store=store, artifact_id=accepted.id
        )
    else:
        try:
            _require_measurement_source(timeline, transcript)
            async with asyncio.timeout(DECODE_TIMEOUT_SECONDS):
                model = await asyncio.to_thread(SileroOnnx.from_path, detector_path)
                windows = stream_pcm_windows(source_path, ffmpeg=ffmpeg, timeline=timeline)
                try:
                    measured = await detect_speech(windows, model)
                finally:
                    await windows.aclose()
            _require_detector_identity(measured)
            saved = _SourceSpeech(
                binding=binding,
                status="measured",
                intervals=[
                    SpeechCoverageInterval(startMs=row.start_ms, endMs=row.end_ms)
                    for row in measured.intervals
                ],
                duration_ms=measured.duration_ms,
                pcm_sample_count=source_pcm_sample_count(timeline),
                window_count=measured.window_count,
                error=None,
            )
        except Exception as error:  # noqa: BLE001
            saved = _SourceSpeech(
                binding=binding,
                status="unknown",
                intervals=[],
                duration_ms=transcript.durationMs,
                pcm_sample_count=0,
                window_count=0,
                error=f"{type(error).__name__}: {error}",
            )
        content = saved.model_dump(mode="json")
        # Validate before publishing; malformed model/detector output cannot become
        # an accepted measured artifact even if a test or adapter returns it.
        coverage_from_source_speech(content, binding=binding, transcript=transcript)
        accepted = await artifacts.publish_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            identity=identity,
            content=content,
            metadata={"format": FORMAT, "status": saved.status},
        )
    coverage = coverage_from_source_speech(content, binding=binding, transcript=transcript)
    return ChapterSpeechCoverage(
        coverage=coverage,
        artifact_id=accepted.id,
        provenance={
            "artifactId": str(accepted.id),
            "artifactSha256": accepted.sha256,
            "binding": binding,
            "thresholdVersion": DEFAULT_THRESHOLDS.version,
            "recognitionInput": "selected-transcript-word-spans/1",
        },
    )
