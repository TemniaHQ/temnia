"""Additive checkpointed WhisperX stages deployed as the `temnia-speech` Modal app.

This module deliberately does not import the protocol-4 `modal_app`: existing
calls keep their deployed code and contract while the new provider is qualified.
It also imports no Temporal or database module; storage keys are authorized by
the worker before they reach this scope-blind compute app.
"""

from __future__ import annotations

import gc
import hashlib
import os
import shutil
import tempfile
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import modal
from pydantic import ValidationError

from temnia_pipeline.modal_build import BUILD_ENV, source_build_id
from temnia_pipeline.settings import StorageSettings
from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
    ObstoreCheckpointStore,
    prepare_execution,
)
from temnia_pipeline.speech.contracts import (
    PROTOCOL,
    RecognizeConfig,
    SpeechStageJob,
    SpeechStageResult,
    Stage,
    StageError,
)
from temnia_pipeline.speech.oom import is_resource_oom
from temnia_pipeline.speech.stages import run_align, run_diarize, run_recognize
from temnia_pipeline.speech.telemetry import TelemetryRecorder
from temnia_pipeline.storage import download, make_store

APP_NAME = "temnia-speech"
GPU = "L4"
CPUS = 4
MEMORY_MB = 16_384
TIMEOUT_SECONDS = 60 * 60
R2_SECRET = "temnia-r2"  # noqa: S105
HF_SECRET = "temnia-hf"  # noqa: S105
MODEL_VOLUME = "temnia-models"
MODEL_DIR = "/models"
PROGRESS_DICT = "temnia-speech-progress"
PIPELINE_ROOT = Path(__file__).resolve().parents[2]
SPEECH_DOCKERFILE = PIPELINE_ROOT / "Dockerfile.speech"

BUILD_ID = source_build_id() if modal.is_local() else os.environ[BUILD_ENV]
image = (
    modal.Image.from_dockerfile(  # pyright: ignore[reportUnknownMemberType]
        SPEECH_DOCKERFILE,
        add_python="3.13",
    )
    .pip_install("obstore>=0.11.1,<0.12", "pydantic>=2.13,<3", "boto3>=1.40,<2")
    .pip_install(
        "torch==2.8.0",
        "torchaudio==2.8.0",
        index_url="https://download.pytorch.org/whl/cu126",
    )
    .pip_install("whisperx==3.8.6")
    .env({"HF_HOME": MODEL_DIR, "TORCH_HOME": MODEL_DIR, BUILD_ENV: BUILD_ID})
    .add_local_file(
        PIPELINE_ROOT / "LICENSES" / "NLTK-3.10.3.txt",
        "/licenses/NLTK-3.10.3.txt",
        copy=True,
    )
    .add_local_file(
        PIPELINE_ROOT / "THIRD_PARTY_NOTICES.md",
        "/licenses/THIRD_PARTY_NOTICES.md",
        copy=True,
    )
    .add_local_python_source("temnia_pipeline")
)
app = modal.App(APP_NAME, image=image)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)


def _ids() -> tuple[str | None, str | None]:
    return modal.current_function_call_id(), os.environ.get("MODAL_TASK_ID")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _progress_writer(stage: Stage, attempt_id: str) -> Any:  # noqa: ANN401
    """Return a best-effort synchronous writer called by native WhisperX code."""
    call_id, task_id = _ids()
    try:
        notes = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)
    except Exception:  # noqa: BLE001

        def discard(_value: float) -> None:
            return

        return discard

    def write(value: float) -> None:
        with suppress(Exception):
            notes.put(
                attempt_id,
                {
                    "protocol": PROTOCOL,
                    "stage": stage,
                    "percent": round(value),
                    "source": "whisperx_callback",
                    "modalCallId": call_id,
                    "modalTaskId": task_id,
                },
            )

    return write


def _retry_class(error: Exception) -> str:
    if isinstance(error, AdmissionOutcomeUnknownError):
        return "outcome_unknown"
    if isinstance(error, (ValidationError, ValueError, TypeError, CheckpointCollisionError)):
        return "terminal"
    if is_resource_oom(error):
        return "resource_oom"
    return "transient"


def _load_whisperx(stage: Stage) -> Any:  # noqa: ANN401
    """Load the explicit diarization submodule omitted by WhisperX 3.8.6's root."""
    engine = cast("Any", import_module("whisperx"))
    if stage == "diarize":
        engine.diarize = import_module("whisperx.diarize")
    return engine


