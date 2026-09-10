"""Isolated, opt-in Chapter-Llama GPU audition with immutable execution admission."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import modal

from temnia_pipeline.chapter_llama.checkpoints import prepare_execution, publish_outcome
from temnia_pipeline.chapter_llama.contracts import (
    ADAPTER_PATH,
    ADAPTER_REPO,
    ADAPTER_REVISION,
    BASE_REPO,
    BASE_REVISION,
    PROTOCOL,
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    ModelConfig,
    ResourceProfile,
)
from temnia_pipeline.chapter_llama.inference import (
    ADAPTER_FILES,
    BASE_FILES,
    InvalidGenerationError,
    TransformersEngine,
    snapshot,
)
from temnia_pipeline.modal_build import BUILD_ENV, source_build_id
from temnia_pipeline.settings import StorageSettings
from temnia_pipeline.speech.checkpoints import AdmissionOutcomeUnknownError, ObstoreCheckpointStore
from temnia_pipeline.storage import make_store

APP_NAME = "temnia-chapter-llama"
MODEL_DIR = "/models"
GPU = "L40S"
CPUS = 4
MEMORY_MB = 32_768
TIMEOUT_SECONDS = 3600
PIPELINE_ROOT = Path(__file__).resolve().parents[2]
BUILD_ID = source_build_id() if modal.is_local() else os.environ[BUILD_ENV]
image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install(
        "torch==2.14.0",
        "transformers==5.16.1",
        "peft==0.20.0",
        "pydantic==2.13.5",
        "obstore==0.11.1",
        "boto3==1.43.89",
    )
    .env({"HF_HOME": MODEL_DIR, "TOKENIZERS_PARALLELISM": "false", BUILD_ENV: BUILD_ID})
    .add_local_file(
        PIPELINE_ROOT / "THIRD_PARTY_NOTICES.md", "/licenses/THIRD_PARTY_NOTICES.md", copy=True
    )
    .add_local_file(
        PIPELINE_ROOT / "LICENSES/Chapter-Llama-MIT.txt",
        "/licenses/Chapter-Llama-MIT.txt",
        copy=True,
    )
    .add_local_python_source("temnia_pipeline")
)
app = modal.App(APP_NAME, image=image)
models = modal.Volume.from_name("temnia-chapter-llama-models", create_if_missing=True)


@app.function(timeout=30)
def deployment_identity() -> dict[str, object]:
    """Read the exact app/config identity before creating any inference attempt."""
    return {
        "protocol": PROTOCOL,
        "build": BUILD_ID,
        "config": ModelConfig().model_dump(mode="json"),
        "resources": ResourceProfile().model_dump(mode="json"),
    }


@app.function(
    secrets=[modal.Secret.from_name("temnia-hf")],
    volumes={MODEL_DIR: models},
    timeout=3600,
    retries=0,
)
def prepare_models() -> dict[str, object]:
    """Explicit deployment setup; inference itself never downloads model bytes."""
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    root = Path(MODEL_DIR)
    for repo, revision, files in (
        (BASE_REPO, BASE_REVISION, BASE_FILES),
        (ADAPTER_REPO, ADAPTER_REVISION, tuple(f"{ADAPTER_PATH}/{name}" for name in ADAPTER_FILES)),
    ):
        snapshot_download(  # pyright: ignore[reportUnknownMemberType]
            repo,
            revision=revision,
            cache_dir=root / "hub",
            allow_patterns=list(files),
        )
        snapshot(root, repo, revision, files)
    models.commit()
    return {"ready": True, "config": ModelConfig().model_dump(mode="json")}


async def execute(raw_job: dict[str, Any]) -> dict[str, Any]:
    """Admit one execution before model loading; persist known failures and successes."""
    job = ChapterLlamaJob.model_validate(raw_job)
    call_id = modal.current_function_call_id() or ""
    task_id = os.environ.get("MODAL_TASK_ID", "")
    store = ObstoreCheckpointStore(
        make_store(StorageSettings.require_env("Modal Secret temnia-r2"))
    )
    try:
        reused = await prepare_execution(
            store, job, build=BUILD_ID, call_id=call_id, task_id=task_id
        )
    except AdmissionOutcomeUnknownError as error:
        return ChapterLlamaOutcome(
            job_sha256=job.sha256,
            build=BUILD_ID,
            modal_call_id=call_id,
            modal_task_id=task_id,
            status="outcome_unknown",
            error_code=type(error).__name__,
            error_message=str(error),
        ).model_dump(mode="json")
    if reused is not None:
        return reused.model_dump(mode="json")
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(TransformersEngine().infer, job.input)
        outcome = ChapterLlamaOutcome(
            job_sha256=job.sha256,
            build=BUILD_ID,
            modal_call_id=call_id,
            modal_task_id=task_id,
            status="ok",
            compute_seconds=time.monotonic() - started,
            result=result,
        )
    except Exception as error:  # noqa: BLE001
        outcome = ChapterLlamaOutcome(
            job_sha256=job.sha256,
            build=BUILD_ID,
            modal_call_id=call_id,
            modal_task_id=task_id,
            status="failed",
            compute_seconds=time.monotonic() - started,
            error_code=type(error).__name__,
            error_message=str(error)[:2000],
            rejected_output=error.raw_output if isinstance(error, InvalidGenerationError) else None,
            rejected_input_tokens=error.input_tokens
            if isinstance(error, InvalidGenerationError)
            else None,
            rejected_output_tokens=error.output_tokens
            if isinstance(error, InvalidGenerationError)
            else None,
        )
    await publish_outcome(store, job, outcome)
    return outcome.model_dump(mode="json")


@app.function(
    gpu=GPU,
    cpu=CPUS,
    memory=MEMORY_MB,
    timeout=TIMEOUT_SECONDS,
    retries=0,
    single_use_containers=True,
    max_containers=1,
    secrets=[modal.Secret.from_name("temnia-r2")],
    volumes={MODEL_DIR: models},
)
async def infer(raw_job: dict[str, Any]) -> dict[str, Any]:
    """One independently addressed inference; no HF token exists in this function."""
    return await execute(raw_job)
