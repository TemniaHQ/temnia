"""Transactional operation, attempt, reservation, and provider-cost persistence.

Every public call opens one short organization-scoped transaction. External
calls belong between ``mark_dispatched`` and a terminal method, after the
dispatch compare-and-swap has committed.
"""

# The public exception names are part of the persistence API specified by the
# workflow brief. Messages are deliberately written at their refusal sites,
# where the exact state that caused each refusal is visible.
# ruff: noqa: EM101, EM102, N818, TRY003

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from temnia_pipeline import db

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from psycopg import AsyncConnection

    from temnia_pipeline.contracts import Scope

JsonObject = Mapping[str, Any]
MAX_ERROR_LENGTH = 2000
# An unknown outcome is a state the reader has to act on, so it owns a sentence.
OUTCOME_UNKNOWN_RUN_MESSAGE = (
    "A provider call ended without a confirmed outcome. Its reservation is retained until "
    "reconciliation; nothing is retried automatically."
)
_URL = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|s3)://[^\s]+")
_SECRET = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|access[-_ ]?key|secret|token|password)"
    r"\s*[:=]\s*[^\s,;]+"
)


class LedgerError(RuntimeError):
    """Base class for expected persistence refusals."""


class BudgetExceeded(LedgerError):
    """The run cannot cover the new worst-case exposure."""


class DispatchLimitExceeded(LedgerError):
    """The run has reached its configured physical dispatch ceiling."""


class OutcomeUnknown(LedgerError):
    """A physical call may have happened and must be reconciled first."""


class LostOwnership(LedgerError):
    """The supplied activity owner token does not own the attempt."""


class IdentityConflict(LedgerError):
    """An idempotency identity was reused for different immutable facts."""


class SourceDeleting(LedgerError):
    """The source deletion fence is set, so no new history may be created."""


class OperationKind(StrEnum):
    """Kinds accepted by ``harness_operation.kind``."""

    RECOGNIZE = "recognize"
    ALIGN = "align"
    DIARIZE = "diarize"
    SPEECH_COVERAGE = "speech_coverage"
    EVIDENCE = "evidence"
    MODEL = "model"
    COMPILE = "compile"
    RENDER = "render"
    CHECK = "check"
    EXPORT = "export"


class AttemptState(StrEnum):
    """Physical attempt states persisted by the ledger."""

    RESERVED = "reserved"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_KNOWN = "failed_known"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED_CONFIRMED = "cancelled_confirmed"


@dataclass(frozen=True, slots=True)
class Operation:
    """The durable logical operation identity and current result."""

    id: UUID
    run_id: UUID | None
    semantic_key: str
    kind: str
    stage: str
    status: str
    input_hash: str
    config_hash: str
    result_artifact_id: UUID | None


@dataclass(frozen=True, slots=True)
class OperationAcquisition:
    """Result of acquiring an idempotent logical operation."""

    operation: Operation
    created: bool
    accepted: bool
    pending: bool
    uncertain: bool


@dataclass(frozen=True, slots=True)
class Attempt:
    """A physical provider attempt and its budget exposure."""

    id: UUID
    operation_id: UUID
    run_id: UUID | None
    attempt_number: int
    owner_token: str
    state: str
    estimated_cost_micros: int
    actual_cost_micros: int | None
    cost_status: str
    remote_handle: str | None
    dispatched_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttemptReservationRequest:
    """Immutable provider identity and exposure requested for one operation."""

    operation_id: UUID
    owner_token: str
    provider: str
    model: str | None
    family: str | None
    route: JsonObject
    request_hash: str
    estimated_cost_micros: int
    dispatch_limit: int | None


def _canonical_hash(value: object) -> str:
    try:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as error:
        msg = "operation identity must be finite canonical JSON"
        raise ValueError(msg) from error
    return hashlib.sha256(body).hexdigest()


def operation_identity(
    *, run_id: UUID | None, kind: OperationKind | str, inputs: object, config: object
) -> tuple[str, str, str]:
    """Return semantic, input, and configuration hashes for a logical operation."""
    kind_value = str(kind)
    input_hash = _canonical_hash(inputs)
    config_hash = _canonical_hash(config)
    semantic_key = _canonical_hash(
        {
            "configHash": config_hash,
            "inputHash": input_hash,
            "kind": kind_value,
            "runId": str(run_id) if run_id is not None else None,
        }
    )
    return semantic_key, input_hash, config_hash


def sanitize_failure(message: str) -> str:
    """Bound a provider-facing failure and remove likely URLs and credentials."""
    clean = _URL.sub("<redacted-url>", message)
    clean = _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", clean)
    return clean[:MAX_ERROR_LENGTH]


def _operation(row: Mapping[str, Any]) -> Operation:
    return Operation(
        id=row["id"],
        run_id=row["run_id"],
        semantic_key=str(row["semantic_key"]),
        kind=str(row["kind"]),
        stage=str(row["stage"]),
        status=str(row["status"]),
        input_hash=str(row["input_hash"]),
        config_hash=str(row["config_hash"]),
        result_artifact_id=row["result_artifact_id"],
    )


def _attempt(row: Mapping[str, Any]) -> Attempt:
    return Attempt(
        id=row["id"],
        operation_id=row["operation_id"],
        run_id=row["run_id"],
        attempt_number=int(row["attempt_number"]),
        owner_token=str(row["owner_token"]),
        state=str(row["state"]),
        estimated_cost_micros=int(row["estimated_cost_micros"]),
        actual_cost_micros=(
            int(row["actual_cost_micros"]) if row["actual_cost_micros"] is not None else None
        ),
        cost_status=str(row["cost_status"]),
        remote_handle=row["remote_handle"],
        dispatched_at=row["dispatched_at"],
        finished_at=row["finished_at"],
    )


