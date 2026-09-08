from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from temnia_pipeline.contracts import (
    Scope,
    TranscribeInput,
    TranscribeOutput,
    TranscriptProvider,
)
from temnia_pipeline.settings import TranscriptionSettings
from temnia_pipeline.speech.activities import (
    remaining_attempt_seconds,
    result_identity_matches,
    semantic_stage_config,
)
from temnia_pipeline.speech.client import (
    SpeechDeploymentError,
    SpeechDeploymentIdentity,
    SpeechModalClient,
    assert_checkpointed_deployment,
    assert_frozen_deployment,
)
from temnia_pipeline.speech.contracts import (
    ArtifactRef,
    SpeechStageResult,
    Stage,
    StageError,
    StageTelemetry,
)
from temnia_pipeline.transcription import TranscribeRecord
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechStage,
    CheckpointedTranscription,
    CoverageRecord,
    TranscriptionPlan,
)
from temnia_pipeline.workflows import TranscribeWorkflow

HISTORY = Path(__file__).parent / "fixtures/workflow-history/transcribe-v4.json"


class IdentityClient:
    def __init__(self, protocol: str, build: str) -> None:
        self.identity = SpeechDeploymentIdentity(protocol=protocol, build=build)

    async def deployment_identity(self) -> SpeechDeploymentIdentity:
        return self.identity


def checkpointed_settings(expected_build: str | None = "expected") -> TranscriptionSettings:
    return TranscriptionSettings(
        provider="modal-checkpointed",
        modal_app="temnia-media",
        modal_environment=None,
        progress_dict="legacy-progress",
        recordings_dir=Path("recordings"),
        recording=None,
        speech_expected_build=expected_build,
    )


def plan() -> TranscriptionPlan:
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech",
        environment=None,
        protocol="temnia-speech/1",
        build="expected",
        budget_micros=6_500_000,
        rate_micros_per_hour=1_250_000,
        stage_timeout_seconds=3600,
        startup_timeout_seconds=120,
        dispatch_limit=5,
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="b" * 64,
    )


