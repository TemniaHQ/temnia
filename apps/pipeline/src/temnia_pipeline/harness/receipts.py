"""Settle unconfirmed provider outcomes from gateway receipts, without a human.

A model call whose stream ends without a conclusive outcome leaves its attempt
`outcome_unknown`, its reservation retained and the run fenced. The gateway's receipt
is the only fact that can settle it, and the ledger already has the owned recovery
path (`fail_attempt(..., reconcile_unknown=True)`); this module drives it:

- a reported charge settles the attempt as a known failure with that charge;
- a generation the gateway does not know after the grace period settles at zero,
  because the gateway bills only the generations it recorded (a dropped stream the
  gateway never logged cannot be charged later);
- a receipt that exists but carries no cost yet keeps the fence and is asked again.

The decision activity waits on this for a bounded window before it stops; the reaper
sweeps fenced runs whose execution has finished, so no run waits for a person.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID  # noqa: TC003  (pydantic resolves the field type at import)

import httpx
from pydantic import BaseModel, ConfigDict

from temnia_pipeline import db
from temnia_pipeline.harness import ledger
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.gateway import (
    CostObservation,
    GatewayConfig,
    GatewayError,
    lookup_generation,
)
from temnia_pipeline.harness.routes import RouteEntry
from temnia_pipeline.harness.runtime_types import WorkflowIdentity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from temnia_pipeline.contracts import Scope

log = logging.getLogger("temnia.harness.receipts")

# How long a generation the gateway does not know may still appear in its records.
# Successful calls are looked up right after the stream and report at once; the
# first staging dropped stream was still unknown nineteen minutes later.
RECEIPT_GRACE_SECONDS = 600.0
# A run never carries many unknown attempts: each one fences the run.
MAX_UNKNOWN_ATTEMPTS = 64


class RecoveryReport(BaseModel):
    """What one pass over a run's unknown attempts did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    looked_up: int = 0
    settled: int = 0
    released: int = 0
    pending: int = 0
    unresolvable: int = 0

    @property
    def remaining(self) -> int:
        """Unknown attempts still fencing the run after this pass."""
        return self.pending + self.unresolvable


class FencedRun(BaseModel):
    """A run fenced on an unknown outcome and the execution that owned it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    source_id: UUID
    workflow: WorkflowIdentity | None


async def unknown_attempt_rows(
    database_url: str, *, scope: Scope, run_id: UUID
) -> list[dict[str, Any]]:
    """The run's attempts whose charge is unknown, oldest first, with their age."""
    async with db.scoped(database_url, scope) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, operation_id, owner_token, source_id, route, remote_handle,
                       result_artifact_id, state,
                       extract(epoch FROM now() - coalesce(finished_at, heartbeat_at, created_at))
                           AS age_seconds
                  FROM harness_attempt
                 WHERE run_id = %s AND state = 'outcome_unknown' AND cost_status = 'unknown'
                 ORDER BY created_at, id
                 LIMIT %s
                """,
                (run_id, MAX_UNKNOWN_ATTEMPTS),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def fenced_runs(database_url: str, *, scope: Scope, policy: str) -> list[FencedRun]:
    """Runs of one program fenced on an unknown outcome inside the scoped organization."""
    async with db.scoped(database_url, scope) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, source_id, workflow_id, workflow_run_id
                  FROM harness_run
                 WHERE lane = 'chapters' AND status = 'outcome_unknown'
                   AND route_snapshot->>'editorialPolicy' = %s
                 ORDER BY updated_at, id
                """,
                (policy,),
            )
        ).fetchall()
    fenced: list[FencedRun] = []
    for row in rows:
        workflow = (
            WorkflowIdentity(
                workflow_id=str(row["workflow_id"]), workflow_run_id=str(row["workflow_run_id"])
            )
            if row["workflow_id"] and row["workflow_run_id"]
            else None
        )
        fenced.append(FencedRun(run_id=row["id"], source_id=row["source_id"], workflow=workflow))
    return fenced


def _route(row: dict[str, Any]) -> RouteEntry:
    raw = row["route"]
    if not isinstance(raw, dict):
        msg = "unknown-cost attempt has no frozen route identity"
        raise TypeError(msg)
    return RouteEntry.model_validate_json(
        canonical_json(cast("dict[str, object]", raw)), strict=True
    )


