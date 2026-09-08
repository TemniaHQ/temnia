"""Durable worker-side orchestration for checkpointed speech and coverage."""

# State transitions are intentionally adjacent to the provider boundary.
# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0913, PLR0915, TRY003, TRY301

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import obstore as obs
from temporalio import activity
from temporalio.exceptions import ApplicationError

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput, TranscriptStage
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.artifacts import ArtifactIdentity, HarnessArtifact
from temnia_pipeline.media.speech_pcm import stream_pcm_windows
from temnia_pipeline.settings import MIN_SPEECH_RATE_MICROS_PER_HOUR
from temnia_pipeline.speech.checkpoints import (
    MAX_CHECKPOINT_BYTES,
    ObstoreCheckpointStore,
    validate_checkpoint_semantics,
)
from temnia_pipeline.speech.client import (
    CallCancelled,
    CallFinished,
    CallUnknown,
    CallUnreachable,
    SpeechModalClient,
    assert_checkpointed_deployment,
    assert_frozen_deployment,
)
from temnia_pipeline.speech.contracts import (
    ArtifactRef,
    SpeechCheckpointV1,
    SpeechStageJob,
    SpeechStageResult,
    Stage,
    StageConfig,
    StageTelemetry,
    canonical_json,
)
from temnia_pipeline.speech.coverage import assess_coverage
from temnia_pipeline.speech.progress import report_speech_progress
from temnia_pipeline.speech.silero import (
    SILERO_DETECTOR,
    SILERO_REVISION,
    SILERO_SHA256,
    SileroOnnx,
    detect_speech,
)
from temnia_pipeline.transcription import TranscribeRecord
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechStage,
    CheckpointedTranscription,
    CoverageRecord,
    TranscriptionPlan,
    alignment_config,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from temnia_pipeline.ingest import Context

POLL_SECONDS = 5.0
CANCEL_CONFIRM_SECONDS = 30.0
SPEECH_ARTIFACT_KIND = "speech_checkpoint"
SPEECH_RUN_NAMESPACE = uuid5(NAMESPACE_URL, "temnia:speech-run:v1")


def workflow_run_id() -> str:
    """The Temporal run identity used for the frozen harness run."""
    return activity.info().workflow_run_id or "unknown"


def _validate_request_paths(request: TranscribeInput) -> None:
    expected_prefix = f"org/{request.scope.organizationId}/source/{request.sourceId}/"
    if request.artifactPrefix != expected_prefix:
        raise ledger.IdentityConflict(
            "speech artifact prefix does not match the scoped source identity"
        )
    if request.audioKey != f"{expected_prefix}audio/audio.m4a":
        raise ledger.IdentityConflict(
            "speech audio key does not match the source's accepted audio artifact"
        )


async def _validate_request_source(database_url: str, request: TranscribeInput) -> None:
    _validate_request_paths(request)
    async with db.scoped(database_url, request.scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (request.sourceId,),
            )
        ).fetchone()
        if source is None:
            raise ledger.IdentityConflict("source is absent from the active scope")
        if source["deletion_requested_at"] is not None:
            raise ledger.SourceDeleting("source is fenced for deletion")


async def _periodic_progress(
    stop: asyncio.Event,
    database_url: str,
    request: TranscribeInput,
    stage: TranscriptStage,
    *,
    update_visible_stage: bool = True,
) -> None:
    while not stop.is_set():
        await report_speech_progress(
            database_url,
            request,
            stage,
            update_visible_stage=update_visible_stage,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except TimeoutError:
            continue


async def _audio_identity(store: Any, key: str) -> tuple[str, int]:  # noqa: ANN401
    result = await obs.get_async(store, key)
    expected = int(result.meta["size"])
    digest = hashlib.sha256()
    size = 0
    async for chunk in result.stream(min_chunk_size=8 * 1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
        activity.heartbeat({"stage": "planning", "audioBytesHashed": size})
        await asyncio.sleep(0)
    if size != expected:
        raise OSError(f"audio size changed while freezing source identity at {key}")
    return digest.hexdigest(), size


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _recognition_spans(
    raw: dict[str, object], duration_ms: int
) -> tuple[list[tuple[int, int]], list[str]]:
    segments = raw.get("segments")
    if not isinstance(segments, list):
        return [], ["raw transcript has no segment list for coverage comparison"]
    spans: list[tuple[int, int]] = []
    warnings: list[str] = []
    for index, item in enumerate(cast("list[object]", segments)):
        if not isinstance(item, dict):
            warnings.append(f"segment {index} is not an object")
            continue
        segment = cast("dict[str, object]", item)
        start = segment.get("start")
        end = segment.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int | float)
            or not isinstance(end, int | float)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
        ):
            warnings.append(f"segment {index} has missing or nonfinite bounds")
            continue
        start_ms = round(float(start) * 1000)
        end_ms = round(float(end) * 1000)
        if end_ms <= start_ms:
            warnings.append(f"segment {index} has a nonpositive time range")
            continue
        if start_ms < 0 or end_ms > duration_ms:
            warnings.append(f"segment {index} extends outside the source duration")
        spans.append((start_ms, end_ms))
    return spans, warnings[:32]


def remaining_attempt_seconds(
    dispatched_at: datetime, timeout_seconds: int, *, now: datetime | None = None
) -> float:
    """Return the original physical call's remaining bound across activity retries."""
    current = now or datetime.now(UTC)
    return timeout_seconds - max(0.0, (current - dispatched_at).total_seconds())


