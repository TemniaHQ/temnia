"""Real-Postgres persistence for the speech-v2 CPU assignment boundary."""

from __future__ import annotations

import hashlib
import os
import uuid
from types import SimpleNamespace
from typing import Any, cast

import obstore as obs
import pytest
from obstore.store import MemoryStore

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.speech import activities_v2
from temnia_pipeline.speech.activities import ensure_speech_run, settle_speech_run
from temnia_pipeline.speech.activities_v2 import SpeechActivitiesV2
from temnia_pipeline.speech.checkpoints_v2 import (
    admission_key_v2,
    checkpoint_for_v2,
)
from temnia_pipeline.speech.contracts import ExecutionIdentity, StageTelemetry, canonical_json
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    SpeechStageJobV2,
    StageConfigV2,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechStageV2,
    CoverageRecord,
    ParallelCheckpointedTranscription,
    TranscriptionPlan,
)


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: speech persistence cannot skip silently")
    return url


def frozen_plan() -> TranscriptionPlan:
    profile = SpeechResourceProfile()
    model_sha = "a" * 64
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech-v2",
        environment="test",
        protocol="temnia-speech/2",
        build="speech-v2-persistence-test",
        budget_micros=6_500_000,
        rate_micros_per_hour=profile.minimum_rate_micros_per_hour,
        stage_timeout_seconds=profile.stage_timeout_seconds,
        startup_timeout_seconds=profile.startup_timeout_seconds,
        dispatch_limit=5,
        audio_sha256="b" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="c" * 64,
        resource_profile=profile,
        model_manifest=SpeechModelManifest(
            sha256=model_sha,
            file_count=3,
            total_bytes=4096,
            model_root=f"/models/frozen/{model_sha}",
            image_assets_sha256="d" * 64,
        ),
        execution_topology="parallel",
        recognize_v2=RecognizeConfigV2(),
        speaker_turns=SpeakerTurnsConfig(),
        allow_oom_recovery=True,
    )


