"""Actual Temporal checks for chapter operational resume and cancellation routing."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.contracts import (
        ChapterReviewAction,
        ChapterReviewInput,
        ChapterReviewOutput,
        ChapterRunInput,
        HarnessArtifactKind,
        HarnessArtifactRef,
        HarnessRunStatus,
        Scope,
        State,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.routes import RouteSnapshot
    from temnia_pipeline.harness.runtime_types import (
        CommitReviewMutationRequest,
        CommitReviewMutationResult,
        ExportRevisionResult,
        MarkRunFailedRequest,
        PinnedSource,
        PinnedTranscript,
        PreparedReviewMutation,
        RenderRevisionRequest,
        RenderRevisionResult,
        ReuseRevisionRenderOutcome,
        ReuseRevisionRenderRequest,
        RunRef,
        RunSnapshot,
    )
    from temnia_pipeline.harness.settings import HarnessSettings
    from temnia_pipeline.harness.workflows import ChapterReviewWorkflow

FIXTURE_DIR = Path(__file__).parent / "fixtures/harness"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


@workflow.defn(name="ChapterRunWorkflow")
class CaptureResumeWorkflow:
    """Test double for the child execution started by an operational command."""

    @workflow.run
    async def run(self, request: ChapterRunInput) -> None:
        await workflow.execute_activity(
            "capture_chapter_resume",
            request,
            start_to_close_timeout=timedelta(seconds=10),
        )


@workflow.defn
class LongRenderWorkflow:
    """Known active execution cancelled by the review workflow."""

    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity(
            "long_fake_chapter_render",
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=2),
        )


class OperationalActivities:
    def __init__(self, before: RunSnapshot) -> None:
        self.before = before
        self.after: RunSnapshot | None = None
        self.get_count = 0
        self.fenced = asyncio.Event()
        self.render_started = asyncio.Event()
        self.render_cancelled = asyncio.Event()
        self.resume_request: ChapterRunInput | None = None
        self.resume_captured = asyncio.Event()

    @activity.defn(name="get_chapter_run")
    async def get_run(self, _request: RunRef) -> RunSnapshot:
        self.get_count += 1
        if self.get_count > 1 and self.after is not None:
            return self.after
        return self.before

    @activity.defn(name="apply_chapter_review")
    async def apply_review(self, request: ChapterReviewInput) -> ChapterReviewOutput:
        self.fenced.set()
        return ChapterReviewOutput(
            message="operational command applied",
            mutationKey=request.mutationKey,
            revision=request.baseRevision or None,
            runId=request.runId,
            state=State.applied,
        )

    @activity.defn(name="capture_chapter_resume")
    async def capture_resume(self, request: ChapterRunInput) -> None:
        self.resume_request = request
        self.resume_captured.set()

    @activity.defn(name="long_fake_chapter_render")
    async def long_render(self) -> None:
        self.render_started.set()
        try:
            while True:
                activity.heartbeat("still rendering")
                await asyncio.sleep(0.05)
        finally:
            assert self.fenced.is_set()
            self.render_cancelled.set()

    @activity.defn(name="cleanup_chapter_source_cache")
    async def cleanup(self, _request: RunRef) -> bool:
        return True


def _artifact(kind: HarnessArtifactKind, suffix: int) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        fingerprint=f"{suffix:064x}",
        id=uuid.UUID(int=suffix),
        kind=kind,
        sha256=f"{suffix + 100:064x}",
        sizeBytes=2,
        storageKey=f"test/{suffix}.json",
    )


class AllDropReviewActivities:
    """A real Worker surface proving reasoned review skips render and model work."""

    def __init__(self) -> None:
        self.evidence = _artifact(HarnessArtifactKind.evidence, 10)
        self.edit = _artifact(HarnessArtifactKind.edit, 11)
        self.descriptor = _artifact(HarnessArtifactKind.render, 12)
        self.exported = asyncio.Event()

    @activity.defn(name="prepare_chapter_review")
    async def prepare(self, request: ChapterReviewInput) -> PreparedReviewMutation:
        assert request.reason.strip()
        return PreparedReviewMutation(
            request=request,
            candidate=self.edit,
            evidence=self.evidence,
            message="accepted deliberate all-drop revision",
        )

    @activity.defn(name="commit_chapter_review")
    async def commit(self, request: CommitReviewMutationRequest) -> CommitReviewMutationResult:
        prepared = request.prepared
        return CommitReviewMutationResult(
            output=ChapterReviewOutput(
                message="accepted deliberate all-drop revision",
                mutationKey=prepared.request.mutationKey,
                revision=2,
                runId=prepared.request.runId,
                state=State.applied,
            ),
            render=RenderRevisionRequest(
                run=RunRef(
                    scope_organization_id=SCOPE.organizationId,
                    scope_user_id=SCOPE.userId,
                    source_id=SOURCE_ID,
                    run_id=RUN_ID,
                ),
                edit=self.edit,
                revision=2,
            ),
        )

    @activity.defn(name="reuse_chapter_revision_render")
    async def reuse(self, request: ReuseRevisionRenderRequest) -> ReuseRevisionRenderOutcome:
        assert request.required
        assert request.predecessor_revision == 1
        return ReuseRevisionRenderOutcome(
            reused=RenderRevisionResult(
                descriptor=self.descriptor,
                has_kept_sections=False,
                technical_report=(),
                technical_passed=True,
            ),
            all_sections_accepted=True,
        )

    @activity.defn(name="export_chapter_revision")
    async def export(self, _request: object) -> ExportRevisionResult:
        self.exported.set()
        return ExportRevisionResult(artifact=_artifact(HarnessArtifactKind.export, 13), ready=True)


class KnownFailureActivities:
    """A terminal activity failure whose workflow must persist a visible state."""

    def __init__(self) -> None:
        self.marked: MarkRunFailedRequest | None = None
        self.marked_event = asyncio.Event()

    @activity.defn(name="prepare_chapter_review")
    async def prepare(self, _request: ChapterReviewInput) -> PreparedReviewMutation:
        message = "the bounded media operation failed"
        raise ApplicationError(
            message,
            type="KnownMediaFailure",
            non_retryable=True,
        )

    @activity.defn(name="mark_chapter_run_failed")
    async def mark(self, request: MarkRunFailedRequest) -> bool:
        self.marked = request
        self.marked_event.set()
        return True

    @activity.defn(name="cleanup_chapter_source_cache")
    async def cleanup(self, _request: RunRef) -> bool:
        return True


def _routes() -> RouteSnapshot:
    return RouteSnapshot.model_validate_json(
        (FIXTURE_DIR / "routes.synthetic.json").read_bytes(), strict=True
    )


def _settings(routes: RouteSnapshot) -> HarnessSettings:
    return HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(FIXTURE_DIR / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(FIXTURE_DIR / "routes.synthetic.json"),
        }
    )


def _snapshot(
    *,
    routes: RouteSnapshot,
    workflow_id: str,
    workflow_run_id: str,
    status: HarnessRunStatus,
) -> RunSnapshot:
    settings = _settings(routes)
    return RunSnapshot(
        accepted_revision=None,
        brief="Preserve the complete source.",
        budget_micros=2_000_000,
        config=settings.allowed_config(),
        current_revision=0,
        dispatch_count=1,
        error_message=None,
        evidence_artifact_id=None,
        id=RUN_ID,
        initial_budget_micros=1_000_000,
        repair_count=0,
        request_key=uuid.UUID("0192e8a0-0000-7000-8000-000000000444"),
        reserved_micros=0,
        route_snapshot=routes,
        source=PinnedSource(
            duration_ms=40_000,
            size_bytes=100,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/master.mp4",
        ),
        source_id=SOURCE_ID,
        spent_micros=10,
        stage="planning",
        status=status,
        transcript=PinnedTranscript(
            revision=1,
            sha256="b" * 64,
            size_bytes=100,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/transcript.json",
            transcript_id=uuid.UUID("0192e8a0-0000-7000-8000-000000000555"),
        ),
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )


def _command(action: ChapterReviewAction) -> ChapterReviewInput:
    return ChapterReviewInput(
        action=action,
        baseRevision=0,
        boundaryId=None,
        budgetMicros=3_000_000 if action == ChapterReviewAction.raise_budget else None,
        mutationKey=uuid.uuid4(),
        otherSectionId=None,
        reason="Operator requested this known-state transition.",
        runId=RUN_ID,
        scope=SCOPE,
        sectionId=None,
        sourceId=SOURCE_ID,
        targetRevision=None,
        targetTimeMs=None,
    )


async def _workers(
    stack: AsyncExitStack,
    environment: WorkflowEnvironment,
    queue: str,
    activities: OperationalActivities,
) -> None:
    await stack.enter_async_context(
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[ChapterReviewWorkflow, CaptureResumeWorkflow, LongRenderWorkflow],
            activities=[activities.capture_resume, activities.long_render, activities.cleanup],
        )
    )
    await stack.enter_async_context(
        Worker(
            environment.client,
            task_queue=control_task_queue(queue),
            activities=[activities.get_run, activities.apply_review],
        )
    )


async def test_raise_budget_starts_one_resume_with_original_immutable_request() -> None:
    routes = _routes()
    before = _snapshot(
        routes=routes,
        status=HarnessRunStatus.budget_paused,
        workflow_id="completed-original",
        workflow_run_id="completed-original-run",
    )
    activities = OperationalActivities(before)
    queue = f"chapter-operational-resume-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await _workers(stack, environment, queue, activities)
        result = await environment.client.execute_workflow(
            ChapterReviewWorkflow.run,
            _command(ChapterReviewAction.raise_budget),
            id=f"review-{uuid.uuid4()}",
            task_queue=queue,
        )
        assert result.state == State.applied
        await asyncio.wait_for(activities.resume_captured.wait(), timeout=10)
        resumed = activities.resume_request
        assert resumed is not None
        assert resumed.runId == before.id
        assert resumed.requestKey == before.request_key
        assert resumed.budgetMicros == before.initial_budget_micros
        assert resumed.config == before.config


async def test_cancel_fences_before_interrupting_the_known_running_execution() -> None:
    routes = _routes()
    queue = f"chapter-operational-cancel-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        target = await environment.client.start_workflow(
            LongRenderWorkflow.run,
            id=f"active-render-{uuid.uuid4()}",
            task_queue=queue,
        )
        description = await target.describe()
        before = _snapshot(
            routes=routes,
            status=HarnessRunStatus.running,
            workflow_id=target.id,
            workflow_run_id=description.run_id,
        )
        activities = OperationalActivities(before)
        await _workers(stack, environment, queue, activities)
        await asyncio.wait_for(activities.render_started.wait(), timeout=10)
        result = await environment.client.execute_workflow(
            ChapterReviewWorkflow.run,
            _command(ChapterReviewAction.cancel),
            id=f"review-{uuid.uuid4()}",
            task_queue=queue,
        )
        assert result.state == State.applied
        await asyncio.wait_for(activities.render_cancelled.wait(), timeout=10)
        assert activities.fenced.is_set()
        with pytest.raises(WorkflowFailureError):
            await target.result()


async def test_cancel_interrupts_a_pending_source_preparation_execution() -> None:
    routes = _routes()
    queue = f"chapter-operational-pending-cancel-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        target = await environment.client.start_workflow(
            LongRenderWorkflow.run,
            id=f"pending-source-preparation-{uuid.uuid4()}",
            task_queue=queue,
        )
        description = await target.describe()
        before = _snapshot(
            routes=routes,
            status=HarnessRunStatus.pending,
            workflow_id=target.id,
            workflow_run_id=description.run_id,
        )
        activities = OperationalActivities(before)
        await _workers(stack, environment, queue, activities)
        await asyncio.wait_for(activities.render_started.wait(), timeout=10)
        result = await environment.client.execute_workflow(
            ChapterReviewWorkflow.run,
            _command(ChapterReviewAction.cancel),
            id=f"review-{uuid.uuid4()}",
            task_queue=queue,
        )
        assert result.state == State.applied
        await asyncio.wait_for(activities.render_cancelled.wait(), timeout=10)
        assert activities.fenced.is_set()
        with pytest.raises(WorkflowFailureError):
            await target.result()


async def test_cancel_targets_the_owner_that_claimed_the_run_before_the_db_fence() -> None:
    routes = _routes()
    queue = f"chapter-operational-owner-race-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        target = await environment.client.start_workflow(
            LongRenderWorkflow.run,
            id=f"new-resume-owner-{uuid.uuid4()}",
            task_queue=queue,
        )
        description = await target.describe()
        before = _snapshot(
            routes=routes,
            status=HarnessRunStatus.needs_review,
            workflow_id="completed-old-owner",
            workflow_run_id="completed-old-owner-run",
        )
        activities = OperationalActivities(before)
        activities.after = _snapshot(
            routes=routes,
            status=HarnessRunStatus.cancelled,
            workflow_id=target.id,
            workflow_run_id=description.run_id,
        )
        await _workers(stack, environment, queue, activities)
        await asyncio.wait_for(activities.render_started.wait(), timeout=10)
        result = await environment.client.execute_workflow(
            ChapterReviewWorkflow.run,
            _command(ChapterReviewAction.cancel),
            id=f"review-{uuid.uuid4()}",
            task_queue=queue,
        )
        assert result.state == State.applied
        await asyncio.wait_for(activities.render_cancelled.wait(), timeout=10)
        with pytest.raises(WorkflowFailureError):
            await target.result()


async def test_reasoned_all_drop_exports_without_render_or_model_activity() -> None:
    activities = AllDropReviewActivities()
    queue = f"chapter-all-drop-review-{uuid.uuid4()}"
    command = _command(ChapterReviewAction.accept).model_copy(
        update={
            "baseRevision": 1,
            "reason": "The complete source is deliberate pre-roll with no publishable chapter.",
            "sectionId": "drop-0",
        }
    )
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterReviewWorkflow],
                activities=[activities.prepare, activities.reuse, activities.export],
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=[activities.commit],
            )
        )
        result = await environment.client.execute_workflow(
            ChapterReviewWorkflow.run,
            command,
            id=f"review-{uuid.uuid4()}",
            task_queue=queue,
        )
        assert result.state == State.applied
        assert result.revision == 2
        assert activities.exported.is_set()


async def test_known_activity_failure_persists_one_visible_failed_state() -> None:
    activities = KnownFailureActivities()
    queue = f"chapter-known-failure-{uuid.uuid4()}"
    command = _command(ChapterReviewAction.accept).model_copy(
        update={"baseRevision": 1, "sectionId": "keep-0"}
    )
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        AsyncExitStack() as stack,
    ):
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChapterReviewWorkflow],
                activities=[activities.prepare, activities.cleanup],
            )
        )
        await stack.enter_async_context(
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=[activities.mark],
            )
        )
        with pytest.raises(WorkflowFailureError):
            await environment.client.execute_workflow(
                ChapterReviewWorkflow.run,
                command,
                id=f"review-{uuid.uuid4()}",
                task_queue=queue,
            )
        await asyncio.wait_for(activities.marked_event.wait(), timeout=10)
        assert activities.marked is not None
        assert activities.marked.status == "failed"
        assert activities.marked.error_message == (
            "The chapter workflow stopped after a known activity failure."
        )
