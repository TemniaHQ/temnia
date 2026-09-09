"""Harness persistence against migrated Postgres and the real obstore memory store."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from obstore.store import MemoryStore
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.gateway import GatewayConfig
from temnia_pipeline.harness.models import (
    HarnessModelDeps,
    ModelRuntime,
    chapter_propose_v1,
    clear_model_runtime,
    configure_model_runtime,
)
from temnia_pipeline.harness.routes import RouteEligibility, RouteEntry, RoutePrices
from temnia_pipeline.speech_benchmark import database_experiment_lease

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store
    from pydantic_ai.messages import ModelMessage

SEEDED = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)
HASH = hashlib.sha256(b"request").hexdigest()
OWNER = "activity-owner"
ORIGINAL_OWNER = "original-activity-owner"
CHARGED_OWNER = "charged-activity-owner"
WAITING_OWNER = "waiting-activity-owner"
REPLACEMENT_OWNER = "replacement-activity-owner"


@dataclass(frozen=True)
class Case:
    project_id: uuid.UUID
    source_id: uuid.UUID
    run_id: uuid.UUID


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: persistence races never skip silently")
    return url


async def make_case(url: str, *, budget: int = 100) -> Case:
    async with db.scoped(url, SEEDED) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SEEDED.organizationId, f"harness-{uuid.uuid4()}"),
            )
        ).fetchone()
        assert project is not None
        source = await (
            await conn.execute(
                """
                INSERT INTO source
                    (organization_id, project_id, title, original_filename, content_type,
                     size_bytes, master_key, status)
                VALUES (%s, %s, 'harness', 'harness.mp4', 'video/mp4', 1, %s, 'ready')
                RETURNING id
                """,
                (SEEDED.organizationId, project["id"], f"harness/{uuid.uuid4()}/master.mp4"),
            )
        ).fetchone()
        assert source is not None
        run = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (organization_id, source_id, request_key, lane, budget_micros,
                     config, route_snapshot)
                VALUES (%s, %s, %s, 'chapters', %s, '{}'::jsonb, '{}'::jsonb)
                RETURNING id
                """,
                (SEEDED.organizationId, source["id"], str(uuid.uuid4()), budget),
            )
        ).fetchone()
        assert run is not None
        return Case(project["id"], source["id"], run["id"])


async def operation(url: str, case: Case, name: str) -> ledger.Operation:
    acquired = await ledger.acquire_operation(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        kind=ledger.OperationKind.MODEL,
        stage=name,
        inputs={"stage": name},
        config={"temperature": 0},
    )
    return acquired.operation


async def reserve(
    url: str,
    case: Case,
    operation_id: uuid.UUID,
    owner: str,
    estimate: int,
) -> ledger.Attempt:
    return await ledger.reserve_attempt(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        operation_id=operation_id,
        owner_token=owner,
        provider="recorded",
        model="fixture",
        family="fixture",
        route={"route": "fixture"},
        request_hash=HASH,
        estimated_cost_micros=estimate,
        dispatch_limit=5,
    )


def batch_request(
    operation_id: uuid.UUID,
    owner: str,
    estimate: int,
    *,
    dispatch_limit: int = 5,
) -> ledger.AttemptReservationRequest:
    return ledger.AttemptReservationRequest(
        operation_id=operation_id,
        owner_token=owner,
        provider="recorded",
        model="fixture",
        family="fixture",
        route={"route": "fixture"},
        request_hash=HASH,
        estimated_cost_micros=estimate,
        dispatch_limit=dispatch_limit,
    )