async def _lock_source(conn: AsyncConnection[dict[str, Any]], source_id: UUID) -> Mapping[str, Any]:
    row = await (
        await conn.execute(
            "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
            (source_id,),
        )
    ).fetchone()
    if row is None:
        msg = f"source {source_id} is absent from the active scope"
        raise IdentityConflict(msg)
    if row["deletion_requested_at"] is not None:
        msg = f"source {source_id} is fenced for deletion"
        raise SourceDeleting(msg)
    return row


async def _lock_operation(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, operation_id: UUID
) -> Mapping[str, Any]:
    row = await (
        await conn.execute(
            """
            SELECT * FROM harness_operation
             WHERE id = %s AND source_id = %s
             FOR UPDATE
            """,
            (operation_id, source_id),
        )
    ).fetchone()
    if row is None:
        msg = f"operation {operation_id} is absent from the source scope"
        raise IdentityConflict(msg)
    return row


async def _lock_attempt(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, attempt_id: UUID, owner_token: str
) -> Mapping[str, Any]:
    row = await (
        await conn.execute(
            """
            SELECT * FROM harness_attempt
             WHERE id = %s AND source_id = %s
             FOR UPDATE
            """,
            (attempt_id, source_id),
        )
    ).fetchone()
    if row is None:
        msg = f"attempt {attempt_id} is absent from the source scope"
        raise IdentityConflict(msg)
    if row["owner_token"] != owner_token:
        raise LostOwnership("attempt belongs to a different activity execution")
    return row


async def acquire_operation(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID | None,
    kind: OperationKind | str,
    stage: str,
    inputs: object,
    config: object,
) -> OperationAcquisition:
    """Create or read one logical operation after fencing source deletion."""
    kind_value = str(kind)
    if kind_value == OperationKind.MODEL and run_id is None:
        raise ValueError("model operations require a budgeted harness run")
    semantic_key, input_hash, config_hash = operation_identity(
        run_id=run_id, kind=kind_value, inputs=inputs, config=config
    )
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        row = await (
            await conn.execute(
                """
                INSERT INTO harness_operation
                    (organization_id, source_id, run_id, semantic_key, kind, stage,
                     input_hash, config_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, source_id, semantic_key) DO NOTHING
                RETURNING *
                """,
                (
                    scope.organizationId,
                    source_id,
                    run_id,
                    semantic_key,
                    kind_value,
                    stage,
                    input_hash,
                    config_hash,
                ),
            )
        ).fetchone()
        created = row is not None
        if row is None:
            row = await (
                await conn.execute(
                    """
                    SELECT * FROM harness_operation
                     WHERE organization_id = %s AND source_id = %s AND semantic_key = %s
                     FOR UPDATE
                    """,
                    (scope.organizationId, source_id, semantic_key),
                )
            ).fetchone()
        if row is None:
            raise IdentityConflict("operation identity disappeared while acquiring it")
        expected = (run_id, kind_value, stage, input_hash, config_hash)
        actual = (
            row["run_id"],
            str(row["kind"]),
            str(row["stage"]),
            str(row["input_hash"]),
            str(row["config_hash"]),
        )
        if actual != expected:
            raise IdentityConflict("semantic operation key maps to different immutable inputs")
        operation = _operation(row)
        return OperationAcquisition(
            operation=operation,
            created=created,
            accepted=operation.status == "succeeded",
            pending=operation.status in {"pending", "running"},
            uncertain=operation.status == "outcome_unknown",
        )


