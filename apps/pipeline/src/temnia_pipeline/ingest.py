"""Ingest activities: download, probe, ladder, derive, publish, finalize.

Each activity is self-contained and idempotent. The work directory for a
source is keyed by its id under `PIPELINE_WORK_DIR`; an activity that finds
what it needs there reuses it and otherwise fetches it from storage, so a
retry on another worker still succeeds. Progress writes are best effort: a
cosmetic signal never fails a transcode. Every activity heartbeats, so a
worker killed mid-ladder is noticed by Temporal's heartbeat timeout, not by
a reaper reading the database.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import (
    ArtifactKind,
    ArtifactRecord,
    IngestInput,
    IngestOutput,
    ProbeResult,
)
from temnia_pipeline.media import derive, hls, peaks
from temnia_pipeline.media.ffmpeg import FfmpegError
from temnia_pipeline.media.probe import InvalidMediaError, probe
from temnia_pipeline.settings import PipelineSettings, StorageSettings
from temnia_pipeline.transcode import LadderJob
from temnia_pipeline.transcode.factory import make_transcoder
from temnia_pipeline.transcription.factory import make_transcription

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from obstore.store import S3Store

    from temnia_pipeline.transcode import LadderProgress, Transcoder
    from temnia_pipeline.transcription.runner import TranscriptionRunner

log = logging.getLogger("temnia.ingest")

PROGRESS_INTERVAL_SECONDS = 10.0
DISK_HEADROOM = 1.5
CALL_ID = "call_id"


@dataclass(frozen=True, slots=True)
class Context:
    """What every activity needs."""

    settings: PipelineSettings
    storage: StorageSettings
    store: S3Store
    transcoder: Transcoder
    transcription: TranscriptionRunner

    @classmethod
    def from_env(cls) -> Context:
        """Build from the environment once per worker.

        `worker.py` is the only caller, so the transcoder that
        `TRANSCODE_BACKEND` chooses and the provider `TRANSCRIPTION_PROVIDER`
        chooses are built exactly once, at boot, beside the probes that refuse a
        wrong one.
        """
        storage_settings = StorageSettings.from_env()
        settings = PipelineSettings.from_env()
        store = storage.make_store(storage_settings)
        return cls(
            settings=settings,
            storage=storage_settings,
            store=store,
            transcoder=make_transcoder(settings, store, settings.work_root),
            transcription=make_transcription(settings, store),
        )


def workflow_id() -> str:
    """The running workflow's id, for the rows it writes."""
    return activity.info().workflow_id or "unknown"


def work_dir(settings: PipelineSettings, source_id: UUID) -> Path:
    """Scratch directory for one source."""
    return settings.work_root / str(source_id)


def resume_call_id() -> str | None:
    """The backend handle the last heartbeat carried, if there was one.

    Temporal hands a retried activity the details of the last heartbeat of the
    previous attempt. That is where a Modal call id survives a worker restart,
    and reattaching to a running GPU call is the difference between a retry
    that costs nothing and one that pays for the whole encode twice.
    """
    for detail in activity.info().heartbeat_details:
        if isinstance(detail, dict):
            found = detail.get(CALL_ID)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(found, str) and found:
                return found
    return None


def failure(message: str) -> ApplicationError:
    """A deterministic failure: terminal on attempt one, message safe to show."""
    return ApplicationError(message, non_retryable=True, type="IngestFailure")


