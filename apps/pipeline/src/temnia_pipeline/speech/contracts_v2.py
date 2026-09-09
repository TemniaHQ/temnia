"""Additive wire contracts for parallel checkpointed speech."""

# ruff: noqa: D102, EM101, EM102, TC001, TRY003

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from temnia_pipeline.speech.contracts import (
    CheckpointSource,
    ExecutionIdentity,
    PayloadDiagnostics,
    StageError,
    StageModelProvenance,
    StageTelemetry,
    canonical_json,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile

PROTOCOL_V2 = "temnia-speech/2"
CHECKPOINT_SCHEMA_V2 = "speech-checkpoint/2"
ADMISSION_SCHEMA_V2 = "speech-execution-admission/2"
ASSIGNMENT_SCHEMA = "speech-assignment/1"

_WIRE = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
    frozen=True,
    allow_inf_nan=False,
)

StageV2 = Literal["recognize", "align", "speaker_turns"]
ExecutionTopology = Literal["serial", "parallel"]


class ArtifactRefV2(BaseModel):
    """A verified immutable v2 GPU checkpoint."""

    model_config = _WIRE

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    stage: StageV2
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_: Literal["speech-checkpoint/2"] = Field(
        default=CHECKPOINT_SCHEMA_V2,
        validation_alias="schema",
        serialization_alias="schema",
    )


