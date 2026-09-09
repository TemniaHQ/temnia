"""Offline guards around the live checkpointed speech qualification driver."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

import pytest

from temnia_pipeline.settings import TranscriptionSettings
from temnia_pipeline.speech.qualification import (
    assert_isolated_database,
    assert_qualification_completed,
    assert_qualification_limits,
    wire_report,
)


def test_qualification_requires_a_dedicated_database() -> None:
    assert_isolated_database("postgresql://temnia@localhost/temnia_speech_qualification_short")
    with pytest.raises(ValueError, match="speech_qualification"):
        assert_isolated_database("postgresql://temnia@localhost/temnia")


def test_qualification_requires_the_reviewed_exposure_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "modal-checkpointed")
    monkeypatch.setenv("MODAL_SPEECH_BUILD", "a" * 64)
    monkeypatch.setenv("SPEECH_RUN_BUDGET_MICROS", "6500000")
    monkeypatch.setenv("SPEECH_DISPATCH_LIMIT", "5")
    monkeypatch.setenv("SPEECH_STAGE_TIMEOUT_SECONDS", "3600")
    monkeypatch.setenv("SPEECH_STARTUP_TIMEOUT_SECONDS", "120")
    settings = TranscriptionSettings.from_env()
    assert_qualification_limits(settings)
    with pytest.raises(ValueError, match=r"reviewed 6\.5m budget"):
        assert_qualification_limits(replace(settings, speech_budget_micros=6_500_001))
    assert_qualification_limits(
        replace(settings, speech_budget_micros=3_875_001, speech_dispatch_limit=3)
    )
    assert_qualification_limits(
        replace(settings, speech_budget_micros=5_166_668, speech_dispatch_limit=4)
    )
    for changed in (
        replace(settings, speech_budget_micros=0),
        replace(settings, speech_dispatch_limit=0),
        replace(settings, speech_dispatch_limit=6),
        replace(settings, speech_stage_timeout_seconds=3599),
        replace(settings, speech_stage_timeout_seconds=3601),
        replace(settings, speech_startup_timeout_seconds=121),
    ):
        with pytest.raises(ValueError, match="qualification requires"):
            assert_qualification_limits(changed)


def test_report_wire_is_finite() -> None:
    assert wire_report({"cost": Decimal("1.25"), "items": [1, None]}) == {
        "cost": "1.25",
        "items": [1, None],
    }
    with pytest.raises(ValueError, match="nonfinite"):
        wire_report({"cost": float("nan")})


def _completed_report() -> dict[str, object]:
    return {
        "ledger": {
            "run": {"status": "ready", "reserved_micros": 0, "dispatch_count": 3},
            "attempts": [
                {
                    "state": "succeeded",
                    "stage": stage,
                    "dispatched_at": "2026-09-08T00:00:00Z",
                    "result_artifact_id": f"artifact-{stage}",
                    "reservation_state": "settled",
                    "reservation_amount_micros": 1000,
                    "cost_status": "reported",
                    "actual_cost_micros": 100,
                }
                for stage in ("recognize", "align", "diarize")
            ],
        },
        "workflow": {
            "injectedLostActivityResult": True,
            "checkpointedActivityAttempts": [1, 2],
            "observedPhysicalDispatchCounts": [3, 3],
        },
    }


def test_completed_qualification_proves_recovery_before_cleanup() -> None:
    assert_qualification_completed(_completed_report(), require_lost_result=True)


def test_known_oom_before_success_remains_counted_in_recovery() -> None:
    report = cast("dict[str, Any]", _completed_report())
    report["ledger"]["attempts"].insert(
        0,
        {
            "state": "failed_known",
            "stage": "recognize",
            "dispatched_at": "2026-09-08T00:00:00Z",
            "result_artifact_id": None,
            "reservation_state": "settled",
            "reservation_amount_micros": 1000,
            "cost_status": "reported",
            "actual_cost_micros": 100,
        },
    )
    report["ledger"]["run"]["dispatch_count"] = 4
    report["workflow"]["observedPhysicalDispatchCounts"] = [4, 4]
    assert_qualification_completed(report, require_lost_result=True)


def test_terminal_unpriced_compute_keeps_its_budget_exposure() -> None:
    report = cast("dict[str, Any]", _completed_report())
    report["ledger"]["run"]["reserved_micros"] = 3000
    for attempt in report["ledger"]["attempts"]:
        attempt.update(reservation_state="active", cost_status="unknown", actual_cost_micros=None)
    assert_qualification_completed(report, require_lost_result=True)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("ledger", "run", "status"), "pending", "not complete"),
        (("ledger", "run", "reserved_micros"), 1, "every reserved cost"),
        (("ledger", "run", "dispatch_count"), 4, "physical-dispatch"),
        (("ledger", "attempts", 0, "state"), "outcome_unknown", "unresolved execution"),
        (("ledger", "attempts", 0, "state"), "cancel_requested", "unresolved execution"),
        (("ledger", "attempts", 0, "result_artifact_id"), None, "accepted evidence"),
        (("ledger", "attempts", 0, "stage"), "align", "stage evidence"),
        (("ledger", "attempts", 0, "reservation_state"), None, "reservation state"),
        (("workflow", "observedPhysicalDispatchCounts"), [2, 3], "zero additional"),
        (("workflow", "checkpointedActivityAttempts"), [1], "zero additional"),
        (("workflow", "injectedLostActivityResult"), False, "zero additional"),
    ],
)
def test_incomplete_qualification_preserves_diagnostic_objects(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    report = deepcopy(_completed_report())
    target: Any = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        assert_qualification_completed(report, require_lost_result=True)