async def complete_operation_from_artifact(  # noqa: C901, PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    result_artifact_id: UUID,
    expected_artifact_kind: str,
    expected_artifact_fingerprint: str,
) -> Operation:
    """Complete a never-dispatched logical operation from an immutable cache hit."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT id, status FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")
        operation = await _lock_operation(conn, source_id, operation_id)
        if operation["run_id"] != run_id:
            raise IdentityConflict("operation does not belong to the supplied run")
        artifact = await (
            await conn.execute(
                """
                SELECT id, kind, fingerprint FROM harness_artifact
                 WHERE id = %s AND organization_id = %s AND source_id = %s
                """,
                (result_artifact_id, scope.organizationId, source_id),
            )
        ).fetchone()
        if artifact is None:
            raise IdentityConflict("cached artifact is absent from the source scope")
        if (
            str(artifact["kind"]) != expected_artifact_kind
            or str(artifact["fingerprint"]) != expected_artifact_fingerprint
        ):
            raise IdentityConflict("cached artifact identity differs from the expected result")
        attempts = await (
            await conn.execute(
                "SELECT count(*)::int AS count FROM harness_attempt WHERE operation_id = %s",
                (operation_id,),
            )
        ).fetchone()
        if attempts is None or int(attempts["count"]) != 0:
            raise IdentityConflict("an operation with physical attempts cannot use a cache result")
        if operation["status"] == "succeeded":
            if operation["result_artifact_id"] != result_artifact_id:
                raise IdentityConflict("operation already succeeded with a different artifact")
            return _operation(operation)
        if operation["status"] != "pending":
            if operation["status"] == "outcome_unknown":
                raise OutcomeUnknown("operation has an unreconciled physical outcome")
            raise IdentityConflict(
                "only a pending never-dispatched operation can use a cache result"
            )
        row = await (
            await conn.execute(
                """
                UPDATE harness_operation
                   SET status = 'succeeded', result_artifact_id = %s, updated_at = now()
                 WHERE id = %s
                 RETURNING *
                """,
                (result_artifact_id, operation_id),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("cache completion did not return the operation")
        return _operation(row)


async def reserve_attempt(  # noqa: C901, PLR0912, PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    owner_token: str,
    provider: str,
    model: str | None,
    family: str | None,
    route: JsonObject,
    request_hash: str,
    estimated_cost_micros: int,
    dispatch_limit: int | None,
) -> Attempt:
    """Reserve worst-case exposure before any physical provider dispatch."""
    if estimated_cost_micros < 0:
        raise ValueError("estimated cost must be nonnegative")
    if dispatch_limit is not None and dispatch_limit <= 0:
        raise ValueError("dispatch limit must be positive")
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")
        operation = await _lock_operation(conn, source_id, operation_id)
        if operation["run_id"] != run_id:
            raise IdentityConflict("operation does not belong to the supplied run")
        if operation["status"] == "succeeded":
            raise IdentityConflict("a successful operation cannot acquire another attempt")
        active = await (
            await conn.execute(
                """
                SELECT * FROM harness_attempt
                 WHERE operation_id = %s
                   AND state IN ('reserved', 'dispatching', 'running',
                                 'outcome_unknown', 'cancel_requested')
                 ORDER BY attempt_number DESC LIMIT 1
                 FOR UPDATE
                """,
                (operation_id,),
            )
        ).fetchone()
        if active is not None:
            if active["state"] == "cancel_requested":
                raise OutcomeUnknown("the current attempt must be reconciled before retry")
            immutable = (
                active["provider"],
                active["model"],
                active["family"],
                active["request_hash"],
                int(active["estimated_cost_micros"]),
            )
            expected = (provider, model, family, request_hash, estimated_cost_micros)
            if immutable != expected or active["route"] != dict(route):
                raise IdentityConflict("active attempt differs from the exact request identity")
            if active["owner_token"] != owner_token:
                if active["state"] == "reserved":
                    reclaimed = await (
                        await conn.execute(
                            """
                            UPDATE harness_attempt
                               SET owner_token = %s, heartbeat_at = now()
                             WHERE id = %s AND state = 'reserved'
                             RETURNING *
                            """,
                            (owner_token, active["id"]),
                        )
                    ).fetchone()
                    if reclaimed is None:
                        raise LostOwnership(
                            "reserved attempt changed while ownership was reclaimed"
                        )
                    return _attempt(reclaimed)
                raise LostOwnership("an active attempt belongs to another activity execution")
            return _attempt(active)
        if operation["status"] == "outcome_unknown":
            raise OutcomeUnknown("operation has an unreconciled physical outcome")
        if run["status"] in {"cancelled", "failed", "ready"}:
            raise IdentityConflict(f"run in terminal state {run['status']} cannot dispatch")
        if run["status"] == "outcome_unknown":
            raise OutcomeUnknown("run has unresolved provider exposure")
        if dispatch_limit is not None and int(run["dispatch_count"]) >= dispatch_limit:
            raise DispatchLimitExceeded("run dispatch ceiling reached")
        exposure = int(run["spent_micros"]) + int(run["reserved_micros"])
        if exposure + estimated_cost_micros > int(run["budget_micros"]):
            raise BudgetExceeded("run budget cannot cover the new provider exposure")
        previous = await (
            await conn.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0)::int AS number
                  FROM harness_attempt WHERE operation_id = %s
                """,
                (operation_id,),
            )
        ).fetchone()
        number = int(previous["number"]) + 1 if previous else 1
        attempt = await (
            await conn.execute(
                """
                INSERT INTO harness_attempt
                    (organization_id, source_id, operation_id, run_id, attempt_number,
                     owner_token, provider, model, family, route, request_hash,
                     estimated_cost_micros, cost_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                        'estimated')
                RETURNING *
                """,
                (
                    scope.organizationId,
                    source_id,
                    operation_id,
                    run_id,
                    number,
                    owner_token,
                    provider,
                    model,
                    family,
                    json.dumps(dict(route), allow_nan=False),
                    request_hash,
                    estimated_cost_micros,
                ),
            )
        ).fetchone()
        if attempt is None:
            raise RuntimeError("attempt insert did not return a row")
        await conn.execute(
            """
            INSERT INTO harness_reservation
                (organization_id, source_id, run_id, attempt_id, amount_micros)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (scope.organizationId, source_id, run_id, attempt["id"], estimated_cost_micros),
        )
        await conn.execute(
            "UPDATE harness_run SET reserved_micros = reserved_micros + %s, updated_at = now()"
            " WHERE id = %s",
            (estimated_cost_micros, run_id),
        )
        return _attempt(attempt)


async def reserve_attempt_batch(  # noqa: C901, PLR0912, PLR0915
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    requests: tuple[AttemptReservationRequest, ...],
) -> tuple[Attempt, ...]:
    """Atomically reserve a set of physical provider attempts.

    The batch locks operations in stable UUID order and returns attempts in the
    caller's order. No provider dispatch may begin until this transaction has
    committed and each returned reserved attempt separately wins
    :func:`mark_dispatched`.
    """
    if not requests:
        raise ValueError("attempt reservation batch must not be empty")
    operation_ids = [request.operation_id for request in requests]
    if len(set(operation_ids)) != len(operation_ids):
        raise ValueError("attempt reservation batch contains a duplicate operation")
    route_values: dict[UUID, dict[str, Any]] = {}
    route_bodies: dict[UUID, str] = {}
    for request in requests:
        if request.estimated_cost_micros < 0:
            raise ValueError("estimated cost must be nonnegative")
        if request.dispatch_limit is not None and request.dispatch_limit <= 0:
            raise ValueError("dispatch limit must be positive")
        route_value = dict(request.route)
        try:
            route_body = json.dumps(route_value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("attempt route must be finite JSON") from error
        route_values[request.operation_id] = route_value
        route_bodies[request.operation_id] = route_body

    request_by_operation = {request.operation_id: request for request in requests}
    ordered_ids = sorted(operation_ids, key=str)
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")

        operations: dict[UUID, Mapping[str, Any]] = {}
        for operation_id in ordered_ids:
            operation = await _lock_operation(conn, source_id, operation_id)
            if operation["run_id"] != run_id:
                raise IdentityConflict("operation does not belong to the supplied run")
            operations[operation_id] = operation

        attempts: dict[UUID, Mapping[str, Any]] = {}
        missing: list[UUID] = []
        reserved: list[UUID] = []
        for operation_id in ordered_ids:
            request = request_by_operation[operation_id]
            operation = operations[operation_id]
            active = await (
                await conn.execute(
                    """
                    SELECT * FROM harness_attempt
                     WHERE operation_id = %s
                       AND state IN ('reserved', 'dispatching', 'running',
                                     'outcome_unknown', 'cancel_requested')
                     ORDER BY attempt_number DESC LIMIT 1
                     FOR UPDATE
                    """,
                    (operation_id,),
                )
            ).fetchone()
            if active is not None:
                if active["run_id"] != run_id:
                    raise IdentityConflict(
                        "operation and active attempt do not belong to the same run"
                    )
                if operation["status"] == "succeeded":
                    raise IdentityConflict("successful operation has an active attempt")
                immutable = (
                    active["provider"],
                    active["model"],
                    active["family"],
                    active["request_hash"],
                    int(active["estimated_cost_micros"]),
                    active["route"],
                )
                expected = (
                    request.provider,
                    request.model,
                    request.family,
                    request.request_hash,
                    request.estimated_cost_micros,
                    route_values[operation_id],
                )
                if immutable != expected:
                    raise IdentityConflict("active attempt differs from the exact request identity")
                if active["state"] in {"outcome_unknown", "cancel_requested"}:
                    raise OutcomeUnknown("the current attempt must be reconciled before retry")
                if active["state"] == "reserved":
                    reserved.append(operation_id)
                elif active["owner_token"] != request.owner_token:
                    raise LostOwnership("an active attempt belongs to another activity execution")
                attempts[operation_id] = active
                continue

            if operation["status"] == "succeeded":
                succeeded = await (
                    await conn.execute(
                        """
                        SELECT * FROM harness_attempt
                         WHERE operation_id = %s AND state = 'succeeded'
                         ORDER BY attempt_number DESC LIMIT 1
                         FOR UPDATE
                        """,
                        (operation_id,),
                    )
                ).fetchone()
                if succeeded is None:
                    raise IdentityConflict(
                        "cache-completed operation must be omitted from attempt batch"
                    )
                if succeeded["run_id"] != run_id:
                    raise IdentityConflict(
                        "operation and successful attempt do not belong to the same run"
                    )
                immutable = (
                    succeeded["provider"],
                    succeeded["model"],
                    succeeded["family"],
                    succeeded["request_hash"],
                    int(succeeded["estimated_cost_micros"]),
                    succeeded["route"],
                )
                expected = (
                    request.provider,
                    request.model,
                    request.family,
                    request.request_hash,
                    request.estimated_cost_micros,
                    route_values[operation_id],
                )
                if immutable != expected:
                    raise IdentityConflict(
                        "successful attempt differs from the exact request identity"
                    )
                attempts[operation_id] = succeeded
                continue
            if operation["status"] == "outcome_unknown":
                raise OutcomeUnknown("operation has an unreconciled physical outcome")
            missing.append(operation_id)

        admission_ids = [*reserved, *missing]
        if admission_ids:
            if run["status"] in {"cancelled", "failed", "ready"}:
                raise IdentityConflict(f"run in terminal state {run['status']} cannot dispatch")
            if run["status"] == "outcome_unknown":
                raise OutcomeUnknown("run has unresolved provider exposure")
            new_exposure = sum(
                request_by_operation[operation_id].estimated_cost_micros for operation_id in missing
            )
            exposure = int(run["spent_micros"]) + int(run["reserved_micros"])
            if exposure + new_exposure > int(run["budget_micros"]):
                raise BudgetExceeded("run budget cannot cover the combined provider exposure")
            reserved_count = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count FROM harness_attempt
                     WHERE run_id = %s AND state = 'reserved'
                    """,
                    (run_id,),
                )
            ).fetchone()
            projected_dispatches = (
                int(run["dispatch_count"])
                + (int(reserved_count["count"]) if reserved_count is not None else 0)
                + len(missing)
            )
            if any(
                (limit := request_by_operation[operation_id].dispatch_limit) is not None
                and projected_dispatches > limit
                for operation_id in admission_ids
            ):
                raise DispatchLimitExceeded(
                    "run dispatch ceiling cannot cover the combined attempt batch"
                )

        for operation_id in reserved:
            request = request_by_operation[operation_id]
            active = attempts[operation_id]
            if active["owner_token"] == request.owner_token:
                continue
            reclaimed = await (
                await conn.execute(
                    """
                    UPDATE harness_attempt
                       SET owner_token = %s, heartbeat_at = now()
                     WHERE id = %s AND state = 'reserved'
                     RETURNING *
                    """,
                    (request.owner_token, active["id"]),
                )
            ).fetchone()
            if reclaimed is None:
                raise LostOwnership("reserved attempt changed while ownership was reclaimed")
            attempts[operation_id] = reclaimed

        for operation_id in missing:
            request = request_by_operation[operation_id]
            previous = await (
                await conn.execute(
                    """
                    SELECT COALESCE(MAX(attempt_number), 0)::int AS number
                      FROM harness_attempt WHERE operation_id = %s
                    """,
                    (operation_id,),
                )
            ).fetchone()
            number = int(previous["number"]) + 1 if previous else 1
            attempt = await (
                await conn.execute(
                    """
                    INSERT INTO harness_attempt
                        (organization_id, source_id, operation_id, run_id, attempt_number,
                         owner_token, provider, model, family, route, request_hash,
                         estimated_cost_micros, cost_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                            'estimated')
                    RETURNING *
                    """,
                    (
                        scope.organizationId,
                        source_id,
                        operation_id,
                        run_id,
                        number,
                        request.owner_token,
                        request.provider,
                        request.model,
                        request.family,
                        route_bodies[operation_id],
                        request.request_hash,
                        request.estimated_cost_micros,
                    ),
                )
            ).fetchone()
            if attempt is None:
                raise RuntimeError("attempt insert did not return a row")
            await conn.execute(
                """
                INSERT INTO harness_reservation
                    (organization_id, source_id, run_id, attempt_id, amount_micros)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    scope.organizationId,
                    source_id,
                    run_id,
                    attempt["id"],
                    request.estimated_cost_micros,
                ),
            )
            attempts[operation_id] = attempt
        if missing:
            await conn.execute(
                """
                UPDATE harness_run
                   SET reserved_micros = reserved_micros + %s, updated_at = now()
                 WHERE id = %s
                """,
                (
                    sum(
                        request_by_operation[operation_id].estimated_cost_micros
                        for operation_id in missing
                    ),
                    run_id,
                ),
            )
        return tuple(_attempt(attempts[request.operation_id]) for request in requests)


async def find_recoverable_attempt(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    provider: str,
    model: str | None,
    family: str | None,
    route: JsonObject,
    request_hash: str,
    estimated_cost_micros: int,
) -> Attempt | None:
    """Return immutable dispatched evidence without transferring its ownership.

    This is the crash-recovery lookup used before reservation. A caller may use
    the returned attempt only to find and settle its exact durable response; an
    absent response remains fenced from redispatch.
    """
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT id FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")
        operation = await _lock_operation(conn, source_id, operation_id)
        if operation["run_id"] != run_id:
            raise IdentityConflict("operation does not belong to the supplied run")
        row = await (
            await conn.execute(
                """
                SELECT * FROM harness_attempt
                 WHERE operation_id = %s
                   AND state IN ('dispatching', 'running', 'outcome_unknown')
                 ORDER BY attempt_number DESC LIMIT 1
                 FOR UPDATE
                """,
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            return None
        immutable = (
            row["provider"],
            row["model"],
            row["family"],
            row["request_hash"],
            int(row["estimated_cost_micros"]),
            row["route"],
        )
        expected = (
            provider,
            model,
            family,
            request_hash,
            estimated_cost_micros,
            dict(route),
        )
        if immutable != expected:
            raise IdentityConflict("recoverable attempt differs from the exact request identity")
        return _attempt(row)


async def mark_dispatched(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    dispatch_limit: int | None,
) -> bool:
    """Win the one physical dispatch by CAS; commit before making the call."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")
        operation = await _lock_operation(conn, source_id, operation_id)
        attempt = await _lock_attempt(conn, source_id, attempt_id, owner_token)
        if (
            operation["run_id"] != run_id
            or attempt["run_id"] != run_id
            or attempt["operation_id"] != operation_id
        ):
            raise IdentityConflict("run, operation, and attempt identities do not agree")
        if attempt["state"] == "reserved":
            if run["status"] == "outcome_unknown":
                raise OutcomeUnknown("run acquired unresolved exposure after reservation")
            if run["status"] not in {"pending", "running", "needs_review"}:
                raise IdentityConflict(
                    f"run in state {run['status']} cannot dispatch a reserved attempt"
                )
            if dispatch_limit is not None and int(run["dispatch_count"]) >= dispatch_limit:
                raise DispatchLimitExceeded("run dispatch ceiling reached")
            exposure = int(run["spent_micros"]) + int(run["reserved_micros"])
            if exposure > int(run["budget_micros"]):
                raise BudgetExceeded("run became over budget after this attempt was reserved")
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET state = 'dispatching', dispatched_at = now(), heartbeat_at = now()
                 WHERE id = %s AND state = 'reserved'
                """,
                (attempt_id,),
            )
            await conn.execute(
                """
                UPDATE harness_run
                   SET dispatch_count = dispatch_count + 1, status = 'running', updated_at = now()
                 WHERE id = %s
                """,
                (run_id,),
            )
            await conn.execute(
                "UPDATE harness_operation SET status = 'running', updated_at = now() WHERE id = %s",
                (operation_id,),
            )
            return True
        if attempt["state"] in {"dispatching", "running", "succeeded", "failed_known"}:
            return False
        if attempt["state"] in {"outcome_unknown", "cancel_requested"}:
            raise OutcomeUnknown("attempt may already have reached the provider")
        raise LostOwnership("attempt was released or cancelled before dispatch")


async def attach_remote_handle(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    remote_handle: str,
) -> None:
    """Persist the provider handle once for the active matching owner."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        operation = await _lock_operation(conn, source_id, operation_id)
        attempt = await _lock_attempt(conn, source_id, attempt_id, owner_token)
        if attempt["operation_id"] != operation["id"]:
            raise IdentityConflict("attempt does not belong to the supplied operation")
        if attempt["state"] not in {"dispatching", "running"}:
            raise LostOwnership("attempt is no longer active")
        if attempt["remote_handle"] not in {None, remote_handle}:
            raise IdentityConflict("attempt already has a different provider handle")
        await conn.execute(
            "UPDATE harness_attempt SET remote_handle = %s, heartbeat_at = now() WHERE id = %s",
            (remote_handle, attempt_id),
        )


