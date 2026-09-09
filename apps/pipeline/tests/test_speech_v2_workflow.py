"""Actual Temporal coverage for protocol-v2 checkpointed speech."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from temnia_pipeline.contracts import Scope, TranscribeInput, TranscribeOutput, TranscriptProvider
from temnia_pipeline.speech.contracts_v2 import (
    ArtifactRefV2,
    AssignmentArtifactRef,
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.transcription import TranscribeRecord
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechAssignment,
    AcceptedSpeechStageV2,
    CoverageRecord,
    ParallelCheckpointedTranscription,
    TranscriptionPlan,
)
from temnia_pipeline.workflows import TranscribeWorkflow

pytestmark = pytest.mark.timeout(30)

V1_HISTORY = Path(__file__).parent / "fixtures/workflow-history/transcribe-v4.json"


def request() -> TranscribeInput:
    scope = Scope(
        organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
        userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
    )
    source_id = UUID("0192e8a0-0000-7000-8000-000000000099")
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    return TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1_000,
    )


def plan() -> TranscriptionPlan:
    profile = SpeechResourceProfile()
    model_sha = "a" * 64
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech-v2",
        environment="test",
        protocol="temnia-speech/2",
        build="v2-workflow-test",
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


def stage_ref(stage: str, number: int, prefix: str) -> AcceptedSpeechStageV2:
    stage_value = cast("StageV2", stage)
    checkpoint = ArtifactRefV2(
        key=f"{prefix}transcript/v2/checkpoints/{stage}/{number}.json",
        sha256=f"{number:x}".rjust(64, "0"),
        size_bytes=100,
        stage=stage_value,
        configuration_sha256="e" * 64,
    )
    return AcceptedSpeechStageV2(
        stage=stage_value,
        artifact_id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
        checkpoint=checkpoint,
        operation_id=UUID(f"10000000-0000-0000-0000-{number:012d}"),
        attempt_id=UUID(f"20000000-0000-0000-0000-{number:012d}"),
        call_id=f"call-{number}",
    )


def completed_stages(value: TranscribeInput) -> ParallelCheckpointedTranscription:
    assignment = AssignmentArtifactRef(
        key=f"{value.artifactPrefix}transcript/v2/assignment.json",
        sha256="f" * 64,
        size_bytes=100,
        configuration_sha256="1" * 64,
    )
    return ParallelCheckpointedTranscription(
        run_id=UUID("30000000-0000-0000-0000-000000000001"),
        recognize=stage_ref("recognize", 1, value.artifactPrefix),
        align=stage_ref("align", 2, value.artifactPrefix),
        speaker_turns=stage_ref("speaker_turns", 3, value.artifactPrefix),
        assignment=AcceptedSpeechAssignment(
            artifact_id=UUID("40000000-0000-0000-0000-000000000001"),
            artifact=assignment,
            operation_id=UUID("50000000-0000-0000-0000-000000000001"),
        ),
    )


def output(value: TranscribeInput) -> TranscribeOutput:
    return TranscribeOutput(
        durationMs=value.durationMs,
        language="en",
        organizationId=value.scope.organizationId,
        provider=TranscriptProvider(name="whisperx", model="large-v3", version="3.8.6"),
        revision=1,
        sourceId=value.sourceId,
        speakerCount=1,
        storageKey=f"{value.artifactPrefix}transcript/rev-1.json",
        wordCount=1,
    )


async def test_v2_workflow_selects_parallel_stages_and_v2_assembly() -> None:
    value = request()
    expected_stages = completed_stages(value)
    expected_output = output(value)
    calls: list[str] = []

    @activity.defn(name="claim_transcription")
    async def claim(_request: TranscribeInput) -> int:
        return 1

    @activity.defn(name="choose_transcription_plan")
    async def choose(_request: TranscribeInput) -> TranscriptionPlan:
        return plan()

    @activity.defn(name="speech_coverage")
    async def coverage(_request: TranscribeInput, frozen: TranscriptionPlan) -> CoverageRecord:
        assert frozen.protocol == "temnia-speech/2"
        calls.append("coverage")
        return CoverageRecord(error="detector unavailable")

    @activity.defn(name="checkpointed_transcribe_v2")
    async def transcribe(
        _request: TranscribeInput, frozen: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        assert frozen.execution_topology == "parallel"
        calls.append("stages-v2")
        return expected_stages

    @activity.defn(name="mark_speech_coverage_progress")
    async def mark_coverage(_request: TranscribeInput) -> None:
        calls.append("coverage-visible")

    @activity.defn(name="assemble_checkpointed_transcript_v2")
    async def assemble(
        _request: TranscribeInput,
        attempt: int,
        frozen: TranscriptionPlan,
        stages: ParallelCheckpointedTranscription,
        _coverage: CoverageRecord,
    ) -> TranscribeRecord:
        assert attempt == 1
        assert frozen.protocol == "temnia-speech/2"
        assert stages == expected_stages
        assert {"coverage", "stages-v2", "coverage-visible"}.issubset(calls)
        calls.append("assemble-v2")
        return TranscribeRecord(
            raw_key=f"{value.artifactPrefix}transcript/v2-raw.json",
            language="en",
            gpu_seconds=1,
            gpu="L4",
            attempt=attempt,
        )

    @activity.defn(name="write_revision")
    async def write(_request: TranscribeInput, _record: TranscribeRecord) -> TranscribeOutput:
        return expected_output

    @activity.defn(name="finalize_transcription")
    async def finalize(
        _request: TranscribeInput,
        _record: TranscribeRecord,
        _written: TranscribeOutput,
    ) -> TranscribeOutput:
        return expected_output

    @activity.defn(name="settle_checkpointed_speech_run")
    async def settle(_request: TranscribeInput, outcome: str) -> bool:
        calls.append(f"settle:{outcome}")
        return True

    queue = f"speech-v2-workflow-{uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[TranscribeWorkflow],
            activities=[
                claim,
                choose,
                coverage,
                transcribe,
                mark_coverage,
                assemble,
                write,
                finalize,
                settle,
            ],
        ),
    ):
        result = await environment.client.execute_workflow(
            TranscribeWorkflow.run,
            value,
            id=f"speech-v2-workflow-{uuid4()}",
            task_queue=queue,
        )

    assert result == expected_output
    assert "stages-v2" in calls
    assert "assemble-v2" in calls
    assert calls[-1] == "settle:ready"


async def test_v2_cancel_waits_for_both_gpu_branch_finalizers_and_coverage() -> None:  # noqa: C901, PLR0915
    events: list[str] = []
    gpu_branches_started = asyncio.Event()
    coverage_started = asyncio.Event()

    @activity.defn(name="claim_transcription")
    async def claim(_request: TranscribeInput) -> int:
        return 1

    @activity.defn(name="choose_transcription_plan")
    async def choose(_request: TranscribeInput) -> TranscriptionPlan:
        return plan()

    async def gpu_branch(name: str, both_started: asyncio.Event) -> None:
        events.append(f"{name}-started")
        if {"recognize-started", "speaker-turns-started"}.issubset(events):
            both_started.set()
        cancelled = asyncio.Event()
        try:
            await cancelled.wait()
        finally:
            events.append(f"{name}-cleanup-started")
            await asyncio.sleep(0.05)
            events.append(f"{name}-cleanup-finished")

    @activity.defn(name="checkpointed_transcribe_v2")
    async def transcribe(
        _request: TranscribeInput, _plan: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        branches = (
            asyncio.create_task(gpu_branch("recognize", gpu_branches_started)),
            asyncio.create_task(gpu_branch("speaker-turns", gpu_branches_started)),
        )
        try:
            while True:
                activity.heartbeat("v2-gpu-branches-live")
                await asyncio.sleep(0.01)
        finally:
            for branch in branches:
                if not branch.done():
                    branch.cancel()
            await asyncio.gather(*branches, return_exceptions=True)
            events.append("gpu-activity-finished")
        message = "unreachable"
        raise AssertionError(message)

    @activity.defn(name="speech_coverage")
    async def coverage(_request: TranscribeInput, _plan: TranscriptionPlan) -> CoverageRecord:
        events.append("coverage-started")
        coverage_started.set()
        try:
            while True:
                activity.heartbeat("coverage-live")
                await asyncio.sleep(0.01)
        finally:
            events.append("coverage-cleanup-started")
            await asyncio.sleep(0.05)
            events.append("coverage-cleanup-finished")

    @activity.defn(name="fail_transcription")
    async def fail(_request: TranscribeInput, message: str) -> None:
        assert message == "The transcription was cancelled."
        assert "recognize-cleanup-finished" in events
        assert "speaker-turns-cleanup-finished" in events
        assert "gpu-activity-finished" in events
        assert "coverage-cleanup-finished" in events
        events.append("transcript-failed")

    @activity.defn(name="settle_checkpointed_speech_run")
    async def settle(_request: TranscribeInput, outcome: str) -> bool:
        assert outcome == "cancelled"
        assert events[-1] == "transcript-failed"
        events.append("run-settled")
        return True

    queue = f"speech-v2-cancel-{uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[TranscribeWorkflow],
            activities=[claim, choose, transcribe, coverage, fail, settle],
        ),
    ):
        handle = await environment.client.start_workflow(
            TranscribeWorkflow.run,
            request(),
            id=f"speech-v2-cancel-{uuid4()}",
            task_queue=queue,
        )
        await asyncio.wait_for(gpu_branches_started.wait(), timeout=5)
        await asyncio.wait_for(coverage_started.wait(), timeout=5)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await handle.result()

    assert isinstance(caught.value.cause, CancelledError)
    assert events[-2:] == ["transcript-failed", "run-settled"]


async def test_protocol_v1_history_still_replays_with_v2_selection_code() -> None:
    history = WorkflowHistory.from_json(
        "old-transcribe-history-v1", V1_HISTORY.read_text(encoding="utf-8")
    )
    await Replayer(
        workflows=[TranscribeWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
