"""Direct vendor adapters: keys, request policy, usage settlement and rate-limit pacing.

A vendor route calls Anthropic, OpenAI or Google through PydanticAI's own adapter with the
vendor SDK's retries off (the harness owns retries), one httpx client per call with the idle
timeout, and an aggregate deadline scaled by payload size. The response carries the usage the
route is priced from; the response headers carry the rate-limit state the route gate paces
from. No gateway, no receipt, no lookup.
"""

# Refusal messages are written at the site that knows the exact policy they enforce.
# ruff: noqa: EM101, EM102, TC002, TC003, TRY003

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import re
from collections.abc import AsyncGenerator, Generator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import httpx2
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.harness.gateway import (
    COLD_SOURCE_TOOL_NAMES,
    EDITORIAL_REVIEW_TOOL_NAMES,
    SOURCE_REVIEW_TOOL_NAMES,
    SOURCE_TOOL_NAMES,
)
from temnia_pipeline.harness.gateway_policy import payload_deadline_multiplier
from temnia_pipeline.harness.routes import (
    TOKENS_PER_PRICE_UNIT,
    VENDOR_KEY_VARIABLES,
    RouteEntry,
    VendorName,
    vendor_key_variable,
)

if TYPE_CHECKING:
    from types import TracebackType

# The idle deadline: the longest silence tolerated between bytes on one request.
VENDOR_IDLE_TIMEOUT_SECONDS = 300.0
# The aggregate deadline per 128 KiB of serialized payload (the gateway transport's unit).
VENDOR_TOTAL_TIMEOUT_SECONDS = 540.0
# When a vendor reports fewer input tokens remaining than this, the route pauses until the
# reset the header names: three of the harness's largest prompts, so a burst never trips 429.
LOW_WATER_TOKENS = 60_000
# The longest pause a header may impose; the ladder in the decision covers anything longer.
MAX_HEADER_PAUSE_SECONDS = 120.0
ANTHROPIC_SPEND_CAP_CODE = "enforced_spend_limit_reached"


class VendorPolicyError(RuntimeError):
    """A request tries to weaken the route's policy on the vendor path."""


@dataclass(frozen=True, slots=True)
class VendorKeys:
    """Process-only API keys, read once at boot; absence is a sentence on the run, not a crash."""

    anthropic: str | None = None
    openai: str | None = None
    google: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> VendorKeys:
        """Read the three vendor keys; `GOOGLE_API_KEY` is accepted as Google's alias."""
        values = os.environ if env is None else env
        return cls(
            anthropic=values.get(VENDOR_KEY_VARIABLES["anthropic"]) or None,
            openai=values.get(VENDOR_KEY_VARIABLES["openai"]) or None,
            google=values.get(VENDOR_KEY_VARIABLES["google"])
            or values.get("GOOGLE_API_KEY")
            or None,
        )

    def key(self, vendor: VendorName) -> str | None:
        """The key for one vendor, or None."""
        return cast("str | None", getattr(self, vendor))

    def missing(self, vendors: frozenset[VendorName]) -> frozenset[VendorName]:
        """The vendors among `vendors` with no key in this process."""
        return frozenset(vendor for vendor in vendors if self.key(vendor) is None)


# ---------------------------------------------------------------------------------------------
# Rate-limit headers
# ---------------------------------------------------------------------------------------------


class RateLimitReading(BaseModel):
    """What one vendor response said about the account's remaining capacity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: VendorName
    remaining_tokens: int | None = None
    tokens_reset_seconds: float | None = None
    remaining_requests: int | None = None
    requests_reset_seconds: float | None = None
    retry_after_seconds: float | None = None
    headers: dict[str, str] = Field(default_factory=dict[str, str])

    def pause_seconds(self, *, low_water_tokens: int = LOW_WATER_TOKENS) -> float:
        """How long the route should hold every dispatch after this response; zero if open."""
        pause = 0.0
        if self.retry_after_seconds is not None:
            pause = max(pause, self.retry_after_seconds)
        if self.remaining_tokens is not None and self.remaining_tokens < low_water_tokens:
            pause = max(pause, self.tokens_reset_seconds or 0.0)
        if self.remaining_requests is not None and self.remaining_requests <= 0:
            pause = max(pause, self.requests_reset_seconds or 0.0)
        return min(pause, MAX_HEADER_PAUSE_SECONDS)


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def duration_seconds(value: str | None) -> float | None:
    """Parse OpenAI's reset durations (`6m0s`, `1s`, `250ms`); junk is None."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    total = 0.0
    position = 0
    for match in _DURATION.finditer(text):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        position = match.end()
    return total if position == len(text) else None


