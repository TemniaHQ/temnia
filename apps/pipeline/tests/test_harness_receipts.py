"""Unknown outcomes settle from gateway receipts through the ledger, without a human.

Every case runs against Postgres: the attempt is reserved, dispatched, given its
generation handle and left `outcome_unknown` exactly as the budgeted model leaves it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.harness import ledger, receipts, runs
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V8
from temnia_pipeline.harness.gateway import CostObservation, GatewayConfig
from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    SeatRoutePool,
)
from temnia_pipeline.harness.run_failures import RECONCILED_RUN_MESSAGE
from temnia_pipeline.harness.runtime_types import RunRef, WorkflowIdentity
from test_harness_model_transport import snapshot
from test_harness_persistence import HASH, OWNER, Case, make_case, operation
from test_harness_runs import SEEDED, pipeline_url, settings

if TYPE_CHECKING:
    import uuid

ROUTE = RouteEntry(
    id="receipt-route",
    gateway_model="family-receipt/model",
    family="family-receipt",
    provider="fixture-provider",
    open_weight=True,
    context_tokens=200_000,
    max_output_tokens=16_384,
    eligibility=RouteEligibility(
        zero_data_retention=True,
        strict_json_schema=True,
        probe_artifact_sha256="a" * 64,
        probed_at=date(2026, 9, 11),
    ),
    prices=RoutePrices(input=1_000_000, output=2_000_000),
)
CONFIG = GatewayConfig(api_key="test-key")
WORKFLOW = WorkflowIdentity(workflow_id="wf-receipts", workflow_run_id="run-1")


def _reported(cost: int) -> Any:  # noqa: ANN401
    async def lookup(*_args: object, **kwargs: object) -> CostObservation:
        return CostObservation(
            status="reported",
            actual_cost_micros=cost,
            components={"generationId": str(kwargs["generation_id"]), "totalCost": "0.000007"},
        )

    return lookup


async def _absent(*_args: object, **kwargs: object) -> CostObservation:
    return CostObservation(
        status="pending",
        actual_cost_micros=None,
        components={"generationId": str(kwargs["generation_id"]), "reason": "usage_not_found"},
    )


async def _recorded_without_cost(*_args: object, **kwargs: object) -> CostObservation:
    return CostObservation(
        status="pending",
        actual_cost_micros=None,
        components={"generationId": str(kwargs["generation_id"]), "totalCost": None},
    )


async def _unknown_attempt(
    url: str, case: Case, *, handle: str | None = "gen-dropped"
) -> tuple[ledger.Operation, ledger.Attempt]:
    """Reserve, dispatch and lose one stream, leaving the run fenced."""
    logical = await operation(url, case, "verify:selection")
    attempt = await ledger.reserve_attempt(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        operation_id=logical.id,
        owner_token=OWNER,
        provider=ROUTE.provider,
        model=ROUTE.gateway_model,
        family=ROUTE.family,
        route=ROUTE.model_dump(mode="json"),
        request_hash=HASH,
        estimated_cost_micros=40,
        dispatch_limit=5,
    )
    await ledger.mark_dispatched(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        operation_id=logical.id,
        attempt_id=attempt.id,
        owner_token=OWNER,
        dispatch_limit=5,
    )
    if handle is not None:
        await ledger.attach_remote_handle(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            remote_handle=handle,
        )
    await ledger.fail_attempt(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        operation_id=logical.id,
        attempt_id=attempt.id,
        owner_token=OWNER,
        outcome_known=False,
        actual_cost_micros=None,
        usage={},
        error_code="transport-ambiguous",
        error_message="provider transport ended without a conclusive outcome",
    )
    return logical, attempt


async def _run_row(url: str, case: Case) -> dict[str, Any]:
    async with db.scoped(url, SEEDED) as conn:
        row = await (
            await conn.execute(
                "SELECT status, spent_micros, reserved_micros, error_message"
                " FROM harness_run WHERE id = %s",
                (case.run_id,),
            )
        ).fetchone()
    assert row is not None
    return dict(row)


async def _attempt_row(url: str, attempt_id: uuid.UUID) -> dict[str, Any]:
    async with db.scoped(url, SEEDED) as conn:
        row = await (
            await conn.execute(
                "SELECT state, cost_status, actual_cost_micros, error_code, usage"
                " FROM harness_attempt WHERE id = %s",
                (attempt_id,),
            )
        ).fetchone()
    assert row is not None
    return dict(row)


async def _recover(url: str, case: Case, lookup: Any, **kwargs: Any) -> receipts.RecoveryReport:  # noqa: ANN401
    async with httpx.AsyncClient() as client:
        return await receipts.recover_unknown_attempts(
            url,
            scope=SEEDED,
            run_id=case.run_id,
            config=CONFIG,
            client=client,
            lookup=lookup,
            **kwargs,
        )


async def test_a_reported_receipt_settles_the_charge_and_lifts_the_fence() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical, attempt = await _unknown_attempt(url, case)
        assert (await _run_row(url, case))["status"] == "outcome_unknown"

        report = await _recover(url, case, _reported(7))
        assert report == receipts.RecoveryReport(looked_up=1, settled=1)
        row = await _attempt_row(url, attempt.id)
        assert row["state"] == "failed_known"
        assert row["cost_status"] == "reported"
        assert row["actual_cost_micros"] == 7
        assert row["error_code"] == "provider-stream-failure"
        run = await _run_row(url, case)
        assert run["status"] == "running"
        assert (run["spent_micros"], run["reserved_micros"]) == (7, 0)
        assert run["error_message"] is None
        # The operation may take a fresh attempt now.
        fresh = await ledger.reserve_attempt(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            owner_token=OWNER,
            provider=ROUTE.provider,
            model=ROUTE.gateway_model,
            family=ROUTE.family,
            route=ROUTE.model_dump(mode="json"),
            request_hash=HASH,
            estimated_cost_micros=40,
            dispatch_limit=5,
        )
        assert fresh.id != attempt.id
        # A second pass finds nothing to do.
        assert await _recover(url, case, _reported(7)) == receipts.RecoveryReport()
    finally:
        await db.close_pool()


async def test_an_absent_receipt_keeps_the_fence_inside_the_grace_then_releases_at_zero() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        _, attempt = await _unknown_attempt(url, case)
        early = await _recover(url, case, _absent)
        assert early == receipts.RecoveryReport(looked_up=1, pending=1)
        assert (await _run_row(url, case))["status"] == "outcome_unknown"

        late = await _recover(url, case, _absent, grace_seconds=0.0)
        assert late == receipts.RecoveryReport(looked_up=1, released=1)
        row = await _attempt_row(url, attempt.id)
        assert row["state"] == "failed_known"
        assert row["actual_cost_micros"] == 0
        assert row["error_code"] == "provider-receipt-absent"
        assert row["usage"]["settled"] == "receipt-absent"
        run = await _run_row(url, case)
        assert run["status"] == "running"
        assert (run["spent_micros"], run["reserved_micros"]) == (0, 0)
    finally:
        await db.close_pool()


async def test_a_recorded_receipt_without_a_cost_keeps_the_fence_past_the_grace() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        await _unknown_attempt(url, case)
        report = await _recover(url, case, _recorded_without_cost, grace_seconds=0.0)
        assert report == receipts.RecoveryReport(looked_up=1, pending=1)
        assert (await _run_row(url, case))["status"] == "outcome_unknown"
    finally:
        await db.close_pool()


async def test_an_attempt_without_a_handle_is_unresolvable_and_stays_fenced() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        await _unknown_attempt(url, case, handle=None)
        report = await _recover(url, case, _reported(7), grace_seconds=0.0)
        assert report == receipts.RecoveryReport(unresolvable=1)
        assert (await _run_row(url, case))["status"] == "outcome_unknown"
    finally:
        await db.close_pool()


async def _claim(url: str, case: Case, workflow: WorkflowIdentity) -> None:
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            "UPDATE harness_run SET workflow_id = %s, workflow_run_id = %s,"
            " route_snapshot = %s::jsonb WHERE id = %s",
            (
                workflow.workflow_id,
                workflow.workflow_run_id,
                f'{{"editorialPolicy": "{TOPIC_SELECTION_POLICY_V8}"}}',
                case.run_id,
            ),
        )


def _ref(case: Case) -> RunRef:
    return RunRef(
        scope_organization_id=SEEDED.organizationId,
        scope_user_id=SEEDED.userId,
        source_id=case.source_id,
        run_id=case.run_id,
    )


async def test_parking_needs_a_clear_ledger_and_the_ending_execution_identity() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        await _claim(url, case, WORKFLOW)
        await _unknown_attempt(url, case)
        # Still unknown: nothing moves.
        assert not await runs.park_reconciled_run(
            url, run=_ref(case), workflow=WORKFLOW, message=RECONCILED_RUN_MESSAGE
        )
        await _recover(url, case, _reported(7))
        assert (await _run_row(url, case))["status"] == "running"
        # Another execution's identity never parks the run it does not own.
        other = WorkflowIdentity(workflow_id="wf-receipts", workflow_run_id="run-2")
        assert not await runs.park_reconciled_run(
            url, run=_ref(case), workflow=other, message=RECONCILED_RUN_MESSAGE
        )
        assert (await _run_row(url, case))["status"] == "running"
        assert await runs.park_reconciled_run(
            url, run=_ref(case), workflow=WORKFLOW, message=RECONCILED_RUN_MESSAGE
        )
        run = await _run_row(url, case)
        assert run["status"] == "failed"
        assert run["error_message"] == RECONCILED_RUN_MESSAGE
    finally:
        await db.close_pool()


def _owner(execution_open: Any) -> HarnessActivities:  # noqa: ANN401
    routes = snapshot((ROUTE,), {"verify": SeatRoutePool(route_ids=(ROUTE.id,))})
    configuration = replace(settings(routes), backend="gateway", gateway_api_key="test-key")
    ctx = SimpleNamespace(settings=SimpleNamespace(database_url=pipeline_url()))
    return HarnessActivities(cast("Any", ctx), configuration, routes, execution_open=execution_open)


async def test_the_reaper_settles_a_fenced_run_whose_execution_has_ended(
    monkeypatch: Any,  # noqa: ANN401
) -> None:
    url = pipeline_url()
    case = await make_case(url)
    seen: list[WorkflowIdentity] = []

    async def closed(identity: WorkflowIdentity) -> bool:
        seen.append(identity)
        return False

    monkeypatch.setattr(receipts, "lookup_generation", _reported(9))
    try:
        await _claim(url, case, WORKFLOW)
        _, attempt = await _unknown_attempt(url, case)
        result = await _owner(closed).reconcile_unknown_runs()
        assert result.runs >= 1
        assert result.settled >= 1
        assert result.parked >= 1
        assert WORKFLOW in seen
        assert (await _attempt_row(url, attempt.id))["state"] == "failed_known"
        run = await _run_row(url, case)
        assert run["status"] == "failed"
        assert run["error_message"] == RECONCILED_RUN_MESSAGE
        assert (run["spent_micros"], run["reserved_micros"]) == (9, 0)
    finally:
        await db.close_pool()


async def test_the_reaper_leaves_a_run_alone_while_its_execution_is_running(
    monkeypatch: Any,  # noqa: ANN401
) -> None:
    url = pipeline_url()
    case = await make_case(url)

    async def running(identity: WorkflowIdentity) -> bool:
        return identity == WORKFLOW

    monkeypatch.setattr(receipts, "lookup_generation", _reported(9))
    try:
        await _claim(url, case, WORKFLOW)
        _, attempt = await _unknown_attempt(url, case)
        result = await _owner(running).reconcile_unknown_runs()
        assert result.skipped_live >= 1
        assert (await _attempt_row(url, attempt.id))["state"] == "outcome_unknown"
        assert (await _run_row(url, case))["status"] == "outcome_unknown"
    finally:
        await db.close_pool()


def test_the_sweep_is_registered_on_the_pipeline_queue() -> None:
    names = {
        activity._Definition.must_from_callable(item).name  # noqa: SLF001 # pyright: ignore[reportUnknownMemberType, reportPrivateUsage]
        for item in _owner(None).activities()
    }
    assert "reconcile_unknown_runs" in names
