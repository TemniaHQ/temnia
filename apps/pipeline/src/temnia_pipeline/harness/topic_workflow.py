"""Durable standalone-topic program: plan, cold/source review, grounded repair, render."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        HarnessRunStatus,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.run_failures import known_failure_details
    from temnia_pipeline.harness.runtime_types import (
        MarkRunFailedRequest,
        RenderRevisionRequest,
        RunRef,
        RunSnapshot,
        StageUpdate,
        WorkflowIdentity,
    )
    from temnia_pipeline.harness.topic_runtime import (
        TopicRenderResult,
    )

RETRY = RetryPolicy(maximum_attempts=3)


class TopicRunWorkflow(PydanticAIWorkflow):
    """The shared shell of a topic run: failure fences, finish and render; subclasses program."""

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Execute the finite topic program while preserving unknown-outcome fences."""
        try:
            return await self.program(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status, message = known_failure_details(error)
            info = workflow.info()
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "mark_chapter_run_failed",
                    MarkRunFailedRequest(
                        run=self.ref(request),
                        workflow=WorkflowIdentity(
                            workflow_id=info.workflow_id, workflow_run_id=info.run_id
                        ),
                        error_message=message,
                        status=status,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    task_queue=control_task_queue(info.task_queue),
                    result_type=bool,
                )
            raise
        finally:
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "cleanup_chapter_source_cache",
                    self.ref(request),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    result_type=bool,
                )

    async def program(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Subclasses run the editorial program; the base owns fences, finish and render."""
        raise NotImplementedError

    @staticmethod
    def ref(request: ChapterRunInput) -> RunRef:
        """Build the scoped durable run reference."""
        return RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        )

    async def finish(
        self,
        request: ChapterRunInput,
        *,
        evidence: HarnessArtifactRef,
        edit: HarnessArtifactRef | None,
        revision: int,
        message: str | None,
    ) -> ChapterRunOutput:
        """End with reviewable output without inventing human acceptance."""
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="render" if revision else "planning",
                expected_revision=revision,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=message,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control_task_queue(workflow.info().task_queue),
            result_type=RunSnapshot,
        )
        return ChapterRunOutput(
            runId=request.runId,
            revision=revision or None,
            status=run.status,
            evidenceArtifact=evidence,
            editArtifact=edit,
            errorMessage=message,
        )

    async def render(
        self,
        request: ChapterRunInput,
        *,
        evidence: HarnessArtifactRef,
        edit: HarnessArtifactRef,
        revision: int,
        reasons: tuple[str, ...] = (),
    ) -> ChapterRunOutput:
        """Render current independent videos and retain their technical outcome."""
        rendered = await workflow.execute_activity(
            "render_topic_revision",
            RenderRevisionRequest(run=self.ref(request), edit=edit, revision=revision),
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=TopicRenderResult,
        )
        message = "; ".join(reasons) or None
        if not rendered.count:
            message = message or (
                "No independently usable video was compiled. "
                "Review the retained proposal and findings."
            )
        elif not rendered.technical_passed:
            message = "; ".join(
                filter(None, (message, "Some rendered videos need technical review."))
            )
        return await self.finish(
            request,
            evidence=evidence,
            edit=edit,
            revision=revision,
            message=message[:2000] if message else None,
        )
