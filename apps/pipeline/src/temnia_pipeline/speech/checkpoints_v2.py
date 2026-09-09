"""Immutable publication and create-only execution admission for speech v2."""

# ruff: noqa: D103, EM101, EM102, TC001, TRY003

from __future__ import annotations

import hashlib
from typing import cast

from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
    CheckpointStore,
)
from temnia_pipeline.speech.contracts import (
    CheckpointSource,
    ExecutionIdentity,
    StageModelProvenance,
    canonical_json,
    normalize_payload,
)
from temnia_pipeline.speech.contracts_v2 import (
    ArtifactRefV2,
    SpeechCheckpointV2,
    SpeechExecutionAdmissionV2,
    SpeechStageJobV2,
    StageConfigV2,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile


def checkpoint_for_v2(
    job: SpeechStageJobV2,
    build: str,
    payload: dict[str, object],
    execution: ExecutionIdentity | None = None,
    model_provenance: StageModelProvenance | None = None,
) -> SpeechCheckpointV2:
    """Seal all v2 source, resource, model and topology facts into a checkpoint."""
    if build != job.build:
        raise CheckpointCollisionError("runtime build does not match the frozen v2 job")
    normalized, diagnostics = normalize_payload(payload)
    if not isinstance(normalized, dict):
        raise TypeError("checkpoint payload must be an object")
    return SpeechCheckpointV2(
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
        resource_profile=job.resource_profile,
        model_manifest=job.model_manifest,
        execution_topology=job.execution_topology,
        payload=cast("dict[str, object]", normalized),
        payload_diagnostics=diagnostics,
        model_provenance=model_provenance or StageModelProvenance(),
    )


def artifact_ref_v2(
    key: str, stage: StageV2, configuration_sha256: str, body: bytes
) -> ArtifactRefV2:
    return ArtifactRefV2(
        key=key,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        stage=stage,
        configuration_sha256=configuration_sha256,
    )


def validate_checkpoint_v2(job: SpeechStageJobV2, body: bytes) -> SpeechCheckpointV2:
    """Validate immutable bytes against the complete v2 job identity."""
    try:
        checkpoint = SpeechCheckpointV2.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(
            f"invalid checkpoint at {job.checkpoint_key}: {error}"
        ) from error
    expected = checkpoint_for_v2(
        job,
        checkpoint.build,
        checkpoint.payload,
        checkpoint.execution,
        checkpoint.model_provenance,
    )
    expected = expected.model_copy(update={"payload_diagnostics": checkpoint.payload_diagnostics})
    if checkpoint != expected:
        raise CheckpointCollisionError(f"checkpoint identity mismatch at {job.checkpoint_key}")
    return checkpoint


async def reuse_checkpoint_v2(
    store: CheckpointStore, job: SpeechStageJobV2
) -> ArtifactRefV2 | None:
    body = await store.read(job.checkpoint_key)
    if body is None:
        return None
    checkpoint = validate_checkpoint_v2(job, body)
    return artifact_ref_v2(
        job.checkpoint_key,
        checkpoint.stage,
        checkpoint.configuration_sha256,
        body,
    )


async def read_input_checkpoint_v2(
    store: CheckpointStore, job: SpeechStageJobV2
) -> SpeechCheckpointV2 | None:
    ref = job.input_checkpoint
    if ref is None:
        return None
    body = await store.read(ref.key)
    if body is None:
        raise CheckpointCollisionError(f"input checkpoint is missing: {ref.key}")
    if len(body) != ref.size_bytes or hashlib.sha256(body).hexdigest() != ref.sha256:
        raise CheckpointCollisionError(f"input checkpoint bytes do not match its ref: {ref.key}")
    try:
        checkpoint = SpeechCheckpointV2.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(f"invalid input checkpoint at {ref.key}: {error}") from error
    if checkpoint.stage != ref.stage:
        raise CheckpointCollisionError(f"input checkpoint stage mismatch at {ref.key}")
    if checkpoint.configuration_sha256 != ref.configuration_sha256:
        raise CheckpointCollisionError(
            f"input checkpoint configuration identity mismatch at {ref.key}"
        )
    if (
        checkpoint.build != job.build
        or checkpoint.source.audio_key != job.audio_key
        or checkpoint.source.audio_sha256 != job.audio_sha256
        or checkpoint.source.audio_size_bytes != job.audio_size_bytes
        or checkpoint.source.duration_ms != job.duration_ms
        or checkpoint.resource_profile != job.resource_profile
        or checkpoint.model_manifest != job.model_manifest
        or checkpoint.execution_topology != job.execution_topology
    ):
        raise CheckpointCollisionError(f"input checkpoint identity mismatch at {ref.key}")
    return checkpoint


async def publish_checkpoint_v2(
    store: CheckpointStore, job: SpeechStageJobV2, checkpoint: SpeechCheckpointV2
) -> ArtifactRefV2:
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    if not await store.create(job.checkpoint_key, body):
        existing = await store.read(job.checkpoint_key)
        if existing != body:
            raise CheckpointCollisionError(
                f"checkpoint collision with different bytes at {job.checkpoint_key}"
            )
    return artifact_ref_v2(
        job.checkpoint_key,
        checkpoint.stage,
        checkpoint.configuration_sha256,
        body,
    )


def admission_key_v2(job: SpeechStageJobV2) -> str:
    return f"{job.artifact_prefix}transcript/v2/admissions/{job.stage}/{job.attempt_id}.json"


def admission_for_v2(
    job: SpeechStageJobV2,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> SpeechExecutionAdmissionV2:
    if build != job.build:
        raise CheckpointCollisionError("runtime build does not match the frozen v2 job")
    job_body = job.model_dump(mode="json", by_alias=True)
    return SpeechExecutionAdmissionV2(
        build=build,
        job_sha256=hashlib.sha256(canonical_json(job_body)).hexdigest(),
        operation_id=job.operation_id,
        attempt_id=job.attempt_id,
        stage=job.stage,
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
        resource_profile=job.resource_profile,
        model_manifest=job.model_manifest,
        execution_topology=job.execution_topology,
    )


def validate_execution_admission_v2(
    job: SpeechStageJobV2,
    execution: ExecutionIdentity,
    body: bytes,
) -> SpeechExecutionAdmissionV2:
    """Verify the persisted admission for the container that produced a checkpoint."""
    if execution.admission_key != admission_key_v2(job):
        raise CheckpointCollisionError("checkpoint execution names a different admission key")
    try:
        admission = SpeechExecutionAdmissionV2.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(
            f"invalid execution admission at {execution.admission_key}: {error}"
        ) from error
    expected = admission_for_v2(
        job,
        job.build,
        modal_call_id=execution.modal_call_id,
        modal_task_id=execution.modal_task_id,
    )
    if admission != expected:
        raise CheckpointCollisionError(
            f"execution admission identity mismatch at {execution.admission_key}"
        )
    return admission


async def claim_execution_v2(
    store: CheckpointStore,
    job: SpeechStageJobV2,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> ExecutionIdentity:
    """Admit one v2 container, refusing all ambiguous or mismatched redelivery."""
    key = admission_key_v2(job)
    admission = admission_for_v2(
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
                SpeechExecutionAdmissionV2.model_validate_json(existing)
                if existing is not None
                else None
            )
        except Exception as error:
            raise AdmissionOutcomeUnknownError(
                f"execution admission is corrupt at {key}: {error}"
            ) from error
        if prior != admission:
            raise AdmissionOutcomeUnknownError(f"execution admission identity collision at {key}")
        raise AdmissionOutcomeUnknownError(
            f"matching prior execution admission exists at {key}; inference is refused"
        )
    return ExecutionIdentity(
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
        admission_key=key,
    )


async def prepare_execution_v2(
    store: CheckpointStore,
    job: SpeechStageJobV2,
    build: str,
    *,
    modal_call_id: str,
    modal_task_id: str,
) -> tuple[ArtifactRefV2 | None, ExecutionIdentity | None]:
    """Reuse authenticated bytes first; otherwise acquire inference admission."""
    reused = await reuse_checkpoint_v2(store, job)
    if reused is not None:
        try:
            checkpoint_body = await store.read(job.checkpoint_key)
            checkpoint = (
                validate_checkpoint_v2(job, checkpoint_body)
                if checkpoint_body is not None
                else None
            )
            if checkpoint is None or checkpoint.execution is None:
                raise CheckpointCollisionError(  # noqa: TRY301
                    "reused checkpoint omits its producer execution identity"
                )
            admission_body = await store.read(checkpoint.execution.admission_key)
            if admission_body is None:
                raise CheckpointCollisionError(  # noqa: TRY301
                    "reused checkpoint admission is missing"
                )
            validate_execution_admission_v2(job, checkpoint.execution, admission_body)
        except Exception as error:
            raise AdmissionOutcomeUnknownError(
                f"reused checkpoint producer admission cannot be verified: {error}"
            ) from error
        return reused, checkpoint.execution
    return None, await claim_execution_v2(
        store,
        job,
        build,
        modal_call_id=modal_call_id,
        modal_task_id=modal_task_id,
    )


def validate_checkpoint_semantics_v2(  # noqa: PLR0913
    body: bytes,
    *,
    stage: StageV2,
    build: str,
    audio_key: str,
    audio_sha256: str,
    audio_size_bytes: int,
    duration_ms: int,
    configuration: StageConfigV2,
    input_sha256: str | None,
    resource_profile: SpeechResourceProfile,
    model_manifest: SpeechModelManifest,
    execution_topology: str,
) -> SpeechCheckpointV2:
    """Verify complete semantic identity while retaining physical attribution."""
    try:
        checkpoint = SpeechCheckpointV2.model_validate_json(body)
    except Exception as error:
        raise CheckpointCollisionError(f"invalid cached v2 checkpoint: {error}") from error
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
        resource_profile,
        model_manifest,
        execution_topology,
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
        checkpoint.resource_profile,
        checkpoint.model_manifest,
        checkpoint.execution_topology,
    )
    if actual != expected:
        raise CheckpointCollisionError("cached v2 checkpoint semantic identity mismatch")
    return checkpoint
