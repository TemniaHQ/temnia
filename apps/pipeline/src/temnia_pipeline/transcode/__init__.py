"""The seam the ladder runs behind: the worker's own ffmpeg, or a GPU on Modal.

The workflow, the database rows, and the UI cannot tell the two apart. Both
backends produce the same playlists under the same artifact prefix, verify
every one of them against the probed duration before anything is published,
and finish by writing `hls/manifest.json`. The manifest is written last, so a
retry that finds it knows the whole ladder is already in storage; that is the
only reuse signal a Modal ladder can have, because the encode happened on a
container the worker never sees.

Nothing in this module may import temporalio, psycopg, or the modal SDK: the
deployed Modal function imports it to read a job and build a result, and the
image it runs in carries none of those.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from temnia_pipeline.media import hls

# Not a type-checking-only import: pydantic resolves `LadderJob.video` at
# runtime, and a name that exists only for the type checker leaves the model
# undefined until some caller happens to have it in scope (AGENTS.md, Python
# pipeline). `media.facts` is exactly the record and none of the decoder.
from temnia_pipeline.media.facts import VideoFacts  # noqa: TC001
from temnia_pipeline.media.hls_inventory import (
    MAX_OBJECTS,
    MAX_PLAYLIST_BYTES,
    playlist_duration,
    validate_inventory,
)
from temnia_pipeline.modal_protocol import CONTRACT_VERSION as CONTRACT_VERSION
from temnia_pipeline.storage import list_objects, read_text

if TYPE_CHECKING:
    from obstore.store import S3Store

# The version lives beside the remote result frame. 1 was the ladder alone,
# 2 added transcription, 3 added the pixel format, and 4 adds explicit remote
# outcomes/build identity and named HLS inventory. The boot check detects a
# new worker paired with an incompatible app; existing workers/call handles
# must be drained before a protocol-changing deployment.
HLS_SUBDIR = "hls/"
INVENTORY_TIMEOUT_SECONDS = 300
INVENTORY_HEARTBEAT_SECONDS = 10
PLAYLIST_DURATION_EPSILON = 0.001

_WIRE = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class LadderJob(BaseModel):
    """Everything a ladder needs, and nothing that identifies a tenant.

    The Modal functions are scope-blind compute: the worker is the only
    authority on which organization a prefix belongs to, and a job carries a
    prefix it has already decided.
    """

    model_config = _WIRE

    master_key: str
    artifact_prefix: str
    size_bytes: int
    video: VideoFacts | None
    has_audio: bool
    expected_seconds: float

    @property
    def scratch_name(self) -> str:
        """The work directory's name on a worker: the prefix's last segment.

        `sourcePrefix()` in `@temnia/contracts` ends the prefix with the source
        id, which is what `PIPELINE_WORK_DIR` is keyed by, so the local backend
        builds the ladder exactly where `derive_source` looks for the lowest
        rung. A job carries no source id of its own: it must stay something a
        scope-blind Modal function can be handed.
        """
        return self.artifact_prefix.rstrip("/").rsplit("/", 1)[-1]

    @property
    def hls_prefix(self) -> str:
        """Where the ladder is published."""
        return self.artifact_prefix + HLS_SUBDIR

    @property
    def manifest_key(self) -> str:
        """The completion marker's key."""
        return self.hls_prefix + hls.MANIFEST_NAME

    @property
    def master_playlist_key(self) -> str:
        """The master playlist a player asks for first."""
        return self.hls_prefix + "master.m3u8"


class LadderResult(BaseModel):
    """A published ladder, as the transcode activity records it."""

    model_config = _WIRE

    renditions: dict[str, float]
    total_bytes: int
    manifest_key: str
    encoder: hls.Encoder
    # Read back from the manifest, never assumed: on Modal the encode may have
    # started on the GPU decoder and finished on the CPU one.
    decoder: hls.Decoder = "cpu"
    call_id: str | None = None


