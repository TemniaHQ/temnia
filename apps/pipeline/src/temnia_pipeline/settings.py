"""Process settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

TranscodeBackend = Literal["local", "modal"]
DEFAULT_MODAL_APP = "temnia-media"
DEFAULT_PROGRESS_DICT = "temnia-ladder-progress"


@dataclass(frozen=True, slots=True)
class TemporalSettings:
    """Where the worker connects and which queue it serves."""

    address: str
    namespace: str
    task_queue: str

    @classmethod
    def from_env(cls) -> TemporalSettings:
        """Read `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`."""
        return cls(
            address=os.environ.get("TEMPORAL_ADDRESS", "localhost:56233"),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            # Must match TASK_QUEUES.pipeline in @temnia/contracts.
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "temnia-pipeline"),
        )


@dataclass(frozen=True, slots=True)
class StorageSettings:
    """The S3-compatible store: Garage in compose, R2 on staging."""

    endpoint: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_env(cls) -> StorageSettings:
        """Read `STORAGE_*`; the compose defaults are the dev-only Garage key."""
        return cls(
            endpoint=os.environ.get("STORAGE_ENDPOINT", "http://localhost:56900"),
            region=os.environ.get("STORAGE_REGION", "garage"),
            bucket=os.environ.get("STORAGE_BUCKET", "temnia-media"),
            access_key_id=os.environ.get("STORAGE_ACCESS_KEY_ID", "GK746d6e696164657600000000"),
            secret_access_key=os.environ.get(
                "STORAGE_SECRET_ACCESS_KEY",
                "7f5fbe4a561d5196e4422e7fe9b8b8880846f9e153aacd3a142fd3d27f8f2bd2",
            ),
        )


@dataclass(frozen=True, slots=True)
class TranscodeSettings:
    """Where the HLS ladder runs: this worker, or a GPU on Modal.

    `local` is the default, because it is what dev and the gate use and a
    missing variable must never route work at a service that costs money.
    """

    backend: TranscodeBackend
    modal_app: str
    modal_environment: str | None
    progress_dict: str

    @classmethod
    def from_env(cls) -> TranscodeSettings:
        """Read `TRANSCODE_BACKEND`, `MODAL_APP`, `MODAL_ENVIRONMENT`, `MODAL_PROGRESS_DICT`."""
        backend = os.environ.get("TRANSCODE_BACKEND", "local")
        if backend not in get_args(TranscodeBackend):
            options = ", ".join(get_args(TranscodeBackend))
            msg = f"TRANSCODE_BACKEND is {backend!r}; it must be one of {options}"
            raise ValueError(msg)
        return cls(
            backend=cast("TranscodeBackend", backend),
            modal_app=os.environ.get("MODAL_APP", DEFAULT_MODAL_APP),
            # Unset means Modal's default environment for the workspace.
            modal_environment=os.environ.get("MODAL_ENVIRONMENT") or None,
            progress_dict=os.environ.get("MODAL_PROGRESS_DICT", DEFAULT_PROGRESS_DICT),
        )


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    """Everything else the worker needs."""

    database_url: str
    work_root: Path
    ffmpeg: str
    ffprobe: str
    transcode: TranscodeSettings

    @classmethod
    def from_env(cls) -> PipelineSettings:
        """Read `PIPELINE_DATABASE_URL`, `PIPELINE_WORK_DIR`, `FFMPEG`, `FFPROBE`."""
        return cls(
            database_url=os.environ.get(
                "PIPELINE_DATABASE_URL",
                "postgres://temnia_pipeline:temnia_pipeline@localhost:56432/temnia",
            ),
            work_root=Path(os.environ.get("PIPELINE_WORK_DIR", "/tmp/temnia-pipeline")),  # noqa: S108
            ffmpeg=os.environ.get("FFMPEG", "ffmpeg"),
            ffprobe=os.environ.get("FFPROBE", "ffprobe"),
            transcode=TranscodeSettings.from_env(),
        )
