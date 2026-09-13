"""Strict wire models shared by every evaluation bundle and report."""

# Validators explain the exact field that failed; the literal messages are the contract.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import math
from datetime import datetime  # noqa: TC003
from typing import Annotated, Literal, Self
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_STORAGE_KEY_LENGTH = 2048
type JSONValue = str | int | float | bool | list[JSONValue] | dict[str, JSONValue] | None
TOKEN_USAGE_FIELDS = frozenset(
    {
        "cache_audio_read_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cachedInputTokens",
        "input_audio_tokens",
        "input_tokens",
        "inputTokens",
        "output_audio_tokens",
        "output_tokens",
        "outputTokens",
        "reasoning_tokens",
        "reasoningTokens",
    }
)


def _validate_finite_json(value: JSONValue, *, path: str = "usage") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        message = f"{path} contains a nonfinite number"
        raise ValueError(message)
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_json(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_json(item, path=f"{path}.{key}")


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class EvaluationModel(BaseModel):
    """Strict immutable camel-case wire model."""

    model_config = ConfigDict(
        alias_generator=_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class AttemptFact(EvaluationModel):
    """Every physical attempt, including failures and unresolved exposure."""

    id: UUID
    operation_id: UUID
    attempt_number: Annotated[int, Field(gt=0)]
    state: Literal[
        "reserved",
        "dispatching",
        "running",
        "succeeded",
        "failed_known",
        "outcome_unknown",
        "cancel_requested",
        "cancelled_confirmed",
    ]
    provider: Annotated[str, Field(min_length=1)]
    model: str | None = None
    family: str | None = None
    route_id: str | None = None
    synthetic: bool
    replayed: bool = False
    estimated_cost_micros: Annotated[int, Field(ge=0)]
    actual_cost_micros: Annotated[int | None, Field(ge=0)] = None
    cost_status: Literal["estimated", "reported", "reconciled", "unknown"]
    reservation_active: bool = False
    response_present: bool
    remote_handle: str | None = None
    result_artifact_id: UUID | None = None
    usage: dict[str, JSONValue]
    dispatched_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("usage")
    @classmethod
    def _finite_usage(cls, value: dict[str, JSONValue]) -> dict[str, JSONValue]:
        _validate_finite_json(value)
        for key in TOKEN_USAGE_FIELDS & value.keys():
            item = value[key]
            if item is not None and (
                not isinstance(item, int) or isinstance(item, bool) or item < 0
            ):
                message = f"usage.{key} must be a nonnegative integer or null"
                raise ValueError(message)
        for snake, camel in (
            ("input_tokens", "inputTokens"),
            ("output_tokens", "outputTokens"),
            ("reasoning_tokens", "reasoningTokens"),
        ):
            if snake in value and camel in value:
                message = f"usage cannot contain both {snake} and {camel}"
                raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _cost_truth(self) -> Self:
        synthetic_providers = {"recorded", "synthetic-recorded"}
        if self.synthetic != (self.provider in synthetic_providers):
            raise ValueError(
                "synthetic attempt flag must match a recorded or synthetic-recorded provider"
            )
        if self.synthetic and self.replayed:
            raise ValueError("an attempt cannot be both synthetic and cassette-replayed")
        if self.cost_status in {"reported", "reconciled"} and self.actual_cost_micros is None:
            raise ValueError("reported or reconciled attempt requires actual cost")
        if self.actual_cost_micros is not None and self.cost_status not in {
            "reported",
            "reconciled",
        }:
            raise ValueError("actual cost requires reported or reconciled provenance")
        if self.synthetic and self.actual_cost_micros not in {None, 0}:
            raise ValueError("synthetic attempts cannot carry a live provider charge")
        if self.finished_at is not None and self.dispatched_at is None:
            raise ValueError("finished attempt requires a dispatch timestamp")
        if (
            self.finished_at is not None
            and self.dispatched_at is not None
            and self.finished_at < self.dispatched_at
        ):
            raise ValueError("attempt finish precedes dispatch")
        return self
