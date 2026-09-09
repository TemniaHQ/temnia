"""Real Temporal and PostgreSQL evidence for the v2 activity heartbeat tail."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from obstore.store import MemoryStore
from pydantic import BaseModel, ConfigDict
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import TranscribeInput
from temnia_pipeline.ingest import Context
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import (
    PipelineSettings,
    StorageSettings,
    TranscodeSettings,
    TranscriptionSettings,
)
from temnia_pipeline.speech.activities_v2 import SpeechActivitiesV2
from temnia_pipeline.speech.checkpoints_v2 import (
    admission_for_v2,
    admission_key_v2,
    artifact_ref_v2,
    checkpoint_for_v2,
)
from temnia_pipeline.speech.client import CallFinished, SpeechDeploymentIdentity
from temnia_pipeline.speech.contracts import ExecutionIdentity, StageTelemetry, canonical_json
from temnia_pipeline.speech.contracts_v2 import (
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    SpeechStageJobV2,
    SpeechStageResultV2,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.transcription.checkpointed import (
    AcceptedSpeechAssignment,
    AcceptedSpeechStageV2,
    ParallelCheckpointedTranscription,
    TranscriptionPlan,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class LivenessInput(BaseModel):
    """One typed workflow input for the isolated activity test."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: TranscribeInput
    plan: TranscriptionPlan


@workflow.defn(name="SpeechV2LivenessWorkflow", sandboxed=False)
class LivenessWorkflow:
    """Invoke the production v2 activity under its production timeout shape."""

    @workflow.run
    async def run(self, value: LivenessInput) -> ParallelCheckpointedTranscription:
        return await workflow.execute_activity(
            "checkpointed_transcribe_v2",
            args=[value.request, value.plan],
            result_type=ParallelCheckpointedTranscription,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=30), maximum_attempts=2),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: liveness persistence cannot skip")
    return url


@pytest.fixture(autouse=True)
async def close_database_pool() -> AsyncIterator[None]:
    """Close this test's process-global pool before its event loop is torn down."""
    try:
        yield
    finally:
        await db.close_pool()


def profile() -> SpeechResourceProfile:
    return SpeechResourceProfile(stage_timeout_seconds=900)


def manifest() -> SpeechModelManifest:
    digest = "a" * 64
    return SpeechModelManifest(
        sha256=digest,
        file_count=3,
        total_bytes=4096,
        model_root=f"/models/frozen/{digest}",
        image_assets_sha256="b" * 64,
    )


def plan() -> TranscriptionPlan:
    frozen_profile = profile()
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech-v2-liveness",
        environment="test",
        protocol="temnia-speech/2",
        build="c" * 64,
        budget_micros=2_000_000,
        rate_micros_per_hour=frozen_profile.minimum_rate_micros_per_hour,
        stage_timeout_seconds=900,
        startup_timeout_seconds=120,
        dispatch_limit=3,
        audio_sha256="d" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="e" * 64,
        resource_profile=frozen_profile,
        model_manifest=manifest(),
        execution_topology="serial",
        recognize_v2=RecognizeConfigV2(),
        speaker_turns=SpeakerTurnsConfig(),
        allow_oom_recovery=False,
    )


def context(url: str, store: MemoryStore, tmp_path: Path) -> Context:
    frozen_profile = profile()
    transcription = TranscriptionSettings(
        provider="modal-checkpointed",
        modal_app="temnia-media",
        modal_environment="test",
        progress_dict="unused",
        recordings_dir=tmp_path,
        recording=None,
        speech_modal_app="temnia-speech-v2-liveness",
        speech_protocol="temnia-speech/2",
        speech_expected_build="c" * 64,
        speech_rate_micros_per_hour=frozen_profile.minimum_rate_micros_per_hour,
        speech_resource_profile=frozen_profile,
        speech_model_manifest=manifest(),
        speech_execution_topology="serial",
        speech_stage_timeout_seconds=900,
        speech_startup_timeout_seconds=120,
        speech_dispatch_limit=3,
    )
    settings = PipelineSettings(
        database_url=url,
        work_root=tmp_path,
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        transcode=TranscodeSettings(
            backend="local",
            modal_app="temnia-media",
            modal_environment="test",
            progress_dict="unused",
        ),
        transcription=transcription,
    )
    return Context(
        settings=settings,
        storage=StorageSettings.from_env(),
        store=cast("Any", store),
        transcoder=cast("Any", object()),
        transcription=cast("Any", object()),
    )


