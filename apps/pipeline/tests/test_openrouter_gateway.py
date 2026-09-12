"""Actual installed SDK request/stream and receipt boundary, without paid inference."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import httpx2
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from temnia_pipeline.harness.gateway import (
    GatewayChatModel,
    GatewayConfig,
    GatewayPolicyError,
    GenerationIdentityError,
    lookup_generation,
    observe_gateway_generation,
    validate_gateway_request,
)
from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy
from test_harness_model_transport import StrictAnswer, route

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from temnia_pipeline.harness.routes import RouteEntry


def openrouter_route(**changes: object) -> RouteEntry:
    return route("openrouter-stream", "family-a", "provider-endpoint").model_copy(
        update={
            "provider_accounting_name": "Provider Display",
            "reasoning_effort": "high",
            "transport": GatewayTransportPolicy(
                gateway="openrouter",
                mode="streaming",
                request_timeout_seconds=300.0,
                total_timeout_seconds=540.0,
            ),
            **changes,
        }
    )


def chunk(
    *,
    content: str | None = None,
    identity: str = "generation-stream",
    finish: str | None = None,
    usage: bool = False,
) -> bytes:
    body: dict[str, Any] = {
        "id": identity,
        "model": "model/family-a",
        "created": 1,
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    **({"content": content} if content is not None else {}),
                },
                "finish_reason": finish,
            }
        ],
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": 3,
            "completion_tokens": 7,
            "total_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
    return ("data: " + json.dumps(body) + "\n\n").encode()


class Frames(httpx2.AsyncByteStream):
    def __init__(
        self,
        frames: list[bytes],
        observed: list[str],
        *,
        timeout: bool = False,
        cancel: bool = False,
    ) -> None:
        self.frames = frames
        self.observed = observed
        self.timeout = timeout
        self.cancel = cancel
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        # The response header must already be durably observed before body iteration.
        assert self.observed == ["generation-stream"]
        for frame in self.frames:
            yield frame
        if self.timeout:
            raise httpx2.ReadTimeout("synthetic streamed timeout")  # noqa: EM101, TRY003
        if self.cancel:
            raise asyncio.CancelledError

    async def aclose(self) -> None:
        self.closed = True


async def run_stream(
    frames: list[bytes], *, read_failure: bool = False, cancel: bool = False
) -> tuple[Any, list[str], list[dict[str, Any]], Frames]:
    observed: list[str] = []
    wires: list[dict[str, Any]] = []
    stream = Frames(frames, observed, timeout=read_failure, cancel=cancel)
    selected = openrouter_route()

    async def remember(identity: str) -> None:
        observed.append(identity)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        wires.append(validate_gateway_request(request, selected))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "x-generation-id": "generation-stream"},
            stream=stream,
            request=request,
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = GatewayChatModel(
            selected, GatewayConfig(api_key="test-key", gateway="openrouter"), http_client=client
        )
        agent = Agent(
            model,
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
            model_settings={"max_tokens": 24},
        )
        with observe_gateway_generation(remember):
            result = await agent.run("answer")
    return result, observed, wires, stream


async def test_stream_header_before_content_and_final_usage_is_fully_drained() -> None:
    result, observed, wires, stream = await run_stream(
        [
            b": OPENROUTER PROCESSING\n\n",
            chunk(),
            chunk(content='{"value":"ok"}'),
            chunk(finish="stop"),
            chunk(finish="stop", usage=True),
            b"data: [DONE]\n\n",
        ]
    )
    assert result.output.value == "ok"
    assert observed == ["generation-stream"]
    assert stream.closed
    assert len(wires) == 1
    assert wires[0]["stream"] is True
    assert wires[0]["max_tokens"] == 24
    assert "max_completion_tokens" not in wires[0]
    assert wires[0]["reasoning"] == {"effort": "high"}
    assert result.response.usage.output_tokens == 7
    assert result.response.usage.details["reasoning_tokens"] == 5
    assert result.response.provider_response_id == "generation-stream"
    assert result.response.provider_details["gatewayTransport"]["mode"] == "streaming"


@pytest.mark.parametrize(
    "frames",
    [
        [],
        [chunk(content='{"value":"partial"}')],
        [chunk(identity="different")],
        [b'data: {"error":{"code":400,"message":"mid-stream rejection"}}\n\n'],
        [
            chunk(content='{"value":'),
            b'data: {"error":{"code":429,"message":"mid-stream rejection"}}\n\n',
        ],
    ],
)
async def test_incomplete_or_conflicting_stream_is_unknown_not_http_rejection(
    frames: list[bytes],
) -> None:
    with pytest.raises(ModelAPIError) as caught:
        await run_stream(frames)
    assert not isinstance(caught.value, ModelHTTPError)


@pytest.mark.parametrize("kind", ["timeout", "cancel"])
async def test_stream_interruptions_keep_observer_before_error(kind: str) -> None:
    with pytest.raises(asyncio.CancelledError if kind == "cancel" else ModelAPIError):
        await run_stream([chunk()], read_failure=kind == "timeout", cancel=kind == "cancel")


async def test_initial_http_rejection_is_one_conclusive_response() -> None:
    calls = 0
    observed: list[str] = []

    async def remember(identity: str) -> None:
        observed.append(identity)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(429, request=request, json={"error": {"message": "rate limit"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        agent = Agent(
            GatewayChatModel(
                openrouter_route(),
                GatewayConfig(api_key="key", gateway="openrouter"),
                http_client=client,
            ),
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with observe_gateway_generation(remember), pytest.raises(ModelHTTPError) as caught:
            await agent.run("answer")
    assert caught.value.status_code == 429
    assert calls == 1
    assert not observed


def test_gateway_url_and_route_mismatch_refuse_without_request() -> None:
    config = GatewayConfig(api_key="key", gateway="openrouter")
    assert config.base_url == "https://openrouter.ai/api/v1"
    with pytest.raises(ValidationError):
        GatewayConfig(
            api_key="key", gateway="openrouter", base_url="https://ai-gateway.vercel.sh/v1"
        )
    with pytest.raises(GatewayPolicyError):
        GatewayChatModel(openrouter_route(), GatewayConfig(api_key="key"))


@pytest.mark.parametrize(("cost", "expected"), [("0", 0), ("0.00000101", 2), (None, None)])
async def test_openrouter_raw_actual_accounting_and_provenance(
    cost: str | None, expected: int | None
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://openrouter.ai/api/v1/generation?")
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": "generation-stream",
                    "model": "model/family-a",
                    "provider_name": "Provider Display",
                    "is_byok": False,
                    "total_cost": cost,
                    "native_tokens_reasoning": 12,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup_generation(
            client,
            config=GatewayConfig(api_key="key", gateway="openrouter"),
            route=openrouter_route(),
            generation_id="generation-stream",
        )
    assert result.actual_cost_micros == expected
    assert result.components["reasoningTokens"] == 12
    assert result.components["gateway"] == "openrouter"
    assert len(str(result.components["receiptSha256"])) == 64
    assert result.status == ("reported" if expected is not None else "pending")


async def test_accounting_provider_slug_is_not_silently_equated_to_display_name() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": "generation-stream",
                    "model": "model/family-a",
                    "provider_name": "provider-endpoint",
                    "is_byok": False,
                    "total_cost": 0,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationIdentityError):
            await lookup_generation(
                client,
                config=GatewayConfig(api_key="key", gateway="openrouter"),
                route=openrouter_route(),
                generation_id="generation-stream",
            )


async def test_first_role_chunk_supplies_missing_header_before_later_read_timeout() -> None:
    observed: list[str] = []

    class MissingHeader(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            assert not observed
            yield chunk()
            assert observed == ["generation-stream"]
            raise httpx2.ReadTimeout("synthetic timeout after first role")  # noqa: EM101, TRY003

    async def remember(identity: str) -> None:
        observed.append(identity)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            stream=MissingHeader(),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        agent = Agent(
            GatewayChatModel(
                openrouter_route(),
                GatewayConfig(api_key="key", gateway="openrouter"),
                http_client=client,
            ),
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with observe_gateway_generation(remember), pytest.raises(ModelAPIError):
            await agent.run("answer")
    assert observed == ["generation-stream"]


async def test_aggregate_deadline_interrupts_keepalive_stream_without_retry() -> None:
    observed: list[str] = []
    calls = 0
    closed = False

    class Waiting(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield chunk()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    async def remember(identity: str) -> None:
        observed.append(identity)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream", "x-generation-id": "generation-stream"},
            stream=Waiting(),
        )

    selected = openrouter_route(
        transport=GatewayTransportPolicy(
            gateway="openrouter",
            mode="streaming",
            request_timeout_seconds=0.03,
            total_timeout_seconds=0.04,
        )
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        agent = Agent(
            GatewayChatModel(
                selected, GatewayConfig(api_key="key", gateway="openrouter"), http_client=client
            ),
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with observe_gateway_generation(remember), pytest.raises(ModelAPIError):
            await agent.run("answer")
    assert observed == ["generation-stream"]
    assert calls == 1
    assert closed


async def test_handle_persistence_failure_aborts_before_reading_body() -> None:
    reads = 0

    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            nonlocal reads
            reads += 1
            yield chunk(content='{"value":"uncommitted"}', finish="stop")

    async def remember(identity: str) -> None:
        assert identity == "generation-stream"
        raise OSError("synthetic persistence failure")  # noqa: EM101, TRY003

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream", "x-generation-id": "generation-stream"},
            stream=Body(),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        agent = Agent(
            GatewayChatModel(
                openrouter_route(),
                GatewayConfig(api_key="key", gateway="openrouter"),
                http_client=client,
            ),
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with observe_gateway_generation(remember), pytest.raises(ModelAPIError):
            await agent.run("answer")
    assert reads == 0


@pytest.mark.parametrize("mode", ["streaming", "non_streaming"])
async def test_observed_router_metadata_is_preserved_without_invented_endpoint(mode: str) -> None:
    policy = GatewayTransportPolicy.model_validate(
        {
            "gateway": "openrouter",
            "mode": mode,
            "request_timeout_seconds": 300.0,
            "total_timeout_seconds": 540.0,
        }
    )
    selected = openrouter_route(transport=policy)
    metadata = {
        "route": {"provider_slug": "provider-endpoint", "region": "observed-region"},
        "opaque": [0, False, {"futureField": "retained"}],
    }

    async def handler(request: httpx2.Request) -> httpx2.Response:
        validate_gateway_request(request, selected)
        if mode == "streaming":
            final = json.loads(chunk(finish="stop", usage=True).decode()[6:].strip())
            final["openrouter_metadata"] = metadata
            final["provider"] = "Provider Display"
            raw = (
                chunk(content='{"value":"ok"}', finish="stop")
                + ("data: " + json.dumps(final) + "\n\ndata: [DONE]\n\n").encode()
            )
            return httpx2.Response(
                200, request=request, headers={"content-type": "text/event-stream"}, content=raw
            )
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "generation-stream",
                "model": "model/family-a",
                "created": 1,
                "object": "chat.completion",
                "openrouter_metadata": metadata,
                "provider": "Provider Display",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"value":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        agent = Agent(
            GatewayChatModel(
                selected, GatewayConfig(api_key="key", gateway="openrouter"), http_client=client
            ),
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        result = await agent.run("answer")
    assert result.response.provider_details is not None
    assert result.response.provider_details["openrouter_metadata"] == metadata
    assert result.response.provider_details["gatewayProvider"] == "Provider Display"
