"""Resource bounds and model identity are enforced before paid dispatch."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from temnia_pipeline.speech.model_files import file_manifest, manifest_identity, verify_model_files
from temnia_pipeline.speech.resources import SpeechResourceProfile


def test_profile_prices_cover_cpu_and_memory_and_change_identity() -> None:
    four = SpeechResourceProfile(stage_timeout_seconds=900)
    eight = SpeechResourceProfile(cpu_cores=8, stage_timeout_seconds=900)
    assert four.minimum_rate_micros_per_hour == 1_115_712
    assert eight.minimum_rate_micros_per_hour == 1_304_352
    assert four.reservation_micros(1_250_000) == 354_167
    assert eight.reservation_micros(1_450_000) == 410_834
    assert four.sha256 != eight.sha256
    assert four.sha256 != four.model_copy(update={"progress_mode": "synchronous_control"}).sha256
    with pytest.raises(ValueError, match="price floor"):
        eight.reservation_micros(1_250_000)
    with pytest.raises(ValidationError):
        SpeechResourceProfile.model_validate({"cpuCores": 16})
    with pytest.raises(ValidationError):
        SpeechResourceProfile.model_validate({"stageTimeoutSeconds": 1800})


def test_model_manifest_refuses_changed_extra_missing_and_links(tmp_path: Path) -> None:
    weight = tmp_path / "weight.bin"
    weight.write_bytes(b"frozen weights")
    identity = manifest_identity(file_manifest(tmp_path))
    verify_model_files(identity, tmp_path)
    weight.write_bytes(b"mutated weights")
    with pytest.raises(ValueError, match="do not match"):
        verify_model_files(identity, tmp_path)
    weight.write_bytes(b"frozen weights")
    extra = tmp_path / "extra.json"
    extra.write_bytes(b"{}")
    with pytest.raises(ValueError, match="do not match"):
        verify_model_files(identity, tmp_path)
    extra.unlink()
    weight.unlink()
    with pytest.raises(ValueError, match="empty"):
        verify_model_files(identity, tmp_path)
    weight.symlink_to(Path(__file__))
    with pytest.raises(ValueError, match="links"):
        verify_model_files(identity, tmp_path)


def test_model_manifest_refuses_credentials(tmp_path: Path) -> None:
    (tmp_path / "token").write_bytes(b"never a model")
    with pytest.raises(ValueError, match="authentication"):
        file_manifest(tmp_path)
