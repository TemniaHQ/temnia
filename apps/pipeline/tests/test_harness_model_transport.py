"""Pure model routing, cassette, and gateway accounting regressions."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import httpx2
import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelResponse, NativeOutput, TextPart, ThinkingPart
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import models as harness_models
from temnia_pipeline.harness.cassettes import (
    CassetteError,
    CassetteMetadata,
    CassetteMiss,
    CassetteStore,
    request_fingerprint,
)
from temnia_pipeline.harness.gateway import (
    GatewayChatModel,
    GatewayConfig,
    GatewayError,
    GenerationIdentityError,
    lookup_generation,
)
from temnia_pipeline.harness.models import HarnessModelDeps, HierarchicalSummaryV1
from temnia_pipeline.harness.routes import (
    ContextWindowExceeded,
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
    estimate_cost,
    select_route,
    select_verifier_route,
)

if TYPE_CHECKING:
    from pathlib import Path


def route(
    route_id: str,
    family: str,
    provider: str,
    *,
    alias: str | None = None,
    open_weight: bool = False,
) -> RouteEntry:
    return RouteEntry(
        id=route_id,
        gateway_model=alias or f"model/{family}",
        family=family,
        provider=provider,
        open_weight=open_weight,
        context_tokens=100_000,
        max_output_tokens=10_000,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=datetime(2026, 9, 8, tzinfo=UTC).date(),
        ),
        prices=RoutePrices(input=2_000_000, output=8_000_000),
    )


def snapshot(routes: tuple[RouteEntry, ...], seats: dict[str, SeatRoutePool]) -> RouteSnapshot:
    payload: dict[str, Any] = {
        "routes": [item.model_dump(mode="json") for item in routes],
        "seats": {key: value.model_dump(mode="json") for key, value in seats.items()},
        "synthetic": True,
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return RouteSnapshot(snapshot_id=digest, routes=routes, seats=seats, synthetic=True, version=1)


def test_route_snapshot_allows_same_alias_at_two_providers_and_excludes_family() -> None:
    routes = (
        route("a-one", "family-a", "provider-one", alias="shared/model"),
        route("a-two", "family-a", "provider-two", alias="shared/model"),
        route("b", "family-b", "provider-three"),
    )
    value = snapshot(routes, {"verify": SeatRoutePool(route_ids=("a-one", "b"))})
    assert select_route(value, "verify").provider == "provider-one"
    assert (
        select_verifier_route(value, "verify", generation_families=frozenset({"family-a"})).family
        == "family-b"
    )


def test_cost_estimate_uses_integer_ceiling_and_requested_output_cap() -> None:
    candidate = route("bounded", "family-a", "provider-one")
    estimate = estimate_cost(
        candidate, payload_bytes=1, protocol_overhead_bytes=0, max_output_tokens=2
    )
    assert estimate.input_tokens == 1
    assert estimate.output_tokens == 2
    assert estimate.amount_micros == 18
    with pytest.raises(ValueError, match="qualified route maximum"):
        estimate_cost(candidate, payload_bytes=1, max_output_tokens=10_001)
    with pytest.raises(ContextWindowExceeded, match="512 KiB"):
        estimate_cost(candidate, payload_bytes=513 * 1024)


def test_unproven_qualification_route_cannot_be_saved_as_a_snapshot() -> None:
    candidate = route("qualification-unproven:candidate", "family-a", "provider-one")
    with pytest.raises(ValueError, match="unproven qualification routes"):
        snapshot((candidate,), {"propose": SeatRoutePool(route_ids=(candidate.id,))})


def cassette_metadata(*, synthetic: bool = False) -> CassetteMetadata:
    return CassetteMetadata(
        route_id="route-a",
        stage="propose",
        schema_version="schema-v1",
        prompt_version="prompt-v1",
        program_version="program-v1",
        synthetic=synthetic,
    )


def test_cassette_roundtrip_miss_corruption_and_synthetic_fence(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    metadata = cassette_metadata()
    response = ModelResponse(
        parts=[TextPart('{"value":"ok"}')],
        model_name="model/a",
        provider_name="provider-one",
        timestamp=datetime(2026, 9, 8, tzinfo=UTC),
    )
    store.record("b" * 64, metadata, response)
    assert store.load("b" * 64, metadata) == response
    with pytest.raises(CassetteMiss):
        store.load("c" * 64, metadata)
    path = tmp_path / "bb" / f"{'b' * 64}.json"
    path.write_text("not-json")
    with pytest.raises(CassetteError, match="corrupt"):
        store.load("b" * 64, metadata)
    with pytest.raises(CassetteError, match="explicit enablement"):
        store.record("d" * 64, cassette_metadata(synthetic=True), response)


def test_request_fingerprint_ignores_incidental_message_timestamp() -> None:
    parameters = ModelRequestParameters()
    first = ModelRequest(
        parts=[UserPromptPart("hello", timestamp=datetime(2026, 9, 8, tzinfo=UTC))]
    )
    second = ModelRequest(
        parts=[UserPromptPart("hello", timestamp=datetime(2027, 1, 1, tzinfo=UTC))]
    )
    assert request_fingerprint([first], None, parameters, cassette_metadata()) == (
        request_fingerprint([second], None, parameters, cassette_metadata())
    )
    first.metadata = {"evidence": {"timestamp": "2026-09-08T00:00:00Z"}}
    second.metadata = {"evidence": {"timestamp": "2027-01-01T00:00:00Z"}}
    assert request_fingerprint([first], None, parameters, cassette_metadata()) != (
        request_fingerprint([second], None, parameters, cassette_metadata())
    )


def _summary_deps(*, schema_version: str = "hierarchical-summary/1") -> HarnessModelDeps:
    return HarnessModelDeps(
        scope=Scope(
            organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
            userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
        ),
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage="summary:window-0000",
        program_version="chapter-workflow/1",
        prompt_version="chapter-summarize-v3",
        schema_version=schema_version,
        route=route("summary", "family-a", "provider-one"),
        operation_inputs={},
        operation_config={},
        dispatch_limit=8,
    )


def _summary_response(units: list[dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        parts=[
            ThinkingPart("retained reasoning", id="thinking-1"),
            TextPart(
                json.dumps({"units": units, "version": 1}, separators=(",", ":")),
                id="answer-1",
                provider_details={"retained": True},
            ),
        ],
        usage=RequestUsage(input_tokens=7, output_tokens=11),
        model_name="model/family-a",
        provider_name="provider-one",
        provider_response_id="generation-one",
        metadata={"retained": "metadata"},
    )


def test_summary_response_derives_only_invalid_cosmetic_labels() -> None:
    units = [
        {
            "firstSentenceId": "s0",
            "id": "",
            "lastSentenceId": "s0",
            "quoteWordIds": ["w0"],
            "text": "First source sentence.",
        },
        {
            "firstSentenceId": "s1",
            "lastSentenceId": "s1",
            "quoteWordIds": ["w1"],
            "text": "Second source sentence.",
        },
        {
            "firstSentenceId": "s2",
            "id": "duplicate",
            "lastSentenceId": "s2",
            "quoteWordIds": ["w2"],
            "text": "Third source sentence.",
        },
        {
            "firstSentenceId": "s3",
            "id": "duplicate",
            "lastSentenceId": "s3",
            "quoteWordIds": ["w3"],
            "text": "Fourth source sentence.",
        },
        {
            "firstSentenceId": "s4",
            "id": 7,
            "lastSentenceId": "s4",
            "quoteWordIds": ["w4"],
            "text": "Fifth source sentence.",
        },
        {
            "firstSentenceId": "s5",
            "id": "provider-label",
            "lastSentenceId": "s5",
            "quoteWordIds": ["w5"],
            "text": "Sixth source sentence.",
        },
    ]
    response = _summary_response(units)

    normalized = harness_models._normalize_summary_response(  # noqa: SLF001
        _summary_deps(), response
    )
    repeated = harness_models._normalize_summary_response(  # noqa: SLF001
        _summary_deps(), response
    )

    assert normalized is not response
    assert normalized.parts[0] is response.parts[0]
    assert normalized.usage is response.usage
    assert normalized.provider_response_id == response.provider_response_id
    assert normalized.metadata is response.metadata
    assert isinstance(normalized.parts[1], TextPart)
    assert isinstance(repeated.parts[1], TextPart)
    parsed = json.loads(normalized.parts[1].content)
    repeated_parsed = json.loads(repeated.parts[1].content)
    labels = [unit["id"] for unit in parsed["units"]]
    assert labels[-1] == "provider-label"
    assert len(labels) == len(set(labels))
    assert parsed == repeated_parsed
    for original, actual in zip(units, parsed["units"], strict=True):
        assert {key: value for key, value in actual.items() if key != "id"} == {
            key: value for key, value in original.items() if key != "id"
        }
    HierarchicalSummaryV1.model_validate(parsed)
    collision = _summary_response(
        [
            {**units[0], "id": ""},
            {**units[1], "id": labels[0]},
        ]
    )
    collision_result = harness_models._normalize_summary_response(  # noqa: SLF001
        _summary_deps(), collision
    )
    assert isinstance(collision_result.parts[1], TextPart)
    collision_units = json.loads(collision_result.parts[1].content)["units"]
    assert collision_units[1]["id"] == labels[0]
    assert collision_units[0]["id"] != labels[0]


def test_summary_response_preserves_valid_and_unrelated_bytes_and_strict_failures() -> None:
    valid = _summary_response(
        [
            {
                "firstSentenceId": "s0",
                "id": "provider-label",
                "lastSentenceId": "s0",
                "quoteWordIds": ["w0"],
                "text": "Source sentence.",
            }
        ]
    )
    assert (
        harness_models._normalize_summary_response(_summary_deps(), valid)  # noqa: SLF001
        is valid
    )
    unrelated = _summary_response(
        [
            {
                "firstSentenceId": "s0",
                "id": "",
                "lastSentenceId": "s0",
                "quoteWordIds": ["w0"],
                "text": "Source sentence.",
            }
        ]
    )
    assert (
        harness_models._normalize_summary_response(  # noqa: SLF001
            _summary_deps(schema_version="chapter-proposal/1"), unrelated
        )
        is unrelated
    )
    malformed = ModelResponse(parts=[TextPart("not-json")])
    wrong_units = ModelResponse(parts=[TextPart('{"units":[null],"version":1}')])
    assert (
        harness_models._normalize_summary_response(  # noqa: SLF001
            _summary_deps(), malformed
        )
        is malformed
    )
    assert (
        harness_models._normalize_summary_response(  # noqa: SLF001
            _summary_deps(), wrong_units
        )
        is wrong_units
    )


@pytest.mark.asyncio
async def test_gateway_generation_cost_is_decimal_ceil_and_byok_remains_unknown() -> None:
    candidate = route("a", "family-a", "provider-one")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["id"] == "generation-one"
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "generation-one",
                    "model": candidate.gateway_model,
                    "provider_name": candidate.provider,
                    "is_byok": calls == 2,
                    "total_cost": "0.0000011",
                    "tokens_prompt": 4,
                    "tokens_completion": 8,
                    "native_tokens_prompt": 5,
                    "native_tokens_completion": 9,
                    "native_tokens_cached": 2,
                    "native_tokens_reasoning": 3,
                    "native_tokens_cache_creation": 0,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = GatewayConfig(api_key="test-key")
        reported = await lookup_generation(
            client,
            config=config,
            route=candidate,
            generation_id="generation-one",
        )
        byok = await lookup_generation(
            client,
            config=config,
            route=candidate,
            generation_id="generation-one",
        )
    assert reported.actual_cost_micros == 2
    assert reported.components["totalCost"] == str(Decimal("0.0000011"))
    assert reported.components["inputTokens"] == 4
    assert reported.components["outputTokens"] == 8
    assert reported.components["nativeInputTokens"] == 5
    assert reported.components["nativeOutputTokens"] == 9
    assert reported.components["cachedInputTokens"] == 2
    assert reported.components["reasoningTokens"] == 3
    assert reported.components["cacheCreationTokens"] == 0
    assert byok.status == "byok_unknown"
    assert byok.actual_cost_micros is None
    assert calls == 2


@pytest.mark.asyncio
async def test_gateway_generation_missing_and_wrong_identity_are_not_settled() -> None:
    candidate = route("a", "family-a", "provider-one")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        _ = request
        calls += 1
        if calls == 1:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "different-generation",
                    "model": candidate.gateway_model,
                    "provider_name": candidate.provider,
                    "is_byok": False,
                    "total_cost": "0.25",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = GatewayConfig(api_key="test-key")
        pending = await lookup_generation(
            client,
            config=config,
            route=candidate,
            generation_id="generation-one",
        )
        with pytest.raises(GenerationIdentityError):
            await lookup_generation(
                client,
                config=config,
                route=candidate,
                generation_id="generation-one",
            )
    assert pending.status == "pending"
    assert pending.actual_cost_micros is None
    assert calls == 2


@pytest.mark.asyncio
async def test_gateway_money_parses_raw_json_without_float_rounding() -> None:
    candidate = route("a", "family-a", "provider-one")
    body = (
        b'{"data":{"id":"generation-one","model":"model/family-a",'
        b'"provider_name":"provider-one","is_byok":false,'
        b'"total_cost":0.0000010000000000000000001}}'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observed = await lookup_generation(
            client,
            config=GatewayConfig(api_key="test-key"),
            route=candidate,
            generation_id="generation-one",
        )
    assert observed.actual_cost_micros == 2
    assert observed.components["totalCost"] == "0.0000010000000000000000001"
    assert observed.components["inputTokens"] is None
    assert observed.components["reasoningTokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"[]", b"null", b"1"])
async def test_gateway_generation_rejects_non_object_receipts(body: bytes) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError, match="must be a JSON object"):
            await lookup_generation(
                client,
                config=GatewayConfig(api_key="test-key"),
                route=route("a", "family-a", "provider-one"),
                generation_id="generation-one",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(("amount", "expected"), [("0.0000505", 51), ("0", 0), ("1", 1_000_000)])
async def test_gateway_generation_accepts_integer_money_without_float_conversion(
    amount: str, expected: int
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        body = (
            '{"data":{"id":"generation-one","model":"model/family-a",'
            '"provider_name":"provider-one","is_byok":false,'
            f'"total_cost":{amount},"gateway_cost":{amount},"upstream_inference_cost":0}}}}'
        )
        return httpx.Response(200, content=body.encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observed = await lookup_generation(
            client,
            config=GatewayConfig(api_key="test-key"),
            route=route("a", "family-a", "provider-one"),
            generation_id="generation-one",
        )
    assert observed.actual_cost_micros == expected
    assert observed.components["upstreamInferenceCost"] == "0"


class StrictAnswer(BaseModel):
    """Strict response schema used to inspect the gateway request shape."""

    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


@pytest.mark.asyncio
async def test_gateway_429_is_one_request_with_pinned_privacy_shape() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(json.loads(request.content))
        return httpx2.Response(
            429,
            request=request,
            json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = GatewayChatModel(
            route("gateway", "family-a", "provider-one"),
            GatewayConfig(api_key="test-key"),
            http_client=client,
        )
        agent = Agent(
            model,
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
            model_settings={"max_tokens": 24},
        )
        with pytest.raises(ModelHTTPError) as caught:
            await agent.run("answer")
    assert caught.value.status_code == 429
    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert calls[0]["max_completion_tokens"] == 24
    assert calls[0]["providerOptions"] == {
        "gateway": {"only": ["provider-one"], "zeroDataRetention": True}
    }


@pytest.mark.asyncio
async def test_gateway_timeout_is_one_request() -> None:
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("synthetic timeout", request=request)  # noqa: EM101, TRY003

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = GatewayChatModel(
            route("gateway", "family-a", "provider-one"),
            GatewayConfig(api_key="test-key"),
            http_client=client,
        )
        agent = Agent(
            model,
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with pytest.raises(ModelAPIError):
            await agent.run("answer")
    assert calls == 1


@pytest.mark.asyncio
async def test_malformed_strict_output_is_one_request() -> None:
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {"content": '{"wrong":"shape"}', "role": "assistant"},
                    }
                ],
                "created": 1,
                "id": "generation-malformed",
                "model": "model/family-a",
                "object": "chat.completion",
                "usage": {"completion_tokens": 2, "prompt_tokens": 3, "total_tokens": 5},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = GatewayChatModel(
            route("gateway", "family-a", "provider-one"),
            GatewayConfig(api_key="test-key"),
            http_client=client,
        )
        agent = Agent(
            model,
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
        )
        with pytest.raises(UnexpectedModelBehavior):
            await agent.run("answer")
    assert calls == 1
