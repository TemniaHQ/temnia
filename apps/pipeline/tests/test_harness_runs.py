"""Chapter run acquisition races against migrated PostgreSQL and RLS."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    Backend,
    ChapterReviewAction,
    ChapterReviewInput,
    ChapterReviewOutput,
    ChapterRunConfig,
    ChapterRunInput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessRunStatus,
    Scope,
    TopicEditorialPatchInput,
)
from temnia_pipeline.harness import ledger
from temnia_pipeline.harness.ledger import IdentityConflict, SourceDeleting
from temnia_pipeline.harness.queues import control_task_queue
from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
)
from temnia_pipeline.harness.runs import (
    RunStateConflict,
    accept_export,
    accept_initial_revision,
    apply_operational_review,
    claim_repair,
    commit_review_mutation,
    get_run,
    mark_run_failed,
    start_or_refetch_run,
    update_stage,
)
from temnia_pipeline.harness.runtime_types import (
    ClaimRepairRequest,
    CommitReviewMutationRequest,
    MarkRunFailedRequest,
    PreparedReviewMutation,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
    WorkflowIdentity,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.workflows import ChapterReviewWorkflow

if TYPE_CHECKING:
    from uuid import UUID

    from temnia_pipeline.harness.settings import TopicShotDetector

SEEDED = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)
CANCEL_TEST_OWNER = "cancel-test-owner"
PLANNING_RETRY_OWNER = "planning-retry-owner"


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL", "")
    if not url:
        pytest.fail("TEST_PIPELINE_DATABASE_URL is required; run races never skip")
    return url


def snapshot() -> RouteSnapshot:
    entry = RouteEntry(
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
    seats = {
        name: SeatRoutePool(route_ids=(entry.id,)) for name in ("propose", "summary", "verify")
    }
    payload: dict[str, Any] = {
        "routes": [entry.model_dump(mode="json")],
        "seats": {key: value.model_dump(mode="json") for key, value in seats.items()},
        "synthetic": True,
        "version": 1,
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return RouteSnapshot(
        version=1,
        snapshot_id=digest,
        routes=(entry,),
        seats=seats,
        synthetic=True,
    )


def settings(value: RouteSnapshot) -> HarnessSettings:
    return HarnessSettings(
        enabled=True,
        backend="recorded",
        route_snapshot_id=value.snapshot_id,
        route_snapshot_path=Path("fixture.json"),
        allow_recorded=True,
        max_run_budget_micros=10_000_000,
        max_dispatches=32,
        max_repairs=1,
        max_output_tokens=8192,
        evidence_window_sentences=80,
        max_render_concurrency=2,
        gateway_api_key=None,
    )


async def ready_source(url: str) -> UUID:
    source_id = uuid.uuid4()
    transcript_id = uuid.uuid4()
    prefix = f"org/{SEEDED.organizationId}/source/{source_id}/"
    async with db.scoped(url, SEEDED) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SEEDED.organizationId, f"run-test-{uuid.uuid4()}"),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, duration_ms, status)
            VALUES (%s, %s, %s, 'source', 'source.mp4', 'video/mp4', 100, %s, 10000,
                    'ready')
            """,
            (source_id, SEEDED.organizationId, project["id"], prefix + "master.mp4"),
        )
        await conn.execute(
            """
            INSERT INTO transcript
                (id, organization_id, source_id, status, current_revision)
            VALUES (%s, %s, %s, 'ready', 1)
            """,
            (transcript_id, SEEDED.organizationId, source_id),
        )
        await conn.execute(
            """
            INSERT INTO transcript_revision
                (organization_id, transcript_id, revision, kind, storage_key,
                 size_bytes, word_count, metadata)
            VALUES (%s, %s, 1, 'machine', %s, 50, 5, %s::jsonb)
            """,
            (
                SEEDED.organizationId,
                transcript_id,
                prefix + "transcript/rev-1.json",
                json.dumps({"sha256": "b" * 64}),
            ),
        )
    return source_id


def start_request(source_id: UUID, value: RouteSnapshot) -> StartRunRequest:
    config = ChapterRunConfig(
        backend=Backend.recorded,
        evidenceWindowSentences=80,
        maxDispatches=32,
        maxOutputTokens=8192,
        maxRenderConcurrency=2,
        maxRepairs=1,
        routeSnapshotId=value.snapshot_id,
    )
    return StartRunRequest(
        request=ChapterRunInput(
            brief="Preserve the full discussion.",
            budgetMicros=1_000_000,
            config=config,
            requestKey=uuid.uuid4(),
            runId=uuid.uuid4(),
            scope=SEEDED,
            sourceId=source_id,
        ),
        workflow=WorkflowIdentity(workflow_id="chapter/test", workflow_run_id="run/test"),
    )


def retry_request(*, source_id: UUID, run_id: UUID) -> ChapterReviewInput:
    return ChapterReviewInput(
        action=ChapterReviewAction.retry,
        baseRevision=0,
        boundaryId=None,
        budgetMicros=None,
        mutationKey=uuid.uuid4(),
        otherSectionId=None,
        reason="Revalidate saved planning results.",
        runId=run_id,
        scope=SEEDED,
        sectionId=None,
        sourceId=source_id,
        targetRevision=None,
        targetTimeMs=None,
    )


async def mark_planning_review(
    url: str,
    *,
    source_id: UUID,
    run_id: UUID,
    error_message: str = "known planning refusal",
) -> RunSnapshot:
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            """
            UPDATE harness_run SET status = 'running', stage = 'planning'
             WHERE id = %s AND source_id = %s
            """,
            (run_id, source_id),
        )
    return await update_stage(
        url,
        StageUpdate(
            scope_organization_id=SEEDED.organizationId,
            scope_user_id=SEEDED.userId,
            source_id=source_id,
            run_id=run_id,
            expected_stage="planning",
            expected_revision=0,
            next_stage="needs_review",
            status=HarnessRunStatus.needs_review,
            error_message=error_message,
        ),
    )


@workflow.defn(name="ChapterRunWorkflow")
class BudgetResumeCaptureWorkflow:
    """Test child proving the operational command actually resumes the same run."""

    @workflow.run
    async def run(self, request: ChapterRunInput) -> None:
        await workflow.execute_activity(
            "capture_budget_resume",
            request,
            start_to_close_timeout=timedelta(seconds=10),
        )


