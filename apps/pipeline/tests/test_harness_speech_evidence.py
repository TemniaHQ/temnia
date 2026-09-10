"""Immutable source ownership, unknown outcomes and measured coverage routing."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

from temnia_pipeline.contracts import Scope, Status1, TranscriptV1
from temnia_pipeline.harness import speech_evidence
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.media.chapters import MediaTimelineFacts
from temnia_pipeline.speech.silero import (
    SILERO_DETECTOR,
    SILERO_REVISION,
    SILERO_SHA256,
    SpeechEvidence,
    SpeechInterval,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

SOURCE = UUID("10000000-0000-4000-8000-000000000001")
ARTIFACT = UUID("20000000-0000-4000-8000-000000000002")
SCOPE = Scope(
    organizationId=UUID("30000000-0000-4000-8000-000000000003"),
    userId=UUID("40000000-0000-4000-8000-000000000004"),
)
MASTER = {
    "key": f"org/{SCOPE.organizationId}/source/{SOURCE}/original/master.mp4",
    "sha256": "a" * 64,
    "sizeBytes": 123,
    "etag": "frozen",
    "versionId": None,
}
TIMELINE = MediaTimelineFacts(
    duration=Fraction(1),
    container_start=Fraction(0),
    source_start=Fraction(0),
    has_video=True,
    has_audio=True,
    video_stream_index=0,
    audio_stream_index=1,
    video_start=Fraction(0),
    audio_start=Fraction(0),
    video_duration=Fraction(1),
    audio_duration=Fraction(1),
    frame_rate=Fraction(25),
    video_time_base=Fraction(1, 12800),
    audio_time_base=Fraction(1, 48000),
    sample_rate=48000,
    width=32,
    height=32,
    rotation=0,
    audio_channels=1,
    audio_layout="mono",
    variable_frame_rate=False,
    video_codec="h264",
    audio_codec="aac",
)


def _transcript(end: int = 900) -> TranscriptV1:
    return TranscriptV1.model_validate(
        {
            "version": 1,
            "durationMs": 1000,
            "language": "en",
            "speakers": [],
            "utterances": [],
            "provider": {"name": "fixture", "model": "fixture", "version": "1"},
            "words": [
                {
                    "text": "Words",
                    "startMs": 100,
                    "endMs": end,
                    "speaker": None,
                    "confidence": 0.99,
                    "timing": "aligned",
                }
            ],
        }
    )


def _binding() -> dict[str, Any]:
    return speech_evidence.source_speech_binding(
        scope=SCOPE, source_id=SOURCE, source_object=MASTER, timeline=TIMELINE
    )


def _content() -> dict[str, Any]:
    return {
        "format": "chapter-source-speech/1",
        "binding": _binding(),
        "status": "measured",
        "intervals": [{"startMs": 100, "endMs": 900}],
        "duration_ms": 1000,
        "pcm_sample_count": 16000,
        "window_count": 32,
        "error": None,
    }


def test_measured_owned_coverage_routes_disagreement_against_selected_transcript() -> None:
    clear = speech_evidence.coverage_from_source_speech(
        _content(), binding=_binding(), transcript=_transcript()
    )
    assert clear.status == Status1.clear
    assert clear.detectorHash == SILERO_SHA256
    assert [(row.startMs, row.endMs) for row in clear.intervals] == [(100, 900)]
    incomplete = speech_evidence.coverage_from_source_speech(
        _content(), binding=_binding(), transcript=_transcript(500)
    )
    assert incomplete.status == Status1.needs_review
    assert incomplete.uncoveredSpeechMs == incomplete.uncoveredTailMs == 400
    assert "agreement only" in clear.warnings[0]


@pytest.mark.parametrize(
    "mutation", ["org", "source", "bytes", "clock", "detector", "range", "count", "legacy"]
)
def test_foreign_unmapped_or_malformed_evidence_fails_closed(mutation: str) -> None:
    content = copy.deepcopy(_content())
    if mutation == "org":
        content["binding"]["organizationId"] = str(SOURCE)
    elif mutation == "source":
        content["binding"]["sourceId"] = str(ARTIFACT)
    elif mutation == "bytes":
        content["binding"]["sourceObject"]["sha256"] = "b" * 64
    elif mutation == "clock":
        content["binding"]["sourceTimeline"]["audioStart"]["numerator"] = 1
    elif mutation == "detector":
        content["binding"]["detector"]["sha256"] = "c" * 64
    elif mutation == "range":
        content["intervals"][0]["endMs"] = 1001
    elif mutation == "count":
        content["pcm_sample_count"] = 15999
    else:
        content = {
            "format": "speech-evidence/1",
            "source": {"audioKey": "audio.m4a"},
            "status": "measured",
        }
    with pytest.raises((IdentityConflict, ValueError)):
        speech_evidence.coverage_from_source_speech(
            content, binding=_binding(), transcript=_transcript()
        )


async def _build(
    tmp_path: Path, *, timeline: MediaTimelineFacts = TIMELINE
) -> speech_evidence.ChapterSpeechCoverage:
    return await speech_evidence.build_source_speech_coverage(
        "scoped-test",
        scope=SCOPE,
        source_id=SOURCE,
        store=cast("Any", None),
        source_path=tmp_path / "verified-master.mp4",
        source_object=MASTER,
        timeline=timeline,
        transcript=_transcript(),
        detector_path=tmp_path / "silero.onnx",
        ffmpeg="ffmpeg",
    )


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(
        id=ARTIFACT, organization_id=SCOPE.organizationId, source_id=SOURCE, sha256="d" * 64
    )


async def test_exact_immutable_reuse_reads_scoped_artifact_without_loading_detector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[object] = []

    async def find(_database: str, **kwargs: object) -> object:
        assert kwargs["scope"] == SCOPE
        assert kwargs["source_id"] == SOURCE
        return _artifact()

    async def read(_database: str, **kwargs: object) -> object:
        reads.append(kwargs)
        return _content()

    def no_load(*_args: object) -> None:
        pytest.fail("cached evidence must not run the detector")

    monkeypatch.setattr(speech_evidence.artifacts, "find_artifact", find)
    monkeypatch.setattr(speech_evidence.artifacts, "read_artifact_json", read)
    monkeypatch.setattr(speech_evidence.SileroOnnx, "from_path", no_load)
    result = await _build(tmp_path)
    assert result.artifact_id == ARTIFACT
    assert result.coverage.status == Status1.clear
    assert reads == [{"scope": SCOPE, "source_id": SOURCE, "store": None, "artifact_id": ARTIFACT}]


@pytest.mark.parametrize(
    "outcome", ["measured", "decode_failure", "no_audio", "cancelled", "timeout"]
)
async def test_new_source_measurement_publishes_known_outcomes_and_drains_cancellation(  # noqa: C901
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    published: list[dict[str, Any]] = []
    closed = asyncio.Event()

    async def absent(*_args: object, **_kwargs: object) -> None:
        return None

    async def publish(_database: str, **kwargs: object) -> object:
        published.append(kwargs)
        return _artifact()

    async def windows(*_args: object, **kwargs: object) -> AsyncIterator[object]:
        assert kwargs["timeline"] == TIMELINE
        try:
            yield object()
            if outcome == "cancelled":
                raise asyncio.CancelledError
            if outcome == "timeout":
                await asyncio.Future()
            if outcome == "decode_failure":
                message = "decoded samples stopped before the source endpoint"
                raise OSError(message)
        finally:
            closed.set()

    async def detect(stream: AsyncIterator[object], _model: object) -> SpeechEvidence:
        async for _window in stream:
            pass
        return SpeechEvidence(
            detector=SILERO_DETECTOR,
            detector_revision=SILERO_REVISION,
            detector_sha256=SILERO_SHA256,
            duration_ms=1000,
            window_count=32,
            intervals=[SpeechInterval(100, 900, 0.9, 0.95, 0.99)],
            probability_quantiles={},
            probability_histogram=[],
        )

    def load_model(_path: Path) -> None:
        return None

    monkeypatch.setattr(speech_evidence.artifacts, "find_artifact", absent)
    monkeypatch.setattr(speech_evidence.artifacts, "publish_json", publish)
    monkeypatch.setattr(speech_evidence.SileroOnnx, "from_path", load_model)
    monkeypatch.setattr(speech_evidence, "stream_pcm_windows", windows)
    monkeypatch.setattr(speech_evidence, "detect_speech", detect)
    if outcome == "timeout":
        monkeypatch.setattr(speech_evidence, "DECODE_TIMEOUT_SECONDS", 0.01)
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await _build(tmp_path)
        assert closed.is_set()
        assert not published
        return
    timeline = (
        replace(TIMELINE, has_audio=False, audio_stream_index=None, audio_start=None)
        if outcome == "no_audio"
        else TIMELINE
    )
    result = await _build(tmp_path, timeline=timeline)
    assert len(published) == 1
    assert published[0]["scope"] == SCOPE
    assert published[0]["source_id"] == SOURCE
    assert published[0]["content"]["binding"]["sourceObject"] == MASTER
    assert result.coverage.status == (Status1.clear if outcome == "measured" else Status1.unknown)
    if outcome == "decode_failure":
        assert closed.is_set()
        assert "decoded samples stopped" in result.coverage.warnings[0]
    if outcome == "timeout":
        assert closed.is_set()
        assert "TimeoutError" in result.coverage.warnings[0]
