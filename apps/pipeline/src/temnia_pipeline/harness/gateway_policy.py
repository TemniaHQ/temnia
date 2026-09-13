"""Non-secret, immutable gateway behavior carried by newly qualified routes."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

GatewayName = Literal["vercel", "openrouter"]
GatewayRequestMode = Literal["non_streaming", "streaming"]
OutputTokenParameter = Literal["max_tokens", "max_completion_tokens"]
GATEWAY_URLS: dict[GatewayName, str] = {
    "vercel": "https://ai-gateway.vercel.sh/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class GatewayTransportPolicy(BaseModel):
    """Explicit request timing and mode within the existing ten-minute activity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["gateway-transport/1", "gateway-transport/2"] = "gateway-transport/1"
    gateway: GatewayName
    mode: GatewayRequestMode
    # The idle deadline: the longest silence tolerated between bytes on one request.
    # It is used verbatim for every httpx timeout and never scales with payload size.
    request_timeout_seconds: Annotated[float, Field(gt=0, le=540)]
    # The aggregate deadline *unit*, in seconds per `PAYLOAD_DEADLINE_UNIT_BYTES` of
    # serialized request payload -- not the ceiling. A request at or under one unit
    # (Karma's ~90 KB prompts) gets exactly this value, which is what every r-run used;
    # a longer source multiplies it, see `effective_total_timeout_seconds`. The 540 s
    # bound keeps the unit inside one snapshot's frozen, reviewable range.
    total_timeout_seconds: Annotated[float, Field(gt=0, le=540)]
    output_token_parameter: OutputTokenParameter | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _ordered_deadlines(self) -> Self:
        if (self.version == "gateway-transport/2") != (self.output_token_parameter is not None):
            message = (
                "transport version 2 requires an explicit output token parameter; "
                "version 1 preserves its original encoding"
            )
            raise ValueError(message)
        if self.request_timeout_seconds > self.total_timeout_seconds:
            message = "request idle timeout exceeds the aggregate request deadline"
            raise ValueError(message)
        return self


# One aggregate-deadline unit of serialized request payload. Karma's full-source prompts
# are ~90 KB, so they stay at one unit and keep the frozen deadline exactly.
PAYLOAD_DEADLINE_UNIT_BYTES = 128 * 1024
# What a model activity is given beyond the transport's own aggregate deadline, so the
# transport times out first and the harness records the outcome itself.
ACTIVITY_DEADLINE_MARGIN_SECONDS = 60
# No model activity outlives this, whatever the payload scaling computes.
MAX_MODEL_ACTIVITY_SECONDS = 45 * 60
# A route with no frozen transport keeps the original ten-minute model activity.
DEFAULT_MODEL_ACTIVITY_SECONDS = 600


def payload_deadline_multiplier(payload_bytes: int) -> int:
    """Count the whole payload units a request occupies, never fewer than one."""
    if payload_bytes < 0:
        message = "payload bytes must be nonnegative"
        raise ValueError(message)
    units = -(-payload_bytes // PAYLOAD_DEADLINE_UNIT_BYTES)
    return max(1, units)


def effective_total_timeout_seconds(policy: GatewayTransportPolicy, payload_bytes: int) -> float:
    """Scale the frozen aggregate deadline by the payload units this request spans."""
    return policy.total_timeout_seconds * payload_deadline_multiplier(payload_bytes)


def model_activity_timeout_seconds(
    policy: GatewayTransportPolicy | None, payload_bytes: int
) -> float:
    """Bound one model activity just past its own transport deadline."""
    if policy is None:
        return float(DEFAULT_MODEL_ACTIVITY_SECONDS)
    deadline = effective_total_timeout_seconds(policy, payload_bytes)
    return min(deadline + ACTIVITY_DEADLINE_MARGIN_SECONDS, float(MAX_MODEL_ACTIVITY_SECONDS))