class BudgetPauseActivities:
    """Real ledger exhaustion behind the actual review workflow failure mapping."""

    def __init__(self, database_url: str, source_id: UUID, run_id: UUID) -> None:
        self.database_url = database_url
        self.source_id = source_id
        self.run_id = run_id
        self.resume_request: ChapterRunInput | None = None
        self.resumed = asyncio.Event()

    @activity.defn(name="prepare_chapter_review")
    async def exhaust_budget(self, _request: ChapterReviewInput) -> PreparedReviewMutation:
        info = activity.info()
        async with db.scoped(self.database_url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET workflow_id = %s, workflow_run_id = %s,
                       status = 'running', budget_micros = 10
                 WHERE id = %s
                """,
                (info.workflow_id, info.workflow_run_id, self.run_id),
            )
        operation = await ledger.acquire_operation(
            self.database_url,
            scope=SEEDED,
            source_id=self.source_id,
            run_id=self.run_id,
            kind=ledger.OperationKind.MODEL,
            stage="actual-budget-exhaustion",
            inputs={"stage": "actual-budget-exhaustion"},
            config={"test": True},
        )
        await ledger.reserve_attempt(
            self.database_url,
            scope=SEEDED,
            source_id=self.source_id,
            run_id=self.run_id,
            operation_id=operation.operation.id,
            owner_token=f"{info.workflow_run_id}:{info.activity_id}",
            provider="recorded",
            model="fixture",
            family="fixture",
            route={"id": "fixture"},
            request_hash="e" * 64,
            estimated_cost_micros=11,
            dispatch_limit=32,
        )
        pytest.fail("budget reservation unexpectedly succeeded")

    @activity.defn(name="mark_chapter_run_failed")
    async def mark_failed(self, request: MarkRunFailedRequest) -> bool:
        return await mark_run_failed(self.database_url, request=request)

    @activity.defn(name="get_chapter_run")
    async def get_snapshot(self, request: RunRef) -> RunSnapshot:
        return await get_run(
            self.database_url,
            scope=SEEDED,
            source_id=request.source_id,
            run_id=request.run_id,
        )

    @activity.defn(name="apply_chapter_review")
    async def apply_review(self, request: ChapterReviewInput) -> ChapterReviewOutput:
        return await apply_operational_review(
            self.database_url,
            request=request,
            max_run_budget_micros=10_000_000,
        )

    @activity.defn(name="capture_budget_resume")
    async def capture_resume(self, request: ChapterRunInput) -> None:
        self.resume_request = request
        self.resumed.set()

    @activity.defn(name="cleanup_chapter_source_cache")
    async def cleanup(self, _request: RunRef) -> bool:
        return True


async def test_concurrent_duplicate_start_is_one_immutable_run() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        first, second = await asyncio.gather(
            start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value),
            start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value),
        )
        assert {first.created, second.created} == {False, True}
        assert first.run == second.run
        changed = start.model_copy(
            update={"request": start.request.model_copy(update={"brief": "Changed intent."})}
        )
        with pytest.raises(IdentityConflict, match="different immutable run intent"):
            await start_or_refetch_run(
                url, start=changed, settings=settings(value), route_snapshot=value
            )
    finally:
        await db.close_pool()


@pytest.mark.parametrize("original_policy", ["legacy", "chapter-editorial/1"])
async def test_editorial_policy_is_frozen_at_first_insert(original_policy: str) -> None:
    """A deployed continuation cannot upgrade or downgrade an existing run's semantics."""
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value).model_copy(update={"editorial_policy": original_policy})
    try:
        created = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        changed = start.model_copy(
            update={
                "editorial_policy": "chapter-editorial/1"
                if original_policy == "legacy"
                else "legacy"
            }
        )
        resumed = await start_or_refetch_run(
            url, start=changed, settings=settings(value), route_snapshot=value
        )
        assert created.run.editorial_policy == resumed.run.editorial_policy == original_policy
        assert resumed.created is False
        assert created.run.config == resumed.run.config == start.request.config
        assert created.run.brief == resumed.run.brief == start.request.brief
        assert created.run.request_key == resumed.run.request_key
    finally:
        await db.close_pool()


