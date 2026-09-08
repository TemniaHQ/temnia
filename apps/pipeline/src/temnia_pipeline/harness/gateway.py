"""Vercel AI Gateway transport construction and bounded charge reconciliation."""

# Domain exception names and refusal-site messages are part of this transport API.
# ruff: noqa: EM101, EM102, TC001, TC002, TC003, TRY003

from __future__ import annotations

import contextlib
import json
import math
from collections.abc import AsyncGenerator
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self, cast

import httpx
import httpx2
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import (
    ModelRequestContext,
    ModelRequestParameters,
    StreamedResponse,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.harness.routes import RouteEntry

GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
USD_TO_MICROS = Decimal(1_000_000)
HTTP_NOT_FOUND = 404


class GatewayError(RuntimeError):
    """Base class for expected gateway transport refusals."""


class GatewayPolicyError(GatewayError):
    """A request tries to weaken the route's qualified policy."""


class GenerationIdentityError(GatewayError):
    """A charge lookup does not describe the attempted generation."""


class GatewayConfig(BaseModel):
    """Process-only gateway credential and bounded HTTP behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    api_key: Annotated[str, Field(min_length=1)]
    base_url: Literal["https://ai-gateway.vercel.sh/v1"] = GATEWAY_BASE_URL
    request_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 300
    lookup_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 10


class GenerationData(BaseModel):
    """Fields needed from the gateway generation accounting response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    model: str
    provider_name: str
    is_byok: bool
    total_cost: Decimal | None = None
    gateway_cost: Decimal | None = None
    upstream_inference_cost: Decimal | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    @model_validator(mode="after")
    def _finite_nonnegative(self) -> Self:
        for name in ("total_cost", "gateway_cost", "upstream_inference_cost"):
            value = getattr(self, name)
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be nonnegative")
        return self


class GenerationEnvelope(BaseModel):
    """Gateway response envelope."""

    model_config = ConfigDict(extra="ignore", strict=True)

    data: GenerationData


class CostObservation(BaseModel):
    """A known non-BYOK charge or an explicitly retained unknown exposure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["reported", "pending", "byok_unknown"]
    actual_cost_micros: Annotated[int | None, Field(ge=0)]
    components: dict[str, str | int | bool | None]


def _micros(value: Decimal) -> int:
    try:
        amount = value * USD_TO_MICROS
    except InvalidOperation as error:
        raise GatewayError("gateway returned an invalid monetary value") from error
    if not amount.is_finite() or amount < 0:
        raise GatewayError("gateway returned a negative or nonfinite monetary value")
    return math.ceil(amount)


async def lookup_generation(
    client: httpx.AsyncClient,
    *,
    config: GatewayConfig,
    route: RouteEntry,
    generation_id: str,
) -> CostObservation:
    """Read one generation charge with no retries and validate its route identity."""
    response = await client.get(
        f"{config.base_url}/generation",
        params={"id": generation_id},
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=config.lookup_timeout_seconds,
    )
    if response.status_code == HTTP_NOT_FOUND:
        return CostObservation(
            status="pending",
            actual_cost_micros=None,
            components={"generationId": generation_id, "reason": "usage_not_found"},
        )
    response.raise_for_status()
    raw = cast("dict[str, Any]", json.loads(response.content, parse_float=Decimal))
    data_value = raw.get("data")
    if isinstance(data_value, dict):
        data_mapping = cast("dict[str, Any]", data_value)
        for name in ("total_cost", "gateway_cost", "upstream_inference_cost"):
            value = data_mapping.get(name)
            if isinstance(value, str):
                data_mapping[name] = Decimal(value)
    envelope = GenerationEnvelope.model_validate(raw, strict=True)
    data = envelope.data
    if (
        data.id != generation_id
        or data.model != route.gateway_model
        or data.provider_name != route.provider
    ):
        raise GenerationIdentityError("generation lookup identity differs from the attempted route")
    components: dict[str, str | int | bool | None] = {
        "generationId": data.id,
        "model": data.model,
        "provider": data.provider_name,
        "isByok": data.is_byok,
        "totalCost": str(data.total_cost) if data.total_cost is not None else None,
        "gatewayCost": str(data.gateway_cost) if data.gateway_cost is not None else None,
        "upstreamInferenceCost": (
            str(data.upstream_inference_cost) if data.upstream_inference_cost is not None else None
        ),
        "inputTokens": data.input_tokens,
        "outputTokens": data.output_tokens,
        "cachedInputTokens": data.cached_input_tokens,
        "reasoningTokens": data.reasoning_tokens,
    }
    if data.is_byok:
        return CostObservation(
            status="byok_unknown", actual_cost_micros=None, components=components
        )
    if data.total_cost is None:
        return CostObservation(status="pending", actual_cost_micros=None, components=components)
    return CostObservation(
        status="reported", actual_cost_micros=_micros(data.total_cost), components=components
    )


class GatewayChatModel(WrapperModel):
    """Enforce the qualified route options around one no-retry OpenAI client."""

    route: RouteEntry

    def __init__(
        self,
        route: RouteEntry,
        config: GatewayConfig,
        *,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=config.request_timeout_seconds,
            http_client=http_client,
        )
        provider = OpenAIProvider(openai_client=client)
        profile = OpenAIModelProfile(
            supports_json_schema_output=True,
            openai_chat_supports_max_completion_tokens=True,
        )
        super().__init__(
            OpenAIChatModel(cast("Any", route.gateway_model), provider=provider, profile=profile)
        )
        self.route = route

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Reject tools/images and inject non-overridable ZDR/provider routing."""
        if (
            model_request_parameters.function_tools
            or model_request_parameters.native_tools
            or model_request_parameters.output_tools
        ):
            raise GatewayPolicyError("tools are disabled on the initial harness model path")
        if model_request_parameters.allow_image_output:
            raise GatewayPolicyError("image output is disabled on the initial harness model path")
        if model_request_parameters.output_mode != "native":
            raise GatewayPolicyError("the harness requires native strict JSON schema output")
        output = model_request_parameters.output_object
        if output is None or output.strict is not True:
            raise GatewayPolicyError("the harness requires a strict output schema")
        settings = dict(model_settings or {})
        forbidden = {"extra_body", "parallel_tool_calls", "openai_store"} & settings.keys()
        if forbidden:
            raise GatewayPolicyError("request settings cannot override gateway privacy policy")
        requested_max = settings.get("max_tokens")
        if requested_max is not None and (
            not isinstance(requested_max, int) or requested_max > self.route.max_output_tokens
        ):
            raise GatewayPolicyError("request max output exceeds the qualified route")
        if self.route.cache_enabled:
            raise GatewayPolicyError(
                "cache transport remains disabled until its request shape is probed"
            )
        gateway_options: dict[str, object] = {
            "zeroDataRetention": True,
            "only": [self.route.provider],
        }
        settings["max_tokens"] = requested_max or self.route.max_output_tokens
        settings["extra_body"] = {"providerOptions": {"gateway": gateway_options}}
        settings["openai_store"] = False
        if self.route.reasoning_effort is not None:
            settings["openai_reasoning_effort"] = self.route.reasoning_effort
        if self.route.service_tier is not None:
            settings["openai_service_tier"] = self.route.service_tier
        return await super().request(
            messages, cast("ModelSettings", settings), model_request_parameters
        )

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        """Forbid hidden token-count requests; reservations use byte bounds."""
        _ = messages, model_settings, model_request_parameters
        raise GatewayPolicyError("network token counting is disabled")

    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        """Forbid provider compaction; workflows summarize explicit finite windows."""
        _ = request_context, instructions
        raise GatewayPolicyError("provider message compaction is disabled")

    @contextlib.asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Forbid streaming until the reservation lifecycle wraps the full stream."""
        _ = messages, model_settings, model_request_parameters, run_context
        raise GatewayPolicyError("streaming is disabled")
        yield  # pragma: no cover


def build_gateway_model(
    route: RouteEntry,
    config: GatewayConfig,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> GatewayChatModel:
    """Construct the SDK client only from activity-side process configuration."""
    return GatewayChatModel(route, config, http_client=http_client)