def rfc3339_seconds_from(value: str | None, now: datetime) -> float | None:
    """Seconds from `now` until an RFC 3339 instant (Anthropic's reset headers); junk is None."""
    if value is None:
        return None
    try:
        instant = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return max(0.0, (instant - now).total_seconds())


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def read_rate_limits(
    vendor: VendorName, headers: Mapping[str, str], *, now: datetime | None = None
) -> RateLimitReading:
    """Read the vendor's rate-limit headers into one reading; Gemini reads only retry-after."""
    lowered = {key.lower(): value for key, value in headers.items()}
    moment = now if now is not None else datetime.now(UTC)
    kept = {
        key: value
        for key, value in lowered.items()
        if key.startswith(("anthropic-ratelimit-", "x-ratelimit-")) or key == "retry-after"
    }
    retry_after = _retry_after(lowered.get("retry-after"))
    if vendor == "anthropic":
        return RateLimitReading(
            vendor=vendor,
            remaining_tokens=_integer(
                lowered.get("anthropic-ratelimit-input-tokens-remaining")
                or lowered.get("anthropic-ratelimit-tokens-remaining")
            ),
            tokens_reset_seconds=rfc3339_seconds_from(
                lowered.get("anthropic-ratelimit-input-tokens-reset")
                or lowered.get("anthropic-ratelimit-tokens-reset"),
                moment,
            ),
            remaining_requests=_integer(lowered.get("anthropic-ratelimit-requests-remaining")),
            requests_reset_seconds=rfc3339_seconds_from(
                lowered.get("anthropic-ratelimit-requests-reset"), moment
            ),
            retry_after_seconds=retry_after,
            headers=kept,
        )
    if vendor == "openai":
        return RateLimitReading(
            vendor=vendor,
            remaining_tokens=_integer(lowered.get("x-ratelimit-remaining-tokens")),
            tokens_reset_seconds=duration_seconds(lowered.get("x-ratelimit-reset-tokens")),
            remaining_requests=_integer(lowered.get("x-ratelimit-remaining-requests")),
            requests_reset_seconds=duration_seconds(lowered.get("x-ratelimit-reset-requests")),
            retry_after_seconds=retry_after,
            headers=kept,
        )
    # Gemini publishes no capacity headers; a 429 carries retry-after and the ladder does the rest.
    return RateLimitReading(vendor=vendor, retry_after_seconds=retry_after, headers=kept)


_readings: ContextVar[list[RateLimitReading] | None] = ContextVar(
    "vendor_rate_limit_readings", default=None
)
_observed_vendor: ContextVar[VendorName | None] = ContextVar(
    "vendor_under_observation", default=None
)


@contextlib.contextmanager
def observe_rate_limits() -> Generator[list[RateLimitReading]]:
    """Collect every rate-limit reading the vendor returns during the block."""
    readings: list[RateLimitReading] = []
    token = _readings.set(readings)
    try:
        yield readings
    finally:
        _readings.reset(token)


async def _observe_vendor_response(response: httpx2.Response) -> None:
    readings = _readings.get()
    vendor = _observed_vendor.get()
    if readings is None or vendor is None:
        return
    readings.append(read_rate_limits(vendor, response.headers))


def is_spend_cap(error: ModelHTTPError) -> bool:
    """Anthropic's monthly spend cap is a 429 with no retry-after and a named error code."""
    body = error.body
    if not isinstance(body, dict):
        return False
    inner = cast("dict[str, Any]", body).get("error")
    if not isinstance(inner, dict):
        return False
    details = cast("dict[str, Any]", inner).get("details")
    return isinstance(details, dict) and (
        cast("dict[str, Any]", details).get("error_code") == ANTHROPIC_SPEND_CAP_CODE
    )


def retry_after_seconds(error: ModelHTTPError) -> float | None:
    """The vendor's own pause on a throttled or failed request, from the exception's headers."""
    if not error.headers:
        return None
    return _retry_after(error.headers.get("retry-after"))


# ---------------------------------------------------------------------------------------------
# Usage settlement
# ---------------------------------------------------------------------------------------------


