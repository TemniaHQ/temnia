"""Immutable speech checkpoint publication and verification."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol, cast

from temnia_pipeline.speech.contracts import (
    ArtifactRef,
    CheckpointSource,
    ExecutionIdentity,
    SpeechCheckpointV1,
    SpeechExecutionAdmissionV1,
    SpeechStageJob,
    Stage,
    StageConfig,
    StageModelProvenance,
    canonical_json,
    normalize_payload,
)

if TYPE_CHECKING:
    from obstore.store import S3Store


class CheckpointCollisionError(RuntimeError):
    """An immutable key exists with bytes or identity other than the request."""


class AdmissionOutcomeUnknownError(RuntimeError):
    """Inference admission may already belong to another container execution."""


MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


class CheckpointStore(Protocol):
    """The two object-store operations needed by checkpoint logic."""

    async def read(self, key: str) -> bytes | None:
        """Read bytes or return None for an absent key."""
        ...

    async def create(self, key: str, body: bytes) -> bool:
        """Create without overwrite; False means the key already exists."""
        ...


class ObstoreCheckpointStore:
    """Atomic create-mode checkpoint storage backed by an obstore S3Store."""

    def __init__(self, store: S3Store) -> None:
        self._store = store

    async def read(self, key: str) -> bytes | None:
        """Read a small checkpoint object."""
        import obstore as obs  # noqa: PLC0415

        try:
            result = await obs.get_async(self._store, key)
        except FileNotFoundError:
            return None
        size = int(result.meta["size"])
        if size > MAX_CHECKPOINT_BYTES:
            raise CheckpointCollisionError(
                f"checkpoint exceeds {MAX_CHECKPOINT_BYTES} bytes at {key}"
            )
        body = bytes(await result.bytes_async())
        if len(body) != size:
            raise CheckpointCollisionError(f"checkpoint size changed while reading {key}")
        return body

    async def create(self, key: str, body: bytes) -> bool:
        """Use the store's conditional create primitive, never an overwrite."""
        import obstore as obs  # noqa: PLC0415
        from obstore.exceptions import AlreadyExistsError  # noqa: PLC0415

        try:
            await obs.put_async(
                self._store,
                key,
                body,
                mode="create",
                attributes={"Content-Type": "application/json"},
            )
        except AlreadyExistsError:
            return False
        return True


def checkpoint_for(
    job: SpeechStageJob,
    build: str,
    payload: dict[str, object],
    execution: ExecutionIdentity | None = None,
    model_provenance: StageModelProvenance | None = None,
) -> SpeechCheckpointV1:
    """Construct the stage artifact with all dependency identity sealed in."""
    normalized, diagnostics = normalize_payload(payload)
    if not isinstance(normalized, dict):
        raise TypeError("checkpoint payload must be an object")
    return SpeechCheckpointV1(
        stage=job.stage,
        operation_id=job.operation_id,
        attempt_id=job.attempt_id,
        build=build,
        source=CheckpointSource(
            audio_key=job.audio_key,
            audio_sha256=job.audio_sha256,
            audio_size_bytes=job.audio_size_bytes,
            duration_ms=job.duration_ms,
        ),
        configuration=job.configuration,
        configuration_sha256=job.configuration_sha256,
        input_checkpoint=job.input_checkpoint,
        execution=execution,
        payload=cast("dict[str, object]", normalized),
        payload_diagnostics=diagnostics,
        model_provenance=model_provenance or StageModelProvenance(),
    )


def _artifact_ref(key: str, stage: Stage, configuration_sha256: str, body: bytes) -> ArtifactRef:
    return ArtifactRef(
        key=key,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        stage=stage,
        configuration_sha256=configuration_sha256,
    )


def validate_checkpoint(job: SpeechStageJob, body: bytes) -> SpeechCheckpointV1:
    """Validate bytes against the complete request identity."""
    try:
        checkpoint = SpeechCheckpointV1.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(
            f"invalid checkpoint at {job.checkpoint_key}: {error}"
        ) from error
    expected = checkpoint_for(
        job,
        checkpoint.build,
        checkpoint.payload,
        checkpoint.execution,
        checkpoint.model_provenance,
    )
    expected.payload_diagnostics = checkpoint.payload_diagnostics
    if checkpoint != expected:
        raise CheckpointCollisionError(f"checkpoint identity mismatch at {job.checkpoint_key}")
    return checkpoint


async def reuse_checkpoint(store: CheckpointStore, job: SpeechStageJob) -> ArtifactRef | None:
    """Return a verified accepted object before any model is loaded."""
    body = await store.read(job.checkpoint_key)
    if body is None:
        return None
    checkpoint = validate_checkpoint(job, body)
    return _artifact_ref(
        job.checkpoint_key, checkpoint.stage, checkpoint.configuration_sha256, body
    )


