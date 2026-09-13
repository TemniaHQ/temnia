"""Endpoint-specific encoding and exact accounting identity through the actual SDK/binder."""

# Public model/provider names, synthetic outputs and in-process HTTP only.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput

from temnia_pipeline.harness.gateway import (
    GatewayChatModel,
    GatewayConfig,
    GenerationIdentityError,
    parse_generation_observation,
    validate_gateway_request,
)
from temnia_pipeline.harness.qualification import CandidateRoute, _provisional_route
from test_harness_model_transport import StrictAnswer
from test_openrouter_qualification import _catalogue, _openrouter

if TYPE_CHECKING:
    from pathlib import Path

CASES = (
    (
        "openai/gpt-6-astra",
        "openai/gpt-6-astra-20260903",
        "azure",
        "Azure",
        "max_completion_tokens",
    ),
    (
        "anthropic/claude-opus-5",
        "anthropic/claude-opus-5-20260723",
        "google-vertex/global",
        "Google",
        "max_tokens",
    ),
    (
        "google/gemini-3.8-flash",
        "google/gemini-3.8-flash-20260902",
        "google-vertex/global",
        "Google",
        "max_tokens",
    ),
    ("moonshotai/kimi-k3", "moonshotai/kimi-k3-20260715", "fireworks", "Fireworks", "max_tokens"),
)


def _candidate(index: int = 0) -> dict[str, Any]:
    candidate = _catalogue(count=1)["candidates"][0]
    model, accounting, provider, display, parameter = CASES[index]
    candidate.update(
        gatewayModel=model,
        accountingModel=accounting,
        provider=provider,
        providerAccountingName=display,
    )
    candidate["transport"].update(version="gateway-transport/2", output_token_parameter=parameter)
    return candidate


def _v2_catalogue() -> dict[str, Any]:
    catalogue = _catalogue()
    for index, candidate in enumerate(catalogue["candidates"]):
        # Keep the three distinct fixture families/open-weight declarations; only the
        # first route needs the new completion key. The other two keep max_tokens.
        candidate["accountingModel"] = candidate["gatewayModel"] + "-frozen-revision"
        candidate["transport"].update(
            version="gateway-transport/2",
            output_token_parameter="max_completion_tokens" if index == 0 else "max_tokens",
        )
    return catalogue


@pytest.mark.parametrize("index", range(4))
async def test_exact_sdk_output_key_preserves_alias_reasoning_and_native_schema(index: int) -> None:
    candidate = CandidateRoute.model_validate(_candidate(index))
    route = _provisional_route(candidate, date(2026, 9, 11))
    wires: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = validate_gateway_request(request, route)
        wires.append(body)
        key = CASES[index][4]
        other = "max_tokens" if key == "max_completion_tokens" else "max_completion_tokens"
        assert body[key] == 24
        assert other not in body
        assert body["model"] == candidate.gateway_model
        assert body["reasoning"] == {"effort": "high"}
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["provider"]["require_parameters"] is True
        assert body["provider"]["allow_fallbacks"] is False
        assert body["provider"]["zdr"] is True
        assert "accountingModel" not in body
        assert "accounting_model" not in body
        chunk = {
            "id": "v2-generation",
            "model": candidate.gateway_model,
            "object": "chat.completion.chunk",
            "created": 1,
            "choices": [
                {"index": 0, "delta": {"content": '{"value":"ok"}'}, "finish_reason": "stop"}
            ],
        }
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "x-generation-id": "v2-generation"},
            content=b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n",
            request=request,
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = GatewayChatModel(
            route, GatewayConfig(api_key="synthetic", gateway="openrouter"), http_client=client
        )
        agent = Agent(
            model,
            output_type=NativeOutput(StrictAnswer, strict=True),
            retries=0,
            model_settings={"max_tokens": 24},
        )
        result = await agent.run("Return the synthetic answer.")
    assert result.output.value == "ok"
    assert len(wires) == 1


@pytest.mark.parametrize("index", range(4))
def test_accounting_requires_exact_frozen_canonical_identity(index: int) -> None:
    candidate = CandidateRoute.model_validate(_candidate(index))
    assert candidate.accounting_model is not None
    route = _provisional_route(candidate, date(2026, 9, 11))
    body = {
        "data": {
            "id": "v2-generation",
            "model": candidate.accounting_model,
            "provider_name": candidate.provider_accounting_name,
            "is_byok": False,
            "total_cost": "0.040225",
        }
    }
    observation = parse_generation_observation(
        json.dumps(body).encode(), route=route, gateway="openrouter", generation_id="v2-generation"
    )
    assert observation.actual_cost_micros == 40225
    assert observation.components["model"] == candidate.accounting_model
    for wrong_model in (candidate.gateway_model, candidate.accounting_model + "-other"):
        body["data"]["model"] = wrong_model
        with pytest.raises(GenerationIdentityError):
            parse_generation_observation(
                json.dumps(body).encode(),
                route=route,
                gateway="openrouter",
                generation_id="v2-generation",
            )


@pytest.mark.parametrize("missing", ["accountingModel", "output_token_parameter"])
def test_new_candidate_requires_both_frozen_identities(missing: str) -> None:
    candidate = _candidate()
    if missing == "accountingModel":
        del candidate[missing]
    else:
        del candidate["transport"][missing]
    with pytest.raises(ValidationError):
        CandidateRoute.model_validate(candidate)


def test_legacy_candidate_keeps_alias_accounting_and_omits_new_fields() -> None:
    raw = _catalogue(count=1)["candidates"][0]
    raw["serviceTier"] = None
    candidate = CandidateRoute.model_validate(raw)
    assert candidate.model_dump(mode="json", by_alias=True) == raw
    route = _provisional_route(candidate, date(2026, 9, 11))
    assert "accounting_model" not in route.model_dump(mode="json")
    assert route.transport is not None
    assert "output_token_parameter" not in route.transport.model_dump(mode="json")
    raw["accountingModel"] = "model/changed-accounting"
    with pytest.raises(ValidationError):
        CandidateRoute.model_validate(raw)


async def test_v2_qualification_records_output_encoding_and_raw_canonical_accounting(
    tmp_path: Path,
) -> None:
    _, _, report, requests = await _openrouter(tmp_path, catalogue_override=_v2_catalogue())
    assert report["status"] == "completed"
    assert report["passed"] is True
    assert len(requests) == 15
    for index, (call, request) in enumerate(zip(report["calls"], requests, strict=True)):
        key = "max_completion_tokens" if index < 5 else "max_tokens"
        assert request[key] == 256
        assert call["request"]["accountingModel"] == call["cost"]["components"]["model"]
        assert call["responseModel"] != call["cost"]["components"]["model"]
