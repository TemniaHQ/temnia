"""Explicit gateway transports with durable generation observation and charge lookup."""

# Domain exception names and refusal-site messages are part of this transport API.
# ruff: noqa: EM101, EM102, TC001, TC002, TRY003

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import time
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, cast

import httpx
import httpx2
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import (
    ModelRequestContext,
    ModelRequestParameters,
    StreamedResponse,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIStreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.harness.gateway_policy import (
    GATEWAY_URLS,
    GatewayName,
    effective_total_timeout_seconds,
)
from temnia_pipeline.harness.routes import RouteEntry

if TYPE_CHECKING:
    from types import TracebackType


GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
USD_TO_MICROS = Decimal(1_000_000)
HTTP_NOT_FOUND = 404
COST_LOOKUP_WAIT_SECONDS = 20.0
MAX_COST_LOOKUP_WAIT_SECONDS = 30.0


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
    gateway: GatewayName = Field(default="vercel", exclude_if=lambda value: value == "vercel")
    base_url: Literal["https://ai-gateway.vercel.sh/v1", "https://openrouter.ai/api/v1"] = (
        GATEWAY_BASE_URL
    )
    request_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 300
    lookup_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 10

    @model_validator(mode="before")
    @classmethod
    def _resolve_url(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        values = cast("dict[str, Any]", value)
        if values.get("gateway") == "openrouter" and "base_url" not in values:
            return {**values, "base_url": GATEWAY_URLS["openrouter"]}
        return values

    @model_validator(mode="after")
    def _gateway_url(self) -> Self:
        if self.base_url != GATEWAY_URLS[self.gateway]:
            raise ValueError("gateway URL differs from its allowlisted transport")
        return self


def gateway_name(route: RouteEntry) -> GatewayName:
    """Absent historical transport means the original Vercel path only."""
    return route.transport.gateway if route.transport is not None else "vercel"


def gateway_output_token_parameter(
    route: RouteEntry,
) -> Literal["max_tokens", "max_completion_tokens"]:
    """Version 2 pins endpoint encoding; version 1 keeps its original gateway convention."""
    if route.transport is not None and route.transport.version == "gateway-transport/2":
        parameter = route.transport.output_token_parameter
        if parameter is None:
            raise GatewayPolicyError("version 2 route lacks its output-token parameter")
        return parameter
    return "max_tokens" if gateway_name(route) == "openrouter" else "max_completion_tokens"


def gateway_accounting_model(route: RouteEntry) -> str:
    """Use the separately frozen accounting identity without learning aliases from a charge."""
    return route.accounting_model if route.accounting_model is not None else route.gateway_model


def expected_gateway_body(route: RouteEntry) -> dict[str, Any]:
    """The exact privacy/routing fragment shared with request qualification."""
    if gateway_name(route) == "openrouter":
        return {
            "provider": {
                "only": [route.provider],
                "order": [route.provider],
                "allow_fallbacks": False,
                "require_parameters": True,
                "zdr": True,
                "data_collection": "deny",
            }
        }
    return {"providerOptions": {"gateway": {"only": [route.provider], "zeroDataRetention": True}}}


def expected_gateway_headers(route: RouteEntry) -> dict[str, str]:
    """Explicit OpenRouter response-cache and observed-routing controls."""
    if gateway_name(route) == "openrouter":
        return {"X-OpenRouter-Cache": "false", "X-OpenRouter-Metadata": "enabled"}
    return {}


def validate_gateway_request(  # noqa: C901, PLR0912
    request: httpx2.Request, route: RouteEntry
) -> dict[str, Any]:
    """Validate the actual wire without reading or buffering any response."""
    name = gateway_name(route)
    if request.method != "POST" or str(request.url) != GATEWAY_URLS[name] + "/chat/completions":
        raise GatewayPolicyError("unexpected gateway request endpoint")
    raw = json.loads(request.content)
    if not isinstance(raw, dict):
        raise GatewayPolicyError("request is not a JSON object")
    body = cast("dict[str, Any]", raw)
    if body.get("model") != route.gateway_model:
        raise GatewayPolicyError("request model differs from the qualified route")
    if body.get("store") is not False or any(
        body.get(k) != v for k, v in expected_gateway_body(route).items()
    ):
        raise GatewayPolicyError("request privacy/routing differs from the qualified route")
    if any(request.headers.get(k) != v for k, v in expected_gateway_headers(route).items()):
        raise GatewayPolicyError("request cache/metadata controls differ")
    if {
        "models",
        "route",
        "plugins",
        "transforms",
        "preset",
        "tools",
        "tool_choice",
        "cache",
    } & body.keys():
        raise GatewayPolicyError("unqualified gateway request options")
    if name == "openrouter" and "providerOptions" in body:
        raise GatewayPolicyError("OpenRouter request contains Vercel policy")
    if name == "vercel" and "provider" in body:
        raise GatewayPolicyError("Vercel request contains foreign provider policy")
    output_key = gateway_output_token_parameter(route)
    forbidden_output_key = "max_completion_tokens" if output_key == "max_tokens" else "max_tokens"
    maximum = body.get(output_key)
    if (
        type(maximum) is not int
        or not 0 < maximum <= route.max_output_tokens
        or forbidden_output_key in body
    ):
        raise GatewayPolicyError("request output allowance differs from route capacity")
    expected_stream = route.transport is not None and route.transport.mode == "streaming"
    if body.get("stream", False) is not expected_stream:
        raise GatewayPolicyError("request mode differs from frozen transport")
    if name == "openrouter":
        reasoning = (
            {"effort": route.reasoning_effort} if route.reasoning_effort is not None else None
        )
        if body.get("reasoning") != reasoning or "reasoning_effort" in body:
            raise GatewayPolicyError("OpenRouter reasoning differs from the route")
    elif body.get("reasoning_effort") != route.reasoning_effort:
        raise GatewayPolicyError("request reasoning differs from the route")
    if body.get("service_tier") != route.service_tier:
        raise GatewayPolicyError("request service tier differs from the route")
    schema = body.get("response_format", {})
    if (
        schema.get("type") != "json_schema"
        or schema.get("json_schema", {}).get("strict") is not True
    ):
        raise GatewayPolicyError("request must carry native strict JSON schema")
    if route.transport is not None:
        timeout = request.extensions.get("timeout", {})
        if not timeout or any(
            value != route.transport.request_timeout_seconds for value in timeout.values()
        ):
            raise GatewayPolicyError("HTTP timeouts differ from the frozen route")
    return body


GenerationObserver = Callable[[str], Awaitable[None]]
_generation_observer: ContextVar[GenerationObserver | None] = ContextVar(
    "gateway_generation_observer", default=None
)
_dispatch_payload_bytes: ContextVar[int] = ContextVar("gateway_dispatch_payload_bytes", default=0)


@contextlib.contextmanager
def dispatch_payload_bytes(payload_bytes: int) -> Generator[None]:
    """Bind the admitted payload size that scales this dispatch's aggregate deadline."""
    token = _dispatch_payload_bytes.set(payload_bytes)
    try:
        yield
    finally:
        _dispatch_payload_bytes.reset(token)


@contextlib.contextmanager
def observe_gateway_generation(callback: GenerationObserver) -> Generator[None]:
    """Bind one task-local durable observer for an already committed paid request."""
    token = _generation_observer.set(callback)
    try:
        yield
    finally:
        _generation_observer.reset(token)


@dataclass
class _RequestObservation:
    route: RouteEntry
    callback: GenerationObserver | None
    generation_id: str | None = None
    response_opened: bool = False
    response_headers: dict[str, str] | None = None

    async def identify(self, identity: str) -> None:
        if not identity or identity.strip() != identity:
            raise GenerationIdentityError("invalid observed generation ID")
        if self.generation_id is not None:
            if self.generation_id != identity:
                raise GenerationIdentityError("gateway generation ID changed during the request")
            return
        if self.callback is not None:
            await self.callback(identity)
        self.generation_id = identity


_request_observation: ContextVar[_RequestObservation | None] = ContextVar(
    "gateway_request_observation", default=None
)


async def _validate_http_request(request: httpx2.Request) -> None:
    observed = _request_observation.get()
    if observed is not None and observed.route.transport is not None:
        validate_gateway_request(request, observed.route)


async def _observe_http_response(response: httpx2.Response) -> None:
    observed = _request_observation.get()
    if observed is None or not response.is_success:
        return
    observed.response_opened = True
    observed.response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"x-generation-id", "x-openrouter-provider", "x-openrouter-model"}
    }
    if identity := response.headers.get("x-generation-id"):
        await observed.identify(identity)


