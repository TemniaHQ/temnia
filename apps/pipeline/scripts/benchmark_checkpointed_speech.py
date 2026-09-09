"""Run the finite checkpointed-speech benchmark in an isolated environment."""

# Reports deliberately retain nullable operational evidence.
# ruff: noqa: BLE001, EM101, PLR0913, PLR0917, T201, TC001, TRY003

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import obstore as obs
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput, TranscriptV1
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.ingest import Context
from temnia_pipeline.modal_build import source_build_id
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import TemporalSettings
from temnia_pipeline.speech.activities_v2 import SpeechActivitiesV2
from temnia_pipeline.speech.assets import SIZE_BYTES, verify_asset
from temnia_pipeline.speech.client import (
    CallFinished,
    SpeechModalClient,
    assert_checkpointed_deployment,
)
from temnia_pipeline.speech.contracts_v2 import SpeechStageResultV2
from temnia_pipeline.speech.qualification import wire_report
from temnia_pipeline.speech_benchmark import (
    CALLS_PER_CASE,
    EXPECTED_CASES,
    LONG_SOURCE_DURATION_MS,
    LONG_SOURCE_SHA256,
    LONG_SOURCE_SIZE,
    REUSED_WITHOUT_ORIGINAL_METRICS,
    SHORT_SOURCE_DURATION_MS,
    SHORT_SOURCE_SHA256,
    SHORT_SOURCE_SIZE,
    BenchmarkCase,
    BenchmarkDeploymentManifest,
    BenchmarkJournal,
    BenchmarkVariant,
    ExperimentLease,
    FrozenSource,
    acknowledge_completed_long_a,
    assert_benchmark_environment,
    assert_case_completed,
    begin_cache_only_recovery,
    benchmark_summary,
    build_journal,
    database_continuation_lease,
    database_experiment_lease,
    database_resume_lease,
    experiment_lease,
    finish_cache_only_recovery,
    finish_case,
    initialize_journal,
    measured_resource_estimate_micros,
    read_journal,
    reserve_case,
    resume_experiment_lease,
    validate_cache_only_recovery_journal,
    validate_completed_long_a_journal,
    validate_completed_long_a_transform,
    write_private_bytes,
    write_private_json,
)
from temnia_pipeline.transcription.activities import Transcribe
from temnia_pipeline.transcription.checkpointed import (
    ParallelCheckpointedTranscription,
    TranscriptionPlan,
)
from temnia_pipeline.transcription.factory import make_transcription
from temnia_pipeline.workflows import TranscribeWorkflow

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

MAX_SNAPSHOT_ROWS = 64
MAX_SNAPSHOT_OBJECTS = 128
MAX_SNAPSHOT_OBJECT_BYTES = 128 * 1024 * 1024
EXPECTED_SPEECH_OPERATIONS = 4
REVIEWED_ORIGINAL_JOURNAL_SHA256 = (
    "8e52c6a204164c314dc9d9fc58841f732431edd84edc5f842b465828318bdc47"
)
REVIEWED_ORIGINAL_LEDGER_SHA256 = "e9148ac523671cb8b055e1395b867f44f9959f9812cea1622b0e6e8a9ac02f4c"
REVIEWED_LONG_A_JOURNAL_SHA256 = "4ac10d0791111c6c461327f8ecc8a30f66a2af0bcdcfab3092c6cb8824befac1"
REVIEWED_LONG_A_REPORT_SHA256 = "77a8a4988cf4f299ea6515d10cc14eee479cd08eb3aca1a22853b91e43accec1"
REVIEWED_LONG_A_DATABASE_SHA256 = "66a1c0a5e60d08882bde61bbccb8670cab9217709d25c2dae91246ed3706848a"
REVIEWED_LONG_A_ARTIFACTS_SHA256 = (
    "90b6677cd63b298116ed92bcc34d036ac0e3d455b9ad7894ac5873d48e2fc327"
)
REVIEWED_LONG_A_SOURCE_SHA256 = "74dd3c9932e1deda731c0b13014a517380156bc8c56200ff21834054c5d6d79a"
REVIEWED_LONG_A_HISTORY_SHA256 = "9908fa12b34f336344c4f4b174978561aa2f16e971f5d4e410741981199bf020"
REVIEWED_LONG_A_EVIDENCE_SHA256 = "b09618a160700148d061d78848927cf6e1d8b236baa0470a2ae74dfa40763790"
PREVIOUS_LONG_A_WORKER_BUILD = "0c64ba401a45f860ae83f0b00911091a7f3177fe2f8387ec352dd3d9d6fb5813"
REVIEWED_LONG_A_WORKFLOW_RUN_ID = "01a08462-8207-7c08-9e8e-27f6e9817a64"
REVIEWED_LONG_A_HARNESS_RUN_ID = "85137441-75f0-5293-a1d8-413a05bcd6d0"
LONG_A_EVIDENCE_FORMAT = "temnia-speech-benchmark-long-a-completed-recovery-evidence/1"
LONG_A_EVIDENCE_FILES = 19
LONG_A_HISTORY_EVENTS = 63
LONG_A_TEMPORAL_MAX_ATTEMPTS = 4
LONG_A_RETRY_ATTEMPT = 2
REVIEWED_CASE_REPORT_SHA256 = {
    "preflight-a": "b2c59fce8cd1cd01e9d5872b46ea1fe9d21cf1499c46e528e67f00e61f168b92",
    "preflight-b": "d253bc00e13dc586797063abad3f308efc44d37162e29d021ff8c68ae05a93fd",
    "preflight-c": "ccef41b4f9035075999a51d7c0d6d69b55b1a98aa5035128185be7ae31e76500",
    "preflight-d": "d3b81a0aceaada969d6cc5e20d39ce86dd5a0f0b2f2a21653bf345fb803b6c16",
    "long-a-1": REVIEWED_LONG_A_REPORT_SHA256,
}


class BenchmarkExecutionError(RuntimeError):
    """One benchmark case failed after its non-recyclable admission."""


class RecoveryNoSpawnSpeechClient(SpeechModalClient):
    """Allow deployment/cache reads while making a recovery dispatch impossible."""

    async def spawn(self, *_args: object, **_kwargs: object) -> str:
        """Refuse every physical provider invocation."""
        raise BenchmarkExecutionError("cache-only recovery reached the provider spawn boundary")


class BenchmarkWorkflowHandle(Protocol):
    """The bounded cancellation surface used from a started workflow."""

    async def cancel(self, *, reason: str = "") -> None:
        """Request workflow cancellation."""

    async def result(self, *, follow_runs: bool = True) -> object:
        """Wait for one exact run's terminal result."""


class V2QualificationSpeechActivities(SpeechActivitiesV2):
    """Lose the preflight activity result only after all GPU facts are accepted."""

    def __init__(
        self, ctx: Context, *, inject_lost_result: bool, refuse_spawn: bool = False
    ) -> None:
        super().__init__(ctx, RecoveryNoSpawnSpeechClient if refuse_spawn else SpeechModalClient)
        self.inject_lost_result = inject_lost_result
        self.injected = False
        self.observed_attempts: list[int] = []
        self.observed_dispatch_counts: list[int] = []
        self.observed_run_ids: list[object] = []

    @activity.defn(name="choose_transcription_plan")
    async def choose_transcription_plan(self, request: TranscribeInput) -> TranscriptionPlan:
        """Freeze the v2 plan with benchmark OOM recovery disabled."""
        plan = await super().choose_transcription_plan(request)
        if plan.protocol != "temnia-speech/2":
            raise ValueError("benchmark worker received a non-v2 transcription plan")
        return plan.model_copy(update={"allow_oom_recovery": False})

    @activity.defn(name="checkpointed_transcribe_v2")
    async def checkpointed_transcribe_v2(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        """Delegate to v2, then inject one retryable lost preflight result."""
        result = await super().checkpointed_transcribe_v2(request, plan)
        self.observed_run_ids.append(result.run_id)
        attempt_number = activity.info().attempt
        self.observed_attempts.append(attempt_number)
        async with db.scoped(self.ctx.settings.database_url, request.scope) as conn:
            run = await (
                await conn.execute(
                    "SELECT dispatch_count FROM harness_run WHERE id = %s",
                    (result.run_id,),
                )
            ).fetchone()
        if run is None:
            raise RuntimeError("benchmark lost its accepted speech run")
        self.observed_dispatch_counts.append(int(run["dispatch_count"]))
        if self.inject_lost_result and attempt_number == 1:
            self.injected = True
            raise ApplicationError(
                "benchmark injected a lost activity result after accepted v2 checkpoints",
                type="BenchmarkLostResult",
            )
        return result


def _variant_context(
    base: Context,
    manifest: BenchmarkDeploymentManifest,
    variant: BenchmarkVariant,
    *,
    budget_micros: int | None = None,
) -> Context:
    transcription_settings = replace(
        base.settings.transcription,
        provider="modal-checkpointed",
        speech_modal_app=variant.app,
        speech_protocol="temnia-speech/2",
        speech_expected_build=manifest.source_build_id,
        speech_budget_micros=(
            variant.case_exposure_micros if budget_micros is None else budget_micros
        ),
        speech_rate_micros_per_hour=variant.rate_micros_per_hour,
        speech_stage_timeout_seconds=variant.resource_profile.stage_timeout_seconds,
        speech_startup_timeout_seconds=variant.resource_profile.startup_timeout_seconds,
        speech_dispatch_limit=3,
        speech_resource_profile=variant.resource_profile,
        speech_model_manifest=manifest.model_manifest,
        speech_execution_topology=variant.execution_topology,
    )
    settings = replace(base.settings, transcription=transcription_settings)
    return replace(
        base,
        settings=settings,
        transcription=make_transcription(settings, base.store),
    )


async def _seed_source(ctx: Context, case: BenchmarkCase, source: FrozenSource) -> None:
    """Create one case-owned source and upload its exact audio bytes."""
    scope = resolve_scope()
    if case.object_prefix != f"org/{scope.organizationId}/source/{case.source_id}/":
        raise ValueError("benchmark case prefix differs from its scoped source id")
    audio_key = f"{case.object_prefix}audio/audio.m4a"
    content_type = storage.content_type_for(source.path)
    async with db.scoped(ctx.settings.database_url, scope) as conn:
        existing = await (
            await conn.execute("SELECT id FROM source WHERE id = %s", (case.source_id,))
        ).fetchone()
        if existing is not None:
            raise ValueError("benchmark source id already exists")
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (scope.organizationId, f"speech-benchmark-{case.source_id}"),
            )
        ).fetchone()
        if project is None:
            raise RuntimeError("benchmark project insert returned no id")
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status, duration_ms, audio_channels, audio_codec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', %s, 1, 'aac')
            """,
            (
                case.source_id,
                scope.organizationId,
                project["id"],
                f"Speech benchmark {case.key}",
                source.path.name,
                content_type,
                source.size_bytes,
                f"{case.object_prefix}master/{source.path.name}",
                source.duration_ms,
            ),
        )
    await storage.upload_file(ctx.store, audio_key, source.path)
    head = await obs.head_async(ctx.store, audio_key)
    if int(head["size"]) != source.size_bytes:
        raise OSError("benchmark audio upload did not preserve the frozen source size")


async def _database_snapshot(
    ctx: Context, case: BenchmarkCase, *, run_id: object | None = None
) -> dict[str, object]:
    scope = resolve_scope()
    async with db.scoped(ctx.settings.database_url, scope) as conn:
        runs = await (
            await conn.execute(
                """
                SELECT id, status, budget_micros, spent_micros, reserved_micros,
                       dispatch_count, config, route_snapshot, workflow_id,
                       workflow_run_id, created_at, updated_at
                  FROM harness_run
                 WHERE source_id = %s AND lane = 'transcription' AND workflow_id = %s
                   AND (%s::uuid IS NULL OR id = %s::uuid)
                 ORDER BY created_at, id
                 LIMIT %s
                """,
                (case.source_id, case.workflow_id, run_id, run_id, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
        if len(runs) > MAX_SNAPSHOT_ROWS:
            raise ValueError("benchmark run snapshot exceeds its bounded row count")
        if len(runs) > 1:
            raise ValueError("benchmark snapshot requires an exact harness run id")
        run = runs[0] if runs else None
        attempts = await (
            await conn.execute(
                """
                SELECT a.id, a.operation_id, a.attempt_number, a.state, a.provider,
                       a.model, a.family, a.route, a.request_hash, a.remote_handle,
                       a.estimated_cost_micros, a.actual_cost_micros, a.cost_status,
                       a.usage, a.dispatched_at, a.finished_at, a.result_artifact_id,
                       o.stage, r.state AS reservation_state,
                       r.amount_micros AS reservation_amount_micros,
                       r.settled_micros AS reservation_settled_micros
                  FROM harness_attempt a
                  JOIN harness_operation o ON o.id = a.operation_id
                  LEFT JOIN harness_reservation r ON r.attempt_id = a.id
                 WHERE a.run_id = %s ORDER BY a.created_at, a.id LIMIT %s
                """,
                (run["id"] if run else None, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
        operations = await (
            await conn.execute(
                """
                SELECT id, semantic_key, kind, stage, status, input_hash, config_hash,
                       result_artifact_id, created_at, updated_at
                  FROM harness_operation WHERE run_id = %s
                 ORDER BY created_at, id LIMIT %s
                """,
                (run["id"] if run else None, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
        artifacts = await (
            await conn.execute(
                """
                SELECT id, kind, fingerprint, storage_key, sha256, size_bytes,
                       metadata, created_at
                  FROM harness_artifact WHERE source_id = %s
                 ORDER BY created_at, id LIMIT %s
                """,
                (case.source_id, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
        dependencies = await (
            await conn.execute(
                """
                SELECT d.artifact_id, d.input_artifact_id
                  FROM harness_artifact_dependency d
                  JOIN harness_artifact a ON a.id = d.artifact_id
                 WHERE a.source_id = %s ORDER BY d.artifact_id, d.input_artifact_id
                 LIMIT %s
                """,
                (case.source_id, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
        transcript = await (
            await conn.execute(
                """
                SELECT id, status, current_revision, language, provider, model,
                       attempts, workflow_id, ready_at, created_at, updated_at
                  FROM transcript WHERE source_id = %s
                """,
                (case.source_id,),
            )
        ).fetchone()
        revisions = await (
            await conn.execute(
                """
                SELECT id, transcript_id, revision, kind, storage_key, size_bytes,
                       word_count, metadata, created_at
                  FROM transcript_revision WHERE transcript_id = %s
                 ORDER BY revision LIMIT %s
                """,
                (transcript["id"] if transcript else None, MAX_SNAPSHOT_ROWS + 1),
            )
        ).fetchall()
    for name, rows in (
        ("attempt", attempts),
        ("operation", operations),
        ("artifact", artifacts),
        ("dependency", dependencies),
        ("transcript revision", revisions),
    ):
        if len(rows) > MAX_SNAPSHOT_ROWS:
            message = f"benchmark {name} snapshot exceeds its bounded row count"
            raise ValueError(message)
    return {
        "run": dict(run) if run else None,
        "attempts": [dict(row) for row in attempts],
        "operations": [dict(row) for row in operations],
        "artifacts": [dict(row) for row in artifacts],
        "dependencies": [dict(row) for row in dependencies],
        "transcript": dict(transcript) if transcript else None,
        "transcriptRevisions": [dict(row) for row in revisions],
    }


async def _harness_run_id_for_workflow_execution(
    ctx: Context, case: BenchmarkCase, temporal_run_id: str
) -> object | None:
    """Resolve one recovery run by the exact Temporal execution identity."""
    async with db.scoped(ctx.settings.database_url, resolve_scope()) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id FROM harness_run
                 WHERE source_id = %s AND workflow_id = %s AND workflow_run_id = %s
                 LIMIT 2
                """,
                (case.source_id, case.workflow_id, temporal_run_id),
            )
        ).fetchall()
    if len(rows) > 1:
        raise BenchmarkExecutionError("Temporal recovery identity matched multiple harness runs")
    return rows[0]["id"] if rows else None


