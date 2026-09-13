"""Native output settings, actual reservations and reuse against the migrated ledger."""

# Only the external gateway transports are mocked; run and paid-operation DML is real.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import httpx
import httpx2
import pytest
from obstore.store import MemoryStore

from temnia_pipeline import db
from temnia_pipeline.harness import ledger, models, runs
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.gateway import GatewayChatModel, GatewayConfig
from temnia_pipeline.harness.models import ModelPersistenceError, ModelRuntime
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_SCHEMAS,
    topic_selection_qualification_prompts,
)
from temnia_pipeline.harness.routes import CostEstimate, RouteEntry, estimate_cost
from temnia_pipeline.harness.topic_selection import SELECTION_POLICY
from temnia_pipeline.harness.topic_selection_runtime import SelectionCallPlan
from temnia_pipeline.harness.topic_selection_workflow import selection_model_deps
from test_harness_runs import SEEDED, pipeline_url, ready_source, settings, start_request
from test_topic_output_profiles import profiles
from test_topic_selection_qualification import _outputs

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store


async def test_native_profiles_reserve_actual_settings_and_reuse_settled_responses(  # noqa: PLR0915
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = pipeline_url()
    routes = profiles()
    configuration = replace(settings(routes), max_output_tokens=32768)
    source_id = await ready_source(url)
    original = start_request(source_id, routes)
    start = original.model_copy(
        update={
            "editorial_policy": SELECTION_POLICY,
            "request": original.request.model_copy(
                update={"config": configuration.allowed_config()}
            ),
        }
    )
    await runs.start_or_refetch_run(url, start=start, settings=configuration, route_snapshot=routes)
    wires: list[dict[str, Any]] = []
    estimates: list[tuple[str, CostEstimate]] = []
    output_bodies = _outputs()
    by_model = {route.gateway_model: route for route in routes.routes}

    def observe_estimate(
        route: RouteEntry, *, payload_bytes: int, max_output_tokens: int | None = None
    ) -> CostEstimate:
        estimate = estimate_cost(
            route, payload_bytes=payload_bytes, max_output_tokens=max_output_tokens
        )
        estimates.append((route.id, estimate))
        return estimate

    monkeypatch.setattr(models, "estimate_cost", observe_estimate)

    async def request_handler(request: httpx2.Request) -> httpx2.Response:
        wire = json.loads(request.content)
        wires.append(wire)
        index = len(wires) - 1
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": f"profile-generation-{index}",
                "object": "chat.completion",
                "created": 1,
                "model": wire["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output_bodies[index]),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async def lookup_handler(request: httpx.Request) -> httpx.Response:
        generation_id = request.url.params["id"]
        index = int(generation_id.removeprefix("profile-generation-"))
        route = by_model[wires[index]["model"]]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": generation_id,
                    "model": route.gateway_model,
                    "provider_name": route.provider,
                    "is_byok": False,
                    "total_cost": "0.000001",
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

    gateway = GatewayConfig(api_key="synthetic-profile-test-key")
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(request_handler)) as request_client,
        httpx.AsyncClient(transport=httpx.MockTransport(lookup_handler)) as lookup_client,
    ):

        def model_factory(route: RouteEntry) -> GatewayChatModel:
            return GatewayChatModel(route, gateway, http_client=request_client)

        models.configure_model_runtime(
            ModelRuntime(
                database_url=url,
                store=cast("S3Store", MemoryStore()),
                cassette_store=CassetteStore(tmp_path),
                gateway=gateway,
                model_factory=model_factory,
                lookup_client=lookup_client,
                allow_outside_activity=True,
            )
        )
        prompts = topic_selection_qualification_prompts()
        stages = (
            ("topic_author", "proposal:selection:0", models.topic_selection_author_v2, 8192),
            ("topic_cold", "verify:selection:cold:fixture", models.topic_selection_cold_v2, 32768),
            ("topic_source", "verify:selection:source:0", models.topic_selection_source_v2, 32768),
            ("topic_patch", "repair:selection:1", models.topic_selection_patch_v2, 8192),
        )
        try:
            for index, (name, stage, agent, expected_output) in enumerate(stages):
                prompt, _, version = prompts[name]
                plan = SelectionCallPlan(
                    prompt=prompt,
                    stage=stage,
                    prompt_version=version,
                    schema_version=TOPIC_SELECTION_SCHEMAS[name],
                    author=routes.routes[0],
                    verifier=routes.routes[1],
                    input_artifacts=(),
                )
                deps = selection_model_deps(start.request, plan)
                assert deps.operation_config["maxOutputTokens"] == expected_output
                first = await agent.run(
                    prompt, deps=deps, model_settings={"max_tokens": expected_output}
                )
                repeated = await agent.run(
                    prompt, deps=deps, model_settings={"max_tokens": expected_output}
                )
                assert first.output == repeated.output
                assert len(wires) == index + 1
                assert wires[index]["max_completion_tokens"] == expected_output
                assert wires[index]["response_format"]["json_schema"]["strict"] is True
                assert wires[index]["store"] is False
                assert estimates[-1][0] == deps.route.id
                assert estimates[-1][1].output_tokens == expected_output
                async with db.scoped(url, SEEDED) as conn:
                    rows = await (
                        await conn.execute(
                            """SELECT a.estimated_cost_micros, a.actual_cost_micros,
                                      a.request_hash, o.input_hash, o.config_hash
                                 FROM harness_operation o
                                 JOIN harness_attempt a ON a.operation_id=o.id
                                WHERE o.run_id=%s AND o.stage=%s""",
                            (start.request.runId, stage),
                        )
                    ).fetchall()
                assert len(rows) == 1
                assert rows[0]["estimated_cost_micros"] == estimates[-1][1].amount_micros
                assert rows[0]["actual_cost_micros"] == 1
                _, expected_inputs, expected_config = ledger.operation_identity(
                    run_id=start.request.runId,
                    kind="model",
                    inputs={**deps.operation_inputs, "requestHash": rows[0]["request_hash"]},
                    config={
                        **deps.operation_config,
                        "programVersion": deps.program_version,
                        "promptVersion": deps.prompt_version,
                        "schemaVersion": deps.schema_version,
                        "route": deps.route.model_dump(mode="json"),
                    },
                )
                assert rows[0]["input_hash"] == expected_inputs
                assert rows[0]["config_hash"] == expected_config
                if name == "topic_author":
                    with pytest.raises(ModelPersistenceError, match="qualified route"):
                        await agent.run(
                            prompt, deps=deps, model_settings={"max_tokens": expected_output + 1}
                        )
                    assert len(wires) == 1
            run = await runs.get_run(
                url, scope=SEEDED, source_id=source_id, run_id=start.request.runId
            )
            assert run.dispatch_count == run.spent_micros == 4
            assert run.reserved_micros == 0
        finally:
            models.clear_model_runtime()
            await db.close_pool()
