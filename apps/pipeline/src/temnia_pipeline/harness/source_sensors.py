"""Source sensors measured once, at ingest, and read by every topic run.

A topic run used to download the master, inspect its timeline, detect shots and measure
speech before its first model call. All three depend only on the master bytes, so they are
measured where the master already is (the ingest worker's scratch directory) and bound to
the master's object identity. A run heads the object, finds the timeline record for that
identity, and assembles evidence without downloading anything; rendering fetches the master
later, as before.
"""

# ruff: noqa: PLR0913

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID  # noqa: TC003 - a pydantic field type, resolved at runtime

from obstore import head_async
from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.shot_evidence import build_source_shot_evidence
from temnia_pipeline.harness.speech_evidence import ensure_source_speech
from temnia_pipeline.media.chapters import MediaTimelineFacts, inspect_timeline
from temnia_pipeline.media.timeline_identity import timeline_from_identity, timeline_identity

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.contracts import Scope
    from temnia_pipeline.harness.shot_evidence import ShotDetector

TIMELINE_FORMAT = "source-timeline/1"


def object_identity(metadata: object) -> dict[str, str | None]:
    """The store's immutable handle on one object version: ETag and version ID."""
    if not isinstance(metadata, dict):
        message = "object storage returned invalid source metadata"
        raise TypeError(message)
    values = cast("dict[object, object]", metadata)

    def text_value(*keys: str) -> str | None:
        for key in keys:
            value = values.get(key)
            if value is not None:
                return str(value)
        return None

    return {
        "etag": text_value("e_tag", "etag"),
        "versionId": text_value("version", "version_id", "versionId"),
    }


def timeline_binding(
    *,
    scope: Scope,
    source_id: UUID,
    storage_key: str,
    size_bytes: int,
    observed: dict[str, str | None],
) -> dict[str, Any]:
    """Identify the master without its hash: what a run can know from one HEAD request."""
    return {
        "organizationId": str(scope.organizationId),
        "sourceId": str(source_id),
        "storageKey": storage_key,
        "sizeBytes": size_bytes,
        "etag": observed["etag"],
        "versionId": observed["versionId"],
    }


class SourceTimelineRecord(BaseModel):
    """The master's hash and exact selected-stream facts, bound to its object identity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["source-timeline/1"] = TIMELINE_FORMAT
    binding: dict[str, Any]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeline: dict[str, Any]


class SourceSensorsResult(BaseModel):
    """What ingest measured for one master; never a reason to fail the ingest."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    timeline_artifact_id: UUID | None = None
    shot_status: str | None = None
    speech_status: str | None = None
    error: str | None = None


def _timeline_identity(
    scope: Scope,
    source_id: UUID,
    binding: dict[str, Any],
) -> artifacts.ArtifactIdentity:
    _ = scope, source_id
    return artifacts.ArtifactIdentity(
        kind="checks",
        fingerprint=artifacts.fingerprint_for(
            kind=TIMELINE_FORMAT, inputs=binding, config={"format": TIMELINE_FORMAT}
        ),
    )


async def find_source_timeline(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    storage_key: str,
    size_bytes: int,
    observed: dict[str, str | None],
) -> tuple[str, MediaTimelineFacts] | None:
    """The master's hash and timeline for this object identity, if ingest recorded them."""
    if observed["etag"] is None and observed["versionId"] is None:
        return None
    binding = timeline_binding(
        scope=scope,
        source_id=source_id,
        storage_key=storage_key,
        size_bytes=size_bytes,
        observed=observed,
    )
    accepted = await artifacts.find_artifact(
        database_url,
        scope=scope,
        source_id=source_id,
        identity=_timeline_identity(scope, source_id, binding),
    )
    if accepted is None:
        return None
    content = await artifacts.read_artifact_json(
        database_url, scope=scope, source_id=source_id, store=store, artifact_id=accepted.id
    )
    record = SourceTimelineRecord.model_validate(content, strict=True)
    if record.binding != binding:
        return None
    return record.sha256, timeline_from_identity(record.timeline)


def _hash_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


async def measure_source_sensors(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    master_path: Path,
    storage_key: str,
    size_bytes: int,
    ffmpeg: str,
    ffprobe: str,
    detector_path: Path,
    detector: ShotDetector = "scdet",
) -> SourceSensorsResult:
    """Inspect, hash and measure one master; publish the three source-bound records."""
    observed = object_identity(await head_async(store, storage_key))
    timeline = await inspect_timeline(master_path, ffprobe=ffprobe)
    sha256 = await asyncio.to_thread(_hash_file, master_path)
    binding = timeline_binding(
        scope=scope,
        source_id=source_id,
        storage_key=storage_key,
        size_bytes=size_bytes,
        observed=observed,
    )
    record = SourceTimelineRecord(
        binding=binding, sha256=sha256, timeline=timeline_identity(timeline)
    )
    accepted = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=_timeline_identity(scope, source_id, binding),
        content=record.model_dump(mode="json"),
        metadata={"format": TIMELINE_FORMAT},
    )
    source_object = {
        "etag": observed["etag"],
        "key": storage_key,
        "sha256": sha256,
        "sizeBytes": size_bytes,
        "versionId": observed["versionId"],
    }
    shots = await build_source_shot_evidence(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        source_path=master_path,
        source_object=source_object,
        timeline=timeline,
        ffmpeg=ffmpeg,
        detector=detector,
    )
    speech = await ensure_source_speech(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        source_path=master_path,
        source_object=source_object,
        timeline=timeline,
        detector_path=detector_path,
        ffmpeg=ffmpeg,
    )
    return SourceSensorsResult(
        timeline_artifact_id=accepted.id,
        shot_status=shots.status,
        speech_status=(
            str(cast("dict[str, object]", speech.content).get("status"))
            if speech is not None
            else None
        ),
    )