def result_identity_matches(
    result: SpeechStageResult,
    *,
    plan: TranscriptionPlan,
    operation_id: UUID,
    attempt_id: UUID,
    stage: Stage,
    call_id: str,
) -> bool:
    """Validate every physical result envelope before interpreting status or OOM."""
    return (
        result.protocol == plan.protocol
        and result.build == plan.build
        and result.operation_id == operation_id
        and result.attempt_id == attempt_id
        and result.stage == stage
        and result.modal_call_id == call_id
    )


def _estimated_cost(plan: TranscriptionPlan) -> int:
    covered_seconds = plan.stage_timeout_seconds + plan.startup_timeout_seconds
    return (plan.rate_micros_per_hour * covered_seconds + 3599) // 3600


def _estimate_usage(plan: TranscriptionPlan) -> dict[str, object]:
    covered_seconds = plan.stage_timeout_seconds + plan.startup_timeout_seconds
    return {
        "costEvidence": "unavailable",
        "estimatedMicros": _estimated_cost(plan),
        "estimate": {
            "coveredSeconds": covered_seconds,
            "cpuCores": 4,
            "excludes": [
                "cold-start time beyond the configured startup allowance",
                "pre-function crashes",
                "platform recovery",
                "unreported provider charges",
            ],
            "gpu": "L4",
            "memoryMiB": 16_384,
            "rateDate": "2026-09-08",
            "rateFloorMicrosPerHour": MIN_SPEECH_RATE_MICROS_PER_HOUR,
            "rateMicrosPerHour": plan.rate_micros_per_hour,
            "startupAllowanceSeconds": plan.startup_timeout_seconds,
            "stageDeadlineSeconds": plan.stage_timeout_seconds,
            "scope": "configured invocation exposure; not a provider invoice ceiling",
        },
    }


async def ensure_speech_run(
    database_url: str,
    *,
    request: TranscribeInput,
    plan: TranscriptionPlan,
    temporal_run_id: str,
) -> UUID:
    """Create/reuse the exact budget/config snapshot under the source deletion lock."""
    _validate_request_paths(request)
    run_id = uuid5(SPEECH_RUN_NAMESPACE, temporal_run_id)
    config = plan.model_dump(mode="json", by_alias=True)
    route = {
        "app": plan.app,
        "build": plan.build,
        "environment": plan.environment,
        "protocol": plan.protocol,
    }
    async with db.scoped(database_url, request.scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (request.sourceId,),
            )
        ).fetchone()
        if source is None:
            raise ledger.IdentityConflict("source is absent from the active scope")
        if source["deletion_requested_at"] is not None:
            raise ledger.SourceDeleting("source is fenced for deletion")
        row = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (id, organization_id, source_id, request_key, lane, budget_micros,
                     config, route_snapshot, workflow_id, workflow_run_id)
                VALUES (%s, %s, %s, %s, 'transcription', %s, %s::jsonb, %s::jsonb, %s, %s)
                ON CONFLICT (organization_id, source_id, request_key) DO NOTHING
                RETURNING *
                """,
                (
                    run_id,
                    request.scope.organizationId,
                    request.sourceId,
                    temporal_run_id,
                    plan.budget_micros,
                    json.dumps(config, allow_nan=False, separators=(",", ":"), sort_keys=True),
                    json.dumps(route, allow_nan=False, separators=(",", ":"), sort_keys=True),
                    activity.info().workflow_id,
                    temporal_run_id,
                ),
            )
        ).fetchone()
        if row is None:
            row = await (
                await conn.execute(
                    """
                    SELECT * FROM harness_run
                     WHERE organization_id = %s AND source_id = %s AND request_key = %s
                     FOR UPDATE
                    """,
                    (request.scope.organizationId, request.sourceId, temporal_run_id),
                )
            ).fetchone()
        if row is None:
            raise ledger.IdentityConflict("speech run identity disappeared while acquiring it")
        expected = (
            run_id,
            "transcription",
            plan.budget_micros,
            config,
            route,
            temporal_run_id,
        )
        actual = (
            row["id"],
            str(row["lane"]),
            int(row["budget_micros"]),
            row["config"],
            row["route_snapshot"],
            str(row["workflow_run_id"]),
        )
        if actual != expected:
            raise ledger.IdentityConflict(
                "speech run request key maps to different immutable facts"
            )
    return run_id


async def settle_speech_run(
    database_url: str,
    *,
    request: TranscribeInput,
    temporal_run_id: str,
    outcome: Literal["ready", "failed", "cancelled", "budget_paused"],
) -> bool:
    """Close a checkpointed run only after its physical execution facts are terminal."""
    run_id = uuid5(SPEECH_RUN_NAMESPACE, temporal_run_id)
    async with db.scoped(database_url, request.scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id FROM source WHERE id = %s FOR UPDATE", (request.sourceId,)
            )
        ).fetchone()
        if source is None:
            raise ledger.IdentityConflict("speech run source is absent from its scope")
        run = await (
            await conn.execute("SELECT * FROM harness_run WHERE id = %s FOR UPDATE", (run_id,))
        ).fetchone()
        if run is None:
            return False
        if (
            run["organization_id"] != request.scope.organizationId
            or run["source_id"] != request.sourceId
            or str(run["lane"]) != "transcription"
            or str(run["workflow_run_id"]) != temporal_run_id
        ):
            raise ledger.IdentityConflict("speech run settlement identity does not match")
        current_status = str(run["status"])
        if current_status in {"ready", "failed", "cancelled", "budget_paused"}:
            if current_status != outcome:
                raise ledger.IdentityConflict(
                    "speech run was already settled with a different outcome"
                )
            return True
        attempts = await (
            await conn.execute(
                """
                SELECT
                    count(*) FILTER (
                        WHERE state NOT IN ('succeeded', 'failed_known', 'cancelled_confirmed')
                    )::int AS nonterminal,
                    count(*) FILTER (WHERE state = 'outcome_unknown')::int AS unknown
                  FROM harness_attempt
                 WHERE run_id = %s
                """,
                (run_id,),
            )
        ).fetchone()
        if attempts is None:
            raise RuntimeError("speech run attempt settlement query returned no row")
        if int(attempts["unknown"]) > 0 or current_status == "outcome_unknown":
            return False
        if int(attempts["nonterminal"]) > 0:
            raise ledger.IdentityConflict("speech run still has a nonterminal physical attempt")
        if outcome == "ready":
            stages = await (
                await conn.execute(
                    """
                    SELECT DISTINCT stage FROM harness_operation
                     WHERE run_id = %s AND status = 'succeeded'
                       AND result_artifact_id IS NOT NULL
                       AND stage IN ('recognize', 'align', 'diarize')
                    """,
                    (run_id,),
                )
            ).fetchall()
            if {str(row["stage"]) for row in stages} != {"recognize", "align", "diarize"}:
                raise ledger.IdentityConflict(
                    "speech run cannot complete without three accepted stage operations"
                )
        updated = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = %s, stage = %s, updated_at = now()
                 WHERE id = %s AND status <> 'outcome_unknown'
                 RETURNING id
                """,
                (outcome, outcome, run_id),
            )
        ).fetchone()
        return updated is not None