async def heartbeat_attempt(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
) -> None:
    """Touch liveness only while the matching owner remains active."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        operation = await _lock_operation(conn, source_id, operation_id)
        attempt = await _lock_attempt(conn, source_id, attempt_id, owner_token)
        if attempt["operation_id"] != operation["id"]:
            raise IdentityConflict("attempt does not belong to the supplied operation")
        if attempt["state"] not in {"reserved", "dispatching", "running", "cancel_requested"}:
            raise LostOwnership("attempt is no longer active")
        await conn.execute(
            "UPDATE harness_attempt SET heartbeat_at = now() WHERE id = %s", (attempt_id,)
        )


async def mark_running(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
) -> bool:
    """Record that the provider has acknowledged the dispatched job."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_source(conn, source_id)
        operation = await _lock_operation(conn, source_id, operation_id)
        attempt = await _lock_attempt(conn, source_id, attempt_id, owner_token)
        if attempt["operation_id"] != operation["id"]:
            raise IdentityConflict("attempt does not belong to the supplied operation")
        if attempt["state"] == "running":
            return False
        if attempt["state"] != "dispatching":
            raise LostOwnership("attempt is not dispatching")
        await conn.execute(
            "UPDATE harness_attempt SET state = 'running', heartbeat_at = now() WHERE id = %s",
            (attempt_id,),
        )
        return True


