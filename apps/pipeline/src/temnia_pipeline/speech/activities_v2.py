"""Durable protocol-v2 orchestration with independent parallel GPU stages."""

# State transitions deliberately remain adjacent to provider calls.
# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0913, PLR0915, TRY003

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID  # noqa: TC003

from temporalio import activity
from temporalio.exceptions import ApplicationError

from temnia_pipeline import storage
from temnia_pipeline.contracts import TranscribeInput, TranscriptStage
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.artifacts import ArtifactIdentity, HarnessArtifact
from temnia_pipeline.speech.activities import (
    POLL_SECONDS,
    SPEECH_ARTIFACT_KIND,
    AttemptLease,
    CoverageRecord,
    SpeechActivities,
    StageOomError,
    ensure_speech_run,
    recognition_spans,
    remaining_attempt_seconds,
    workflow_run_id,
)
from temnia_pipeline.speech.assignment import assignment_for
from temnia_pipeline.speech.checkpoints import (
    MAX_CHECKPOINT_BYTES,
    CheckpointCollisionError,
    ObstoreCheckpointStore,
)
from temnia_pipeline.speech.checkpoints_v2 import (
    admission_key_v2,
    artifact_ref_v2,
    validate_checkpoint_semantics_v2,
    validate_checkpoint_v2,
    validate_execution_admission_v2,
)
from temnia_pipeline.speech.client import (
    CallCancelled,
    CallFinished,
    CallUnknown,
    CallUnreachable,
    SpeechModalClient,
    assert_frozen_deployment,
)
from temnia_pipeline.speech.contracts import CheckpointSource, StageTelemetry, canonical_json
from temnia_pipeline.speech.contracts_v2 import (
    ArtifactRefV2,
    AssignmentArtifactRef,
    ExecutionTopology,
    RecognizeConfigV2,
    SpeakerAssignmentConfig,
    SpeakerTurnsConfig,
    SpeechAssignmentV1,
    SpeechCheckpointV2,
    SpeechStageJobV2,
    SpeechStageResultV2,
    StageConfigV2,
    StageV2,
)
from temnia_pipeline.speech.coverage import assess_coverage
from temnia_pipeline.speech.liveness import run_with_activity_heartbeat
from temnia_pipeline.speech.progress import report_speech_progress
from temnia_pipeline.transcription import TranscribeRecord
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechAssignment,
    AcceptedSpeechStageV2,
    ParallelCheckpointedTranscription,
    TranscriptionPlan,
    alignment_config_v2,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile

SPEECH_ASSIGNMENT_KIND = "speech_assignment"


