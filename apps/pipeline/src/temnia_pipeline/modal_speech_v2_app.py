"""Versioned offline-model speech execution with frozen resource profiles.

Deploy this additive application under its own name. Existing v1/media apps
retain their exact deployment and interpretation until their work is drained.
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
from typing import TYPE_CHECKING, Any, cast

import modal
from pydantic import ValidationError

from temnia_pipeline.modal_build import BUILD_ENV, source_build_id
from temnia_pipeline.settings import StorageSettings
from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
    ObstoreCheckpointStore,
)
from temnia_pipeline.speech.checkpoints_v2 import prepare_execution_v2 as prepare_execution
from temnia_pipeline.speech.contracts import StageError
from temnia_pipeline.speech.contracts_v2 import (
    PROTOCOL_V2 as PROTOCOL,
)
from temnia_pipeline.speech.contracts_v2 import (
    RecognizeConfigV2 as RecognizeConfig,
)
from temnia_pipeline.speech.contracts_v2 import (
    SpeechStageJobV2 as SpeechStageJob,
)
from temnia_pipeline.speech.contracts_v2 import (
    SpeechStageResultV2 as SpeechStageResult,
)
from temnia_pipeline.speech.contracts_v2 import (
    StageV2 as Stage,
)
from temnia_pipeline.speech.image_models import (
    NLTK_DATA_DIR,
    image_assets_sha256,
    prepare_image_models,
    read_image_model_manifest,
)
from temnia_pipeline.speech.modal_progress import progress_publisher
from temnia_pipeline.speech.model_files import verify_model_files
from temnia_pipeline.speech.oom import is_resource_oom
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.speech.stages_v2 import (
    run_align_v2 as run_align,
)
from temnia_pipeline.speech.stages_v2 import (
    run_recognize_v2 as run_recognize,
)
from temnia_pipeline.speech.stages_v2 import (
    run_speaker_turns_v2 as run_diarize,
)
from temnia_pipeline.speech.telemetry import TelemetryRecorder
from temnia_pipeline.storage import download, make_store

if TYPE_CHECKING:
    from temnia_pipeline.speech.progress_transport import (
        CoalescedProgressPublisher,
        SynchronousProgressPublisher,
    )

if __package__ != "temnia_pipeline":
    message = "Deploy speech/2 with: modal deploy -m temnia_pipeline.modal_speech_v2_app"
    raise ValueError(message)

APP_NAME = os.environ.get("MODAL_SPEECH_V2_APP", "")
if not APP_NAME or APP_NAME in {"temnia-speech", "temnia-media"}:
    message = "speech/2 requires a distinct MODAL_SPEECH_V2_APP"
    raise ValueError(message)
RESOURCE_PROFILE = SpeechResourceProfile.model_validate_json(
    os.environ["MODAL_SPEECH_RESOURCE_PROFILE"]
)


def _frozen_manifest() -> SpeechModelManifest:
    configured = SpeechModelManifest.model_validate_json(os.environ["MODAL_SPEECH_MODEL_MANIFEST"])
    if modal.is_local():
        return configured
    image_sha = image_assets_sha256()
    return configured.model_copy(update={"image_assets_sha256": image_sha})


MODEL_MANIFEST = _frozen_manifest()
MODEL_VOLUME = os.environ["MODAL_SPEECH_MODEL_VOLUME"]
if MODEL_VOLUME == "temnia-models":
    message = "speech/2 requires a separately frozen model volume"
    raise ValueError(message)
GPU = RESOURCE_PROFILE.gpu
CPUS = RESOURCE_PROFILE.cpu_cores
MEMORY_MB = RESOURCE_PROFILE.memory_mib
TIMEOUT_SECONDS = RESOURCE_PROFILE.stage_timeout_seconds
R2_SECRET = "temnia-r2"  # noqa: S105
HF_SECRET = "temnia-hf"  # noqa: S105
MODEL_DIR = MODEL_MANIFEST.model_root
PROGRESS_DICT = "temnia-speech-v2-progress"
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
    .run_function(prepare_image_models)
    .env(
        {
            "HF_HOME": MODEL_DIR,
            "TORCH_HOME": MODEL_DIR,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NLTK_DATA": NLTK_DATA_DIR,
            BUILD_ENV: BUILD_ID,
            "MODAL_SPEECH_V2_APP": APP_NAME,
            "MODAL_SPEECH_RESOURCE_PROFILE": RESOURCE_PROFILE.model_dump_json(by_alias=True),
            "MODAL_SPEECH_MODEL_MANIFEST": MODEL_MANIFEST.model_dump_json(by_alias=True),
            "MODAL_SPEECH_MODEL_VOLUME": MODEL_VOLUME,
        }
    )
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
models = modal.Volume.from_name(MODEL_VOLUME)


def _ids() -> tuple[str | None, str | None]:
    return modal.current_function_call_id(), os.environ.get("MODAL_TASK_ID")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    if stage == "speaker_turns":
        engine.diarize = import_module("whisperx.diarize")
    return engine


async def _execute(  # noqa: C901, PLR0912, PLR0915
    raw_job: dict[str, Any], expected_stage: Stage
) -> dict[str, Any]:
    call_id, task_id = _ids()
    job: SpeechStageJob | None = None
    recorder = TelemetryRecorder()
    execution = None
    publisher: CoalescedProgressPublisher | SynchronousProgressPublisher | None = None
    torch_module: Any | None = None
    scratch: Path | None = None
    try:
        job = SpeechStageJob.model_validate(raw_job)
        if (
            job.build != BUILD_ID
            or job.resource_profile != RESOURCE_PROFILE
            or job.model_manifest != MODEL_MANIFEST
        ):
            message = "speech request does not match deployment build, resources and model files"
            raise ValueError(message)  # noqa: TRY301
        if job.stage != expected_stage:
            message = f"{expected_stage} function received {job.stage} job"
            raise ValueError(message)  # noqa: TRY301
        requested = (
            job.configuration.batch_size if isinstance(job.configuration, RecognizeConfig) else None
        )
        recorder = TelemetryRecorder(
            requested_batch=requested,
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
                resource_profile=RESOURCE_PROFILE,
                model_manifest=MODEL_MANIFEST,
                execution_topology=job.execution_topology,
                status="ok",
                operation_id=job.operation_id,
                attempt_id=job.attempt_id,
                stage=job.stage,
                modal_call_id=call_id,
                modal_task_id=task_id,
                checkpoint_reused=True,
                execution_identity=execution,
                checkpoint=reused,
                telemetry=telemetry,
            ).model_dump(mode="json", by_alias=True)
        if execution is None:
            message = "execution admission returned no checkpoint or admitted container"
            raise RuntimeError(message)  # noqa: TRY301

        publisher = progress_publisher(
            protocol=PROTOCOL,
            dict_name=PROGRESS_DICT,
            stage=job.stage,
            attempt_id=str(job.attempt_id),
            call_id=call_id,
            task_id=task_id,
            mode=RESOURCE_PROFILE.progress_mode,
        )
        publisher.start()
        recorder.set_progress_callback(publisher.callback)

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
        with recorder.phase("modelVerify"):
            verify_model_files(MODEL_MANIFEST)
            if image_assets_sha256(verify_files=True) != MODEL_MANIFEST.image_assets_sha256:
                message = "speech image model identity changed before inference"
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
        recorder.attach_progress_diagnostics(publisher.close())
        telemetry = recorder.finish(complete=True)
        return SpeechStageResult(
            build=BUILD_ID,
            resource_profile=RESOURCE_PROFILE,
            model_manifest=MODEL_MANIFEST,
            execution_topology=job.execution_topology if job else "parallel",
            status="ok",
            operation_id=job.operation_id,
            attempt_id=job.attempt_id,
            stage=job.stage,
            modal_call_id=call_id,
            modal_task_id=task_id,
            execution_identity=execution,
            checkpoint=checkpoint,
            telemetry=telemetry,
        ).model_dump(mode="json", by_alias=True)
    except Exception as error:  # noqa: BLE001
        retry_class = _retry_class(error)
        if retry_class == "outcome_unknown":
            recorder.mark_unavailable("provider_execution_outcome_unknown")
        if publisher is not None:
            recorder.attach_progress_diagnostics(publisher.close())
        telemetry = recorder.finish(complete=retry_class != "outcome_unknown")
        return SpeechStageResult(
            build=BUILD_ID,
            resource_profile=RESOURCE_PROFILE,
            model_manifest=MODEL_MANIFEST,
            execution_topology=job.execution_topology if job else "parallel",
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
        if publisher is not None:
            publisher.close()
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
    "startup_timeout": RESOURCE_PROFILE.startup_timeout_seconds,
    "timeout": TIMEOUT_SECONDS,
    "retries": 0,
    "single_use_containers": True,
    "secrets": [modal.Secret.from_name(R2_SECRET), modal.Secret.from_name(HF_SECRET)],
    "volumes": {"/models": models.with_mount_options(read_only=True)},
}


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def recognize_v2(job: dict[str, Any]) -> dict[str, Any]:
    """Recognition in its own process lifetime and immutable artifact."""
    return await _execute(job, "recognize")


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def align_v2(job: dict[str, Any]) -> dict[str, Any]:
    """Alignment in its own process lifetime and immutable artifact."""
    return await _execute(job, "align")


@app.function(**_FUNCTION)  # type: ignore[arg-type]  # pyright: ignore[reportUnknownMemberType]
async def speaker_turns_v2(job: dict[str, Any]) -> dict[str, Any]:
    """Diarization in its own process lifetime and immutable artifact."""
    return await _execute(job, "speaker_turns")


@app.function()  # pyright: ignore[reportUnknownMemberType]
def version() -> str:
    """The additive app protocol checked by the opt-in provider."""
    return PROTOCOL


@app.function()  # pyright: ignore[reportUnknownMemberType]
def deployment_identity() -> dict[str, object]:
    """The protocol and source fingerprint sealed into this image."""
    return {
        "protocol": PROTOCOL,
        "build": BUILD_ID,
        "resource_profile": RESOURCE_PROFILE.model_dump(mode="json", by_alias=True),
        "model_manifest": MODEL_MANIFEST.model_dump(mode="json", by_alias=True),
    }


@app.function()  # pyright: ignore[reportUnknownMemberType]
def image_model_manifest() -> dict[str, object]:
    """Return auxiliary model file/version evidence for an offline comparison."""
    return read_image_model_manifest()