async def _preserve_objects(  # noqa: C901, PLR0912
    ctx: Context, case: BenchmarkCase, case_dir: Path, artifacts: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Preserve every artifact and bounded transcript operational object exactly."""
    expected_prefix = f"org/{resolve_scope().organizationId}/source/{case.source_id}/"
    if case.object_prefix != expected_prefix:
        raise ValueError("benchmark evidence prefix differs from its scoped source")
    inventory = await storage.list_objects(
        ctx.store, case.object_prefix, max_objects=MAX_SNAPSHOT_OBJECTS
    )
    inventory_by_key: dict[str, int] = {}
    for key, size in inventory:
        if not key.startswith(case.object_prefix):
            raise ValueError("benchmark object inventory escaped its case prefix")
        if key in inventory_by_key or size < 0:
            raise ValueError("benchmark object inventory contains an invalid entry")
        inventory_by_key[key] = size
    artifact_by_key: dict[str, list[dict[str, object]]] = {}
    for artifact in artifacts:
        key_value = artifact.get("storage_key")
        if not isinstance(key_value, str) or not key_value.startswith(case.object_prefix):
            raise ValueError("benchmark artifact storage key escaped its case prefix")
        size_value = artifact.get("size_bytes")
        sha256_value = artifact.get("sha256")
        if (
            not isinstance(size_value, int)
            or isinstance(size_value, bool)
            or size_value < 0
            or not isinstance(sha256_value, str)
        ):
            raise ValueError("benchmark artifact has an invalid accepted identity")
        artifact_by_key.setdefault(key_value, []).append(artifact)
    missing_artifact_keys = set(artifact_by_key).difference(inventory_by_key)
    if missing_artifact_keys:
        raise OSError("benchmark accepted artifact is absent from object storage")
    selected_keys = {key for key in inventory_by_key if "/transcript/" in key}.union(
        artifact_by_key
    )
    selected = [(key, inventory_by_key[key]) for key in sorted(selected_keys)]
    if sum(size for _, size in selected) > MAX_SNAPSHOT_OBJECT_BYTES:
        raise ValueError("benchmark preserved evidence exceeds its snapshot byte bound")
    preserved: list[dict[str, object]] = []
    for key, expected_size in selected:
        relative = f"objects/{hashlib.sha256(key.encode()).hexdigest()}.bin"
        sha256, size = await _preserve_streamed_object(
            ctx.store,
            key=key,
            expected_size=expected_size,
            destination=case_dir / relative,
        )
        artifact_rows = artifact_by_key.get(key, [])
        if any(
            artifact.get("sha256") != sha256 or artifact.get("size_bytes") != size
            for artifact in artifact_rows
        ):
            raise OSError("benchmark object differs from its accepted artifact identity")
        preserved.append(
            {
                "key": key,
                "sha256": sha256,
                "sizeBytes": size,
                "localPath": relative,
                "artifactIds": [str(artifact["id"]) for artifact in artifact_rows],
            }
        )
    preserved_by_key = {str(item["key"]): item for item in preserved}
    for key, artifact_rows in artifact_by_key.items():
        preserved_row = preserved_by_key.get(key)
        if preserved_row is None or any(
            preserved_row["sha256"] != artifact["sha256"]
            or preserved_row["sizeBytes"] != artifact["size_bytes"]
            for artifact in artifact_rows
        ):
            raise OSError("benchmark snapshot lacks an exact preserved artifact body")
    return preserved


async def _preserve_streamed_object(
    store: object, *, key: str, expected_size: int, destination: Path
) -> tuple[str, int]:
    """Stream one bounded object to a private file and detect growth or mutation."""
    result = await obs.get_async(store, key)  # pyright: ignore[reportArgumentType]
    stored_size = int(result.meta["size"])
    if stored_size != expected_size:
        raise OSError("benchmark object changed size before evidence preservation")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    temporary_name: str | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, mode="wb") as output:
            temporary_name = output.name
            os.fchmod(output.fileno(), 0o600)
            async for chunk in result.stream(min_chunk_size=8 * 1024 * 1024):
                size += len(chunk)
                if size > expected_size or size > MAX_SNAPSHOT_OBJECT_BYTES:
                    raise OSError("benchmark object grew during evidence preservation")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != expected_size:
            raise OSError("benchmark object changed size during evidence preservation")
        Path(temporary_name).replace(destination)
        destination.chmod(0o600)
        temporary_name = None
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return digest.hexdigest(), size
    finally:
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary_name).unlink()


def _cost_facts(
    attempts: list[dict[str, object]], variant: BenchmarkVariant
) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for attempt in attempts:
        usage = attempt.get("usage")
        usage_values = cast("dict[str, object]", usage) if isinstance(usage, dict) else None
        telemetry = usage_values.get("telemetry") if usage_values is not None else None
        telemetry_values = (
            cast("dict[str, object]", telemetry) if isinstance(telemetry, dict) else None
        )
        unavailable = (
            telemetry_values.get("metricsUnavailable") if telemetry_values is not None else None
        )
        unavailable_values = (
            cast("list[object]", unavailable) if isinstance(unavailable, list) else []
        )
        telemetry_complete = (
            telemetry_values is not None
            and telemetry_values.get("complete") is True
            and REUSED_WITHOUT_ORIGINAL_METRICS not in unavailable_values
        )
        elapsed = (
            telemetry_values.get("elapsedSeconds")
            if telemetry_values is not None and telemetry_complete
            else None
        )
        measured = (
            measured_resource_estimate_micros(variant.resource_profile, float(elapsed))
            if isinstance(elapsed, int | float) and not isinstance(elapsed, bool)
            else None
        )
        actual = (
            attempt.get("actual_cost_micros")
            if attempt.get("cost_status") in {"reported", "reconciled"}
            else None
        )
        facts.append(
            {
                "attemptId": str(attempt["id"]),
                "configuredExposureMicros": attempt["estimated_cost_micros"],
                "measuredResourceEstimateMicros": measured,
                "elapsedSeconds": elapsed,
                "telemetryComplete": telemetry_complete,
                "actualCostMicros": actual,
                "costStatus": attempt["cost_status"],
            }
        )
    return facts


def _transcript_output_facts(body: bytes) -> dict[str, object]:
    """Hash normalized transcript dimensions without exposing transcript text."""
    transcript = TranscriptV1.model_validate_json(body, strict=True)
    words = transcript.words
    return {
        "language": transcript.language,
        "wordCount": len(words),
        "speakerCount": len(transcript.speakers),
        "firstWordStartMs": words[0].startMs if words else None,
        "lastWordEndMs": words[-1].endMs if words else None,
        "tokenSha256": hashlib.sha256(canonical_json([word.text for word in words])).hexdigest(),
        "timingSha256": hashlib.sha256(
            canonical_json([[word.startMs, word.endMs, word.timing] for word in words])
        ).hexdigest(),
        "speakerSha256": hashlib.sha256(
            canonical_json([word.speaker for word in words])
        ).hexdigest(),
    }


async def _cancel_and_drain(
    handle: BenchmarkWorkflowHandle, *, timeout_seconds: float = 120
) -> str:
    """Request Temporal cancellation and keep the worker alive until terminal or bounded unknown."""

    async def cancel_then_wait() -> str:
        try:
            await handle.cancel(reason="benchmark driver stopped locally")
            await handle.result(follow_runs=False)
        except WorkflowFailureError:
            return "terminal"
        except Exception:
            return "outcome_unknown"
        return "terminal"

    task = asyncio.create_task(cancel_then_wait())
    deadline = time.monotonic() + timeout_seconds
    current = asyncio.current_task()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return "outcome_unknown"
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return "outcome_unknown"
        except asyncio.CancelledError:
            if current is not None:
                current.uncancel()


async def _run_case(  # noqa: PLR0915
    base: Context,
    temporal: TemporalSettings,
    manifest: BenchmarkDeploymentManifest,
    variant: BenchmarkVariant,
    case: BenchmarkCase,
    source: FrozenSource,
    output_dir: Path,
    task_queue_prefix: str,
    worker_source_build_id: str,
) -> dict[str, object]:
    ctx = _variant_context(base, manifest, variant)
    await assert_checkpointed_deployment(
        SpeechModalClient(ctx.settings.transcription), ctx.settings.transcription
    )
    await _seed_source(ctx, case, source)
    scope = resolve_scope()
    request = TranscribeInput(
        artifactPrefix=case.object_prefix,
        audioKey=f"{case.object_prefix}audio/audio.m4a",
        durationMs=source.duration_ms,
        scope=scope,
        sourceId=case.source_id,
    )
    client = await Client.connect(
        temporal.address,
        namespace=temporal.namespace,
        data_converter=pydantic_data_converter,
    )
    transcribe = Transcribe(ctx)
    qualifier = V2QualificationSpeechActivities(ctx, inject_lost_result=case.kind == "preflight")
    activities = [
        *transcribe.activities(),
        *qualifier.activities(),
    ]
    task_queue = f"{task_queue_prefix}-{case.key}"
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[TranscribeWorkflow],
        activities=activities,
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "pydantic", "pydantic_core"
            )
        ),
    )
    result: object | None = None
    execution_error: BaseException | None = None
    cancellation_state: str | None = None
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    try:
        async with worker:
            handle = cast(
                "BenchmarkWorkflowHandle",
                await client.start_workflow(
                    TranscribeWorkflow.run,
                    request,
                    id=case.workflow_id,
                    task_queue=task_queue,
                ),
            )
            timeout_seconds = (
                3
                * (
                    variant.resource_profile.stage_timeout_seconds
                    + variant.resource_profile.startup_timeout_seconds
                )
                + 600
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await handle.result(follow_runs=False)
            except (TimeoutError, asyncio.CancelledError):
                cancellation_state = await _cancel_and_drain(handle)
                raise
    except BaseException as error:
        execution_error = error
    finished_at = datetime.now(UTC)
    wall_seconds = time.monotonic() - started_monotonic
    snapshot = await _database_snapshot(ctx, case)
    run = cast("dict[str, object] | None", snapshot["run"])
    attempts = cast("list[dict[str, object]]", snapshot["attempts"])
    case_dir = output_dir / "cases" / case.key
    objects = await _preserve_objects(
        ctx,
        case,
        case_dir,
        cast("list[dict[str, object]]", snapshot["artifacts"]),
    )
    result_body = (
        cast("Any", result).model_dump(mode="json", by_alias=True) if result is not None else None
    )
    result_hash = (
        hashlib.sha256(canonical_json(result_body)).hexdigest() if result_body is not None else None
    )
    revisions = cast("list[dict[str, object]]", snapshot["transcriptRevisions"])
    revision_key = str(revisions[-1]["storage_key"]) if revisions else None
    transcript_hash = next(
        (str(item["sha256"]) for item in objects if item["key"] == revision_key),
        None,
    )
    revision_object = next(
        (item for item in objects if item["key"] == revision_key),
        None,
    )
    revision_path = (
        case_dir / cast("str", revision_object["localPath"])
        if revision_object is not None
        else None
    )
    output_facts = (
        _transcript_output_facts(revision_path.read_bytes()) if revision_path is not None else None
    )
    cost_facts = _cost_facts(attempts, variant)
    elapsed_values = [fact.get("elapsedSeconds") for fact in cost_facts]
    complete_elapsed = all(
        isinstance(elapsed, int | float) and not isinstance(elapsed, bool)
        for elapsed in elapsed_values
    )
    numeric_elapsed = cast("list[int | float]", elapsed_values)
    function_elapsed_seconds = (
        sum(float(elapsed) for elapsed in numeric_elapsed) if complete_elapsed else None
    )
    report = {
        "format": "temnia-speech-benchmark-case/1",
        "case": case.model_dump(mode="json", by_alias=True),
        "variant": variant.model_dump(mode="json", by_alias=True),
        "source": {
            **source.model_dump(mode="json", by_alias=True, exclude={"path"}),
            "uploadedAudioContentType": storage.content_type_for(source.path),
        },
        "deployment": {
            "app": variant.app,
            "protocol": "temnia-speech/2",
            "gpuSourceBuildId": manifest.source_build_id,
            "workerSourceBuildId": worker_source_build_id,
            "modelManifest": manifest.model_manifest.model_dump(mode="json", by_alias=True),
        },
        "workflow": {
            "errorType": type(execution_error).__name__ if execution_error else None,
            "activityAttempts": qualifier.observed_attempts,
            "observedDispatchCounts": qualifier.observed_dispatch_counts,
            "injectedLostResult": qualifier.injected,
            "resultSha256": result_hash,
            "transcriptSha256": transcript_hash,
            "startedAt": started_at.isoformat().replace("+00:00", "Z"),
            "finishedAt": finished_at.isoformat().replace("+00:00", "Z"),
            "wallSeconds": wall_seconds,
            "functionElapsedSeconds": function_elapsed_seconds,
            "cancellationState": cancellation_state,
        },
        **snapshot,
        "objects": objects,
        "costFacts": cost_facts,
        "outputFacts": output_facts,
        "proofLimits": [
            "measured resource estimate is not a provider invoice",
            "actual cost remains null without authoritative call-bound billing evidence",
            "two long repetitions are a screening result",
        ],
        "cleanup": {
            "objectPrefix": case.object_prefix,
            "sourceId": str(case.source_id),
            "databaseCleanupRequired": True,
        },
    }
    write_private_json(case_dir / "report.json", wire_report(report))
    if execution_error is not None:
        message = f"benchmark case {case.key} failed as {type(execution_error).__name__}"
        raise BenchmarkExecutionError(message) from execution_error
    if run is None:
        raise BenchmarkExecutionError("benchmark case produced no harness run")
    assert_case_completed(
        cast("dict[str, object]", report),
        variant=variant,
        require_lost_result=case.kind == "preflight",
    )
    return report


def _original_recovery_facts(  # noqa: C901
    snapshot: dict[str, object], *, variant: BenchmarkVariant, require_failed_transcript: bool
) -> dict[str, object]:
    """Prove the failed run contains exactly the three accepted GPU invocations."""
    run = snapshot.get("run")
    transcript = snapshot.get("transcript")
    attempts = snapshot.get("attempts")
    operations = snapshot.get("operations")
    artifacts = snapshot.get("artifacts")
    dependencies = snapshot.get("dependencies")
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (run, dict),
            (transcript, dict),
            (attempts, list),
            (operations, list),
            (artifacts, list),
            (dependencies, list),
        )
    ):
        raise BenchmarkExecutionError("cache-only recovery lacks complete original ledger evidence")
    run_values = cast("dict[str, object]", run)
    attempt_values = cast("list[dict[str, object]]", attempts)
    operation_values = cast("list[dict[str, object]]", operations)
    artifact_values = cast("list[dict[str, object]]", artifacts)
    dependency_values = cast("list[dict[str, object]]", dependencies)
    transcript_values = cast("dict[str, object]", transcript)
    if (
        run_values.get("status") != "failed"
        or run_values.get("budget_micros") != variant.case_exposure_micros
        or run_values.get("dispatch_count") != CALLS_PER_CASE
        or run_values.get("spent_micros") != 0
        or run_values.get("reserved_micros") != variant.case_exposure_micros
        or (require_failed_transcript and transcript_values.get("status") != "failed")
    ):
        raise BenchmarkExecutionError("original benchmark run is not the exact failed admission")
    if len(attempt_values) != CALLS_PER_CASE or {
        attempt.get("stage") for attempt in attempt_values
    } != {
        "recognize",
        "align",
        "speaker_turns",
    }:
        raise BenchmarkExecutionError("original benchmark run lacks its exact three GPU attempts")
    if any(
        attempt.get("state") != "succeeded"
        or attempt.get("dispatched_at") is None
        or attempt.get("estimated_cost_micros") != variant.reservation_micros
        or attempt.get("actual_cost_micros") is not None
        or attempt.get("cost_status") != "unknown"
        or attempt.get("reservation_state") != "active"
        or attempt.get("reservation_amount_micros") != variant.reservation_micros
        for attempt in attempt_values
    ):
        raise BenchmarkExecutionError("original benchmark GPU attempt evidence is incomplete")
    operation_by_stage = {str(operation.get("stage")): operation for operation in operation_values}
    if len(operation_values) != EXPECTED_SPEECH_OPERATIONS or set(operation_by_stage) != {
        "recognize",
        "align",
        "speaker_turns",
        "assign_speakers",
    }:
        raise BenchmarkExecutionError("original benchmark operation set is not exact")
    gpu_stages = ("recognize", "align", "speaker_turns")
    if any(
        operation_by_stage[stage].get("status") != "succeeded"
        or operation_by_stage[stage].get("result_artifact_id") is None
        for stage in gpu_stages
    ):
        raise BenchmarkExecutionError("original benchmark GPU operations are not terminal")
    checkpoint_ids = {
        str(operation_by_stage[stage]["result_artifact_id"]): stage for stage in gpu_stages
    }
    if len(checkpoint_ids) != CALLS_PER_CASE:
        raise BenchmarkExecutionError("original benchmark GPU result identities are not distinct")
    artifact_by_id = {str(artifact.get("id")): artifact for artifact in artifact_values}
    checkpoint_artifacts = {
        artifact_id: artifact_by_id[artifact_id]
        for artifact_id in checkpoint_ids
        if artifact_id in artifact_by_id
    }
    if set(checkpoint_artifacts) != set(checkpoint_ids):
        raise BenchmarkExecutionError(
            "original benchmark checkpoint artifact identity is incomplete"
        )
    for artifact_id, stage in checkpoint_ids.items():
        artifact = checkpoint_artifacts[artifact_id]
        metadata = artifact.get("metadata")
        operation = operation_by_stage[stage]
        metadata_values = cast("dict[str, object]", metadata) if isinstance(metadata, dict) else {}
        if (
            artifact.get("kind") != "speech_checkpoint"
            or not isinstance(metadata, dict)
            or metadata_values.get("format") != "speech-checkpoint/2"
            or metadata_values.get("stage") != stage
            or str(metadata_values.get("operationId")) != str(operation.get("id"))
        ):
            raise BenchmarkExecutionError(
                "original benchmark checkpoint artifact identity is incomplete"
            )
    recognize_id = next(key for key, stage in checkpoint_ids.items() if stage == "recognize")
    align_id = next(key for key, stage in checkpoint_ids.items() if stage == "align")
    checkpoint_dependencies = {
        (str(value.get("artifact_id")), str(value.get("input_artifact_id")))
        for value in dependency_values
        if str(value.get("artifact_id")) in checkpoint_ids
    }
    if checkpoint_dependencies != {(align_id, recognize_id)}:
        raise BenchmarkExecutionError("original benchmark checkpoint lineage is not exact")
    immutable_ledger = {
        "run": run_values,
        "attempts": attempt_values,
        "operations": operation_values,
        "checkpointArtifacts": [checkpoint_artifacts[key] for key in sorted(checkpoint_artifacts)],
        "checkpointDependencies": sorted(checkpoint_dependencies),
    }
    return {
        "runId": str(run_values["id"]),
        "workflowRunId": str(run_values["workflow_run_id"]),
        "checkpointArtifactIdsByStage": {
            stage: artifact_id for artifact_id, stage in checkpoint_ids.items()
        },
        "immutableLedgerSha256": hashlib.sha256(
            canonical_json(wire_report(immutable_ledger))
        ).hexdigest(),
        "immutableLedger": immutable_ledger,
    }


def _assert_cache_only_recovery(
    snapshot: dict[str, object], *, original: dict[str, object]
) -> None:
    """Prove the continuation reused every GPU result and admitted no invocation."""
    run_value = snapshot.get("run")
    transcript_value = snapshot.get("transcript")
    attempts = cast("list[dict[str, object]]", snapshot.get("attempts"))
    operations = cast("list[dict[str, object]]", snapshot.get("operations"))
    artifacts = cast("list[dict[str, object]]", snapshot.get("artifacts"))
    dependencies = cast("list[dict[str, object]]", snapshot.get("dependencies"))
    if not isinstance(run_value, dict) or not isinstance(transcript_value, dict):
        raise BenchmarkExecutionError("cache-only recovery lacks run or transcript evidence")
    run = cast("dict[str, object]", run_value)
    transcript = cast("dict[str, object]", transcript_value)
    if (
        run.get("status") != "ready"
        or run.get("budget_micros") != 1
        or run.get("dispatch_count") != 0
        or run.get("spent_micros") != 0
        or run.get("reserved_micros") != 0
        or attempts != []
        or transcript.get("status") != "ready"
    ):
        raise BenchmarkExecutionError("cache-only recovery admitted provider exposure")
    by_stage = {str(operation.get("stage")): operation for operation in operations}
    expected_stages = {"recognize", "align", "speaker_turns", "assign_speakers"}
    if (
        len(operations) != EXPECTED_SPEECH_OPERATIONS
        or set(by_stage) != expected_stages
        or any(
            operation.get("status") != "succeeded" or operation.get("result_artifact_id") is None
            for operation in by_stage.values()
        )
    ):
        raise BenchmarkExecutionError("cache-only recovery operation set is incomplete")
    expected_gpu = cast("dict[str, str]", original["checkpointArtifactIdsByStage"])
    if any(
        str(by_stage[stage]["result_artifact_id"]) != expected_gpu[stage] for stage in expected_gpu
    ):
        raise BenchmarkExecutionError("cache-only recovery changed a GPU checkpoint identity")
    artifact_by_id = {str(artifact.get("id")): artifact for artifact in artifacts}
    assignment_id = str(by_stage["assign_speakers"]["result_artifact_id"])
    if artifact_by_id.get(assignment_id, {}).get("kind") != "speech_assignment":
        raise BenchmarkExecutionError("cache-only recovery lacks the typed CPU assignment artifact")
    expected_assignment_dependencies = {
        expected_gpu["align"],
        expected_gpu["speaker_turns"],
    }
    actual_assignment_dependencies = {
        str(value.get("input_artifact_id"))
        for value in dependencies
        if str(value.get("artifact_id")) == assignment_id
    }
    if actual_assignment_dependencies != expected_assignment_dependencies:
        raise BenchmarkExecutionError("cache-only recovery assignment lineage is not exact")


def _validate_permitted_recovering_journal(
    *,
    original: BenchmarkJournal,
    current: BenchmarkJournal,
    worker_source_build_id: str,
    deployment_source_build_id: str,
) -> None:
    """Allow only the already-recorded failed-to-recovering journal transformation."""
    first = original.cases[0]
    expected_cases = tuple(
        case.model_copy(update={"status": "recovering"}) if case.key == first.key else case
        for case in original.cases
    )
    expected = original.model_copy(
        update={
            "initial_worker_source_build_id": original.initial_worker_source_build_id
            or original.worker_source_build_id
            or deployment_source_build_id,
            "worker_source_build_id": worker_source_build_id,
            "cases": expected_cases,
        }
    )
    if canonical_json(current.model_dump(mode="json", by_alias=True)) != canonical_json(
        expected.model_dump(mode="json", by_alias=True)
    ):
        raise ValueError("recovering benchmark journal contains an unreviewed mutation")


async def _assert_one_original_database_run(
    ctx: Context, case: BenchmarkCase, original_run_id: str
) -> None:
    """Refuse continuation if any second transcription harness run was created."""
    async with db.scoped(ctx.settings.database_url, resolve_scope()) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id FROM harness_run
                 WHERE source_id = %s AND lane = 'transcription'
                 ORDER BY created_at, id
                 LIMIT 2
                """,
                (case.source_id,),
            )
        ).fetchall()
    if len(rows) != 1 or str(rows[0]["id"]) != original_run_id:
        raise BenchmarkExecutionError("cache-only recovery has a later database run")


