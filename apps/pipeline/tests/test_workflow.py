"""Workflow test on Temporal's time-skipping test server (downloaded once, cached)."""

import uuid
from uuid import UUID

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline.activities import say_hello
from temnia_pipeline.contracts import HelloInput, Scope
from temnia_pipeline.workflows import HelloWorkflow

SEEDED = Scope(
    organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
)


@pytest.mark.timeout(120)
async def test_hello_workflow_round_trip() -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(
            env.client, task_queue=queue, workflows=[HelloWorkflow], activities=[say_hello]
        ):
            result = await env.client.execute_workflow(
                HelloWorkflow.run,
                HelloInput(scope=SEEDED, name="gate"),
                id=f"hello-{uuid.uuid4()}",
                task_queue=queue,
            )
    assert result.organizationId == SEEDED.organizationId
    assert result.workerLanguage == "python"
