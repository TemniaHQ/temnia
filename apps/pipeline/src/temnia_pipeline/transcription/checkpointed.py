"""Workflow-safe records for the opt-in checkpointed speech path."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from temnia_pipeline.speech.contracts import (
    AlignConfig,
    ArtifactRef,
    DiarizeConfig,
    RecognizeConfig,
    Stage,
    StageTelemetry,
)

_WIRE = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
    allow_inf_nan=False,
)


class TranscriptionPlan(BaseModel):
    """Provider/config/source identity frozen once for a workflow run."""

    model_config = _WIRE

    backend: Literal["recorded", "modal", "modal-checkpointed"]
    app: str
    environment: str | None
    protocol: str
    build: str
    budget_micros: Annotated[int, Field(ge=0)]
    rate_micros_per_hour: Annotated[int, Field(ge=0)]
    stage_timeout_seconds: Annotated[int, Field(gt=0)]
    startup_timeout_seconds: Annotated[int, Field(gt=0)]
    dispatch_limit: Annotated[int, Field(gt=0)]
    audio_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    audio_size_bytes: Annotated[int | None, Field(ge=0)] = None
    recognize: RecognizeConfig = Field(default_factory=RecognizeConfig)
    diarize: DiarizeConfig = Field(default_factory=DiarizeConfig)
    detector: str
    detector_revision: str
    detector_sha256: str


class AcceptedSpeechStage(BaseModel):
    """One ledger-accepted checkpoint and its physical provenance."""

    model_config = _WIRE

    stage: Stage
    artifact_id: UUID
    checkpoint: ArtifactRef
    operation_id: UUID
    attempt_id: UUID | None
    call_id: str | None
    telemetry: StageTelemetry | None = None
    reused: bool = False


class CheckpointedTranscription(BaseModel):
    """All three accepted stage refs; no transcript arrays cross Temporal."""

    model_config = _WIRE

    run_id: UUID
    recognize: AcceptedSpeechStage
    align: AcceptedSpeechStage
    diarize: AcceptedSpeechStage


class CoverageRecord(BaseModel):
    """Independent evidence artifact, or a visible detector failure."""

    model_config = _WIRE

    artifact_id: UUID | None = None
    error: str | None = None


def alignment_config(language: str) -> AlignConfig:
    """Freeze the detected language into the downstream stage identity."""
    return AlignConfig(language=language)