@pytest.mark.parametrize(
    ("policy", "initial_detector"),
    [
        ("standalone-topics/1", "pyscenedetect-adaptive"),
        ("standalone-topics/1", "scdet"),
        ("standalone-topics/2", "pyscenedetect-adaptive"),
        ("standalone-topics/2", "scdet"),
        ("legacy", "pyscenedetect-adaptive"),
        ("chapter-editorial/1", "pyscenedetect-adaptive"),
    ],
)
async def test_topic_detector_is_frozen_on_insert_and_historical_lanes_keep_scdet(
    policy: str, initial_detector: TopicShotDetector
) -> None:
    """New worker settings cannot reinterpret the detector chosen by an existing run."""
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value).model_copy(update={"editorial_policy": policy})
    initial_settings = replace(
        settings(value), topic_shot_detector=initial_detector, topic_selection_enabled=True
    )
    changed_settings = replace(
        initial_settings,
        topic_shot_detector="scdet"
        if initial_detector == "pyscenedetect-adaptive"
        else "pyscenedetect-adaptive",
    )
    try:
        created = await start_or_refetch_run(
            url, start=start, settings=initial_settings, route_snapshot=value
        )
        resumed = await start_or_refetch_run(
            url, start=start, settings=changed_settings, route_snapshot=value
        )
        loaded = await get_run(url, scope=SEEDED, source_id=source_id, run_id=start.request.runId)
        expected = initial_detector if policy.startswith("standalone-topics/") else "scdet"
        assert created.run.topic_shot_detector == resumed.run.topic_shot_detector == expected
        assert loaded.topic_shot_detector == expected
        assert resumed.created is False
        assert resumed.run.editorial_policy == created.run.editorial_policy == policy
        assert resumed.run.config == created.run.config == start.request.config
        async with db.scoped(url, SEEDED) as conn:
            retained = await (
                await conn.execute(
                    "SELECT route_snapshot FROM harness_run WHERE id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
        assert retained is not None
        if policy.startswith("standalone-topics/"):
            assert retained["route_snapshot"]["topicShotDetector"] == initial_detector
        else:
            assert "topicShotDetector" not in retained["route_snapshot"]
    finally:
        await db.close_pool()


@pytest.mark.parametrize(
    "original_policy",
    ["standalone-topics/1", "standalone-topics/2", "standalone-topics/3"],
)
async def test_topic_generation_is_exact_and_rollout_does_not_fence_existing_runs(
    original_policy: str,
) -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value).model_copy(update={"editorial_policy": original_policy})
    disabled = settings(value)
    enabled = replace(
        disabled,
        topic_selection_enabled=original_policy == "standalone-topics/2",
        topic_selection_v3_enabled=original_policy == "standalone-topics/3",
    )
    try:
        if original_policy in {"standalone-topics/2", "standalone-topics/3"}:
            with pytest.raises(IdentityConflict, match="disabled until deployment qualification"):
                await start_or_refetch_run(
                    url, start=start, settings=disabled, route_snapshot=value
                )
        created = await start_or_refetch_run(
            url, start=start, settings=enabled, route_snapshot=value
        )
        resumed = await start_or_refetch_run(
            url, start=start, settings=disabled, route_snapshot=value
        )
        assert resumed.created is False
        assert resumed.run.editorial_policy == created.run.editorial_policy == original_policy
        changed = start.model_copy(
            update={
                "editorial_policy": "standalone-topics/2"
                if original_policy == "standalone-topics/1"
                else "standalone-topics/1"
            }
        )
        with pytest.raises(IdentityConflict, match="incompatible editorial lanes"):
            await start_or_refetch_run(url, start=changed, settings=enabled, route_snapshot=value)
    finally:
        await db.close_pool()


async def test_known_failure_marks_only_the_active_owner_and_preserves_cancellation() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    ref = RunRef(
        scope_organization_id=SEEDED.organizationId,
        scope_user_id=SEEDED.userId,
        source_id=source_id,
        run_id=start.request.runId,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        marked = await mark_run_failed(
            url,
            request=MarkRunFailedRequest(
                run=ref,
                workflow=start.workflow,
                error_message="The budget cannot cover the next operation.",
                status="budget_paused",
            ),
        )
        assert marked
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "UPDATE harness_run SET status = 'cancelled' WHERE id = %s RETURNING status",
                    (start.request.runId,),
                )
            ).fetchone()
            assert row == {"status": "cancelled"}
        late = await mark_run_failed(
            url,
            request=MarkRunFailedRequest(
                run=ref,
                workflow=start.workflow,
                error_message="late activity failure",
                status="failed",
            ),
        )
        assert not late
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT status, error_message FROM harness_run WHERE id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
            assert row == {
                "status": "cancelled",
                "error_message": "The budget cannot cover the next operation.",
            }
    finally:
        await db.close_pool()


async def test_retry_clears_only_current_failure_and_keeps_review_history() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = 'failed', error_message = 'known planning failure'
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
        base = ChapterReviewInput(
            action=ChapterReviewAction.raise_budget,
            baseRevision=0,
            boundaryId=None,
            budgetMicros=2_000_000,
            mutationKey=uuid.uuid4(),
            otherSectionId=None,
            reason="Retain the known failure until retry is explicit.",
            runId=start.request.runId,
            scope=SEEDED,
            sectionId=None,
            sourceId=source_id,
            targetRevision=None,
            targetTimeMs=None,
        )
        raised = await apply_operational_review(
            url,
            request=base,
            max_run_budget_micros=10_000_000,
        )
        assert raised.state == "applied"
        after_raise = await get_run(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
        )
        assert after_raise.status == HarnessRunStatus.failed
        assert after_raise.error_message == "known planning failure"

        retry = base.model_copy(
            update={
                "action": ChapterReviewAction.retry,
                "budgetMicros": None,
                "mutationKey": uuid.uuid4(),
                "reason": "Retry the known failed work.",
            }
        )
        first = await apply_operational_review(
            url,
            request=retry,
            max_run_budget_micros=10_000_000,
        )
        duplicate = await apply_operational_review(
            url,
            request=retry,
            max_run_budget_micros=10_000_000,
        )
        assert first == duplicate
        assert first.state == "applied"
        after_retry = await get_run(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
        )
        assert after_retry.status == HarnessRunStatus.pending
        assert after_retry.error_message is None

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = 'outcome_unknown', error_message = 'provider outcome unresolved'
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
        refused = await apply_operational_review(
            url,
            request=retry.model_copy(update={"mutationKey": uuid.uuid4()}),
            max_run_budget_micros=10_000_000,
        )
        assert refused.state == "refused"
        after_refusal = await get_run(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
        )
        assert after_refusal.status == HarnessRunStatus.outcome_unknown
        assert after_refusal.error_message == "provider outcome unresolved"
        async with db.scoped(url, SEEDED) as conn:
            events = await (
                await conn.execute(
                    """
                    SELECT action, state, result
                      FROM chapter_review_event
                     WHERE run_id = %s
                     ORDER BY created_at
                    """,
                    (start.request.runId,),
                )
            ).fetchall()
        assert [(str(row["action"]), str(row["state"])) for row in events] == [
            ("raise_budget", "applied"),
            ("retry", "applied"),
            ("retry", "refused"),
        ]
        assert ChapterReviewOutput.model_validate(events[1]["result"]) == first
    finally:
        await db.close_pool()


