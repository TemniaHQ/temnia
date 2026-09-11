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
    request_timeout_seconds: Annotated[float, Field(gt=0, le=540)]
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
