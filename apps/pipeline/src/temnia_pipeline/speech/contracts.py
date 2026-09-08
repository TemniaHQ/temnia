"""Strict, small wire models for the checkpointed speech app."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

PROTOCOL = "temnia-speech/1"
CHECKPOINT_SCHEMA = "speech-checkpoint/1"
ADMISSION_SCHEMA = "speech-execution-admission/1"
MAX_DIAGNOSTIC_PATHS = 32

_WIRE = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
    allow_inf_nan=False,
)

Stage = Literal["recognize", "align", "diarize"]
RetryClass = Literal["terminal", "resource_oom", "transient", "outcome_unknown"]


def canonical_json(value: object) -> bytes:
    """Encode the exact stable bytes used for identity and immutable objects."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


class ArtifactRef(BaseModel):
    """A verified immutable object; large payloads travel through storage only."""

    model_config = _WIRE

    key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    stage: Stage
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_: Literal["speech-checkpoint/1"] = Field(
        default=CHECKPOINT_SCHEMA, validation_alias="schema", serialization_alias="schema"
    )


class RecognizeConfig(BaseModel):
    """Every recognition choice that can affect the accepted artifact."""

    model_config = _WIRE

    stage: Literal["recognize"] = "recognize"
    model: str = "large-v3"
    whisperx_version: str = "3.8.6"
    compute_type: Literal["float16"] = "float16"
    batch_size: Literal[4, 8, 16] = 16
    language: str | None = None
    vad_method: str = "pyannote"
    vad_options: dict[str, bool | float | int | str | None] = Field(default_factory=dict)


class AlignConfig(BaseModel):
    """Alignment identity, including the language chosen by recognition."""

    model_config = _WIRE

    stage: Literal["align"] = "align"
    language: str = Field(min_length=1)
    model: str | None = None
    whisperx_version: str = "3.8.6"
    return_char_alignments: Literal[False] = False


class DiarizeConfig(BaseModel):
    """Diarization identity and speaker-bound choices."""

    model_config = _WIRE

    stage: Literal["diarize"] = "diarize"
    model: str = "pyannote/speaker-diarization-community-1"
    whisperx_version: str = "3.8.6"
    min_speakers: int | None = Field(default=None, ge=1)
    max_speakers: int | None = Field(default=None, ge=1)
    fill_nearest: Literal[True] = True

    @model_validator(mode="after")
    def ordered_speaker_bounds(self) -> DiarizeConfig:
        """Reject an impossible speaker range before booking a GPU."""
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("minSpeakers cannot exceed maxSpeakers")
        return self


StageConfig = Annotated[
    RecognizeConfig | AlignConfig | DiarizeConfig,
    Field(discriminator="stage"),
]


class SpeechStageJob(BaseModel):
    """A scope-blind request for exactly one independently retryable stage."""

    model_config = _WIRE

    operation_id: UUID
    attempt_id: UUID
    stage: Stage
    artifact_prefix: str = Field(min_length=1)
    audio_key: str = Field(min_length=1)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_size_bytes: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    checkpoint_key: str = Field(min_length=1)
    configuration: StageConfig
    input_checkpoint: ArtifactRef | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> SpeechStageJob:
        """Bind the stage to its configuration, dependency and immutable key."""
        if not self.artifact_prefix.endswith("/"):
            raise ValueError("artifactPrefix must end with '/'")
        if not self.audio_key.startswith(self.artifact_prefix):
            raise ValueError("audioKey must be inside artifactPrefix")
        expected = (
            f"{self.artifact_prefix}transcript/checkpoints/{self.stage}/{self.attempt_id}.json"
        )
        if self.checkpoint_key != expected:
            raise ValueError(f"checkpointKey must equal {expected}")
        if self.configuration.stage != self.stage:
            raise ValueError("configuration stage does not match job stage")
        if self.stage == "recognize":
            if self.input_checkpoint is not None:
                raise ValueError("recognize must not have inputCheckpoint")
        else:
            if self.input_checkpoint is None:
                raise ValueError(f"{self.stage} requires inputCheckpoint")
            required = "recognize" if self.stage == "align" else "align"
            if self.input_checkpoint.stage != required:
                raise ValueError(f"{self.stage} requires a {required} checkpoint")
            if not self.input_checkpoint.key.startswith(self.artifact_prefix):
                raise ValueError("inputCheckpoint must be inside artifactPrefix")
        return self

    @property
    def configuration_sha256(self) -> str:
        """Fingerprint the exact configuration serialized on the wire."""
        body = self.configuration.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(canonical_json(body)).hexdigest()


class CheckpointSource(BaseModel):
    """The audio identity carried through every stage."""

    model_config = _WIRE

    audio_key: str
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_size_bytes: int = Field(ge=0)
    duration_ms: int


class ExecutionIdentity(BaseModel):
    """The first Modal container admitted to perform one physical attempt."""

    model_config = _WIRE

    modal_call_id: str = Field(min_length=1)
    modal_task_id: str = Field(min_length=1)
    admission_key: str = Field(min_length=1)