async def ladder_inventory_matches(  # noqa: PLR0911
    store: S3Store,
    job: LadderJob,
    manifest: hls.LadderManifest,
    *,
    objects: dict[str, int] | None = None,
) -> bool:
    """Check exact names/sizes and the hash and references of each playlist.

    Extra objects from interrupted older attempts do not invalidate a current
    ladder. The caller accounts for them in retained storage bytes.
    """
    if manifest.artifacts is None or manifest.playlist_sha256 is None:
        return False
    if objects is None:
        objects = dict(await list_objects(store, job.hls_prefix, max_objects=MAX_OBJECTS))
    relative = {key.removeprefix(job.hls_prefix): size for key, size in objects.items()}
    if any(relative.get(name) != size for name, size in manifest.artifacts.items()):
        return False
    if sum(manifest.artifacts.values()) != manifest.total_bytes:
        return False
    playlists: dict[str, str] = {}
    try:
        for name in manifest.playlist_sha256:
            if name not in manifest.artifacts or not name.endswith(".m3u8"):
                return False
            text = await read_text(store, job.hls_prefix + name, max_bytes=MAX_PLAYLIST_BYTES)
            if text is None:
                return False
            playlists[name] = text
        referenced = validate_inventory(
            manifest.artifacts, playlists, set(manifest.renditions), manifest.playlist_sha256
        )
        if any(
            abs(playlist_duration(playlists[f"{name}/index.m3u8"]) - seconds)
            > PLAYLIST_DURATION_EPSILON
            for name, seconds in manifest.renditions.items()
        ):
            return False
    except ValueError:
        return False
    return referenced == set(manifest.artifacts)


class LadderProgress(BaseModel):
    """How far the ladder has got, in the two stages the UI already shows."""

    model_config = _WIRE

    stage: Literal["hls", "publish"]
    percent: int


# The call id travels beside the progress so the activity can heartbeat a
# handle a retry can reattach to; the local backend passes None.
ProgressCallback = Callable[[LadderProgress, str | None], Awaitable[None]]


class Transcoder(Protocol):
    """Produce the ladder for a job and publish it under the job's prefix."""

    async def run(
        self, job: LadderJob, *, on_progress: ProgressCallback, resume: str | None
    ) -> LadderResult:
        """Ladder and publish. `resume` is a backend handle from an earlier attempt."""
        ...

    async def reuse(
        self,
        job: LadderJob,
        *,
        on_progress: ProgressCallback | None = None,
        resume: str | None = None,
    ) -> LadderResult | None:
        """The result of a ladder already complete in storage, or None."""
        ...


async def stored_ladder(
    store: S3Store,
    job: LadderJob,
    *,
    on_progress: ProgressCallback | None = None,
    resume: str | None = None,
) -> LadderResult | None:
    """Verify reuse within five minutes, heartbeating a saved handle during slow reads.

    Old manifests without named inventory are readable but never reusable.
    All retained prefix bytes, including the marker and stale unreferenced
    output from earlier attempts, are returned for storage accounting.
    """
    task = asyncio.create_task(_stored_ladder(store, job))
    try:
        async with asyncio.timeout(INVENTORY_TIMEOUT_SECONDS):
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=INVENTORY_HEARTBEAT_SECONDS)
                if not done and on_progress is not None:
                    await on_progress(LadderProgress(stage="publish", percent=0), resume)
            return task.result()
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _stored_ladder(store: S3Store, job: LadderJob) -> LadderResult | None:
    try:
        text = await read_text(store, job.manifest_key, max_bytes=32 * 1024 * 1024)
        if text is None:
            return None
        manifest = hls.read_manifest(text)
    except ValueError:
        return None
    expected: set[str] = {rung.name for rung in hls.plan_rungs(job.video)} if job.video else set()
    if job.has_audio:
        expected.add("audio")
    if job.video is not None:
        expected.add(hls.IFRAMES_RENDITION)
    if set(manifest.renditions) != expected or not hls.manifest_covers(
        manifest, job.expected_seconds
    ):
        return None
    objects = dict(await list_objects(store, job.hls_prefix, max_objects=MAX_OBJECTS))
    if not await ladder_inventory_matches(store, job, manifest, objects=objects):
        return None
    return LadderResult(
        renditions=manifest.renditions,
        total_bytes=sum(objects.values()),
        manifest_key=job.manifest_key,
        encoder=manifest.encoder,
        decoder=manifest.decoder,
        call_id=manifest.call_id,
    )
