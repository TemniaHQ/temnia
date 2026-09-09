"""Actual Temporal and Postgres proof for speech benchmark cache recovery."""

# ruff: noqa: EM101, PLR0913, PLR0917, SLF001, TRY003

from __future__ import annotations

import hashlib
import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.ingest import Context
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import (
    PipelineSettings,
    StorageSettings,
    TranscodeSettings,
    TranscriptionSettings,
)
from temnia_pipeline.speech.activities import ensure_speech_run, settle_speech_run
from temnia_pipeline.speech.activities_v2 import PreparedStageV2, SpeechActivitiesV2
from temnia_pipeline.speech.checkpoints_v2 import admission_key_v2, checkpoint_for_v2
from temnia_pipeline.speech.contracts import ExecutionIdentity, StageTelemetry, canonical_json
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    RecognizeConfigV2,
    SpeakerAssignmentConfig,
    SpeakerTurnsConfig,
    SpeechStageJobV2,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.speech_benchmark import BenchmarkCase, BenchmarkVariant
from temnia_pipeline.transcription.activities import Transcribe
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechStageV2,
    CoverageRecord,
    TranscriptionPlan,
)
from temnia_pipeline.transcription.factory import make_transcription
from temnia_pipeline.workflows import TranscribeWorkflow

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

pytestmark = pytest.mark.timeout(60)

DRIVER_PATH = Path(__file__).parents[1] / "scripts/benchmark_checkpointed_speech.py"
DRIVER_SPEC = importlib.util.spec_from_file_location(
    "benchmark_checkpointed_speech_recovery", DRIVER_PATH
)
if DRIVER_SPEC is None or DRIVER_SPEC.loader is None:
    raise RuntimeError("could not load checkpointed speech benchmark driver")
driver = cast("Any", importlib.util.module_from_spec(DRIVER_SPEC))
DRIVER_SPEC.loader.exec_module(driver)


def _pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: recovery persistence cannot skip")
    return url


def _profile() -> SpeechResourceProfile:
    return SpeechResourceProfile(
        cpu_cores=4,
        stage_timeout_seconds=900,
        progress_mode="synchronous_control",
    )


def _variant() -> BenchmarkVariant:
    return BenchmarkVariant(
        id="A",
        app="temnia-speech-v2-a-test",
        resource_profile=_profile(),
        execution_topology="serial",
        rate_micros_per_hour=1_250_000,
    )


def _plan(*, budget_micros: int) -> TranscriptionPlan:
    profile = _profile()
    model_sha = "a" * 64
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech-v2-a-test",
        environment="test",
        protocol="temnia-speech/2",
        build="b" * 64,
        budget_micros=budget_micros,
        rate_micros_per_hour=1_250_000,
        stage_timeout_seconds=profile.stage_timeout_seconds,
        startup_timeout_seconds=profile.startup_timeout_seconds,
        dispatch_limit=3,
        audio_sha256="c" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="d" * 64,
        resource_profile=profile,
        model_manifest=SpeechModelManifest(
            sha256=model_sha,
            file_count=3,
            total_bytes=4096,
            model_root=f"/models/frozen/{model_sha}",
            image_assets_sha256="e" * 64,
        ),
        execution_topology="serial",
        recognize_v2=RecognizeConfigV2(),
        speaker_turns=SpeakerTurnsConfig(),
        allow_oom_recovery=False,
    )


def _context(url: str, store: MemoryStore, tmp_path: Path) -> Context:
    transcription = TranscriptionSettings(
        provider="modal-checkpointed",
        modal_app="temnia-media",
        modal_environment="test",
        progress_dict="test-progress",
        recordings_dir=tmp_path,
        recording=None,
        speech_modal_app="temnia-speech-v2-a-test",
        speech_protocol="temnia-speech/2",
        speech_expected_build="b" * 64,
        speech_budget_micros=1,
        speech_rate_micros_per_hour=1_250_000,
        speech_stage_timeout_seconds=900,
        speech_startup_timeout_seconds=120,
        speech_dispatch_limit=3,
        speech_resource_profile=_profile(),
        speech_model_manifest=cast("SpeechModelManifest", _plan(budget_micros=1).model_manifest),
        speech_execution_topology="serial",
    )
    settings = PipelineSettings(
        database_url=url,
        work_root=tmp_path,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        transcode=TranscodeSettings(
            backend="local", modal_app="temnia-media", modal_environment="test", progress_dict="x"
        ),
        transcription=transcription,
    )
    return Context(
        settings=settings,
        storage=StorageSettings.from_env(),
        store=cast("Any", store),
        transcoder=cast("Any", object()),
        transcription=make_transcription(settings, cast("Any", store)),
    )