def _semantic_inputs(
    request: TranscribeInput, plan: TranscriptionPlan, input_checkpoint: ArtifactRef | None
) -> dict[str, object]:
    return {
        "audioKey": request.audioKey,
        "audioSha256": plan.audio_sha256,
        "audioSizeBytes": plan.audio_size_bytes,
        "durationMs": request.durationMs,
        "inputSha256": input_checkpoint.sha256 if input_checkpoint else None,
    }


def semantic_stage_config(
    plan: TranscriptionPlan, configuration: StageConfig, *, cache_scope_run_id: UUID
) -> dict[str, object]:
    """Scope mutable model aliases to one run until exact weights are configured."""
    return {
        "app": plan.app,
        "build": plan.build,
        "cacheScopeRunId": str(cache_scope_run_id),
        "modelIdentityStatus": "unresolved_mutable_alias",
        "protocol": plan.protocol,
        "stageConfig": configuration.model_dump(mode="json", by_alias=True),
    }


def _artifact_ref(
    artifact: HarnessArtifact, stage: Stage, configuration_sha256: str
) -> ArtifactRef:
    return ArtifactRef(
        key=artifact.storage_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        stage=stage,
        configuration_sha256=configuration_sha256,
    )


class StageOomError(RuntimeError):
    """A known explicit resource OOM eligible for the bounded batch plan."""


@dataclass(frozen=True, slots=True)
class AttemptLease:
    """The exact ledger ownership needed while one physical call is live."""

    request: TranscribeInput
    run_id: UUID
    operation_id: UUID
    attempt_id: UUID
    owner: str
    usage_base: dict[str, object]
    stage: Stage


