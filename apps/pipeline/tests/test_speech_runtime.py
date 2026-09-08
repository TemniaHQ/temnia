# These deployment-envelope tests intentionally inspect private constants and
# the private cost helper to keep runtime resources and reservations aligned.
# pyright: reportPrivateUsage=false

import hashlib
import io
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from temnia_pipeline import modal_speech_app
from temnia_pipeline.media.speech_pcm import WINDOW_SAMPLES, stream_pcm_windows
from temnia_pipeline.settings import (
    DEFAULT_SPEECH_BUDGET_MICROS,
    DEFAULT_SPEECH_PROGRESS_DICT,
    DEFAULT_SPEECH_RATE_MICROS_PER_HOUR,
    MIN_SPEECH_RATE_MICROS_PER_HOUR,
    TranscriptionSettings,
)
from temnia_pipeline.speech import assets
from temnia_pipeline.speech.activities import _estimated_cost
from temnia_pipeline.speech.client import (
    SpeechDeploymentError,
    SpeechModalClient,
    _progress,
    assert_checkpointed_deployment,
)
from temnia_pipeline.speech.telemetry import TelemetryRecorder
from temnia_pipeline.transcription.checkpointed import TranscriptionPlan


def opener(body: bytes) -> assets.Opener:
    def open_url(url: str) -> io.BytesIO:
        del url
        return io.BytesIO(body)

    return open_url


async def test_pcm_stream_pads_only_the_final_window(tmp_path: Path) -> None:
    executable = tmp_path / "fake-ffmpeg"
    executable.write_text(
        "#!/bin/sh\n"
        "python3 -c 'import os,struct; os.write(1, struct.pack(\"<600f\", *range(600)))'\n"
    )
    executable.chmod(0o755)
    found = [window async for window in stream_pcm_windows(Path("ignored"), ffmpeg=str(executable))]
    assert [window.valid_samples for window in found] == [WINDOW_SAMPLES, 88]
    assert found[1].start_sample == WINDOW_SAMPLES
    assert found[1].samples[-1] == 0


def test_partial_telemetry_distinguishes_failed_from_completed_inference() -> None:
    recorder = TelemetryRecorder(interval=0.01)
    recorder.start()
    with pytest.raises(RuntimeError), recorder.phase("inference"):
        raise RuntimeError
    assert not recorder.inference_completed
    time.sleep(0.02)
    partial = recorder.finish(complete=True)
    assert partial.sample_count >= 1
    assert partial.progress.percent is None
    assert partial.metrics_unavailable

    completed = TelemetryRecorder()
    with completed.phase("inference"):
        pass
    assert completed.inference_completed


def test_asset_install_is_verified_and_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"pinned-model"
    monkeypatch.setattr(assets, "SIZE_BYTES", len(body))
    monkeypatch.setattr(assets, "SHA256", hashlib.sha256(body).hexdigest())
    destination = tmp_path / "models" / "silero.onnx"
    assert assets.install_asset(destination, opener=opener(body))
    assert destination.read_bytes() == body
    assert not assets.install_asset(destination, opener=opener(b"unused"))
    destination.unlink()
    with pytest.raises(ValueError, match="bytes"):
        assets.install_asset(destination, opener=opener(b"wrong"))
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_modal_stage_resource_envelope_and_budget_cover_all_dispatches() -> None:
    function = modal_speech_app._FUNCTION  # noqa: SLF001
    assert function["gpu"] == "L4"
    assert function["cpu"] == (4, 4)
    assert function["memory"] == (16_384, 16_384)
    assert function["startup_timeout"] == 120
    assert function["timeout"] == 3600
    assert function["retries"] == 0
    assert function["single_use_containers"] is True

    plan = TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech",
        environment=None,
        protocol="temnia-speech/1",
        build="build",
        budget_micros=DEFAULT_SPEECH_BUDGET_MICROS,
        rate_micros_per_hour=DEFAULT_SPEECH_RATE_MICROS_PER_HOUR,
        stage_timeout_seconds=3600,
        startup_timeout_seconds=120,
        dispatch_limit=5,
        audio_sha256="a" * 64,
        audio_size_bytes=1,
        detector="silero",
        detector_revision="revision",
        detector_sha256="b" * 64,
    )
    estimated = _estimated_cost(plan)
    assert estimated == 1_291_667
    assert estimated * plan.dispatch_limit <= plan.budget_micros


def test_diarization_loads_the_explicit_whisperx_submodule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = SimpleNamespace()
    diarize = SimpleNamespace(DiarizationPipeline=object())
    loaded: list[str] = []

    def load(name: str) -> object:
        loaded.append(name)
        return root if name == "whisperx" else diarize

    monkeypatch.setattr(modal_speech_app, "import_module", load)

    assert modal_speech_app._load_whisperx("diarize") is root  # noqa: SLF001
    assert root.diarize is diarize
    assert loaded == ["whisperx", "whisperx.diarize"]


def test_speech_rate_below_dated_resource_floor_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPEECH_RATE_MICROS_PER_HOUR", str(MIN_SPEECH_RATE_MICROS_PER_HOUR - 1))
    with pytest.raises(ValueError, match=r"L4\+4CPU\+16GiB floor"):
        TranscriptionSettings.from_env()


async def test_checkpointed_boot_requires_an_explicit_deployed_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "modal-checkpointed")
    monkeypatch.delenv("MODAL_SPEECH_BUILD", raising=False)
    settings = TranscriptionSettings.from_env()
    assert settings.speech_expected_build is None
    with pytest.raises(SpeechDeploymentError, match="MODAL_SPEECH_BUILD"):
        await assert_checkpointed_deployment(SpeechModalClient(settings), settings)

    monkeypatch.setenv("MODAL_SPEECH_BUILD", "deployed-input-fingerprint")
    assert TranscriptionSettings.from_env().speech_expected_build == "deployed-input-fingerprint"


def test_checkpointed_progress_uses_the_protocol_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    expected = object()

    def from_name(name: str, **options: object) -> object:
        observed.update(name=name, **options)
        return expected

    monkeypatch.setenv("MODAL_SPEECH_PROGRESS_DICT", "disconnected-worker-only-dict")
    monkeypatch.setattr(modal_speech_app.modal.Dict, "from_name", from_name)
    settings = TranscriptionSettings.from_env()
    assert _progress(settings) is expected
    assert observed == {
        "name": DEFAULT_SPEECH_PROGRESS_DICT,
        "create_if_missing": True,
        "environment_name": None,
    }