class UsageSettlement(BaseModel):
    """The charge for one response, computed from its usage at the route's prices."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_micros: int
    from_usage: bool
    components: dict[str, int | str]


def settle_usage(route: RouteEntry, usage: RequestUsage) -> UsageSettlement | None:
    """Price the usage a vendor returned; None when the response carried no usage at all.

    PydanticAI's `input_tokens` includes cache reads and writes, so the uncached share is the
    remainder. A route with no cache price charges cache tokens at the input price, which is
    the conservative reading of a vendor that does not bill them separately.
    """
    input_tokens = max(0, usage.input_tokens)
    output_tokens = max(0, usage.output_tokens)
    if input_tokens == 0 and output_tokens == 0:
        return None
    cache_read = min(max(0, usage.cache_read_tokens), input_tokens)
    cache_write = min(max(0, usage.cache_write_tokens), input_tokens - cache_read)
    uncached = input_tokens - cache_read - cache_write
    prices = route.prices
    read_price = prices.cache_read if prices.cache_read is not None else prices.input
    write_price = prices.cache_write if prices.cache_write is not None else prices.input
    numerator = (
        uncached * prices.input
        + cache_read * read_price
        + cache_write * write_price
        + output_tokens * prices.output
    )
    amount = (
        numerator + TOKENS_PER_PRICE_UNIT - 1
    ) // TOKENS_PER_PRICE_UNIT + prices.request_surcharge
    return UsageSettlement(
        amount_micros=amount,
        from_usage=True,
        components={
            "settlement": "usage",
            "uncachedInputTokens": uncached,
            "cacheReadTokens": cache_read,
            "cacheWriteTokens": cache_write,
            "outputTokens": output_tokens,
            "priceUnit": prices.unit,
        },
    )


# ---------------------------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------------------------

ALLOWED_TOOLSETS = frozenset(
    {
        SOURCE_TOOL_NAMES,
        SOURCE_REVIEW_TOOL_NAMES,
        COLD_SOURCE_TOOL_NAMES,
        EDITORIAL_REVIEW_TOOL_NAMES,
    }
)
_dispatch_payload_bytes: ContextVar[int] = ContextVar("vendor_dispatch_payload_bytes", default=0)


@contextlib.contextmanager
def dispatch_payload_bytes(payload_bytes: int) -> Generator[None]:
    """Bind the admitted payload size that scales this dispatch's aggregate deadline."""
    token = _dispatch_payload_bytes.set(payload_bytes)
    try:
        yield
    finally:
        _dispatch_payload_bytes.reset(token)


def effective_vendor_deadline_seconds(payload_bytes: int) -> float:
    """The aggregate deadline for one vendor request of this payload size."""
    return VENDOR_TOTAL_TIMEOUT_SECONDS * payload_deadline_multiplier(payload_bytes)


def vendor_settings(route: RouteEntry, requested: ModelSettings | None) -> ModelSettings:
    """The settings the harness sends for one route: allowance, thinking, tier and caching."""
    settings: dict[str, Any] = dict(requested or {})
    forbidden = {
        "extra_body",
        "parallel_tool_calls",
        "openai_store",
        "extra_headers",
        "timeout",
    } & settings.keys()
    if forbidden:
        raise VendorPolicyError("request settings cannot override the vendor route policy")
    requested_max = settings.get("max_tokens")
    if requested_max is not None and (
        not isinstance(requested_max, int) or requested_max > route.max_output_tokens
    ):
        raise VendorPolicyError("request max output exceeds the route maximum")
    settings["max_tokens"] = requested_max or route.max_output_tokens
    if route.reasoning_effort is not None:
        settings["thinking"] = route.reasoning_effort
    if route.service_tier is not None:
        settings["service_tier"] = route.service_tier
    if route.vendor == "anthropic" and route.cache_enabled:
        # The server places the breakpoint: every round after the first pays a tenth for the
        # prefix, and sibling decisions on the same section share it when their prompts do.
        settings["anthropic_cache"] = True
    if route.vendor == "openai":
        settings["openai_store"] = False
    return cast("ModelSettings", settings)


def _http_client() -> httpx2.AsyncClient:
    client = httpx2.AsyncClient(
        timeout=httpx2.Timeout(timeout=VENDOR_IDLE_TIMEOUT_SECONDS, connect=10.0)
    )
    client.event_hooks["response"].append(_observe_vendor_response)
    return client