async def _await_committed(coroutine: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401
    """Finish one accounting action despite repeated parent cancellation."""
    task = asyncio.create_task(coroutine)
    current = asyncio.current_task()
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
            if current is not None:
                current.uncancel()


async def _await_terminal(coroutine: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401
    """Finish terminal accounting, then preserve cancellation for the caller."""
    task = asyncio.create_task(coroutine)
    current = asyncio.current_task()
    cancelled = False
    result: Any = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
            if current is not None:
                current.uncancel()
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        if task.done() and not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return result


async def _drain_tasks(tasks: list[asyncio.Task[Any]], *, cancel: bool) -> None:
    """Cancel at most once and wait until every physical cleanup finalizer ends."""
    if cancel:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def gather_tasks() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    drain = asyncio.create_task(gather_tasks())
    current = asyncio.current_task()
    cancelled_again = False
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            cancelled_again = True
            if current is not None:
                current.uncancel()
    await drain
    if cancelled_again:
        raise asyncio.CancelledError


def _require_v2(plan: TranscriptionPlan) -> None:
    if (
        plan.protocol != "temnia-speech/2"
        or plan.audio_sha256 is None
        or plan.audio_size_bytes is None
        or plan.resource_profile is None
        or plan.model_manifest is None
        or plan.execution_topology is None
        or plan.recognize_v2 is None
        or plan.speaker_turns is None
        or plan.allow_oom_recovery is None
    ):
        raise ValueError("checkpointed speech/2 plan is incomplete")


def _semantic_inputs_v2(
    request: TranscribeInput,
    plan: TranscriptionPlan,
    input_checkpoint: ArtifactRefV2 | None,
) -> dict[str, object]:
    return {
        "audioKey": request.audioKey,
        "audioSha256": plan.audio_sha256,
        "audioSizeBytes": plan.audio_size_bytes,
        "durationMs": request.durationMs,
        "inputSha256": input_checkpoint.sha256 if input_checkpoint else None,
    }


def _semantic_config_v2(plan: TranscriptionPlan, configuration: StageConfigV2) -> dict[str, object]:
    _require_v2(plan)
    return {
        "app": plan.app,
        "allowOomRecovery": plan.allow_oom_recovery,
        "build": plan.build,
        "executionTopology": plan.execution_topology,
        "modelManifest": cast("Any", plan.model_manifest).model_dump(mode="json", by_alias=True),
        "protocol": plan.protocol,
        "resourceProfile": cast("Any", plan.resource_profile).model_dump(
            mode="json", by_alias=True
        ),
        "stageConfig": configuration.model_dump(mode="json", by_alias=True),
    }


def _usage_v2(plan: TranscriptionPlan) -> dict[str, object]:
    _require_v2(plan)
    profile = cast("Any", plan.resource_profile)
    estimated = profile.reservation_micros(plan.rate_micros_per_hour)
    return {
        "costEvidence": "unavailable",
        "estimatedMicros": estimated,
        "estimate": {
            "coveredSeconds": profile.stage_timeout_seconds + profile.startup_timeout_seconds,
            "cpuCores": profile.cpu_cores,
            "gpu": profile.gpu,
            "memoryMiB": profile.memory_mib,
            "progressMode": profile.progress_mode,
            "rateMicrosPerHour": plan.rate_micros_per_hour,
            "resourceProfileSha256": profile.sha256,
            "scope": "configured invocation exposure; not a provider invoice ceiling",
        },
    }


def _artifact_ref_v2(
    artifact: HarnessArtifact, configuration_sha256: str, stage: StageV2
) -> ArtifactRefV2:
    return ArtifactRefV2(
        key=artifact.storage_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        stage=stage,
        configuration_sha256=configuration_sha256,
    )


def _transcript_stage(stage: StageV2) -> TranscriptStage:
    return TranscriptStage(
        {"recognize": "transcribe", "align": "align", "speaker_turns": "diarize"}[stage]
    )


def _visible(value: bool | asyncio.Event) -> bool:  # noqa: FBT001
    return value if isinstance(value, bool) else value.is_set()


def validate_fresh_checkpoint_v2(
    job: SpeechStageJobV2,
    result: SpeechStageResultV2,
    body: bytes,
    admission_body: bytes,
    *,
    handle: str,
) -> SpeechCheckpointV2:
    """Bind a fresh result to the exact job, bytes, admission and provider handle."""
    if result.checkpoint is None:
        raise CheckpointCollisionError("successful result omitted its checkpoint")
    checkpoint = validate_checkpoint_v2(job, body)
    expected_ref = artifact_ref_v2(
        job.checkpoint_key,
        job.stage,
        checkpoint.configuration_sha256,
        body,
    )
    if (
        result.checkpoint != expected_ref
        or checkpoint.execution is None
        or checkpoint.execution.modal_call_id != handle
        or result.execution_identity != checkpoint.execution
        or checkpoint.execution.admission_key != admission_key_v2(job)
        or (
            not result.checkpoint_reused
            and checkpoint.execution.modal_task_id != result.modal_task_id
        )
    ):
        raise CheckpointCollisionError("checkpoint execution identity is incompatible")
    validate_execution_admission_v2(job, checkpoint.execution, admission_body)
    return checkpoint


@dataclass(frozen=True, slots=True)
class PreparedStageV2:
    """A logical v2 operation ready for atomic physical reservation."""

    request: TranscribeInput
    plan: TranscriptionPlan
    run_id: UUID
    operation: ledger.Operation
    configuration: StageConfigV2
    input_stage: AcceptedSpeechStageV2 | None
    identity: ArtifactIdentity
    owner: str
    usage: dict[str, object]
    reservation: ledger.AttemptReservationRequest


class SpeechActivitiesV2(SpeechActivities):
    """Add protocol-v2 activities while preserving every v1 registration."""

    async def _cached_stage_v2(
        self,
        *,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        operation: ledger.Operation,
        configuration: StageConfigV2,
        input_stage: AcceptedSpeechStageV2 | None,
        identity: ArtifactIdentity,
    ) -> AcceptedSpeechStageV2 | None:
        _require_v2(plan)
        found = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            identity=identity,
        )
        if found is None:
            return None
        input_checkpoint = input_stage.checkpoint if input_stage else None
        expected_dependencies = (input_stage.artifact_id,) if input_stage else ()
        if found.dependency_ids != expected_dependencies:
            raise ApplicationError(
                "cached speech/2 artifact has incompatible dependencies",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=found.id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        checkpoint = validate_checkpoint_semantics_v2(
            body,
            stage=configuration.stage,
            build=plan.build,
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
            configuration=configuration,
            input_sha256=input_checkpoint.sha256 if input_checkpoint else None,
            resource_profile=cast("Any", plan.resource_profile),
            model_manifest=cast("Any", plan.model_manifest),
            execution_topology=cast("str", plan.execution_topology),
        )
        if checkpoint.input_checkpoint != input_checkpoint:
            raise ApplicationError(
                "cached speech/2 checkpoint names a different predecessor reference",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        attempt_id: UUID | None = None
        call_id: str | None = None
        telemetry: StageTelemetry | None = None
        if checkpoint.operation_id == operation.id:
            attempt_id = checkpoint.attempt_id
            call_value = found.metadata.get("callId")
            call_id = str(call_value) if call_value is not None else None
            telemetry = StageTelemetry.model_validate(found.metadata.get("telemetry"))
            usage = _usage_v2(plan)
            usage["telemetry"] = telemetry.model_dump(mode="json", by_alias=True)
            await _await_terminal(
                ledger.complete_attempt(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    run_id=run_id,
                    operation_id=operation.id,
                    attempt_id=attempt_id,
                    owner_token=f"speech-v2:{run_id}:{operation.semantic_key}",
                    result_artifact_id=found.id,
                    usage=usage,
                    actual_cost_micros=None,
                )
            )
        else:
            await ledger.complete_operation_from_artifact(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=operation.id,
                result_artifact_id=found.id,
                expected_artifact_kind=SPEECH_ARTIFACT_KIND,
                expected_artifact_fingerprint=identity.fingerprint,
            )
        return AcceptedSpeechStageV2(
            stage=configuration.stage,
            artifact_id=found.id,
            checkpoint=_artifact_ref_v2(
                found, checkpoint.configuration_sha256, configuration.stage
            ),
            operation_id=operation.id,
            attempt_id=attempt_id,
            call_id=call_id,
            telemetry=telemetry,
            reused=True,
        )

    async def _prepare_stage_v2(
        self,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        configuration: StageConfigV2,
        input_stage: AcceptedSpeechStageV2 | None,
    ) -> AcceptedSpeechStageV2 | PreparedStageV2:
        _require_v2(plan)
        input_ref = input_stage.checkpoint if input_stage else None
        inputs = _semantic_inputs_v2(request, plan, input_ref)
        config = _semantic_config_v2(plan, configuration)
        acquired = await ledger.acquire_operation(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            run_id=run_id,
            kind=("diarize" if configuration.stage == "speaker_turns" else configuration.stage),
            stage=configuration.stage,
            inputs=inputs,
            config=config,
        )
        identity = ArtifactIdentity(
            kind=SPEECH_ARTIFACT_KIND,
            fingerprint=artifacts.fingerprint_for(
                inputs=inputs, config=config, kind=f"{configuration.stage}_v2"
            ),
        )
        cached = await self._cached_stage_v2(
            request=request,
            plan=plan,
            run_id=run_id,
            operation=acquired.operation,
            configuration=configuration,
            input_stage=input_stage,
            identity=identity,
        )
        if cached is not None:
            return cached
        if acquired.uncertain:
            raise ApplicationError(
                "speech operation has unresolved provider exposure",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        owner = f"speech-v2:{run_id}:{acquired.operation.semantic_key}"
        usage = _usage_v2(plan)
        request_hash = hashlib.sha256(
            canonical_json({"config": config, "inputs": inputs})
        ).hexdigest()
        route = {
            "app": plan.app,
            "environment": plan.environment,
            "estimate": usage["estimate"],
            "function": f"{configuration.stage}_v2",
            "modelManifestSha256": cast("Any", plan.model_manifest).sha256,
            "resourceProfileSha256": cast("Any", plan.resource_profile).sha256,
            "topology": plan.execution_topology,
        }
        reservation = ledger.AttemptReservationRequest(
            operation_id=acquired.operation.id,
            owner_token=owner,
            provider="modal",
            model=configuration.model,
            family="whisperx",
            route=route,
            request_hash=request_hash,
            estimated_cost_micros=cast("int", usage["estimatedMicros"]),
            dispatch_limit=plan.dispatch_limit,
        )
        return PreparedStageV2(
            request,
            plan,
            run_id,
            acquired.operation,
            configuration,
            input_stage,
            identity,
            owner,
            usage,
            reservation,
        )

    async def _reserve_v2(
        self, prepared: tuple[PreparedStageV2, ...]
    ) -> tuple[ledger.Attempt, ...]:
        first = prepared[0]

        async def reserve() -> tuple[ledger.Attempt, ...]:
            return await ledger.reserve_attempt_batch(
                self.ctx.settings.database_url,
                scope=first.request.scope,
                source_id=first.request.sourceId,
                run_id=first.run_id,
                requests=tuple(value.reservation for value in prepared),
            )

        try:
            return await reserve()
        except asyncio.CancelledError:
            attempts = await _await_committed(reserve())
            releases = [
                asyncio.create_task(
                    self._release_or_mark_unknown_on_cancel(
                        AttemptLease(
                            value.request,
                            value.run_id,
                            value.operation.id,
                            attempt.id,
                            value.owner,
                            value.usage,
                            value.configuration.stage,
                        ),
                        "speech/2 activity was cancelled while reservation was uncertain",
                    )
                )
                for value, attempt in zip(prepared, attempts, strict=True)
            ]
            await _drain_tasks(releases, cancel=False)
            raise

    async def _poll_v2(
        self,
        client: SpeechModalClient,
        lease: AttemptLease,
        handle: str,
        timeout_seconds: float,
        *,
        visible_progress: bool | asyncio.Event,
    ) -> SpeechStageResultV2:
        """Poll one v2 handle and fully settle its remote cancellation."""
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    state = await client.status(handle)
                    if isinstance(state, CallFinished):
                        if not isinstance(state.result, SpeechStageResultV2):
                            await _await_terminal(
                                self._mark_unknown(
                                    lease, "speech/2 received a speech/1 result envelope"
                                )
                            )
                            raise ApplicationError(
                                "speech/2 received a speech/1 result envelope",
                                non_retryable=True,
                                type="ProviderOutcomeUnknown",
                            )
                        return state.result
                    if isinstance(state, CallCancelled):
                        await _await_terminal(self._confirm_cancelled(lease))
                        raise ApplicationError(
                            "speech stage was cancelled remotely",
                            non_retryable=True,
                            type="SpeechCancelled",
                        )
                    if isinstance(state, (CallUnknown, CallUnreachable)):
                        message = (
                            state.message
                            if isinstance(state, CallUnreachable)
                            else "Modal no longer knows the speech call handle"
                        )
                        await _await_terminal(self._mark_unknown(lease, message))
                        raise ApplicationError(
                            message, non_retryable=True, type="ProviderOutcomeUnknown"
                        )
                    progress = await client.progress(str(lease.attempt_id))
                    visible_stage = _transcript_stage(cast("StageV2", lease.stage))
                    await report_speech_progress(
                        self.ctx.settings.database_url,
                        lease.request,
                        visible_stage,
                        update_visible_stage=_visible(visible_progress),
                    )
                    activity.heartbeat(
                        {
                            "attemptId": str(lease.attempt_id),
                            "callId": handle,
                            "progress": progress,
                        }
                    )
                    await ledger.heartbeat_attempt(
                        self.ctx.settings.database_url,
                        scope=lease.request.scope,
                        source_id=lease.request.sourceId,
                        operation_id=lease.operation_id,
                        attempt_id=lease.attempt_id,
                        owner_token=lease.owner,
                    )
                    await asyncio.sleep(POLL_SECONDS)
        except TimeoutError:
            return await self._cancel_expired_v2(client, lease, handle)
        except asyncio.CancelledError:
            with suppress(Exception):
                await _await_committed(
                    self._cancel_and_wait(client, lease, handle, return_finished=False)
                )
            raise

    async def _cancel_expired_v2(
        self, client: SpeechModalClient, lease: AttemptLease, handle: str
    ) -> SpeechStageResultV2:
        """Resolve deadline cancellation, recording ambiguity before external cancel escapes."""
        try:
            result = await self._cancel_and_wait(client, lease, handle, return_finished=True)
        except asyncio.CancelledError:
            with suppress(Exception):
                await _await_committed(
                    self._cancel_and_wait(client, lease, handle, return_finished=False)
                )
            raise
        if not isinstance(result, SpeechStageResultV2):
            await _await_terminal(
                self._mark_unknown(lease, "speech/2 received a speech/1 result envelope")
            )
            raise ApplicationError(
                "speech/2 received a speech/1 result envelope",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        return result

    async def _execute_stage_v2(
        self,
        prepared: PreparedStageV2,
        attempt: ledger.Attempt,
        *,
        visible_progress: bool | asyncio.Event,
    ) -> AcceptedSpeechStageV2:
        request, plan = prepared.request, prepared.plan
        _require_v2(plan)
        profile = cast("SpeechResourceProfile", plan.resource_profile)
        manifest = cast("SpeechModelManifest", plan.model_manifest)
        lease = AttemptLease(
            request,
            prepared.run_id,
            prepared.operation.id,
            attempt.id,
            prepared.owner,
            prepared.usage,
            prepared.configuration.stage,
        )
        client = self._client(plan)
        if attempt.state == "reserved":
            try:
                await report_speech_progress(
                    self.ctx.settings.database_url,
                    request,
                    _transcript_stage(prepared.configuration.stage),
                    update_visible_stage=_visible(visible_progress),
                )
                await assert_frozen_deployment(
                    client,
                    app=plan.app,
                    protocol=plan.protocol,
                    build=plan.build,
                    resource_profile=plan.resource_profile,
                    model_manifest=plan.model_manifest,
                )
            except asyncio.CancelledError:
                await _await_committed(
                    self._release_or_mark_unknown_on_cancel(
                        lease, "speech/2 activity was cancelled during its deployment check"
                    )
                )
                raise
            except Exception as error:
                await _await_terminal(
                    ledger.release_undispatched(
                        self.ctx.settings.database_url,
                        scope=request.scope,
                        source_id=request.sourceId,
                        run_id=prepared.run_id,
                        operation_id=prepared.operation.id,
                        attempt_id=attempt.id,
                        owner_token=prepared.owner,
                    )
                )
                raise ApplicationError(
                    f"speech deployment changed before {prepared.configuration.stage}: {error}",
                    non_retryable=True,
                    type="SpeechDeploymentChanged",
                ) from error
        try:
            should_spawn = await ledger.mark_dispatched(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=prepared.run_id,
                operation_id=prepared.operation.id,
                attempt_id=attempt.id,
                owner_token=prepared.owner,
                dispatch_limit=plan.dispatch_limit,
            )
        except asyncio.CancelledError:
            await _await_committed(
                self._release_or_mark_unknown_on_cancel(
                    lease, "speech/2 activity was cancelled at its dispatch fence"
                )
            )
            raise
        if should_spawn:
            # Re-read the same immutable attempt after the dispatch CAS to recover
            # its original deadline. This cannot create another attempt.
            (attempt,) = await self._reserve_v2((prepared,))
        handle = attempt.remote_handle
        job = SpeechStageJobV2(
            build=plan.build,
            operation_id=prepared.operation.id,
            attempt_id=attempt.id,
            stage=prepared.configuration.stage,
            artifact_prefix=request.artifactPrefix,
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
            checkpoint_key=(
                f"{request.artifactPrefix}transcript/v2/checkpoints/"
                f"{prepared.configuration.stage}/{attempt.id}.json"
            ),
            configuration=prepared.configuration,
            input_checkpoint=(prepared.input_stage.checkpoint if prepared.input_stage else None),
            resource_profile=profile,
            model_manifest=manifest,
            execution_topology=cast("ExecutionTopology", plan.execution_topology),
        )
        if should_spawn:
            try:
                handle = await client.spawn(prepared.configuration.stage, job)
                await ledger.attach_remote_handle(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    operation_id=prepared.operation.id,
                    attempt_id=attempt.id,
                    owner_token=prepared.owner,
                    remote_handle=handle,
                )
                await ledger.mark_running(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    operation_id=prepared.operation.id,
                    attempt_id=attempt.id,
                    owner_token=prepared.owner,
                )
            except asyncio.CancelledError:
                if handle:
                    with suppress(Exception):
                        await _await_committed(
                            self._cancel_and_wait(client, lease, handle, return_finished=False)
                        )
                else:
                    await _await_committed(
                        self._mark_unknown(
                            lease,
                            "speech/2 spawn acknowledgement was lost during cancellation",
                        )
                    )
                raise
            except Exception as error:
                await _await_terminal(
                    self._mark_unknown(
                        lease,
                        f"spawn acknowledgement was lost: {type(error).__name__}: {error}",
                    )
                )
                raise ApplicationError(
                    "speech dispatch outcome is unknown",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                ) from error
        if not handle:
            await _await_terminal(
                self._mark_unknown(
                    lease, "dispatch fence was committed without a recoverable Modal handle"
                )
            )
            raise ApplicationError(
                "speech dispatch has no recoverable handle",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        if not should_spawn:
            try:
                await report_speech_progress(
                    self.ctx.settings.database_url,
                    request,
                    _transcript_stage(prepared.configuration.stage),
                    update_visible_stage=_visible(visible_progress),
                )
            except asyncio.CancelledError:
                with suppress(Exception):
                    await _await_committed(
                        self._cancel_and_wait(client, lease, handle, return_finished=False)
                    )
                raise
        if attempt.dispatched_at is None:
            await _await_terminal(
                self._mark_unknown(lease, "speech attempt has no original dispatch timestamp")
            )
            raise ApplicationError(
                "speech attempt deadline cannot be recovered",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        remaining = remaining_attempt_seconds(
            attempt.dispatched_at, cast("Any", plan.resource_profile).stage_timeout_seconds
        )
        result = (
            await self._poll_v2(
                client,
                lease,
                handle,
                remaining,
                visible_progress=visible_progress,
            )
            if remaining > 0
            else await self._cancel_expired_v2(client, lease, handle)
        )

        async def reconcile_terminal() -> AcceptedSpeechStageV2:
            if (
                result.protocol != plan.protocol
                or result.build != plan.build
                or result.operation_id != prepared.operation.id
                or result.attempt_id != attempt.id
                or result.stage != prepared.configuration.stage
                or result.modal_call_id != handle
                or result.resource_profile != plan.resource_profile
                or result.model_manifest != plan.model_manifest
                or result.execution_topology != plan.execution_topology
            ):
                await self._mark_unknown(lease, "speech/2 result identity is incompatible")
                raise ApplicationError(
                    "speech result identity is incompatible",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            usage = dict(prepared.usage)
            usage["telemetry"] = result.telemetry.model_dump(mode="json", by_alias=True)
            if result.status == "outcome_unknown":
                await self._mark_unknown(
                    lease,
                    (
                        result.error.message
                        if result.error
                        else "outcome-unknown result omitted error"
                    ),
                    result.telemetry,
                )
                raise ApplicationError(
                    "speech provider outcome is unknown",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            if result.status == "failed":
                if result.error is None:
                    await self._mark_unknown(lease, "failed result omitted error", result.telemetry)
                    raise ApplicationError(
                        "failed speech result omitted error",
                        non_retryable=True,
                        type="ProviderOutcomeUnknown",
                    )
                await ledger.fail_attempt(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    run_id=prepared.run_id,
                    operation_id=prepared.operation.id,
                    attempt_id=attempt.id,
                    owner_token=prepared.owner,
                    outcome_known=True,
                    actual_cost_micros=None,
                    usage=usage,
                    error_code=result.error.type,
                    error_message=result.error.message,
                )
                if (
                    prepared.configuration.stage == "recognize"
                    and result.error.retry_class == "resource_oom"
                ):
                    raise StageOomError(result.error.message)
                raise ApplicationError(
                    result.error.message,
                    non_retryable=True,
                    type="SpeechStageFailure",
                )
            if result.checkpoint is None:
                await self._mark_unknown(lease, "successful result omitted checkpoint")
                raise ApplicationError(
                    "speech result omitted its checkpoint",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            body = await ObstoreCheckpointStore(self.ctx.store).read(result.checkpoint.key)
            if body is None:
                await self._mark_unknown(
                    lease, "successful speech call returned a missing checkpoint"
                )
                raise ApplicationError(
                    "speech checkpoint is missing after inference",
                    non_retryable=True,
                    type="SpeechCheckpointFailure",
                )
            if (
                len(body) != result.checkpoint.size_bytes
                or hashlib.sha256(body).hexdigest() != result.checkpoint.sha256
                or result.checkpoint.stage != prepared.configuration.stage
            ):
                await self._mark_unknown(
                    lease, "speech checkpoint bytes differ from the returned immutable reference"
                )
                raise ApplicationError(
                    "speech checkpoint reference is incompatible",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            admission_body = await ObstoreCheckpointStore(self.ctx.store).read(
                admission_key_v2(job)
            )
            if admission_body is None:
                await self._mark_unknown(
                    lease, "successful speech checkpoint has no producer admission"
                )
                raise ApplicationError(
                    "speech checkpoint producer admission is missing",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            try:
                checkpoint = validate_fresh_checkpoint_v2(
                    job, result, body, admission_body, handle=handle
                )
            except Exception as error:
                await self._mark_unknown(
                    lease, f"speech checkpoint exact job identity is incompatible: {error}"
                )
                raise ApplicationError(
                    "speech checkpoint exact job identity is incompatible",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                ) from error
            artifact = await artifacts.accept_existing_json(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                identity=prepared.identity,
                key=result.checkpoint.key,
                sha256=result.checkpoint.sha256,
                size_bytes=result.checkpoint.size_bytes,
                metadata={
                    "format": "speech-checkpoint/2",
                    "attemptId": str(attempt.id),
                    "callId": handle,
                    "operationId": str(prepared.operation.id),
                    "stage": prepared.configuration.stage,
                    "checkpointReused": result.checkpoint_reused,
                    "respondingTaskId": result.modal_task_id,
                    "producerExecution": cast("Any", result.execution_identity).model_dump(
                        mode="json", by_alias=True
                    ),
                    "telemetry": usage["telemetry"],
                    "estimate": prepared.usage["estimate"],
                    "modelManifest": manifest.model_dump(mode="json", by_alias=True),
                    "modelProvenance": checkpoint.model_provenance.model_dump(
                        mode="json", by_alias=True
                    ),
                    "resourceProfile": profile.model_dump(mode="json", by_alias=True),
                    "executionTopology": plan.execution_topology,
                },
                dependency_ids=([prepared.input_stage.artifact_id] if prepared.input_stage else []),
            )
            await ledger.complete_attempt(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=prepared.run_id,
                operation_id=prepared.operation.id,
                attempt_id=attempt.id,
                owner_token=prepared.owner,
                result_artifact_id=artifact.id,
                usage=usage,
                actual_cost_micros=None,
            )
            return AcceptedSpeechStageV2(
                stage=prepared.configuration.stage,
                artifact_id=artifact.id,
                checkpoint=result.checkpoint,
                operation_id=prepared.operation.id,
                attempt_id=attempt.id,
                call_id=handle,
                telemetry=result.telemetry,
            )

        return cast(
            "AcceptedSpeechStageV2",
            await _await_terminal(reconcile_terminal()),
        )

    async def _run_prepared_v2(
        self, prepared: PreparedStageV2, *, visible_progress: bool | asyncio.Event
    ) -> AcceptedSpeechStageV2:
        (attempt,) = await self._reserve_v2((prepared,))
        return await self._execute_stage_v2(prepared, attempt, visible_progress=visible_progress)

    async def _recognize_chain_v2(
        self,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        initial: AcceptedSpeechStageV2 | PreparedStageV2,
        attempt: ledger.Attempt | None,
    ) -> tuple[AcceptedSpeechStageV2, AcceptedSpeechStageV2]:
        recognized: AcceptedSpeechStageV2 | None = None
        candidate = initial
        last_oom: StageOomError | None = None
        batches = (16, 8, 4) if plan.allow_oom_recovery else (16,)
        for index, batch in enumerate(batches):
            if index:
                candidate = await self._prepare_stage_v2(
                    request,
                    plan,
                    run_id,
                    cast("RecognizeConfigV2", plan.recognize_v2).model_copy(
                        update={"batch_size": batch}
                    ),
                    None,
                )
                attempt = None
            try:
                recognized = (
                    candidate
                    if isinstance(candidate, AcceptedSpeechStageV2)
                    else await (
                        self._execute_stage_v2(
                            candidate,
                            attempt,
                            visible_progress=True,
                        )
                        if attempt is not None
                        else self._run_prepared_v2(candidate, visible_progress=True)
                    )
                )
                break
            except StageOomError as error:
                last_oom = error
        if recognized is None:
            raise ApplicationError(
                str(last_oom or "recognition exhausted its OOM plan"),
                non_retryable=True,
                type="SpeechResourceOom",
            )
        body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=recognized.artifact_id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        checkpoint = SpeechCheckpointV2.model_validate_json(body)
        language = str(checkpoint.payload.get("language") or "und")
        aligned_prepared = await self._prepare_stage_v2(
            request, plan, run_id, alignment_config_v2(language), recognized
        )
        aligned = (
            aligned_prepared
            if isinstance(aligned_prepared, AcceptedSpeechStageV2)
            else await self._run_prepared_v2(aligned_prepared, visible_progress=True)
        )
        return recognized, aligned

    async def _parallel_branches(
        self,
        recognition: Coroutine[Any, Any, object],
        turns: Coroutine[Any, Any, object],
    ) -> tuple[object, object]:
        tasks = [asyncio.create_task(recognition), asyncio.create_task(turns)]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            failure = next((task.exception() for task in done if task.exception()), None)
            if failure is not None:
                await _drain_tasks(list(pending), cancel=True)
                raise failure
            return tuple(await asyncio.gather(*tasks))  # type: ignore[return-value]
        except asyncio.CancelledError:
            await _drain_tasks(tasks, cancel=True)
            raise

    async def _assignment_v2(  # noqa: PLR0917
        self,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        recognized: AcceptedSpeechStageV2,
        aligned: AcceptedSpeechStageV2,
        turns: AcceptedSpeechStageV2,
    ) -> AcceptedSpeechAssignment:
        _require_v2(plan)
        profile = cast("SpeechResourceProfile", plan.resource_profile)
        manifest = cast("SpeechModelManifest", plan.model_manifest)
        topology = cast("ExecutionTopology", plan.execution_topology)
        align_body, turns_body = await asyncio.gather(
            artifacts.read_artifact_bytes(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                artifact_id=aligned.artifact_id,
                max_bytes=MAX_CHECKPOINT_BYTES,
            ),
            artifacts.read_artifact_bytes(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                artifact_id=turns.artifact_id,
                max_bytes=MAX_CHECKPOINT_BYTES,
            ),
        )
        align_checkpoint = SpeechCheckpointV2.model_validate_json(align_body)
        turns_checkpoint = SpeechCheckpointV2.model_validate_json(turns_body)
        expected_source = CheckpointSource(
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
        )
        if (
            len(align_body) != aligned.checkpoint.size_bytes
            or hashlib.sha256(align_body).hexdigest() != aligned.checkpoint.sha256
            or len(turns_body) != turns.checkpoint.size_bytes
            or hashlib.sha256(turns_body).hexdigest() != turns.checkpoint.sha256
            or align_checkpoint.stage != "align"
            or align_checkpoint.input_checkpoint != recognized.checkpoint
            or turns_checkpoint.stage != "speaker_turns"
            or turns_checkpoint.input_checkpoint is not None
            or align_checkpoint.source != expected_source
            or turns_checkpoint.source != expected_source
            or align_checkpoint.resource_profile != profile
            or turns_checkpoint.resource_profile != profile
            or align_checkpoint.model_manifest != manifest
            or turns_checkpoint.model_manifest != manifest
            or align_checkpoint.execution_topology != topology
            or turns_checkpoint.execution_topology != topology
        ):
            raise ApplicationError(
                "speaker assignment inputs have incompatible immutable identity",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        config = SpeakerAssignmentConfig()
        inputs = {
            "alignSha256": aligned.checkpoint.sha256,
            "speakerTurnsSha256": turns.checkpoint.sha256,
            "source": {
                "audioKey": request.audioKey,
                "audioSha256": plan.audio_sha256,
                "audioSizeBytes": plan.audio_size_bytes,
                "durationMs": request.durationMs,
            },
        }
        semantic_config = {
            "assignment": config.model_dump(mode="json", by_alias=True),
            "build": plan.build,
            "executionTopology": plan.execution_topology,
            "modelManifest": manifest.model_dump(mode="json", by_alias=True),
            "protocol": plan.protocol,
            "resourceProfile": profile.model_dump(mode="json", by_alias=True),
        }
        acquired = await ledger.acquire_operation(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            run_id=run_id,
            kind="diarize",
            stage="assign_speakers",
            inputs=inputs,
            config=semantic_config,
        )
        identity = ArtifactIdentity(
            kind=SPEECH_ASSIGNMENT_KIND,
            fingerprint=artifacts.fingerprint_for(
                kind="assign_speakers", inputs=inputs, config=semantic_config
            ),
        )
        found = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            identity=identity,
        )
        reused = found is not None
        if found is None:
            if acquired.uncertain:
                raise ApplicationError(
                    "speaker assignment operation is uncertain",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            content = assignment_for(
                build=plan.build,
                source=expected_source,
                alignment_checkpoint=aligned.checkpoint,
                speaker_turns_checkpoint=turns.checkpoint,
                resource_profile=profile,
                model_manifest=manifest,
                execution_topology=topology,
                aligned_payload=align_checkpoint.payload,
                diarization=turns_checkpoint.payload.get("diarization"),
                configuration=config,
            )
            found = await artifacts.publish_json(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                identity=identity,
                content=content.model_dump(mode="json", by_alias=True),
                metadata={
                    "format": "speech-assignment/1",
                    "runId": str(run_id),
                    "build": plan.build,
                    "executionTopology": plan.execution_topology,
                },
                dependency_ids=[aligned.artifact_id, turns.artifact_id],
            )
        expected_dependencies = tuple(sorted((aligned.artifact_id, turns.artifact_id), key=str))
        if found.dependency_ids != expected_dependencies:
            raise ApplicationError(
                "speaker assignment artifact has incompatible dependencies",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=found.id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        content = SpeechAssignmentV1.model_validate_json(body)
        if (
            content.build != plan.build
            or content.source.audio_sha256 != plan.audio_sha256
            or content.alignment_checkpoint != aligned.checkpoint
            or content.speaker_turns_checkpoint != turns.checkpoint
            or content.resource_profile != plan.resource_profile
            or content.model_manifest != plan.model_manifest
            or content.execution_topology != plan.execution_topology
        ):
            raise ApplicationError(
                "speaker assignment artifact identity is incompatible",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        await ledger.complete_operation_from_artifact(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            run_id=run_id,
            operation_id=acquired.operation.id,
            result_artifact_id=found.id,
            expected_artifact_kind=SPEECH_ASSIGNMENT_KIND,
            expected_artifact_fingerprint=identity.fingerprint,
        )
        return AcceptedSpeechAssignment(
            artifact_id=found.id,
            artifact=AssignmentArtifactRef(
                key=found.storage_key,
                sha256=found.sha256,
                size_bytes=found.size_bytes,
                configuration_sha256=content.configuration_sha256,
            ),
            operation_id=acquired.operation.id,
            reused=reused,
        )

    @activity.defn(name="checkpointed_transcribe_v2")
    async def checkpointed_transcribe_v2(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        """Run v2 under liveness that also covers its post-provider tail."""
        return await run_with_activity_heartbeat(
            lambda: self._checkpointed_transcribe_v2(request, plan),
            details={"stage": "checkpointed_transcribe_v2"},
        )

    async def _checkpointed_transcribe_v2(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        """Run v2 recognition/alignment and independent speaker turns."""
        _require_v2(plan)
        recognize_config = cast("RecognizeConfigV2", plan.recognize_v2)
        turns_config = cast("SpeakerTurnsConfig", plan.speaker_turns)
        run_id = await ensure_speech_run(
            self.ctx.settings.database_url,
            request=request,
            plan=plan,
            temporal_run_id=workflow_run_id(),
        )
        recognize = await self._prepare_stage_v2(request, plan, run_id, recognize_config, None)
        turns = await self._prepare_stage_v2(request, plan, run_id, turns_config, None)
        if plan.execution_topology == "parallel":
            missing = tuple(
                value for value in (recognize, turns) if isinstance(value, PreparedStageV2)
            )
            attempts = await self._reserve_v2(missing) if missing else ()
            by_operation = {value.operation_id: value for value in attempts}
            turns_visible = asyncio.Event()

            async def recognition_then_reveal() -> object:
                result = await self._recognize_chain_v2(
                    request,
                    plan,
                    run_id,
                    recognize,
                    (
                        by_operation[recognize.operation.id]
                        if isinstance(recognize, PreparedStageV2)
                        else None
                    ),
                )
                turns_visible.set()
                await report_speech_progress(
                    self.ctx.settings.database_url,
                    request,
                    TranscriptStage.diarize,
                )
                return result

            recognition_result, turns_result = await self._parallel_branches(
                recognition_then_reveal(),
                (
                    asyncio.sleep(0, result=turns)
                    if isinstance(turns, AcceptedSpeechStageV2)
                    else self._execute_stage_v2(
                        turns,
                        by_operation[turns.operation.id],
                        visible_progress=turns_visible,
                    )
                ),
            )
            recognized, aligned = cast(
                "tuple[AcceptedSpeechStageV2, AcceptedSpeechStageV2]",
                recognition_result,
            )
            speaker_turns = cast("AcceptedSpeechStageV2", turns_result)
        else:
            recognized, aligned = await self._recognize_chain_v2(
                request, plan, run_id, recognize, None
            )
            speaker_turns = (
                turns
                if isinstance(turns, AcceptedSpeechStageV2)
                else await self._run_prepared_v2(turns, visible_progress=True)
            )
        assignment = await self._assignment_v2(
            request, plan, run_id, recognized, aligned, speaker_turns
        )
        return ParallelCheckpointedTranscription(
            run_id=run_id,
            recognize=recognized,
            align=aligned,
            speaker_turns=speaker_turns,
            assignment=assignment,
        )

    @activity.defn(name="assemble_checkpointed_transcript_v2")
    async def assemble_checkpointed_transcript_v2(
        self,
        request: TranscribeInput,
        attempt: int,
        plan: TranscriptionPlan,
        stages: ParallelCheckpointedTranscription,
        coverage: CoverageRecord,
    ) -> TranscribeRecord:
        """Verify the CPU join, assess coverage, and return normalizer-compatible raw."""
        _require_v2(plan)
        body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=stages.assignment.artifact_id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        if (
            len(body) != stages.assignment.artifact.size_bytes
            or hashlib.sha256(body).hexdigest() != stages.assignment.artifact.sha256
        ):
            raise ApplicationError(
                "accepted speaker assignment differs from its workflow reference",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        assignment = SpeechAssignmentV1.model_validate_json(body)
        expected_source = CheckpointSource(
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
        )
        if (
            assignment.build != plan.build
            or assignment.source != expected_source
            or assignment.alignment_checkpoint != stages.align.checkpoint
            or assignment.speaker_turns_checkpoint != stages.speaker_turns.checkpoint
            or assignment.resource_profile != plan.resource_profile
            or assignment.model_manifest != plan.model_manifest
            or assignment.execution_topology != plan.execution_topology
        ):
            raise ApplicationError(
                "accepted speaker assignment has incompatible identity",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        raw = cast("dict[str, object]", assignment.raw)
        recognition, warnings = recognition_spans(raw, request.durationMs)
        detected: list[tuple[int, int]] | None = None
        detector_error = coverage.error
        if coverage.artifact_id is not None:
            evidence = await artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                artifact_id=coverage.artifact_id,
            )
            if not isinstance(evidence, dict):
                raise ApplicationError(
                    "coverage artifact is not an object",
                    non_retryable=True,
                    type="SpeechCheckpointFailure",
                )
            evidence_map = cast("dict[str, object]", evidence)
            if evidence_map.get("status") == "measured":
                expected_coverage_source = {
                    "audioKey": request.audioKey,
                    "audioSha256": plan.audio_sha256,
                    "audioSizeBytes": plan.audio_size_bytes,
                    "durationMs": request.durationMs,
                }
                if evidence_map.get("source") != expected_coverage_source:
                    raise ApplicationError(
                        "coverage artifact names a different frozen source",
                        non_retryable=True,
                        type="SpeechCheckpointFailure",
                    )
                expected_detector = (
                    plan.detector,
                    plan.detector_revision,
                    plan.detector_sha256,
                )
                actual_detector = (
                    evidence_map.get("detector"),
                    evidence_map.get("detector_revision"),
                    evidence_map.get("detector_sha256"),
                )
                if actual_detector != expected_detector:
                    raise ApplicationError(
                        "coverage artifact names a different detector build",
                        non_retryable=True,
                        type="SpeechCheckpointFailure",
                    )
                detected = [
                    (int(row["start_ms"]), int(row["end_ms"]))
                    for row in cast("list[dict[str, Any]]", evidence_map.get("intervals", []))
                ]
            else:
                detector_error = str(evidence_map.get("error") or "detector unknown")
        if warnings:
            detector_error = "; ".join(warnings)
        assessment = assess_coverage(
            detected, recognition, request.durationMs, detector_error=detector_error
        )
        key = f"{request.artifactPrefix}transcript/checkpointed-v2-raw-{stages.run_id}.json"
        await storage.upload_bytes(self.ctx.store, key, canonical_json(raw), "application/json")
        gpu_stages = (stages.recognize, stages.align, stages.speaker_turns)
        await report_speech_progress(
            self.ctx.settings.database_url, request, TranscriptStage.speech_coverage
        )
        return TranscribeRecord(
            raw_key=key,
            language=str(raw.get("language") or "und"),
            gpu_seconds=sum(
                value.telemetry.elapsed_seconds if value.telemetry else 0 for value in gpu_stages
            ),
            gpu="L4",
            attempt=attempt,
            call_id=stages.speaker_turns.call_id,
            metadata={
                "checkpointedSpeech": {
                    "build": plan.build,
                    "coverage": asdict(assessment),
                    "coverageArtifactId": (
                        str(coverage.artifact_id) if coverage.artifact_id else None
                    ),
                    "protocol": plan.protocol,
                    "runId": str(stages.run_id),
                    "executionTopology": plan.execution_topology,
                    "assignment": stages.assignment.model_dump(mode="json", by_alias=True),
                    "stages": {
                        value.stage: value.model_dump(mode="json", by_alias=True)
                        for value in gpu_stages
                    },
                }
            },
        )

    def activities(self) -> list[Callable[..., Any]]:
        """Register v1 and additive v2 activity names together during drain."""
        return [
            *super().activities(),
            self.checkpointed_transcribe_v2,
            self.assemble_checkpointed_transcript_v2,
        ]
