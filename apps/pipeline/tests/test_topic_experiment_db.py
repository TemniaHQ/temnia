"""Production database ownership and immutable programme provenance for prepared experiments."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from temnia_pipeline import db
from temnia_pipeline.evals.topics import digest
from temnia_pipeline.harness import runs
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.harness.runtime_types import StartRunRequest, WorkflowIdentity
from temnia_pipeline.harness.topic_experiment import (
    prepare_experiment,
    run_execution,
)
from test_harness_runs import pipeline_url, ready_source
from test_topic_experiment import FakeDriver, runtime, spec

if TYPE_CHECKING:
    from pathlib import Path


async def test_precreated_pending_run_claims_once_and_preserves_programme(tmp_path: Path) -> None:
    url = pipeline_url()
    source_id = await ready_source(url)
    value = spec(tmp_path)
    value = value.model_copy(
        update={"sources": (value.sources[0].model_copy(update={"source_id": source_id}),)}
    )
    try:
        prepared = await prepare_experiment(value, database_url=url, environment={})
        settings, temporal = runtime(prepared)
        execution = prepared.executions[0]
        driver = FakeDriver()
        driver.read_unavailable = True
        result = await run_execution(
            prepared,
            execution,
            database_url=url,
            settings=settings,
            temporal=temporal,
            driver=driver,
        )
        assert result.launch_state == "start_unknown"
        assert driver.starts == []
        before = await runs.get_run(
            url, scope=prepared.scope, source_id=source_id, run_id=execution.request.runId
        )
        assert before.status.value == "pending"
        assert before.workflow_run_id == execution.preparation_identity.workflow_run_id
        assert before.evaluation_program == prepared.program.model_dump(mode="json", by_alias=True)
        assert before.evaluation_program_sha256 == digest(before.evaluation_program)
        # A repeated launcher cannot claim its own placeholder as a running Temporal execution.
        precreate = StartRunRequest(
            request=execution.request,
            workflow=execution.preparation_identity,
            editorial_policy="standalone-topics/3",
            evaluation_program=before.evaluation_program,
        )
        duplicates = await asyncio.gather(
            *[
                runs.start_or_refetch_run(
                    url,
                    start=precreate,
                    settings=settings,
                    route_snapshot=prepared.arms[0].snapshot,
                )
                for _ in range(2)
            ]
        )
        assert all(not result.created and result.run == before for result in duplicates)
        # This is the unchanged workflow's activity request: it supplies no experiment manifest.
        claim = StartRunRequest(
            request=execution.request,
            workflow=WorkflowIdentity(
                workflow_id=execution.workflow_id, workflow_run_id="actual-temporal-execution"
            ),
            editorial_policy="standalone-topics/3",
        )
        claimed = await runs.start_or_refetch_run(
            url, start=claim, settings=settings, route_snapshot=prepared.arms[0].snapshot
        )
        assert claimed.run.status.value == "running"
        assert claimed.run.evaluation_program == before.evaluation_program
        assert claimed.run.evaluation_program_sha256 == before.evaluation_program_sha256
        assert claimed.run.source == before.source
        assert claimed.run.transcript == before.transcript
        assert claimed.run.spent_micros == claimed.run.reserved_micros == 0
        with pytest.raises(IdentityConflict, match="already owned"):
            await runs.start_or_refetch_run(
                url, start=precreate, settings=settings, route_snapshot=prepared.arms[0].snapshot
            )
        changed_program = prepared.program.model_copy(update={"implementation_sha256": "f" * 64})
        changed = claim.model_copy(
            update={"evaluation_program": changed_program.model_dump(mode="json", by_alias=True)}
        )
        with pytest.raises(IdentityConflict, match="different evaluation programme"):
            await runs.start_or_refetch_run(
                url, start=changed, settings=settings, route_snapshot=prepared.arms[0].snapshot
            )
    finally:
        await db.close_pool()