def test_mutable_model_alias_cache_identity_is_scoped_to_one_run() -> None:
    first = semantic_stage_config(
        plan().model_copy(),
        plan().recognize,
        cache_scope_run_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    second = semantic_stage_config(
        plan().model_copy(),
        plan().recognize,
        cache_scope_run_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    assert first != second
    assert first["modelIdentityStatus"] == "unresolved_mutable_alias"


async def test_protocol4_history_replays_after_checkpoint_patch() -> None:
    history = WorkflowHistory.from_json(
        "old-transcribe-history-v1", HISTORY.read_text(encoding="utf-8")
    )
    await Replayer(
        workflows=[TranscribeWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_new_checkpointed_history_uses_small_refs_and_parallel_activities() -> None:  # noqa: C901
    calls: list[str] = []
    scope = Scope(
        organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
        userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
    )
    source_id = UUID("0192e8a0-0000-7000-8000-000000000099")
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    request = TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1_000,
    )
    output = TranscribeOutput(
        durationMs=1_000,
        language="en",
        organizationId=scope.organizationId,
        provider=TranscriptProvider(name="whisperx", model="large-v3", version="3.8.6"),
        revision=1,
        sourceId=source_id,
        speakerCount=1,
        storageKey=f"{prefix}transcript/rev-1.json",
        wordCount=1,
    )

    def accepted(stage: str, number: int) -> AcceptedSpeechStage:
        stage_value = cast("Stage", stage)
        artifact_id = UUID(f"00000000-0000-0000-0000-{number:012d}")
        return AcceptedSpeechStage(
            stage=stage_value,
            artifact_id=artifact_id,
            checkpoint=ArtifactRef(
                key=f"{prefix}transcript/checkpoints/{stage}/{number}.json",
                sha256=f"{number:x}".rjust(64, "0"),
                size_bytes=100,
                stage=stage_value,
                configuration_sha256="f" * 64,
            ),
            operation_id=UUID(f"10000000-0000-0000-0000-{number:012d}"),
            attempt_id=UUID(f"20000000-0000-0000-0000-{number:012d}"),
            call_id=f"call-{number}",
        )

    stages = CheckpointedTranscription(
        run_id=UUID("30000000-0000-0000-0000-000000000001"),
        recognize=accepted("recognize", 1),
        align=accepted("align", 2),
        diarize=accepted("diarize", 3),
    )

    @activity.defn(name="claim_transcription")
    async def claim(_request: TranscribeInput) -> int:
        return 1

    @activity.defn(name="choose_transcription_plan")
    async def choose(_request: TranscribeInput) -> TranscriptionPlan:
        calls.append("choose")
        return plan()

    @activity.defn(name="speech_coverage")
    async def coverage(_request: TranscribeInput, _plan: TranscriptionPlan) -> CoverageRecord:
        calls.append("coverage")
        return CoverageRecord(error="detector unavailable")

    @activity.defn(name="mark_speech_coverage_progress")
    async def mark_coverage(_request: TranscribeInput) -> None:
        calls.append("coverage-visible")

    @activity.defn(name="checkpointed_transcribe")
    async def transcribe(
        _request: TranscribeInput, _plan: TranscriptionPlan
    ) -> CheckpointedTranscription:
        calls.append("stages")
        return stages

    @activity.defn(name="assemble_checkpointed_transcript")
    async def assemble(
        _request: TranscribeInput,
        attempt: int,
        _plan: TranscriptionPlan,
        _stages: CheckpointedTranscription,
        _coverage: CoverageRecord,
    ) -> TranscribeRecord:
        assert {"coverage", "stages"}.issubset(calls)
        calls.append("assemble")
        return TranscribeRecord(
            raw_key=f"{prefix}transcript/checkpointed-raw.json",
            language="en",
            gpu_seconds=1,
            gpu="L4",
            attempt=attempt,
        )

    @activity.defn(name="write_revision")
    async def write(_request: TranscribeInput, _record: TranscribeRecord) -> TranscribeOutput:
        return output

    @activity.defn(name="finalize_transcription")
    async def finalize(
        _request: TranscribeInput,
        _record: TranscribeRecord,
        _written: TranscribeOutput,
    ) -> TranscribeOutput:
        return output

    @activity.defn(name="fail_transcription")
    async def fail(_request: TranscribeInput, _message: str) -> None:
        return None

    @activity.defn(name="settle_checkpointed_speech_run")
    async def settle(_request: TranscribeInput, outcome: str) -> bool:
        calls.append(f"settle:{outcome}")
        return True

    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue="checkpointed-speech-workflow-test",
            workflows=[TranscribeWorkflow],
            activities=[
                claim,
                choose,
                coverage,
                mark_coverage,
                transcribe,
                assemble,
                write,
                finalize,
                fail,
                settle,
            ],
        ),
    ):
        result = await environment.client.execute_workflow(
            TranscribeWorkflow.run,
            request,
            id="checkpointed-speech-workflow-test",
            task_queue="checkpointed-speech-workflow-test",
        )
    assert result == output
    assert calls[0] == "choose"
    assert calls[-1] == "settle:ready"


async def test_boot_and_predispatch_probe_require_exact_build() -> None:
    settings = checkpointed_settings()
    matching = cast("SpeechModalClient", IdentityClient("temnia-speech/1", "expected"))
    identity = await assert_checkpointed_deployment(matching, settings)
    assert identity.build == "expected"

    changed = cast("SpeechModalClient", IdentityClient("temnia-speech/1", "new-build"))
    with pytest.raises(SpeechDeploymentError, match="worker requires"):
        await assert_checkpointed_deployment(changed, settings)
    with pytest.raises(SpeechDeploymentError, match="changed before dispatch"):
        await assert_frozen_deployment(
            changed,
            app="temnia-speech",
            protocol="temnia-speech/1",
            build="expected",
        )


def test_failure_identity_is_checked_before_oom_classification() -> None:
    operation_id = UUID("11111111-1111-1111-1111-111111111111")
    attempt_id = UUID("22222222-2222-2222-2222-222222222222")
    failed = SpeechStageResult(
        build="wrong-build",
        status="failed",
        operation_id=operation_id,
        attempt_id=attempt_id,
        stage="recognize",
        modal_call_id="call-1",
        error=StageError(
            type="OutOfMemoryError",
            message="CUDA out of memory",
            retry_class="resource_oom",
        ),
        telemetry=StageTelemetry(),
    )
    assert not result_identity_matches(
        failed,
        plan=plan(),
        operation_id=operation_id,
        attempt_id=attempt_id,
        stage="recognize",
        call_id="call-1",
    )


def test_reattach_uses_original_dispatch_deadline() -> None:
    dispatched = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    assert (
        remaining_attempt_seconds(
            dispatched,
            3600,
            now=dispatched + timedelta(seconds=3590),
        )
        == 10
    )
    assert (
        remaining_attempt_seconds(
            dispatched,
            3600,
            now=dispatched + timedelta(seconds=3610),
        )
        == -10
    )