def _openrouter_metadata(response: BaseModel) -> dict[str, Any]:
    observed = _request_observation.get()
    if observed is None or gateway_name(observed.route) != "openrouter":
        return {}
    extra = response.model_extra or {}
    result: dict[str, Any] = {}
    metadata = extra.get("openrouter_metadata")
    if isinstance(metadata, dict):
        result["openrouter_metadata"] = cast("dict[str, Any]", metadata)
    provider = extra.get("provider")
    if isinstance(provider, str):
        result["gatewayProvider"] = provider
    return result


class _ObservedStreamedResponse(OpenAIStreamedResponse):
    async def _validate_response(self) -> AsyncIterable[ChatCompletionChunk]:
        async for chunk in super()._validate_response():
            self.provider_details = {**(self.provider_details or {}), **_openrouter_metadata(chunk)}
            observed = _request_observation.get()
            if observed is not None and chunk.id:
                await observed.identify(chunk.id)
            yield chunk


class _ObservedChatModel(OpenAIChatModel):
    def _process_provider_details(self, response: ChatCompletion) -> dict[str, Any] | None:
        return {
            **(super()._process_provider_details(response) or {}),
            **_openrouter_metadata(response),
        }

    @property
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return _ObservedStreamedResponse


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
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    native_tokens_prompt: int | None = None
    native_tokens_completion: int | None = None
    native_tokens_cached: int | None = None
    native_tokens_reasoning: int | None = None
    native_tokens_cache_creation: int | None = None

    @model_validator(mode="after")
    def _finite_nonnegative(self) -> Self:
        for name in ("total_cost", "gateway_cost", "upstream_inference_cost"):
            value = getattr(self, name)
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in (
            "tokens_prompt",
            "tokens_completion",
            "native_tokens_prompt",
            "native_tokens_completion",
            "native_tokens_cached",
            "native_tokens_reasoning",
            "native_tokens_cache_creation",
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
    if config.gateway != gateway_name(route):
        raise GatewayPolicyError("charge lookup gateway differs from the frozen route")
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
    return parse_generation_observation(
        response.content, route=route, gateway=config.gateway, generation_id=generation_id
    )


def parse_generation_observation(
    raw: bytes,
    *,
    route: RouteEntry,
    gateway: GatewayName,
    generation_id: str,
) -> CostObservation:
    """Parse one original successful accounting response without I/O or inferred charges."""
    if gateway != gateway_name(route):
        raise GatewayPolicyError("accounting gateway differs from its frozen route")
    raw_body = raw
    raw_value = json.loads(raw_body, parse_float=Decimal)
    if not isinstance(raw_value, dict):
        raise GatewayError("gateway generation response must be a JSON object")
    raw_mapping = cast("dict[str, Any]", raw_value)
    data_value = raw_mapping.get("data")
    if isinstance(data_value, dict):
        data_mapping = cast("dict[str, Any]", data_value)
        for name in ("total_cost", "gateway_cost", "upstream_inference_cost"):
            value = data_mapping.get(name)
            if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
                data_mapping[name] = Decimal(value)
    envelope = GenerationEnvelope.model_validate(raw_mapping, strict=True)
    data = envelope.data
    if (
        data.id != generation_id
        or data.model != gateway_accounting_model(route)
        or data.provider_name
        != (route.provider_accounting_name if gateway == "openrouter" else route.provider)
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
        "inputTokens": data.tokens_prompt,
        "outputTokens": data.tokens_completion,
        "nativeInputTokens": data.native_tokens_prompt,
        "nativeOutputTokens": data.native_tokens_completion,
        "cachedInputTokens": data.native_tokens_cached,
        "reasoningTokens": data.native_tokens_reasoning,
        "cacheCreationTokens": data.native_tokens_cache_creation,
    }
    if gateway == "openrouter":
        components.update(
            {
                "gateway": "openrouter",
                "receiptSha256": hashlib.sha256(raw_body).hexdigest(),
                "lookupUrl": GATEWAY_URLS[gateway] + "/generation",
            }
        )
    if data.is_byok:
        return CostObservation(
            status="byok_unknown", actual_cost_micros=None, components=components
        )
    if data.total_cost is None:
        return CostObservation(status="pending", actual_cost_micros=None, components=components)
    return CostObservation(
        status="reported", actual_cost_micros=_micros(data.total_cost), components=components
    )


async def observe_generation_cost(  # noqa: PLR0913
    client: httpx.AsyncClient,
    *,
    config: GatewayConfig,
    route: RouteEntry,
    generation_id: str,
    wait_seconds: float = COST_LOOKUP_WAIT_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CostObservation:
    """Wait finitely for a known generation's receipt; never repeat inference.

    The wait budget includes the first lookup. Zero means one lookup with its
    configured timeout and no polling. Only an explicitly pending result permits
    further lookups. Errors, identity mismatches, BYOK and cancellation propagate.
    """
    if not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= MAX_COST_LOOKUP_WAIT_SECONDS:
        raise ValueError("cost lookup wait must be finite and between zero and 30 seconds")
    deadline = monotonic() + wait_seconds
    first_timeout = (
        min(config.lookup_timeout_seconds, wait_seconds)
        if wait_seconds > 0
        else config.lookup_timeout_seconds
    )
    try:
        async with asyncio.timeout(first_timeout):
            observation = await lookup_generation(
                client, config=config, route=route, generation_id=generation_id
            )
    except TimeoutError as error:
        raise httpx.ReadTimeout("generation cost lookup exceeded its bounded time limit") from error
    while observation.status == "pending":
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        await sleep(min(2.0, remaining))
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                observation = await lookup_generation(
                    client, config=config, route=route, generation_id=generation_id
                )
        except TimeoutError:
            break
    return observation


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
        if config.gateway != gateway_name(route):
            raise GatewayPolicyError("configured gateway differs from the frozen route")
        self._owned_http_client = (
            httpx2.AsyncClient() if http_client is None and route.transport is not None else None
        )
        http_client = http_client or self._owned_http_client
        if (
            http_client is not None
            and _observe_http_response not in http_client.event_hooks["response"]
        ):
            http_client.event_hooks["response"].append(_observe_http_response)
        if (
            http_client is not None
            and _validate_http_request not in http_client.event_hooks["request"]
        ):
            http_client.event_hooks["request"].append(_validate_http_request)
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
            timeout=route.transport.request_timeout_seconds
            if route.transport is not None
            else config.request_timeout_seconds,
            http_client=http_client,
        )
        provider = OpenAIProvider(openai_client=client)
        profile = OpenAIModelProfile(
            supports_json_schema_output=True,
            openai_chat_supports_max_completion_tokens=(
                gateway_output_token_parameter(route) == "max_completion_tokens"
            ),
            openai_chat_streaming_requires_finish_reason=True,
        )
        super().__init__(
            _ObservedChatModel(cast("Any", route.gateway_model), provider=provider, profile=profile)
        )
        self.route = route

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        """Close only the explicit transport client created by this wrapper."""
        try:
            return await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self._owned_http_client is not None:
                await self._owned_http_client.aclose()

    async def request(  # noqa: C901, PLR0912, PLR0915
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
        if self.route.transport is not None and "timeout" in settings:
            raise GatewayPolicyError("request settings cannot override frozen transport timeouts")
        controlled_headers = expected_gateway_headers(self.route)
        existing_headers = dict(cast("dict[str, str]", settings.get("extra_headers") or {}))
        if any(
            key.lower() in {name.lower() for name in controlled_headers} for key in existing_headers
        ):
            raise GatewayPolicyError("request settings cannot override gateway headers")
        settings["max_tokens"] = requested_max or self.route.max_output_tokens
        settings["extra_body"] = expected_gateway_body(self.route)
        settings["openai_store"] = False
        if controlled_headers:
            settings["extra_headers"] = {**existing_headers, **controlled_headers}
        if self.route.reasoning_effort is not None:
            if gateway_name(self.route) == "openrouter":
                settings["extra_body"]["reasoning"] = {"effort": self.route.reasoning_effort}
            else:
                settings["openai_reasoning_effort"] = self.route.reasoning_effort
        if gateway_name(self.route) == "openrouter" and "openai_reasoning_effort" in settings:
            raise GatewayPolicyError("OpenRouter reasoning must come from its frozen route")
        if self.route.service_tier is not None:
            settings["openai_service_tier"] = self.route.service_tier
        observed = _RequestObservation(self.route, _generation_observer.get())
        token = _request_observation.set(observed)
        try:
            if self.route.transport is None:
                return await super().request(
                    messages, cast("ModelSettings", settings), model_request_parameters
                )
            payload_bytes = _dispatch_payload_bytes.get()
            aggregate_deadline = effective_total_timeout_seconds(
                self.route.transport, payload_bytes
            )
            async with asyncio.timeout(aggregate_deadline):
                if self.route.transport.mode == "streaming":
                    async with super().request_stream(
                        messages, cast("ModelSettings", settings), model_request_parameters
                    ) as streamed:
                        async for _event in streamed:
                            pass
                        response = streamed.get()
                    if response.state != "complete" or response.finish_reason is None:
                        raise GatewayError("gateway stream did not reach terminal completion")  # noqa: TRY301
                else:
                    response = await super().request(
                        messages, cast("ModelSettings", settings), model_request_parameters
                    )
                if response.provider_response_id:
                    await observed.identify(response.provider_response_id)
                response.provider_details = {
                    **(response.provider_details or {}),
                    "gatewayTransport": self.route.transport.model_dump(mode="json"),
                    "gatewayDeadline": {
                        "payloadBytes": payload_bytes,
                        "totalTimeoutSecondsPerUnit": self.route.transport.total_timeout_seconds,
                        "effectiveTotalTimeoutSeconds": aggregate_deadline,
                    },
                    "responseHeaders": observed.response_headers or {},
                }
                return response
        except Exception as error:
            if observed.response_opened:
                # A numeric error inside a successful HTTP stream is never an initial rejection.
                raise ModelAPIError(
                    model_name=self.route.gateway_model,
                    message="gateway response ended without complete durable output",
                ) from error
            raise
        finally:
            _request_observation.reset(token)

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
