from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.harness.queues import control_task_queue


@workflow.defn
class OccupyMediaSlotWorkflow:
    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity(
            "queue_test_hold_media",
            start_to_close_timeout=timedelta(seconds=30),
        )


@workflow.defn
class ShortControlWorkflow:
    @workflow.run
    async def run(self, separate: bool) -> str:  # noqa: FBT001
        pipeline_queue = workflow.info().task_queue
        return await workflow.execute_activity(
            "queue_test_cancel",
            task_queue=(control_task_queue(pipeline_queue) if separate else pipeline_queue),
            start_to_close_timeout=timedelta(seconds=10),
            result_type=str,
        )


class QueueActivities:
    def __init__(self) -> None:
        self.started = 0
        self.media_full = asyncio.Event()
        self.release_media = asyncio.Event()
        self.cancelled = asyncio.Event()

    @activity.defn(name="queue_test_hold_media")
    async def hold_media(self) -> None:
        self.started += 1
        if self.started == 2:
            self.media_full.set()
        await self.release_media.wait()

    @activity.defn(name="queue_test_cancel")
    async def cancel(self) -> str:
        self.cancelled.set()
        return "cancellation admitted"


@pytest.mark.parametrize("separate", [False, True])
async def test_control_progress_while_both_media_slots_are_occupied(
    separate: bool,  # noqa: FBT001
) -> None:
    activities = QueueActivities()
    queue = f"control-capacity-{uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping() as environment,
        AsyncExitStack() as workers,
    ):
        # This measures occupied slots in wall time. Advancing the test clock
        # while another workflow is queued can manufacture an unrelated timeout.
        workers.enter_context(environment.auto_time_skipping_disabled())
        await workers.enter_async_context(
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[OccupyMediaSlotWorkflow, ShortControlWorkflow],
                activities=[activities.hold_media, activities.cancel],
                max_concurrent_activities=2,
            )
        )
        if separate:
            await workers.enter_async_context(
                Worker(
                    environment.client,
                    task_queue=control_task_queue(queue),
                    activities=[activities.cancel],
                    max_concurrent_activities=4,
                )
            )
        holds = [
            await environment.client.start_workflow(
                OccupyMediaSlotWorkflow.run,
                id=f"{queue}-{index}",
                task_queue=queue,
            )
            for index in range(2)
        ]
        try:
            await asyncio.wait_for(activities.media_full.wait(), timeout=10)
            command = await environment.client.start_workflow(
                ShortControlWorkflow.run,
                separate,
                id=f"{queue}-cancel",
                task_queue=queue,
            )
            if separate:
                await asyncio.wait_for(activities.cancelled.wait(), timeout=5)
                assert not activities.release_media.is_set()
            else:
                await asyncio.sleep(0.25)
                assert not activities.cancelled.is_set()
            activities.release_media.set()
            await asyncio.gather(*(handle.result() for handle in holds))
            assert await command.result() == "cancellation admitted"
        finally:
            activities.release_media.set()
