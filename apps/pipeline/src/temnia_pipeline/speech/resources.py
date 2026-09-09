"""Frozen, bounded resource choices for the second speech protocol.

Rates were checked against https://modal.com/pricing on 2026-09-09. They
are reservation floors for capped standard L4 tasks, never invoice charges.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from temnia_pipeline.speech.contracts import canonical_json

_WIRE = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
    frozen=True,
    allow_inf_nan=False,
)


class SpeechResourceProfile(BaseModel):
    """Every scheduling and progress choice sealed into one deployment."""

    model_config = _WIRE

    schema_: Literal["speech-resources/1"] = Field(
        default="speech-resources/1", validation_alias="schema", serialization_alias="schema"
    )
    gpu: Literal["L4"] = "L4"
    cpu_cores: Literal[4, 8] = 4
    memory_mib: Literal[16384] = 16384
    stage_timeout_seconds: Literal[900, 3600] = 3600
    startup_timeout_seconds: Literal[120] = 120
    single_use_containers: Literal[True] = True
    retries: Literal[0] = 0
    progress_mode: Literal["coalesced", "synchronous_control"] = "coalesced"
    rate_date: Literal["2026-09-09"] = "2026-09-09"

    @property
    def sha256(self) -> str:
        """Hash exact wire fields, including diagnostic control mode."""
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json", by_alias=True))
        ).hexdigest()

    @property
    def minimum_rate_micros_per_hour(self) -> int:
        """Ceiling of capped L4 + CPU + memory standard hourly resource price."""
        # $0.000222/GPU-second + $0.0000131/core-second
        # + $0.00000222/GiB-second, represented in nano-dollars.
        nanos_per_second = 222_000 + 13_100 * self.cpu_cores + 2_220 * 16
        return (nanos_per_second * 3600 + 999) // 1000

    def reservation_micros(self, rate_micros_per_hour: int) -> int:
        """Refuse underpriced bookings and bound one startup plus execution."""
        if rate_micros_per_hour < self.minimum_rate_micros_per_hour:
            message = "speech rate is below the frozen resource profile price floor"
            raise ValueError(message)
        seconds = self.stage_timeout_seconds + self.startup_timeout_seconds
        return (rate_micros_per_hour * seconds + 3599) // 3600


class SpeechModelManifest(BaseModel):
    """Identity of a verified offline model directory; its entries live in storage."""

    model_config = _WIRE

    schema_: Literal["speech-model-manifest/1"] = Field(
        default="speech-model-manifest/1", validation_alias="schema", serialization_alias="schema"
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(gt=0)
    total_bytes: int = Field(gt=0)
    model_root: str = Field(pattern=r"^/models/frozen/[0-9a-f]{64}$")
    offline: Literal[True] = True
    image_assets_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def addressed_by_hash(self) -> SpeechModelManifest:
        """Reject a manifest that points at another model directory."""
        if self.model_root != f"/models/frozen/{self.sha256}":
            message = "model root must be addressed by its exact manifest hash"
            raise ValueError(message)
        return self