async def result_artifact(url: str, case: Case) -> uuid.UUID:
    async with db.scoped(url, SEEDED) as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO harness_artifact
                    (organization_id, source_id, kind, fingerprint, storage_key,
                     sha256, size_bytes, metadata)
                VALUES (%s, %s, 'model_response', %s, 'fixture', %s, 2, '{}'::jsonb)
                RETURNING id
                """,
                (
                    SEEDED.organizationId,
                    case.source_id,
                    hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                ),
            )
        ).fetchone()
        assert row is not None
        return row["id"]


async def test_simultaneous_reservations_cannot_overspend() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        first, second = await asyncio.gather(
            operation(url, case, "first"), operation(url, case, "second")
        )
        outcomes = await asyncio.gather(
            reserve(url, case, first.id, "owner-first", 60),
            reserve(url, case, second.id, "owner-second", 60),
            return_exceptions=True,
        )
        assert sum(isinstance(item, ledger.Attempt) for item in outcomes) == 1
        assert sum(isinstance(item, ledger.BudgetExceeded) for item in outcomes) == 1
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT spent_micros, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            assert row == {"spent_micros": 0, "reserved_micros": 60}
    finally:
        await db.close_pool()


async def test_attempt_batch_reserves_all_members_and_preserves_request_order() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=200)
    try:
        first = await operation(url, case, "batch-first")
        second = await operation(url, case, "batch-second")
        attempts = await ledger.reserve_attempt_batch(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            requests=(
                batch_request(second.id, "owner-second", 70),
                batch_request(first.id, "owner-first", 60),
            ),
        )
        assert [attempt.operation_id for attempt in attempts] == [second.id, first.id]
        assert [attempt.attempt_number for attempt in attempts] == [1, 1]
        retried = await ledger.reserve_attempt_batch(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            requests=(
                batch_request(second.id, "replacement-second", 70),
                batch_request(first.id, "replacement-first", 60),
            ),
        )
        assert [attempt.id for attempt in retried] == [attempt.id for attempt in attempts]
        assert [attempt.owner_token for attempt in retried] == [
            "replacement-second",
            "replacement-first",
        ]
        async with db.scoped(url, SEEDED) as conn:
            aggregate = await (
                await conn.execute(
                    """
                    SELECT reserved_micros, dispatch_count FROM harness_run WHERE id = %s
                    """,
                    (case.run_id,),
                )
            ).fetchone()
            rows = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS attempts,
                           count(r.id)::int AS reservations
                      FROM harness_attempt a
                      LEFT JOIN harness_reservation r ON r.attempt_id = a.id
                     WHERE a.run_id = %s
                    """,
                    (case.run_id,),
                )
            ).fetchone()
        assert aggregate == {"reserved_micros": 130, "dispatch_count": 0}
        assert rows == {"attempts": 2, "reservations": 2}
    finally:
        await db.close_pool()