class SpeechExecutionAdmissionV1(BaseModel):
    """Immutable proof that one container won the attempt's inference admission."""

    model_config = _WIRE

    schema_: Literal["speech-execution-admission/1"] = Field(
        default=ADMISSION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    protocol: Literal["temnia-speech/1"] = PROTOCOL
    build: str = Field(min_length=1)
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: UUID
    attempt_id: UUID
    stage: Stage
    modal_call_id: str = Field(min_length=1)
    modal_task_id: str = Field(min_length=1)


class PayloadDiagnostics(BaseModel):
    """Bounded disclosure of non-standard engine floats normalized at publication."""

    model_config = _WIRE

    nonfinite_count: int = Field(default=0, ge=0)
    nonfinite_paths: list[str] = Field(default_factory=list, max_length=MAX_DIAGNOSTIC_PATHS)
    paths_truncated: bool = False


class StageModelProvenance(BaseModel):
    """Observed runtime identity; null means the loaded object did not expose it."""

    model_config = _WIRE

    requested_model: str | None = None
    loaded_class: str | None = None
    resolved_model: str | None = None
    resolved_revision: str | None = None
    weight_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    library_versions: dict[str, str | None] = Field(default_factory=dict)
    identity_status: Literal["observed_unpinned", "immutable"] = "observed_unpinned"
    reuse_scope: Literal["run", "cross_run"] = "run"


def normalize_payload(value: object) -> tuple[object, PayloadDiagnostics]:
    """Replace nonfinite engine floats with null and record bounded JSON paths."""
    paths: list[str] = []
    count = 0

    def visit(item: object, path: str) -> object:
        nonlocal count
        if isinstance(item, float) and not math.isfinite(item):
            count += 1
            if len(paths) < MAX_DIAGNOSTIC_PATHS:
                paths.append(path)
            return None
        if isinstance(item, dict):
            mapping = cast("dict[object, object]", item)
            return {str(key): visit(child, f"{path}/{key}") for key, child in mapping.items()}
        if isinstance(item, list | tuple):
            sequence = cast("list[object] | tuple[object, ...]", item)
            return [visit(child, f"{path}/{index}") for index, child in enumerate(sequence)]
        return item

    normalized = visit(value, "$")
    return normalized, PayloadDiagnostics(
        nonfinite_count=count,
        nonfinite_paths=paths,
        paths_truncated=count > len(paths),
    )


class SpeechCheckpointV1(BaseModel):
    """The immutable accepted artifact written before a stage returns."""

    model_config = _WIRE

    schema_: Literal["speech-checkpoint/1"] = Field(
        default=CHECKPOINT_SCHEMA, validation_alias="schema", serialization_alias="schema"
    )
    stage: Stage
    operation_id: UUID
    attempt_id: UUID
    build: str
    source: CheckpointSource
    configuration: StageConfig
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_checkpoint: ArtifactRef | None = None
    execution: ExecutionIdentity | None = None
    payload: dict[str, Any]
    payload_diagnostics: PayloadDiagnostics = Field(default_factory=PayloadDiagnostics)
    model_provenance: StageModelProvenance = Field(default_factory=StageModelProvenance)


class ProgressValue(BaseModel):
    """Measured engine progress; null means no callback has fired."""

    model_config = _WIRE

    percent: int | None = Field(default=None, ge=0, le=100)
    source: Literal["whisperx_callback", "none"] = "none"


class StageTelemetry(BaseModel):
    """Per-attempt timing and resource observations, including partial failures."""

    model_config = _WIRE

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    phase_seconds: dict[str, float] = Field(default_factory=dict)
    requested_batch: int | None = None
    effective_batch: int | None = None
    progress: ProgressValue = Field(default_factory=ProgressValue)
    process_rss_bytes: int | None = None
    process_peak_rss_bytes: int | None = None
    gpu_used_peak_bytes: int | None = None
    gpu_total_bytes: int | None = None
    pid_gpu_used_peak_bytes: int | None = None
    torch_allocated_peak_bytes: int | None = None
    torch_reserved_peak_bytes: int | None = None
    gpu_name: str | None = None
    sample_interval_seconds: float = Field(default=1.0, gt=0)
    sample_count: int = Field(default=0, ge=0)
    metrics_unavailable: list[str] = Field(default_factory=list)
    complete: bool = False


class StageError(BaseModel):
    """A known stage failure returned to the reconciler."""

    model_config = _WIRE

    type: str
    message: str
    retry_class: RetryClass
    inference_complete: bool = False


class SpeechStageResult(BaseModel):
    """A small stage outcome; checkpoint payload arrays never appear here."""

    model_config = _WIRE

    protocol: Literal["temnia-speech/1"] = PROTOCOL
    build: str
    status: Literal["ok", "failed", "outcome_unknown"]
    operation_id: UUID | None
    attempt_id: UUID | None
    stage: Stage | None
    modal_call_id: str | None = None
    modal_task_id: str | None = None
    checkpoint: ArtifactRef | None = None
    error: StageError | None = None
    telemetry: StageTelemetry

    @model_validator(mode="after")
    def coherent_status(self) -> SpeechStageResult:
        """Require exactly the payload appropriate to success or failure."""
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
        return self
