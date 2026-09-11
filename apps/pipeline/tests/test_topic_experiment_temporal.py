"""The actual Temporal client preserves immutable start identity without running paid work."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment

from temnia_pipeline.harness.topic_experiment import TemporalDriver, assert_workflow
from test_topic_experiment import prepare, spec

if TYPE_CHECKING:
    from pathlib import Path


async def test_real_temporal_start_is_duplicate_rejecting_and_memo_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    execution = prepared.executions[0]
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as environment:
        driver = TemporalDriver(environment.client)
        assert await driver.describe(execution.workflow_id) is None
        # No worker is attached: this tests the real start RPC and retained input, no inference.
        await driver.start(execution, prepared.arms[0].pipeline_queue, prepared.sha256)
        observed = await driver.describe(execution.workflow_id)
        assert observed is not None
        assert observed.status == "RUNNING"
        assert_workflow(prepared, execution, observed)
        with pytest.raises(WorkflowAlreadyStartedError):
            await driver.start(execution, prepared.arms[0].pipeline_queue, prepared.sha256)
        assert await driver.describe(execution.workflow_id) == observed
        await environment.client.get_workflow_handle(execution.workflow_id).terminate(
            reason="Operational test cleanup; no worker or model was dispatched."
        )
        # The duplicate-rejection also applies after close, where SDK defaults would allow reuse.
        with pytest.raises(WorkflowAlreadyStartedError):
            await driver.start(execution, prepared.arms[0].pipeline_queue, prepared.sha256)
        closed = await driver.describe(execution.workflow_id)
        assert closed is not None
        assert closed.run_id == observed.run_id
        assert closed.status == "TERMINATED"
