"""Call-site cancellation safety for protocol-v2 terminal reconciliation."""

# ruff: noqa: C901, PLR0915, SLF001
# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from temnia_pipeline.contracts import Scope, TranscribeInput
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.artifacts import ArtifactIdentity, HarnessArtifact
from temnia_pipeline.speech import activities_v2
from temnia_pipeline.speech.activities_v2 import PreparedStageV2, SpeechActivitiesV2
from temnia_pipeline.speech.checkpoints_v2 import (
    admission_for_v2,
    admission_key_v2,
    artifact_ref_v2,
    checkpoint_for_v2,
)
from temnia_pipeline.speech.contracts import (
    ExecutionIdentity,
    StageError,
    StageTelemetry,
    canonical_json,
)
from temnia_pipeline.speech.contracts_v2 import (
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    SpeechStageJobV2,
    SpeechStageResultV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.transcription.checkpointed import TranscriptionPlan

ORGANIZATION_ID = UUID("0192e8a0-0000-7000-8000-000000000001")
USER_ID = UUID("0192e8a0-0000-7000-8000-000000000002")
SOURCE_ID = UUID("0192e8a0-0000-7000-8000-000000000099")
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("20000000-0000-0000-0000-000000000001")
ATTEMPT_ID = UUID("30000000-0000-0000-0000-000000000001")
ARTIFACT_ID = UUID("40000000-0000-0000-0000-000000000001")
HANDLE = "modal-call-1"
TASK_ID = "modal-task-1"
PREFIX = f"org/{ORGANIZATION_ID}/source/{SOURCE_ID}/"


def lease_owner() -> str:
    return f"speech-v2:{RUN_ID}:semantic-key"


def frozen_plan() -> TranscriptionPlan:
    profile = SpeechResourceProfile()
    model_sha = "a" * 64
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech-v2",
        environment="test",
        protocol="temnia-speech/2",
        build="terminal-test",
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


def request() -> TranscribeInput:
    return TranscribeInput(
        scope=Scope(organizationId=ORGANIZATION_ID, userId=USER_ID),
        sourceId=SOURCE_ID,
        artifactPrefix=PREFIX,
        audioKey=f"{PREFIX}audio/audio.m4a",
        durationMs=1_000,
    )


def operation() -> ledger.Operation:
    return ledger.Operation(
        id=OPERATION_ID,
        run_id=RUN_ID,
        semantic_key="semantic-key",
        kind="recognize",
        stage="recognize",
        status="running",
        input_hash="e" * 64,
        config_hash="f" * 64,
        result_artifact_id=None,
    )


def attempt() -> ledger.Attempt:
    return ledger.Attempt(
        id=ATTEMPT_ID,
        operation_id=OPERATION_ID,
        run_id=RUN_ID,
        attempt_number=1,
        owner_token=lease_owner(),
        state="running",
        estimated_cost_micros=100,
        actual_cost_micros=None,
        cost_status="pending",
        remote_handle=HANDLE,
        dispatched_at=datetime.now(UTC),
        finished_at=None,
    )


def prepared(plan: TranscriptionPlan) -> PreparedStageV2:
    config = cast("RecognizeConfigV2", plan.recognize_v2)
    return PreparedStageV2(
        request=request(),
        plan=plan,
        run_id=RUN_ID,
        operation=operation(),
        configuration=config,
        input_stage=None,
        identity=ArtifactIdentity(kind="speech_checkpoint", fingerprint="9" * 64),
        owner=lease_owner(),
        usage={"estimatedMicros": 100, "estimate": {}},
        reservation=ledger.AttemptReservationRequest(
            operation_id=OPERATION_ID,
            owner_token=lease_owner(),
            provider="modal",
            model=config.model,
            family="whisperx",
            route={},
            request_hash="8" * 64,
            estimated_cost_micros=100,
            dispatch_limit=5,
        ),
    )


def exact_job(plan: TranscriptionPlan) -> SpeechStageJobV2:
    config = cast("RecognizeConfigV2", plan.recognize_v2)
    return SpeechStageJobV2(
        build=plan.build,
        operation_id=OPERATION_ID,
        attempt_id=ATTEMPT_ID,
        stage="recognize",
        artifact_prefix=PREFIX,
        audio_key=f"{PREFIX}audio/audio.m4a",
        audio_sha256=cast("str", plan.audio_sha256),
        audio_size_bytes=cast("int", plan.audio_size_bytes),
        duration_ms=1_000,
        checkpoint_key=f"{PREFIX}transcript/v2/checkpoints/recognize/{ATTEMPT_ID}.json",
        configuration=config,
        resource_profile=cast("SpeechResourceProfile", plan.resource_profile),
        model_manifest=cast("SpeechModelManifest", plan.model_manifest),
        execution_topology="parallel",
    )


def successful_result(
    plan: TranscriptionPlan,
) -> tuple[SpeechStageResultV2, bytes, bytes]:
    job = exact_job(plan)
    producer = ExecutionIdentity(
        modal_call_id=HANDLE,
        modal_task_id=TASK_ID,
        admission_key=admission_key_v2(job),
    )
    checkpoint = checkpoint_for_v2(
        job,
        plan.build,
        {"language": "en", "segments": []},
        producer,
    )
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    reference = artifact_ref_v2(
        job.checkpoint_key,
        job.stage,
        checkpoint.configuration_sha256,
        body,
    )
    admission = admission_for_v2(
        job,
        plan.build,
        modal_call_id=HANDLE,
        modal_task_id=TASK_ID,
    )
    admission_body = canonical_json(admission.model_dump(mode="json", by_alias=True))
    return (
        SpeechStageResultV2(
            build=plan.build,
            status="ok",
            operation_id=OPERATION_ID,
            attempt_id=ATTEMPT_ID,
            stage="recognize",
            modal_call_id=HANDLE,
            modal_task_id=TASK_ID,
            execution_identity=producer,
            checkpoint=reference,
            telemetry=StageTelemetry(complete=True),
            resource_profile=cast("SpeechResourceProfile", plan.resource_profile),
            model_manifest=cast("SpeechModelManifest", plan.model_manifest),
            execution_topology="parallel",
        ),
        body,
        admission_body,
    )


def failed_result(plan: TranscriptionPlan) -> SpeechStageResultV2:
    return SpeechStageResultV2(
        build=plan.build,
        status="failed",
        operation_id=OPERATION_ID,
        attempt_id=ATTEMPT_ID,
        stage="recognize",
        modal_call_id=HANDLE,
        modal_task_id=TASK_ID,
        error=StageError(
            type="InferenceFailure",
            message="inference failed",
            retry_class="terminal",
            inference_complete=False,
        ),
        telemetry=StageTelemetry(complete=True),
        resource_profile=cast("SpeechResourceProfile", plan.resource_profile),
        model_manifest=cast("SpeechModelManifest", plan.model_manifest),
        execution_topology="parallel",
    )


def accepted_artifact(result: SpeechStageResultV2) -> HarnessArtifact:
    reference = result.checkpoint
    assert reference is not None
    return HarnessArtifact(
        id=ARTIFACT_ID,
        organization_id=ORGANIZATION_ID,
        source_id=SOURCE_ID,
        kind="speech_checkpoint",
        fingerprint="9" * 64,
        storage_key=reference.key,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        metadata={},
        transcript_id=None,
        transcript_revision=None,
        dependency_ids=(),
    )


@pytest.mark.parametrize(
    ("pause_at", "expected_state"),
    [
        ("failed_result", "failed_known"),
        ("checkpoint_read", "succeeded"),
        ("artifact_acceptance", "succeeded"),
        ("complete_attempt", "succeeded"),
    ],
)
async def test_execute_stage_commits_terminal_accounting_before_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    pause_at: str,
    expected_state: str,
) -> None:
    plan = frozen_plan()
    job = exact_job(plan)
    success, body, admission_body = successful_result(plan)
    result = failed_result(plan) if pause_at == "failed_result" else success
    state = "running"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause(boundary: str) -> None:
        if pause_at == boundary:
            entered.set()
            await release.wait()

    async def mark_dispatched(*_args: object, **_kwargs: object) -> bool:
        return False

    async def poll(*_args: object, **_kwargs: object) -> SpeechStageResultV2:
        return result

    async def report(*_args: object, **_kwargs: object) -> None:
        return None

    async def fail(*_args: object, **_kwargs: object) -> ledger.Attempt:
        nonlocal state
        await pause("failed_result")
        state = "failed_known"
        return attempt()

    async def read(key: str) -> bytes:
        if key == job.checkpoint_key:
            await pause("checkpoint_read")
            return body
        if key == admission_key_v2(job):
            return admission_body
        message = f"unexpected checkpoint-store key: {key}"
        raise AssertionError(message)

    async def accept(*_args: object, **_kwargs: object) -> HarnessArtifact:
        await pause("artifact_acceptance")
        return accepted_artifact(success)

    async def complete(*_args: object, **_kwargs: object) -> ledger.Attempt:
        nonlocal state
        await pause("complete_attempt")
        state = "succeeded"
        return attempt()

    def client(_plan: TranscriptionPlan) -> object:
        return object()

    def checkpoint_store(_store: object) -> SimpleNamespace:
        return SimpleNamespace(read=read)

    instance = SpeechActivitiesV2.__new__(SpeechActivitiesV2)
    instance.ctx = cast(
        "Any",
        SimpleNamespace(
            settings=SimpleNamespace(database_url="unused"),
            store=object(),
        ),
    )
    monkeypatch.setattr(instance, "_client", client)
    monkeypatch.setattr(instance, "_poll_v2", poll)
    monkeypatch.setattr(activities_v2, "report_speech_progress", report)
    monkeypatch.setattr(ledger, "mark_dispatched", mark_dispatched)
    monkeypatch.setattr(ledger, "fail_attempt", fail)
    monkeypatch.setattr(ledger, "complete_attempt", complete)
    monkeypatch.setattr(
        activities_v2,
        "ObstoreCheckpointStore",
        checkpoint_store,
    )
    monkeypatch.setattr(artifacts, "accept_existing_json", accept)

    execution = asyncio.create_task(
        instance._execute_stage_v2(prepared(plan), attempt(), visible_progress=True)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    execution.cancel()
    await asyncio.sleep(0)
    assert not execution.done()
    assert state == "running"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert state == expected_state
