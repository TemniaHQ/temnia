"""Real scoped ledger with installed streaming SDK and mocked external transports."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import httpx
import httpx2
import pytest
from obstore.store import MemoryStore
from pydantic_ai.exceptions import UnexpectedModelBehavior

from qualification_fixtures import _outputs_v3, _published_source_index_ref
from temnia_pipeline import db
from temnia_pipeline.harness import models, runs
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3
from temnia_pipeline.harness.gateway import (
    GatewayChatModel,
    GatewayConfig,
    validate_gateway_request,
)
from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy
from temnia_pipeline.harness.models import ModelRuntime
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_V3_SCHEMAS,
    topic_selection_qualification_prompts,
)
from temnia_pipeline.harness.routes import SeatRoutePool
from temnia_pipeline.harness.topic_selection_runtime import SelectionCallPlan
from temnia_pipeline.harness.topic_selection_workflow import selection_model_deps
from test_harness_model_transport import snapshot
from test_harness_runs import SEEDED, pipeline_url, ready_source, settings, start_request
from test_openrouter_gateway import openrouter_route

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.harness.routes import RouteEntry


@pytest.mark.parametrize("outcome", ["success", "timeout", "length"])
@pytest.mark.parametrize("transport_version", [1, 2])
async def test_stream_handle_and_settlement_use_existing_ledger(  # noqa: C901, PLR0915
    tmp_path: Path,
    *,
    outcome: str,
    transport_version: int,
) -> None:
    interrupted = outcome == "timeout"
    url = pipeline_url()
    selected = openrouter_route()
    if transport_version == 2:
        selected = selected.model_copy(
            update={
                "accounting_model": selected.gateway_model + "-20260911",
                "transport": GatewayTransportPolicy(
                    version="gateway-transport/2",
                    gateway="openrouter",
                    mode="streaming",
                    request_timeout_seconds=300.0,
                    total_timeout_seconds=540.0,
                    output_token_parameter="max_completion_tokens",  # noqa: S106
                ),
            }
        )
    routes = snapshot(
        (selected,),
        {
            seat: SeatRoutePool(route_ids=(selected.id,))
            for seat in ("propose", "verify", "summary")
        },
    )
    configuration = replace(settings(routes), gateway="openrouter")
    source_id = await ready_source(url)
    original = start_request(source_id, routes)
    start = original.model_copy(
        update={
            "editorial_policy": TOPIC_SELECTION_POLICY_V3,
            "request": original.request.model_copy(
                update={"config": configuration.allowed_config()}
            ),
        }
    )
    await runs.start_or_refetch_run(url, start=start, settings=configuration, route_snapshot=routes)
    store = cast("S3Store", MemoryStore())
    source_index = await _published_source_index_ref(
        url, scope=SEEDED, source_id=source_id, store=store
    )
    requests = 0
    lookups = 0
    early_rows: list[dict[str, Any]] = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            # This code runs before content can cross the SDK boundary.
            async with db.scoped(url, SEEDED) as conn:
                row = await (
                    await conn.execute(
                        "SELECT remote_handle, state FROM harness_attempt WHERE run_id=%s",
                        (start.request.runId,),
                    )
                ).fetchone()
            assert row is not None
            assert row["remote_handle"] == "generation-ledger"
            early_rows.append(dict(row))
            role: dict[str, Any] = {
                "id": "generation-ledger",
                "model": selected.gateway_model,
                "created": 1,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield ("data: " + json.dumps(role) + "\n\n").encode()
            if interrupted:
                raise httpx2.ReadTimeout("synthetic after durable handle")  # noqa: EM101, TRY003
            role["choices"] = [
                {
                    "index": 0,
                    "delta": {"content": json.dumps(_outputs_v3()[1])},
                    "finish_reason": "length" if outcome == "length" else "stop",
                }
            ]
            role["usage"] = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
            yield ("data: " + json.dumps(role) + "\n\ndata: [DONE]\n\n").encode()

    async def request_handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        body = validate_gateway_request(request, selected)
        output_key = "max_completion_tokens" if transport_version == 2 else "max_tokens"
        other_key = "max_tokens" if transport_version == 2 else "max_completion_tokens"
        assert body["stream"] is True
        assert body[output_key] == 8192
        assert other_key not in body
        assert body["model"] == selected.gateway_model
        assert {item["function"]["name"] for item in body["tools"]} == {
            "browse_source",
            "search_source",
            "read_source",
        }
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream", "x-generation-id": "generation-ledger"},
            stream=Stream(),
        )

    async def lookup_handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookups
        lookups += 1
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": "generation-ledger",
                    "model": selected.accounting_model or selected.gateway_model,
                    "provider_name": selected.provider_accounting_name,
                    "is_byok": False,
                    "total_cost": "0.000001",
                }
            },
        )

    gateway = GatewayConfig(api_key="synthetic-test-key", gateway="openrouter")
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(request_handler)) as request_client,
        httpx.AsyncClient(transport=httpx.MockTransport(lookup_handler)) as lookup_client,
    ):

        def factory(route: RouteEntry) -> GatewayChatModel:
            return GatewayChatModel(route, gateway, http_client=request_client)

        models.configure_model_runtime(
            ModelRuntime(
                database_url=url,
                store=store,
                cassette_store=CassetteStore(tmp_path),
                gateway=gateway,
                model_factory=factory,
                lookup_client=lookup_client,
                allow_outside_activity=True,
            )
        )
        prompt, _, prompt_version = topic_selection_qualification_prompts()["topic_author"]
        plan = SelectionCallPlan(
            prompt=prompt,
            stage="proposal:selection:0",
            prompt_version=prompt_version,
            schema_version=TOPIC_SELECTION_V3_SCHEMAS["topic_author"],
            author=selected,
            verifier=selected,
            input_artifacts=(source_index,),
            source_index=source_index,
            source_tool_role="author",
        )
        deps = selection_model_deps(start.request, plan)
        agent = models.topic_selection_author_v3
        try:
            if interrupted:
                # The receipt settles the lost stream: each attempt is a known failure
                # with its charge, and the next call is a fresh paid attempt.
                for _ in range(2):
                    with pytest.raises(models.TransientProviderFailure):
                        await agent.run(prompt, deps=deps, model_settings={"max_tokens": 8192})
            elif outcome == "length":
                for _ in range(2):
                    with pytest.raises(
                        UnexpectedModelBehavior, match="did not finish successfully"
                    ):
                        await agent.run(prompt, deps=deps, model_settings={"max_tokens": 8192})
            else:
                first = await agent.run(prompt, deps=deps, model_settings={"max_tokens": 8192})
                second = await agent.run(prompt, deps=deps, model_settings={"max_tokens": 8192})
                assert first.output == second.output
            async with db.scoped(url, SEEDED) as conn:
                attempts = await (
                    await conn.execute(
                        "SELECT remote_handle,state,actual_cost_micros,cost_status,usage "
                        "FROM harness_attempt WHERE run_id=%s ORDER BY attempt_number",
                        (start.request.runId,),
                    )
                ).fetchall()
            assert attempts
            attempt = attempts[-1]
            assert all(row["remote_handle"] == "generation-ledger" for row in attempts)
            expected_requests = 2 if interrupted else 1
            assert len(early_rows) == requests == expected_requests
            run = await runs.get_run(
                url, scope=SEEDED, source_id=source_id, run_id=start.request.runId
            )
            assert run.dispatch_count == expected_requests
            if interrupted:
                assert len(attempts) == 2
                assert all(row["state"] == "failed_known" for row in attempts)
                assert all(row["actual_cost_micros"] == 1 for row in attempts)
                assert all(row["cost_status"] == "reported" for row in attempts)
                assert run.spent_micros == 2
                assert run.reserved_micros == 0
                assert lookups == 2
            else:
                assert attempt["state"] == "succeeded"
                assert run.spent_micros == 1
                assert run.reserved_micros == 0
                assert lookups == 1
                assert attempt["usage"]["gateway"]["components"]["model"] == (
                    selected.accounting_model or selected.gateway_model
                )
        finally:
            models.clear_model_runtime()
            await db.close_pool()