async def _execute(  # noqa: C901, PLR0912, PLR0915
    raw_job: dict[str, Any], expected_stage: Stage
) -> dict[str, Any]:
    call_id, task_id = _ids()
    job: SpeechStageJob | None = None
    recorder = TelemetryRecorder()
    execution = None
    torch_module: Any | None = None
    scratch: Path | None = None
    try:
        job = SpeechStageJob.model_validate(raw_job)
        if job.stage != expected_stage:
            message = f"{expected_stage} function received {job.stage} job"
            raise ValueError(message)  # noqa: TRY301
        requested = (
            job.configuration.batch_size if isinstance(job.configuration, RecognizeConfig) else None
        )
        recorder = TelemetryRecorder(
            requested_batch=requested,
            on_progress=_progress_writer(job.stage, str(job.attempt_id)),
        )
        settings = StorageSettings.require_env(f"the Modal Secret {R2_SECRET!r}")
        object_store = make_store(settings)
        checkpoint_store = ObstoreCheckpointStore(object_store)

        if not call_id or not task_id:
            message = "Modal call/container identity is unavailable for execution admission"
            raise ValueError(message)  # noqa: TRY301
        # Checkpoint/dependency/admission checks happen before audio or model work.
        reused, execution = await prepare_execution(
            checkpoint_store,
            job,
            BUILD_ID,
            modal_call_id=call_id,
            modal_task_id=task_id,
        )
        if reused is not None:
            recorder.mark_unavailable("checkpoint_reused_without_original_container_metrics")
            telemetry = recorder.finish(complete=False)
            return SpeechStageResult(
                build=BUILD_ID,
                status="ok",
                operation_id=job.operation_id,
                attempt_id=job.attempt_id,
                stage=job.stage,
                modal_call_id=call_id,
                modal_task_id=task_id,
                checkpoint=reused,
                telemetry=telemetry,
            ).model_dump(mode="json", by_alias=True)
        if execution is None:
            message = "execution admission returned no checkpoint or admitted container"
            raise RuntimeError(message)  # noqa: TRY301

        scratch = Path(tempfile.mkdtemp(prefix=f"speech-{job.stage}-", dir="/tmp"))
        audio_file = scratch / Path(job.audio_key).name
        with recorder.phase("download"):
            await download(
                object_store,
                job.audio_key,
                audio_file,
                expected_size=job.audio_size_bytes,
            )
            if _sha256_file(audio_file) != job.audio_sha256:
                message = f"audio bytes do not match frozen source identity at {job.audio_key}"
                raise ValueError(message)  # noqa: TRY301
        engine = _load_whisperx(job.stage)
        torch_module = cast("Any", import_module("torch"))
        recorder.start(torch_module)
        if job.stage == "recognize":
            checkpoint = await run_recognize(
                engine,
                checkpoint_store,
                job,
                audio_file,
                BUILD_ID,
                recorder,
                execution,
            )
        elif job.stage == "align":
            checkpoint = await run_align(
                engine,
                checkpoint_store,
                job,
                audio_file,
                BUILD_ID,
                recorder,
                execution,
            )
        else:
            token = os.environ.get("HF_TOKEN")
            if not token:
                message = f"HF_TOKEN is missing or empty; the Modal Secret {HF_SECRET!r} sets it"
                raise RuntimeError(message)  # noqa: TRY301
            checkpoint = await run_diarize(
                engine,
                checkpoint_store,
                job,
                audio_file,
                BUILD_ID,
                recorder,
                token,
                execution,
            )
        telemetry = recorder.finish(complete=True)
        return SpeechStageResult(
            build=BUILD_ID,
            status="ok",
            operation_id=job.operation_id,
            attempt_id=job.attempt_id,
            stage=job.stage,
            modal_call_id=call_id,
            modal_task_id=task_id,
            checkpoint=checkpoint,
            telemetry=telemetry,
        ).model_dump(mode="json", by_alias=True)
    except Exception as error:  # noqa: BLE001
        retry_class = _retry_class(error)
        if retry_class == "outcome_unknown":
            recorder.mark_unavailable("provider_execution_outcome_unknown")
        telemetry = recorder.finish(complete=retry_class != "outcome_unknown")
        return SpeechStageResult(
            build=BUILD_ID,
            status=("outcome_unknown" if retry_class == "outcome_unknown" else "failed"),
            operation_id=job.operation_id if job else None,
            attempt_id=job.attempt_id if job else None,
            stage=job.stage if job else expected_stage,
            modal_call_id=call_id,
            modal_task_id=task_id,
            error=StageError(
                type=type(error).__name__,
                message=str(error),
                retry_class=cast("Any", retry_class),
                inference_complete=recorder.inference_completed,
            ),
            telemetry=telemetry,
        ).model_dump(mode="json", by_alias=True)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        gc.collect()
        if torch_module is not None:
            with suppress(Exception):
                torch_module.cuda.empty_cache()


_FUNCTION = {
    "gpu": GPU,
    "cpu": (CPUS, CPUS),
    "memory": (MEMORY_MB, MEMORY_MB),
    "startup_timeout": 120,
    "timeout": TIMEOUT_SECONDS,
    "retries": 0,
    "single_use_containers": True,
    "secrets": [modal.Secret.from_name(R2_SECRET), modal.Secret.from_name(HF_SECRET)],
    "volumes": {MODEL_DIR: models},
}


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def recognize(job: dict[str, Any]) -> dict[str, Any]:
    """Recognition in its own process lifetime and immutable artifact."""
    return await _execute(job, "recognize")


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def align(job: dict[str, Any]) -> dict[str, Any]:
    """Alignment in its own process lifetime and immutable artifact."""
    return await _execute(job, "align")


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def diarize(job: dict[str, Any]) -> dict[str, Any]:
    """Diarization in its own process lifetime and immutable artifact."""
    return await _execute(job, "diarize")


@app.function()  # pyright: ignore[reportUnknownMemberType]
def version() -> str:
    """The additive app protocol checked by the opt-in provider."""
    return PROTOCOL


@app.function()  # pyright: ignore[reportUnknownMemberType]
def deployment_identity() -> dict[str, str]:
    """The protocol and source fingerprint sealed into this image."""
    return {"protocol": PROTOCOL, "build": BUILD_ID}