async def read_input_checkpoint(
    store: CheckpointStore, job: SpeechStageJob
) -> SpeechCheckpointV1 | None:
    """Read and hash-check the immutable dependency named by the job."""
    ref = job.input_checkpoint
    if ref is None:
        return None
    body = await store.read(ref.key)
    if body is None:
        raise CheckpointCollisionError(f"input checkpoint is missing: {ref.key}")
    if len(body) != ref.size_bytes or hashlib.sha256(body).hexdigest() != ref.sha256:
        raise CheckpointCollisionError(f"input checkpoint bytes do not match its ref: {ref.key}")
    try:
        checkpoint = SpeechCheckpointV1.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(f"invalid input checkpoint at {ref.key}: {error}") from error
    if checkpoint.stage != ref.stage:
        raise CheckpointCollisionError(f"input checkpoint stage mismatch at {ref.key}")
    if checkpoint.configuration_sha256 != ref.configuration_sha256:
        raise CheckpointCollisionError(
            f"input checkpoint configuration identity mismatch at {ref.key}"
        )
    if (
        checkpoint.source.audio_key != job.audio_key
        or checkpoint.source.audio_sha256 != job.audio_sha256
        or checkpoint.source.audio_size_bytes != job.audio_size_bytes
        or checkpoint.source.duration_ms != job.duration_ms
    ):
        raise CheckpointCollisionError(f"input checkpoint source mismatch at {ref.key}")
    return checkpoint


async def publish_checkpoint(
    store: CheckpointStore, job: SpeechStageJob, checkpoint: SpeechCheckpointV1
) -> ArtifactRef:
    """Create immutable bytes, or reuse only byte-identical existing bytes."""
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    if not await store.create(job.checkpoint_key, body):
        existing = await store.read(job.checkpoint_key)
        if existing != body:
            raise CheckpointCollisionError(
                f"checkpoint collision with different bytes at {job.checkpoint_key}"
            )
    return _artifact_ref(
        job.checkpoint_key, checkpoint.stage, checkpoint.configuration_sha256, body
    )


def admission_key(job: SpeechStageJob) -> str:
    """Derive the immutable source-scoped execution claim key."""
    return f"{job.artifact_prefix}transcript/admissions/{job.stage}/{job.attempt_id}.json"


def admission_for(
    job: SpeechStageJob,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> SpeechExecutionAdmissionV1:
    """Bind an admission to the complete canonical job and first container."""
    job_body = job.model_dump(mode="json", by_alias=True)
    return SpeechExecutionAdmissionV1(
        build=build,
        job_sha256=hashlib.sha256(canonical_json(job_body)).hexdigest(),
        operation_id=job.operation_id,
        attempt_id=job.attempt_id,
        stage=job.stage,
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
    )


async def claim_execution(
    store: CheckpointStore,
    job: SpeechStageJob,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> ExecutionIdentity:
    """Admit only the first container; ambiguity always refuses inference."""
    key = admission_key(job)
    admission = admission_for(
        job,
        build,
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
    )
    body = canonical_json(admission.model_dump(mode="json", by_alias=True))
    try:
        created = await store.create(key, body)
    except Exception as error:
        raise AdmissionOutcomeUnknownError(
            f"execution admission create outcome is unknown at {key}: {error}"
        ) from error
    if not created:
        try:
            existing = await store.read(key)
        except Exception as error:
            raise AdmissionOutcomeUnknownError(
                f"existing execution admission cannot be verified at {key}: {error}"
            ) from error
        try:
            prior = (
                SpeechExecutionAdmissionV1.model_validate_json(existing)
                if existing is not None
                else None
            )
        except ValueError:
            prior = None
        expected_semantics = (
            admission.protocol,
            admission.build,
            admission.job_sha256,
            admission.operation_id,
            admission.attempt_id,
            admission.stage,
        )
        actual_semantics = (
            (
                prior.protocol,
                prior.build,
                prior.job_sha256,
                prior.operation_id,
                prior.attempt_id,
                prior.stage,
            )
            if prior is not None
            else None
        )
        detail = (
            "matching prior execution"
            if actual_semantics == expected_semantics
            else "identity collision"
        )
        raise AdmissionOutcomeUnknownError(f"execution admission refused {detail} at {key}")
    return ExecutionIdentity(
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
        admission_key=key,
    )


async def prepare_execution(
    store: CheckpointStore,
    job: SpeechStageJob,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> tuple[ArtifactRef | None, ExecutionIdentity | None]:
    """Prefer completed work, validate lineage, then claim model execution once."""
    if reused := await reuse_checkpoint(store, job):
        return reused, None
    await read_input_checkpoint(store, job)
    return None, await claim_execution(
        store,
        job,
        build,
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
    )


def validate_checkpoint_semantics(  # noqa: PLR0913
    body: bytes,
    *,
    stage: Stage,
    build: str,
    audio_key: str,
    audio_sha256: str,
    audio_size_bytes: int,
    duration_ms: int,
    configuration: StageConfig,
    input_sha256: str | None,
) -> SpeechCheckpointV1:
    """Verify cache semantics while allowing original operation/attempt attribution."""
    try:
        checkpoint = SpeechCheckpointV1.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(f"invalid cached checkpoint: {error}") from error
    config_body = configuration.model_dump(mode="json", by_alias=True)
    config_sha = hashlib.sha256(canonical_json(config_body)).hexdigest()
    actual_input_sha = (
        checkpoint.input_checkpoint.sha256 if checkpoint.input_checkpoint is not None else None
    )
    expected = (
        stage,
        build,
        audio_key,
        audio_sha256,
        audio_size_bytes,
        duration_ms,
        config_body,
        config_sha,
        input_sha256,
    )
    actual = (
        checkpoint.stage,
        checkpoint.build,
        checkpoint.source.audio_key,
        checkpoint.source.audio_sha256,
        checkpoint.source.audio_size_bytes,
        checkpoint.source.duration_ms,
        checkpoint.configuration.model_dump(mode="json", by_alias=True),
        checkpoint.configuration_sha256,
        actual_input_sha,
    )
    if actual != expected:
        raise CheckpointCollisionError("cached checkpoint semantic identity mismatch")
    return checkpoint
