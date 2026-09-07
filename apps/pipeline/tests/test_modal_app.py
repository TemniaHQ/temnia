"""The deployed app's shape, checked without an account.

Nothing here calls Modal. What it protects is the two things that would fail
silently: an image that stops verifying the ffmpeg it downloads, and an import
that drags Temporal or the database into a container that has neither.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from temnia_pipeline import modal_app
from temnia_pipeline.transcode import CONTRACT_VERSION
from temnia_pipeline.transcode.modal_client import LADDER_FUNCTION, VERSION_FUNCTION
from temnia_pipeline.transcription import TranscribeJob, UnsupportedLanguageError
from temnia_pipeline.transcription.modal_whisperx import (
    MODEL,
    NAME,
    TRANSCRIBE_FUNCTION,
    VERSION,
)

BANNED = ("temporalio", "psycopg", "av")

# Also builds a job from a plain dict, which is what the container does with
# the payload Modal hands it. Pydantic resolves `LadderJob.video` lazily, so a
# `VideoFacts` that exists only for the type checker passes every test that
# happens to have the name in scope and fails here, in the one process that
# does not.
ISOLATION_PROBE = f"""
import sys
from temnia_pipeline.transcode import LadderJob
from temnia_pipeline.transcription import TranscribeJob
import temnia_pipeline.modal_app

TranscribeJob.model_validate(
    {{
        "audioKey": "org/a/source/b/audio/audio.m4a",
        "artifactPrefix": "org/a/source/b/",
        "attempt": 1,
        "durationMs": 40116,
    }}
)
LadderJob.model_validate(
    {{
        "masterKey": "org/a/source/b/master/master.mov",
        "artifactPrefix": "org/a/source/b/",
        "sizeBytes": 1,
        "video": {{
            "width": 1920,
            "height": 1080,
            "fps": "25",
            "variableFrameRate": False,
            "codec": "h264",
        }},
        "hasAudio": True,
        "expectedSeconds": 1.0,
    }}
)
loaded = [name for name in {BANNED!r} if name in sys.modules]
print(",".join(loaded))
"""


def test_the_ffmpeg_tarball_is_pinned_and_the_build_checks_it() -> None:
    assert len(modal_app.FFMPEG_SHA256) == 64
    assert modal_app.FFMPEG_URL.endswith(f"{modal_app.FFMPEG_DIR}.tar.xz")
    steps = modal_app.FFMPEG_INSTALL
    verify = next(i for i, step in enumerate(steps) if "sha256sum -c -" in step)
    extract = next(i for i, step in enumerate(steps) if step.startswith("tar -xJf"))
    # A replaced build must fail before the archive is opened, not after.
    assert verify < extract
    assert modal_app.FFMPEG_SHA256 in steps[verify]
    assert steps[-1].startswith("rm -rf")


def test_the_worker_and_the_app_agree_on_the_function_names() -> None:
    """Modal registers a function under its Python name; the client looks it up by string."""
    assert hasattr(modal_app, LADDER_FUNCTION)
    assert hasattr(modal_app, VERSION_FUNCTION)
    assert hasattr(modal_app, TRANSCRIBE_FUNCTION)
    assert modal_app.version.local() == CONTRACT_VERSION


def test_one_contract_version_covers_both_functions() -> None:
    """A ladder deployed without its transcription is a failed deploy, not a surprise."""
    assert CONTRACT_VERSION == "2"


def test_the_provider_reports_the_model_and_release_the_image_pins() -> None:
    """The three strings ride on every revision, so a calibration round can compare runs."""
    assert NAME == "whisperx"
    assert MODEL == modal_app.WHISPER_MODEL
    assert VERSION == modal_app.WHISPERX_VERSION
    assert any(VERSION in package for package in modal_app.WHISPERX_PACKAGES)


def test_the_transcription_function_asks_for_both_secrets_and_the_model_volume() -> None:
    """A missing HF_TOKEN is a gated model failing minutes into a billing GPU."""
    assert modal_app.HF_SECRET == "temnia-hf"  # noqa: S105
    assert modal_app.R2_SECRET == "temnia-r2"  # noqa: S105
    assert modal_app.MODEL_VOLUME == "temnia-models"
    assert modal_app.MODEL_DIR == "/models"


def test_torch_comes_from_a_pytorch_index_that_carries_torch_2_8() -> None:
    """The cu124 index stops at torch 2.6 and whisperx needs 2.8 (found on 2026-09-07).

    The wheels bundle their own CUDA libraries, so the index only has to carry
    the pinned version; pinning it keeps a second copy from arriving via PyPI.
    """
    assert modal_app.TORCH_INDEX.startswith("https://download.pytorch.org/whl/cu")
    assert "cu124" not in modal_app.TORCH_INDEX
    assert all(
        package.startswith(("torch==", "torchaudio==")) for package in modal_app.TORCH_PACKAGES
    )
    assert all("==" in package for package in modal_app.TORCH_PACKAGES)


def test_the_diarization_model_is_the_one_pyannote_4_defaults_to() -> None:
    assert modal_app.DIARIZATION_MODEL == "pyannote/speaker-diarization-community-1"


def test_an_alignable_language_passes_and_an_unsupported_one_names_its_code() -> None:
    """The check runs before the alignment model is fetched, so it costs nothing."""
    available = {"en", "fr", "de"}
    modal_app.assert_alignable("fr", available)
    with pytest.raises(UnsupportedLanguageError, match="code: sw"):
        modal_app.assert_alignable("sw", available)


def test_the_raw_key_is_one_object_per_attempt() -> None:
    """Never overwritten: it is the record a fixture is cut from and S12 reads back."""
    job = TranscribeJob(
        audio_key="org/a/source/b/audio/audio.m4a",
        artifact_prefix="org/a/source/b/",
        attempt=2,
        duration_ms=1000,
    )
    assert job.raw_key == "org/a/source/b/transcript/raw-2.json"


def test_the_module_a_container_imports_pulls_in_no_worker_dependencies() -> None:
    """A subprocess, because the rest of this suite has already imported them."""
    finished = subprocess.run(  # noqa: S603
        [sys.executable, "-c", ISOLATION_PROBE], capture_output=True, text=True, check=True
    )
    assert finished.stdout.strip() == ""