class AssignmentArtifactRef(BaseModel):
    """A verified immutable CPU speaker-assignment artifact."""

    model_config = _WIRE

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    schema_: Literal["speech-assignment/1"] = Field(
        default=ASSIGNMENT_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecognizeConfigV2(BaseModel):
    """Recognition choices for protocol v2."""

    model_config = _WIRE

    stage: Literal["recognize"] = "recognize"
    model: str = "large-v3"
    whisperx_version: Literal["3.8.6"] = "3.8.6"
    compute_type: Literal["float16"] = "float16"
    batch_size: Literal[4, 8, 16] = 16
    language: str | None = None
    vad_method: Literal["pyannote"] = "pyannote"
    vad_options: dict[str, bool | float | int | str | None] = Field(default_factory=dict)


class AlignConfigV2(BaseModel):
    """Alignment choices with recognition language frozen into identity."""

    model_config = _WIRE

    stage: Literal["align"] = "align"
    language: str = Field(min_length=1)
    model: str | None = None
    whisperx_version: Literal["3.8.6"] = "3.8.6"
    return_char_alignments: Literal[False] = False


class SpeakerTurnsConfig(BaseModel):
    """Independent diarization choices; word assignment is a CPU operation."""

    model_config = _WIRE

    stage: Literal["speaker_turns"] = "speaker_turns"
    model: str = "pyannote/speaker-diarization-community-1"
    whisperx_version: Literal["3.8.6"] = "3.8.6"
    min_speakers: int | None = Field(default=None, ge=1)
    max_speakers: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def ordered_speaker_bounds(self) -> SpeakerTurnsConfig:
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("minSpeakers cannot exceed maxSpeakers")
        return self


StageConfigV2 = Annotated[
    RecognizeConfigV2 | AlignConfigV2 | SpeakerTurnsConfig,
    Field(discriminator="stage"),
]


class SpeakerAssignmentConfig(BaseModel):
    """Pinned CPU join semantics kept separate from the GPU model config."""

    model_config = _WIRE

    algorithm: Literal["whisperx-3.8.6-assign-word-speakers"] = (
        "whisperx-3.8.6-assign-word-speakers"
    )
    fill_nearest: Literal[True] = True

    @property
    def sha256(self) -> str:
        body = self.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(canonical_json(body)).hexdigest()


class SpeechStageJobV2(BaseModel):
    """One independently admitted protocol-v2 GPU stage."""

    model_config = _WIRE

    protocol: Literal["temnia-speech/2"] = PROTOCOL_V2
    build: str = Field(min_length=1)
    operation_id: UUID
    attempt_id: UUID
    stage: StageV2
    artifact_prefix: str = Field(min_length=1)
    audio_key: str = Field(min_length=1)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_size_bytes: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    checkpoint_key: str = Field(min_length=1)
    configuration: StageConfigV2
    input_checkpoint: ArtifactRefV2 | None = None
    resource_profile: SpeechResourceProfile
    model_manifest: SpeechModelManifest
    execution_topology: ExecutionTopology

    @model_validator(mode="after")
    def validate_identity(self) -> SpeechStageJobV2:
        if not self.artifact_prefix.endswith("/"):
            raise ValueError("artifactPrefix must end with '/'")
        if not self.audio_key.startswith(self.artifact_prefix):
            raise ValueError("audioKey must be inside artifactPrefix")
        expected = (
            f"{self.artifact_prefix}transcript/v2/checkpoints/{self.stage}/{self.attempt_id}.json"
        )
        if self.checkpoint_key != expected:
            raise ValueError(f"checkpointKey must equal {expected}")
        if self.configuration.stage != self.stage:
            raise ValueError("configuration stage does not match job stage")
        if self.stage == "align":
            if self.input_checkpoint is None or self.input_checkpoint.stage != "recognize":
                raise ValueError("align requires a recognize checkpoint")
            if not self.input_checkpoint.key.startswith(self.artifact_prefix):
                raise ValueError("inputCheckpoint must be inside artifactPrefix")
        elif self.input_checkpoint is not None:
            raise ValueError(f"{self.stage} must not have inputCheckpoint")
        if self.model_manifest.model_root.removeprefix("/models/frozen/") != (
            self.model_manifest.sha256
        ):
            raise ValueError("modelManifest root must name its sha256")
        return self

    @property
    def configuration_sha256(self) -> str:
        body = self.configuration.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(canonical_json(body)).hexdigest()


class SpeechExecutionAdmissionV2(BaseModel):
    """Create-only proof binding execution to resources and offline weights."""

    model_config = _WIRE

    schema_: Literal["speech-execution-admission/2"] = Field(
        default=ADMISSION_SCHEMA_V2,
        validation_alias="schema",
        serialization_alias="schema",
    )
    protocol: Literal["temnia-speech/2"] = PROTOCOL_V2
    build: str = Field(min_length=1)
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: UUID
    attempt_id: UUID
    stage: StageV2
    modal_call_id: str = Field(min_length=1)
    modal_task_id: str = Field(min_length=1)
    resource_profile: SpeechResourceProfile
    model_manifest: SpeechModelManifest
    execution_topology: ExecutionTopology


class SpeechCheckpointV2(BaseModel):
    """Immutable v2 GPU output accepted before a function returns."""

    model_config = _WIRE

    schema_: Literal["speech-checkpoint/2"] = Field(
        default=CHECKPOINT_SCHEMA_V2,
        validation_alias="schema",
        serialization_alias="schema",
    )
    protocol: Literal["temnia-speech/2"] = PROTOCOL_V2
    stage: StageV2
    operation_id: UUID
    attempt_id: UUID
    build: str = Field(min_length=1)
    source: CheckpointSource
    configuration: StageConfigV2
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_checkpoint: ArtifactRefV2 | None = None
    execution: ExecutionIdentity | None = None
    resource_profile: SpeechResourceProfile
    model_manifest: SpeechModelManifest
    execution_topology: ExecutionTopology
    payload: dict[str, Any]
    payload_diagnostics: PayloadDiagnostics = Field(default_factory=PayloadDiagnostics)
    model_provenance: StageModelProvenance = Field(default_factory=StageModelProvenance)


class SpeechStageResultV2(BaseModel):
    """Small result envelope; artifact arrays remain in object storage."""

    model_config = _WIRE

    protocol: Literal["temnia-speech/2"] = PROTOCOL_V2
    build: str = Field(min_length=1)
    status: Literal["ok", "failed", "outcome_unknown"]
    operation_id: UUID | None
    attempt_id: UUID | None
    stage: StageV2 | None
    modal_call_id: str | None = None
    modal_task_id: str | None = None
    checkpoint_reused: bool = False
    execution_identity: ExecutionIdentity | None = None
    checkpoint: ArtifactRefV2 | None = None
    error: StageError | None = None
    telemetry: StageTelemetry
    resource_profile: SpeechResourceProfile
    model_manifest: SpeechModelManifest
    execution_topology: ExecutionTopology

    @model_validator(mode="after")
    def coherent_status(self) -> SpeechStageResultV2:
        if self.status == "ok" and (self.checkpoint is None or self.error is not None):
            raise ValueError("successful result requires checkpoint and no error")
        if self.status != "ok" and (self.error is None or self.checkpoint is not None):
            raise ValueError("non-ok result requires error and no checkpoint")
        if (
            self.status == "outcome_unknown"
            and self.error is not None
            and self.error.retry_class != "outcome_unknown"
        ):
            raise ValueError("outcomeUnknown result requires matching retryClass")
        if (
            self.status == "failed"
            and self.error is not None
            and self.error.retry_class == "outcome_unknown"
        ):
            raise ValueError("failed result cannot carry outcomeUnknown retryClass")
        if self.status == "ok" and (
            not self.modal_call_id or not self.modal_task_id or self.execution_identity is None
        ):
            raise ValueError("successful result requires responder and producer execution identity")
        if self.status != "ok" and (self.checkpoint_reused or self.execution_identity is not None):
            raise ValueError("non-ok result cannot claim checkpoint execution identity")
        if self.checkpoint_reused and (
            self.telemetry.complete
            or "checkpoint_reused_without_original_container_metrics"
            not in self.telemetry.metrics_unavailable
        ):
            raise ValueError("reused checkpoint requires explicit incomplete telemetry")
        return self


class SpeechAssignmentV1(BaseModel):
    """Attempt-free CPU join over two accepted immutable GPU artifacts."""

    model_config = _WIRE

    schema_: Literal["speech-assignment/1"] = Field(
        default=ASSIGNMENT_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    protocol: Literal["temnia-speech/2"] = PROTOCOL_V2
    build: str = Field(min_length=1)
    source: CheckpointSource
    alignment_checkpoint: ArtifactRefV2
    speaker_turns_checkpoint: ArtifactRefV2
    configuration: SpeakerAssignmentConfig = Field(default_factory=SpeakerAssignmentConfig)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_profile: SpeechResourceProfile
    model_manifest: SpeechModelManifest
    execution_topology: ExecutionTopology
    raw: dict[str, Any]
    diarization: list[dict[str, object]]
    payload_diagnostics: PayloadDiagnostics = Field(default_factory=PayloadDiagnostics)

    @model_validator(mode="after")
    def validate_dependencies(self) -> SpeechAssignmentV1:
        if self.alignment_checkpoint.stage != "align":
            raise ValueError("alignmentCheckpoint must be an align checkpoint")
        if self.speaker_turns_checkpoint.stage != "speaker_turns":
            raise ValueError("speakerTurnsCheckpoint must be a speaker_turns checkpoint")
        if self.configuration_sha256 != self.configuration.sha256:
            raise ValueError("assignment configuration hash does not match")
        return self
