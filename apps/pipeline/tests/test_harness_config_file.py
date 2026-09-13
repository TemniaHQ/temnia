"""The committed deployment file is the harness configuration; the environment only keys."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from temnia_pipeline.harness.settings import RECORDED_TOPIC_OUTPUTS, HarnessSettings
from test_harness_settings import snapshot, write_snapshot

HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"
PLACEHOLDER_SECRETS = {"OPENROUTER_API_KEY": "placeholder", "AI_GATEWAY_API_KEY": "placeholder"}


def _committed_configurations() -> list[Path]:
    return sorted(
        path
        for path in HARNESS_DIR.glob("*.json")
        if json.loads(path.read_bytes()).get("format") == "harness-config/1"
    )


def test_every_committed_deployment_configuration_boots() -> None:
    """What the rollout would find on the box is found here: snapshot ID, pools, ceilings."""
    files = _committed_configurations()
    assert files, "no deployment configuration is committed"
    for path in files:
        settings = HarnessSettings.from_env(
            {"HARNESS_CONFIG_PATH": str(path), **PLACEHOLDER_SECRETS}
        )
        assert settings.config_path == path
        loaded = settings.validate_boot()
        assert loaded is not None
        assert loaded.snapshot_id == settings.route_snapshot_id
        assert not loaded.synthetic
        assert settings.allowed_config().routeSnapshotId == loaded.snapshot_id
        assert settings.gateway_api_key == "placeholder"


def _write_config(directory: Path, **overrides: object) -> Path:
    value = snapshot()
    write_snapshot(directory / "routes.json", value)
    (directory / "recorded.json").write_text(
        json.dumps(
            {
                "outputs": {
                    stage: {"output": {}, "synthetic": True} for stage in RECORDED_TOPIC_OUTPUTS
                },
                "synthetic": True,
            }
        )
    )
    body: dict[str, object] = {
        "format": "harness-config/1",
        "enabled": True,
        "backend": "recorded",
        "gateway": "openrouter",
        "routeSnapshot": {"path": "routes.json", "id": value.snapshot_id},
        "limits": {
            "maxOutputTokens": 4096,
            "maxDispatches": 7,
            "maxRepairs": 1,
            "maxRunBudgetMicros": 123,
            "maxRenderConcurrency": 1,
            "evidenceWindowSentences": 40,
        },
        "topicShotDetector": "pyscenedetect-adaptive",
        "allowRecorded": True,
        "recordedFixturePath": "recorded.json",
        **overrides,
    }
    path = directory / "deployment.json"
    path.write_text(json.dumps(body))
    return path


def test_file_configuration_ignores_every_other_harness_environment_entry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write_config(tmp_path)
    stale = {
        "HARNESS_ENABLED": "0",
        "HARNESS_MAX_OUTPUT_TOKENS": "999",
        "HARNESS_ROUTE_SNAPSHOT_ID": "f" * 64,
        "HARNESS_CHAPTERS_ENABLED": "1",
    }
    with caplog.at_level(logging.WARNING, logger="temnia.harness.settings"):
        settings = HarnessSettings.from_env(
            {"HARNESS_CONFIG_PATH": str(path), "OPENROUTER_API_KEY": "key", **stale}
        )
    assert settings.enabled is True
    assert settings.max_output_tokens == 4096
    assert settings.max_dispatches == 7
    assert settings.max_run_budget_micros == 123
    assert settings.topic_shot_detector == "pyscenedetect-adaptive"
    assert settings.gateway == "openrouter"
    assert settings.gateway_api_key == "key"
    assert settings.route_snapshot_path == tmp_path / "routes.json"
    assert settings.recorded_fixture_path == tmp_path / "recorded.json"
    assert "ignoring environment entries" in caplog.text
    assert all(name in caplog.text for name in stale)
    assert "OPENROUTER_API_KEY" not in caplog.text


def test_empty_config_path_keeps_the_environment_as_the_configuration() -> None:
    settings = HarnessSettings.from_env({"HARNESS_CONFIG_PATH": "", "HARNESS_ENABLED": "0"})
    assert settings.config_path is None
    assert settings.enabled is False


def test_missing_or_invalid_file_fails_at_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        HarnessSettings.from_env({"HARNESS_CONFIG_PATH": str(tmp_path / "absent.json")})
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "harness-config/1", "enabled": True}))
    with pytest.raises(ValidationError):
        HarnessSettings.from_env({"HARNESS_CONFIG_PATH": str(broken)})
    mismatched = _write_config(tmp_path, routeSnapshot={"path": "routes.json", "id": "e" * 64})
    with pytest.raises(RuntimeError, match="differs from the configured snapshot ID"):
        HarnessSettings.from_env({"HARNESS_CONFIG_PATH": str(mismatched)}).validate_boot()
