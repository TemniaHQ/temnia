"""The deployed app's shape, checked without an account.

Nothing here calls Modal. What it protects is the two things that would fail
silently: an image that stops verifying the ffmpeg it downloads, and an import
that drags Temporal or the database into a container that has neither.
"""

from __future__ import annotations

import json
import subprocess
import sys
from fractions import Fraction
from typing import TYPE_CHECKING

import pytest

from temnia_pipeline import modal_app
from temnia_pipeline.media.facts import VideoFacts
from temnia_pipeline.media.ffmpeg import FfmpegError
from temnia_pipeline.transcode import CONTRACT_VERSION, LadderJob
from temnia_pipeline.transcode.modal_client import LADDER_FUNCTION, VERSION_FUNCTION
from temnia_pipeline.transcription import TranscribeJob, UnsupportedLanguageError
from temnia_pipeline.transcription.modal_whisperx import (
    MODEL,
    NAME,
    TRANSCRIBE_FUNCTION,
    VERSION,
)

if TYPE_CHECKING:
    from pathlib import Path

BANNED = ("temporalio", "psycopg", "av")

PREFIX = "org/0192e8a0-0000-7000-8000-000000000001/source/0192e8a0-0000-7000-8000-0000000000aa/"

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
            "pixFmt": "yuv420p",
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


def test_the_release_smoke_is_deployed_beside_the_functions_it_proves() -> None:
    """The boot probe checks a constant; the smoke runs the speech path (S2 review, I29)."""
    assert hasattr(modal_app, "smoke_transcribe")
    assert modal_app.SMOKE_FIXTURE.exists()
    assert modal_app.SMOKE_FIXTURE.stat().st_size < 200_000
    meta = json.loads(modal_app.SMOKE_META.read_text())
    assert meta["durationMs"] == 8824
    assert meta["expect"] == ["chapters", "smoke"]


def test_every_call_gets_its_own_scratch_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per call, not per source: a warm container serves many (S2 review, I27)."""
    monkeypatch.setattr(modal_app, "WORK_DIR", tmp_path / "work")
    first = modal_app.scratch_dir("ladder")
    second = modal_app.scratch_dir("ladder")
    assert first != second
    assert first.parent == tmp_path / "work"
    assert first.name.startswith("ladder-")
    assert first.is_dir()


def test_the_disk_preflight_refuses_with_the_numbers(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"GB free under .* and .* GB needed"):
        modal_app.assert_disk_headroom(tmp_path, 1 << 60)
    modal_app.assert_disk_headroom(tmp_path, 1)


def test_one_contract_version_covers_both_functions() -> None:
    """A ladder deployed without its transcription is a failed deploy, not a surprise."""
    assert CONTRACT_VERSION == "4"


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


class FakeFfmpeg:
    """Stands in for `run_ffmpeg`: records the arguments, writes what the ladder reads back."""

    def __init__(self, out_dir: Path, *, failures: int) -> None:
        self.out_dir = out_dir
        self.failures = failures
        self.calls: list[list[str]] = []

    async def __call__(self, ffmpeg: str, args: list[str], *, on_progress: object = None) -> str:
        _ = (ffmpeg, on_progress)
        self.calls.append(args)
        if len(self.calls) <= self.failures:
            # What a card that cannot decode this profile, or a driver that
            # will not initialise the decoder, actually looks like.
            msg = (
                "ffmpeg exited 1: Failed setup for format cuda: "
                "hwaccel initialisation returned error"
            )
            raise FfmpegError(msg)
        (self.out_dir / "master.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:6\n")
        iframes = self.out_dir / "iframes"
        iframes.mkdir(parents=True, exist_ok=True)
        (iframes / "index.m3u8").write_text(
            "#EXTM3U\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXTINF:2.000,\niframes.mp4\n#EXT-X-ENDLIST\n"
        )
        (iframes / "iframes.mp4").write_bytes(b"0" * 1024)
        return ""

    def decoders(self) -> list[str]:
        """Which graph each attempt asked for, read off the arguments themselves."""
        return ["cuda" if "-hwaccel" in args else "cpu" for args in self.calls]


def _ladder_job(codec: str = "h264", pix_fmt: str | None = "yuv420p") -> LadderJob:
    return LadderJob(
        master_key=PREFIX + "master/master.mov",
        artifact_prefix=PREFIX,
        size_bytes=1024,
        video=VideoFacts(
            width=1920,
            height=1080,
            fps=Fraction(25),
            variable_frame_rate=False,
            codec=codec,
            pix_fmt=pix_fmt,
        ),
        has_audio=True,
        expected_seconds=4.0,
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job: LadderJob, *, failures: int
) -> tuple[FakeFfmpeg, str]:
    out_dir = tmp_path / "hls"
    fake = FakeFfmpeg(out_dir, failures=failures)
    monkeypatch.setattr("temnia_pipeline.media.hls.run_ffmpeg", fake)

    async def on_encode(seconds: float) -> None:
        _ = seconds

    _rungs, decoder = await modal_app.run_ladder(job, tmp_path / "master.mov", out_dir, on_encode)
    return fake, decoder


async def test_a_decodable_source_runs_once_on_the_gpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake, decoder = await _run(monkeypatch, tmp_path, _ladder_job(), failures=0)

    assert decoder == "cuda"
    assert fake.decoders() == ["cuda"]


async def test_a_cuda_graph_that_fails_is_encoded_again_on_the_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A profile this card refuses, or a driver that will not initialise, costs
    the GPU attempt and still produces a ladder.
    """
    fake, decoder = await _run(monkeypatch, tmp_path, _ladder_job(), failures=1)

    assert fake.decoders() == ["cuda", "cpu"]
    # What is recorded is what produced the ladder, not what was asked for.
    assert decoder == "cpu"


async def test_a_second_failure_is_the_encode_failure_the_worker_classifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One retry, in one direction. `FfmpegError` is terminal at the worker."""
    with pytest.raises(FfmpegError):
        await _run(monkeypatch, tmp_path, _ladder_job(), failures=2)


async def test_a_source_the_gpu_cannot_decode_never_spends_an_attempt_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ProRes is the master format this matters most for, and it is decided
    from the probe rather than from a failed run.
    """
    job = _ladder_job(codec="prores", pix_fmt="yuv422p10le")
    fake, decoder = await _run(monkeypatch, tmp_path, job, failures=0)

    assert fake.decoders() == ["cpu"]
    assert decoder == "cpu"


async def test_a_cpu_run_that_fails_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job = _ladder_job(codec="prores", pix_fmt="yuv422p10le")
    with pytest.raises(FfmpegError):
        await _run(monkeypatch, tmp_path, job, failures=1)
