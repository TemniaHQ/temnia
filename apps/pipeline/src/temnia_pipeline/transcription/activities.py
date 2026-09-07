"""Transcription activities: claim, run, write the revision, meter, fail.

Transcription is its own workflow, never a step of ingest, so a transcription
that fails leaves a finished source alone and a retry costs one GPU call rather
than a whole re-ingest.

Liveness is Temporal's: the run heartbeats on every poll tick, carrying the
provider's handle, and the heartbeat timeout is what notices a killed worker.
There is no reaper sweeping transcripts (`reaper.py`'s own rule); the transcript
row's `heartbeat_at` mirrors the heartbeat for the surface to read, and nothing
else depends on it.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from typing import TYPE_CHECKING, Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import (
    TranscribeInput,
    TranscribeOutput,
    TranscriptProvider,
)
from temnia_pipeline.transcription import TranscribeJob, TranscribeRecord
from temnia_pipeline.transcription.normalize import (
    TranscriptContractError,
    normalize_whisperx,
)
from temnia_pipeline.transcription.runner import CALL_ID, classify_exception

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from temnia_pipeline.ingest import Context
    from temnia_pipeline.transcription import TranscriptionProgress

log = logging.getLogger("temnia.transcription")

PROGRESS_INTERVAL_SECONDS = 10.0


def workflow_id() -> str:
    """The running workflow's id, for the rows it writes."""
    return activity.info().workflow_id or "unknown"


def resume_handle() -> str | None:
    """The provider handle the last heartbeat carried, if there was one.

    Temporal hands a retried activity the details of the previous attempt's
    last heartbeat. That is where a Modal call id survives a worker restart,
    and reattaching to a run already on a GPU is the difference between a retry
    that costs nothing and one that pays for the whole transcription twice.
    """
    for detail in activity.info().heartbeat_details:
        if isinstance(detail, dict):
            found = detail.get(CALL_ID)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(found, str) and found:
                return found
    return None