def _adapter(route: RouteEntry, key: str, http_client: httpx2.AsyncClient) -> Model:
    vendor = route.vendor
    model_id = route.gateway_model
    if vendor == "anthropic":
        from anthropic import AsyncAnthropic  # noqa: PLC0415
        from pydantic_ai.models.anthropic import AnthropicModel  # noqa: PLC0415
        from pydantic_ai.providers.anthropic import AnthropicProvider  # noqa: PLC0415

        client = AsyncAnthropic(
            api_key=key,
            max_retries=0,
            timeout=VENDOR_IDLE_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        return AnthropicModel(model_id, provider=AnthropicProvider(anthropic_client=client))
    if vendor == "openai":
        from openai import AsyncOpenAI  # noqa: PLC0415
        from pydantic_ai.models.openai import OpenAIResponsesModel  # noqa: PLC0415
        from pydantic_ai.providers.openai import OpenAIProvider  # noqa: PLC0415

        client = AsyncOpenAI(
            api_key=key,
            max_retries=0,
            timeout=VENDOR_IDLE_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        return OpenAIResponsesModel(model_id, provider=OpenAIProvider(openai_client=client))
    if vendor == "google":
        from pydantic_ai.models.google import GoogleModel  # noqa: PLC0415
        from pydantic_ai.providers.google import GoogleProvider  # noqa: PLC0415

        return GoogleModel(model_id, provider=GoogleProvider(api_key=key, http_client=http_client))
    raise VendorPolicyError(f"route {route.id} names no direct vendor")


class VendorModel(WrapperModel):
    """Enforce the route's policy around one no-retry vendor SDK client."""

    route: RouteEntry

    def __init__(
        self,
        route: RouteEntry,
        keys: VendorKeys,
        *,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if route.vendor is None:
            raise VendorPolicyError(f"route {route.id} is not a direct vendor route")
        key = keys.key(route.vendor)
        if key is None:
            raise VendorPolicyError(
                f"{vendor_key_variable(route.vendor)} is not set on the pipeline service, so "
                f"route {route.id} cannot be called"
            )
        self._owned_http_client = _http_client() if http_client is None else None
        client = http_client if http_client is not None else self._owned_http_client
        assert client is not None  # noqa: S101 - one of the two branches assigned it
        if _observe_vendor_response not in client.event_hooks["response"]:
            client.event_hooks["response"].append(_observe_vendor_response)
        super().__init__(_adapter(route, key, client))
        self.route = route

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        """Close only the transport client this wrapper created."""
        try:
            return await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self._owned_http_client is not None:
                await self._owned_http_client.aclose()

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Admit the source tools and the strict output, then make one bounded request."""
        tool_names = frozenset(tool.name for tool in model_request_parameters.function_tools)
        if (
            model_request_parameters.native_tools
            or model_request_parameters.output_tools
            or (tool_names and tool_names not in ALLOWED_TOOLSETS)
        ):
            raise VendorPolicyError("only the indexed source function tools are allowed")
        if model_request_parameters.allow_image_output:
            raise VendorPolicyError("image output is disabled on the harness model path")
        if model_request_parameters.output_mode != "native":
            raise VendorPolicyError("the harness requires native strict JSON schema output")
        output = model_request_parameters.output_object
        if output is None or output.strict is not True:
            raise VendorPolicyError("the harness requires a strict output schema")
        settings = vendor_settings(self.route, model_settings)
        payload_bytes = _dispatch_payload_bytes.get()
        deadline = effective_vendor_deadline_seconds(payload_bytes)
        vendor = self.route.vendor
        assert vendor is not None  # noqa: S101 - the constructor refused otherwise
        token = _observed_vendor.set(vendor)
        try:
            async with asyncio.timeout(deadline):
                response = await super().request(messages, settings, model_request_parameters)
        finally:
            _observed_vendor.reset(token)
        response.provider_details = {
            **(response.provider_details or {}),
            "vendor": vendor,
            "vendorDeadline": {
                "payloadBytes": payload_bytes,
                "idleTimeoutSeconds": VENDOR_IDLE_TIMEOUT_SECONDS,
                "effectiveTotalTimeoutSeconds": deadline,
            },
        }
        return response

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        """Forbid hidden token-count requests; reservations use byte bounds."""
        _ = messages, model_settings, model_request_parameters
        raise VendorPolicyError("network token counting is disabled")

    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        """Forbid provider compaction; decisions are bounded by construction."""
        _ = request_context, instructions
        raise VendorPolicyError("provider message compaction is disabled")

    @contextlib.asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Forbid streaming; one request is one settled response."""
        _ = messages, model_settings, model_request_parameters, run_context
        raise VendorPolicyError("streaming is disabled")
        yield  # pragma: no cover


def build_vendor_model(
    route: RouteEntry,
    keys: VendorKeys,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> VendorModel:
    """Construct the SDK-backed adapter only from activity-side process configuration."""
    return VendorModel(route, keys, http_client=http_client)