async def test_attempt_batch_insufficient_combined_budget_creates_nothing() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=100)
    try:
        first = await operation(url, case, "batch-budget-first")
        second = await operation(url, case, "batch-budget-second")
        with pytest.raises(ledger.BudgetExceeded, match="combined provider exposure"):
            await ledger.reserve_attempt_batch(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                requests=(
                    batch_request(first.id, "owner-first", 60),
                    batch_request(second.id, "owner-second", 60),
                ),
            )
        async with db.scoped(url, SEEDED) as conn:
            aggregate = await (
                await conn.execute(
                    """
                    SELECT reserved_micros, dispatch_count FROM harness_run WHERE id = %s
                    """,
                    (case.run_id,),
                )
            ).fetchone()
            rows = await (
                await conn.execute(
                    "SELECT count(*)::int AS count FROM harness_attempt WHERE run_id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert aggregate == {"reserved_micros": 0, "dispatch_count": 0}
        assert rows == {"count": 0}
    finally:
        await db.close_pool()


async def test_attempt_batch_reuses_succeeded_attempt_and_reserves_only_missing() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=200)
    try:
        completed_operation = await operation(url, case, "batch-completed")
        missing_operation = await operation(url, case, "batch-missing")
        completed_attempt = await reserve(url, case, completed_operation.id, ORIGINAL_OWNER, 40)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=completed_operation.id,
            attempt_id=completed_attempt.id,
            owner_token=ORIGINAL_OWNER,
            dispatch_limit=5,
        )
        artifact_id = await result_artifact(url, case)
        await ledger.complete_attempt(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=completed_operation.id,
            attempt_id=completed_attempt.id,
            owner_token=ORIGINAL_OWNER,
            result_artifact_id=artifact_id,
            usage={},
            actual_cost_micros=30,
        )
        attempts = await ledger.reserve_attempt_batch(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            requests=(
                batch_request(completed_operation.id, "new-owner", 40),
                batch_request(missing_operation.id, "missing-owner", 50),
            ),
        )
        assert attempts[0].id == completed_attempt.id
        assert attempts[0].owner_token == ORIGINAL_OWNER
        assert attempts[0].state == "succeeded"
        assert attempts[1].operation_id == missing_operation.id
        assert attempts[1].state == "reserved"
        async with db.scoped(url, SEEDED) as conn:
            aggregate = await (
                await conn.execute(
                    "SELECT spent_micros, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert aggregate == {"spent_micros": 30, "reserved_micros": 50}
    finally:
        await db.close_pool()


async def test_concurrent_attempt_batches_cannot_overbook_dispatch_ceiling() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=1_000)
    try:
        operations = await asyncio.gather(
            *(operation(url, case, f"batch-limit-{index}") for index in range(4))
        )

        async def reserve_pair(offset: int) -> tuple[ledger.Attempt, ...]:
            return await ledger.reserve_attempt_batch(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                requests=tuple(
                    batch_request(
                        operations[index].id,
                        f"batch-owner-{index}",
                        10,
                        dispatch_limit=2,
                    )
                    for index in range(offset, offset + 2)
                ),
            )

        outcomes = await asyncio.gather(reserve_pair(0), reserve_pair(2), return_exceptions=True)
        assert sum(isinstance(result, tuple) for result in outcomes) == 1
        assert sum(isinstance(result, ledger.DispatchLimitExceeded) for result in outcomes) == 1
        async with db.scoped(url, SEEDED) as conn:
            rows = await (
                await conn.execute(
                    "SELECT count(*)::int AS count FROM harness_attempt WHERE run_id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            aggregate = await (
                await conn.execute(
                    "SELECT reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert rows == {"count": 2}
        assert aggregate == {"reserved_micros": 20}
    finally:
        await db.close_pool()


async def test_attempt_batch_identity_refusal_rolls_back_reserved_owner_transfer() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=200)
    try:
        reserved_operation = await operation(url, case, "batch-reclaim")
        conflicting_operation = await operation(url, case, "batch-conflict")
        original = await reserve(url, case, reserved_operation.id, ORIGINAL_OWNER, 20)
        conflicting = await reserve(url, case, conflicting_operation.id, "conflict-owner", 30)
        conflict_request = batch_request(conflicting_operation.id, "replacement-conflict-owner", 31)
        with pytest.raises(ledger.IdentityConflict, match="exact request identity"):
            await ledger.reserve_attempt_batch(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                requests=(
                    batch_request(reserved_operation.id, REPLACEMENT_OWNER, 20),
                    conflict_request,
                ),
            )
        async with db.scoped(url, SEEDED) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, owner_token FROM harness_attempt
                     WHERE id = ANY(%s) ORDER BY id
                    """,
                    ([original.id, conflicting.id],),
                )
            ).fetchall()
        assert {row["id"]: row["owner_token"] for row in rows} == {
            original.id: ORIGINAL_OWNER,
            conflicting.id: "conflict-owner",
        }
    finally:
        await db.close_pool()


async def test_attempt_batch_unknown_member_fences_every_missing_member() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=200)
    try:
        unknown_operation = await operation(url, case, "batch-unknown")
        missing_operation = await operation(url, case, "batch-after-unknown")
        unknown = await reserve(url, case, unknown_operation.id, OWNER, 20)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=unknown_operation.id,
            attempt_id=unknown.id,
            owner_token=OWNER,
            dispatch_limit=5,
        )
        await ledger.fail_attempt(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=unknown_operation.id,
            attempt_id=unknown.id,
            owner_token=OWNER,
            outcome_known=False,
            actual_cost_micros=None,
            usage={},
            error_code="reply-lost",
            error_message="provider outcome is unknown",
        )
        with pytest.raises(ledger.OutcomeUnknown, match="reconciled"):
            await ledger.reserve_attempt_batch(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                requests=(
                    batch_request(unknown_operation.id, OWNER, 20),
                    batch_request(missing_operation.id, "missing-owner", 30),
                ),
            )
        async with db.scoped(url, SEEDED) as conn:
            attempts = await (
                await conn.execute(
                    "SELECT count(*)::int AS count FROM harness_attempt WHERE run_id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            run = await (
                await conn.execute(
                    "SELECT reserved_micros, status FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert attempts == {"count": 1}
        assert run == {"reserved_micros": 20, "status": "outcome_unknown"}
    finally:
        await db.close_pool()


async def test_release_and_dispatch_cannot_both_win() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "race")
        attempt = await reserve(url, case, logical.id, OWNER, 40)

        async def dispatch() -> bool:
            return await ledger.mark_dispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=attempt.id,
                owner_token=OWNER,
                dispatch_limit=5,
            )

        async def release() -> bool:
            return await ledger.release_undispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=attempt.id,
                owner_token=OWNER,
            )

        outcomes = await asyncio.gather(
            dispatch(),
            release(),
            return_exceptions=True,
        )
        assert sum(item is True for item in outcomes) == 1
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute("SELECT state FROM harness_attempt WHERE id = %s", (attempt.id,))
            ).fetchone()
            assert row is not None
            assert row["state"] in {"dispatching", "cancelled_confirmed"}
    finally:
        await db.close_pool()


async def test_lost_dispatch_reply_cannot_redispatch_or_change_owner() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "lost-reply")
        attempt = await reserve(url, case, logical.id, ORIGINAL_OWNER, 20)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=ORIGINAL_OWNER,
            dispatch_limit=5,
        )
        assert not await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=ORIGINAL_OWNER,
            dispatch_limit=5,
        )
        with pytest.raises(ledger.LostOwnership):
            await reserve(url, case, logical.id, "replacement", 20)
    finally:
        await db.close_pool()


async def test_reserved_attempt_is_reclaimed_after_owner_crash() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "reserved-owner-crash")
        original = await reserve(url, case, logical.id, ORIGINAL_OWNER, 20)
        replacement = await reserve(url, case, logical.id, REPLACEMENT_OWNER, 20)
        assert replacement.id == original.id
        assert replacement.owner_token == REPLACEMENT_OWNER
        with pytest.raises(ledger.LostOwnership):
            await ledger.mark_dispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=original.id,
                owner_token=ORIGINAL_OWNER,
                dispatch_limit=5,
            )
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=replacement.id,
            owner_token=REPLACEMENT_OWNER,
            dispatch_limit=5,
        )
    finally:
        await db.close_pool()


async def test_reserved_reclaim_racing_original_dispatch_has_one_winner() -> None:
    url = pipeline_url()
    case = await make_case(url)
    replacement_owner = REPLACEMENT_OWNER
    try:
        logical = await operation(url, case, "reserved-reclaim-race")
        original = await reserve(url, case, logical.id, ORIGINAL_OWNER, 20)

        async def original_dispatch() -> bool:
            return await ledger.mark_dispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=original.id,
                owner_token=ORIGINAL_OWNER,
                dispatch_limit=5,
            )

        async def reclaim_and_dispatch() -> bool:
            replacement = await reserve(url, case, logical.id, replacement_owner, 20)
            return await ledger.mark_dispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=replacement.id,
                owner_token=replacement_owner,
                dispatch_limit=5,
            )

        outcomes = await asyncio.gather(
            original_dispatch(), reclaim_and_dispatch(), return_exceptions=True
        )
        assert sum(item is True for item in outcomes) == 1
        assert sum(isinstance(item, ledger.LostOwnership) for item in outcomes) == 1
        async with db.scoped(url, SEEDED) as conn:
            attempt = await (
                await conn.execute(
                    "SELECT state, owner_token FROM harness_attempt WHERE id = %s",
                    (original.id,),
                )
            ).fetchone()
            run = await (
                await conn.execute(
                    "SELECT dispatch_count FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert attempt is not None
        assert attempt["state"] == "dispatching"
        assert attempt["owner_token"] in {ORIGINAL_OWNER, replacement_owner}
        assert run == {"dispatch_count": 1}
    finally:
        await db.close_pool()


async def test_reserved_attempt_cannot_dispatch_after_run_cancellation() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "cancelled-after-reserve")
        attempt = await reserve(url, case, logical.id, OWNER, 20)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'cancelled' WHERE id = %s", (case.run_id,)
            )
        with pytest.raises(ledger.IdentityConflict, match="cannot dispatch"):
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
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT state, dispatched_at FROM harness_attempt WHERE id = %s", (attempt.id,)
                )
            ).fetchone()
            assert row == {"state": "reserved", "dispatched_at": None}
    finally:
        await db.close_pool()


async def test_reserved_attempt_cannot_dispatch_after_known_overage() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        charged_operation, waiting_operation = await asyncio.gather(
            operation(url, case, "charged"), operation(url, case, "waiting")
        )
        charged = await reserve(url, case, charged_operation.id, CHARGED_OWNER, 10)
        waiting = await reserve(url, case, waiting_operation.id, WAITING_OWNER, 40)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=charged_operation.id,
            attempt_id=charged.id,
            owner_token=CHARGED_OWNER,
            dispatch_limit=5,
        )
        await ledger.complete_attempt(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=charged_operation.id,
            attempt_id=charged.id,
            owner_token=CHARGED_OWNER,
            result_artifact_id=await result_artifact(url, case),
            usage={},
            actual_cost_micros=70,
        )
        with pytest.raises(ledger.BudgetExceeded, match="became over budget"):
            await ledger.mark_dispatched(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=waiting_operation.id,
                attempt_id=waiting.id,
                owner_token=WAITING_OWNER,
                dispatch_limit=5,
            )
        async with db.scoped(url, SEEDED) as conn:
            run = await (
                await conn.execute(
                    "SELECT spent_micros, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            assert run == {"spent_micros": 70, "reserved_micros": 40}
    finally:
        await db.close_pool()


async def test_pending_never_dispatched_operation_can_complete_from_verified_cache() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "cached")
        fingerprint = hashlib.sha256(b"semantic-cache-result").hexdigest()
        async with db.scoped(url, SEEDED) as conn:
            artifact = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'speech_checkpoint', %s, 'fixture/cache', %s, 2,
                            '{}'::jsonb)
                    RETURNING id
                    """,
                    (SEEDED.organizationId, case.source_id, fingerprint, "a" * 64),
                )
            ).fetchone()
        assert artifact is not None
        completed = await ledger.complete_operation_from_artifact(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            result_artifact_id=artifact["id"],
            expected_artifact_kind="speech_checkpoint",
            expected_artifact_fingerprint=fingerprint,
        )
        repeated = await ledger.complete_operation_from_artifact(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            result_artifact_id=artifact["id"],
            expected_artifact_kind="speech_checkpoint",
            expected_artifact_fingerprint=fingerprint,
        )
        assert completed.status == "succeeded"
        assert repeated == completed
        assert completed.result_artifact_id == artifact["id"]
    finally:
        await db.close_pool()


async def test_budgeted_model_persists_then_reuses_response_without_second_call(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    url = pipeline_url()
    case = await make_case(url, budget=1_000_000)
    calls = 0

    async def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        _ = messages, info
        calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    '{"sections":[{"firstSentenceId":"s1","id":"chapter-1",'
                    '"kind":"keep","lastSentenceId":"s1","quoteWordIds":["w1"],'
                    '"reason":"complete","title":"Opening"}],"summary":"One chapter",'
                    '"version":1}'
                )
            ],
            model_name="fixture-model",
            provider_name="fixture-provider",
            provider_response_id="fixture-generation",
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        _ = route
        return FunctionModel(response, model_name="fixture-model")

    candidate = RouteEntry(
        id="fixture-route",
        gateway_model="fixture/model",
        family="fixture-family",
        provider="fixture-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=1024,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="b" * 64,
            probed_at=date(2026, 9, 8),
        ),
        prices=RoutePrices(input=0, output=0),
    )
    store = MemoryStore()
    runtime = ModelRuntime(
        database_url=url,
        store=cast("S3Store", store),
        cassette_store=CassetteStore(tmp_path),
        model_factory=model_factory,
        allow_outside_activity=True,
    )
    deps = HarnessModelDeps(
        scope=SEEDED,
        source_id=case.source_id,
        run_id=case.run_id,
        stage="propose",
        program_version="program-v1",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        route=candidate,
        operation_inputs={"window": "all"},
        operation_config={"temperature": 0},
        dispatch_limit=5,
    )
    configure_model_runtime(runtime)
    try:
        first = await chapter_propose_v1.run("Return the proposal.", deps=deps)
        second = await chapter_propose_v1.run("Return the proposal.", deps=deps)
        assert first.output == second.output
        assert calls == 1
        async with db.scoped(url, SEEDED) as conn:
            operation_row = await (
                await conn.execute(
                    "SELECT status FROM harness_operation WHERE run_id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            artifacts_count = await (
                await conn.execute(
                    "SELECT count(*)::int AS count FROM harness_artifact WHERE source_id = %s",
                    (case.source_id,),
                )
            ).fetchone()
        assert operation_row == {"status": "succeeded"}
        assert artifacts_count == {"count": 1}

        crashed = False

        async def failure_hook(phase: str) -> None:
            nonlocal crashed
            if phase == "after_publication_before_settlement" and not crashed:
                crashed = True
                raise asyncio.CancelledError

        configure_model_runtime(
            ModelRuntime(
                database_url=url,
                store=cast("S3Store", store),
                cassette_store=CassetteStore(tmp_path),
                model_factory=model_factory,
                failure_hook=failure_hook,
                allow_outside_activity=True,
            )
        )
        crash_deps = deps.model_copy(
            update={"stage": "crash-recovery", "operation_inputs": {"window": "crash"}}
        )
        with pytest.raises(asyncio.CancelledError):
            await chapter_propose_v1.run("Return the proposal.", deps=crash_deps)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'cancelled' WHERE id = %s",
                (case.run_id,),
            )
            await conn.execute(
                "UPDATE harness_attempt SET owner_token = %s, state = 'running'"
                " WHERE run_id = %s"
                " AND state = 'outcome_unknown'",
                ("prior-workflow-activity-owner", case.run_id),
            )
        recovered = await chapter_propose_v1.run("Return the proposal.", deps=crash_deps)
        assert recovered.output == first.output
        assert calls == 2
        async with db.scoped(url, SEEDED) as conn:
            cancelled = await (
                await conn.execute("SELECT status FROM harness_run WHERE id = %s", (case.run_id,))
            ).fetchone()
        assert cancelled == {"status": "cancelled"}
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'running' WHERE id = %s",
                (case.run_id,),
            )

        lookup_calls = 0

        async def lookup_handler(request: httpx.Request) -> httpx.Response:
            nonlocal lookup_calls
            _ = request
            lookup_calls += 1
            if lookup_calls == 1:
                raise asyncio.CancelledError
            return httpx.Response(404)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lookup_handler)
        ) as lookup_client:
            configure_model_runtime(
                ModelRuntime(
                    database_url=url,
                    store=cast("S3Store", store),
                    cassette_store=CassetteStore(tmp_path),
                    gateway=GatewayConfig(api_key="test-key"),
                    model_factory=model_factory,
                    lookup_client=lookup_client,
                    allow_outside_activity=True,
                )
            )
            lookup_deps = deps.model_copy(
                update={
                    "stage": "lookup-recovery",
                    "operation_inputs": {"window": "lookup"},
                }
            )
            with pytest.raises(asyncio.CancelledError):
                await chapter_propose_v1.run("Return the proposal.", deps=lookup_deps)
            lookup_recovered = await chapter_propose_v1.run(
                "Return the proposal.", deps=lookup_deps
            )
        assert lookup_recovered.output == first.output
        assert lookup_calls == 2
        assert calls == 3
    finally:
        clear_model_runtime()
        await db.close_pool()