async def test_revision_zero_planning_review_retries_same_run_from_evidence() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        operation = await ledger.acquire_operation(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            kind=ledger.OperationKind.MODEL,
            stage="summary:window-0000",
            inputs={"window": "window-0000"},
            config={"policy": "fixture"},
        )
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET spent_micros = 123, dispatch_count = 1
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
        await mark_planning_review(
            url,
            source_id=source_id,
            run_id=start.request.runId,
            error_message="known summary coverage refusal",
        )
        async with db.scoped(url, SEEDED) as conn:
            before = await (
                await conn.execute(
                    """
                    SELECT id, request_key, budget_micros, spent_micros, reserved_micros,
                           dispatch_count, repair_count, config, route_snapshot,
                           workflow_id, workflow_run_id
                      FROM harness_run WHERE id = %s
                    """,
                    (start.request.runId,),
                )
            ).fetchone()
            operation_before = await (
                await conn.execute(
                    "SELECT * FROM harness_operation WHERE id = %s",
                    (operation.operation.id,),
                )
            ).fetchone()
        assert before is not None
        assert operation_before is not None

        request = retry_request(source_id=source_id, run_id=start.request.runId)
        applied, duplicate = await asyncio.gather(
            apply_operational_review(url, request=request, max_run_budget_micros=10_000_000),
            apply_operational_review(url, request=request, max_run_budget_micros=10_000_000),
        )
        assert applied == duplicate
        assert applied.state == "applied"
        assert "revalidated" in applied.message

        async with db.scoped(url, SEEDED) as conn:
            after = await (
                await conn.execute(
                    """
                    SELECT id, request_key, budget_micros, spent_micros, reserved_micros,
                           dispatch_count, repair_count, config, route_snapshot,
                           workflow_id, workflow_run_id, status, stage, error_message
                      FROM harness_run WHERE id = %s
                    """,
                    (start.request.runId,),
                )
            ).fetchone()
            operation_after = await (
                await conn.execute(
                    "SELECT * FROM harness_operation WHERE id = %s",
                    (operation.operation.id,),
                )
            ).fetchone()
            event_count = await (
                await conn.execute(
                    "SELECT count(*)::int AS count FROM chapter_review_event WHERE run_id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
        assert after is not None
        assert {key: after[key] for key in before} == before
        assert after["status"] == "pending"
        assert after["stage"] == "evidence"
        assert after["error_message"] is None
        assert operation_after == operation_before
        assert event_count == {"count": 1}
    finally:
        await db.close_pool()


async def test_planning_review_retry_refuses_unresolved_execution_and_active_cost() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        operation = await ledger.acquire_operation(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            kind=ledger.OperationKind.MODEL,
            stage="summary:window-0000",
            inputs={"window": "window-0000"},
            config={"policy": "fixture"},
        )
        attempt = await ledger.reserve_attempt(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            operation_id=operation.operation.id,
            owner_token=PLANNING_RETRY_OWNER,
            provider="recorded",
            model="fixture",
            family="fixture",
            route={"id": "fixture"},
            request_hash="f" * 64,
            estimated_cost_micros=100,
            dispatch_limit=32,
        )
        await mark_planning_review(url, source_id=source_id, run_id=start.request.runId)

        reserved = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert reserved.state == "refused"
        assert "cost remains unresolved" in reserved.message

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_attempt SET state = 'succeeded' WHERE id = %s",
                (attempt.id,),
            )
        active_cost = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert active_cost.state == "refused"
        assert "cost remains unresolved" in active_cost.message

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_reservation
                   SET state = 'settled', settled_micros = 100, settled_at = now()
                 WHERE attempt_id = %s
                """,
                (attempt.id,),
            )
        counter_exposure = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert counter_exposure.state == "refused"
        assert "cost remains unresolved" in counter_exposure.message

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run SET reserved_micros = 0, spent_micros = 100
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
            await conn.execute(
                "UPDATE harness_attempt SET state = 'running' WHERE id = %s",
                (attempt.id,),
            )
        running = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert running.state == "refused"
        assert "execution" in running.message

        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_attempt
                   SET state = 'failed_known', actual_cost_micros = 100,
                       cost_status = 'reported', usage = '{}'::jsonb, finished_at = now()
                 WHERE id = %s
                """,
                (attempt.id,),
            )
            await conn.execute(
                "UPDATE harness_operation SET status = 'failed' WHERE id = %s",
                (operation.operation.id,),
            )
        safe = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert safe.state == "applied"
    finally:
        await db.close_pool()


