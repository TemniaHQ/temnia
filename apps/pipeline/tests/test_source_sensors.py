"""Ingest measures a master once; a run finds every record by identity and downloads nothing."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from obstore.store import MemoryStore

from temnia_pipeline.harness import artifacts, shot_evidence, source_sensors, speech_evidence
from temnia_pipeline.harness.shot_evidence import find_source_shot_evidence
from temnia_pipeline.harness.source_sensors import (
    find_source_timeline,
    measure_source_sensors,
    object_identity,
)
from temnia_pipeline.harness.speech_evidence import ensure_source_speech
from temnia_pipeline.media.chapters import inspect_timeline
from temnia_pipeline.media.ffmpeg import run_ffmpeg
from temnia_pipeline.media.timeline_identity import timeline_identity
from temnia_pipeline.speech.silero import (
    SILERO_DETECTOR,
    SILERO_REVISION,
    SILERO_SHA256,
    SpeechEvidence,
)
from test_harness_shot_evidence import SCOPE, SOURCE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from obstore.store import S3Store

KEY = f"org/{SCOPE.organizationId}/source/{SOURCE}/original/master.mp4"


class _Rows:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    async def fetchone(self) -> dict[str, Any] | None:
        return self.values[0] if self.values else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.values


async def _master(tmp_path: Path) -> Path:
    path = tmp_path / "master.mp4"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        timeout_seconds=60,
    )
    return path


def _install_artifact_doubles(monkeypatch: pytest.MonkeyPatch) -> list[artifacts.HarnessArtifact]:
    accepted: list[artifacts.HarnessArtifact] = []
    counter = 0

    class Connection:
        async def execute(self, query: str, parameters: object) -> _Rows:
            if "FROM source " in query:
                return _Rows([{"id": SOURCE, "deletion_requested_at": None}])
            if "FROM harness_artifact_dependency" in query:
                return _Rows([])
            if "FROM harness_artifact" in query:
                wanted = {str(value) for value in cast("tuple[Any, ...]", parameters)}
                rows = [
                    asdict(row)
                    for row in accepted
                    if row.fingerprint in wanted or str(row.id) in wanted
                ]
                return _Rows(rows)
            pytest.fail(f"Unexpected source artifact SQL: {query}")

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[Connection]:
        yield Connection()

    async def accept(*_args: object, **data: object) -> artifacts.HarnessArtifact:
        nonlocal counter
        counter += 1
        values = cast("dict[str, Any]", data)
        identity = values["identity"]
        row = artifacts.HarnessArtifact(
            id=UUID(int=counter),
            organization_id=SCOPE.organizationId,
            source_id=SOURCE,
            kind=identity.kind,
            fingerprint=identity.fingerprint,
            storage_key=values["key"],
            sha256=values["sha256"],
            size_bytes=values["size_bytes"],
            metadata=values["metadata"],
            transcript_id=None,
            transcript_revision=None,
            dependency_ids=values["dependencies"],
        )
        accepted.append(row)
        return row

    monkeypatch.setattr(artifacts.db, "scoped", scoped)
    monkeypatch.setattr(artifacts, "_accept_artifact", accept)
    return accepted


def _binary(value: str) -> Any:  # noqa: ANN401
    def hash_of(_path: str) -> str:
        return value

    return hash_of


def _install_detector_doubles(monkeypatch: pytest.MonkeyPatch, binary_hash: str) -> None:
    monkeypatch.setattr(shot_evidence, "_binary_hash", _binary(binary_hash))

    class Model:
        pass

    async def detect(windows: object, _model: object) -> SpeechEvidence:
        _ = windows
        return SpeechEvidence(
            detector=SILERO_DETECTOR,
            detector_revision=SILERO_REVISION,
            detector_sha256=SILERO_SHA256,
            duration_ms=2000,
            window_count=62,
            intervals=[],
            probability_quantiles={},
            probability_histogram=[],
        )

    def load(_path: object) -> Model:
        return Model()

    monkeypatch.setattr(speech_evidence.SileroOnnx, "from_path", staticmethod(load))
    monkeypatch.setattr(speech_evidence, "detect_speech", detect)


async def test_ingest_measures_once_and_a_run_finds_every_record_without_the_master(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    master = await _master(tmp_path)
    store = cast("S3Store", MemoryStore())
    accepted = _install_artifact_doubles(monkeypatch)
    _install_detector_doubles(monkeypatch, "b" * 64)
    observed_meta: dict[str, object] = {"e_tag": "etag-one", "version": None}

    async def head(_store: object, _key: object) -> dict[str, object]:
        return observed_meta

    monkeypatch.setattr(source_sensors, "head_async", head)
    size = master.stat().st_size
    result = await measure_source_sensors(
        "unused",
        scope=SCOPE,
        source_id=SOURCE,
        store=store,
        master_path=master,
        storage_key=KEY,
        size_bytes=size,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        detector_path=tmp_path / "silero.onnx",
    )
    assert result.error is None
    assert result.shot_status == "measured"
    assert result.speech_status == "measured"
    assert {row.metadata["format"] for row in accepted} == {
        "source-timeline/1",
        "source-shot-evidence/1",
        "chapter-source-speech/1",
    }

    # The run's view: one HEAD, then every record by identity, and no file anywhere.
    observed = object_identity(observed_meta)
    found = await find_source_timeline(
        "unused",
        scope=SCOPE,
        source_id=SOURCE,
        store=store,
        storage_key=KEY,
        size_bytes=size,
        observed=observed,
    )
    assert found is not None
    sha256, timeline = found
    assert sha256 == hashlib.sha256(master.read_bytes()).hexdigest()
    assert timeline_identity(timeline) == timeline_identity(
        await inspect_timeline(master, ffprobe="ffprobe")
    )
    source_object = {
        "etag": "etag-one",
        "key": KEY,
        "sha256": sha256,
        "sizeBytes": size,
        "versionId": None,
    }
    # A later deploy ships another ffmpeg build: the record is still this master's.
    monkeypatch.setattr(shot_evidence, "_binary_hash", _binary("c" * 64))
    shots = await find_source_shot_evidence(
        "unused",
        scope=SCOPE,
        source_id=SOURCE,
        store=store,
        source_object=source_object,
        timeline=timeline,
        ffmpeg="ffmpeg",
    )
    assert shots is not None
    assert shots.status == "measured"
    assert shots.provenance["binding"]["detector"]["binarySha256"] == "b" * 64
    speech = await ensure_source_speech(
        "unused",
        scope=SCOPE,
        source_id=SOURCE,
        store=store,
        source_path=None,
        source_object=source_object,
        timeline=timeline,
        detector_path=tmp_path / "silero.onnx",
        ffmpeg="ffmpeg",
        measure=False,
    )
    assert speech is not None
    assert cast("dict[str, Any]", speech.content)["status"] == "measured"

    # Another object version is another master: nothing is found, nothing is measured.
    other = object_identity({"e_tag": "etag-two", "version": None})
    assert (
        await find_source_timeline(
            "unused",
            scope=SCOPE,
            source_id=SOURCE,
            store=store,
            storage_key=KEY,
            size_bytes=size,
            observed=other,
        )
        is None
    )
    assert (
        await find_source_shot_evidence(
            "unused",
            scope=SCOPE,
            source_id=SOURCE,
            store=store,
            source_object={**source_object, "etag": "etag-two"},
            timeline=timeline,
            ffmpeg="ffmpeg",
        )
        is None
    )


async def test_an_unstable_object_identity_never_matches_a_record() -> None:
    assert (
        await find_source_timeline(
            "unused",
            scope=SCOPE,
            source_id=SOURCE,
            store=cast("S3Store", MemoryStore()),
            storage_key=KEY,
            size_bytes=1,
            observed={"etag": None, "versionId": None},
        )
        is None
    )