async def recover_unknown_attempts(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    run_id: UUID,
    config: GatewayConfig,
    client: httpx.AsyncClient,
    grace_seconds: float = RECEIPT_GRACE_SECONDS,
    lookup: Callable[..., Awaitable[CostObservation]] | None = None,
) -> RecoveryReport:
    """One pass: ask the gateway about every unknown attempt and settle what it answers.

    Idempotent and safe to run from several places at once: a settled attempt no
    longer matches the unknown filter, and a concurrent settlement surfaces as a
    ledger identity error that this pass simply skips.
    """
    ask = lookup if lookup is not None else lookup_generation
    rows = await unknown_attempt_rows(database_url, scope=scope, run_id=run_id)
    looked_up = settled = released = pending = unresolvable = 0
    for row in rows:
        handle = row["remote_handle"]
        if not isinstance(handle, str) or not handle:
            unresolvable += 1
            continue
        try:
            route = _route(row)
            observation = await ask(client, config=config, route=route, generation_id=handle)
        except (GatewayError, httpx.HTTPError, ValueError, TimeoutError, TypeError) as error:
            log.warning("receipt lookup for %s failed: %s", handle, error)
            pending += 1
            continue
        looked_up += 1
        usage = dict(observation.components)
        try:
            if observation.status == "reported" and observation.actual_cost_micros is not None:
                await _settle(
                    database_url,
                    scope=scope,
                    run_id=run_id,
                    row=row,
                    actual_cost_micros=observation.actual_cost_micros,
                    usage=usage,
                    error_code="provider-stream-failure",
                    error_message=(
                        f"Route {route.id}: the request ended without a response; its charge of "
                        f"{observation.actual_cost_micros} micros was settled from the gateway "
                        "receipt and a fresh attempt is allowed."
                    ),
                )
                settled += 1
            elif (
                observation.status == "pending"
                and usage.get("reason") == "usage_not_found"
                and row["result_artifact_id"] is None
                and float(row["age_seconds"]) >= grace_seconds
            ):
                usage["settled"] = "receipt-absent"
                usage["graceSeconds"] = int(grace_seconds)
                await _settle(
                    database_url,
                    scope=scope,
                    run_id=run_id,
                    row=row,
                    actual_cost_micros=0,
                    usage=usage,
                    error_code="provider-receipt-absent",
                    error_message=(
                        f"Route {route.id}: the request ended without a response and the gateway "
                        f"has no record of generation {handle} {int(grace_seconds // 60)} minutes "
                        "later; no charge is attributable and a fresh attempt is allowed."
                    ),
                )
                released += 1
            else:
                pending += 1
        except (ledger.IdentityConflict, ledger.LostOwnership) as error:
            log.info("unknown attempt %s was settled elsewhere: %s", row["id"], error)
    return RecoveryReport(
        looked_up=looked_up,
        settled=settled,
        released=released,
        pending=pending,
        unresolvable=unresolvable,
    )


async def _settle(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    run_id: UUID,
    row: dict[str, Any],
    actual_cost_micros: int,
    usage: dict[str, Any],
    error_code: str,
    error_message: str,
) -> None:
    if row["result_artifact_id"] is not None:
        # The answer was retained; only its charge was unknown.
        await ledger.reconcile_cost(
            database_url,
            scope=scope,
            source_id=row["source_id"],
            run_id=run_id,
            operation_id=row["operation_id"],
            attempt_id=row["id"],
            owner_token=str(row["owner_token"]),
            observed_cost_micros=actual_cost_micros,
            usage=usage,
        )
        return
    await ledger.fail_attempt(
        database_url,
        scope=scope,
        source_id=row["source_id"],
        run_id=run_id,
        operation_id=row["operation_id"],
        attempt_id=row["id"],
        owner_token=str(row["owner_token"]),
        outcome_known=True,
        actual_cost_micros=actual_cost_micros,
        usage=usage,
        error_code=error_code,
        error_message=error_message,
        reconcile_unknown=True,
    )
