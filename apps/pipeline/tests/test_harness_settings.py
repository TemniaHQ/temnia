"""Harness boot configuration fails before accepting user work."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
)
from temnia_pipeline.harness.settings import RECORDED_TOPIC_OUTPUTS, HarnessSettings


def route(
    *,
    route_id: str = "synthetic-route",
    context_tokens: int = 20_000,
    max_output_tokens: int = 8192,
) -> RouteEntry:
    return RouteEntry(
        id=route_id,
        gateway_model=f"synthetic/{route_id}",
        family=f"synthetic-family-{route_id}",
        provider="synthetic-provider",
        open_weight=True,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 8),
        ),
        prices=RoutePrices(input=0, output=0),
    )


def snapshot(
    *,
    seats: bool = True,
    routes: tuple[RouteEntry, ...] | None = None,
    seat_route_ids: tuple[str, ...] | None = None,
    synthetic: bool = True,
) -> RouteSnapshot:
    routes = routes or (route(),)
    route_ids = seat_route_ids or tuple(item.id for item in routes)
    seat_map = (
        {name: SeatRoutePool(route_ids=route_ids) for name in ("propose", "summary", "verify")}
        if seats
        else {}
    )
    payload: dict[str, Any] = {
        "routes": [item.model_dump(mode="json") for item in routes],
        "seats": {key: value.model_dump(mode="json") for key, value in seat_map.items()},
        "synthetic": synthetic,
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return RouteSnapshot(
        version=1,
        snapshot_id=digest,
        routes=routes,
        seats=seat_map,
        synthetic=synthetic,
    )


def write_snapshot(path: Path, value: RouteSnapshot) -> None:
    path.write_text(value.model_dump_json())


def env(path: Path, value: RouteSnapshot, backend: str) -> dict[str, str]:
    recorded = path.parent / "recorded.json"
    recorded.write_text(
        json.dumps(
            {
                "outputs": {
                    stage: {"output": {}, "synthetic": True} for stage in RECORDED_TOPIC_OUTPUTS
                },
                "synthetic": True,
            }
        )
    )
    return {
        "AI_GATEWAY_API_KEY": "test-key",
        "HARNESS_ALLOW_RECORDED": "1",
        "HARNESS_BACKEND": backend,
        "HARNESS_ENABLED": "1",
        "HARNESS_RECORDED_FIXTURE_PATH": str(recorded),
        "HARNESS_ROUTE_SNAPSHOT_ID": value.snapshot_id,
        "HARNESS_ROUTE_SNAPSHOT_PATH": str(path),
    }


def test_topic_scene_detection_defaults_to_scdet_and_keeps_pyscenedetect_comparison() -> None:
    assert HarnessSettings.from_env({}).topic_shot_detector == "scdet"
    assert (
        HarnessSettings.from_env(
            {"HARNESS_TOPIC_SHOT_DETECTOR": "pyscenedetect-adaptive"}
        ).topic_shot_detector
        == "pyscenedetect-adaptive"
    )
    for invalid in ("", "auto", "opencv", "pyscenedetect"):
        with pytest.raises(ValueError, match="HARNESS_TOPIC_SHOT_DETECTOR"):
            HarnessSettings.from_env({"HARNESS_TOPIC_SHOT_DETECTOR": invalid})


def test_disabled_harness_does_not_require_snapshot() -> None:
    assert HarnessSettings.from_env({}).validate_boot() is None


def test_gateway_refuses_synthetic_snapshot(tmp_path: Path) -> None:
    value = snapshot()
    path = tmp_path / "routes.json"
    write_snapshot(path, value)
    with pytest.raises(RuntimeError, match="production route snapshot"):
        HarnessSettings.from_env(env(path, value, "gateway")).validate_boot()


def test_enabled_snapshot_requires_every_workflow_seat(tmp_path: Path) -> None:
    value = snapshot(seats=False)
    path = tmp_path / "routes.json"
    write_snapshot(path, value)
    with pytest.raises(RuntimeError, match="propose, summary, verify"):
        HarnessSettings.from_env(env(path, value, "recorded")).validate_boot()


@pytest.mark.parametrize(
    "routes",
    [
        (route(route_id="primary", max_output_tokens=4096), route(route_id="failover")),
        (route(route_id="primary"), route(route_id="failover", max_output_tokens=4096)),
    ],
)
def test_route_below_the_ceiling_boots_under_its_own_effective_output_cap(
    tmp_path: Path,
    routes: tuple[RouteEntry, ...],
) -> None:
    # Every route is admitted at min(configured ceiling, route maximum); there is no
    # per-programme asymmetry left in the boot check.
    value = snapshot(routes=routes)
    path = tmp_path / "routes.json"
    write_snapshot(path, value)

    assert HarnessSettings.from_env(env(path, value, "recorded")).validate_boot() == value


def test_gateway_boot_still_refuses_a_route_without_effective_context_headroom(
    tmp_path: Path,
) -> None:
    value = snapshot(
        routes=(
            route(route_id="primary"),
            route(route_id="failover", context_tokens=4096, max_output_tokens=4096),
            route(route_id="third"),
        ),
        synthetic=False,
    )
    path = tmp_path / "routes.json"
    write_snapshot(path, value)

    with pytest.raises(
        RuntimeError,
        match=r"route 'failover'.*HARNESS_MAX_OUTPUT_TOKENS=8192",
    ):
        HarnessSettings.from_env(env(path, value, "gateway")).validate_boot()


def test_required_route_needs_headroom_for_a_nonempty_request(tmp_path: Path) -> None:
    value = snapshot(routes=(route(context_tokens=16_384),))
    path = tmp_path / "routes.json"
    write_snapshot(path, value)

    with pytest.raises(
        RuntimeError,
        match=r"route 'synthetic-route'.*HARNESS_MAX_OUTPUT_TOKENS=8192",
    ):
        HarnessSettings.from_env(env(path, value, "recorded")).validate_boot()


def test_exact_minimum_route_capacity_is_valid(tmp_path: Path) -> None:
    value = snapshot(
        routes=(
            route(context_tokens=16_385),
            route(route_id="unreferenced", max_output_tokens=4096),
        ),
        seat_route_ids=("synthetic-route",),
    )
    path = tmp_path / "routes.json"
    write_snapshot(path, value)

    assert HarnessSettings.from_env(env(path, value, "recorded")).validate_boot() == value


def test_committed_recorded_snapshot_has_valid_capacity() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "harness"
    route_path = fixture_dir / "routes.synthetic.json"
    value = json.loads(route_path.read_text())
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(fixture_dir / "topic.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": value["snapshot_id"],
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(route_path),
        }
    )

    assert settings.validate_boot() is not None