class SpeechActivities:
    """Activities bound to process resources; root registers these methods."""

    def __init__(
        self,
        ctx: Context,
        client_factory: Callable[[Any], SpeechModalClient] = SpeechModalClient,
    ) -> None:
        self.ctx = ctx
        self._client_factory = client_factory

    def _client(self, plan: TranscriptionPlan) -> SpeechModalClient:
        frozen = replace(
            self.ctx.settings.transcription,
            speech_modal_app=plan.app,
            modal_environment=plan.environment,
            speech_protocol=plan.protocol,
        )
        return self._client_factory(frozen)

    @activity.defn(name="choose_transcription_plan")
    async def choose_transcription_plan(self, request: TranscribeInput) -> TranscriptionPlan:
        """Freeze provider, deployment, budget, model and source bytes once per new history."""
        settings = self.ctx.settings.transcription
        await _validate_request_source(self.ctx.settings.database_url, request)
        identity_build = "legacy"
        protocol = "temnia-media/4"
        audio_sha: str | None = None
        audio_size: int | None = None
        app = settings.modal_app
        if settings.provider == "modal-checkpointed":
            heartbeat_stop = asyncio.Event()
            heartbeat = asyncio.create_task(
                _periodic_progress(
                    heartbeat_stop,
                    self.ctx.settings.database_url,
                    request,
                    TranscriptStage.planning,
                )
            )
            try:
                identity = await assert_checkpointed_deployment(
                    SpeechModalClient(settings), settings
                )
                identity_build = identity.build
                protocol = identity.protocol
                app = settings.speech_modal_app
                audio_sha, audio_size = await _audio_identity(self.ctx.store, request.audioKey)
            finally:
                heartbeat_stop.set()
                await heartbeat
        return TranscriptionPlan(
            backend=settings.provider,
            app=app,
            environment=settings.modal_environment,
            protocol=protocol,
            build=identity_build,
            budget_micros=(
                settings.speech_budget_micros if settings.provider == "modal-checkpointed" else 0
            ),
            rate_micros_per_hour=(
                settings.speech_rate_micros_per_hour
                if settings.provider == "modal-checkpointed"
                else 0
            ),
            stage_timeout_seconds=settings.speech_stage_timeout_seconds,
            startup_timeout_seconds=settings.speech_startup_timeout_seconds,
            dispatch_limit=settings.speech_dispatch_limit,
            audio_sha256=audio_sha,
            audio_size_bytes=audio_size,
            detector=SILERO_DETECTOR,
            detector_revision=SILERO_REVISION,
            detector_sha256=SILERO_SHA256,
        )

    @activity.defn(name="mark_speech_coverage_progress")
    async def mark_speech_coverage_progress(self, request: TranscribeInput) -> None:
        """Make coverage visible once the concurrent primary GPU stages are done."""
        await report_speech_progress(
            self.ctx.settings.database_url,
            request,
            TranscriptStage.speech_coverage,
        )

    @activity.defn(name="settle_checkpointed_speech_run")
    async def settle_checkpointed_speech_run(
        self,
        request: TranscribeInput,
        outcome: Literal["ready", "failed", "cancelled", "budget_paused"],
    ) -> bool:
        """Persist success/failure after all known provider execution has stopped."""
        return await settle_speech_run(
            self.ctx.settings.database_url,
            request=request,
            temporal_run_id=workflow_run_id(),
            outcome=outcome,
        )

    async def _cached_stage(
        self,
        *,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        operation: ledger.Operation,
        configuration: StageConfig,
        input_checkpoint: ArtifactRef | None,
        identity: ArtifactIdentity,
    ) -> AcceptedSpeechStage | None:
        found = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            identity=identity,
        )
        if found is None:
            return None
        body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=found.id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        checkpoint = validate_checkpoint_semantics(
            body,
            stage=configuration.stage,
            build=plan.build,
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
            configuration=configuration,
            input_sha256=input_checkpoint.sha256 if input_checkpoint else None,
        )
        attempt_id: UUID | None = None
        call_id: str | None = None
        telemetry: StageTelemetry | None = None
        if checkpoint.operation_id == operation.id:
            attempt_id = checkpoint.attempt_id
            call_value = found.metadata.get("callId")
            call_id = str(call_value) if call_value is not None else None
            telemetry_value = found.metadata.get("telemetry")
            telemetry = StageTelemetry.model_validate(telemetry_value)
            usage = _estimate_usage(plan)
            usage["telemetry"] = telemetry.model_dump(mode="json", by_alias=True)
            await ledger.complete_attempt(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=operation.id,
                attempt_id=attempt_id,
                owner_token=f"speech:{run_id}:{operation.semantic_key}",
                result_artifact_id=found.id,
                usage=usage,
                actual_cost_micros=None,
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
        return AcceptedSpeechStage(
            stage=configuration.stage,
            artifact_id=found.id,
            checkpoint=_artifact_ref(found, configuration.stage, checkpoint.configuration_sha256),
            operation_id=operation.id,
            attempt_id=attempt_id,
            call_id=call_id,
            telemetry=telemetry,
            reused=True,
        )

    async def _mark_unknown(
        self,
        lease: AttemptLease,
        message: str,
        telemetry: StageTelemetry | None = None,
    ) -> None:
        usage = dict(lease.usage_base)
        if telemetry is not None:
            usage["telemetry"] = telemetry.model_dump(mode="json", by_alias=True)
        await ledger.fail_attempt(
            self.ctx.settings.database_url,
            scope=lease.request.scope,
            source_id=lease.request.sourceId,
            run_id=lease.run_id,
            operation_id=lease.operation_id,
            attempt_id=lease.attempt_id,
            owner_token=lease.owner,
            outcome_known=False,
            actual_cost_micros=None,
            usage=usage,
            error_code="ProviderOutcomeUnknown",
            error_message=message,
        )

    async def _confirm_cancelled(self, lease: AttemptLease) -> None:
        await ledger.request_cancellation(
            self.ctx.settings.database_url,
            scope=lease.request.scope,
            source_id=lease.request.sourceId,
            run_id=lease.run_id,
            operation_id=lease.operation_id,
            attempt_id=lease.attempt_id,
            owner_token=lease.owner,
        )
        await ledger.confirm_cancellation(
            self.ctx.settings.database_url,
            scope=lease.request.scope,
            source_id=lease.request.sourceId,
            run_id=lease.run_id,
            operation_id=lease.operation_id,
            attempt_id=lease.attempt_id,
            owner_token=lease.owner,
            actual_cost_micros=None,
            usage=lease.usage_base,
        )

    async def _release_or_mark_unknown_on_cancel(self, lease: AttemptLease, message: str) -> None:
        """Release a proven reservation; retain ambiguity after the dispatch fence."""
        try:
            await ledger.release_undispatched(
                self.ctx.settings.database_url,
                scope=lease.request.scope,
                source_id=lease.request.sourceId,
                run_id=lease.run_id,
                operation_id=lease.operation_id,
                attempt_id=lease.attempt_id,
                owner_token=lease.owner,
            )
        except (ledger.OutcomeUnknown, ledger.LostOwnership):
            await self._mark_unknown(lease, message)

    async def _cancel_and_wait(
        self,
        client: SpeechModalClient,
        lease: AttemptLease,
        handle: str,
        *,
        return_finished: bool,
    ) -> SpeechStageResult:
        await ledger.request_cancellation(
            self.ctx.settings.database_url,
            scope=lease.request.scope,
            source_id=lease.request.sourceId,
            run_id=lease.run_id,
            operation_id=lease.operation_id,
            attempt_id=lease.attempt_id,
            owner_token=lease.owner,
        )
        try:
            await client.cancel(handle)
        except Exception as error:
            message = f"speech cancellation request was unreachable: {error}"
            await self._mark_unknown(lease, message)
            raise ApplicationError(
                message,
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            ) from error
        try:
            async with asyncio.timeout(CANCEL_CONFIRM_SECONDS):
                while True:
                    state = await client.status(handle)
                    if isinstance(state, CallCancelled):
                        await ledger.confirm_cancellation(
                            self.ctx.settings.database_url,
                            scope=lease.request.scope,
                            source_id=lease.request.sourceId,
                            run_id=lease.run_id,
                            operation_id=lease.operation_id,
                            attempt_id=lease.attempt_id,
                            owner_token=lease.owner,
                            actual_cost_micros=None,
                            usage=lease.usage_base,
                        )
                        raise ApplicationError(
                            "speech stage cancellation was confirmed",
                            non_retryable=True,
                            type="SpeechCancelled",
                        )
                    if isinstance(state, CallFinished):
                        if return_finished:
                            return state.result
                        message = "speech stage completed while its activity was cancelled"
                        await self._mark_unknown(lease, message)
                        raise ApplicationError(
                            message,
                            non_retryable=True,
                            type="ProviderOutcomeUnknown",
                        )
                    if isinstance(state, (CallUnknown, CallUnreachable)):
                        message = (
                            state.message
                            if isinstance(state, CallUnreachable)
                            else "Modal lost the call while cancellation was pending"
                        )
                        await self._mark_unknown(lease, message)
                        raise ApplicationError(
                            message,
                            non_retryable=True,
                            type="ProviderOutcomeUnknown",
                        )
                    await asyncio.sleep(1)
        except TimeoutError:
            message = "speech cancellation was not confirmed within 30 seconds"
            await self._mark_unknown(lease, message)
            raise ApplicationError(
                message,
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            ) from None

    async def _poll(
        self,
        client: SpeechModalClient,
        lease: AttemptLease,
        handle: str,
        timeout_seconds: float,
    ) -> SpeechStageResult:
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    state = await client.status(handle)
                    if isinstance(state, CallFinished):
                        return state.result
                    if isinstance(state, CallCancelled):
                        await self._confirm_cancelled(lease)
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
                        await self._mark_unknown(lease, message)
                        raise ApplicationError(
                            message, non_retryable=True, type="ProviderOutcomeUnknown"
                        )
                    progress = await client.progress(str(lease.attempt_id))
                    stage = TranscriptStage(
                        {
                            "recognize": "transcribe",
                            "align": "align",
                            "diarize": "diarize",
                        }[lease.stage]
                    )
                    await report_speech_progress(
                        self.ctx.settings.database_url,
                        lease.request,
                        stage,
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
            return await self._cancel_and_wait(
                client,
                lease,
                handle,
                return_finished=True,
            )
        except asyncio.CancelledError:
            with suppress(Exception):
                await asyncio.shield(
                    self._cancel_and_wait(
                        client,
                        lease,
                        handle,
                        return_finished=False,
                    )
                )
            raise

    async def _stage(
        self,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        configuration: StageConfig,
        input_stage: AcceptedSpeechStage | None,
    ) -> AcceptedSpeechStage:
        await report_speech_progress(
            self.ctx.settings.database_url,
            request,
            TranscriptStage(
                {
                    "recognize": "transcribe",
                    "align": "align",
                    "diarize": "diarize",
                }[configuration.stage]
            ),
        )
        if plan.audio_sha256 is None or plan.audio_size_bytes is None:
            raise ValueError("checkpointed plan has no frozen audio identity")
        input_ref = input_stage.checkpoint if input_stage else None
        inputs = _semantic_inputs(request, plan, input_ref)
        config = semantic_stage_config(plan, configuration, cache_scope_run_id=run_id)
        acquired = await ledger.acquire_operation(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            run_id=run_id,
            kind=configuration.stage,
            stage=configuration.stage,
            inputs=inputs,
            config=config,
        )
        identity = ArtifactIdentity(
            kind=SPEECH_ARTIFACT_KIND,
            fingerprint=artifacts.fingerprint_for(
                inputs=inputs, config=config, kind=configuration.stage
            ),
        )
        cached = await self._cached_stage(
            request=request,
            plan=plan,
            run_id=run_id,
            operation=acquired.operation,
            configuration=configuration,
            input_checkpoint=input_ref,
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
        owner = f"speech:{run_id}:{acquired.operation.semantic_key}"
        estimated = _estimated_cost(plan)
        usage_base = _estimate_usage(plan)
        request_hash = hashlib.sha256(
            canonical_json({"config": config, "inputs": inputs})
        ).hexdigest()
        route = {
            "app": plan.app,
            "environment": plan.environment,
            "estimate": usage_base["estimate"],
            "function": configuration.stage,
        }

        async def reserve() -> ledger.Attempt:
            return await ledger.reserve_attempt(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=acquired.operation.id,
                owner_token=owner,
                provider="modal",
                model=configuration.model,
                family="whisperx",
                route=route,
                request_hash=request_hash,
                estimated_cost_micros=estimated,
                dispatch_limit=plan.dispatch_limit,
            )

        try:
            attempt = await reserve()
        except asyncio.CancelledError:
            attempt = await asyncio.shield(reserve())
            await asyncio.shield(
                self._release_or_mark_unknown_on_cancel(
                    AttemptLease(
                        request,
                        run_id,
                        acquired.operation.id,
                        attempt.id,
                        owner,
                        usage_base,
                        configuration.stage,
                    ),
                    "speech activity was cancelled while its reservation state was uncertain",
                )
            )
            raise
        client = self._client(plan)
        if attempt.state == "reserved":
            try:
                await assert_frozen_deployment(
                    client,
                    app=plan.app,
                    protocol=plan.protocol,
                    build=plan.build,
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._release_or_mark_unknown_on_cancel(
                        AttemptLease(
                            request,
                            run_id,
                            acquired.operation.id,
                            attempt.id,
                            owner,
                            usage_base,
                            configuration.stage,
                        ),
                        "speech activity was cancelled during its pre-dispatch build check",
                    )
                )
                raise
            except Exception as error:
                await ledger.release_undispatched(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    run_id=run_id,
                    operation_id=acquired.operation.id,
                    attempt_id=attempt.id,
                    owner_token=owner,
                )
                raise ApplicationError(
                    f"speech deployment changed before {configuration.stage} dispatch: {error}",
                    non_retryable=True,
                    type="SpeechDeploymentChanged",
                ) from error
        try:
            should_spawn = await ledger.mark_dispatched(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner,
                dispatch_limit=plan.dispatch_limit,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._release_or_mark_unknown_on_cancel(
                    AttemptLease(
                        request,
                        run_id,
                        acquired.operation.id,
                        attempt.id,
                        owner,
                        usage_base,
                        configuration.stage,
                    ),
                    "speech activity was cancelled while committing its dispatch fence",
                )
            )
            raise
        if should_spawn:
            attempt = await ledger.reserve_attempt(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=acquired.operation.id,
                owner_token=owner,
                provider="modal",
                model=configuration.model,
                family="whisperx",
                route={
                    "app": plan.app,
                    "environment": plan.environment,
                    "estimate": usage_base["estimate"],
                    "function": configuration.stage,
                },
                request_hash=request_hash,
                estimated_cost_micros=estimated,
                dispatch_limit=plan.dispatch_limit,
            )
        handle = attempt.remote_handle
        if should_spawn:
            job = SpeechStageJob(
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                stage=configuration.stage,
                artifact_prefix=request.artifactPrefix,
                audio_key=request.audioKey,
                audio_sha256=plan.audio_sha256,
                audio_size_bytes=plan.audio_size_bytes,
                duration_ms=request.durationMs,
                checkpoint_key=(
                    f"{request.artifactPrefix}transcript/checkpoints/"
                    f"{configuration.stage}/{attempt.id}.json"
                ),
                configuration=configuration,
                input_checkpoint=input_ref,
            )
            try:
                handle = await client.spawn(configuration.stage, job)
                await ledger.attach_remote_handle(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    operation_id=acquired.operation.id,
                    attempt_id=attempt.id,
                    owner_token=owner,
                    remote_handle=handle,
                )
                await ledger.mark_running(
                    self.ctx.settings.database_url,
                    scope=request.scope,
                    source_id=request.sourceId,
                    operation_id=acquired.operation.id,
                    attempt_id=attempt.id,
                    owner_token=owner,
                )
            except asyncio.CancelledError:
                lease = AttemptLease(
                    request,
                    run_id,
                    acquired.operation.id,
                    attempt.id,
                    owner,
                    usage_base,
                    configuration.stage,
                )
                if handle:
                    with suppress(Exception):
                        await asyncio.shield(
                            self._cancel_and_wait(
                                client,
                                lease,
                                handle,
                                return_finished=False,
                            )
                        )
                else:
                    await asyncio.shield(
                        self._mark_unknown(
                            lease,
                            "speech spawn acknowledgement was lost during activity cancellation",
                        )
                    )
                raise
            except Exception as error:
                await self._mark_unknown(
                    AttemptLease(
                        request,
                        run_id,
                        acquired.operation.id,
                        attempt.id,
                        owner,
                        usage_base,
                        configuration.stage,
                    ),
                    f"spawn acknowledgement was lost: {type(error).__name__}: {error}",
                )
                raise ApplicationError(
                    "speech dispatch outcome is unknown",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                ) from error
        if not handle:
            await self._mark_unknown(
                AttemptLease(
                    request,
                    run_id,
                    acquired.operation.id,
                    attempt.id,
                    owner,
                    usage_base,
                    configuration.stage,
                ),
                "dispatch fence was committed without a recoverable Modal handle",
            )
            raise ApplicationError(
                "speech dispatch has no recoverable handle",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        lease = AttemptLease(
            request,
            run_id,
            acquired.operation.id,
            attempt.id,
            owner,
            usage_base,
            configuration.stage,
        )
        if attempt.dispatched_at is None:
            await self._mark_unknown(
                lease, "dispatched speech attempt has no original dispatch timestamp"
            )
            raise ApplicationError(
                "speech attempt deadline cannot be recovered",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        remaining = remaining_attempt_seconds(attempt.dispatched_at, plan.stage_timeout_seconds)
        result = (
            await self._poll(client, lease, handle, remaining)
            if remaining > 0
            else await self._cancel_and_wait(client, lease, handle, return_finished=True)
        )
        usage = dict(usage_base)
        usage["telemetry"] = result.telemetry.model_dump(mode="json", by_alias=True)
        if not result_identity_matches(
            result,
            plan=plan,
            operation_id=acquired.operation.id,
            attempt_id=attempt.id,
            stage=configuration.stage,
            call_id=handle,
        ):
            await self._mark_unknown(
                lease,
                "Modal returned a result with mismatched protocol, build, or call identity",
            )
            raise ApplicationError(
                "speech result identity is incompatible",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        if result.status == "outcome_unknown":
            if result.error is None:
                await self._mark_unknown(
                    lease,
                    "outcome-unknown speech result omitted its error",
                    result.telemetry,
                )
                raise ApplicationError(
                    "outcome-unknown speech result omitted its error",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            await self._mark_unknown(lease, result.error.message, result.telemetry)
            raise ApplicationError(
                result.error.message,
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        if result.status == "failed":
            if result.error is None:
                await self._mark_unknown(lease, "failed speech result omitted its error")
                raise ApplicationError(
                    "failed speech result omitted its error",
                    non_retryable=True,
                    type="ProviderOutcomeUnknown",
                )
            await ledger.fail_attempt(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                run_id=run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner,
                outcome_known=True,
                actual_cost_micros=None,
                usage=usage,
                error_code=result.error.type,
                error_message=result.error.message,
            )
            if configuration.stage == "recognize" and result.error.retry_class == "resource_oom":
                raise StageOomError(result.error.message)
            raise ApplicationError(
                result.error.message,
                non_retryable=True,
                type="SpeechStageFailure",
            )
        if result.checkpoint is None:
            await self._mark_unknown(
                lease,
                "successful Modal result omitted its checkpoint reference",
            )
            raise ApplicationError(
                "speech result identity is incompatible",
                non_retryable=True,
                type="ProviderOutcomeUnknown",
            )
        checkpoint_store = ObstoreCheckpointStore(self.ctx.store)
        body = await checkpoint_store.read(result.checkpoint.key)
        if body is None:
            raise ApplicationError(
                "speech checkpoint is missing after successful inference",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        accepted_checkpoint = validate_checkpoint_semantics(
            body,
            stage=configuration.stage,
            build=plan.build,
            audio_key=request.audioKey,
            audio_sha256=plan.audio_sha256,
            audio_size_bytes=plan.audio_size_bytes,
            duration_ms=request.durationMs,
            configuration=configuration,
            input_sha256=input_ref.sha256 if input_ref else None,
        )
        artifact = await artifacts.accept_existing_json(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            identity=identity,
            key=result.checkpoint.key,
            sha256=result.checkpoint.sha256,
            size_bytes=result.checkpoint.size_bytes,
            metadata={
                "attemptId": str(attempt.id),
                "callId": handle,
                "operationId": str(acquired.operation.id),
                "stage": configuration.stage,
                "telemetry": usage["telemetry"],
                "estimate": usage_base["estimate"],
                "modelProvenance": accepted_checkpoint.model_provenance.model_dump(
                    mode="json", by_alias=True
                ),
            },
            dependency_ids=([input_stage.artifact_id] if input_stage else []),
        )
        await ledger.complete_attempt(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            run_id=run_id,
            operation_id=acquired.operation.id,
            attempt_id=attempt.id,
            owner_token=owner,
            result_artifact_id=artifact.id,
            usage=usage,
            actual_cost_micros=None,
        )
        return AcceptedSpeechStage(
            stage=configuration.stage,
            artifact_id=artifact.id,
            checkpoint=result.checkpoint,
            operation_id=acquired.operation.id,
            attempt_id=attempt.id,
            call_id=handle,
            telemetry=result.telemetry,
        )

    @activity.defn(name="checkpointed_transcribe")
    async def checkpointed_transcribe(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> CheckpointedTranscription:
        """Run/reuse recognition, alignment and diarization without repeating completed stages."""
        temporal_id = workflow_run_id()
        run_id = await ensure_speech_run(
            self.ctx.settings.database_url,
            request=request,
            plan=plan,
            temporal_run_id=temporal_id,
        )
        recognized: AcceptedSpeechStage | None = None
        last_oom: StageOomError | None = None
        for batch in (16, 8, 4):
            try:
                recognized = await self._stage(
                    request,
                    plan,
                    run_id,
                    plan.recognize.model_copy(update={"batch_size": batch}),
                    None,
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
        recognition_body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=recognized.artifact_id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        recognition_checkpoint = SpeechCheckpointV1.model_validate_json(recognition_body)
        language = str(recognition_checkpoint.payload.get("language") or "und")
        aligned = await self._stage(request, plan, run_id, alignment_config(language), recognized)
        diarized = await self._stage(request, plan, run_id, plan.diarize, aligned)
        return CheckpointedTranscription(
            run_id=run_id,
            recognize=recognized,
            align=aligned,
            diarize=diarized,
        )

    @activity.defn(name="speech_coverage")
    async def speech_coverage(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> CoverageRecord:
        """Publish independent detector evidence; detector failure remains an explicit artifact."""
        await _validate_request_source(self.ctx.settings.database_url, request)
        scratch = Path(tempfile.mkdtemp(prefix="speech-coverage-"))
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            _periodic_progress(
                heartbeat_stop,
                self.ctx.settings.database_url,
                request,
                TranscriptStage.speech_coverage,
                update_visible_stage=False,
            )
        )
        try:
            content: dict[str, object]
            try:
                audio = scratch / "audio.m4a"
                await storage.download(
                    self.ctx.store,
                    request.audioKey,
                    audio,
                    expected_size=plan.audio_size_bytes,
                )
                audio_sha256, audio_size_bytes = await asyncio.to_thread(_file_identity, audio)
                if audio_sha256 != plan.audio_sha256 or audio_size_bytes != plan.audio_size_bytes:
                    raise OSError(
                        "downloaded audio differs from the workflow's frozen source identity"
                    )
                detected = await detect_speech(
                    stream_pcm_windows(audio, ffmpeg=self.ctx.settings.ffmpeg),
                    SileroOnnx.from_path(self.ctx.settings.transcription.speech_vad_model_path),
                )
                content = {
                    "format": "speech-evidence/1",
                    "source": {
                        "audioKey": request.audioKey,
                        "audioSha256": plan.audio_sha256,
                        "audioSizeBytes": plan.audio_size_bytes,
                        "durationMs": request.durationMs,
                    },
                    "status": "measured",
                    **cast("dict[str, object]", asdict(detected)),
                }
            except Exception as error:  # noqa: BLE001
                content = {
                    "format": "speech-evidence/1",
                    "source": {
                        "audioKey": request.audioKey,
                        "audioSha256": plan.audio_sha256,
                        "audioSizeBytes": plan.audio_size_bytes,
                        "durationMs": request.durationMs,
                    },
                    "status": "unknown",
                    "error": f"{type(error).__name__}: {error}",
                    "intervals": [],
                }
            identity = ArtifactIdentity(
                kind=SPEECH_ARTIFACT_KIND,
                fingerprint=artifacts.fingerprint_for(
                    kind="speech_coverage",
                    inputs=content["source"],
                    config={
                        "detector": plan.detector,
                        "revision": plan.detector_revision,
                        "sha256": plan.detector_sha256,
                    },
                ),
            )
            artifact = await artifacts.publish_json(
                self.ctx.settings.database_url,
                scope=request.scope,
                source_id=request.sourceId,
                store=self.ctx.store,
                identity=identity,
                content=content,
                metadata={"format": "speech-evidence/1", "status": content["status"]},
            )
            return CoverageRecord(
                artifact_id=artifact.id,
                error=(str(content.get("error")) if content.get("error") else None),
            )
        finally:
            heartbeat_stop.set()
            await heartbeat
            shutil.rmtree(scratch, ignore_errors=True)

    @activity.defn(name="assemble_checkpointed_transcript")
    async def assemble_checkpointed_transcript(
        self,
        request: TranscribeInput,
        attempt: int,
        plan: TranscriptionPlan,
        stages: CheckpointedTranscription,
        coverage: CoverageRecord,
    ) -> TranscribeRecord:
        """Verify refs, assess coverage, and publish the final existing-normalizer input."""
        final_body = await artifacts.read_artifact_bytes(
            self.ctx.settings.database_url,
            scope=request.scope,
            source_id=request.sourceId,
            store=self.ctx.store,
            artifact_id=stages.diarize.artifact_id,
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        if (
            len(final_body) != stages.diarize.checkpoint.size_bytes
            or hashlib.sha256(final_body).hexdigest() != stages.diarize.checkpoint.sha256
        ):
            raise ApplicationError(
                "accepted diarization artifact differs from its workflow reference",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        final = validate_checkpoint_semantics(
            final_body,
            stage="diarize",
            build=plan.build,
            audio_key=request.audioKey,
            audio_sha256=cast("str", plan.audio_sha256),
            audio_size_bytes=cast("int", plan.audio_size_bytes),
            duration_ms=request.durationMs,
            configuration=plan.diarize,
            input_sha256=stages.align.checkpoint.sha256,
        )
        raw_value = final.payload.get("raw")
        if not isinstance(raw_value, dict):
            raise ApplicationError(
                "diarization checkpoint has no final raw transcript",
                non_retryable=True,
                type="SpeechCheckpointFailure",
            )
        raw = cast("dict[str, object]", raw_value)
        recognition, recognition_warnings = _recognition_spans(raw, request.durationMs)
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
            evidence_map = (
                cast("dict[str, object]", evidence) if isinstance(evidence, dict) else None
            )
            if evidence_map is not None and evidence_map.get("status") == "measured":
                expected_source = {
                    "audioKey": request.audioKey,
                    "audioSha256": plan.audio_sha256,
                    "audioSizeBytes": plan.audio_size_bytes,
                    "durationMs": request.durationMs,
                }
                if evidence_map.get("source") != expected_source:
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
                    (
                        int(cast("int", row["start_ms"])),
                        int(cast("int", row["end_ms"])),
                    )
                    for row in cast("list[dict[str, object]]", evidence_map.get("intervals", []))
                ]
            elif evidence_map is not None:
                detector_error = str(evidence_map.get("error") or "detector evidence is unknown")
        if recognition_warnings:
            detector_error = "; ".join(recognition_warnings)
        assessment = assess_coverage(
            detected,
            recognition,
            request.durationMs,
            detector_error=detector_error,
        )
        key = f"{request.artifactPrefix}transcript/checkpointed-raw-{stages.run_id}.json"
        body = canonical_json(raw)
        await storage.upload_bytes(self.ctx.store, key, body, "application/json")
        stage_metadata = {
            name: value.model_dump(mode="json", by_alias=True)
            for name, value in (
                ("recognize", stages.recognize),
                ("align", stages.align),
                ("diarize", stages.diarize),
            )
        }
        gpu_seconds = sum(
            stage.telemetry.elapsed_seconds if stage.telemetry is not None else 0
            for stage in (stages.recognize, stages.align, stages.diarize)
        )
        return TranscribeRecord(
            raw_key=key,
            language=str(raw.get("language") or "und"),
            gpu_seconds=gpu_seconds,
            gpu="L4",
            attempt=attempt,
            call_id=stages.diarize.call_id,
            metadata={
                "checkpointedSpeech": {
                    "build": plan.build,
                    "coverage": asdict(assessment),
                    "coverageArtifactId": (
                        str(coverage.artifact_id) if coverage.artifact_id else None
                    ),
                    "protocol": plan.protocol,
                    "runId": str(stages.run_id),
                    "stages": stage_metadata,
                }
            },
        )

    def activities(self) -> list[Callable[..., Any]]:
        """The methods root adds to worker registration."""
        return [
            self.choose_transcription_plan,
            self.mark_speech_coverage_progress,
            self.checkpointed_transcribe,
            self.speech_coverage,
            self.assemble_checkpointed_transcript,
            self.settle_checkpointed_speech_run,
        ]