class Ingest:
    """Activities bound to one process's settings and store."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    async def _progress(self, request: IngestInput, stage: str, percent: int | None) -> None:
        try:
            async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
                await db.report_progress(conn, request.sourceId, stage, percent)
        except Exception:
            log.warning("progress write failed", exc_info=True)

    async def _ensure_master(self, request: IngestInput, expected_size: int | None) -> Path:
        directory = work_dir(self.ctx.settings, request.sourceId)
        directory.mkdir(parents=True, exist_ok=True)
        master = directory / ("master" + Path(request.masterKey).suffix)
        if master.exists() and (expected_size is None or master.stat().st_size == expected_size):
            return master
        if expected_size is not None:
            free = shutil.disk_usage(directory).free
            if free < expected_size * DISK_HEADROOM:
                msg = f"not enough scratch disk: {free} free, {expected_size} needed"
                raise ApplicationError(msg, type="DiskPressure")
        activity.heartbeat("downloading master")
        await storage.download(
            self.ctx.store, request.masterKey, master, expected_size=expected_size
        )
        return master

    @activity.defn(name="claim_source")
    async def claim_source(self, request: IngestInput) -> bool:
        """Mark the source processing; False if another run owns it."""
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            return await db.claim_source(conn, request.sourceId, workflow_id())

    @activity.defn(name="probe_source")
    async def probe_source(self, request: IngestInput) -> ProbeResult:
        """Download the master and probe it; the probe is a gate."""
        await self._progress(request, "probe", None)
        master = await self._ensure_master(request, expected_size=None)
        try:
            result, _video = await asyncio.to_thread(probe, master)
        except InvalidMediaError as error:
            raise failure(str(error)) from error
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            await db.record_probe(conn, request.sourceId, result)
        return result

    @activity.defn(name="transcode_source")
    async def transcode_source(
        self, request: IngestInput, probed: ProbeResult
    ) -> list[ArtifactRecord]:
        """The audio extract here, then the ladder wherever `TRANSCODE_BACKEND` says."""
        master = await self._ensure_master(request, expected_size=probed.sizeBytes)
        _, video = await asyncio.to_thread(probe, master)
        records: list[ArtifactRecord] = []
        if probed.audioChannels:
            records.append(await self._extract_audio(request, master))

        job = LadderJob(
            master_key=request.masterKey,
            artifact_prefix=request.artifactPrefix,
            size_bytes=probed.sizeBytes,
            video=video,
            has_audio=bool(probed.audioChannels),
            expected_seconds=probed.durationMs / 1000,
        )
        last_report = 0.0

        async def on_progress(progress: LadderProgress, call_id: str | None) -> None:
            nonlocal last_report
            note = f"{progress.stage} {progress.percent}%"
            # The call id rides in the heartbeat because that is what a retried
            # activity reads to reattach to a Modal call still on a GPU.
            if call_id is None:
                activity.heartbeat(note)
            else:
                activity.heartbeat(note, {CALL_ID: call_id})
            now = time.monotonic()
            if now - last_report >= PROGRESS_INTERVAL_SECONDS:
                last_report = now
                await self._progress(request, progress.stage, progress.percent)

        await self._progress(request, "hls", 0)
        try:
            result = await self.ctx.transcoder.reuse(job)
            if result is None:
                result = await self.ctx.transcoder.run(
                    job, on_progress=on_progress, resume=resume_call_id()
                )
        except (FfmpegError, hls.TruncatedOutputError) as error:
            raise ApplicationError(str(error), type="TranscodeFailure") from error

        records.append(
            ArtifactRecord(
                kind=ArtifactKind.hls,
                storageKey=f"{request.artifactPrefix}hls/master.m3u8",
                storagePrefix=f"{request.artifactPrefix}hls/",
                contentType="application/vnd.apple.mpegurl",
                sizeBytes=result.total_bytes,
                metadata={
                    "renditions": {
                        name: round(seconds, 3) for name, seconds in result.renditions.items()
                    },
                    "segment_seconds": hls.SEGMENT_SECONDS,
                    "iframes": video is not None,
                    "encoder": result.encoder,
                    "decoder": result.decoder,
                },
            )
        )
        return records

    async def _extract_audio(self, request: IngestInput, master: Path) -> ArtifactRecord:
        """The 96k mono-friendly extract WhisperX and the waveform read.

        It stays on the worker whichever backend runs the ladder: it is
        seconds of CPU, and `derive_source` wants it on the work volume.
        """
        directory = work_dir(self.ctx.settings, request.sourceId)
        audio_dir = directory / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        await self._progress(request, "audio", None)
        activity.heartbeat("audio extract")
        from temnia_pipeline.media.ffmpeg import run_ffmpeg  # noqa: PLC0415

        audio_file = audio_dir / "audio.m4a"
        await run_ffmpeg(
            self.ctx.settings.ffmpeg,
            [
                "-i",
                str(master),
                "-vn",
                "-map",
                "0:a:0",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                str(audio_file),
            ],
        )
        size = await storage.upload_file(
            self.ctx.store, f"{request.artifactPrefix}audio/audio.m4a", audio_file
        )
        return ArtifactRecord(
            kind=ArtifactKind.audio,
            storageKey=f"{request.artifactPrefix}audio/audio.m4a",
            storagePrefix=None,
            contentType="audio/mp4",
            sizeBytes=size,
            metadata={"bitrate": "96k"},
        )

    @activity.defn(name="derive_source")
    async def derive_source(
        self, request: IngestInput, probed: ProbeResult
    ) -> list[ArtifactRecord]:
        """Peaks from the audio extract; thumbnails and shots from the lowest rung."""
        directory = work_dir(self.ctx.settings, request.sourceId)
        duration = probed.durationMs / 1000
        records: list[ArtifactRecord] = []

        if probed.audioChannels:
            await self._progress(request, "peaks", None)
            activity.heartbeat("peaks")
            audio_file = directory / "audio" / "audio.m4a"
            if not audio_file.exists():
                await storage.download(
                    self.ctx.store,
                    f"{request.artifactPrefix}audio/audio.m4a",
                    audio_file,
                    expected_size=None,
                )
            peaks_file = directory / "waveform" / "peaks.json"
            meta = await peaks.write_peaks(
                self.ctx.settings.ffmpeg, audio_file, peaks_file, duration
            )
            size = await storage.upload_file(
                self.ctx.store, f"{request.artifactPrefix}waveform/peaks.json", peaks_file
            )
            records.append(
                ArtifactRecord(
                    kind=ArtifactKind.peaks,
                    storageKey=f"{request.artifactPrefix}waveform/peaks.json",
                    storagePrefix=None,
                    contentType="application/json",
                    sizeBytes=size,
                    metadata=dict(meta),
                )
            )

        if probed.width:
            rung_dir = self._lowest_rung(directory / "hls")
            if rung_dir is None:
                rung_dir = await self._fetch_lowest_rung(request, directory / "hls")
            playlist = rung_dir / "index.m3u8"

            await self._progress(request, "thumbnails", None)
            activity.heartbeat("thumbnails")
            thumbs_dir = directory / "thumbs"
            if thumbs_dir.exists():
                shutil.rmtree(thumbs_dir)
            thumb_meta = await derive.write_thumbnails(
                self.ctx.settings.ffmpeg, playlist, thumbs_dir, duration
            )
            total = await storage.upload_tree(
                self.ctx.store, f"{request.artifactPrefix}thumbs/", thumbs_dir
            )
            records.append(
                ArtifactRecord(
                    kind=ArtifactKind.thumbnails,
                    storageKey=f"{request.artifactPrefix}thumbs/poster.jpg",
                    storagePrefix=f"{request.artifactPrefix}thumbs/",
                    contentType="image/jpeg",
                    sizeBytes=total,
                    metadata=dict(thumb_meta),
                )
            )

            await self._progress(request, "shots", None)
            activity.heartbeat("shots")
            shots_file = directory / "shots" / "shots.json"
            shot_meta = await derive.write_shots(self.ctx.settings.ffmpeg, playlist, shots_file)
            size = await storage.upload_file(
                self.ctx.store, f"{request.artifactPrefix}shots/shots.json", shots_file
            )
            records.append(
                ArtifactRecord(
                    kind=ArtifactKind.shots,
                    storageKey=f"{request.artifactPrefix}shots/shots.json",
                    storagePrefix=None,
                    contentType="application/json",
                    sizeBytes=size,
                    metadata=dict(shot_meta),
                )
            )
        return records

    @staticmethod
    def _lowest_rung(hls_dir: Path) -> Path | None:
        candidates = (
            [d for d in hls_dir.iterdir() if (d / "index.m3u8").exists()]
            if hls_dir.exists()
            else []
        )
        candidates = [d for d in candidates if d.name not in {"audio", "iframes"}]
        if not candidates:
            return None
        order = {"360p": 0, "720p": 1, "top": 2}
        return min(candidates, key=lambda d: order.get(d.name, 3))

    async def _fetch_lowest_rung(self, request: IngestInput, hls_dir: Path) -> Path:
        """A retry on a fresh worker: pull the smallest rung back from storage."""
        for name in ("360p", "720p", "top"):
            prefix = f"{request.artifactPrefix}hls/{name}/"
            keys = await storage.list_keys(self.ctx.store, prefix)
            if not keys:
                continue
            target = hls_dir / name
            for key in keys:
                await storage.download(
                    self.ctx.store, key, target / key[len(prefix) :], expected_size=None
                )
            return target
        msg = "no video rendition found in storage for derivation"
        raise ApplicationError(msg, type="DeriveFailure")

    @activity.defn(name="finalize_source")
    async def finalize_source(
        self,
        request: IngestInput,
        probed: ProbeResult,
        artifacts: list[ArtifactRecord],
        processing_seconds: int,
    ) -> IngestOutput:
        """One transaction: artifact rows, ready, ledger. Then drop the scratch dir."""
        await self._progress(request, "finalize", None)
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            storage_bytes = await db.finalize_source(
                conn,
                scope=request.scope,
                source_id=request.sourceId,
                workflow_id=workflow_id(),
                artifacts=artifacts,
                duration_ms=probed.durationMs,
                processing_seconds=processing_seconds,
            )
        shutil.rmtree(work_dir(self.ctx.settings, request.sourceId), ignore_errors=True)
        return IngestOutput(
            sourceId=request.sourceId,
            organizationId=request.scope.organizationId,
            probe=probed,
            artifacts=artifacts,
            processingSeconds=processing_seconds,
            storageBytes=storage_bytes,
        )

    @activity.defn(name="fail_source")
    async def fail_source(self, request: IngestInput, message: str) -> None:
        """Record a terminal failure in words safe to show, and clean up."""
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            await db.fail_source(conn, request.sourceId, message)
        shutil.rmtree(work_dir(self.ctx.settings, request.sourceId), ignore_errors=True)

    def activities(self) -> list[Callable[..., Any]]:
        """Everything the worker registers."""
        return [
            self.claim_source,
            self.probe_source,
            self.transcode_source,
            self.derive_source,
            self.finalize_source,
            self.fail_source,
        ]
