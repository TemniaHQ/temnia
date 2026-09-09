"""Temporal and real-Postgres proof for reusable summary label normalization."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, PydanticAIWorkflow
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from temnia_pipeline import db
from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER, CassetteStore
from temnia_pipeline.harness.models import (
    HarnessModelDeps,
    ModelRuntime,
    chapter_summarize_v1,
    clear_model_runtime,
    configure_model_runtime,
)
from temnia_pipeline.harness.routes import RouteEligibility, RouteEntry, RoutePrices

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store
    from pydantic_ai.messages import ModelMessage


SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def _pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required for the real persistence boundary")
    return url


async def _run_case(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with db.scoped(url, SCOPE) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SCOPE.organizationId, f"summary-normalization-{uuid.uuid4()}"),
            )
        ).fetchone()
        assert project is not None
        source = await (
            await conn.execute(
                """
                INSERT INTO source
                    (organization_id, project_id, title, original_filename, content_type,
                     size_bytes, master_key, status)
                VALUES (%s, %s, 'summary normalization', 'source.mp4', 'video/mp4',
                        1, %s, 'ready')
                RETURNING id
                """,
                (
                    SCOPE.organizationId,
                    project["id"],
                    f"summary-normalization/{uuid.uuid4()}/master.mp4",
                ),
            )
        ).fetchone()
        assert source is not None
        run = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (organization_id, source_id, request_key, lane, budget_micros,
                     config, route_snapshot, status, stage)
                VALUES (%s, %s, %s, 'chapters', 1000000,
                        '{}'::jsonb, '{}'::jsonb, 'running', 'planning')
                RETURNING id
                """,
                (SCOPE.organizationId, source["id"], str(uuid.uuid4())),
            )
        ).fetchone()
        assert run is not None
    return source["id"], run["id"]


@workflow.defn
class SummaryNormalizationWorkflow(PydanticAIWorkflow):
    """Exercise the actual PydanticAI model activity from Temporal."""

    __pydantic_ai_agents__ = (chapter_summarize_v1,)

    @workflow.run
    async def run(self, deps: HarnessModelDeps) -> dict[str, Any]:
        result = await chapter_summarize_v1.run(
            "Return the summary.", deps=deps, model_settings={"max_tokens": 1024}
        )
        return result.output.model_dump(mode="json")


async def test_temporal_summary_boundary_reuses_raw_response_without_dispatch(
    tmp_path: Path,
) -> None:
    url = _pipeline_url()
    source_id, run_id = await _run_case(url)
    calls = 0

    async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        _ = messages, info
        calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    '{"units":[{"firstSentenceId":"s0","id":"",'
                    '"lastSentenceId":"s0","quoteWordIds":["w0"],'
                    '"text":"Original source sentence."}],"version":1}'
                )
            ],
            model_name="summary-model",
            provider_name="summary-provider",
            provider_response_id="summary-generation",
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        _ = route
        return FunctionModel(response, model_name="summary-model")

    route = RouteEntry(
        id="summary-route",
        gateway_model="fixture/summary",
        family="summary-family",
        provider="summary-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=1024,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="b" * 64,
            probed_at=date(2026, 9, 9),
        ),
        prices=RoutePrices(input=0, output=0),
    )
    deps = HarnessModelDeps(
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        stage="summary:window-0006",
        program_version="chapter-workflow/1",
        prompt_version="chapter-summarize-v3",
        schema_version="hierarchical-summary/1",
        route=route,
        operation_inputs={"windowId": "window-0006"},
        operation_config={"hierarchyLevel": 1, "maxOutputTokens": 1024},
        dispatch_limit=8,
    )
    store = MemoryStore()
    configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=cast("S3Store", store),
            cassette_store=CassetteStore(tmp_path),
            model_factory=model_factory,
        )
    )
    queue = f"summary-normalization-{uuid.uuid4()}"
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[SummaryNormalizationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            first = await environment.client.execute_workflow(
                SummaryNormalizationWorkflow.run,
                deps,
                id=f"summary-normalization-first-{uuid.uuid4()}",
                task_queue=queue,
            )
            second = await environment.client.execute_workflow(
                SummaryNormalizationWorkflow.run,
                deps,
                id=f"summary-normalization-second-{uuid.uuid4()}",
                task_queue=queue,
            )
        assert first == second
        assert first["units"][0]["id"].startswith("summary-0000-")
        assert calls == 1
        async with db.scoped(url, SCOPE) as conn:
            facts = await (
                await conn.execute(
                    """
                    SELECT o.result_artifact_id,
                           (SELECT count(*)::int FROM harness_operation
                             WHERE run_id = %s) AS operation_count,
                           (SELECT count(*)::int FROM harness_attempt
                             WHERE run_id = %s) AS attempt_count
                      FROM harness_operation o
                     WHERE o.run_id = %s AND o.stage = 'summary:window-0006'
                    """,
                    (run_id, run_id, run_id),
                )
            ).fetchone()
        assert facts is not None
        assert facts["operation_count"] == 1
        assert facts["attempt_count"] == 1
        raw_response = await artifacts.read_artifact_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            artifact_id=facts["result_artifact_id"],
        )
        assert isinstance(raw_response, dict)
        retained = MODEL_RESPONSE_ADAPTER.validate_python(raw_response)
        assert isinstance(retained.parts[0], TextPart)
        assert json.loads(retained.parts[0].content)["units"][0]["id"] == ""
    finally:
        clear_model_runtime()
        await db.close_pool()