async def _seed_source(url: str, request: TranscribeInput) -> None:
    async with db.scoped(url, request.scope) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (request.scope.organizationId, f"speech-recovery-{request.sourceId}"),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status)
            VALUES (%s, %s, %s, 'speech recovery', 'speech.m4a', 'audio/mp4', 123, %s, 'ready')
            """,
            (
                request.sourceId,
                request.scope.organizationId,
                project["id"],
                f"{request.artifactPrefix}master/speech.m4a",
            ),
        )


def _job(prepared: PreparedStageV2, attempt: ledger.Attempt) -> SpeechStageJobV2:
    plan = prepared.plan
    request = prepared.request
    return SpeechStageJobV2(
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
        resource_profile=cast("SpeechResourceProfile", plan.resource_profile),
        model_manifest=cast("SpeechModelManifest", plan.model_manifest),
        execution_topology=cast("Any", plan.execution_topology),
    )


async def _complete_physical_stage(
    url: str,
    store: MemoryStore,
    prepared: PreparedStageV2,
    attempt: ledger.Attempt,
    payload: dict[str, object],
) -> AcceptedSpeechStageV2:
    request = prepared.request
    await ledger.mark_dispatched(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        run_id=prepared.run_id,
        operation_id=prepared.operation.id,
        attempt_id=attempt.id,
        owner_token=prepared.owner,
        dispatch_limit=prepared.plan.dispatch_limit,
    )
    handle = f"call-{attempt.id}"
    await ledger.attach_remote_handle(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        operation_id=prepared.operation.id,
        attempt_id=attempt.id,
        owner_token=prepared.owner,
        remote_handle=handle,
    )
    await ledger.mark_running(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        operation_id=prepared.operation.id,
        attempt_id=attempt.id,
        owner_token=prepared.owner,
    )
    job = _job(prepared, attempt)
    execution = ExecutionIdentity(
        modal_call_id=handle,
        modal_task_id=f"task-{attempt.id}",
        admission_key=admission_key_v2(job),
    )
    checkpoint = checkpoint_for_v2(job, prepared.plan.build, payload, execution)
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    await storage.upload_bytes(cast("Any", store), job.checkpoint_key, body, "application/json")
    telemetry = StageTelemetry(complete=True)
    artifact = await artifacts.accept_existing_json(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        store=cast("Any", store),
        identity=prepared.identity,
        key=job.checkpoint_key,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        metadata={
            "format": "speech-checkpoint/2",
            "attemptId": str(attempt.id),
            "callId": handle,
            "operationId": str(prepared.operation.id),
            "stage": prepared.configuration.stage,
            "telemetry": telemetry.model_dump(mode="json", by_alias=True),
        },
        dependency_ids=(
            [prepared.input_stage.artifact_id] if prepared.input_stage is not None else []
        ),
    )
    usage = dict(prepared.usage)
    usage["telemetry"] = telemetry.model_dump(mode="json", by_alias=True)
    await ledger.complete_attempt(
        url,
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
        checkpoint=ArtifactRefV2(
            key=job.checkpoint_key,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            stage=prepared.configuration.stage,
            configuration_sha256=checkpoint.configuration_sha256,
        ),
        operation_id=prepared.operation.id,
        attempt_id=attempt.id,
        call_id=handle,
        telemetry=telemetry,
        reused=False,
    )


async def _pending_assignment_operation(
    url: str,
    request: TranscribeInput,
    plan: TranscriptionPlan,
    run_id: UUID,
    aligned: AcceptedSpeechStageV2,
    turns: AcceptedSpeechStageV2,
) -> None:
    profile = cast("SpeechResourceProfile", plan.resource_profile)
    manifest = cast("SpeechModelManifest", plan.model_manifest)
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
    config = {
        "assignment": SpeakerAssignmentConfig().model_dump(mode="json", by_alias=True),
        "build": plan.build,
        "executionTopology": plan.execution_topology,
        "modelManifest": manifest.model_dump(mode="json", by_alias=True),
        "protocol": plan.protocol,
        "resourceProfile": profile.model_dump(mode="json", by_alias=True),
    }
    await ledger.acquire_operation(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        run_id=run_id,
        kind="diarize",
        stage="assign_speakers",
        inputs=inputs,
        config=config,
    )


async def _original_ledger(
    monkeypatch: pytest.MonkeyPatch,
    activities: SpeechActivitiesV2,
    request: TranscribeInput,
    plan: TranscriptionPlan,
    workflow_id: str,
) -> tuple[UUID, Mapping[StageV2, AcceptedSpeechStageV2]]:
    temporal_run_id = f"failed-{uuid.uuid4()}"
    with monkeypatch.context() as patch:
        patch.setattr(
            "temnia_pipeline.speech.activities.activity.info",
            lambda: SimpleNamespace(workflow_id=workflow_id),
        )
        run_id = await ensure_speech_run(
            activities.ctx.settings.database_url,
            request=request,
            plan=plan,
            temporal_run_id=temporal_run_id,
        )
    recognized_prepared = await activities._prepare_stage_v2(  # pyright: ignore[reportPrivateUsage]
        request, plan, run_id, cast("RecognizeConfigV2", plan.recognize_v2), None
    )
    turns_prepared = await activities._prepare_stage_v2(  # pyright: ignore[reportPrivateUsage]
        request, plan, run_id, cast("SpeakerTurnsConfig", plan.speaker_turns), None
    )
    assert isinstance(recognized_prepared, PreparedStageV2)
    assert isinstance(turns_prepared, PreparedStageV2)
    recognize_attempt, turns_attempt = await activities._reserve_v2(  # pyright: ignore[reportPrivateUsage]
        (recognized_prepared, turns_prepared)
    )
    recognized = await _complete_physical_stage(
        activities.ctx.settings.database_url,
        cast("MemoryStore", activities.ctx.store),
        recognized_prepared,
        recognize_attempt,
        {"language": "en", "segments": []},
    )
    turns = await _complete_physical_stage(
        activities.ctx.settings.database_url,
        cast("MemoryStore", activities.ctx.store),
        turns_prepared,
        turns_attempt,
        {"diarization": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]},
    )
    align_prepared = await activities._prepare_stage_v2(  # pyright: ignore[reportPrivateUsage]
        request, plan, run_id, AlignConfigV2(language="en"), recognized
    )
    assert isinstance(align_prepared, PreparedStageV2)
    (align_attempt,) = await activities._reserve_v2((align_prepared,))  # pyright: ignore[reportPrivateUsage]
    aligned = await _complete_physical_stage(
        activities.ctx.settings.database_url,
        cast("MemoryStore", activities.ctx.store),
        align_prepared,
        align_attempt,
        {
            "language": "en",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "hello",
                    "words": [{"word": "hello", "start": 0.0, "end": 1.0, "score": 0.9}],
                }
            ],
        },
    )
    await _pending_assignment_operation(
        activities.ctx.settings.database_url, request, plan, run_id, aligned, turns
    )
    async with db.scoped(activities.ctx.settings.database_url, request.scope) as conn:
        attempt = await db.claim_transcription(
            conn,
            request.sourceId,
            request.scope.organizationId,
            workflow_id,
            temporal_run_id,
        )
        assert attempt == 1
        await db.fail_transcription(
            conn, request.sourceId, "assignment SQL kind missing", temporal_run_id
        )
    assert await settle_speech_run(
        activities.ctx.settings.database_url,
        request=request,
        temporal_run_id=temporal_run_id,
        outcome="failed",
    )
    return run_id, {"recognize": recognized, "align": aligned, "speaker_turns": turns}


async def test_actual_temporal_cache_only_recovery_preserves_original_gpu_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = _pipeline_url()
    scope = resolve_scope()
    source_id = uuid.uuid4()
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    request = TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1_000,
    )
    store = MemoryStore()
    ctx = _context(url, store, tmp_path)
    speech = SpeechActivitiesV2(ctx)
    variant = _variant()
    producer_plan = _plan(budget_micros=variant.case_exposure_micros)
    workflow_id = f"speech-benchmark-test-{uuid.uuid4()}"
    case = BenchmarkCase(
        key="preflight-a",
        variant_id="A",
        kind="preflight",
        source_id=source_id,
        workflow_id=workflow_id,
        object_prefix=prefix,
        configured_exposure_micros=variant.case_exposure_micros,
        status="failed",
        error_type="UndefinedColumn",
    )
    await _seed_source(url, request)
    original_run_id, checkpoints = await _original_ledger(
        monkeypatch, speech, request, producer_plan, workflow_id
    )
    original_snapshot = await driver._database_snapshot(ctx, case, run_id=original_run_id)
    original = driver._original_recovery_facts(
        original_snapshot, variant=variant, require_failed_transcript=True
    )
    original_hash = original["immutableLedgerSha256"]
    recovery_plan = producer_plan.model_copy(update={"budget_micros": 1})

    @activity.defn(name="choose_transcription_plan")
    async def choose(_request: TranscribeInput) -> TranscriptionPlan:
        return recovery_plan

    @activity.defn(name="speech_coverage")
    async def coverage(_request: TranscribeInput, _plan: TranscriptionPlan) -> CoverageRecord:
        return CoverageRecord(error="detector deliberately unavailable in recovery test")

    def forbidden_client(_plan: TranscriptionPlan) -> object:
        pytest.fail("cache-only recovery attempted to construct a provider client")

    monkeypatch.setattr(speech, "_client", forbidden_client)
    transcribe = Transcribe(ctx)
    queue = f"speech-benchmark-recovery-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[TranscribeWorkflow],
            activities=[
                *transcribe.activities(),
                choose,
                coverage,
                speech.mark_speech_coverage_progress,
                speech.checkpointed_transcribe_v2,
                speech.assemble_checkpointed_transcript_v2,
                speech.settle_checkpointed_speech_run,
            ],
        ),
    ):
        result = await environment.client.execute_workflow(
            TranscribeWorkflow.run,
            request,
            id=workflow_id,
            task_queue=queue,
        )

    assert result.wordCount == 1
    assert result.speakerCount == 1
    async with db.scoped(url, scope) as conn:
        recovery = await (
            await conn.execute(
                """
                SELECT id FROM harness_run
                 WHERE source_id = %s AND id <> %s AND status = 'ready'
                 ORDER BY created_at DESC LIMIT 1
                """,
                (source_id, original_run_id),
            )
        ).fetchone()
    assert recovery is not None
    recovery_snapshot = await driver._database_snapshot(ctx, case, run_id=recovery["id"])
    driver._assert_cache_only_recovery(recovery_snapshot, original=original)
    original_after = await driver._database_snapshot(ctx, case, run_id=original_run_id)
    assert (
        driver._original_recovery_facts(
            original_after, variant=variant, require_failed_transcript=False
        )["immutableLedgerSha256"]
        == original_hash
    )
    recovery_operations = {
        row["stage"]: row
        for row in cast("list[dict[str, object]]", recovery_snapshot["operations"])
    }
    for stage, accepted in checkpoints.items():
        assert recovery_operations[stage]["result_artifact_id"] == accepted.artifact_id
    assert recovery_snapshot["attempts"] == []
    assert cast("dict[str, object]", recovery_snapshot["run"])["reserved_micros"] == 0
    assert cast("dict[str, object]", recovery_snapshot["run"])["spent_micros"] == 0
    assert cast("dict[str, object]", original_after["run"])["status"] == "failed"
    assert len(cast("list[object]", original_after["attempts"])) == 3
    await db.close_pool()
