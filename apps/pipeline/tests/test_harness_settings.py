"""Harness boot configuration fails before accepting user work."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
)
from temnia_pipeline.harness.settings import HarnessSettings

if TYPE_CHECKING:
    from pathlib import Path


def route() -> RouteEntry:
    return RouteEntry(
        id="synthetic-route",
        gateway_model="synthetic/model",
        family="synthetic-family",
        provider="synthetic-provider",
        open_weight=True,
        context_tokens=20_000,
        max_output_tokens=8192,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 8),
        ),
        prices=RoutePrices(input=0, output=0),
    )


def snapshot(*, seats: bool = True) -> RouteSnapshot:
    routes = (route(),)
    seat_map = (
        {
            name: SeatRoutePool(route_ids=("synthetic-route",))
            for name in ("propose", "summary", "verify")
        }
        if seats
        else {}
    )
    payload: dict[str, Any] = {
        "routes": [item.model_dump(mode="json") for item in routes],
        "seats": {key: value.model_dump(mode="json") for key, value in seat_map.items()},
        "synthetic": True,
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
        synthetic=True,
    )


def write_snapshot(path: Path, value: RouteSnapshot) -> None:
    path.write_text(value.model_dump_json())


def env(path: Path, value: RouteSnapshot, backend: str) -> dict[str, str]:
    recorded = path.parent / "recorded.json"
    recorded.write_text(
        json.dumps(
            {
                "outputs": {
                    stage: {"output": {}, "synthetic": True}
                    for stage in ("propose", "summary", "verify")
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
