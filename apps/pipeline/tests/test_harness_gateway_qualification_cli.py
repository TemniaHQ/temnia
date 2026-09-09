"""CLI and candidate-file boundaries for the finite gateway qualification."""

from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from temnia_pipeline.harness.qualification import (
    MAX_DISPATCHES,
    MAX_EXPOSURE_MICROS,
    MAX_OUTPUT_TOKENS,
    QualificationLimits,
    QualificationRefusal,
    load_candidates,
)

SCRIPT = Path(__file__).parents[1] / "scripts/qualify_harness_gateway.py"
SPEC = importlib.util.spec_from_file_location("qualify_harness_gateway", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - broken test installation
    message = "could not load gateway qualification driver"
    raise RuntimeError(message)
driver = cast("Any", importlib.util.module_from_spec(SPEC))
SPEC.loader.exec_module(driver)


def _candidate_catalogue() -> dict[str, object]:
    """Return the reviewed public-metadata wire shape, without private file dependencies."""
    return {
        "version": 1,
        "catalogueObservedAt": "2026-09-09T09:51:28Z",
        "catalogueSha256": "a" * 64,
        "candidates": [
            {
                "id": "deepseek-v4-flash-deepinfra",
                "gatewayModel": "deepseek/deepseek-v4-flash",
                "family": "deepseek",
                "provider": "deepinfra",
                "openWeight": False,
                "contextTokens": 1_048_576,
                "maxOutputTokens": 1_048_576,
                "zdrClaim": True,
                "prices": {
                    "unit": "micros_per_million_tokens",
                    "input": 80_000,
                    "output": 180_000,
                    "cacheRead": None,
                    "cacheWrite": None,
                    "requestSurcharge": 0,
                },
            }
        ],
    }


def _write_catalogue(tmp_path: Path, value: object | None = None) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(_candidate_catalogue() if value is None else value))
    return path


def _run_arguments(tmp_path: Path, candidates: Path) -> list[str]:
    return [
        "run",
        "--candidates",
        str(candidates),
        "--journal",
        str(tmp_path / "journal.json"),
        "--receipts",
        str(tmp_path / "receipts"),
        "--report",
        str(tmp_path / "report.json"),
        "--max-exposure-micros",
        str(MAX_EXPOSURE_MICROS),
        "--max-dispatches",
        str(MAX_DISPATCHES),
        "--max-output-tokens",
        str(MAX_OUTPUT_TOKENS),
    ]


async def _unexpected_dispatch(**_kwargs: object) -> dict[str, object]:
    pytest.fail("qualification unexpectedly dispatched")


def test_help_needs_no_credential_and_cannot_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(
        driver,
        "run_qualification",
        _unexpected_dispatch,
    )

    with pytest.raises(SystemExit) as raised:
        driver.main(["--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "run" in help_text
    assert "reconcile" in help_text


def test_missing_credential_refuses_before_read_or_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(
        driver,
        "run_qualification",
        _unexpected_dispatch,
    )

    with pytest.raises(SystemExit) as raised:
        driver.main(_run_arguments(tmp_path, tmp_path / "missing.json"))

    assert raised.value.code == 1
    error = capsys.readouterr().err
    assert error == (
        "qualify-harness-gateway: input or qualification refused "
        "(QualificationRefusal); inspect the private report when present\n"
    )


def test_real_public_candidate_shape_loads_strict_timestamp_and_tuple(tmp_path: Path) -> None:
    path = _write_catalogue(tmp_path)

    catalogue = load_candidates(path)

    assert isinstance(catalogue.candidates, tuple)
    assert catalogue.catalogue_observed_at.utcoffset() == timedelta(0)
    assert catalogue.catalogue_observed_at.isoformat() == "2026-09-09T09:51:28+00:00"
    assert catalogue.candidates[0].gateway_model == "deepseek/deepseek-v4-flash"
    assert catalogue.candidates[0].prices.input == 80_000


@pytest.mark.parametrize(
    ("invalid_kind", "sensitive_value"),
    [
        ("timestamp", "private-invalid-timestamp"),
        ("credential", "private-credential-value"),
        ("candidate_id", "../../private-candidate"),
    ],
)
def test_invalid_candidate_input_is_sanitized_and_never_dispatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_kind: str,
    sensitive_value: str,
) -> None:
    value = _candidate_catalogue()
    if invalid_kind == "timestamp":
        value["catalogueObservedAt"] = sensitive_value
    elif invalid_kind == "credential":
        value["authorization"] = sensitive_value
    else:
        candidates_value = cast("list[dict[str, object]]", value["candidates"])
        candidates_value[0]["id"] = sensitive_value
    candidates = _write_catalogue(tmp_path, value)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "private-test-key")

    with pytest.raises(SystemExit) as raised:
        driver.main(_run_arguments(tmp_path, candidates))

    assert raised.value.code == 1
    error = capsys.readouterr().err
    assert "input_value" not in error
    assert sensitive_value not in error
    assert "private-test-key" not in error
    assert not (tmp_path / "journal.json").exists()
    assert not (tmp_path / "report.json").exists()