@pytest.mark.parametrize(
    ("current_revision", "accepted_revision", "stage"),
    [(1, None, "needs_review"), (0, 1, "needs_review"), (0, None, "planning")],
)
async def test_planning_review_retry_refuses_revision_acceptance_or_other_stage(
    current_revision: int,
    accepted_revision: int | None,
    stage: str,
) -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = 'needs_review', stage = %s, current_revision = %s,
                       accepted_revision = %s, error_message = 'must remain'
                 WHERE id = %s
                """,
                (stage, current_revision, accepted_revision, start.request.runId),
            )
        result = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId).model_copy(
                update={"baseRevision": current_revision}
            ),
            max_run_budget_micros=10_000_000,
        )
        assert result.state == "refused"
        after = await get_run(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
        )
        assert after.status == HarnessRunStatus.needs_review
        assert after.stage == stage
        assert after.error_message == "must remain"
    finally:
        await db.close_pool()


async def test_planning_review_retry_refuses_an_existing_edit_even_at_revision_zero() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        await mark_planning_review(url, source_id=source_id, run_id=start.request.runId)
        artifact_id = uuid.uuid4()
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                INSERT INTO harness_artifact
                    (id, organization_id, source_id, kind, fingerprint, sha256,
                     size_bytes, storage_key, metadata)
                VALUES (%s, %s, %s, 'edit', %s, %s, 2, %s, '{}'::jsonb)
                """,
                (
                    artifact_id,
                    SEEDED.organizationId,
                    source_id,
                    "1" * 64,
                    "2" * 64,
                    f"owned/{source_id}/edit.json",
                ),
            )
            await conn.execute(
                """
                INSERT INTO chapter_revision
                    (organization_id, source_id, run_id, revision, artifact_id,
                     base_revision, mutation_key)
                VALUES (%s, %s, %s, 1, %s, NULL, %s)
                """,
                (
                    SEEDED.organizationId,
                    source_id,
                    start.request.runId,
                    artifact_id,
                    str(uuid.uuid4()),
                ),
            )
            await conn.execute(
                """
                UPDATE harness_run
                   SET current_revision = 0, accepted_revision = NULL,
                       error_message = 'must remain'
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
        result = await apply_operational_review(
            url,
            request=retry_request(source_id=source_id, run_id=start.request.runId),
            max_run_budget_micros=10_000_000,
        )
        assert result.state == "refused"
        assert "without an edit" in result.message
    finally:
        await db.close_pool()


async def test_two_distinct_planning_retries_admit_only_one_transition() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        await mark_planning_review(url, source_id=source_id, run_id=start.request.runId)
        results = await asyncio.gather(
            *(
                apply_operational_review(
                    url,
                    request=retry_request(source_id=source_id, run_id=start.request.runId),
                    max_run_budget_micros=10_000_000,
                )
                for _ in range(2)
            )
        )
        assert sorted(str(result.state) for result in results) == ["applied", "refused"]
        after = await get_run(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
        )
        assert after.status == HarnessRunStatus.pending
        assert after.stage == "evidence"
    finally:
        await db.close_pool()


async def test_actual_budget_exhaustion_pauses_then_raise_resumes_same_run() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    activities = BudgetPauseActivities(url, source_id, start.request.runId)
    queue = f"chapter-budget-resume-{uuid.uuid4()}"
    failing_review = ChapterReviewInput(
        action=ChapterReviewAction.accept,
        baseRevision=0,
        boundaryId=None,
        budgetMicros=None,
        mutationKey=uuid.uuid4(),
        otherSectionId=None,
        reason="Exercise the actual budget boundary.",
        runId=start.request.runId,
        scope=SEEDED,
        sectionId="keep-0",
        sourceId=source_id,
        targetRevision=None,
        targetTimeMs=None,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with (
            await WorkflowEnvironment.start_time_skipping(
                plugins=[PydanticAIPlugin()]
            ) as environment,
            AsyncExitStack() as stack,
        ):
            await stack.enter_async_context(
                Worker(
                    environment.client,
                    task_queue=queue,
                    workflows=[ChapterReviewWorkflow, BudgetResumeCaptureWorkflow],
                    activities=[
                        activities.exhaust_budget,
                        activities.capture_resume,
                        activities.cleanup,
                    ],
                )
            )
            await stack.enter_async_context(
                Worker(
                    environment.client,
                    task_queue=control_task_queue(queue),
                    activities=[
                        activities.mark_failed,
                        activities.get_snapshot,
                        activities.apply_review,
                    ],
                )
            )
            with pytest.raises(WorkflowFailureError):
                await environment.client.execute_workflow(
                    ChapterReviewWorkflow.run,
                    failing_review,
                    id=f"budget-failure-{uuid.uuid4()}",
                    task_queue=queue,
                )
            paused = await get_run(
                url,
                scope=SEEDED,
                source_id=source_id,
                run_id=start.request.runId,
            )
            assert paused.status == HarnessRunStatus.budget_paused
            assert paused.error_message is not None
            assert paused.dispatch_count == 0
            raise_budget = failing_review.model_copy(
                update={
                    "action": ChapterReviewAction.raise_budget,
                    "budgetMicros": 100,
                    "mutationKey": uuid.uuid4(),
                    "sectionId": None,
                }
            )
            result = await environment.client.execute_workflow(
                ChapterReviewWorkflow.run,
                raise_budget,
                id=f"budget-raise-{uuid.uuid4()}",
                task_queue=queue,
            )
            assert result.state == "applied"
            await asyncio.wait_for(activities.resumed.wait(), timeout=10)
            resumed = activities.resume_request
            assert resumed is not None
            assert resumed.runId == start.request.runId
            assert resumed.requestKey == start.request.requestKey
            after = await get_run(
                url,
                scope=SEEDED,
                source_id=source_id,
                run_id=start.request.runId,
            )
            assert after.status == HarnessRunStatus.pending
            assert after.budget_micros == 100
            assert after.error_message is None
    finally:
        await db.close_pool()


async def test_source_deletion_fence_refuses_run_creation() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE source SET deletion_requested_at = now() WHERE id = %s",
                (source_id,),
            )
        with pytest.raises(SourceDeleting):
            await start_or_refetch_run(
                url, start=start, settings=settings(value), route_snapshot=value
            )
    finally:
        await db.close_pool()


async def test_duplicate_start_keeps_original_pins_after_source_advances() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        original = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        async with db.scoped(url, SEEDED) as conn:
            transcript = await (
                await conn.execute("SELECT id FROM transcript WHERE source_id = %s", (source_id,))
            ).fetchone()
            assert transcript is not None
            await conn.execute(
                """
                INSERT INTO transcript_revision
                    (organization_id, transcript_id, revision, kind, storage_key,
                     size_bytes, word_count, metadata)
                VALUES (%s, %s, 2, 'correction', %s, 60, 6, %s::jsonb)
                """,
                (
                    SEEDED.organizationId,
                    transcript["id"],
                    f"org/{SEEDED.organizationId}/source/{source_id}/transcript/rev-2.json",
                    json.dumps({"sha256": "c" * 64}),
                ),
            )
            await conn.execute(
                "UPDATE transcript SET current_revision = 2 WHERE id = %s",
                (transcript["id"],),
            )
            await conn.execute(
                "UPDATE harness_run SET budget_micros = 2000000 WHERE id = %s",
                (start.request.runId,),
            )
        retried = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        assert not retried.created
        assert retried.run.transcript == original.run.transcript
        assert retried.run.transcript.revision == 1
        assert retried.run.budget_micros == 2_000_000
    finally:
        await db.close_pool()


async def test_known_pending_run_is_claimed_by_one_new_workflow_execution() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        created = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        assert created.created
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = 'pending', error_message = 'failure from the prior execution'
                 WHERE id = %s
                """,
                (start.request.runId,),
            )
        contenders = tuple(
            start.model_copy(
                update={
                    "workflow": WorkflowIdentity(
                        workflow_id=f"chapter/resume/{index}",
                        workflow_run_id=f"resume-run/{index}",
                    )
                }
            )
            for index in range(2)
        )
        outcomes = cast(
            "tuple[StartRunResult | BaseException, ...]",
            await asyncio.gather(
                *(
                    start_or_refetch_run(
                        url,
                        start=contender,
                        settings=settings(value),
                        route_snapshot=value,
                    )
                    for contender in contenders
                ),
                return_exceptions=True,
            ),
        )
        claimed = [item for item in outcomes if isinstance(item, StartRunResult)]
        refused = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(claimed) == 1
        assert len(refused) == 1
        assert isinstance(refused[0], IdentityConflict)
        winner = claimed[0]
        assert winner.run.status == HarnessRunStatus.running
        assert winner.run.error_message is None
        assert winner.run.workflow_id in {item.workflow.workflow_id for item in contenders}
        assert winner.run.workflow_run_id in {item.workflow.workflow_run_id for item in contenders}
    finally:
        await db.close_pool()