async def seed_source(url: str, value: TranscribeInput) -> None:
    async with db.scoped(url, value.scope) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (value.scope.organizationId, f"speech-liveness-{value.sourceId}"),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status)
            VALUES (%s, %s, %s, 'speech liveness', 'speech.m4a', 'audio/mp4', 123, %s, 'ready')
            """,
            (
                value.sourceId,
                value.scope.organizationId,
                project["id"],
                f"{value.artifactPrefix}master/speech.m4a",
            ),
        )


class FinishedSpeechClient:
    """Write exact fake provider results while counting physical calls."""

    def __init__(self, store: MemoryStore, frozen_plan: TranscriptionPlan) -> None:
        self.store = store
        self.plan = frozen_plan
        self.results: dict[str, SpeechStageResultV2] = {}
        self.spawn_count = 0

    async def deployment_identity(self) -> SpeechDeploymentIdentity:
        return SpeechDeploymentIdentity(
            protocol=self.plan.protocol,
            build=self.plan.build,
            resource_profile=self.plan.resource_profile,
            model_manifest=self.plan.model_manifest,
        )

    async def spawn(self, stage: StageV2, job: SpeechStageJobV2) -> str:
        self.spawn_count += 1
        handle = f"call-{self.spawn_count}-{stage}"
        task_id = f"task-{self.spawn_count}-{stage}"
        execution = ExecutionIdentity(
            modal_call_id=handle,
            modal_task_id=task_id,
            admission_key=admission_key_v2(job),
        )
        payload: dict[str, object]
        if stage == "recognize":
            payload = {"language": "en", "segments": []}
        elif stage == "align":
            payload = {
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": "hello",
                        "words": [{"word": "hello", "start": 0.0, "end": 1.0, "score": 0.9}],
                    }
                ],
            }
        else:
            payload = {"diarization": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]}
        admission = admission_for_v2(
            job,
            self.plan.build,
            modal_call_id=handle,
            modal_task_id=task_id,
        )
        admission_body = canonical_json(admission.model_dump(mode="json", by_alias=True))
        checkpoint = checkpoint_for_v2(job, self.plan.build, payload, execution)
        checkpoint_body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
        await storage.upload_bytes(
            cast("Any", self.store), admission_key_v2(job), admission_body, "application/json"
        )
        await storage.upload_bytes(
            cast("Any", self.store), job.checkpoint_key, checkpoint_body, "application/json"
        )
        self.results[handle] = SpeechStageResultV2(
            build=self.plan.build,
            status="ok",
            operation_id=job.operation_id,
            attempt_id=job.attempt_id,
            stage=stage,
            modal_call_id=handle,
            modal_task_id=task_id,
            execution_identity=execution,
            checkpoint=artifact_ref_v2(
                job.checkpoint_key,
                stage,
                checkpoint.configuration_sha256,
                checkpoint_body,
            ),
            telemetry=StageTelemetry(complete=True),
            resource_profile=job.resource_profile,
            model_manifest=job.model_manifest,
            execution_topology=job.execution_topology,
        )
        return handle

    async def status(self, handle: str) -> CallFinished:
        return CallFinished(self.results[handle])

    async def progress(self, _attempt_id: str) -> None:
        return None

    async def cancel(self, _handle: str) -> None:
        return None


class SlowTailSpeechActivities(SpeechActivitiesV2):
    """Pause only after all provider stages and before assignment publication."""

    def __init__(self, ctx: Context, client: FinishedSpeechClient, *, block: bool) -> None:
        super().__init__(ctx, lambda _settings: cast("Any", client))
        self.tail_started = asyncio.Event()
        self.tail_cleaned = asyncio.Event()
        self.release_tail = asyncio.Event()
        self.block = block
        self.activity_attempts: list[int] = []

    async def _checkpointed_transcribe_v2(
        self, request: TranscribeInput, plan: TranscriptionPlan
    ) -> ParallelCheckpointedTranscription:
        self.activity_attempts.append(activity.info().attempt)
        return await super()._checkpointed_transcribe_v2(request, plan)

    async def _assignment_v2(  # noqa: PLR0913, PLR0917
        self,
        request: TranscribeInput,
        plan: TranscriptionPlan,
        run_id: UUID,
        recognized: AcceptedSpeechStageV2,
        aligned: AcceptedSpeechStageV2,
        turns: AcceptedSpeechStageV2,
    ) -> AcceptedSpeechAssignment:
        self.tail_started.set()
        try:
            if self.block:
                await self.release_tail.wait()
            else:
                await asyncio.sleep(12.0)
            return await super()._assignment_v2(request, plan, run_id, recognized, aligned, turns)
        except asyncio.CancelledError:
            if self.block:
                # Longer than the test heartbeat timeout: keep liveness active
                # until this owned cleanup acknowledges cancellation.
                await asyncio.sleep(12.0)
            raise
        finally:
            self.tail_cleaned.set()


async def request_and_seed(url: str) -> TranscribeInput:
    scope = resolve_scope()
    source_id = uuid.uuid4()
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    value = TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1_000,
    )
    await seed_source(url, value)
    return value


async def test_quiet_post_gpu_tail_keeps_one_activity_attempt_and_three_calls(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    frozen_plan = plan()
    store = MemoryStore()
    request = await request_and_seed(url)
    client = FinishedSpeechClient(store, frozen_plan)
    activities = SlowTailSpeechActivities(context(url, store, tmp_path), client, block=False)
    queue = f"speech-v2-liveness-{uuid.uuid4()}"
    workflow_id = f"speech-v2-liveness-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[LivenessWorkflow],
            activities=[activities.checkpointed_transcribe_v2],
        ),
    ):
        result = await environment.client.execute_workflow(
            LivenessWorkflow.run,
            LivenessInput(request=request, plan=frozen_plan),
            id=workflow_id,
            task_queue=queue,
        )
        history = await environment.client.get_workflow_handle(workflow_id).fetch_history()
    starts = [
        event.activity_task_started_event_attributes.attempt
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
    ]
    assert result.assignment.artifact.size_bytes > 0
    assert client.spawn_count == 3
    assert starts == [1]
    async with db.scoped(url, request.scope) as conn:
        facts = await (
            await conn.execute(
                """
                SELECT count(*)::int AS attempts,
                       count(*) FILTER (WHERE state = 'succeeded')::int AS succeeded
                  FROM harness_attempt a JOIN harness_run r ON r.id = a.run_id
                 WHERE r.source_id = %s
                """,
                (request.sourceId,),
            )
        ).fetchone()
    assert facts == {"attempts": 3, "succeeded": 3}


async def test_cancel_during_post_gpu_tail_heartbeats_until_cleanup_and_never_publishes(
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    frozen_plan = plan()
    store = MemoryStore()
    request = await request_and_seed(url)
    client = FinishedSpeechClient(store, frozen_plan)
    activities = SlowTailSpeechActivities(context(url, store, tmp_path), client, block=True)
    queue = f"speech-v2-liveness-cancel-{uuid.uuid4()}"
    workflow_id = f"speech-v2-liveness-cancel-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[LivenessWorkflow],
            activities=[activities.checkpointed_transcribe_v2],
        ),
    ):
        handle = await environment.client.start_workflow(
            LivenessWorkflow.run,
            LivenessInput(request=request, plan=frozen_plan),
            id=workflow_id,
            task_queue=queue,
        )
        await asyncio.wait_for(activities.tail_started.wait(), timeout=5)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await handle.result()
        history = await handle.fetch_history()
    assert isinstance(caught.value.cause, CancelledError)
    assert activities.tail_cleaned.is_set()
    assert client.spawn_count == 3
    assert activities.activity_attempts == [1]
    assert any(
        event.HasField("activity_task_canceled_event_attributes") for event in history.events
    )
    assert not any(
        event.HasField("activity_task_timed_out_event_attributes") for event in history.events
    )
    async with db.scoped(url, request.scope) as conn:
        assignment = await (
            await conn.execute(
                """
                SELECT count(*)::int AS count FROM harness_artifact
                 WHERE source_id = %s AND kind = 'speech_assignment'
                """,
                (request.sourceId,),
            )
        ).fetchone()
        attempts = await (
            await conn.execute(
                """
                SELECT count(*)::int AS count
                  FROM harness_attempt a JOIN harness_run r ON r.id = a.run_id
                 WHERE r.source_id = %s AND a.state = 'succeeded'
                """,
                (request.sourceId,),
            )
        ).fetchone()
    assert assignment == {"count": 0}
    assert attempts == {"count": 3}
