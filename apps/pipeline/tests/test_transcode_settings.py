"""How `TRANSCODE_BACKEND` is read, and what it builds."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from temnia_pipeline.settings import (
    DEFAULT_MODAL_APP,
    DEFAULT_PROGRESS_DICT,
    PipelineSettings,
    TranscodeSettings,
)
from temnia_pipeline.transcode.factory import make_transcoder
from temnia_pipeline.transcode.local import LocalTranscoder

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

BACKEND_VARIABLES = (
    "TRANSCODE_BACKEND",
    "MODAL_APP",
    "MODAL_ENVIRONMENT",
    "MODAL_PROGRESS_DICT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # a pytest fixture is called by name, not by reference
    for name in BACKEND_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_the_default_backend_is_local() -> None:
    """A missing variable must never route work at something that costs money."""
    settings = TranscodeSettings.from_env()
    assert settings.backend == "local"
    assert settings.modal_app == DEFAULT_MODAL_APP
    assert settings.modal_environment is None
    assert settings.progress_dict == DEFAULT_PROGRESS_DICT


def test_the_modal_backend_reads_its_app_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCODE_BACKEND", "modal")
    monkeypatch.setenv("MODAL_ENVIRONMENT", "staging")
    settings = TranscodeSettings.from_env()
    assert settings.backend == "modal"
    assert settings.modal_environment == "staging"


def test_a_misspelt_backend_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCODE_BACKEND", "moddal")
    with pytest.raises(ValueError, match="TRANSCODE_BACKEND is 'moddal'"):
        TranscodeSettings.from_env()


def test_an_empty_environment_means_modals_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODAL_ENVIRONMENT", "")
    assert TranscodeSettings.from_env().modal_environment is None


def test_the_factory_builds_the_local_ladder_without_touching_modal(tmp_path: Path) -> None:
    settings = PipelineSettings.from_env()
    transcoder = make_transcoder(settings, cast("S3Store", object()), tmp_path)
    assert isinstance(transcoder, LocalTranscoder)
    assert transcoder.work_root == tmp_path