async def _lock_terminal_context(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    await _lock_source(conn, source_id)
    run = await (
        await conn.execute(
            "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
            (run_id, source_id),
        )
    ).fetchone()
    if run is None:
        raise IdentityConflict("run is absent from the source scope")
    operation = await _lock_operation(conn, source_id, operation_id)
    attempt = await _lock_attempt(conn, source_id, attempt_id, owner_token)
    reservation = await (
        await conn.execute(
            "SELECT * FROM harness_reservation WHERE attempt_id = %s FOR UPDATE", (attempt_id,)
        )
    ).fetchone()
    if reservation is None:
        raise IdentityConflict("attempt has no budget reservation")
    if (
        operation["run_id"] != run_id
        or attempt["run_id"] != run_id
        or attempt["operation_id"] != operation_id
        or reservation["run_id"] != run_id
    ):
        raise IdentityConflict("terminal attempt identities do not agree")
    return run, operation, attempt, reservation


async def _settle_cost(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    attempt_id: UUID,
    reservation: Mapping[str, Any],
    actual_cost_micros: int,
    usage: JsonObject,
    reconciled: bool = False,
) -> None:
    if actual_cost_micros < 0:
        raise ValueError("actual cost must be nonnegative")
    if reservation["state"] == "settled":
        if int(reservation["settled_micros"]) != actual_cost_micros:
            raise IdentityConflict("attempt was already settled at a different provider cost")
        return
    if reservation["state"] != "active":
        raise IdentityConflict("a released reservation cannot acquire provider cost")
    reserved = int(reservation["amount_micros"])
    await conn.execute(
        """
        UPDATE harness_reservation
           SET state = 'settled', settled_micros = %s, settled_at = now()
         WHERE id = %s
        """,
        (actual_cost_micros, reservation["id"]),
    )
    await conn.execute(
        """
        UPDATE harness_run
           SET reserved_micros = reserved_micros - %s,
               spent_micros = spent_micros + %s, updated_at = now()
         WHERE id = %s
        """,
        (reserved, actual_cost_micros, run_id),
    )
    detail: dict[str, object] = {"attemptId": str(attempt_id), "usage": dict(usage)}
    if reconciled:
        detail["reconciled"] = True
    await conn.execute(
        """
        INSERT INTO usage_ledger
            (organization_id, kind, quantity, source_id, detail, idempotency_key)
        VALUES (%s, 'provider_cost_micros', %s, %s, %s::jsonb, %s)
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        """,
        (
            scope.organizationId,
            actual_cost_micros,
            source_id,
            json.dumps(detail, allow_nan=False),
            f"provider-cost:{attempt_id}",
        ),
    )


async def complete_attempt(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    result_artifact_id: UUID,
    usage: JsonObject,
    actual_cost_micros: int | None,
) -> Attempt:
    """Accept a successful result and settle known cost exactly once."""
    async with db.scoped(database_url, scope) as conn:
        _, operation, attempt, reservation = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        if attempt["state"] == "succeeded":
            expected_cost = (
                int(attempt["actual_cost_micros"])
                if attempt["actual_cost_micros"] is not None
                else None
            )
            if (
                attempt["result_artifact_id"] != result_artifact_id
                or expected_cost != actual_cost_micros
                or attempt["usage"] != dict(usage)
            ):
                raise IdentityConflict("successful attempt completion was replayed differently")
            return _attempt(attempt)
        recovered_unknown = attempt["state"] == "outcome_unknown"
        if attempt["state"] not in {"dispatching", "running", "outcome_unknown"}:
            if attempt["state"] == "cancel_requested":
                raise OutcomeUnknown("attempt outcome must be reconciled before completion")
            raise LostOwnership("attempt is already terminal")
        artifact = await (
            await conn.execute(
                """
                SELECT id FROM harness_artifact
                 WHERE id = %s AND source_id = %s
                """,
                (result_artifact_id, source_id),
            )
        ).fetchone()
        if artifact is None:
            raise IdentityConflict("result artifact is absent from the source scope")
        cost_status = "reported" if actual_cost_micros is not None else "unknown"
        row = await (
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET state = 'succeeded', result_artifact_id = %s, usage = %s::jsonb,
                       actual_cost_micros = %s, cost_status = %s, finished_at = now(),
                       heartbeat_at = now()
                 WHERE id = %s
                 RETURNING *
                """,
                (
                    result_artifact_id,
                    json.dumps(dict(usage), allow_nan=False),
                    actual_cost_micros,
                    cost_status,
                    attempt_id,
                ),
            )
        ).fetchone()
        await conn.execute(
            """
            UPDATE harness_operation
               SET status = 'succeeded', result_artifact_id = %s, updated_at = now()
             WHERE id = %s
            """,
            (result_artifact_id, operation["id"]),
        )
        if recovered_unknown:
            other_unknown = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count FROM harness_attempt
                     WHERE run_id = %s AND id <> %s AND state = 'outcome_unknown'
                    """,
                    (run_id, attempt_id),
                )
            ).fetchone()
            if other_unknown is not None and int(other_unknown["count"]) == 0:
                await conn.execute(
                    "UPDATE harness_run SET status = 'running', updated_at = now()"
                    " WHERE id = %s AND status = 'outcome_unknown'",
                    (run_id,),
                )
        if actual_cost_micros is not None:
            await _settle_cost(
                conn,
                scope=scope,
                source_id=source_id,
                run_id=run_id,
                attempt_id=attempt_id,
                reservation=reservation,
                actual_cost_micros=actual_cost_micros,
                usage=usage,
            )
        if row is None:
            raise RuntimeError("attempt completion did not return a row")
        return _attempt(row)