async def test_unknown_outcome_retains_exposure() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "unknown")
        attempt = await reserve(url, case, logical.id, OWNER, 80)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            dispatch_limit=5,
        )
        failed = await ledger.fail_attempt(
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
            error_code="timeout",
            error_message="reply lost at https://user:secret@gateway.example/generation?key=secret",
        )
        assert failed.state == "outcome_unknown"
        assert failed.actual_cost_micros is None
        async with db.scoped(url, SEEDED) as conn:
            exposure = await (
                await conn.execute(
                    """
                    SELECT r.reserved_micros, a.error_message, v.state
                      FROM harness_run r
                      JOIN harness_attempt a ON a.run_id = r.id
                      JOIN harness_reservation v ON v.attempt_id = a.id
                     WHERE a.id = %s
                    """,
                    (attempt.id,),
                )
            ).fetchone()
            assert exposure is not None
            assert exposure["reserved_micros"] == 80
            assert exposure["state"] == "active"
            assert "secret" not in exposure["error_message"]
    finally:
        await db.close_pool()


async def test_late_ambiguous_failure_does_not_overwrite_cancelled_run() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "cancel-before-ambiguous-failure")
        attempt = await reserve(url, case, logical.id, OWNER, 30)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            dispatch_limit=5,
        )
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'cancelled' WHERE id = %s",
                (case.run_id,),
            )
        failed = await ledger.fail_attempt(
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
            error_code="cancelled-after-dispatch",
            error_message="activity cancellation hid the provider outcome",
        )
        assert failed.state == "outcome_unknown"
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT status, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert row == {"status": "cancelled", "reserved_micros": 30}
    finally:
        await db.close_pool()