async def test_stage_update_requires_revision_generation_token() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET stage = 'render', current_revision = 2 WHERE id = %s",
                (start.request.runId,),
            )
        stale = StageUpdate(
            scope_organization_id=SEEDED.organizationId,
            scope_user_id=SEEDED.userId,
            source_id=source_id,
            run_id=start.request.runId,
            expected_stage="render",
            expected_revision=1,
            next_stage="verify",
            status=HarnessRunStatus.running,
        )
        with pytest.raises(RunStateConflict):
            await update_stage(url, stale)
        current = await update_stage(url, stale.model_copy(update={"expected_revision": 2}))
        assert current.stage == "verify"
        assert current.current_revision == 2
    finally:
        await db.close_pool()


async def test_repair_claim_is_idempotent_bounded_and_owned_by_one_workflow() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    request = ClaimRepairRequest(
        expected_repair_count=0,
        run=RunRef(
            scope_organization_id=SEEDED.organizationId,
            scope_user_id=SEEDED.userId,
            source_id=source_id,
            run_id=start.request.runId,
        ),
        workflow=start.workflow,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'running', stage = 'planning' WHERE id = %s",
                (start.request.runId,),
            )
        claimed = await claim_repair(url, request=request, max_repairs=1)
        replay = await claim_repair(url, request=request, max_repairs=1)
        assert claimed.repair_count == replay.repair_count == 1
        with pytest.raises(RunStateConflict, match="stale workflow"):
            await claim_repair(
                url,
                request=request.model_copy(
                    update={
                        "workflow": WorkflowIdentity(
                            workflow_id="chapter/stale",
                            workflow_run_id="run/stale",
                        )
                    }
                ),
                max_repairs=1,
            )
        with pytest.raises(RunStateConflict, match="exhausted"):
            await claim_repair(
                url,
                request=request.model_copy(update={"expected_repair_count": 1}),
                max_repairs=1,
            )
    finally:
        await db.close_pool()