def test_run_parser_requires_all_three_operator_caps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidates = _write_catalogue(tmp_path)

    with pytest.raises(SystemExit) as raised:
        driver.main(
            [
                "run",
                "--candidates",
                str(candidates),
                "--journal",
                str(tmp_path / "journal.json"),
                "--receipts",
                str(tmp_path / "receipts"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "--max-exposure-micros" in error
    assert "--max-dispatches" in error
    assert "--max-output-tokens" in error


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--max-exposure-micros", MAX_EXPOSURE_MICROS + 1),
        ("--max-dispatches", MAX_DISPATCHES + 1),
        ("--max-output-tokens", MAX_OUTPUT_TOKENS + 1),
    ],
)
def test_operator_caps_cannot_exceed_reviewed_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: int,
) -> None:
    candidates = _write_catalogue(tmp_path)
    arguments = _run_arguments(tmp_path, candidates)
    arguments[arguments.index(flag) + 1] = str(value)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "private-test-key")

    async def refuse_dispatch(**_kwargs: object) -> dict[str, object]:
        pytest.fail("an over-cap qualification dispatched")

    monkeypatch.setattr(driver, "run_qualification", refuse_dispatch)

    with pytest.raises(SystemExit) as raised:
        driver.main(arguments)

    assert raised.value.code == 1
    error = capsys.readouterr().err
    assert "input_value" not in error
    assert "private-test-key" not in error


def test_exact_reviewed_caps_reach_runner_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = _write_catalogue(tmp_path)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "private-test-key")
    observed: dict[str, object] = {}

    async def complete(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"status": "completed", "passed": True}

    monkeypatch.setattr(driver, "run_qualification", complete)

    assert driver.main(_run_arguments(tmp_path, candidates)) == 0
    limits = observed["limits"]
    assert isinstance(limits, QualificationLimits)
    assert limits.max_exposure_micros == MAX_EXPOSURE_MICROS
    assert limits.max_dispatches == MAX_DISPATCHES
    assert limits.max_output_tokens == MAX_OUTPUT_TOKENS


async def test_output_paths_must_be_distinct_before_journal_or_transport(
    tmp_path: Path,
) -> None:
    candidates_path = _write_catalogue(tmp_path)
    shared = tmp_path / "shared"

    with pytest.raises(QualificationRefusal, match="paths must be distinct"):
        await driver.run_qualification(
            candidate_path=candidates_path,
            api_key="private-test-key",
            journal_path=shared,
            receipts_path=shared,
            report_path=tmp_path / "report.json",
            limits=QualificationLimits(
                max_exposure_micros=MAX_EXPOSURE_MICROS,
                max_dispatches=MAX_DISPATCHES,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )

    assert not shared.exists()
    assert not (tmp_path / "report.json").exists()
