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

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from temnia_pipeline.media import hls

# Not a type-checking-only import: pydantic resolves `LadderJob.video` at
# runtime, and a name that exists only for the type checker leaves the model
# undefined until some caller happens to have it in scope (AGENTS.md, Python
# pipeline). `media.facts` is exactly the record and none of the decoder.
from temnia_pipeline.media.facts import VideoFacts  # noqa: TC001
from temnia_pipeline.storage import key_exists, read_text

if TYPE_CHECKING:
    from obstore.store import S3Store

# Bumped whenever any job or result the deployed app exchanges changes shape.
# The worker refuses to boot against a Modal app that answers with a different
# one, so a half deployed pair is a failed deploy and never a run that quietly
# does the wrong thing. It covers the whole app, not just the ladder: S2 added
# `transcribe` beside `ladder`, and the two must be deployed together.
#
# "1" was the ladder alone (S2, PR A). "2" adds transcription.
CONTRACT_VERSION = "2"

HLS_SUBDIR = "hls/"

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
    call_id: str | None = None


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

    async def reuse(self, job: LadderJob) -> LadderResult | None:
        """The result of a ladder already complete in storage, or None."""
        ...


async def stored_ladder(store: S3Store, job: LadderJob) -> LadderResult | None:
    """Read back a published ladder and return its result when it is whole.

    Whole means all three: the manifest is there, it covers the source the
    probe measured, and the master playlist a player asks for first is a real
    key. A prefix left half written by a killed upload fails the third check
    even though the second passed, because the manifest goes up last.
    """
    text = await read_text(store, job.manifest_key)
    if text is None:
        return None
    manifest = hls.read_manifest(text)
    if not hls.manifest_covers(manifest, job.expected_seconds):
        return None
    if not await key_exists(store, job.master_playlist_key):
        return None
    return LadderResult(
        renditions=manifest.renditions,
        total_bytes=manifest.total_bytes,
        manifest_key=job.manifest_key,
        encoder=manifest.encoder,
        call_id=manifest.call_id,
    )
