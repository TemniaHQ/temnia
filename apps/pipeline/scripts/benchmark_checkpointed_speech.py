"""Run the finite checkpointed-speech benchmark in an isolated environment."""

# Reports deliberately retain nullable operational evidence.
# ruff: noqa: BLE001, EM101, PLR0913, PLR0917, T201, TC001, TRY003

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import os
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import obstore as obs
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
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
from temnia_pipeline.speech.client import SpeechModalClient, assert_checkpointed_deployment
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
    assert_benchmark_environment,
    assert_case_completed,
    begin_cache_only_recovery,
    benchmark_summary,
    build_journal,
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


def _original_recovery_facts(
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
    if set(operation_by_stage) != {
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
    checkpoint_artifacts = {
        str(artifact.get("id")): artifact
        for artifact in artifact_values
        if artifact.get("kind") == "speech_checkpoint"
    }
    if set(checkpoint_artifacts) != set(checkpoint_ids) or any(
        cast("dict[str, object]", artifact.get("metadata", {})).get("format")
        != "speech-checkpoint/2"
        for artifact in checkpoint_artifacts.values()
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
    if set(by_stage) != expected_stages or any(
        operation.get("status") != "succeeded" or operation.get("result_artifact_id") is None
        for operation in by_stage.values()
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
    original_journal_sha256: str,
) -> tuple[dict[str, object], UUID, str]:
    """Complete only CPU assignment from the original immutable checkpoint set."""
    original_snapshot = await _database_snapshot(base, case)
    original = _original_recovery_facts(
        original_snapshot, variant=variant, require_failed_transcript=True
    )
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
            "noAdditionalExposure": (
                cast("dict[str, object]", recovery_snapshot.get("run", {})).get("dispatch_count")
                == 0
                and recovery_snapshot.get("attempts") == []
            ),
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


async def run(args: argparse.Namespace) -> None:  # noqa: PLR0915
    """Validate and then execute the fixed experiment sequentially."""
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
    if args.resume_failed_case is not None:
        if args.dry_run:
            raise ValueError("cache-only recovery cannot be combined with dry-run")
        if args.worker_source_build_id != current_worker_build:
            raise ValueError("cache-only recovery requires the explicit current worker build id")
        journal = read_journal(journal_path)
        validate_cache_only_recovery_journal(
            journal,
            organization_id=scope.organizationId,
            manifest=manifest,
        )
        original_journal = journal_path.read_bytes()
        original_journal_sha256 = hashlib.sha256(original_journal).hexdigest()
        original_journal_path = output_dir / "original-admitted-journal.json"
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
                begin_cache_only_recovery(
                    journal_path,
                    args.resume_failed_case,
                    worker_source_build_id=current_worker_build,
                    deployment_source_build_id=manifest.source_build_id,
                    lease=lease,
                )
                base = Context.from_env()
                variant = manifest.variants[0]
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
                    original_journal_sha256=original_journal_sha256,
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
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run the reviewed matrix and halt on the first failed case."""
    asyncio.run(run(parser().parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
