"""Fail-closed guards and report encoding for live speech qualification."""

# Operator-facing refusal messages state the violated qualification invariant.
# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from temnia_pipeline.settings import (
    DEFAULT_SPEECH_BUDGET_MICROS,
    DEFAULT_SPEECH_DISPATCH_LIMIT,
    DEFAULT_SPEECH_STAGE_TIMEOUT_SECONDS,
    DEFAULT_SPEECH_STARTUP_TIMEOUT_SECONDS,
    TranscriptionSettings,
)

QUALIFICATION_DATABASE_MARKER = "speech_qualification"
EXPECTED_STAGES = {"recognize", "align", "diarize"}
TERMINAL_ATTEMPTS = {"succeeded", "failed_known", "cancelled_confirmed"}


def _retained_cost_exposure(attempt: Mapping[str, object]) -> int:
    """Terminal but unpriced work still consumes its original budget reservation."""
    amount = attempt.get("reservation_amount_micros")
    state = attempt.get("reservation_state")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValueError("qualification reservation amount is absent or invalid")
    if state == "active":
        if attempt.get("cost_status") != "unknown" or attempt.get("actual_cost_micros") is not None:
            raise ValueError("terminal active reservation lacks unknown-cost provenance")
        return amount
    if state not in ("settled", "released"):
        raise ValueError("qualification reservation state is absent or invalid")
    return 0


def _completed_dispatches(attempts: list[object]) -> tuple[int, int]:
    """Check every physical attempt, including failures before a successful retry."""
    completed_stages: set[str] = set()
    dispatches = 0
    retained_cost = 0
    for item in attempts:
        if not isinstance(item, dict):
            raise TypeError("qualification attempt is invalid")
        attempt = cast("dict[str, object]", item)
        state = attempt.get("state")
        if not isinstance(state, str) or state not in TERMINAL_ATTEMPTS:
            raise ValueError("qualification attempt has an unresolved execution outcome")
        retained_cost += _retained_cost_exposure(attempt)
        if attempt.get("dispatched_at") is not None:
            dispatches += 1
        if attempt.get("state") == "succeeded":
            stage = attempt.get("stage")
            if not isinstance(stage, str) or attempt.get("result_artifact_id") is None:
                raise ValueError("successful qualification attempt lacks accepted evidence")
            completed_stages.add(stage)
    if completed_stages != EXPECTED_STAGES:
        raise ValueError("qualification stage evidence is incomplete")
    return dispatches, retained_cost


def assert_qualification_completed(
    report: Mapping[str, object], *, require_lost_result: bool = False
) -> None:
    """Retain diagnostic objects unless successful completion and recovery are proven."""
    ledger = report.get("ledger")
    workflow = report.get("workflow")
    if not isinstance(ledger, dict) or not isinstance(workflow, dict):
        raise TypeError("qualification report lacks ledger/workflow facts")
    ledger_values = cast("dict[str, object]", ledger)
    workflow_values = cast("dict[str, object]", workflow)
    run = ledger_values.get("run")
    attempts = ledger_values.get("attempts")
    if not isinstance(run, dict) or not isinstance(attempts, list) or not attempts:
        raise ValueError("qualification report lacks run/attempt facts")
    run_values = cast("dict[str, object]", run)
    if run_values.get("status") != "ready":
        raise ValueError("qualification run is not complete")
    dispatches, retained_cost = _completed_dispatches(cast("list[object]", attempts))
    if run_values.get("reserved_micros") != retained_cost:
        raise ValueError("qualification report does not account for every reserved cost")
    if run_values.get("dispatch_count") != dispatches:
        raise ValueError("qualification stage/physical-dispatch evidence is incomplete")
    if require_lost_result and (
        workflow_values.get("injectedLostActivityResult") is not True
        or workflow_values.get("checkpointedActivityAttempts") != [1, 2]
        or workflow_values.get("observedPhysicalDispatchCounts") != [dispatches, dispatches]
    ):
        raise ValueError("lost-result recovery did not prove zero additional dispatches")


def assert_isolated_database(database_url: str) -> None:
    """Require an explicitly disposable database for the qualification."""
    database_name = database_url.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if QUALIFICATION_DATABASE_MARKER not in database_name:
        raise ValueError("PIPELINE_DATABASE_URL database name must contain 'speech_qualification'")


def assert_qualification_limits(settings: TranscriptionSettings) -> None:
    """Allow stricter exposure caps without widening the reviewed deadlines."""
    if not (
        0 < settings.speech_budget_micros <= DEFAULT_SPEECH_BUDGET_MICROS
        and 0 < settings.speech_dispatch_limit <= DEFAULT_SPEECH_DISPATCH_LIMIT
        and settings.speech_stage_timeout_seconds == DEFAULT_SPEECH_STAGE_TIMEOUT_SECONDS
        and settings.speech_startup_timeout_seconds == DEFAULT_SPEECH_STARTUP_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "qualification requires at most the reviewed 6.5m budget and five dispatches, "
            "positive caps, and the exact 3600s stage and 120s startup limits"
        )


def wire_report(value: object) -> object:
    """Convert known database value types to finite JSON without a permissive fallback."""
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("qualification report contains a nonfinite float")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("qualification report contains a nonfinite decimal")
        return str(value)
    if isinstance(value, UUID | datetime):
        return str(value)
    if isinstance(value, Mapping):
        values = cast("Mapping[object, object]", value)
        return {str(key): wire_report(item) for key, item in values.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [wire_report(item) for item in cast("Sequence[object]", value)]
    raise TypeError(f"qualification report contains unsupported {type(value).__name__}")
