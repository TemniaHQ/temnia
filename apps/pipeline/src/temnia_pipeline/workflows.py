"""Workflows served on the pipeline task queue."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    WorkflowAlreadyStartedError,
)
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.activities import say_hello

    # Temporal resolves the run method's type hints at runtime to deserialise
    # payloads, so these must be real imports, not TYPE_CHECKING ones.
    from temnia_pipeline.contracts import (
        ArtifactRecord,
        HelloInput,
        HelloOutput,
        IngestInput,
        IngestOutput,
        ProbeResult,
        TranscribeInput,
        TranscribeOutput,
    )
    from temnia_pipeline.harness.source_sensors import SourceSensorsResult
    from temnia_pipeline.transcription import TranscribeRecord
    from temnia_pipeline.transcription.checkpointed import (
        CheckpointedTranscription,
        CoverageRecord,
        ParallelCheckpointedTranscription,
        TranscriptionPlan,
    )

# Deterministic failures (bad media, truncated output) are terminal on attempt
# one; everything else (network, disk, a killed worker) retries a few times.
INGEST_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=4,
    non_retryable_error_types=["IngestFailure", "TranscodeFailure", "DeriveFailure"],
)

# The ingest policy plus the transcription-specific terminal type: an
# unsupported language, a corrupt response, or a result that fails the contract
# fails the same way on the next container, and retrying it three times at GPU
# prices buys nothing.
TRANSCRIBE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=4,
    non_retryable_error_types=["TranscriptionFailure", "NotClaimable", "StaleRun"],
)

# The audio extract ingest makes, relative to the source prefix. Matches
# ARTIFACT_PATHS.audio in @temnia/contracts.
AUDIO_PATH = "audio/audio.m4a"


@workflow.defn(name="HelloWorkflow")
class HelloWorkflow:
    """S0 walking skeleton: Next.js starts it, this worker runs it."""

    @workflow.run
    async def run(self, request: HelloInput) -> HelloOutput:
        """Run the single greeting activity."""
        return await workflow.execute_activity(
            say_hello,
            request,
            start_to_close_timeout=timedelta(seconds=10),
        )


@workflow.defn(name="ReaperWorkflow")
class ReaperWorkflow:
    """One sweep per schedule tick (reaper.py holds the activity)."""

    @workflow.run
    async def run(self) -> int:
        """Run the sweep activity."""
        return await workflow.execute_activity(
            "reap_abandoned_uploads",
            result_type=int,
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn(name="IngestWorkflow")
class IngestWorkflow:
    """Uploaded master -> probed, laddered, derived, finalized source.

    Heartbeat timeouts are how a hard-killed worker is noticed: the ladder
    heartbeats on every progress block, the rest on every step. A failure
    that exhausts retries records itself on the source row before the
    workflow fails, so the row never sits at `processing` with nothing to say.
    """

    @workflow.run
    async def run(self, request: IngestInput) -> IngestOutput:
        """Run the ingest stages in order."""
        started = workflow.now()
        claimed = await workflow.execute_activity(
            "claim_source",
            request,
            result_type=bool,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=INGEST_RETRY,
        )
        if not claimed:
            msg = "the source is not in a claimable state"
            raise ApplicationError(msg, non_retryable=True, type="NotClaimable")
        try:
            probed = await workflow.execute_activity(
                "probe_source",
                request,
                result_type=ProbeResult,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            transcoded = await workflow.execute_activity(
                "transcode_source",
                args=[request, probed],
                result_type=list[ArtifactRecord],
                start_to_close_timeout=timedelta(hours=8),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            derived = await workflow.execute_activity(
                "derive_source",
                args=[request, probed],
                result_type=list[ArtifactRecord],
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            await workflow.execute_activity(
                "measure_source_sensors",
                args=[request, probed],
                result_type=SourceSensorsResult,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            elapsed = int((workflow.now() - started).total_seconds())
            finalized = await workflow.execute_activity(
                "finalize_source",
                args=[request, probed, [*transcoded, *derived], elapsed],
                result_type=IngestOutput,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
        except ActivityError as error:
            cause = error.cause
            message = (
                cause.message
                if isinstance(cause, ApplicationError)
                else "The ingest did not complete. Upload the file again to retry."
            )
            await workflow.execute_activity(
                "fail_source",
                args=[request, message],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            raise
        else:
            await self._start_transcription(request, probed)
            return finalized

    @staticmethod
    async def _start_transcription(request: IngestInput, probed: ProbeResult) -> None:
        """Hand the finished source to `TranscribeWorkflow` and stop caring about it.

        Abandoned, so this workflow completes now and the transcription outlives
        it: a source is ready when its playback is ready, and transcription can
        take another half hour. A source with no audio, and a start that
        failed, each write a typed failure on the transcript row so the tab can
        say so instead of "Queued" for ever (S2 review, I19); neither fails the
        source, which has laddered and published. The web's Retry starts the
        same workflow id.
        """
        transcribe = TranscribeInput(
            scope=request.scope,
            sourceId=request.sourceId,
            artifactPrefix=request.artifactPrefix,
            audioKey=request.artifactPrefix + AUDIO_PATH,
            durationMs=probed.durationMs,
        )
        if not probed.audioChannels:
            await IngestWorkflow._mark_unavailable(
                transcribe, "NoAudioError: the recording has no audio track"
            )
            return
        try:
            await workflow.start_child_workflow(
                "TranscribeWorkflow",
                transcribe,
                id=f"transcribe-{request.sourceId}",
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
        except WorkflowAlreadyStartedError:
            # The web's Retry got there first under the same id; a run exists.
            return
        except Exception:  # noqa: BLE001
            workflow.logger.warning(
                "could not start transcription for source %s", request.sourceId, exc_info=True
            )
            await IngestWorkflow._mark_unavailable(
                transcribe, "DispatchError: transcription could not be queued"
            )

    @staticmethod
    async def _mark_unavailable(request: TranscribeInput, message: str) -> None:
        """Best effort, and never a reason to fail a source that is ready."""
        try:
            await workflow.execute_activity(
                "mark_transcript_unavailable",
                args=[request, message],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except Exception:  # noqa: BLE001
            workflow.logger.warning(
                "could not record the transcript state for source %s",
                request.sourceId,
                exc_info=True,
            )


@workflow.defn(name="TranscribeWorkflow")
class TranscribeWorkflow:
    """Audio extract -> a machine transcript revision in storage, and two ledger rows.

    Its own workflow, started as an abandoned child of the ingest and by the
    web's Retry under the same id, so a transcription failure never touches a
    finished source and a retry costs one engine run rather than a re-ingest.

    Liveness is Temporal's. The run heartbeats on every poll tick carrying the
    provider's handle, so a killed worker is noticed by the heartbeat timeout
    and the retry reattaches to the run already on the GPU. No reaper sweeps
    transcripts; after the last attempt this workflow writes the failure to the
    row itself, so the row never sits at `processing` with nothing to say.
    """

    @workflow.run
    async def run(self, request: TranscribeInput) -> TranscribeOutput:  # noqa: C901, PLR0912
        """Claim, transcribe, write the revision, meter."""
        checkpointed_run = False
        stage_handle = None
        coverage_handle = None
        attempt = await workflow.execute_activity(
            "claim_transcription",
            request,
            result_type=int,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=TRANSCRIBE_RETRY,
        )
        if attempt == 0:
            # Already running, or already ready. A second start for one source
            # must be a no-op, never a second engine run; a user who wants a
            # finished transcript replaced goes through Retry, which parks the
            # row back at pending first.
            msg = "the transcript is not in a claimable state"
            raise ApplicationError(msg, non_retryable=True, type="NotClaimable")
        try:
            if workflow.patched("checkpointed-speech-v1"):
                plan = await workflow.execute_activity(
                    "choose_transcription_plan",
                    request,
                    result_type=TranscriptionPlan,
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=TRANSCRIBE_RETRY,
                )
                if plan.backend == "modal-checkpointed":
                    checkpointed_run = True
                    use_speech_v2 = plan.protocol == "temnia-speech/2" and workflow.patched(
                        "checkpointed-speech-v2"
                    )
                    coverage_handle = workflow.start_activity(
                        "speech_coverage",
                        args=[request, plan],
                        result_type=CoverageRecord,
                        start_to_close_timeout=timedelta(hours=4),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=TRANSCRIBE_RETRY,
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                    stage_handle = workflow.start_activity(
                        "checkpointed_transcribe_v2"
                        if use_speech_v2
                        else "checkpointed_transcribe",
                        args=[request, plan],
                        result_type=(
                            ParallelCheckpointedTranscription
                            if use_speech_v2
                            else CheckpointedTranscription
                        ),
                        start_to_close_timeout=timedelta(hours=4),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=TRANSCRIBE_RETRY,
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                    stages = await stage_handle
                    await workflow.execute_activity(
                        "mark_speech_coverage_progress",
                        request,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=TRANSCRIBE_RETRY,
                    )
                    coverage = await coverage_handle
                    record = await workflow.execute_activity(
                        "assemble_checkpointed_transcript_v2"
                        if use_speech_v2
                        else "assemble_checkpointed_transcript",
                        args=[request, attempt, plan, stages, coverage],
                        result_type=TranscribeRecord,
                        start_to_close_timeout=timedelta(minutes=15),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=TRANSCRIBE_RETRY,
                    )
                else:
                    record = await self._legacy_transcribe(request, attempt)
            else:
                record = await self._legacy_transcribe(request, attempt)
            written = await workflow.execute_activity(
                "write_revision",
                args=[request, record],
                result_type=TranscribeOutput,
                start_to_close_timeout=timedelta(minutes=15),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=TRANSCRIBE_RETRY,
            )
            finalized = await workflow.execute_activity(
                "finalize_transcription",
                args=[request, record, written],
                result_type=TranscribeOutput,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=TRANSCRIBE_RETRY,
            )
        except ActivityError as error:
            cause = error.cause
            if checkpointed_run:
                handles = tuple(
                    handle for handle in (stage_handle, coverage_handle) if handle is not None
                )
                for handle in handles:
                    if not handle.done():
                        handle.cancel()
                for handle in handles:
                    with contextlib.suppress(ActivityError, CancelledError, asyncio.CancelledError):
                        await handle
            message = (
                "The transcription was cancelled."
                if isinstance(cause, CancelledError)
                else (
                    cause.message
                    if isinstance(cause, ApplicationError)
                    else "The transcription did not complete. Try again from the source page."
                )
            )
            await workflow.execute_activity(
                "fail_transcription",
                args=[request, message],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if checkpointed_run:
                if isinstance(cause, CancelledError) or (
                    isinstance(cause, ApplicationError) and cause.type == "SpeechCancelled"
                ):
                    outcome = "cancelled"
                elif isinstance(cause, ApplicationError) and cause.type == "BudgetExceeded":
                    outcome = "budget_paused"
                else:
                    outcome = "failed"
                await workflow.execute_activity(
                    "settle_checkpointed_speech_run",
                    args=[request, outcome],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                    result_type=bool,
                )
            raise
        else:
            if checkpointed_run:
                await workflow.execute_activity(
                    "settle_checkpointed_speech_run",
                    args=[request, "ready"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                    result_type=bool,
                )
            return finalized

    @staticmethod
    async def _legacy_transcribe(request: TranscribeInput, attempt: int) -> TranscribeRecord:
        """Keep the protocol-4 activity command intact for old and selected new runs."""
        return await workflow.execute_activity(
            "transcribe_source",
            args=[request, attempt],
            result_type=TranscribeRecord,
            # Five hours bounds the activity; the deployed function's own
            # timeout is four, so the function gives up first and this
            # never hides a run that is already over.
            start_to_close_timeout=timedelta(hours=5),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=TRANSCRIBE_RETRY,
        )
