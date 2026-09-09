"""Workflow-safe records for the opt-in checkpointed speech path."""

# ruff: noqa: EM101, TC001, TRY003

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from temnia_pipeline.speech.contracts import (
    AlignConfig,
    ArtifactRef,
    DiarizeConfig,
    RecognizeConfig,
    Stage,
    StageTelemetry,
)
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    AssignmentArtifactRef,
    ExecutionTopology,
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile

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
    resource_profile: SpeechResourceProfile | None = None
    model_manifest: SpeechModelManifest | None = None
    execution_topology: ExecutionTopology | None = None
    recognize_v2: RecognizeConfigV2 | None = None
    speaker_turns: SpeakerTurnsConfig | None = None
    allow_oom_recovery: bool | None = None

    @model_validator(mode="after")
    def validate_protocol_resources(self) -> TranscriptionPlan:
        """Require complete v2 identity without changing v1 history defaults."""
        if self.protocol == "temnia-speech/2":
            if (
                self.resource_profile is None
                or self.model_manifest is None
                or self.execution_topology is None
                or self.recognize_v2 is None
                or self.speaker_turns is None
                or self.allow_oom_recovery is None
            ):
                raise ValueError(
                    "speech/2 plan requires resources, models, topology and v2 configs"
                )
            if (
                self.stage_timeout_seconds != self.resource_profile.stage_timeout_seconds
                or self.startup_timeout_seconds != self.resource_profile.startup_timeout_seconds
            ):
                raise ValueError("speech/2 plan deadlines must match its resource profile")
            self.resource_profile.reservation_micros(self.rate_micros_per_hour)
        elif any(
            value is not None
            for value in (
                self.resource_profile,
                self.model_manifest,
                self.execution_topology,
                self.recognize_v2,
                self.speaker_turns,
                self.allow_oom_recovery,
            )
        ):
            raise ValueError("speech/1 plan cannot carry speech/2 identity fields")
        return self


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


class AcceptedSpeechStageV2(BaseModel):
    """One accepted protocol-v2 GPU stage."""

    model_config = _WIRE

    stage: StageV2
    artifact_id: UUID
    checkpoint: ArtifactRefV2
    operation_id: UUID
    attempt_id: UUID | None
    call_id: str | None
    telemetry: StageTelemetry | None = None
    reused: bool = False


class AcceptedSpeechAssignment(BaseModel):
    """Attempt-free CPU assignment over accepted alignment and turns."""

    model_config = _WIRE

    artifact_id: UUID
    artifact: AssignmentArtifactRef
    operation_id: UUID
    reused: bool = False


class ParallelCheckpointedTranscription(BaseModel):
    """Three accepted GPU refs plus their immutable CPU join."""

    model_config = _WIRE

    run_id: UUID
    recognize: AcceptedSpeechStageV2
    align: AcceptedSpeechStageV2
    speaker_turns: AcceptedSpeechStageV2
    assignment: AcceptedSpeechAssignment


class CoverageRecord(BaseModel):
    """Independent evidence artifact, or a visible detector failure."""

    model_config = _WIRE

    artifact_id: UUID | None = None
    error: str | None = None


def alignment_config(language: str) -> AlignConfig:
    """Freeze the detected language into the downstream stage identity."""
    return AlignConfig(language=language)


def alignment_config_v2(language: str) -> AlignConfigV2:
    """Freeze detected language into the v2 alignment operation."""
    return AlignConfigV2(language=language)