async def seed_source(url: str, request: TranscribeInput) -> None:
    async with db.scoped(url, request.scope) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (request.scope.organizationId, f"speech-v2-{request.sourceId}"),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status)
            VALUES (%s, %s, %s, 'speech-v2', 'speech.m4a', 'audio/mp4', 123, %s, 'ready')
            """,
            (
                request.sourceId,
                request.scope.organizationId,
                project["id"],
                f"{request.artifactPrefix}master/speech.m4a",
            ),
        )


async def accept_stage(  # noqa: PLR0913
    url: str,
    store: MemoryStore,
    *,
    request: TranscribeInput,
    plan: TranscriptionPlan,
    run_id: uuid.UUID,
    stage: StageV2,
    configuration: StageConfigV2,
    payload: dict[str, object],
    predecessor: AcceptedSpeechStageV2 | None = None,
) -> AcceptedSpeechStageV2:
    input_ref = predecessor.checkpoint if predecessor else None
    inputs = activities_v2._semantic_inputs_v2(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        request, plan, input_ref
    )
    semantic_config = activities_v2._semantic_config_v2(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        plan, configuration
    )
    operation = await ledger.acquire_operation(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        run_id=run_id,
        kind={"recognize": "recognize", "align": "align", "speaker_turns": "diarize"}[stage],
        stage=stage,
        inputs=inputs,
        config=semantic_config,
    )
    attempt_id = uuid.uuid4()
    job = SpeechStageJobV2(
        build=plan.build,
        operation_id=operation.operation.id,
        attempt_id=attempt_id,
        stage=stage,
        artifact_prefix=request.artifactPrefix,
        audio_key=request.audioKey,
        audio_sha256=cast("str", plan.audio_sha256),
        audio_size_bytes=cast("int", plan.audio_size_bytes),
        duration_ms=request.durationMs,
        checkpoint_key=(
            f"{request.artifactPrefix}transcript/v2/checkpoints/{stage}/{attempt_id}.json"
        ),
        configuration=configuration,
        input_checkpoint=input_ref,
        resource_profile=cast("SpeechResourceProfile", plan.resource_profile),
        model_manifest=cast("SpeechModelManifest", plan.model_manifest),
        execution_topology="parallel",
    )
    execution = ExecutionIdentity(
        modal_call_id=f"call-{stage}",
        modal_task_id=f"task-{stage}",
        admission_key=admission_key_v2(job),
    )
    checkpoint = checkpoint_for_v2(job, plan.build, payload, execution)
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    await storage.upload_bytes(cast("Any", store), job.checkpoint_key, body, "application/json")
    fingerprint = artifacts.fingerprint_for(
        inputs=inputs, config=semantic_config, kind=f"{stage}_v2"
    )
    identity = artifacts.ArtifactIdentity(kind="speech_checkpoint", fingerprint=fingerprint)
    accepted = await artifacts.accept_existing_json(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        store=cast("Any", store),
        identity=identity,
        key=job.checkpoint_key,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        metadata={"format": "speech-checkpoint/2", "stage": stage},
        dependency_ids=[predecessor.artifact_id] if predecessor else [],
    )
    await ledger.complete_operation_from_artifact(
        url,
        scope=request.scope,
        source_id=request.sourceId,
        run_id=run_id,
        operation_id=operation.operation.id,
        result_artifact_id=accepted.id,
        expected_artifact_kind="speech_checkpoint",
        expected_artifact_fingerprint=fingerprint,
    )
    return AcceptedSpeechStageV2(
        stage=stage,
        artifact_id=accepted.id,
        checkpoint=ArtifactRefV2(
            key=job.checkpoint_key,
            sha256=accepted.sha256,
            size_bytes=accepted.size_bytes,
            stage=stage,
            configuration_sha256=checkpoint.configuration_sha256,
        ),
        operation_id=operation.operation.id,
        attempt_id=None,
        call_id=None,
        telemetry=StageTelemetry(),
        reused=True,
    )


async def test_real_assignment_publication_reuse_assembly_and_settlement(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = pipeline_url()
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
    plan = frozen_plan()
    producer_temporal_run_id = f"speech-v2-producer-{uuid.uuid4()}"
    store = MemoryStore()
    await seed_source(url, request)
    monkeypatch.setattr(
        "temnia_pipeline.speech.activities.activity.info",
        lambda: SimpleNamespace(workflow_id=f"workflow-{source_id}"),
    )
    run_id = await ensure_speech_run(
        url,
        request=request,
        plan=plan,
        temporal_run_id=producer_temporal_run_id,
    )
    recognize = await accept_stage(
        url,
        store,
        request=request,
        plan=plan,
        run_id=run_id,
        stage="recognize",
        configuration=cast("RecognizeConfigV2", plan.recognize_v2),
        payload={"language": "en", "segments": []},
    )
    align = await accept_stage(
        url,
        store,
        request=request,
        plan=plan,
        run_id=run_id,
        stage="align",
        configuration=AlignConfigV2(language="en"),
        payload={
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
        predecessor=recognize,
    )
    turns = await accept_stage(
        url,
        store,
        request=request,
        plan=plan,
        run_id=run_id,
        stage="speaker_turns",
        configuration=cast("SpeakerTurnsConfig", plan.speaker_turns),
        payload={"diarization": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]},
    )
    assert await settle_speech_run(
        url,
        request=request,
        temporal_run_id=producer_temporal_run_id,
        outcome="failed",
    )

    recovery_temporal_run_id = f"speech-v2-recovery-{uuid.uuid4()}"
    recovery_plan = plan.model_copy(update={"budget_micros": 1})
    monkeypatch.setattr(activities_v2, "workflow_run_id", lambda: recovery_temporal_run_id)

    async def no_progress(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(activities_v2, "report_speech_progress", no_progress)

    def heartbeat(_details: object) -> None:
        return None

    monkeypatch.setattr("temnia_pipeline.speech.liveness.activity.heartbeat", heartbeat)

    instance = SpeechActivitiesV2.__new__(SpeechActivitiesV2)
    instance.ctx = cast(
        "Any",
        SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store),
    )
    recovered = await instance.checkpointed_transcribe_v2(request, recovery_plan)
    assert recovered.recognize.artifact_id == recognize.artifact_id
    assert recovered.align.artifact_id == align.artifact_id
    assert recovered.speaker_turns.artifact_id == turns.artifact_id
    assert all(
        stage.reused for stage in (recovered.recognize, recovered.align, recovered.speaker_turns)
    )
    first = recovered.assignment
    second = await instance._assignment_v2(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        request,
        recovery_plan,
        recovered.run_id,
        recovered.recognize,
        recovered.align,
        recovered.speaker_turns,
    )
    assert not first.reused
    assert second.artifact_id == first.artifact_id
    assert second.reused

    stages = ParallelCheckpointedTranscription(
        run_id=recovered.run_id,
        recognize=recovered.recognize,
        align=recovered.align,
        speaker_turns=recovered.speaker_turns,
        assignment=first,
    )
    record = await instance.assemble_checkpointed_transcript_v2(
        request,
        1,
        recovery_plan,
        stages,
        CoverageRecord(error="coverage deliberately unavailable in persistence test"),
    )
    raw = await obs.get_async(store, record.raw_key)
    assert b"SPEAKER_00" in bytes(await raw.bytes_async())
    assert await settle_speech_run(
        url,
        request=request,
        temporal_run_id=recovery_temporal_run_id,
        outcome="ready",
    )

    async with db.scoped(url, scope) as conn:
        assignment = await (
            await conn.execute(
                """
                SELECT id, kind FROM harness_artifact
                 WHERE id = %s
                """,
                (first.artifact_id,),
            )
        ).fetchone()
        dependencies = await (
            await conn.execute(
                """
                SELECT input_artifact_id FROM harness_artifact_dependency
                 WHERE artifact_id = %s ORDER BY input_artifact_id
                """,
                (first.artifact_id,),
            )
        ).fetchall()
        operation = await (
            await conn.execute(
                """
                SELECT status, stage FROM harness_operation
                 WHERE id = %s
                """,
                (first.operation_id,),
            )
        ).fetchone()
        attempt_count = await (
            await conn.execute(
                "SELECT count(*)::int AS count FROM harness_attempt WHERE run_id = %s",
                (recovered.run_id,),
            )
        ).fetchone()
        recovery_run = await (
            await conn.execute(
                """
                SELECT status, stage, spent_micros, reserved_micros
                  FROM harness_run WHERE id = %s
                """,
                (recovered.run_id,),
            )
        ).fetchone()
        producer_run = await (
            await conn.execute(
                "SELECT status, stage FROM harness_run WHERE id = %s",
                (run_id,),
            )
        ).fetchone()
        recovery_inputs = await (
            await conn.execute(
                """
                SELECT stage, result_artifact_id FROM harness_operation
                 WHERE run_id = %s AND stage = ANY(%s)
                 ORDER BY stage
                """,
                (recovered.run_id, ["recognize", "align", "speaker_turns"]),
            )
        ).fetchall()
    assert assignment == {"id": first.artifact_id, "kind": "speech_assignment"}
    assert [row["input_artifact_id"] for row in dependencies] == sorted(
        [align.artifact_id, turns.artifact_id], key=str
    )
    assert operation == {"status": "succeeded", "stage": "assign_speakers"}
    assert attempt_count == {"count": 0}
    assert recovery_run == {
        "status": "ready",
        "stage": "ready",
        "spent_micros": 0,
        "reserved_micros": 0,
    }
    assert producer_run == {"status": "failed", "stage": "failed"}
    assert {row["stage"]: row["result_artifact_id"] for row in recovery_inputs} == {
        "recognize": recognize.artifact_id,
        "align": align.artifact_id,
        "speaker_turns": turns.artifact_id,
    }

    missing_temporal_run_id = f"speech-v2-missing-cache-{uuid.uuid4()}"
    missing_plan = recovery_plan.model_copy(
        update={"recognize_v2": RecognizeConfigV2(batch_size=8)}
    )
    monkeypatch.setattr(activities_v2, "workflow_run_id", lambda: missing_temporal_run_id)
    with pytest.raises(ledger.BudgetExceeded):
        await instance.checkpointed_transcribe_v2(request, missing_plan)
    missing_run_id = await ensure_speech_run(
        url,
        request=request,
        plan=missing_plan,
        temporal_run_id=missing_temporal_run_id,
    )
    async with db.scoped(url, scope) as conn:
        missing_attempts = await (
            await conn.execute(
                "SELECT count(*)::int AS count FROM harness_attempt WHERE run_id = %s",
                (missing_run_id,),
            )
        ).fetchone()
    assert missing_attempts == {"count": 0}
    await db.close_pool()