async def _assert_latest_original_failed(
    client: Client, case: BenchmarkCase, expected_run_id: str
) -> dict[str, object]:
    """Prove the latest exact Temporal execution is still the original failure."""
    try:
        async with asyncio.timeout(30):
            description = await client.get_workflow_handle(case.workflow_id).describe()
    except Exception as error:
        raise BenchmarkExecutionError(
            "cache-only recovery could not confirm the latest Temporal execution"
        ) from error
    if (
        description.id != case.workflow_id
        or description.run_id != expected_run_id
        or description.status is not WorkflowExecutionStatus.FAILED
        or description.close_time is None
        or description.workflow_type != "TranscribeWorkflow"
    ):
        raise BenchmarkExecutionError(
            "cache-only recovery found a later or non-failed Temporal execution"
        )
    return {
        "workflowId": description.id,
        "runId": description.run_id,
        "status": description.status.name,
        "workflowType": description.workflow_type,
        "historyLength": description.history_length,
        "closeTime": description.close_time.isoformat().replace("+00:00", "Z"),
    }


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_long_a_evidence(  # noqa: C901
    output_dir: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Verify every immutable preservation file before trusting the reviewed exception."""
    evidence_dir = output_dir.parent / "long-a-1-completed-recovery-evidence"
    manifest_path = evidence_dir / "evidence-manifest.json"
    if _sha256_bytes(manifest_path.read_bytes()) != REVIEWED_LONG_A_EVIDENCE_SHA256:
        raise BenchmarkExecutionError("completed long-A preservation manifest bytes changed")
    manifest_values = cast("dict[str, object]", json.loads(manifest_path.read_bytes()))
    if (
        manifest_values.get("format") != LONG_A_EVIDENCE_FORMAT
        or manifest_values.get("journalSha256") != REVIEWED_LONG_A_JOURNAL_SHA256
        or manifest_values.get("reportSha256") != REVIEWED_LONG_A_REPORT_SHA256
        or manifest_values.get("databaseSnapshotSha256") != REVIEWED_LONG_A_DATABASE_SHA256
        or manifest_values.get("artifactFactsSha256") != REVIEWED_LONG_A_ARTIFACTS_SHA256
        or manifest_values.get("sourceFactsSha256") != REVIEWED_LONG_A_SOURCE_SHA256
        or manifest_values.get("workflowRunId") != REVIEWED_LONG_A_WORKFLOW_RUN_ID
        or manifest_values.get("harnessRunId") != REVIEWED_LONG_A_HARNESS_RUN_ID
        or manifest_values.get("unknownCostsRetained") is not True
    ):
        raise BenchmarkExecutionError("completed long-A preservation manifest changed")
    files = manifest_values.get("files")
    if not isinstance(files, list):
        raise BenchmarkExecutionError("completed long-A preservation file set is incomplete")
    file_values = cast("list[object]", files)
    if len(file_values) != LONG_A_EVIDENCE_FILES:
        raise BenchmarkExecutionError("completed long-A preservation file set is incomplete")
    for raw in file_values:
        if not isinstance(raw, dict):
            raise BenchmarkExecutionError("completed long-A preservation entry is malformed")
        item = cast("dict[str, object]", raw)
        relative = item.get("path")
        size = item.get("sizeBytes")
        digest = item.get("sha256")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise BenchmarkExecutionError("completed long-A preservation path escaped")
        path = evidence_dir / relative
        body = path.read_bytes()
        if size != len(body) or digest != _sha256_bytes(body):
            raise BenchmarkExecutionError("completed long-A preserved file changed")
    report_body = (evidence_dir / "case-report.json").read_bytes()
    database_body = (evidence_dir / "database-snapshot.json").read_bytes()
    if _sha256_bytes(report_body) != REVIEWED_LONG_A_REPORT_SHA256:
        raise BenchmarkExecutionError("completed long-A report changed")
    if _sha256_bytes(database_body) != REVIEWED_LONG_A_DATABASE_SHA256:
        raise BenchmarkExecutionError("completed long-A database snapshot changed")
    return (
        cast("dict[str, object]", json.loads(report_body)),
        cast("dict[str, object]", json.loads(database_body)),
    )


def _validate_long_a_history(history: dict[str, object]) -> dict[str, object]:
    """Recognize only the reviewed complete history with one heartbeat retry."""
    if _sha256_bytes(canonical_json(history)) != REVIEWED_LONG_A_HISTORY_SHA256:
        raise BenchmarkExecutionError("completed long-A Temporal history changed")
    raw_events = history.get("events")
    if not isinstance(raw_events, list):
        raise BenchmarkExecutionError("completed long-A Temporal history is incomplete")
    event_values = cast("list[object]", raw_events)
    if len(event_values) != LONG_A_HISTORY_EVENTS:
        raise BenchmarkExecutionError("completed long-A Temporal history is incomplete")
    events = cast("list[dict[str, object]]", event_values)
    if [event.get("eventId") for event in events] != [str(index) for index in range(1, 64)]:
        raise BenchmarkExecutionError("completed long-A Temporal history event ids are incomplete")
    scheduled = cast("dict[str, object]", events[21].get("activityTaskScheduledEventAttributes"))
    activity_type = cast("dict[str, object]", scheduled.get("activityType"))
    retry = cast("dict[str, object]", scheduled.get("retryPolicy"))
    started = cast("dict[str, object]", events[27].get("activityTaskStartedEventAttributes"))
    failure = cast("dict[str, object]", started.get("lastFailure"))
    timeout = cast("dict[str, object]", failure.get("timeoutFailureInfo"))
    completed = cast("dict[str, object]", events[28].get("activityTaskCompletedEventAttributes"))
    if (
        events[21].get("eventType") != "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
        or activity_type.get("name") != "checkpointed_transcribe_v2"
        or scheduled.get("heartbeatTimeout") != "10s"
        or retry.get("maximumAttempts") != LONG_A_TEMPORAL_MAX_ATTEMPTS
        or events[27].get("eventType") != "EVENT_TYPE_ACTIVITY_TASK_STARTED"
        or started.get("scheduledEventId") != "22"
        or started.get("attempt") != LONG_A_RETRY_ATTEMPT
        or failure.get("message") != "activity Heartbeat timeout"
        or timeout.get("timeoutType") != "TIMEOUT_TYPE_HEARTBEAT"
        or events[28].get("eventType") != "EVENT_TYPE_ACTIVITY_TASK_COMPLETED"
        or completed.get("scheduledEventId") != "22"
        or completed.get("startedEventId") != "28"
        or events[62].get("eventType") != "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    ):
        raise BenchmarkExecutionError("completed long-A heartbeat history proof changed")
    return {
        "canonicalSha256": REVIEWED_LONG_A_HISTORY_SHA256,
        "eventCount": 63,
        "retryCause": "TIMEOUT_TYPE_HEARTBEAT",
        "scheduledEventId": "22",
        "retryStartedEventId": "28",
        "completionEventId": "63",
    }


async def _verify_long_a_objects(
    ctx: Context, case: BenchmarkCase, report: dict[str, object]
) -> list[dict[str, object]]:
    """Re-hash the exact accepted object inventory without writing local or remote state."""
    raw_objects = report.get("objects")
    if not isinstance(raw_objects, list):
        raise BenchmarkExecutionError("completed long-A report object inventory is invalid")
    object_values = cast("list[object]", raw_objects)
    if not 1 <= len(object_values) <= MAX_SNAPSHOT_OBJECTS:
        raise BenchmarkExecutionError("completed long-A report object inventory is invalid")
    verified: list[dict[str, object]] = []
    total = 0
    for raw in object_values:
        if not isinstance(raw, dict):
            raise BenchmarkExecutionError("completed long-A object entry is malformed")
        item = cast("dict[str, object]", raw)
        key = item.get("key")
        expected_sha = item.get("sha256")
        expected_size = item.get("sizeBytes")
        if (
            not isinstance(key, str)
            or not key.startswith(case.object_prefix)
            or not isinstance(expected_sha, str)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise BenchmarkExecutionError("completed long-A object identity is invalid")
        total += expected_size
        if total > MAX_SNAPSHOT_OBJECT_BYTES:
            raise BenchmarkExecutionError("completed long-A object proof exceeds its byte bound")
        result = await obs.get_async(ctx.store, key)
        digest = hashlib.sha256()
        size = 0
        async for chunk in result.stream(min_chunk_size=1024 * 1024):
            size += len(chunk)
            if size > expected_size:
                raise BenchmarkExecutionError("completed long-A object grew during proof")
            digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha:
            raise BenchmarkExecutionError("completed long-A object bytes changed")
        verified.append({"key": key, "sha256": expected_sha, "sizeBytes": expected_size})
    return verified


async def _verify_long_a_source(
    ctx: Context, case: BenchmarkCase, source: FrozenSource
) -> dict[str, object]:
    """Re-read the scoped source row that binds the frozen long input."""
    async with db.scoped(ctx.settings.database_url, resolve_scope()) as conn:
        row = await (
            await conn.execute(
                """
                SELECT id, organization_id, title, original_filename, content_type,
                       size_bytes, master_key, status, duration_ms, audio_channels, audio_codec,
                       deletion_requested_at
                  FROM source WHERE id = %s
                """,
                (case.source_id,),
            )
        ).fetchone()
    expected = {
        "id": case.source_id,
        "organization_id": resolve_scope().organizationId,
        "title": f"Speech benchmark {case.key}",
        "original_filename": source.path.name,
        "content_type": storage.content_type_for(source.path),
        "size_bytes": source.size_bytes,
        "master_key": f"{case.object_prefix}master/{source.path.name}",
        "status": "ready",
        "duration_ms": source.duration_ms,
        "audio_channels": 1,
        "audio_codec": "aac",
        "deletion_requested_at": None,
    }
    if row is None or dict(row) != expected:
        raise BenchmarkExecutionError("completed long-A source identity changed")
    return cast("dict[str, object]", wire_report(expected))


async def _verify_long_a_modal_calls(
    ctx: Context,
    manifest: BenchmarkDeploymentManifest,
    variant: BenchmarkVariant,
    snapshot: dict[str, object],
) -> list[dict[str, object]]:
    """Confirm all three reviewed handles are terminal successes without dispatching."""
    attempts = cast("list[dict[str, object]]", snapshot.get("attempts"))
    artifacts = cast("list[dict[str, object]]", snapshot.get("artifacts"))
    artifact_by_id = {str(item.get("id")): item for item in artifacts}
    client = SpeechModalClient(_variant_context(ctx, manifest, variant).settings.transcription)
    facts: list[dict[str, object]] = []
    for attempt in attempts:
        handle = attempt.get("remote_handle")
        artifact = artifact_by_id.get(str(attempt.get("result_artifact_id")))
        if not isinstance(handle, str) or artifact is None:
            raise BenchmarkExecutionError("completed long-A call identity is incomplete")
        async with asyncio.timeout(30):
            state = await client.status(handle)
        if not isinstance(state, CallFinished) or not isinstance(state.result, SpeechStageResultV2):
            raise BenchmarkExecutionError("completed long-A provider call is not terminal success")
        result = state.result
        checkpoint = result.checkpoint
        if (
            result.status != "ok"
            or result.checkpoint_reused
            or result.build != manifest.source_build_id
            or str(result.attempt_id) != str(attempt.get("id"))
            or str(result.operation_id) != str(attempt.get("operation_id"))
            or result.stage != attempt.get("stage")
            or result.modal_call_id != handle
            or result.resource_profile != variant.resource_profile
            or result.model_manifest != manifest.model_manifest
            or result.execution_topology != variant.execution_topology
            or not result.telemetry.complete
            or checkpoint is None
            or checkpoint.key != artifact.get("storage_key")
            or checkpoint.sha256 != artifact.get("sha256")
            or checkpoint.size_bytes != artifact.get("size_bytes")
        ):
            raise BenchmarkExecutionError("completed long-A provider result identity changed")
        facts.append({"attemptId": str(result.attempt_id), "callId": handle, "stage": result.stage})
    if len(facts) != CALLS_PER_CASE:
        raise BenchmarkExecutionError("completed long-A provider proof is incomplete")
    return facts


async def _prove_completed_long_a(
    *,
    base: Context,
    temporal: TemporalSettings,
    manifest: BenchmarkDeploymentManifest,
    case: BenchmarkCase,
    variant: BenchmarkVariant,
    source: FrozenSource,
    output_dir: Path,
) -> dict[str, object]:
    """Revalidate the exact ready result across preserved and live authorities."""
    report, preserved_snapshot = _validate_long_a_evidence(output_dir)
    live_snapshot = cast("dict[str, object]", wire_report(await _database_snapshot(base, case)))
    live_body = canonical_json(live_snapshot) + b"\n"
    if (
        _sha256_bytes(live_body) != REVIEWED_LONG_A_DATABASE_SHA256
        or live_snapshot != preserved_snapshot
    ):
        raise BenchmarkExecutionError("completed long-A live ledger changed")
    assert_case_completed(report, variant=variant, require_lost_result=True)
    async with asyncio.timeout(30):
        objects = await _verify_long_a_objects(base, case, report)
        source_facts = await _verify_long_a_source(base, case, source)
    try:
        async with asyncio.timeout(30):
            client = await Client.connect(
                temporal.address,
                namespace=temporal.namespace,
                data_converter=pydantic_data_converter,
            )
            latest = await client.get_workflow_handle(case.workflow_id).describe()
            exact = client.get_workflow_handle(
                case.workflow_id, run_id=REVIEWED_LONG_A_WORKFLOW_RUN_ID
            )
            history = await exact.fetch_history()
    except BenchmarkExecutionError:
        raise
    except Exception as error:
        raise BenchmarkExecutionError("completed long-A Temporal proof is unavailable") from error
    if (
        latest.run_id != REVIEWED_LONG_A_WORKFLOW_RUN_ID
        or latest.status is not WorkflowExecutionStatus.COMPLETED
        or latest.workflow_type != "TranscribeWorkflow"
    ):
        raise BenchmarkExecutionError("completed long-A is not the latest Temporal run")
    history_facts = _validate_long_a_history(cast("dict[str, object]", history.to_json_dict()))
    modal = await _verify_long_a_modal_calls(base, manifest, variant, live_snapshot)
    return {
        "databaseSnapshotSha256": REVIEWED_LONG_A_DATABASE_SHA256,
        "reportSha256": REVIEWED_LONG_A_REPORT_SHA256,
        "history": history_facts,
        "objects": objects,
        "source": source_facts,
        "modalCalls": modal,
        "harnessRunId": REVIEWED_LONG_A_HARNESS_RUN_ID,
        "workflowRunId": REVIEWED_LONG_A_WORKFLOW_RUN_ID,
    }


def _summary_from_report(
    case: BenchmarkCase, report: dict[str, object], *, contaminated_wall: bool = False
) -> dict[str, object]:
    workflow = cast("dict[str, object]", report.get("workflow"))
    return {
        "case": case.key,
        "variant": case.variant_id,
        "kind": case.kind,
        "block": case.block,
        "resultSha256": workflow.get("resultSha256"),
        "transcriptSha256": workflow.get("transcriptSha256"),
        "wallSeconds": None if contaminated_wall else workflow.get("wallSeconds"),
        "wallTimingStatus": ("activity_retry_contaminated" if contaminated_wall else "usable"),
        "functionElapsedSeconds": workflow.get("functionElapsedSeconds"),
        "outputFacts": report.get("outputFacts"),
        "costFacts": report.get("costFacts"),
    }


def _summary_from_preflight_recovery(
    case: BenchmarkCase, variant: BenchmarkVariant, receipt: dict[str, object]
) -> dict[str, object]:
    recovery = cast("dict[str, object]", receipt.get("recovery"))
    original = cast("dict[str, object]", receipt.get("original"))
    immutable = cast("dict[str, object]", original.get("immutableLedger"))
    attempts = cast("list[dict[str, object]]", immutable.get("attempts"))
    revisions = cast("list[dict[str, object]]", recovery.get("transcriptRevisions"))
    objects = cast("list[dict[str, object]]", recovery.get("objects"))
    revision_key = str(revisions[-1].get("storage_key")) if revisions else ""
    transcript_sha = next(
        (item.get("sha256") for item in objects if item.get("key") == revision_key), None
    )
    elapsed = [
        cast(
            "dict[str, object]",
            cast("dict[str, object]", item.get("usage")).get("telemetry"),
        ).get("elapsedSeconds")
        for item in attempts
    ]
    if transcript_sha is None or not all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in elapsed
    ):
        raise BenchmarkExecutionError("recovered preflight summary evidence is incomplete")
    return {
        "case": case.key,
        "variant": case.variant_id,
        "kind": case.kind,
        "block": case.block,
        "resultSha256": recovery.get("resultSha256"),
        "transcriptSha256": transcript_sha,
        "wallSeconds": recovery.get("wallSeconds"),
        "wallTimingStatus": "usable",
        "functionElapsedSeconds": sum(float(cast("int | float", value)) for value in elapsed),
        "outputFacts": recovery.get("outputFacts"),
        "costFacts": _cost_facts(attempts, variant),
    }


def _load_completed_summaries(
    output_dir: Path,
    journal: BenchmarkJournal,
    manifest: BenchmarkDeploymentManifest,
) -> list[dict[str, object]]:
    """Reconstruct every earlier result from its immutable private receipt."""
    variants = {variant.id: variant for variant in manifest.variants}
    summaries: list[dict[str, object]] = []
    for case in journal.cases:
        if case.status != "completed":
            continue
        if case.key == "preflight-a":
            path = output_dir / "cases" / case.key / "recovery" / "continuation-receipt.json"
            body = path.read_bytes()
            receipt = cast("dict[str, object]", json.loads(body))
            if (
                _sha256_bytes(body) != case.recovery_receipt_sha256
                or _sha256_bytes(body) != REVIEWED_CASE_REPORT_SHA256[case.key]
                or receipt.get("format") != "temnia-speech-benchmark-cache-recovery/1"
            ):
                raise BenchmarkExecutionError("recovered preflight receipt format changed")
            summaries.append(
                _summary_from_preflight_recovery(case, variants[case.variant_id], receipt)
            )
            continue
        report_path = output_dir / "cases" / case.key / "report.json"
        report_body = report_path.read_bytes()
        if _sha256_bytes(report_body) != REVIEWED_CASE_REPORT_SHA256.get(case.key):
            raise BenchmarkExecutionError("completed benchmark case report changed")
        report = cast("dict[str, object]", json.loads(report_body))
        report_case = cast("dict[str, object]", report.get("case"))
        if (
            report.get("format") != "temnia-speech-benchmark-case/1"
            or report_case.get("key") != case.key
            or report_case.get("sourceId") != str(case.source_id)
            or report_case.get("workflowId") != case.workflow_id
            or cast("dict[str, object]", report.get("variant")).get("id") != case.variant_id
        ):
            raise BenchmarkExecutionError("completed benchmark report identity changed")
        assert_case_completed(
            report,
            variant=variants[case.variant_id],
            require_lost_result=case.kind == "preflight" or case.key == "long-a-1",
        )
        summaries.append(
            _summary_from_report(case, report, contaminated_wall=case.key == "long-a-1")
        )
    return summaries


def _continuation_paths(output_dir: Path) -> tuple[Path, Path]:
    root = output_dir / "cases" / "long-a-1" / "completed-continuation"
    return root / "intent.json", root / "receipt.json"


def _validate_continuation_receipt(
    *,
    output_dir: Path,
    original: BenchmarkJournal,
    current: BenchmarkJournal,
    worker_source_build_id: str,
) -> dict[str, object]:
    intent_path, receipt_path = _continuation_paths(output_dir)
    if not intent_path.exists() or not receipt_path.exists():
        raise BenchmarkExecutionError("completed long-A continuation receipt is incomplete")
    intent = cast("dict[str, object]", json.loads(intent_path.read_bytes()))
    receipt = cast("dict[str, object]", json.loads(receipt_path.read_bytes()))
    validate_completed_long_a_transform(
        original=original,
        current=current,
        worker_source_build_id=worker_source_build_id,
    )
    current_sha = _sha256_bytes(
        canonical_json(current.model_dump(mode="json", by_alias=True)) + b"\n"
    )
    if (
        intent.get("format") != "temnia-speech-benchmark-completed-long-a-intent/1"
        or intent.get("journalSha256") != REVIEWED_LONG_A_JOURNAL_SHA256
        or intent.get("evidenceManifestSha256") != REVIEWED_LONG_A_EVIDENCE_SHA256
        or receipt.get("format") != "temnia-speech-benchmark-completed-long-a-receipt/1"
        or receipt.get("intentSha256") != _sha256_bytes(intent_path.read_bytes())
        or receipt.get("journalSha256Before") != REVIEWED_LONG_A_JOURNAL_SHA256
        or receipt.get("journalSha256After") != current_sha
        or receipt.get("workerSourceBuildId") != worker_source_build_id
        or receipt.get("noWorkflowOrGpuStarted") is not True
    ):
        raise BenchmarkExecutionError("completed long-A continuation receipt changed")
    return receipt


async def _acknowledge_completed_long_a_case(
    *,
    base: Context,
    temporal: TemporalSettings,
    manifest: BenchmarkDeploymentManifest,
    journal_path: Path,
    journal: BenchmarkJournal,
    output_dir: Path,
    source: FrozenSource,
    worker_source_build_id: str,
    lease: ExperimentLease,
) -> BenchmarkJournal:
    """Prove and accept only the reviewed completed long-A execution."""
    case = journal.cases[4]
    variant = manifest.variants[0]
    proof = await _prove_completed_long_a(
        base=base,
        temporal=temporal,
        manifest=manifest,
        case=case,
        variant=variant,
        source=source,
        output_dir=output_dir,
    )
    intent_path, receipt_path = _continuation_paths(output_dir)
    if intent_path.exists() or receipt_path.exists():
        raise BenchmarkExecutionError("completed long-A continuation already has durable intent")
    intent = wire_report(
        {
            "format": "temnia-speech-benchmark-completed-long-a-intent/1",
            "case": case.model_dump(mode="json", by_alias=True),
            "journalSha256": REVIEWED_LONG_A_JOURNAL_SHA256,
            "evidenceManifestSha256": REVIEWED_LONG_A_EVIDENCE_SHA256,
            "reportSha256": REVIEWED_LONG_A_REPORT_SHA256,
            "databaseSnapshotSha256": REVIEWED_LONG_A_DATABASE_SHA256,
            "temporalHistorySha256": REVIEWED_LONG_A_HISTORY_SHA256,
            "previousWorkerSourceBuildId": PREVIOUS_LONG_A_WORKER_BUILD,
            "workerSourceBuildId": worker_source_build_id,
            "gpuSourceBuildId": manifest.source_build_id,
            "driverSha256": _sha256_bytes(Path(__file__).read_bytes()),
            "proof": proof,
            "noWorkflowOrGpuStartAuthorized": True,
        }
    )
    write_private_bytes(intent_path, canonical_json(intent) + b"\n", refuse_existing=True)
    updated = acknowledge_completed_long_a(
        journal_path,
        expected_journal_sha256=REVIEWED_LONG_A_JOURNAL_SHA256,
        previous_worker_source_build_id=PREVIOUS_LONG_A_WORKER_BUILD,
        worker_source_build_id=worker_source_build_id,
        lease=lease,
    )
    receipt = wire_report(
        {
            "format": "temnia-speech-benchmark-completed-long-a-receipt/1",
            "intentSha256": _sha256_bytes(intent_path.read_bytes()),
            "journalSha256Before": REVIEWED_LONG_A_JOURNAL_SHA256,
            "journalSha256After": _sha256_bytes(journal_path.read_bytes()),
            "workerSourceBuildId": worker_source_build_id,
            "proof": proof,
            "noWorkflowOrGpuStarted": True,
            "wallTimingStatus": "activity_retry_contaminated",
        }
    )
    write_private_bytes(receipt_path, canonical_json(receipt) + b"\n", refuse_existing=True)
    return updated


async def _acknowledge_unstarted_recovery(
    *,
    base: Context,
    temporal: TemporalSettings,
    case: BenchmarkCase,
    variant: BenchmarkVariant,
    output_dir: Path,
    current_journal_sha256: str,
    original_journal_sha256: str,
    worker_source_build_id: str,
) -> tuple[dict[str, object], str]:
    """Persist an exact receipt that the earlier continuation never started."""
    snapshot = await _database_snapshot(base, case)
    original = _original_recovery_facts(snapshot, variant=variant, require_failed_transcript=True)
    if original["immutableLedgerSha256"] != REVIEWED_ORIGINAL_LEDGER_SHA256:
        raise BenchmarkExecutionError("original benchmark GPU/accounting ledger changed")
    await _assert_one_original_database_run(base, case, cast("str", original["runId"]))
    try:
        async with asyncio.timeout(30):
            client = await Client.connect(
                temporal.address,
                namespace=temporal.namespace,
                data_converter=pydantic_data_converter,
            )
    except Exception as error:
        raise BenchmarkExecutionError(
            "cache-only recovery could not connect for Temporal proof"
        ) from error
    temporal_facts = await _assert_latest_original_failed(
        client, case, cast("str", original["workflowRunId"])
    )
    recovery_dir = output_dir / "cases" / case.key / "recovery"
    if any(
        (recovery_dir / name).exists()
        for name in (
            "continuation-receipt.json",
            "recovery-attempt.json",
            "recovery-start-intent.json",
        )
    ):
        raise BenchmarkExecutionError("cache-only recovery already has an execution receipt")
    receipt = wire_report(
        {
            "format": "temnia-speech-benchmark-unstarted-recovery/1",
            "case": case.model_dump(mode="json", by_alias=True),
            "currentJournalSha256": current_journal_sha256,
            "originalJournalSha256": original_journal_sha256,
            "workerSourceBuildId": worker_source_build_id,
            "driverSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "original": original,
            "latestTemporalExecution": temporal_facts,
            "noRecoveryExecutionStarted": True,
        }
    )
    body = canonical_json(receipt) + b"\n"
    path = recovery_dir / "unstarted-recovery-acknowledgment.json"
    if path.exists():
        if path.read_bytes() != body:
            raise BenchmarkExecutionError("unstarted recovery acknowledgment changed")
    else:
        write_private_bytes(path, body, refuse_existing=True)
    return original, cast("str", original["workflowRunId"])


async def _recover_failed_case(  # noqa: C901, PLR0915
    *,
    base: Context,
    temporal: TemporalSettings,
    manifest: BenchmarkDeploymentManifest,
    variant: BenchmarkVariant,
    case: BenchmarkCase,
    source: FrozenSource,
    output_dir: Path,
    task_queue_prefix: str,
    worker_source_build_id: str,
    current_journal_sha256: str,
    original_journal_sha256: str,
    expected_original_ledger_sha256: str,
) -> tuple[dict[str, object], UUID, str]:
    """Complete only CPU assignment from the original immutable checkpoint set."""
    original_snapshot = await _database_snapshot(base, case)
    original = _original_recovery_facts(
        original_snapshot, variant=variant, require_failed_transcript=True
    )
    if original["immutableLedgerSha256"] != expected_original_ledger_sha256:
        raise BenchmarkExecutionError("original benchmark GPU/accounting ledger changed")
    original_run_id = original["runId"]
    original_dir = output_dir / "cases" / case.key / "recovery" / "original"
    original_objects = await _preserve_objects(
        base,
        case,
        original_dir,
        cast("list[dict[str, object]]", original_snapshot["artifacts"]),
    )
    ctx = _variant_context(base, manifest, variant, budget_micros=1)
    await assert_checkpointed_deployment(
        SpeechModalClient(ctx.settings.transcription), ctx.settings.transcription
    )
    scope = resolve_scope()
    request = TranscribeInput(
        artifactPrefix=case.object_prefix,
        audioKey=f"{case.object_prefix}audio/audio.m4a",
        durationMs=source.duration_ms,
        scope=scope,
        sourceId=case.source_id,
    )
    client = await Client.connect(
        temporal.address,
        namespace=temporal.namespace,
        data_converter=pydantic_data_converter,
    )
    transcribe = Transcribe(ctx)
    qualifier = V2QualificationSpeechActivities(ctx, inject_lost_result=False, refuse_spawn=True)
    task_queue = f"{task_queue_prefix}-{case.key}-cache-recovery"
    result: object | None = None
    execution_error: BaseException | None = None
    temporal_run_id: str | None = None
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[TranscribeWorkflow],
        activities=[*transcribe.activities(), *qualifier.activities()],
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "pydantic", "pydantic_core"
            )
        ),
    )
    try:
        async with worker:
            latest_original = _original_recovery_facts(
                await _database_snapshot(base, case, run_id=original_run_id),
                variant=variant,
                require_failed_transcript=True,
            )
            if latest_original["immutableLedgerSha256"] != expected_original_ledger_sha256:
                raise BenchmarkExecutionError(  # noqa: TRY301
                    "original benchmark GPU/accounting ledger changed before recovery start"
                )
            await _assert_latest_original_failed(
                client, case, cast("str", original["workflowRunId"])
            )
            start_intent = wire_report(
                {
                    "format": "temnia-speech-benchmark-cache-recovery-start-intent/1",
                    "case": case.model_dump(mode="json", by_alias=True),
                    "workflowId": case.workflow_id,
                    "originalWorkflowRunId": original["workflowRunId"],
                    "sourceId": str(case.source_id),
                    "objectPrefix": case.object_prefix,
                    "taskQueue": task_queue,
                    "requestSha256": hashlib.sha256(
                        canonical_json(request.model_dump(mode="json", by_alias=True))
                    ).hexdigest(),
                    "currentJournalSha256": current_journal_sha256,
                    "originalJournalSha256": original_journal_sha256,
                    "originalImmutableLedgerSha256": expected_original_ledger_sha256,
                    "gpuSourceBuildId": manifest.source_build_id,
                    "workerSourceBuildId": worker_source_build_id,
                    "driverSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            write_private_bytes(
                output_dir / "cases" / case.key / "recovery" / "recovery-start-intent.json",
                canonical_json(start_intent) + b"\n",
                refuse_existing=True,
            )
            handle = cast(
                "BenchmarkWorkflowHandle",
                await client.start_workflow(
                    TranscribeWorkflow.run,
                    request,
                    id=case.workflow_id,
                    task_queue=task_queue,
                ),
            )
            temporal_run_id = cast("str | None", cast("Any", handle).first_execution_run_id)
            try:
                async with asyncio.timeout(600):
                    result = await handle.result(follow_runs=False)
            except (TimeoutError, asyncio.CancelledError):
                await _cancel_and_drain(handle)
                raise
    except BaseException as error:
        execution_error = error
    case_dir = output_dir / "cases" / case.key / "recovery"
    recovery_run_id: object | None = None
    recovery_snapshot: dict[str, object] = {
        "run": None,
        "attempts": [],
        "operations": [],
        "artifacts": cast("list[dict[str, object]]", original_snapshot["artifacts"]),
        "dependencies": cast("list[dict[str, object]]", original_snapshot["dependencies"]),
        "transcript": original_snapshot["transcript"],
        "transcriptRevisions": original_snapshot["transcriptRevisions"],
    }
    recovery_objects: list[dict[str, object]] = []
    result_body: dict[str, object] | None = None
    revision_object: dict[str, object] | None = None
    output_facts: dict[str, object] | None = None
    reconciliation_error: BaseException | None = None
    try:
        recovery_run_id = (
            qualifier.observed_run_ids[-1]
            if qualifier.observed_run_ids
            else (
                await _harness_run_id_for_workflow_execution(base, case, temporal_run_id)
                if temporal_run_id is not None
                else None
            )
        )
        if recovery_run_id is not None:
            recovery_snapshot = await _database_snapshot(base, case, run_id=recovery_run_id)
        original_after = await _database_snapshot(base, case, run_id=original_run_id)
        original_after_facts = _original_recovery_facts(
            original_after, variant=variant, require_failed_transcript=False
        )
        if original_after_facts["immutableLedgerSha256"] != original["immutableLedgerSha256"]:
            raise BenchmarkExecutionError(  # noqa: TRY301
                "cache-only recovery mutated the original run accounting"
            )
        recovery_objects = await _preserve_objects(
            base,
            case,
            case_dir / "completed",
            cast("list[dict[str, object]]", recovery_snapshot["artifacts"]),
        )
        if execution_error is None and recovery_run_id is not None and result is not None:
            _assert_cache_only_recovery(recovery_snapshot, original=original)
            result_body = cast(
                "dict[str, object]",
                cast("Any", result).model_dump(mode="json", by_alias=True),
            )
            revisions = cast("list[dict[str, object]]", recovery_snapshot["transcriptRevisions"])
            revision_key = str(revisions[-1]["storage_key"]) if revisions else None
            revision_object = next(
                (item for item in recovery_objects if item["key"] == revision_key),
                None,
            )
            if revision_object is None:
                raise BenchmarkExecutionError(  # noqa: TRY301
                    "cache-only recovery did not preserve its transcript body"
                )
            revision_path = case_dir / "completed" / cast("str", revision_object["localPath"])
            output_facts = _transcript_output_facts(revision_path.read_bytes())
    except BaseException as error:
        reconciliation_error = error
    final_error = execution_error or reconciliation_error
    if final_error is None and (
        recovery_run_id is None
        or result_body is None
        or revision_object is None
        or output_facts is None
    ):
        final_error = BenchmarkExecutionError("cache-only recovery did not finish ready")
    recovery_run = recovery_snapshot.get("run")
    recovery_run_values = (
        cast("dict[str, object]", recovery_run) if isinstance(recovery_run, dict) else None
    )
    no_additional_exposure = (
        recovery_run_values is not None
        and recovery_run_values.get("dispatch_count") == 0
        and recovery_snapshot.get("attempts") == []
    )
    failure_report = wire_report(
        {
            "format": "temnia-speech-benchmark-cache-recovery-attempt/1",
            "case": case.model_dump(mode="json", by_alias=True),
            "temporalRunId": temporal_run_id,
            "recoveryRunId": str(recovery_run_id) if recovery_run_id is not None else None,
            "errorType": type(final_error).__name__ if final_error else None,
            "gpuSourceBuildId": manifest.source_build_id,
            "workerSourceBuildId": worker_source_build_id,
            "originalJournalSha256": original_journal_sha256,
            "originalImmutableLedgerSha256": original["immutableLedgerSha256"],
            "original": original,
            "originalObjects": original_objects,
            "recovery": recovery_snapshot,
            "objects": recovery_objects,
            "noAdditionalExposure": no_additional_exposure,
        }
    )
    attempt_body = canonical_json(failure_report) + b"\n"
    write_private_bytes(case_dir / "recovery-attempt.json", attempt_body, refuse_existing=True)
    if final_error is not None:
        raise BenchmarkExecutionError("cache-only recovery did not finish ready") from final_error
    if result_body is None or revision_object is None or output_facts is None:
        raise AssertionError("validated cache-only recovery facts were not retained")
    finished_at = datetime.now(UTC)
    wall_seconds = time.monotonic() - started_monotonic
    result_sha256 = hashlib.sha256(canonical_json(result_body)).hexdigest()
    report = wire_report(
        {
            "format": "temnia-speech-benchmark-cache-recovery/1",
            "case": case.model_dump(mode="json", by_alias=True),
            "deployment": {
                "app": variant.app,
                "protocol": "temnia-speech/2",
                "gpuSourceBuildId": manifest.source_build_id,
                "initialWorkerSourceBuildId": manifest.source_build_id,
                "workerSourceBuildId": worker_source_build_id,
                "originalJournalSha256": original_journal_sha256,
                "modelManifest": manifest.model_manifest.model_dump(mode="json", by_alias=True),
            },
            "original": {
                **original,
                "objects": original_objects,
                "reservedExposureMicros": variant.case_exposure_micros,
                "reservedDispatches": 3,
            },
            "recovery": {
                **recovery_snapshot,
                "objects": recovery_objects,
                "resultSha256": result_sha256,
                "outputFacts": output_facts,
                "startedAt": started_at.isoformat().replace("+00:00", "Z"),
                "finishedAt": finished_at.isoformat().replace("+00:00", "Z"),
                "wallSeconds": wall_seconds,
                "budgetMicros": 1,
            },
            "noAdditionalExposure": True,
            "cleanupRequired": True,
        }
    )
    body = canonical_json(report) + b"\n"
    receipt_path = case_dir / "continuation-receipt.json"
    write_private_bytes(receipt_path, body, refuse_existing=True)
    receipt_sha256 = hashlib.sha256(body).hexdigest()
    summary: dict[str, object] = {
        "case": case.key,
        "variant": case.variant_id,
        "kind": case.kind,
        "block": case.block,
        "resultSha256": result_sha256,
        "transcriptSha256": revision_object["sha256"],
        "wallSeconds": wall_seconds,
        "functionElapsedSeconds": None,
        "outputFacts": output_facts,
        "costFacts": _cost_facts(
            cast("list[dict[str, object]]", original_snapshot["attempts"]), variant
        ),
    }
    return summary, cast("UUID", recovery_run_id), receipt_sha256


def _load_manifest(path: Path) -> BenchmarkDeploymentManifest:
    return BenchmarkDeploymentManifest.model_validate_json(
        path.resolve(strict=True).read_bytes(), strict=True
    )


def _sources(args: argparse.Namespace) -> dict[str, FrozenSource]:
    return {
        "preflight": FrozenSource(
            kind="preflight",
            path=args.short_source,
            sha256=SHORT_SOURCE_SHA256,
            size_bytes=SHORT_SOURCE_SIZE,
            duration_ms=SHORT_SOURCE_DURATION_MS,
        ),
        "long": FrozenSource(
            kind="long",
            path=args.long_source,
            sha256=LONG_SOURCE_SHA256,
            size_bytes=LONG_SOURCE_SIZE,
            duration_ms=LONG_SOURCE_DURATION_MS,
        ),
    }


def _validate_recovery_arguments(args: argparse.Namespace) -> None:
    """Reject an acknowledgment that is not paired with the recovery operation."""
    if args.acknowledge_unstarted_recovery is not None and args.resume_failed_case is None:
        raise ValueError("unstarted recovery acknowledgment requires --resume-failed-case")
    completed = getattr(args, "acknowledge_completed_long_a_1", None)
    if completed is not None and (
        args.resume_failed_case is not None
        or args.acknowledge_unstarted_recovery is not None
        or args.dry_run
    ):
        raise ValueError("completed long-A acknowledgment is a separate live continuation")


async def run(args: argparse.Namespace) -> None:  # noqa: C901, PLR0912, PLR0915
    """Validate and then execute the fixed experiment sequentially."""
    _validate_recovery_arguments(args)
    manifest = _load_manifest(args.deployment_manifest)
    current_worker_build = source_build_id()
    sources = _sources(args)
    for source in sources.values():
        source.verify()
    temporal = TemporalSettings.from_env()
    database_url = os.environ.get("PIPELINE_DATABASE_URL", "")
    if not database_url:
        raise ValueError("PIPELINE_DATABASE_URL is required")
    assert_benchmark_environment(database_url, temporal.namespace, args.experiment_id)
    scope = resolve_scope()
    output_dir = args.output_dir.resolve()
    journal_path = output_dir / "experiment.json"
    completed_long_a_ack = getattr(args, "acknowledge_completed_long_a_1", None)
    if completed_long_a_ack is not None:
        if completed_long_a_ack != REVIEWED_LONG_A_JOURNAL_SHA256:
            raise ValueError("completed long-A acknowledgment differs from reviewed journal bytes")
        if args.worker_source_build_id != current_worker_build:
            raise ValueError("completed long-A continuation requires the current worker build id")
        current_bytes = journal_path.read_bytes()
        current = BenchmarkJournal.model_validate_json(current_bytes, strict=True)
        evidence_journal = (
            output_dir.parent / "long-a-1-completed-recovery-evidence" / "journal.json"
        ).read_bytes()
        if _sha256_bytes(evidence_journal) != REVIEWED_LONG_A_JOURNAL_SHA256:
            raise ValueError("preserved completed long-A journal identity changed")
        original = BenchmarkJournal.model_validate_json(evidence_journal, strict=True)
        validate_completed_long_a_journal(
            original,
            organization_id=scope.organizationId,
            manifest=manifest,
            previous_worker_source_build_id=PREVIOUS_LONG_A_WORKER_BUILD,
        )
        current_is_original = current_bytes == evidence_journal
        if not current_is_original:
            validate_completed_long_a_transform(
                original=original,
                current=current,
                worker_source_build_id=current_worker_build,
            )
        with resume_experiment_lease(
            args.experiment_id,
            journal_path=journal_path,
            manifest_sha256=manifest.sha256,
        ) as lease:
            async with database_continuation_lease(
                database_url,
                organization_id=scope.organizationId,
                experiment_id=args.experiment_id,
                cases=current.cases,
            ):
                if (
                    journal_path.read_bytes() != current_bytes
                    or (
                        output_dir.parent / "long-a-1-completed-recovery-evidence" / "journal.json"
                    ).read_bytes()
                    != evidence_journal
                ):
                    raise ValueError("completed long-A evidence changed after lease acquisition")
                base = Context.from_env()
                summary_journal = current
                if current_is_original:
                    summary_journal = original.model_copy(
                        update={
                            "worker_source_build_id": current_worker_build,
                            "cases": tuple(
                                case.model_copy(update={"status": "completed"})
                                if case.key == "long-a-1"
                                else case
                                for case in original.cases
                            ),
                        }
                    )
                summaries = _load_completed_summaries(output_dir, summary_journal, manifest)
                if current_is_original:
                    current = await _acknowledge_completed_long_a_case(
                        base=base,
                        temporal=temporal,
                        manifest=manifest,
                        journal_path=journal_path,
                        journal=original,
                        output_dir=output_dir,
                        source=sources["long"],
                        worker_source_build_id=current_worker_build,
                        lease=lease,
                    )
                else:
                    await _prove_completed_long_a(
                        base=base,
                        temporal=temporal,
                        manifest=manifest,
                        case=current.cases[4],
                        variant=manifest.variants[0],
                        source=sources["long"],
                        output_dir=output_dir,
                    )
                    _validate_continuation_receipt(
                        output_dir=output_dir,
                        original=original,
                        current=current,
                        worker_source_build_id=current_worker_build,
                    )
                if [summary["case"] for summary in summaries] != [
                    "preflight-a",
                    "preflight-b",
                    "preflight-c",
                    "preflight-d",
                    "long-a-1",
                ]:
                    raise BenchmarkExecutionError("completed benchmark summaries are incomplete")
                await _execute_cases(
                    args=args,
                    database_url=database_url,
                    temporal=temporal,
                    manifest=manifest,
                    journal=current,
                    journal_path=journal_path,
                    sources=sources,
                    lease=lease,
                    worker_source_build_id=current_worker_build,
                    initial_summaries=summaries,
                )
        print(f"wrote private benchmark evidence under {output_dir}")
        return
    if args.resume_failed_case is not None:
        if args.dry_run:
            raise ValueError("cache-only recovery cannot be combined with dry-run")
        if args.worker_source_build_id != current_worker_build:
            raise ValueError("cache-only recovery requires the explicit current worker build id")
        current_journal_bytes = journal_path.read_bytes()
        current_journal_sha256 = hashlib.sha256(current_journal_bytes).hexdigest()
        journal = BenchmarkJournal.model_validate_json(current_journal_bytes, strict=True)
        original_journal_path = output_dir / "original-admitted-journal.json"
        already_recovering = journal.cases[0].status == "recovering"
        if already_recovering:
            if args.acknowledge_unstarted_recovery != current_journal_sha256:
                raise ValueError(
                    "recovering benchmark requires acknowledgment of its exact current journal"
                )
            original_journal = original_journal_path.read_bytes()
            original_journal_sha256 = hashlib.sha256(original_journal).hexdigest()
            if original_journal_sha256 != REVIEWED_ORIGINAL_JOURNAL_SHA256:
                raise ValueError("preserved original admitted journal identity changed")
            original = BenchmarkJournal.model_validate_json(original_journal, strict=True)
            validate_cache_only_recovery_journal(
                original,
                organization_id=scope.organizationId,
                manifest=manifest,
            )
            _validate_permitted_recovering_journal(
                original=original,
                current=journal,
                worker_source_build_id=current_worker_build,
                deployment_source_build_id=manifest.source_build_id,
            )
        else:
            if args.acknowledge_unstarted_recovery is not None:
                raise ValueError("unstarted acknowledgment applies only to a recovering journal")
            validate_cache_only_recovery_journal(
                journal,
                organization_id=scope.organizationId,
                manifest=manifest,
            )
            original_journal = current_journal_bytes
            original_journal_sha256 = current_journal_sha256
            if original_journal_path.exists():
                if original_journal_path.read_bytes() != original_journal:
                    raise ValueError("preserved original journal differs from the admitted journal")
            else:
                write_private_bytes(original_journal_path, original_journal, refuse_existing=True)
        with resume_experiment_lease(
            args.experiment_id,
            journal_path=journal_path,
            manifest_sha256=manifest.sha256,
        ) as lease:
            async with database_resume_lease(
                database_url,
                organization_id=scope.organizationId,
                experiment_id=args.experiment_id,
                case=journal.cases[0],
            ):
                if journal_path.read_bytes() != current_journal_bytes:
                    raise ValueError("benchmark journal changed after recovery lock acquisition")
                if original_journal_path.read_bytes() != original_journal:
                    raise ValueError(
                        "preserved original journal changed after recovery lock acquisition"
                    )
                base = Context.from_env()
                variant = manifest.variants[0]
                if already_recovering:
                    acknowledged, _ = await _acknowledge_unstarted_recovery(
                        base=base,
                        temporal=temporal,
                        case=journal.cases[0],
                        variant=variant,
                        output_dir=output_dir,
                        current_journal_sha256=current_journal_sha256,
                        original_journal_sha256=original_journal_sha256,
                        worker_source_build_id=current_worker_build,
                    )
                    expected_original_ledger_sha256 = cast(
                        "str", acknowledged["immutableLedgerSha256"]
                    )
                    recovery_journal_sha256 = current_journal_sha256
                else:
                    original_snapshot = await _database_snapshot(base, journal.cases[0])
                    original_facts = _original_recovery_facts(
                        original_snapshot,
                        variant=variant,
                        require_failed_transcript=True,
                    )
                    expected_original_ledger_sha256 = cast(
                        "str", original_facts["immutableLedgerSha256"]
                    )
                    begin_cache_only_recovery(
                        journal_path,
                        args.resume_failed_case,
                        worker_source_build_id=current_worker_build,
                        deployment_source_build_id=manifest.source_build_id,
                        lease=lease,
                    )
                    recovery_journal_sha256 = hashlib.sha256(journal_path.read_bytes()).hexdigest()
                recovery_summary, recovery_run_id, receipt_sha256 = await _recover_failed_case(
                    base=base,
                    temporal=temporal,
                    manifest=manifest,
                    variant=variant,
                    case=journal.cases[0],
                    source=sources["preflight"],
                    output_dir=output_dir,
                    task_queue_prefix=args.task_queue,
                    worker_source_build_id=current_worker_build,
                    current_journal_sha256=recovery_journal_sha256,
                    original_journal_sha256=original_journal_sha256,
                    expected_original_ledger_sha256=expected_original_ledger_sha256,
                )
                finish_cache_only_recovery(
                    journal_path,
                    args.resume_failed_case,
                    recovery_run_id=recovery_run_id,
                    receipt_sha256=receipt_sha256,
                    lease=lease,
                )
                await _execute_cases(
                    args=args,
                    database_url=database_url,
                    temporal=temporal,
                    manifest=manifest,
                    journal=read_journal(journal_path),
                    journal_path=journal_path,
                    sources=sources,
                    lease=lease,
                    worker_source_build_id=current_worker_build,
                    initial_summaries=[recovery_summary],
                )
        print(f"wrote private benchmark evidence under {output_dir}")
        return
    if current_worker_build != manifest.source_build_id:
        raise ValueError("fresh benchmark worker build differs from every deployment")
    journal = build_journal(
        experiment_id=args.experiment_id,
        organization_id=scope.organizationId,
        manifest=manifest,
    )
    with experiment_lease(args.experiment_id) as lease:
        if args.dry_run:
            initialize_journal(journal_path, journal, lease=lease)
            write_private_json(
                output_dir / "deployment-manifest.json",
                manifest.model_dump(mode="json", by_alias=True),
            )
            print(f"validated benchmark plan at {journal_path}")
            return
        async with database_experiment_lease(
            database_url,
            organization_id=scope.organizationId,
            experiment_id=args.experiment_id,
        ):
            lease.mark_admitted(journal_path=journal_path, manifest_sha256=manifest.sha256)
            initialize_journal(journal_path, journal, lease=lease)
            write_private_json(
                output_dir / "deployment-manifest.json",
                manifest.model_dump(mode="json", by_alias=True),
            )
            await _execute_cases(
                args=args,
                database_url=database_url,
                temporal=temporal,
                manifest=manifest,
                journal=journal,
                journal_path=journal_path,
                sources=sources,
                lease=lease,
                worker_source_build_id=current_worker_build,
                initial_summaries=[],
            )
    print(f"wrote private benchmark evidence under {output_dir}")


async def _execute_cases(
    *,
    args: argparse.Namespace,
    database_url: str,
    temporal: TemporalSettings,
    manifest: BenchmarkDeploymentManifest,
    journal: BenchmarkJournal,
    journal_path: Path,
    sources: dict[str, FrozenSource],
    lease: ExperimentLease,
    worker_source_build_id: str,
    initial_summaries: list[dict[str, object]],
) -> None:
    """Run every admitted case in order while retaining evidence on failure."""
    base = Context.from_env()
    if base.settings.database_url != database_url:
        raise ValueError("benchmark context opened a different database")
    if base.settings.transcription.provider != "modal-checkpointed":
        raise ValueError("TRANSCRIPTION_PROVIDER must be modal-checkpointed")
    with base.settings.transcription.speech_vad_model_path.open("rb") as model:
        verify_asset(model.read(SIZE_BYTES + 1))
    variants = {variant.id: variant for variant in manifest.variants}
    summaries = list(initial_summaries)
    try:
        for case in journal.cases:
            if case.status == "completed":
                continue
            if case.status != "planned":
                raise ValueError("benchmark continuation found a nonterminal admitted case")
            reserve_case(journal_path, case.key, lease=lease)
            print(f"benchmark case start key={case.key}")
            case_started = time.monotonic()
            error_type: str | None = None
            completion_status = "failed"
            completion_dispatches: object = "unknown"
            try:
                report = await _run_case(
                    base,
                    temporal,
                    manifest,
                    variants[case.variant_id],
                    case,
                    sources[case.kind],
                    args.output_dir.resolve(),
                    args.task_queue,
                    worker_source_build_id,
                )
                workflow = cast("dict[str, object]", report["workflow"])
                run_values = cast("dict[str, object]", report["run"])
                completion_status = str(run_values["status"])
                completion_dispatches = run_values["dispatch_count"]
                summaries.append(
                    {
                        "case": case.key,
                        "variant": case.variant_id,
                        "kind": case.kind,
                        "block": case.block,
                        "resultSha256": workflow["resultSha256"],
                        "transcriptSha256": workflow["transcriptSha256"],
                        "wallSeconds": workflow["wallSeconds"],
                        "wallTimingStatus": "usable",
                        "functionElapsedSeconds": workflow["functionElapsedSeconds"],
                        "outputFacts": report["outputFacts"],
                        "costFacts": report["costFacts"],
                    }
                )
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                finish_case(
                    journal_path,
                    case.key,
                    error_type=error_type,
                    lease=lease,
                )
                elapsed = time.monotonic() - case_started
                print(
                    f"benchmark case complete key={case.key} elapsedSeconds={elapsed:.3f} "
                    f"dispatchCount={completion_dispatches} status={completion_status}"
                )
    finally:
        current = read_journal(journal_path)
        comparisons = (
            benchmark_summary(summaries)
            if len(summaries) == EXPECTED_CASES
            else {
                "outputDifferencesVsA": None,
                "latencyStaircase": None,
                "actualCostTotalsMicros": None,
                "actualCostWinner": None,
            }
        )
        write_private_json(
            args.output_dir.resolve() / "summary.json",
            {
                "format": "temnia-speech-benchmark-summary/1",
                "deploymentManifestSha256": manifest.sha256,
                "reservedExposureMicros": current.reserved_exposure_micros,
                "reservedDispatches": current.reserved_dispatches,
                "cases": summaries,
                **comparisons,
                "cleanupRequired": True,
            },
        )
        await db.close_pool()


def parser() -> argparse.ArgumentParser:
    """Build the finite benchmark CLI; credentials remain environment-only."""
    result = argparse.ArgumentParser(prog="benchmark-checkpointed-speech")
    result.add_argument("--deployment-manifest", required=True, type=Path)
    result.add_argument("--short-source", required=True, type=Path)
    result.add_argument("--long-source", required=True, type=Path)
    result.add_argument("--experiment-id", required=True)
    result.add_argument("--task-queue", required=True)
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--resume-failed-case", choices=("preflight-a",))
    result.add_argument("--worker-source-build-id")
    result.add_argument("--acknowledge-unstarted-recovery")
    result.add_argument("--acknowledge-completed-long-a-1")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run the reviewed matrix and halt on the first failed case."""
    asyncio.run(run(parser().parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