async def test_initial_revision_cas_is_idempotent_and_rejects_stale_edit() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            artifact_ids: list[UUID] = []
            for index in range(2):
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO harness_artifact
                            (organization_id, source_id, kind, fingerprint, storage_key,
                             sha256, size_bytes, metadata)
                        VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                        RETURNING id
                        """,
                        (
                            SEEDED.organizationId,
                            source_id,
                            hashlib.sha256(f"edit-{index}".encode()).hexdigest(),
                            f"org/{SEEDED.organizationId}/source/{source_id}/edit-{index}.json",
                            hashlib.sha256(f"body-{index}".encode()).hexdigest(),
                            json.dumps({"runId": str(start.request.runId)}),
                        ),
                    )
                ).fetchone()
                assert row is not None
                artifact_ids.append(row["id"])
        first, replay = await asyncio.gather(
            *(
                accept_initial_revision(
                    url,
                    scope=SEEDED,
                    source_id=source_id,
                    run_id=start.request.runId,
                    request_key=start.request.requestKey,
                    edit_artifact_id=artifact_ids[0],
                )
                for _ in range(2)
            )
        )
        assert first.current_revision == replay.current_revision == 1
        with pytest.raises(IdentityConflict, match="different edit"):
            await accept_initial_revision(
                url,
                scope=SEEDED,
                source_id=source_id,
                run_id=start.request.runId,
                request_key=start.request.requestKey,
                edit_artifact_id=artifact_ids[1],
            )
    finally:
        await db.close_pool()


async def test_cancel_review_is_persisted_and_idempotent() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    mutation_key = uuid.uuid4()
    review = ChapterReviewInput(
        action=ChapterReviewAction.cancel,
        baseRevision=0,
        boundaryId=None,
        budgetMicros=None,
        mutationKey=mutation_key,
        otherSectionId=None,
        reason="Stop this run.",
        runId=start.request.runId,
        scope=SEEDED,
        sectionId=None,
        sourceId=source_id,
        targetRevision=None,
        targetTimeMs=None,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        operation = await ledger.acquire_operation(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            kind=ledger.OperationKind.MODEL,
            stage="reserved-before-cancel",
            inputs={"stage": "reserved-before-cancel"},
            config={},
        )
        attempt = await ledger.reserve_attempt(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            operation_id=operation.operation.id,
            owner_token=CANCEL_TEST_OWNER,
            provider="recorded",
            model="synthetic",
            family="synthetic",
            route={"id": "synthetic"},
            request_hash="d" * 64,
            estimated_cost_micros=100,
            dispatch_limit=32,
        )
        first = await apply_operational_review(
            url,
            request=review,
            max_run_budget_micros=10_000_000,
        )
        second = await apply_operational_review(
            url,
            request=review,
            max_run_budget_micros=10_000_000,
        )
        assert first == second
        assert first.state == "applied"
        async with db.scoped(url, SEEDED) as conn:
            released = await (
                await conn.execute(
                    """
                    SELECT a.state AS attempt_state, r.state AS reservation_state,
                           h.reserved_micros
                      FROM harness_attempt a
                      JOIN harness_reservation r ON r.attempt_id = a.id
                      JOIN harness_run h ON h.id = a.run_id
                     WHERE a.id = %s
                    """,
                    (attempt.id,),
                )
            ).fetchone()
        assert released == {
            "attempt_state": "cancelled_confirmed",
            "reservation_state": "released",
            "reserved_micros": 0,
        }
        raise_budget = review.model_copy(
            update={
                "action": ChapterReviewAction.raise_budget,
                "budgetMicros": 2_000_000,
                "mutationKey": uuid.uuid4(),
            }
        )
        refused_raise = await apply_operational_review(
            url,
            request=raise_budget,
            max_run_budget_micros=10_000_000,
        )
        assert refused_raise.state == "refused"
        async with db.scoped(url, SEEDED) as conn:
            cancelled = await (
                await conn.execute(
                    "SELECT status, budget_micros FROM harness_run WHERE id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
        assert cancelled == {"status": "cancelled", "budget_micros": 1_000_000}
        changed = review.model_copy(update={"reason": "Different payload."})
        with pytest.raises(IdentityConflict, match="different payload"):
            await apply_operational_review(
                url,
                request=changed,
                max_run_budget_micros=10_000_000,
            )
    finally:
        await db.close_pool()


async def test_cancel_refuses_a_run_that_became_ready_under_the_locked_row() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    review = ChapterReviewInput(
        action=ChapterReviewAction.cancel,
        baseRevision=0,
        boundaryId=None,
        budgetMicros=None,
        mutationKey=uuid.uuid4(),
        otherSectionId=None,
        reason="Too late to cancel an accepted export.",
        runId=start.request.runId,
        scope=SEEDED,
        sectionId=None,
        sourceId=source_id,
        targetRevision=None,
        targetTimeMs=None,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            await conn.execute(
                "UPDATE harness_run SET status = 'ready', stage = 'ready' WHERE id = %s",
                (start.request.runId,),
            )
        result = await apply_operational_review(
            url,
            request=review,
            max_run_budget_micros=10_000_000,
        )
        assert result.state == "refused"
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT status, stage FROM harness_run WHERE id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
            assert row == {"status": "ready", "stage": "ready"}
    finally:
        await db.close_pool()


@pytest.mark.parametrize("editorial_patch", [False, True])
async def test_concurrent_review_revision_cas_persists_applied_and_conflict(
    *,
    editorial_patch: bool,
) -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        first_artifact: UUID | None = None
        candidates: list[
            tuple[ChapterReviewInput | TopicEditorialPatchInput, HarnessArtifactRef]
        ] = []
        async with db.scoped(url, SEEDED) as conn:
            initial = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                    RETURNING *
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"review-initial").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/initial.json",
                        hashlib.sha256(b"initial-body").hexdigest(),
                        json.dumps({"runId": str(start.request.runId)}),
                    ),
                )
            ).fetchone()
            assert initial is not None
            first_artifact = initial["id"]
        assert first_artifact is not None
        await accept_initial_revision(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            request_key=start.request.requestKey,
            edit_artifact_id=first_artifact,
        )
        async with db.scoped(url, SEEDED) as conn:
            for index in range(2):
                mutation = uuid.uuid4()
                command: ChapterReviewInput | TopicEditorialPatchInput = ChapterReviewInput(
                    action=ChapterReviewAction.restore,
                    baseRevision=1,
                    boundaryId=None,
                    budgetMicros=None,
                    mutationKey=mutation,
                    otherSectionId=None,
                    reason="Restore deliberate drop.",
                    runId=start.request.runId,
                    scope=SEEDED,
                    sectionId="drop-1",
                    sourceId=source_id,
                    targetRevision=None,
                    targetTimeMs=None,
                )
                if editorial_patch:
                    command = TopicEditorialPatchInput.model_validate(
                        {
                            "action": "topic_edit",
                            "version": 1,
                            "scope": SEEDED,
                            "runId": start.request.runId,
                            "sourceId": source_id,
                            "mutationKey": mutation,
                            "baseRevision": 1,
                            "baseEditSha256": hashlib.sha256(b"initial-body").hexdigest(),
                            "evidenceSha256": "a" * 64,
                            "reason": "The selected discussion does not deliver viewer value.",
                            "correctionActiveSeconds": None,
                            "correctionMeasurementMethod": None,
                            "operations": [
                                {
                                    "operationId": "remove",
                                    "kind": "drop",
                                    "affectedCandidateIds": ["weak"],
                                    "replacementCandidates": [],
                                }
                            ],
                        }
                    )
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO harness_artifact
                            (organization_id, source_id, kind, fingerprint, storage_key,
                             sha256, size_bytes, metadata)
                        VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                        RETURNING *
                        """,
                        (
                            SEEDED.organizationId,
                            source_id,
                            hashlib.sha256(f"candidate-{index}".encode()).hexdigest(),
                            f"org/{SEEDED.organizationId}/source/{source_id}/candidate-{index}.json",
                            hashlib.sha256(f"candidate-body-{index}".encode()).hexdigest(),
                            json.dumps(
                                {
                                    "format": "chapter-edit/1",
                                    "mutationKey": str(mutation),
                                    "revision": 2,
                                    "runId": str(start.request.runId),
                                }
                            ),
                        ),
                    )
                ).fetchone()
                assert row is not None
                candidates.append(
                    (
                        command,
                        HarnessArtifactRef(
                            id=row["id"],
                            kind=HarnessArtifactKind.edit,
                            fingerprint=row["fingerprint"],
                            sha256=row["sha256"],
                            sizeBytes=row["size_bytes"],
                            storageKey=row["storage_key"],
                        ),
                    )
                )

        async def commit(
            item: tuple[ChapterReviewInput | TopicEditorialPatchInput, HarnessArtifactRef],
        ) -> str:
            command, candidate = item
            result = await commit_review_mutation(
                url,
                CommitReviewMutationRequest(
                    prepared=PreparedReviewMutation(
                        request=command,
                        candidate=candidate,
                        message="Applied.",
                    ),
                    workflow=WorkflowIdentity(
                        workflow_id=f"chapter-review/{command.mutationKey}",
                        workflow_run_id=f"review-run/{command.mutationKey}",
                    ),
                ),
            )
            return str(result.output.state)

        states = await asyncio.gather(*(commit(item) for item in candidates))
        assert sorted(states) == ["applied", "conflict"]
        assert await asyncio.gather(*(commit(item) for item in candidates)) == states
        if editorial_patch:
            command, artifact = candidates[0]
            changed = command.model_copy(update={"reason": "A different correction."})
            with pytest.raises(IdentityConflict, match="different payload"):
                await commit((changed, artifact))
        async with db.scoped(url, SEEDED) as conn:
            run = await (
                await conn.execute(
                    "SELECT current_revision FROM harness_run WHERE id = %s",
                    (start.request.runId,),
                )
            ).fetchone()
            events = await (
                await conn.execute(
                    "SELECT state FROM chapter_review_event WHERE run_id = %s ORDER BY state",
                    (start.request.runId,),
                )
            ).fetchall()
        assert run == {"current_revision": 2}
        assert [str(row["state"]) for row in events] == ["applied", "conflict"]
    finally:
        await db.close_pool()