async def test_duplicate_completion_spends_once_and_overage_blocks_next_call() -> None:
    url = pipeline_url()
    case = await make_case(url)
    try:
        logical = await operation(url, case, "complete")
        attempt = await reserve(url, case, logical.id, OWNER, 40)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            dispatch_limit=5,
        )
        artifact_id = await result_artifact(url, case)

        async def complete() -> ledger.Attempt:
            return await ledger.complete_attempt(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=attempt.id,
                owner_token=OWNER,
                result_artifact_id=artifact_id,
                usage={"tokens": 3},
                actual_cost_micros=75,
            )

        first = await complete()
        second = await complete()
        assert first == second
        next_operation = await operation(url, case, "after-overage")
        with pytest.raises(ledger.BudgetExceeded):
            await reserve(url, case, next_operation.id, "next", 30)
        async with db.scoped(url, SEEDED) as conn:
            run = await (
                await conn.execute(
                    "SELECT spent_micros, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            count = await (
                await conn.execute(
                    """
                    SELECT count(*) AS n FROM usage_ledger
                     WHERE idempotency_key = %s
                    """,
                    (f"provider-cost:{attempt.id}",),
                )
            ).fetchone()
            assert run == {"spent_micros": 75, "reserved_micros": 0}
            assert count == {"n": 1}
    finally:
        await db.close_pool()


async def test_cost_reconciliation_is_exactly_idempotent_and_identity_scoped() -> None:
    url = pipeline_url()
    case = await make_case(url, budget=200)
    try:
        logical = await operation(url, case, "reconcile-cost")
        unrelated = await operation(url, case, "reconcile-cost-wrong-operation")
        attempt = await reserve(url, case, logical.id, OWNER, 100)
        assert await ledger.mark_dispatched(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            dispatch_limit=5,
        )
        artifact_id = await result_artifact(url, case)
        completed = await ledger.complete_attempt(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            run_id=case.run_id,
            operation_id=logical.id,
            attempt_id=attempt.id,
            owner_token=OWNER,
            result_artifact_id=artifact_id,
            usage={},
            actual_cost_micros=None,
        )
        assert completed.cost_status == "unknown"

        async def reconcile(
            *, cost: int = 73, usage: dict[str, object] | None = None
        ) -> ledger.Attempt:
            return await ledger.reconcile_cost(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=logical.id,
                attempt_id=attempt.id,
                owner_token=OWNER,
                observed_cost_micros=cost,
                usage=usage or {"inputTokens": 11, "outputTokens": 7},
            )

        first = await reconcile()
        replay = await reconcile()
        assert first == replay
        assert first.actual_cost_micros == 73
        assert first.cost_status == "reconciled"
        with pytest.raises(ledger.IdentityConflict, match="differing post-reconciliation charge"):
            await reconcile(cost=74)
        with pytest.raises(ledger.IdentityConflict, match="different usage"):
            await reconcile(usage={"inputTokens": 11, "outputTokens": 8})
        with pytest.raises(ledger.IdentityConflict, match="terminal attempt identities"):
            await ledger.reconcile_cost(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                run_id=case.run_id,
                operation_id=unrelated.id,
                attempt_id=attempt.id,
                owner_token=OWNER,
                observed_cost_micros=73,
                usage={"inputTokens": 11, "outputTokens": 7},
            )
        async with db.scoped(url, SEEDED) as conn:
            aggregate = await (
                await conn.execute(
                    "SELECT spent_micros, reserved_micros FROM harness_run WHERE id = %s",
                    (case.run_id,),
                )
            ).fetchone()
            entries = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM usage_ledger WHERE idempotency_key = %s",
                    (f"provider-cost:{attempt.id}",),
                )
            ).fetchone()
        assert aggregate == {"spent_micros": 73, "reserved_micros": 0}
        assert entries == {"n": 1}
    finally:
        await db.close_pool()


