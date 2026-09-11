"""Explicit output profiles bind exact original requests, never observed token counts."""

# Mock qualification records are invented test evidence and dispatch no network requests.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from temnia_pipeline.harness.qualification_topic_selection import (
    bind_topic_selection_qualification,
    validate_topic_selection_qualification,
)
from test_harness_settings import snapshot
from test_topic_qualification_halted_reports import _halted
from test_topic_selection_qualification import _qualified

if TYPE_CHECKING:
    from temnia_pipeline.harness.routes import RouteSnapshot


async def _profiles(directory: Path) -> tuple[RouteSnapshot, list[Path]]:
    reports: list[Path] = []
    for allowance in (256, 512):
        case = directory / str(allowance)
        case.mkdir()
        frozen, _, report, requests = await _qualified(case, max_output_tokens=allowance)
        assert all(request["max_completion_tokens"] == allowance for request in requests)
        assert all(call["request"]["maxCompletionTokens"] == allowance for call in report["calls"])
        reports.append(case / "report.json")
    routes = tuple(
        route.model_copy(update={"max_output_tokens": 256 if index == 0 else 512})
        for index, route in enumerate(frozen.routes)
    )
    return snapshot(routes=routes, synthetic=False), reports


async def test_explicit_profiles_bind_exact_mixed_requests_without_rewriting_reports(
    tmp_path: Path,
) -> None:
    frozen, reports = await _profiles(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    manifest = tmp_path / "profiles.json"
    bind_topic_selection_qualification(
        frozen, reports, manifest, max_output_tokens=512, per_route_output=True
    )
    validate_topic_selection_qualification(frozen, manifest, max_output_tokens=512)
    value = json.loads(manifest.read_bytes())
    assert value["format"] == "topic-selection-qualification/3"
    assert value["maxOutputTokens"] == 512
    assert value["routeMaxOutputTokens"] == {
        route.id: route.max_output_tokens for route in frozen.routes
    }
    assert {path: path.read_bytes() for path in originals} == originals
    with pytest.raises(ValueError, match="lack exact request qualification"):
        bind_topic_selection_qualification(
            frozen, reports, tmp_path / "old-uniform.json", max_output_tokens=512
        )
    assert not (tmp_path / "old-uniform.json").exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_map",
        "missing_route",
        "extra_route",
        "swapped",
        "zero",
        "boolean",
        "ceiling",
        "version",
    ],
)
async def test_profile_map_is_exact_and_explicit(tmp_path: Path, tamper: str) -> None:
    frozen, reports = await _profiles(tmp_path)
    manifest = tmp_path / "profiles.json"
    bind_topic_selection_qualification(
        frozen, reports, manifest, max_output_tokens=512, per_route_output=True
    )
    value: dict[str, Any] = json.loads(manifest.read_bytes())
    first, second = (route.id for route in frozen.routes[:2])
    if tamper == "missing_map":
        del value["routeMaxOutputTokens"]
    elif tamper == "missing_route":
        del value["routeMaxOutputTokens"][first]
    elif tamper == "extra_route":
        value["routeMaxOutputTokens"]["unselected-route"] = 512
    elif tamper == "swapped":
        value["routeMaxOutputTokens"][first], value["routeMaxOutputTokens"][second] = 512, 256
    elif tamper == "zero":
        value["routeMaxOutputTokens"][first] = 0
    elif tamper == "boolean":
        value["routeMaxOutputTokens"][first] = True
    elif tamper == "ceiling":
        value["maxOutputTokens"] = 256
    else:
        value["format"] = "topic-selection-qualification/2"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="topic qualification"):
        validate_topic_selection_qualification(frozen, manifest, max_output_tokens=512)


async def test_profile_binding_cannot_substitute_other_output_receipts(tmp_path: Path) -> None:
    frozen, reports = await _profiles(tmp_path)
    with pytest.raises(ValueError, match="lack exact request qualification"):
        bind_topic_selection_qualification(
            frozen,
            [reports[1]],
            tmp_path / "refused.json",
            max_output_tokens=512,
            per_route_output=True,
        )


async def test_profile_binding_keeps_cross_report_unknown_quarantine(tmp_path: Path) -> None:
    frozen, reports = await _profiles(tmp_path)
    _, halted_path, _ = await _halted(tmp_path / "halted", timeout_at=1)
    with pytest.raises(ValueError, match="selected model/provider has unsettled calls"):
        bind_topic_selection_qualification(
            frozen,
            [*reports, halted_path],
            tmp_path / "refused.json",
            max_output_tokens=512,
            per_route_output=True,
        )


async def test_profile_binding_refuses_changed_snapshot(tmp_path: Path) -> None:
    frozen, reports = await _profiles(tmp_path)
    manifest = tmp_path / "profiles.json"
    bind_topic_selection_qualification(
        frozen, reports, manifest, max_output_tokens=512, per_route_output=True
    )
    changed = snapshot(
        routes=(frozen.routes[0].model_copy(update={"max_output_tokens": 512}), *frozen.routes[1:]),
        synthetic=False,
    )
    with pytest.raises(ValueError, match="does not bind this production snapshot"):
        validate_topic_selection_qualification(changed, manifest, max_output_tokens=512)


async def test_profile_ceiling_cannot_exceed_original_candidate_capacity(tmp_path: Path) -> None:
    frozen, reports = await _profiles(tmp_path)
    oversized = snapshot(
        routes=(
            frozen.routes[0].model_copy(update={"max_output_tokens": 8193}),
            *frozen.routes[1:],
        ),
        synthetic=False,
    )
    # The original candidate advertises 8192. A smaller passed 512 request does
    # not establish this newly declared 8193 profile ceiling.
    with pytest.raises(ValueError, match="lack exact request qualification"):
        bind_topic_selection_qualification(
            oversized,
            reports,
            tmp_path / "refused.json",
            max_output_tokens=512,
            per_route_output=True,
        )
    assert not (tmp_path / "refused.json").exists()
    bind_topic_selection_qualification(
        oversized, reports, tmp_path / "legacy-uniform.json", max_output_tokens=512
    )


async def test_binding_cli_requires_explicit_per_route_opt_in(tmp_path: Path) -> None:
    frozen, reports = await _profiles(tmp_path)
    route_file = tmp_path / "routes.json"
    route_file.write_text(frozen.model_dump_json())
    output = tmp_path / "cli-bound.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "qualify_harness_gateway.py"),
        "bind-topics",
        "--snapshot",
        str(route_file),
        "--reports",
        *(str(path) for path in reports),
        "--output",
        str(output),
        "--max-output-tokens",
        "512",
    ]
    refused = await asyncio.to_thread(
        subprocess.run, command, capture_output=True, text=True, check=False
    )
    assert refused.returncode == 1
    assert not output.exists()
    accepted = await asyncio.to_thread(
        subprocess.run,
        [*command, "--per-route-output"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    validate_topic_selection_qualification(frozen, output, max_output_tokens=512)