async def test_export_acceptance_is_current_revision_cas_and_idempotent() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    edit_sha = hashlib.sha256(b"accepted-edit").hexdigest()
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            edit = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"accepted-edit-fingerprint").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/accepted-edit.json",
                        edit_sha,
                        json.dumps({"runId": str(start.request.runId)}),
                    ),
                )
            ).fetchone()
            assert edit is not None
        await accept_initial_revision(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            request_key=start.request.requestKey,
            edit_artifact_id=edit["id"],
        )
        async with db.scoped(url, SEEDED) as conn:
            exported = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'export', %s, %s, %s, 2, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"accepted-export-fingerprint").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/export.json",
                        hashlib.sha256(b"accepted-export").hexdigest(),
                        json.dumps(
                            {
                                "editSha256": edit_sha,
                                "format": "chapter-export/1",
                                "revision": 1,
                                "runId": str(start.request.runId),
                            }
                        ),
                    ),
                )
            ).fetchone()
            assert exported is not None
        with pytest.raises(IdentityConflict, match="hash differs"):
            await accept_export(
                url,
                scope=SEEDED,
                source_id=source_id,
                run_id=start.request.runId,
                revision=1,
                edit_artifact_id=edit["id"],
                edit_sha256="f" * 64,
                export_artifact_id=exported["id"],
            )
        first = await accept_export(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            revision=1,
            edit_artifact_id=edit["id"],
            edit_sha256=edit_sha,
            export_artifact_id=exported["id"],
        )
        replay = await accept_export(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            revision=1,
            edit_artifact_id=edit["id"],
            edit_sha256=edit_sha,
            export_artifact_id=exported["id"],
        )
        assert first.status == replay.status == HarnessRunStatus.ready
        assert first.accepted_revision == replay.accepted_revision == 1
    finally:
        await db.close_pool()


async def test_reasoned_all_drop_revision_can_accept_an_empty_export_without_model_work() -> None:
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value)
    initial_sha = hashlib.sha256(b"initial-all-drop").hexdigest()
    accepted_sha = hashlib.sha256(b"accepted-all-drop").hexdigest()
    mutation_key = uuid.uuid4()
    command = ChapterReviewInput(
        action=ChapterReviewAction.accept,
        baseRevision=1,
        boundaryId=None,
        budgetMicros=None,
        mutationKey=mutation_key,
        otherSectionId=None,
        reason="The source is deliberate pre-roll and contains no publishable chapter.",
        runId=start.request.runId,
        scope=SEEDED,
        sectionId="drop-0",
        sourceId=source_id,
        targetRevision=None,
        targetTimeMs=None,
    )
    try:
        await start_or_refetch_run(url, start=start, settings=settings(value), route_snapshot=value)
        async with db.scoped(url, SEEDED) as conn:
            initial = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"initial-all-drop-fingerprint").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/all-drop-initial.json",
                        initial_sha,
                        json.dumps({"runId": str(start.request.runId)}),
                    ),
                )
            ).fetchone()
            assert initial is not None
        await accept_initial_revision(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            request_key=start.request.requestKey,
            edit_artifact_id=initial["id"],
        )
        async with db.scoped(url, SEEDED) as conn:
            accepted = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'edit', %s, %s, %s, 2, %s::jsonb)
                    RETURNING *
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"accepted-all-drop-fingerprint").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/all-drop-accepted.json",
                        accepted_sha,
                        json.dumps(
                            {
                                "format": "chapter-edit/1",
                                "mutationKey": str(mutation_key),
                                "revision": 2,
                                "runId": str(start.request.runId),
                            }
                        ),
                    ),
                )
            ).fetchone()
            assert accepted is not None
        committed = await commit_review_mutation(
            url,
            CommitReviewMutationRequest(
                prepared=PreparedReviewMutation(
                    request=command,
                    candidate=HarnessArtifactRef(
                        fingerprint=accepted["fingerprint"],
                        id=accepted["id"],
                        kind=HarnessArtifactKind.edit,
                        sha256=accepted["sha256"],
                        sizeBytes=accepted["size_bytes"],
                        storageKey=accepted["storage_key"],
                    ),
                    message="Accepted the deliberate drop with its supplied reason.",
                ),
                workflow=WorkflowIdentity(
                    workflow_id=f"chapter-review/{mutation_key}",
                    workflow_run_id=f"review-run/{mutation_key}",
                ),
            ),
        )
        assert committed.output.state == "applied"
        assert committed.output.revision == 2
        async with db.scoped(url, SEEDED) as conn:
            exported = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes, metadata)
                    VALUES (%s, %s, 'export', %s, %s, %s, 2, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        SEEDED.organizationId,
                        source_id,
                        hashlib.sha256(b"empty-export-fingerprint").hexdigest(),
                        f"org/{SEEDED.organizationId}/source/{source_id}/empty-export.json",
                        hashlib.sha256(b"empty-export").hexdigest(),
                        json.dumps(
                            {
                                "editSha256": accepted_sha,
                                "format": "chapter-export/1",
                                "revision": 2,
                                "runId": str(start.request.runId),
                            }
                        ),
                    ),
                )
            ).fetchone()
            assert exported is not None
        ready = await accept_export(
            url,
            scope=SEEDED,
            source_id=source_id,
            run_id=start.request.runId,
            revision=2,
            edit_artifact_id=accepted["id"],
            edit_sha256=accepted_sha,
            export_artifact_id=exported["id"],
        )
        assert ready.status == HarnessRunStatus.ready
        assert ready.accepted_revision == 2
        async with db.scoped(url, SEEDED) as conn:
            model_operations = await (
                await conn.execute(
                    """
                    SELECT count(*) AS n FROM harness_operation
                     WHERE run_id = %s AND kind = 'model'
                    """,
                    (start.request.runId,),
                )
            ).fetchone()
            event = await (
                await conn.execute(
                    "SELECT payload FROM chapter_review_event WHERE mutation_key = %s",
                    (str(mutation_key),),
                )
            ).fetchone()
        assert model_operations == {"n": 0}
        assert event is not None
        assert event["payload"]["reason"] == command.reason
    finally:
        await db.close_pool()