async def fail_attempt(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    outcome_known: bool,
    actual_cost_micros: int | None,
    usage: JsonObject,
    error_code: str | None,
    error_message: str,
    reconcile_unknown: bool = False,
) -> Attempt:
    """Persist failure; explicit owned reconciliation may settle a later known outcome."""
    state = "failed_known" if outcome_known else "outcome_unknown"
    if not outcome_known and actual_cost_micros is not None:
        raise ValueError("an ambiguous outcome must be reconciled separately")
    async with db.scoped(database_url, scope) as conn:
        _, operation, attempt, reservation = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        if attempt["state"] == state:
            expected_cost = (
                int(attempt["actual_cost_micros"])
                if attempt["actual_cost_micros"] is not None
                else None
            )
            if (
                expected_cost != actual_cost_micros
                or attempt["usage"] != dict(usage)
                or attempt["error_code"] != (sanitize_failure(error_code) if error_code else None)
                or attempt["error_message"] != sanitize_failure(error_message)
            ):
                raise IdentityConflict("terminal attempt failure was replayed differently")
            return _attempt(attempt)
        recovered_unknown = (
            attempt["state"] == "outcome_unknown" and outcome_known and reconcile_unknown
        )
        if (
            attempt["state"] not in {"dispatching", "running", "cancel_requested"}
            and not recovered_unknown
        ):
            if attempt["state"] == "outcome_unknown":
                raise OutcomeUnknown("attempt outcome is already uncertain")
            raise LostOwnership("attempt is already terminal")
        cost_status = "reported" if actual_cost_micros is not None else "unknown"
        row = await (
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET state = %s, actual_cost_micros = %s, cost_status = %s,
                       usage = %s::jsonb, error_code = %s, error_message = %s,
                       finished_at = now(), heartbeat_at = now()
                 WHERE id = %s
                 RETURNING *
                """,
                (
                    state,
                    actual_cost_micros,
                    cost_status,
                    json.dumps(dict(usage), allow_nan=False),
                    sanitize_failure(error_code) if error_code else None,
                    sanitize_failure(error_message),
                    attempt_id,
                ),
            )
        ).fetchone()
        await conn.execute(
            "UPDATE harness_operation SET status = %s, updated_at = now() WHERE id = %s",
            ("failed" if outcome_known else "outcome_unknown", operation["id"]),
        )
        if not outcome_known:
            await conn.execute(
                "UPDATE harness_run SET status = 'outcome_unknown', error_message = %s,"
                " updated_at = now()"
                " WHERE id = %s AND status NOT IN ('cancelled', 'failed', 'ready')",
                (OUTCOME_UNKNOWN_RUN_MESSAGE, run_id),
            )
        elif recovered_unknown:
            await conn.execute(
                """UPDATE harness_run
                      SET status = 'running', error_message = NULL, updated_at = now()
                    WHERE id = %s AND status = 'outcome_unknown' AND NOT EXISTS (
                        SELECT 1 FROM harness_attempt WHERE run_id = %s
                        AND id <> %s AND state = 'outcome_unknown'
                    )""",
                (run_id, run_id, attempt_id),
            )
        if actual_cost_micros is not None:
            await _settle_cost(
                conn,
                scope=scope,
                source_id=source_id,
                run_id=run_id,
                attempt_id=attempt_id,
                reservation=reservation,
                actual_cost_micros=actual_cost_micros,
                usage=usage,
            )
        if row is None:
            raise RuntimeError("attempt failure did not return a row")
        return _attempt(row)


async def request_cancellation(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
) -> bool:
    """Fence an in-flight attempt before asking the provider to cancel it."""
    async with db.scoped(database_url, scope) as conn:
        _, _, attempt, _ = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        if attempt["state"] == "cancel_requested":
            return False
        if attempt["state"] not in {"dispatching", "running"}:
            raise LostOwnership("only an in-flight attempt can request cancellation")
        await conn.execute(
            "UPDATE harness_attempt SET state = 'cancel_requested', heartbeat_at = now()"
            " WHERE id = %s",
            (attempt_id,),
        )
        return True


async def confirm_cancellation(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    actual_cost_micros: int | None,
    usage: JsonObject,
) -> Attempt:
    """Record provider-confirmed cancellation, retaining unknown exposure."""
    async with db.scoped(database_url, scope) as conn:
        _, operation, attempt, reservation = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        if attempt["state"] == "cancelled_confirmed":
            expected = (
                int(attempt["actual_cost_micros"])
                if attempt["actual_cost_micros"] is not None
                else None
            )
            if expected != actual_cost_micros or attempt["usage"] != dict(usage):
                raise IdentityConflict("cancellation was replayed with a different charge")
            return _attempt(attempt)
        if attempt["state"] != "cancel_requested":
            raise LostOwnership("provider cancellation was not requested by this owner")
        row = await (
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET state = 'cancelled_confirmed', actual_cost_micros = %s,
                       cost_status = %s, usage = %s::jsonb, finished_at = now(),
                       heartbeat_at = now()
                 WHERE id = %s RETURNING *
                """,
                (
                    actual_cost_micros,
                    "reported" if actual_cost_micros is not None else "unknown",
                    json.dumps(dict(usage), allow_nan=False),
                    attempt_id,
                ),
            )
        ).fetchone()
        await conn.execute(
            "UPDATE harness_operation SET status = 'cancelled', updated_at = now() WHERE id = %s",
            (operation["id"],),
        )
        if actual_cost_micros is not None:
            await _settle_cost(
                conn,
                scope=scope,
                source_id=source_id,
                run_id=run_id,
                attempt_id=attempt_id,
                reservation=reservation,
                actual_cost_micros=actual_cost_micros,
                usage=usage,
            )
        if row is None:
            raise RuntimeError("cancellation did not return an attempt")
        return _attempt(row)