class Transcribe:
    """Activities bound to one process's settings, store, and provider."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    @property
    def _provider(self) -> TranscriptProvider:
        provider = self.ctx.transcription.provider
        return TranscriptProvider(
            name=provider.name, model=provider.model, version=provider.version
        )

    async def _progress(self, request: TranscribeInput, stage: str, percent: int | None) -> None:
        try:
            async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
                await db.report_transcription_progress(conn, request.sourceId, stage, percent)
        except Exception:
            log.warning("transcript progress write failed", exc_info=True)

    @activity.defn(name="claim_transcription")
    async def claim_transcription(self, request: TranscribeInput) -> int:
        """Insert or claim the transcript row; 0 when another run owns it."""
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            return await db.claim_transcription(
                conn, request.sourceId, request.scope.organizationId, workflow_id()
            )

    @activity.defn(name="transcribe_source")
    async def transcribe_source(self, request: TranscribeInput, attempt: int) -> TranscribeRecord:
        """Run the provider, and keep the engine's own response in storage.

        The response is several megabytes for a long episode, so it never
        crosses the Temporal boundary: it goes to `raw-{attempt}.json` and this
        returns the key. That object is also the record a fixture is cut from
        and what S12's calibration round reads back.
        """
        job = TranscribeJob(
            audio_key=request.audioKey,
            artifact_prefix=request.artifactPrefix,
            attempt=attempt,
            duration_ms=request.durationMs,
        )
        last_report = 0.0
        live_handle: str | None = None

        async def on_progress(note: TranscriptionProgress, handle: str | None) -> None:
            nonlocal last_report, live_handle
            live_handle = handle
            summary = f"{note.stage} {note.percent}%"
            # The handle rides in the heartbeat because that is what a retried
            # activity reads to reattach to a run still on a GPU.
            if handle is None:
                activity.heartbeat(summary)
            else:
                activity.heartbeat(summary, {CALL_ID: handle})
            now = time.monotonic()
            if now - last_report >= PROGRESS_INTERVAL_SECONDS:
                last_report = now
                await self._progress(request, note.stage, note.percent)

        await self._progress(request, "download", None)
        try:
            raw = await self.ctx.transcription.run(
                job, on_progress=on_progress, resume=resume_handle()
            )
        except ApplicationError as error:
            await self._mark_retrying(request, error)
            raise

        raw_key = raw.raw_key or job.raw_key
        if raw.raw_key is None:
            # The recorded provider has nothing in storage; the Modal function
            # writes its own. Either way the next activity reads one key.
            await self._store_raw(request, raw_key, raw.raw)
        return TranscribeRecord(
            raw_key=raw_key,
            language=raw.language,
            gpu_seconds=raw.gpu_seconds,
            gpu=raw.gpu,
            attempt=attempt,
            call_id=live_handle,
        )

    async def _mark_retrying(self, request: TranscribeInput, error: ApplicationError) -> None:
        """Park the row at `processing / retrying` when another attempt is coming.

        The surface must never flash Failed between two attempts (S2 plan §5).
        A terminal failure is left alone: the workflow writes the real message
        to the row after the last attempt.
        """
        if error.non_retryable:
            return
        await self._progress(request, "retrying", None)

    async def _store_raw(self, request: TranscribeInput, key: str, raw: dict[str, Any]) -> None:
        path = self._scratch(request) / "raw.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Python's json writes the bare NaN a whisperx alignment score can be.
        # It is kept, not cleaned: this object records what the engine said, and
        # the normaliser is the only place a score is interpreted.
        path.write_text(json.dumps(raw))
        await storage.upload_file(self.ctx.store, key, path)

    def _scratch(self, request: TranscribeInput) -> Path:
        return self.ctx.settings.work_root / str(request.sourceId) / "transcript"

    @activity.defn(name="write_revision")
    async def write_revision(
        self, request: TranscribeInput, record: TranscribeRecord
    ) -> TranscribeOutput:
        """Normalise, validate, upload the revision, and mark the transcript ready."""
        await self._progress(request, "write", None)
        activity.heartbeat("normalising")
        raw = await storage.read_json(self.ctx.store, record.raw_key)
        if raw is None:
            msg = f"the engine's response is missing from storage at {record.raw_key}"
            raise ApplicationError(msg, type="TranscriptionProviderFailure")
        try:
            transcript = normalize_whisperx(raw, request.durationMs, self._provider)
        except (TranscriptContractError, TypeError, ValueError) as error:
            raise classify_exception(error) from error

        body = transcript.model_dump_json().encode()
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            transcript_id, revision, already = await db.next_transcript_revision(
                conn, request.sourceId, record.attempt
            )
            key = revision_key(request.artifactPrefix, revision)
            # The upload is inside the transaction: one that fails rolls the row
            # back, and a process killed after it recomputes the same number and
            # overwrites the same key.
            await storage.upload_bytes(self.ctx.store, key, body, "application/json")
            if not already:
                await db.record_transcript_revision(
                    conn,
                    organization_id=request.scope.organizationId,
                    transcript_id=transcript_id,
                    revision=revision,
                    attempt=record.attempt,
                    storage_key=key,
                    size_bytes=len(body),
                    word_count=len(transcript.words),
                    language=transcript.language,
                    provider=self._provider.name,
                    model=self._provider.model,
                    metadata={
                        "providerVersion": self._provider.version,
                        "speakers": len(transcript.speakers),
                        "gpu": record.gpu,
                        "gpuSeconds": record.gpu_seconds,
                    },
                )
        return TranscribeOutput(
            sourceId=request.sourceId,
            organizationId=request.scope.organizationId,
            revision=revision,
            language=transcript.language,
            provider=self._provider,
            durationMs=transcript.durationMs,
            wordCount=len(transcript.words),
            speakerCount=len(transcript.speakers),
            storageKey=key,
        )

    @activity.defn(name="finalize_transcription")
    async def finalize_transcription(
        self, request: TranscribeInput, record: TranscribeRecord, result: TranscribeOutput
    ) -> TranscribeOutput:
        """Meter the run and the bytes, then drop the scratch directory."""
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            await db.finalize_transcription(
                conn,
                organization_id=request.scope.organizationId,
                source_id=request.sourceId,
                workflow_id=workflow_id(),
                duration_ms=request.durationMs,
                revision=result.revision,
                detail={
                    "category": "transcription",
                    "attempt": record.attempt,
                    "provider": self._provider.name,
                    "model": self._provider.model,
                    "gpuSeconds": record.gpu_seconds,
                    "gpu": record.gpu,
                },
            )
        shutil.rmtree(self._scratch(request), ignore_errors=True)
        return result

    @activity.defn(name="fail_transcription")
    async def fail_transcription(self, request: TranscribeInput, message: str) -> None:
        """Record a terminal failure in words safe to show, and clean up."""
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            await db.fail_transcription(conn, request.sourceId, message)
        shutil.rmtree(self._scratch(request), ignore_errors=True)

    def activities(self) -> list[Callable[..., Any]]:
        """Everything the worker registers."""
        return [
            self.claim_transcription,
            self.transcribe_source,
            self.write_revision,
            self.finalize_transcription,
            self.fail_transcription,
        ]


def revision_key(artifact_prefix: str, revision: int) -> str:
    """`{sourcePrefix}transcript/rev-{N}.json`, matching `@temnia/contracts`."""
    return f"{artifact_prefix}transcript/rev-{revision}.json"