async def test_artifact_identity_lineage_and_verified_reads(tmp_path: Path) -> None:
    url = pipeline_url()
    case = await make_case(url)
    store = cast("S3Store", MemoryStore())
    try:
        evidence = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(
                kind="proposal",
                fingerprint=artifacts.fingerprint_for(kind="proposal", inputs={"n": 1}, config={}),
            ),
            content={"version": 1},
            metadata={"producer": "test"},
        )
        edit_identity = artifacts.ArtifactIdentity(
            kind="edit",
            fingerprint=artifacts.fingerprint_for(kind="edit", inputs={"n": 2}, config={}),
        )
        edit = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=edit_identity,
            content={"version": 1, "title": "Témnia"},
            metadata={},
            dependency_ids=[evidence.id],
        )
        duplicate = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=edit_identity,
            content={"title": "Témnia", "version": 1},
            metadata={},
            dependency_ids=[evidence.id],
        )
        assert edit == duplicate
        assert await artifacts.read_artifact_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            artifact_id=edit.id,
        ) == {"title": "Témnia", "version": 1}
        media_path = tmp_path / "chapter.mp4"
        media_path.write_bytes(b"media-bytes")
        media = await artifacts.publish_file(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(kind="render", fingerprint="c" * 64),
            path=media_path,
            content_type="video/mp4",
            suffix=".mp4",
            metadata={},
            dependency_ids=[edit.id],
        )
        restored = tmp_path / "restored.mp4"
        assert await artifacts.read_artifact_file(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            artifact_id=media.id,
            destination=restored,
        ) == len(b"media-bytes")
        assert restored.read_bytes() == b"media-bytes"
        with pytest.raises(ledger.IdentityConflict):
            await artifacts.publish_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                identity=edit_identity,
                content={"version": 2},
                metadata={},
                dependency_ids=[evidence.id],
            )
        await storage.upload_bytes(store, edit.storage_key, b"{}", "application/json")
        with pytest.raises(artifacts.ArtifactIntegrityError):
            await artifacts.read_artifact_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                artifact_id=edit.id,
            )
    finally:
        await db.close_pool()


