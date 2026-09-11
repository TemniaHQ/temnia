"""Source sensors exercise real artifact identity, publication and cached reads."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from fractions import Fraction
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore

from temnia_pipeline.harness import artifacts, shot_evidence
from temnia_pipeline.media.source_shots import NativeShot
from test_harness_shot_evidence import ARTIFACT, MASTER, SCOPE, SOURCE, TIMELINE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from obstore.store import S3Store


class _Rows:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    async def fetchone(self) -> dict[str, Any] | None:
        return self.values[0] if self.values else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.values


@pytest.mark.parametrize("measured", [False, True])
async def test_source_sensor_uses_real_publication_and_cache_identity_without_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, measured: bool
) -> None:
    accepted: list[artifacts.HarnessArtifact] = []
    detections = 0

    class Connection:
        async def execute(self, query: str, _parameters: object) -> _Rows:
            if "FROM source " in query:
                return _Rows([{"id": SOURCE, "deletion_requested_at": None}])
            if "FROM harness_artifact_dependency" in query:
                return _Rows([])
            if "FROM harness_artifact" in query:
                return _Rows([asdict(row) for row in accepted])
            pytest.fail(f"Unexpected source artifact SQL: {query}")

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[Connection]:
        yield Connection()

    async def accept(*_args: object, **data: object) -> artifacts.HarnessArtifact:
        values = cast("dict[str, Any]", data)
        identity = values["identity"]
        assert identity.kind == "checks"
        assert identity.transcript_id is None
        assert identity.transcript_revision is None
        row = artifacts.HarnessArtifact(
            id=ARTIFACT,
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

    async def detect(*_args: object, **_kwargs: object) -> tuple[NativeShot, ...]:
        nonlocal detections
        detections += 1
        if measured:
            return (NativeShot(time=Fraction(1), score=20.0),)
        raise OSError

    def binary_hash(_path: str) -> str:
        return "b" * 64

    # Keep find_artifact, both validators, publish_json/publish_bytes and the
    # verified cached JSON read intact. Only the DB transactions and detector
    # bytes are substituted; MemoryStore exercises actual object publication.
    monkeypatch.setattr(artifacts.db, "scoped", scoped)
    monkeypatch.setattr(artifacts, "_accept_artifact", accept)
    monkeypatch.setattr(shot_evidence, "_binary_hash", binary_hash)
    monkeypatch.setattr(shot_evidence, "detect_source_shots", detect)
    store = cast("S3Store", MemoryStore())
    arguments: dict[str, Any] = {
        "scope": SCOPE,
        "source_id": SOURCE,
        "store": store,
        "source_path": tmp_path / "master.mp4",
        "source_object": MASTER,
        "timeline": TIMELINE,
        "ffmpeg": "ffmpeg",
    }
    first = await shot_evidence.build_source_shot_evidence("unused", **arguments)
    second = await shot_evidence.build_source_shot_evidence("unused", **arguments)
    assert first == second
    assert detections == 1
    assert len(accepted) == 1
    assert first.status == ("measured" if measured else "unavailable")
    assert first.shot_times_ms == ((1500,) if measured else ())
    content = cast(
        "dict[str, Any]",
        await artifacts.read_artifact_json(
            "unused", scope=SCOPE, source_id=SOURCE, store=store, artifact_id=ARTIFACT
        ),
    )
    assert content["binding"]["sourceObject"] == MASTER
    assert content["binding"] == first.provenance["binding"]
    assert accepted[0].metadata["format"] == shot_evidence.FORMAT
    assert content["observations"] == (
        [{"numerator": 1, "denominator": 1, "score": 20.0, "metrics": {}}] if measured else []
    )
