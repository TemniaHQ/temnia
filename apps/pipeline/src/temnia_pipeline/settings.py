"""Process settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

TranscodeBackend = Literal["local", "modal"]
TranscriptionProviderName = Literal["recorded", "modal"]
DEFAULT_MODAL_APP = "temnia-media"
DEFAULT_PROGRESS_DICT = "temnia-ladder-progress"
# A second Dict rather than one shared with the ladder: the two functions write
# different progress shapes, and a reader that guessed wrong from a call id
# would be a bug that only appears when both run at once.
DEFAULT_TRANSCRIPT_DICT = "temnia-transcript-progress"
# Where recorded WhisperX responses are looked for when no explicit file is
# named. Relative to nothing: the gate mounts its fixtures and points here.
DEFAULT_RECORDINGS_DIR = "/var/lib/temnia/recordings"
# Where model weights are cached. One directory for every model the pipeline
# loads, pointed at by HF_HOME, so a machine downloads each of them once and
# the pipeline image can bake them in at the same path.
DEFAULT_MODELS_DIR = "~/.cache/temnia-models"

# Region is not in this list on purpose: R2 wants `auto` and Garage `garage`,
# neither is a secret, and a wrong one fails at the first request rather than
# quietly. The other four have no safe default away from the laptop.
REQUIRED_STORAGE_VARIABLES = (
    "STORAGE_ENDPOINT",
    "STORAGE_BUCKET",
    "STORAGE_ACCESS_KEY_ID",
    "STORAGE_SECRET_ACCESS_KEY",
)
DEFAULT_STORAGE_REGION = "garage"


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
            region=os.environ.get("STORAGE_REGION", DEFAULT_STORAGE_REGION),
            bucket=os.environ.get("STORAGE_BUCKET", "temnia-media"),
            access_key_id=os.environ.get("STORAGE_ACCESS_KEY_ID", "GK746d6e696164657600000000"),
            secret_access_key=os.environ.get(
                "STORAGE_SECRET_ACCESS_KEY",
                "7f5fbe4a561d5196e4422e7fe9b8b8880846f9e153aacd3a142fd3d27f8f2bd2",
            ),
        )

    @classmethod
    def require_env(cls, source: str) -> StorageSettings:
        """Read `STORAGE_*` with no fallback, naming what is missing and where from.

        `from_env`'s dev defaults are right on a laptop and wrong anywhere the
        values arrive from something that can be half configured. A Modal
        container whose secret is missing a key would otherwise take the Garage
        defaults, dial localhost, and surface minutes later as a connection
        error deep inside the download, with a GPU already booked. `source`
        names the thing that was supposed to set them, because the reader's
        next move is to open it.
        """
        missing = [name for name in REQUIRED_STORAGE_VARIABLES if not os.environ.get(name)]
        if missing:
            plural = "is" if len(missing) == 1 else "are"
            msg = (
                f"the object store is not configured: {', '.join(missing)} {plural} missing or "
                f"empty. {source} sets them; check its contents, not just that it exists."
            )
            raise RuntimeError(msg)
        return cls.from_env()


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
class TranscriptionSettings:
    """Which engine transcribes: a recording, or WhisperX on a Modal GPU.

    `recorded` is the default for the same reason `local` is the transcode
    default: a missing variable must never route work at something that costs
    money, and it must never route it at nothing at all either. There is no
    "unconfigured" state in which transcription silently does not happen; the
    legacy degraded to "no transcript" that way and nobody noticed for a week.
    """

    provider: TranscriptionProviderName
    modal_app: str
    modal_environment: str | None
    progress_dict: str
    recordings_dir: Path
    recording: Path | None

    @classmethod
    def from_env(cls) -> TranscriptionSettings:
        """Read `TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_RECORDING(S_DIR)`, and the Modal names."""
        provider = os.environ.get("TRANSCRIPTION_PROVIDER", "recorded")
        if provider not in get_args(TranscriptionProviderName):
            options = ", ".join(get_args(TranscriptionProviderName))
            msg = f"TRANSCRIPTION_PROVIDER is {provider!r}; it must be one of {options}"
            raise ValueError(msg)
        recording = os.environ.get("TRANSCRIPTION_RECORDING") or None
        return cls(
            provider=cast("TranscriptionProviderName", provider),
            modal_app=os.environ.get("MODAL_APP", DEFAULT_MODAL_APP),
            modal_environment=os.environ.get("MODAL_ENVIRONMENT") or None,
            progress_dict=os.environ.get("MODAL_TRANSCRIPT_PROGRESS_DICT", DEFAULT_TRANSCRIPT_DICT),
            recordings_dir=Path(
                os.environ.get("TRANSCRIPTION_RECORDINGS_DIR", DEFAULT_RECORDINGS_DIR)
            ),
            recording=Path(recording) if recording else None,
        )


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Where the substrate's model weights live.

    Its own settings object rather than a field of `PipelineSettings`,
    because the eval runner and the fetch script read it without being a
    worker, and because a wrong value here costs a download rather than a
    misrouted job.
    """

    models_dir: Path

    @classmethod
    def from_env(cls) -> ModelSettings:
        """Read `TEMNIA_MODELS_DIR`; `~` is expanded, so the default works unset."""
        return cls(
            models_dir=Path(os.environ.get("TEMNIA_MODELS_DIR") or DEFAULT_MODELS_DIR).expanduser()
        )


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    """Everything else the worker needs."""

    database_url: str
    work_root: Path
    ffmpeg: str
    ffprobe: str
    transcode: TranscodeSettings
    transcription: TranscriptionSettings

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
            transcription=TranscriptionSettings.from_env(),
        )
