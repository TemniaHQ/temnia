"""Run checkpointed speech through an isolated Temporal worker and ledger.

The operator supplies a dedicated migrated database and private environment
file through the shell. This program never accepts or prints credentials.
"""

# The qualification report is intentionally verbose and keeps nullable evidence.
# ruff: noqa: EM101, PLR0913, TRY003

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import obstore as obs
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.ingest import Context
from temnia_pipeline.modal_build import source_build_id
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import (
    TemporalSettings,
)
from temnia_pipeline.speech.activities import SpeechActivities
from temnia_pipeline.speech.assets import SIZE_BYTES, verify_asset
from temnia_pipeline.speech.client import SpeechModalClient, assert_checkpointed_deployment
from temnia_pipeline.speech.qualification import (
    assert_isolated_database,
    assert_qualification_completed,
    assert_qualification_limits,
    wire_report,
)
from temnia_pipeline.transcription.activities import Transcribe
from temnia_pipeline.transcription.checkpointed import (  # noqa: TC001
    CheckpointedTranscription,
    TranscriptionPlan,
)
from temnia_pipeline.workflows import TranscribeWorkflow

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_REPORT_ATTEMPTS = 8


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class QualificationSpeechActivities(SpeechActivities):
    """Inject one retryable lost activity result after every stage was accepted."""

    injected = False
    observed_attempts: list[int]

    def __init__(self, ctx: Context, *, inject_lost_result: bool) -> None:
        super().__init__(ctx)
        self.inject_lost_result = inject_lost_result
        self.observed_attempts = []
        self.observed_dispatch_counts: list[int] = []

    @activity.defn(name="checkpointed_transcribe")
    async def checkpointed_transcribe(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> CheckpointedTranscription:
        """Delegate to the real boundary, then lose only activity attempt one's return."""
        result = await super().checkpointed_transcribe(request, plan)
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
            raise RuntimeError("qualification lost its accepted speech run")
        self.observed_dispatch_counts.append(int(run["dispatch_count"]))
        if self.inject_lost_result and attempt_number == 1:
            self.injected = True
            raise ApplicationError(
                "qualification injected a lost activity result after accepted checkpoints",
                type="QualificationLostResult",
            )
        return result


async def _seed_source(ctx: Context, source_id: UUID, source: Path, duration_ms: int) -> str:
    scope = resolve_scope()
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    audio_key = f"{prefix}audio/audio.m4a"
    sha256, size = _sha256(source)
    async with db.scoped(ctx.settings.database_url, scope) as conn:
        existing = await (
            await conn.execute("SELECT id FROM source WHERE id = %s", (source_id,))
        ).fetchone()
        if existing is not None:
            raise ValueError("qualification source id already exists")
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (scope.organizationId, f"speech-qualification-{source_id}"),
            )
        ).fetchone()
        if project is None:
            raise RuntimeError("qualification project insert returned no id")
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status, duration_ms, audio_channels, audio_codec)
            VALUES (%s, %s, %s, %s, %s, 'audio/mp4', %s, %s, 'ready', %s, 1, 'aac')
            """,
            (
                source_id,
                scope.organizationId,
                project["id"],
                f"Speech qualification {source_id}",
                source.name,
                size,
                f"{prefix}master/{source.name}",
                duration_ms,
            ),
        )
    await storage.upload_file(ctx.store, audio_key, source)
    head = await obs.head_async(ctx.store, audio_key)
    if int(head["size"]) != size:
        raise OSError("qualification audio upload did not preserve its frozen size")
    return sha256


async def _report(
    ctx: Context,
    *,
    source_id: UUID,
    source_sha256: str,
    source_size: int,
    duration_ms: int,
    result: object | None,
    qualifier: QualificationSpeechActivities,
    workflow_error: str | None = None,
) -> dict[str, object]:
    scope = resolve_scope()
    async with db.scoped(ctx.settings.database_url, scope) as conn:
        run = await (
            await conn.execute(
                """
                SELECT id, status, budget_micros, spent_micros, reserved_micros,
                       dispatch_count, config, route_snapshot
                  FROM harness_run
                 WHERE source_id = %s AND lane = 'transcription'
                """,
                (source_id,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                """
                SELECT a.id, a.operation_id, a.attempt_number, a.state, a.provider,
                       a.model, a.family, a.remote_handle, a.estimated_cost_micros,
                       a.actual_cost_micros, a.cost_status, a.usage, a.dispatched_at,
                       a.finished_at, a.result_artifact_id, o.stage,
                       r.state AS reservation_state,
                       r.amount_micros AS reservation_amount_micros
                  FROM harness_attempt a
                  JOIN harness_operation o ON o.id = a.operation_id
                  LEFT JOIN harness_reservation r ON r.attempt_id = a.id
                 WHERE a.run_id = %s
                 ORDER BY a.created_at, a.id
                 LIMIT %s
                """,
                (run["id"] if run else None, MAX_REPORT_ATTEMPTS + 1),
            )
        ).fetchall()
        if len(attempts) > MAX_REPORT_ATTEMPTS:
            raise RuntimeError("qualification exceeded its bounded attempt report")
        artifacts = await (
            await conn.execute(
                """
                SELECT id, fingerprint, sha256, size_bytes, metadata
                  FROM harness_artifact
                 WHERE source_id = %s AND kind = 'speech_checkpoint'
                 ORDER BY created_at, id
                """,
                (source_id,),
            )
        ).fetchall()
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    admission_keys = sorted(
        key for key in await storage.list_keys(ctx.store, f"{prefix}transcript/admissions/")
    )
    normalized_result = (
        cast("Any", result).model_dump(mode="json", by_alias=True) if result is not None else None
    )
    return {
        "format": "temnia-checkpointed-speech-qualification/1",
        "observedAt": datetime.now(UTC),
        "source": {
            "id": str(source_id),
            "sha256": source_sha256,
            "sizeBytes": source_size,
            "durationMs": duration_ms,
        },
        "deployment": {
            "app": ctx.settings.transcription.speech_modal_app,
            "environment": ctx.settings.transcription.modal_environment,
            "protocol": ctx.settings.transcription.speech_protocol,
            "sourceBuildId": source_build_id(),
        },
        "workflow": {
            "result": normalized_result,
            "errorType": workflow_error,
            "injectedLostActivityResult": qualifier.injected,
            "checkpointedActivityAttempts": qualifier.observed_attempts,
            "observedPhysicalDispatchCounts": qualifier.observed_dispatch_counts,
        },
        "ledger": {
            "run": {key: run[key] for key in run} if run else None,
            "attempts": [{key: row[key] for key in row} for row in attempts],
        },
        "checkpoints": [{key: row[key] for key in row} for row in artifacts],
        "admissionKeys": admission_keys,
        "proofLimits": [
            "configured exposure is not a provider invoice ceiling",
            "successful source execution does not establish general accuracy",
            "admission cannot fence provider allocation before user code",
        ],
        "cleanup": {"objectPrefix": prefix, "databaseCleanupRequired": True},
    }


async def run(args: argparse.Namespace) -> None:
    """Run one source through the real local Temporal and checkpointed provider path."""
    source = args.source.resolve(strict=True)
    source_id = args.source_id or uuid4()
    source_sha256, source_size = _sha256(source)
    if args.expected_sha256 and source_sha256 != args.expected_sha256:
        raise ValueError("source SHA-256 differs from --expected-sha256")
    if args.expected_size is not None and source_size != args.expected_size:
        raise ValueError("source size differs from --expected-size")
    temporal = TemporalSettings.from_env()
    ctx = Context.from_env()
    assert_isolated_database(ctx.settings.database_url)
    if ctx.settings.transcription.provider != "modal-checkpointed":
        raise ValueError("TRANSCRIPTION_PROVIDER must be modal-checkpointed")
    assert_qualification_limits(ctx.settings.transcription)
    with ctx.settings.transcription.speech_vad_model_path.open("rb") as model:
        verify_asset(model.read(SIZE_BYTES + 1))
    await assert_checkpointed_deployment(
        SpeechModalClient(ctx.settings.transcription), ctx.settings.transcription
    )
    seeded_sha = await _seed_source(ctx, source_id, source, args.duration_ms)
    if seeded_sha != source_sha256:
        raise RuntimeError("source identity changed while preparing qualification")
    scope = resolve_scope()
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    request = TranscribeInput(
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=args.duration_ms,
        scope=scope,
        sourceId=source_id,
    )
    client = await Client.connect(
        temporal.address,
        namespace=temporal.namespace,
        data_converter=pydantic_data_converter,
    )
    transcribe = Transcribe(ctx)
    qualifier = QualificationSpeechActivities(ctx, inject_lost_result=args.inject_lost_result)
    worker = Worker(
        client,
        task_queue=args.task_queue,
        workflows=[TranscribeWorkflow],
        activities=[*transcribe.activities(), *qualifier.activities()],
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "pydantic", "pydantic_core"
            )
        ),
    )
    result = None
    execution_error: Exception | asyncio.CancelledError | None = None
    try:
        async with worker:
            result = await client.execute_workflow(
                TranscribeWorkflow.run,
                request,
                id=args.workflow_id,
                task_queue=args.task_queue,
            )
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
        # Save scoped diagnostic facts even when the workflow never reaches success.
        execution_error = error
    report = await _report(
        ctx,
        source_id=source_id,
        source_sha256=source_sha256,
        source_size=source_size,
        duration_ms=args.duration_ms,
        result=result,
        qualifier=qualifier,
        workflow_error=type(execution_error).__name__ if execution_error else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(
        os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb"
    ) as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(canonical_json(wire_report(report)) + b"\n")
    try:
        if execution_error is not None:
            raise execution_error
        assert_qualification_completed(report, require_lost_result=args.inject_lost_result)
        if args.cleanup_prefix:
            deleted = await storage.delete_prefix(ctx.store, prefix)
            print(f"deleted {deleted} owned qualification objects")  # noqa: T201
    finally:
        await db.close_pool()
    print(f"wrote {args.output}")  # noqa: T201


def parser() -> argparse.ArgumentParser:
    """Build the explicit qualification arguments; secrets remain environment-only."""
    result = argparse.ArgumentParser(prog="qualify-checkpointed-speech")
    result.add_argument("--source", required=True, type=Path)
    result.add_argument("--source-id", type=UUID)
    result.add_argument("--duration-ms", required=True, type=int)
    result.add_argument("--expected-sha256")
    result.add_argument("--expected-size", type=int)
    result.add_argument("--workflow-id", required=True)
    result.add_argument("--task-queue", required=True)
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--inject-lost-result", action="store_true")
    result.add_argument("--cleanup-prefix", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run qualification, surfacing any unknown outcome without cleanup."""
    args = parser().parse_args(argv)
    if args.duration_ms <= 0:
        raise ValueError("--duration-ms must be positive")
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