async def release_undispatched(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
) -> bool:
    """Release only a still-reserved attempt; dispatch and release cannot both win."""
    async with db.scoped(database_url, scope) as conn:
        _, operation, attempt, reservation = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        if attempt["state"] == "cancelled_confirmed" and reservation["state"] == "released":
            return False
        if attempt["state"] != "reserved" or reservation["state"] != "active":
            if attempt["state"] in {"dispatching", "running", "outcome_unknown"}:
                raise OutcomeUnknown("attempt may have reached the provider and cannot be released")
            raise LostOwnership("attempt is no longer an undispatched reservation")
        await conn.execute(
            """
            UPDATE harness_attempt
               SET state = 'cancelled_confirmed', actual_cost_micros = 0,
                   cost_status = 'reported', usage = '{}'::jsonb, finished_at = now()
             WHERE id = %s
            """,
            (attempt_id,),
        )
        await conn.execute(
            """
            UPDATE harness_reservation
               SET state = 'released', settled_micros = 0, settled_at = now()
             WHERE id = %s
            """,
            (reservation["id"],),
        )
        await conn.execute(
            """
            UPDATE harness_run
               SET reserved_micros = reserved_micros - %s, updated_at = now()
             WHERE id = %s
            """,
            (int(reservation["amount_micros"]), run_id),
        )
        await conn.execute(
            "UPDATE harness_operation SET status = 'cancelled', updated_at = now() WHERE id = %s",
            (operation["id"],),
        )
        return True


