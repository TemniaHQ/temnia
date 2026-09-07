"""The ladder on the worker's own CPU: what Temnia did for the whole of S1.

This is the default backend, and the one the gate and `pnpm worker` run. It
keeps S1's two reuse paths: `transcode_ladder` recognises a finished ladder on
the work volume, and `reuse` recognises one already published to storage.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from temnia_pipeline import storage
from temnia_pipeline.media import hls
from temnia_pipeline.transcode import (
    LadderJob,
    LadderProgress,
    LadderResult,
    ProgressCallback,
    stored_ladder,
)

if TYPE_CHECKING:
    from obstore.store import S3Store

    from temnia_pipeline.settings import PipelineSettings

ENCODER: hls.Encoder = "libx264"
# The worker's ffmpeg is a static musl build with no CUDA in it, and the box it
# runs on has no GPU. The local backend has one decoder and says so.
DECODER: hls.Decoder = "cpu"


def master_path(work_root: Path, job: LadderJob) -> Path:
    """Where the worker keeps this source's master.

    The same path `probe_source` downloaded it to: the scratch directory is
    named after the source, which is the last segment of the artifact prefix.
    """
    return work_root / job.scratch_name / ("master" + Path(job.master_key).suffix)


def ladder_dir(work_root: Path, job: LadderJob) -> Path:
    """Where the ladder is built, beside the master `derive_source` reads from."""
    return work_root / job.scratch_name / "hls"


class LocalTranscoder:
    """Run the ladder here, verify every playlist, then publish it."""

    def __init__(self, settings: PipelineSettings, store: S3Store, work_root: Path) -> None:
        self.settings = settings
        self.store = store
        self.work_root = work_root

    async def reuse(self, job: LadderJob) -> LadderResult | None:
        """A ladder already published under this prefix, or None."""
        return await stored_ladder(self.store, job)

    async def run(
        self, job: LadderJob, *, on_progress: ProgressCallback, resume: str | None = None
    ) -> LadderResult:
        """Ladder, verify, publish, and write the manifest last.

        `resume` is unused here: the local backend has no remote handle to
        reattach to, and a retry finds its own work on the volume.
        """
        _ = resume
        out_dir = ladder_dir(self.work_root, job)
        master = await self._ensure_master(job)

        async def report(seconds: float) -> None:
            percent = _percent(seconds, job.expected_seconds)
            await on_progress(LadderProgress(stage="hls", percent=percent), None)

        rungs = await hls.transcode_ladder(
            self.settings.ffmpeg,
            master,
            out_dir,
            job.video,
            has_audio=job.has_audio,
            expected_seconds=job.expected_seconds,
            on_progress=report,
            encoder=ENCODER,
        )
        renditions = verify_ladder(out_dir, rungs, job)

        async def publishing(done: int, total_bytes: int) -> None:
            percent = _percent(done, total_bytes)
            await on_progress(LadderProgress(stage="publish", percent=percent), None)

        # An earlier attempt's marker must never ride along in the middle of a
        # tree upload: a reader that saw it would believe a half published
        # prefix was whole.
        (out_dir / hls.MANIFEST_NAME).unlink(missing_ok=True)
        total = await storage.upload_tree(
            self.store, job.hls_prefix, out_dir, on_progress=publishing
        )
        manifest = hls.LadderManifest(
            renditions=renditions,
            iframes=job.video is not None,
            segment_seconds=hls.SEGMENT_SECONDS,
            total_bytes=total,
            encoder=ENCODER,
            produced_by="local",
            decoder=DECODER,
        )
        await storage.upload_file(
            self.store, job.manifest_key, hls.write_manifest(out_dir, manifest)
        )
        return LadderResult(
            renditions=renditions,
            total_bytes=total,
            manifest_key=job.manifest_key,
            encoder=ENCODER,
            decoder=DECODER,
        )

    async def _ensure_master(self, job: LadderJob) -> Path:
        master = master_path(self.work_root, job)
        if master.exists() and master.stat().st_size == job.size_bytes:
            return master
        await storage.download(self.store, job.master_key, master, expected_size=job.size_bytes)
        return master


def verify_ladder(out_dir: Path, rungs: list[hls.Rung], job: LadderJob) -> dict[str, float]:
    """Every playlist's length, refusing to publish one that is short.

    ffmpeg reads a dropped input as end-of-file and exits 0, so this assertion
    is the only thing between a truncated encode and a player.
    """
    renditions = {
        rung.name: hls.assert_covers(out_dir / rung.name / "index.m3u8", job.expected_seconds)
        for rung in rungs
    }
    if job.has_audio:
        renditions["audio"] = hls.assert_covers(
            out_dir / "audio" / "index.m3u8", job.expected_seconds
        )
    if job.video is not None:
        renditions[hls.IFRAMES_RENDITION] = hls.assert_covers(
            out_dir / hls.IFRAMES_RENDITION / "index.m3u8",
            job.expected_seconds,
            floor_seconds=hls.KEYFRAME_SECONDS,
        )
    return renditions


def _percent(done: float, total: float) -> int:
    if total <= 0:
        return 100
    return max(0, min(100, int(100 * done / total)))