async def test_identical_evidence_is_reused_by_a_second_run() -> None:
    """Run membership is a consumer pointer, not part of immutable evidence identity."""
    url = pipeline_url()
    case = await make_case(url)
    store = cast("S3Store", MemoryStore())
    transcript_id: uuid.UUID
    second_run_id: uuid.UUID
    try:
        async with db.scoped(url, SEEDED) as conn:
            transcript = await (
                await conn.execute(
                    """
                    INSERT INTO transcript
                        (organization_id, source_id, status, current_revision)
                    VALUES (%s, %s, 'ready', 1)
                    RETURNING id
                    """,
                    (SEEDED.organizationId, case.source_id),
                )
            ).fetchone()
            assert transcript is not None
            transcript_id = transcript["id"]
            await conn.execute(
                """
                INSERT INTO transcript_revision
                    (organization_id, transcript_id, revision, kind, storage_key,
                     size_bytes, word_count)
                VALUES (%s, %s, 1, 'machine', %s, 10, 1)
                """,
                (
                    SEEDED.organizationId,
                    transcript_id,
                    f"transcripts/{transcript_id}/1.json",
                ),
            )
            await conn.execute(
                """
                UPDATE harness_run SET stage = 'evidence', status = 'pending'
                 WHERE id = %s
                """,
                (case.run_id,),
            )
            second = await (
                await conn.execute(
                    """
                    INSERT INTO harness_run
                        (organization_id, source_id, request_key, lane, budget_micros,
                         config, route_snapshot, stage, status)
                    VALUES (%s, %s, %s, 'chapters', 100, '{}'::jsonb, '{}'::jsonb,
                            'evidence', 'pending')
                    RETURNING id
                    """,
                    (SEEDED.organizationId, case.source_id, str(uuid.uuid4())),
                )
            ).fetchone()
            assert second is not None
            second_run_id = second["id"]

        dependency = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(kind="proposal", fingerprint="d" * 64),
            content={"version": 1},
            metadata={"format": "test-dependency/1"},
        )
        identity = artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint="e" * 64,
            transcript_id=transcript_id,
            transcript_revision=1,
        )
        stable_metadata = {
            "format": "harness-evidence/1",
            "sourceFingerprint": "f" * 64,
            "sourceSha256": "a" * 64,
            "transcriptSha256": "b" * 64,
        }
        first = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=identity,
            content={"sourceId": str(case.source_id), "version": 1},
            metadata={**stable_metadata, "runId": str(case.run_id)},
        )
        reused = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=identity,
            content={"sourceId": str(case.source_id), "version": 1},
            metadata=stable_metadata,
        )
        assert reused.id == first.id

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run SET evidence_artifact_id = %s
                 WHERE source_id = %s AND id = ANY(%s)
                """,
                (first.id, case.source_id, [case.run_id, second_run_id]),
            )
            artifact_count = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count FROM harness_artifact
                     WHERE source_id = %s AND kind = 'evidence'
                    """,
                    (case.source_id,),
                )
            ).fetchone()
            storage_count = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count FROM usage_ledger
                     WHERE idempotency_key = %s
                    """,
                    (f"harness-storage:{first.id}",),
                )
            ).fetchone()
            attached = await (
                await conn.execute(
                    """
                    SELECT id, evidence_artifact_id FROM harness_run
                     WHERE id = ANY(%s) ORDER BY id
                    """,
                    ([case.run_id, second_run_id],),
                )
            ).fetchall()
        assert artifact_count == {"count": 1}
        assert storage_count == {"count": 1}
        assert {row["evidence_artifact_id"] for row in attached} == {first.id}

        with pytest.raises(ledger.IdentityConflict, match="different content or lineage"):
            await artifacts.publish_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                identity=identity,
                content={"sourceId": str(case.source_id), "version": 2},
                metadata=stable_metadata,
            )
        with pytest.raises(ledger.IdentityConflict, match="different content or lineage"):
            await artifacts.publish_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                identity=identity,
                content={"sourceId": str(case.source_id), "version": 1},
                metadata={**stable_metadata, "sourceSha256": "c" * 64},
            )
        with pytest.raises(ledger.IdentityConflict, match="different content or lineage"):
            await artifacts.publish_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                identity=identity,
                content={"sourceId": str(case.source_id), "version": 1},
                metadata=stable_metadata,
                dependency_ids=[dependency.id],
            )
    finally:
        await db.close_pool()


async def test_scope_blind_dependency_cannot_cross_sources() -> None:
    url = pipeline_url()
    first = await make_case(url)
    second = await make_case(url)
    store = cast("S3Store", MemoryStore())
    try:
        foreign = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=second.source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(kind="proposal", fingerprint="a" * 64),
            content={},
            metadata={},
        )
        with pytest.raises(ledger.IdentityConflict):
            await artifacts.publish_json(
                url,
                scope=SEEDED,
                source_id=first.source_id,
                store=store,
                identity=artifacts.ArtifactIdentity(kind="edit", fingerprint="b" * 64),
                content={},
                metadata={},
                dependency_ids=[foreign.id],
            )
    finally:
        await db.close_pool()


async def test_benchmark_database_lease_refuses_concurrency_and_prior_run() -> None:
    """A second output directory cannot reset one experiment's durable dispatch cap."""
    url = pipeline_url()
    case = await make_case(url)
    experiment_id = f"lease-{uuid.uuid4().hex[:12]}"
    try:
        async with database_experiment_lease(
            url,
            organization_id=SEEDED.organizationId,
            experiment_id=experiment_id,
        ):
            with pytest.raises(RuntimeError, match="already running"):
                async with database_experiment_lease(
                    url,
                    organization_id=SEEDED.organizationId,
                    experiment_id=experiment_id,
                ):
                    pytest.fail("concurrent database lease unexpectedly opened")

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                INSERT INTO transcript
                    (organization_id, source_id, status, workflow_id)
                VALUES (%s, %s, 'pending', %s)
                """,
                (
                    SEEDED.organizationId,
                    case.source_id,
                    f"speech-benchmark-{experiment_id}-preflight-a",
                ),
            )
        with pytest.raises(ValueError, match="already contains"):
            async with database_experiment_lease(
                url,
                organization_id=SEEDED.organizationId,
                experiment_id=experiment_id,
            ):
                pytest.fail("prior admitted workflow did not fence the experiment")
    finally:
        await db.close_pool()
