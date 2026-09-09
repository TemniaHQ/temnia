"""Actual Temporal cancellation ordering for checkpointed speech."""

# ruff: noqa: PLR0915

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline.contracts import Scope, TranscribeInput
from temnia_pipeline.transcription.checkpointed import (
    CheckpointedTranscription,
    CoverageRecord,
    TranscriptionPlan,
)
from temnia_pipeline.workflows import TranscribeWorkflow

pytestmark = pytest.mark.timeout(30)


def _request() -> TranscribeInput:
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


def _plan() -> TranscriptionPlan:
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech",
        environment=None,
        protocol="temnia-speech/1",
        build="cancellation-test",
        budget_micros=6_500_000,
        rate_micros_per_hour=1_250_000,
        stage_timeout_seconds=3_600,
        startup_timeout_seconds=120,
        dispatch_limit=5,
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="b" * 64,
    )


async def test_external_cancel_waits_for_both_physical_activities_before_settlement() -> None:
    """Cancellation must not call the UI/ledger fences while GPU work can still write."""
    events: list[str] = []
    stages_started = asyncio.Event()
    coverage_started = asyncio.Event()

    @activity.defn(name="claim_transcription")
    async def claim(_request: TranscribeInput) -> int:
        return 1

    @activity.defn(name="choose_transcription_plan")
    async def choose(_request: TranscribeInput) -> TranscriptionPlan:
        return _plan()

    @activity.defn(name="checkpointed_transcribe")
    async def checkpointed(
        _request: TranscribeInput, _frozen: TranscriptionPlan
    ) -> CheckpointedTranscription:
        events.append("stages-started")
        stages_started.set()
        try:
            while True:
                activity.heartbeat("stages-live")
                await asyncio.sleep(0.01)
        finally:
            events.append("stages-cleanup-started")
            await asyncio.sleep(0.05)
            events.append("stages-cleanup-finished")

    @activity.defn(name="speech_coverage")
    async def coverage(_request: TranscribeInput, _frozen: TranscriptionPlan) -> CoverageRecord:
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
        assert "stages-cleanup-finished" in events
        assert "coverage-cleanup-finished" in events
        assert message == "The transcription was cancelled."
        events.append("transcript-failed")

    @activity.defn(name="settle_checkpointed_speech_run")
    async def settle(_request: TranscribeInput, outcome: str) -> bool:
        assert outcome == "cancelled"
        assert events[-1] == "transcript-failed"
        events.append("run-settled")
        return True

    queue = f"checkpointed-speech-cancel-{uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[TranscribeWorkflow],
            activities=[claim, choose, checkpointed, coverage, fail, settle],
        ),
    ):
        handle = await environment.client.start_workflow(
            TranscribeWorkflow.run,
            _request(),
            id=f"checkpointed-speech-cancel-{uuid4()}",
            task_queue=queue,
        )
        await asyncio.wait_for(stages_started.wait(), timeout=5)
        await asyncio.wait_for(coverage_started.wait(), timeout=5)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await handle.result()

    assert isinstance(caught.value.cause, CancelledError)
    assert events[-2:] == ["transcript-failed", "run-settled"]
    assert events.index("stages-cleanup-finished") < events.index("transcript-failed")
    assert events.index("coverage-cleanup-finished") < events.index("transcript-failed")