async def reconcile_cost(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    owner_token: str,
    observed_cost_micros: int,
    usage: JsonObject,
) -> Attempt:
    """Turn one unknown provider charge into a known, idempotently metered charge."""
    async with db.scoped(database_url, scope) as conn:
        _, _, attempt, reservation = await _lock_terminal_context(
            conn,
            source_id=source_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            owner_token=owner_token,
        )
        existing = (
            int(attempt["actual_cost_micros"])
            if attempt["actual_cost_micros"] is not None
            else None
        )
        if existing is not None:
            if existing != observed_cost_micros:
                raise IdentityConflict(
                    "a differing post-reconciliation charge requires an explicit adjustment"
                )
            if attempt["usage"] != dict(usage):
                raise IdentityConflict("cost reconciliation was replayed with different usage")
            return _attempt(attempt)
        if attempt["cost_status"] != "unknown" or reservation["state"] != "active":
            raise IdentityConflict("attempt does not have an unknown active exposure")
        await _settle_cost(
            conn,
            scope=scope,
            source_id=source_id,
            run_id=run_id,
            attempt_id=attempt_id,
            reservation=reservation,
            actual_cost_micros=observed_cost_micros,
            usage=usage,
            reconciled=True,
        )
        row = await (
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET actual_cost_micros = %s, cost_status = 'reconciled', usage = %s::jsonb,
                       heartbeat_at = now()
                 WHERE id = %s RETURNING *
                """,
                (observed_cost_micros, json.dumps(dict(usage), allow_nan=False), attempt_id),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("cost reconciliation did not return an attempt")
        return _attempt(row)
