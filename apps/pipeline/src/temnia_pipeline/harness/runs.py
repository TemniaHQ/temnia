"""Scoped chapter-run creation and compare-and-set state transitions."""

# Refusal messages stay beside the exact immutable identity or state check.
# ruff: noqa: C901, EM101, N818, TRY003

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterReviewAction,
    ChapterReviewInput,
    ChapterReviewOutput,
    ChapterRunConfig,
    ChapterRunRoutePreferences,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessRunStatus,
    Scope,
    State,
    TopicEditorialPatchInput,
    TranscriptRevisionAnnotations,
)
from temnia_pipeline.harness.editorial_policy import (
    TOPIC_SELECTION_POLICY_V3,
    is_topic_policy,
)
from temnia_pipeline.harness.ledger import IdentityConflict, SourceDeleting
from temnia_pipeline.harness.routes import (
    ADMISSION_VERSION,
    ADMISSION_VERSION_LEGACY,
    AdmissionVersion,
    RouteSnapshot,
)
from temnia_pipeline.harness.runtime_types import (
    ClaimRepairRequest,
    CommitReviewMutationRequest,
    CommitReviewMutationResult,
    MarkRunFailedRequest,
    PinnedSource,
    PinnedTranscript,
    RenderRevisionRequest,
    ResumeRunAssets,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
)
from temnia_pipeline.harness.topic_editorial import EDITORIAL_BRIEF

if TYPE_CHECKING:
    from collections.abc import Mapping

    from psycopg import AsyncConnection

    from temnia_pipeline.harness.settings import HarnessSettings


class RunStateConflict(RuntimeError):
    """A late transition lost its stage/status compare-and-set."""


def _pinned(row: Mapping[str, Any]) -> PinnedTranscript:
    metadata = row["transcript_revision_metadata"]
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = cast("dict[str, object]", metadata)
    sha = metadata.get("sha256") or metadata.get("contentSha256")
    raw_annotations = metadata.get("annotations")
    annotations = (
        TranscriptRevisionAnnotations.model_validate(raw_annotations)
        if raw_annotations is not None
        else None
    )
    raw_labels = cast("object", row.get("transcript_speaker_labels"))
    labels = (
        {str(key): str(value) for key, value in cast("dict[object, object]", raw_labels).items()}
        if isinstance(raw_labels, dict)
        else {}
    )
    return PinnedTranscript(
        transcript_id=row["transcript_id"],
        revision=int(row["transcript_revision"]),
        storage_key=str(row["transcript_storage_key"]),
        size_bytes=int(row["transcript_size_bytes"]),
        sha256=sha if isinstance(sha, str) else None,
        annotations=annotations,
        machine_revision=cast("int | None", row.get("transcript_machine_revision")),
        legacy_speaker_labels=labels,
    )


def run_admission_version(route_snapshot: Mapping[str, Any]) -> AdmissionVersion:
    """Read the admission arithmetic a run froze; rows without the key predate it."""
    value = route_snapshot.get("admission")
    return ADMISSION_VERSION if value == ADMISSION_VERSION else ADMISSION_VERSION_LEGACY


def _snapshot(row: Mapping[str, Any]) -> RunSnapshot:
    route_snapshot = row["route_snapshot"]
    transcript = PinnedTranscript.model_validate_json(
        json.dumps(route_snapshot["pinnedTranscript"])
    )
    source = PinnedSource.model_validate_json(json.dumps(route_snapshot["pinnedSource"]))
    frozen_routes = RouteSnapshot.model_validate_json(json.dumps(route_snapshot["snapshot"]))
    return RunSnapshot(
        admission=run_admission_version(route_snapshot),
        id=row["id"],
        workflow_id=str(row["workflow_id"]),
        workflow_run_id=str(row["workflow_run_id"]),
        source_id=row["source_id"],
        request_key=UUID(str(row["request_key"])),
        initial_budget_micros=int(route_snapshot.get("initialBudgetMicros", row["budget_micros"])),
        config=ChapterRunConfig.model_validate(row["config"]),
        brief=str(row["brief"]),
        status=HarnessRunStatus(str(row["status"])),
        stage=row["stage"],
        budget_micros=int(row["budget_micros"]),
        spent_micros=int(row["spent_micros"]),
        reserved_micros=int(row["reserved_micros"]),
        dispatch_count=int(row["dispatch_count"]),
        repair_count=int(row["repair_count"]),
        current_revision=int(row["current_revision"]),
        accepted_revision=row["accepted_revision"],
        evidence_artifact_id=row["evidence_artifact_id"],
        error_message=row["error_message"],
        route_preferences={
            str(key): str(value)
            for key, value in dict(route_snapshot.get("routePreferences") or {}).items()
        },
        projection=route_snapshot.get("projection"),
        source=source,
        transcript=transcript,
        route_snapshot=frozen_routes,
        evaluation_program=route_snapshot.get("evaluationProgram"),
        evaluation_program_sha256=route_snapshot.get("evaluationProgramSha256"),
        editorial_policy=route_snapshot.get("editorialPolicy", TOPIC_SELECTION_POLICY_V3),
        topic_shot_detector=route_snapshot.get("topicShotDetector", "scdet"),
    )


async def _lock_ready_source(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID
) -> Mapping[str, Any]:
    row = await (
        await conn.execute(
            """
            SELECT s.id, s.status, s.deletion_requested_at, s.master_key,
                   s.size_bytes AS source_size_bytes, s.duration_ms,
                   t.id AS transcript_id, t.status AS transcript_status,
                   t.current_revision AS transcript_revision,
                   t.speaker_labels AS transcript_speaker_labels,
                   tr.storage_key AS transcript_storage_key,
                   tr.size_bytes AS transcript_size_bytes,
                   tr.metadata AS transcript_revision_metadata,
                   (SELECT MAX(machine.revision)
                      FROM transcript_revision machine
                     WHERE machine.organization_id = t.organization_id
                       AND machine.transcript_id = t.id
                       AND machine.kind = 'machine'
                       AND machine.revision <= t.current_revision
                   ) AS transcript_machine_revision
              FROM source s
              LEFT JOIN transcript t
                ON t.organization_id = s.organization_id AND t.source_id = s.id
              LEFT JOIN transcript_revision tr
                ON tr.organization_id = t.organization_id
               AND tr.transcript_id = t.id AND tr.revision = t.current_revision
             WHERE s.id = %s
             FOR UPDATE OF s
            """,
            (source_id,),
        )
    ).fetchone()
    if row is None:
        raise IdentityConflict("source is absent from the active organization scope")
    if row["deletion_requested_at"] is not None:
        raise SourceDeleting("source is fenced for deletion")
    if row["status"] != "ready":
        raise IdentityConflict("chapter runs require a ready source")
    if (
        row["transcript_id"] is None
        or row["transcript_status"] != "ready"
        or row["transcript_revision"] is None
    ):
        raise IdentityConflict("chapter runs require an accepted transcript revision")
    return row


RESUMABLE_STATUSES = ("pending", "failed", "budget_paused", "needs_review")


async def start_or_refetch_run(  # noqa: PLR0912, PLR0915
    database_url: str,
    *,
    start: StartRunRequest,
    settings: HarnessSettings,
    route_snapshot: RouteSnapshot,
) -> StartRunResult:
    """Create one immutable request-keyed run or return its exact prior identity."""
    request = start.request
    # One default brief lives in Python, so a web run hashes the rubric the r-runs hashed.
    brief = request.brief if request.brief and request.brief.strip() else EDITORIAL_BRIEF
    program_value: object = start.evaluation_program
    if program_value is None:
        # Every topic run freezes its own prompt-template and native-schema bytes.
        from temnia_pipeline.harness.topic_program import current_program  # noqa: PLC0415

        program_value = current_program(start.editorial_policy)
    # The operator-only manifest is not part of the cross-language run request.
    from temnia_pipeline.evals.topics import (  # noqa: PLC0415
        TopicProgramManifest,
        digest,
        editorial_identity,
    )

    program = TopicProgramManifest.model_validate(program_value)
    if program.policy != start.editorial_policy:
        raise IdentityConflict("evaluation programme differs from the run editorial policy")
    evaluation_program = program.model_dump(mode="json", by_alias=True)
    evaluation_program_sha = digest(evaluation_program)
    if not settings.enabled:
        raise RuntimeError("chapter harness is disabled on this worker")
    if request.budgetMicros > settings.max_run_budget_micros:
        raise IdentityConflict("requested run budget exceeds the worker maximum")
    if request.config != settings.allowed_config():
        raise IdentityConflict("requested run config differs from worker allowed config")
    if route_snapshot.snapshot_id != request.config.routeSnapshotId:
        raise IdentityConflict("requested route snapshot differs from loaded immutable snapshot")
    preferences = route_preferences(route_snapshot, request.routes)
    async with db.scoped(database_url, request.scope) as conn:
        fenced = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (request.sourceId,),
            )
        ).fetchone()
        if fenced is None:
            raise IdentityConflict("source is absent from the active organization scope")
        if fenced["deletion_requested_at"] is not None:
            raise SourceDeleting("source is fenced for deletion")
        existing = await (
            await conn.execute(
                """
                SELECT * FROM harness_run
                 WHERE source_id = %s AND request_key = %s
                 FOR UPDATE
                """,
                (request.sourceId, str(request.requestKey)),
            )
        ).fetchone()
        config_value = request.config.model_dump(mode="json")
        if existing is not None:
            pinned = existing["route_snapshot"]
            # The lane is the coarser identity, and a lane change also changes the
            # programme this worker would attach; name the lane rather than the manifest.
            prior_policy = pinned.get("editorialPolicy", "legacy")
            if (is_topic_policy(start.editorial_policy) or is_topic_policy(prior_policy)) and (
                start.editorial_policy != prior_policy
            ):
                raise IdentityConflict("request key was reused across incompatible editorial lanes")
            pinned_program = pinned.get("evaluationProgram")
            if start.evaluation_program is not None:
                # An experiment claims exactly the programme it prepared, build included.
                if (
                    pinned_program != evaluation_program
                    or pinned.get("evaluationProgramSha256") != evaluation_program_sha
                ):
                    raise IdentityConflict(
                        "request key was reused with different evaluation programme"
                    )
            elif editorial_identity(pinned_program) != editorial_identity(evaluation_program):
                # A product run resumes under any build that speaks its editorial programme;
                # the build is recorded below. Changed prompts or schemas are another run.
                raise IdentityConflict(
                    "request key was reused with different evaluation programme: the prompts "
                    "or schemas changed since this run started, so it cannot be resumed; "
                    "start a new run"
                )
            expected = (
                request.runId,
                request.sourceId,
                str(request.requestKey),
                brief,
                request.budgetMicros,
                config_value,
                preferences,
            )
            actual = (
                existing["id"],
                existing["source_id"],
                str(existing["request_key"]),
                str(existing["brief"]),
                int(pinned.get("initialBudgetMicros", existing["budget_micros"])),
                existing["config"],
                dict(pinned.get("routePreferences") or {}),
            )
            if actual != expected:
                raise IdentityConflict("request key was reused with different immutable run intent")
            same_execution = (
                existing["workflow_id"] == start.workflow.workflow_id
                and existing["workflow_run_id"] == start.workflow.workflow_run_id
            )
            # A new execution resumes a run that is waiting or stopped on a known failure;
            # its retained artifacts and settled responses are reused, never paid again.
            # An unconfirmed provider outcome keeps its fence until reconciled.
            if existing["status"] in RESUMABLE_STATUSES and not same_execution:
                if existing["status"] == "needs_review":
                    refusal = await _planning_retry_refusal(
                        conn, run=existing, source_id=request.sourceId, run_id=request.runId
                    )
                    if refusal is not None:
                        raise RunStateConflict(refusal)
                resumed_by = json.dumps(
                    [
                        {
                            "implementationSha256": program.implementation_sha256,
                            "workflowId": start.workflow.workflow_id,
                            "workflowRunId": start.workflow.workflow_run_id,
                        }
                    ]
                )
                existing = await (
                    await conn.execute(
                        """
                        UPDATE harness_run
                           SET workflow_id = %s, workflow_run_id = %s,
                               status = 'running', error_message = NULL, updated_at = now(),
                               route_snapshot = jsonb_set(
                                   route_snapshot,
                                   '{resumedImplementations}',
                                   COALESCE(route_snapshot->'resumedImplementations', '[]'::jsonb)
                                       || %s::jsonb
                               )
                         WHERE id = %s AND status = ANY(%s)
                         RETURNING *
                        """,
                        (
                            start.workflow.workflow_id,
                            start.workflow.workflow_run_id,
                            resumed_by,
                            request.runId,
                            list(RESUMABLE_STATUSES),
                        ),
                    )
                ).fetchone()
                if existing is None:
                    raise IdentityConflict("run resume ownership changed concurrently")
            elif existing["status"] == "running" and not same_execution:
                raise IdentityConflict("run resume is already owned by another execution")
            return StartRunResult(run=_snapshot(existing), created=False)
        source = await _lock_ready_source(conn, request.sourceId)
        transcript = _pinned(source)
        prefix = f"org/{request.scope.organizationId}/source/{request.sourceId}/"
        if not str(source["master_key"]).startswith(prefix):
            raise IdentityConflict("source master key is outside its immutable source prefix")
        if not transcript.storage_key.startswith(prefix):
            raise IdentityConflict("transcript revision key is outside its source prefix")
        pinned_source = PinnedSource(
            storage_key=str(source["master_key"]),
            size_bytes=int(source["source_size_bytes"]),
            duration_ms=int(source["duration_ms"]),
        )
        route_value: dict[str, object] = {
            # The admission arithmetic this run's reservations were computed with. Rows
            # written before the key exists used `admission/1`; see `run_admission_version`.
            "admission": ADMISSION_VERSION,
            "initialBudgetMicros": request.budgetMicros,
            "pinnedSource": pinned_source.model_dump(mode="json"),
            "pinnedTranscript": transcript.model_dump(mode="json"),
            "snapshot": route_snapshot.model_dump(mode="json"),
        }
        route_value["routePreferences"] = preferences
        route_value["evaluationProgram"] = evaluation_program
        route_value["evaluationProgramSha256"] = evaluation_program_sha
        route_value["editorialPolicy"] = start.editorial_policy
        route_value["topicShotDetector"] = settings.topic_shot_detector
        row = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (id, organization_id, source_id, request_key, lane, budget_micros,
                     brief, config, route_snapshot, workflow_id, workflow_run_id, stage)
                VALUES (%s, %s, %s, %s, 'chapters', %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, 'evidence')
                ON CONFLICT (organization_id, source_id, request_key) DO NOTHING
                RETURNING *
                """,
                (
                    request.runId,
                    request.scope.organizationId,
                    request.sourceId,
                    str(request.requestKey),
                    request.budgetMicros,
                    brief,
                    json.dumps(config_value, separators=(",", ":"), sort_keys=True),
                    json.dumps(route_value, separators=(",", ":"), sort_keys=True),
                    start.workflow.workflow_id,
                    start.workflow.workflow_run_id,
                ),
            )
        ).fetchone()
        created = row is not None
        if row is None:
            raise IdentityConflict("request-keyed run disappeared during acquisition")
        return StartRunResult(run=_snapshot(row), created=created)


def route_preferences(
    snapshot: RouteSnapshot, requested: ChapterRunRoutePreferences | None
) -> dict[str, str]:
    """Freeze seat preferences only when each names a route inside that seat's pool."""
    preferences: dict[str, str] = {}
    if requested is None:
        return preferences
    for seat, key in (("propose", "author"), ("verify", "verifier")):
        route_id = getattr(requested, key)
        if route_id is None:
            continue
        pool = snapshot.seats.get(seat)
        if pool is None or route_id not in pool.route_ids:
            message = (
                f"requested {key} route {route_id!r} is not in the frozen snapshot's {seat} pool"
            )
            raise IdentityConflict(message)
        preferences[key] = route_id
    return preferences


def apply_route_preferences(
    snapshot: RouteSnapshot, preferences: Mapping[str, str]
) -> RouteSnapshot:
    """Put each preferred route first in its seat pool; the rest keep their frozen order."""
    if not preferences:
        return snapshot
    seats = dict(snapshot.seats)
    for seat, key in (("propose", "author"), ("verify", "verifier")):
        route_id = preferences.get(key)
        pool = seats.get(seat)
        if route_id is None or pool is None or route_id not in pool.route_ids:
            continue
        ordered = (route_id, *(item for item in pool.route_ids if item != route_id))
        seats[seat] = pool.model_copy(update={"route_ids": ordered})
    return snapshot.model_copy(update={"seats": seats})


async def record_run_projection(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    projection: Mapping[str, Any],
) -> None:
    """Attach the pre-spend work projection to the run row for the panel and the ledger."""
    async with db.scoped(database_url, scope) as conn:
        await conn.execute(
            """
            UPDATE harness_run
               SET route_snapshot = jsonb_set(route_snapshot, '{projection}', %s::jsonb),
                   updated_at = now()
             WHERE id = %s AND source_id = %s
            """,
            (
                json.dumps(dict(projection), separators=(",", ":"), sort_keys=True),
                run_id,
                source_id,
            ),
        )


async def get_run(database_url: str, *, scope: Scope, source_id: UUID, run_id: UUID) -> RunSnapshot:
    """Read one scoped run and its pinned transcript identity."""
    run = await find_run(database_url, scope=scope, source_id=source_id, run_id=run_id)
    if run is None:
        raise IdentityConflict("run is absent from the source scope")
    return run


async def find_run(
    database_url: str, *, scope: Scope, source_id: UUID, run_id: UUID
) -> RunSnapshot | None:
    """Read an optional prepared run without acquiring or changing its ownership."""
    async with db.scoped(database_url, scope) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s",
                (run_id, source_id),
            )
        ).fetchone()
        return _snapshot(row) if row is not None else None


async def ready_source_pins(
    database_url: str, *, scope: Scope, source_id: UUID
) -> tuple[PinnedSource, PinnedTranscript]:
    """Observe ready source facts; creation must still pin and compare them atomically."""
    async with db.scoped(database_url, scope) as conn:
        row = await _lock_ready_source(conn, source_id)
        return (
            PinnedSource(
                storage_key=str(row["master_key"]),
                size_bytes=int(row["source_size_bytes"]),
                duration_ms=int(row["duration_ms"]),
            ),
            _pinned(row),
        )


async def get_resume_assets(
    database_url: str, *, scope: Scope, source_id: UUID, run_id: UUID
) -> ResumeRunAssets:
    """Return the exact current evidence/edit pair after a resume execution claims the run."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None or int(run["current_revision"]) <= 0:
            raise IdentityConflict("resumed run has no current chapter revision")
        revision_row = await (
            await conn.execute(
                """
                SELECT base_revision FROM chapter_revision
                 WHERE run_id = %s AND source_id = %s AND revision = %s
                """,
                (run_id, source_id, run["current_revision"]),
            )
        ).fetchone()
        if revision_row is None:
            raise IdentityConflict("resumed run current chapter revision is absent")
        rows = await (
            await conn.execute(
                """
                SELECT a.* FROM harness_artifact a
                 WHERE a.source_id = %s AND a.id IN (
                    %s,
                    (SELECT artifact_id FROM chapter_revision
                      WHERE run_id = %s AND revision = %s)
                 )
                """,
                (
                    source_id,
                    run["evidence_artifact_id"],
                    run_id,
                    run["current_revision"],
                ),
            )
        ).fetchall()
        by_kind = {str(row["kind"]): row for row in rows}
        if "evidence" not in by_kind or "edit" not in by_kind:
            raise IdentityConflict("resumed run current artifacts are incomplete")
        return ResumeRunAssets(
            evidence=_artifact_ref(by_kind["evidence"]),
            edit=_artifact_ref(by_kind["edit"]),
            revision=int(run["current_revision"]),
            base_revision=(
                int(revision_row["base_revision"])
                if revision_row["base_revision"] is not None
                else None
            ),
        )


async def claim_repair(
    database_url: str,
    *,
    request: ClaimRepairRequest,
    max_repairs: int,
) -> RunSnapshot:
    """Claim one finite semantic repair slot, idempotently across activity retries."""
    ref = request.run
    scope = Scope(
        organizationId=ref.scope_organization_id,
        userId=ref.scope_user_id,
    )
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, ref.source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (ref.run_id, ref.source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("repair run is absent from the source scope")
        if (
            str(run["workflow_id"]) != request.workflow.workflow_id
            or str(run["workflow_run_id"]) != request.workflow.workflow_run_id
        ):
            raise RunStateConflict("repair claim belongs to a stale workflow execution")
        current = int(run["repair_count"])
        if current == request.expected_repair_count + 1:
            return _snapshot(run)
        if current != request.expected_repair_count:
            raise RunStateConflict("repair count changed before the requested claim")
        indexed = run["route_snapshot"].get("editorialPolicy") == "standalone-topics/7"
        base = request.repair_base_count if indexed else 0
        if base > request.expected_repair_count or current >= base + max_repairs:
            raise RunStateConflict("run exhausted its configured semantic repair limit")
        if (
            run["status"] != "running"
            or run["stage"] != "planning"
            or (int(run["current_revision"]) != 0 and not indexed)
        ):
            raise RunStateConflict("only active unrevised planning may claim a repair")
        updated = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET repair_count = repair_count + 1, updated_at = now()
                 WHERE id = %s AND repair_count = %s
                 RETURNING *
                """,
                (ref.run_id, current),
            )
        ).fetchone()
        if updated is None:
            raise RunStateConflict("semantic repair claim compare-and-set was lost")
        return _snapshot(updated)


async def settle_reconciled_run(database_url: str, *, run: RunRef, message: str) -> bool:
    """Lift the unknown-outcome fence once no attempt's charge is unknown any more."""
    scope = Scope(organizationId=run.scope_organization_id, userId=run.scope_user_id)
    async with db.scoped(database_url, scope) as conn:
        remaining = await (
            await conn.execute(
                "SELECT count(*) AS value FROM harness_attempt"
                " WHERE run_id = %s AND cost_status = 'unknown'",
                (run.run_id,),
            )
        ).fetchone()
        if remaining is None or int(remaining["value"]) != 0:
            return False
        updated = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = 'failed', error_message = %s, updated_at = now()
                 WHERE id = %s AND source_id = %s AND status = 'outcome_unknown'
                 RETURNING id
                """,
                (message, run.run_id, run.source_id),
            )
        ).fetchone()
        return updated is not None


async def mark_run_failed(database_url: str, *, request: MarkRunFailedRequest) -> bool:
    """Mark only the active owning execution failed; preserve every terminal fence."""
    ref = request.run
    scope = Scope(
        organizationId=ref.scope_organization_id,
        userId=ref.scope_user_id,
    )
    async with db.scoped(database_url, scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id FROM source WHERE id = %s FOR UPDATE",
                (ref.source_id,),
            )
        ).fetchone()
        if source is None:
            return False
        row = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET status = %s, error_message = %s, updated_at = now()
                 WHERE id = %s AND source_id = %s
                   AND workflow_id = %s AND workflow_run_id = %s
                   AND status IN ('pending', 'running')
                 RETURNING id
                """,
                (
                    request.status,
                    request.error_message,
                    ref.run_id,
                    ref.source_id,
                    request.workflow.workflow_id,
                    request.workflow.workflow_run_id,
                ),
            )
        ).fetchone()
        return row is not None


async def update_stage(database_url: str, update: StageUpdate) -> RunSnapshot:
    """Advance only the exact current stage and never revive a terminal run."""
    scope = Scope(
        organizationId=update.scope_organization_id,
        userId=update.scope_user_id,
    )
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, update.source_id)
        row = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET stage = %s, status = %s, error_message = %s, updated_at = now()
                 WHERE id = %s AND source_id = %s
                   AND stage IS NOT DISTINCT FROM %s
                   AND current_revision = %s
                   AND status NOT IN ('cancelled', 'ready', 'outcome_unknown')
                 RETURNING *
                """,
                (
                    update.next_stage,
                    str(update.status),
                    update.error_message,
                    update.run_id,
                    update.source_id,
                    update.expected_stage,
                    update.expected_revision,
                ),
            )
        ).fetchone()
        if row is None:
            raise RunStateConflict("run stage changed or the run became terminal")
        return _snapshot(row)


async def attach_evidence(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    artifact_id: UUID,
) -> RunSnapshot:
    """Attach evidence only while the run still owns the evidence stage."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, source_id)
        artifact = await (
            await conn.execute(
                """
                SELECT id FROM harness_artifact
                 WHERE id = %s AND source_id = %s AND kind = 'evidence'
                """,
                (artifact_id, source_id),
            )
        ).fetchone()
        if artifact is None:
            raise IdentityConflict("evidence artifact is absent from the source scope")
        row = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET evidence_artifact_id = %s, stage = 'planning', status = 'running',
                       updated_at = now()
                 WHERE id = %s AND source_id = %s AND stage = 'evidence'
                   AND status IN ('pending', 'running')
                 RETURNING *
                """,
                (artifact_id, run_id, source_id),
            )
        ).fetchone()
        if row is None:
            current = await (
                await conn.execute(
                    "SELECT * FROM harness_run WHERE id = %s AND source_id = %s",
                    (run_id, source_id),
                )
            ).fetchone()
            if current is not None and current["evidence_artifact_id"] == artifact_id:
                return _snapshot(current)
            raise RunStateConflict("late evidence cannot replace the run's current stage")
        return _snapshot(row)


async def accept_initial_revision(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    request_key: UUID,
    edit_artifact_id: UUID,
    base_revision: int = 0,
) -> RunSnapshot:
    """Insert an immutable compiled revision while comparing its expected base revision."""
    mutation_key = (
        f"initial:{request_key}"
        if base_revision == 0
        else f"editorial:{request_key}:{base_revision + 1}"
    )
    revision = base_revision + 1
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("run is absent from the source scope")
        if UUID(str(run["request_key"])) != request_key:
            raise IdentityConflict("initial revision names a different run request")
        artifact = await (
            await conn.execute(
                """
                SELECT id, metadata FROM harness_artifact
                 WHERE id = %s AND source_id = %s AND kind = 'edit'
                """,
                (edit_artifact_id, source_id),
            )
        ).fetchone()
        if artifact is None or artifact["metadata"].get("runId") != str(run_id):
            raise IdentityConflict("initial edit artifact is absent from the scoped run")
        existing = await (
            await conn.execute(
                """
                SELECT revision, artifact_id FROM chapter_revision
                 WHERE run_id = %s AND mutation_key = %s
                """,
                (run_id, mutation_key),
            )
        ).fetchone()
        if existing is not None:
            if int(existing["revision"]) != revision or existing["artifact_id"] != edit_artifact_id:
                raise IdentityConflict("initial revision identity maps to a different edit")
            return _snapshot(run)
        if int(run["current_revision"]) != base_revision:
            raise RunStateConflict("a newer chapter revision already exists")
        if run["status"] in {"cancelled", "failed", "ready", "outcome_unknown"}:
            raise RunStateConflict("terminal or uncertain run cannot accept an initial revision")
        await conn.execute(
            """
            INSERT INTO chapter_revision
                (organization_id, source_id, run_id, revision, artifact_id,
                 base_revision, mutation_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                scope.organizationId,
                source_id,
                run_id,
                revision,
                edit_artifact_id,
                base_revision or None,
                mutation_key,
            ),
        )
        updated = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET current_revision = %s, stage = 'render', status = 'running',
                       updated_at = now()
                 WHERE id = %s AND current_revision = %s
                 RETURNING *
                """,
                (revision, run_id, base_revision),
            )
        ).fetchone()
        if updated is None:
            raise RunStateConflict("initial revision compare-and-set was lost")
        return _snapshot(updated)


async def _planning_retry_refusal(
    conn: AsyncConnection[dict[str, Any]],
    *,
    run: Mapping[str, Any],
    source_id: UUID,
    run_id: UUID,
) -> str | None:
    """Return why a revision-zero planning refusal cannot safely resume."""
    has_edit_row = await (
        await conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM chapter_revision
                 WHERE run_id = %s AND source_id = %s
            ) AS value
            """,
            (run_id, source_id),
        )
    ).fetchone()
    if has_edit_row is None:
        raise RuntimeError("planning retry edit check returned no row")
    indexed_progress = False
    if run["route_snapshot"].get("editorialPolicy") == "standalone-topics/7":
        progress = await (
            await conn.execute(
                """SELECT metadata FROM harness_artifact
                    WHERE source_id=%s AND kind='checks'
                      AND metadata->>'format'='topic-editorial-progress/1'
                      AND metadata->>'runId'=%s
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                (source_id, str(run_id)),
            )
        ).fetchone()
        indexed_progress = (
            progress is not None
            and progress["metadata"].get("phase") == "review"
            and int(progress["metadata"].get("baseRevision", -1))
            in {int(run["current_revision"]), int(run["current_revision"]) - 1}
        )
    if (
        int(run["current_revision"]) != 0
        or run["accepted_revision"] is not None
        or str(run["stage"]) != "needs_review"
        or bool(has_edit_row["value"])
    ) and not (indexed_progress and run["accepted_revision"] is None):
        return "Only revision-zero planning work without an edit may retry."

    if int(run["reserved_micros"]) != 0:
        return "Planning cannot retry while provider execution or cost remains unresolved."

    unresolved_row = await (
        await conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM harness_attempt a
                  LEFT JOIN harness_reservation r
                    ON r.organization_id = a.organization_id
                   AND r.source_id = a.source_id
                   AND r.attempt_id = a.id
                 WHERE a.run_id = %s AND a.source_id = %s
                   AND (
                       a.state NOT IN ('succeeded', 'failed_known', 'cancelled_confirmed')
                       OR r.state = 'active'
                   )
            ) AS value
            """,
            (run_id, source_id),
        )
    ).fetchone()
    if unresolved_row is None:
        raise RuntimeError("planning retry exposure check returned no row")
    if bool(unresolved_row["value"]):
        return "Planning cannot retry while provider execution or cost remains unresolved."
    return None


async def apply_operational_review(  # noqa: PLR0912, PLR0915
    database_url: str,
    *,
    request: ChapterReviewInput,
    max_run_budget_micros: int,
) -> ChapterReviewOutput:
    """Persist idempotent cancel, raise-budget, and known-state retry commands."""
    payload = request.model_dump(mode="json")
    async with db.scoped(database_url, request.scope) as conn:
        await _lock_ready_source(conn, request.sourceId)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (request.runId, request.sourceId),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("review run is absent from the source scope")
        existing = await (
            await conn.execute(
                """
                SELECT payload, result FROM chapter_review_event
                 WHERE run_id = %s AND mutation_key = %s
                """,
                (request.runId, str(request.mutationKey)),
            )
        ).fetchone()
        if existing is not None:
            if existing["payload"] != payload:
                raise IdentityConflict("review mutation key was reused with a different payload")
            return ChapterReviewOutput.model_validate(existing["result"])
        state = State.refused
        message = "this review action requires an edit mutation activity"
        status = str(run["status"])
        stale_revision = request.baseRevision != int(run["current_revision"])
        if stale_revision:
            state = State.conflict
            message = "The run revision changed before this operational command was applied."
        elif request.action == ChapterReviewAction.cancel:
            if status in {"cancelled", "failed", "outcome_unknown", "ready"}:
                message = "A terminal, ready, or outcome-unknown run cannot be cancelled."
                reserved = []
            else:
                reserved = await (
                    await conn.execute(
                        """
                        SELECT a.id AS attempt_id, a.operation_id, r.id AS reservation_id,
                               r.amount_micros
                          FROM harness_attempt a
                          JOIN harness_reservation r ON r.attempt_id = a.id
                         WHERE a.run_id = %s AND a.source_id = %s
                           AND a.state = 'reserved' AND r.state = 'active'
                         FOR UPDATE OF a, r
                        """,
                        (request.runId, request.sourceId),
                    )
                ).fetchall()
            released = sum(int(row["amount_micros"]) for row in reserved)
            for row in reserved:
                await conn.execute(
                    """
                    UPDATE harness_attempt
                       SET state = 'cancelled_confirmed', actual_cost_micros = 0,
                           cost_status = 'reported', usage = '{}'::jsonb, finished_at = now()
                     WHERE id = %s
                    """,
                    (row["attempt_id"],),
                )
                await conn.execute(
                    """
                    UPDATE harness_reservation
                       SET state = 'released', settled_micros = 0, settled_at = now()
                     WHERE id = %s
                    """,
                    (row["reservation_id"],),
                )
                await conn.execute(
                    """
                    UPDATE harness_operation SET status = 'cancelled', updated_at = now()
                     WHERE id = %s AND status = 'pending'
                    """,
                    (row["operation_id"],),
                )
            if status not in {"cancelled", "failed", "outcome_unknown", "ready"}:
                await conn.execute(
                    """
                    UPDATE harness_run
                       SET status = 'cancelled', reserved_micros = reserved_micros - %s,
                           updated_at = now()
                     WHERE id = %s
                    """,
                    (released, request.runId),
                )
                state = State.applied
                message = (
                    "Run cancelled; in-flight provider exposure remains pending reconciliation."
                )
        elif request.action == ChapterReviewAction.raise_budget:
            amount = request.budgetMicros
            if amount is None or amount <= int(run["budget_micros"]):
                message = "The new budget must be greater than the current budget."
            elif amount > max_run_budget_micros:
                message = "The new budget exceeds this worker's configured maximum."
            elif status in {"cancelled", "ready"}:
                message = "A cancelled or ready run cannot be resumed by changing its budget."
            elif status == "outcome_unknown":
                message = "Unknown provider exposure must be reconciled before any resume."
            else:
                await conn.execute(
                    """
                    UPDATE harness_run
                       SET budget_micros = %s,
                           status = CASE WHEN status = 'budget_paused'
                                         THEN 'pending' ELSE status END,
                           error_message = CASE WHEN status = 'budget_paused'
                                                THEN NULL ELSE error_message END,
                           updated_at = now()
                     WHERE id = %s
                    """,
                    (amount, request.runId),
                )
                state = State.applied
                message = "Budget increased without changing the current run stage."
        elif request.action == ChapterReviewAction.retry:
            planning_retry = status == "needs_review"
            if planning_retry:
                refusal = await _planning_retry_refusal(
                    conn,
                    run=run,
                    source_id=request.sourceId,
                    run_id=request.runId,
                )
                if refusal is not None:
                    message = refusal
                else:
                    await conn.execute(
                        """
                        UPDATE harness_run
                           SET status = 'pending', stage = 'evidence',
                               error_message = NULL, updated_at = now()
                         WHERE id = %s
                        """,
                        (request.runId,),
                    )
                    state = State.applied
                    message = (
                        "Saved planning results will be revalidated before unfinished work resumes."
                    )
            elif status in {"failed", "budget_paused"}:
                await conn.execute(
                    """
                    UPDATE harness_run
                       SET status = 'pending', error_message = NULL, updated_at = now()
                     WHERE id = %s
                    """,
                    (request.runId,),
                )
                state = State.applied
                message = "Known unfinished work may resume without repeating accepted operations."
            else:
                message = "Only known failed, budget-paused, or planning-review work may retry."
        result = ChapterReviewOutput(
            message=message,
            mutationKey=request.mutationKey,
            revision=(int(run["current_revision"]) or None),
            runId=request.runId,
            state=state,
        )
        await conn.execute(
            """
            INSERT INTO chapter_review_event
                (organization_id, source_id, run_id, mutation_key, action, base_revision,
                 payload, result, state, resulting_revision, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
            """,
            (
                request.scope.organizationId,
                request.sourceId,
                request.runId,
                str(request.mutationKey),
                str(request.action),
                request.baseRevision,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                result.model_dump_json(),
                str(state),
                result.revision,
                request.scope.userId,
            ),
        )
        return result


async def commit_review_mutation(
    database_url: str,
    request: CommitReviewMutationRequest,
) -> CommitReviewMutationResult:
    """Persist one prepared edit and advance its revision under a source/run CAS."""
    prepared = request.prepared
    command = prepared.request
    payload = command.model_dump(mode="json")
    async with db.scoped(database_url, command.scope) as conn:
        await _lock_ready_source(conn, command.sourceId)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (command.runId, command.sourceId),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("review run is absent from the source scope")
        existing = await (
            await conn.execute(
                """
                SELECT payload, result, resulting_revision
                  FROM chapter_review_event
                 WHERE run_id = %s AND mutation_key = %s
                """,
                (command.runId, str(command.mutationKey)),
            )
        ).fetchone()
        if existing is not None:
            if existing["payload"] != payload:
                raise IdentityConflict("review mutation key was reused with a different payload")
            output = ChapterReviewOutput.model_validate(existing["result"])
            render_request = await _existing_render_request(
                conn, command, run, existing["resulting_revision"]
            )
            return CommitReviewMutationResult(output=output, render=render_request)

        state = State.refused
        message = prepared.message
        resulting_revision: int | None = None
        render_request = None
        candidate = prepared.candidate
        if int(run["current_revision"]) != command.baseRevision:
            state = State.conflict
            message = "The chapter edit changed before this review was applied."
        elif run["status"] in {"cancelled", "failed", "outcome_unknown"}:
            message = "This run cannot accept an edit while terminal or outcome-unknown."
        elif candidate is not None:
            next_revision = command.baseRevision + 1
            artifact = await (
                await conn.execute(
                    """
                    SELECT * FROM harness_artifact
                     WHERE id = %s AND source_id = %s AND kind = 'edit'
                    """,
                    (candidate.id, command.sourceId),
                )
            ).fetchone()
            if artifact is None:
                raise IdentityConflict("prepared review artifact is absent from the source scope")
            metadata = artifact["metadata"]
            expected_metadata = {
                "mutationKey": str(command.mutationKey),
                "revision": next_revision,
                "runId": str(command.runId),
            }
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                raise IdentityConflict("prepared review artifact metadata is not the requested CAS")
            await conn.execute(
                """
                INSERT INTO chapter_revision
                    (organization_id, source_id, run_id, revision, artifact_id,
                     base_revision, mutation_key, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command.scope.organizationId,
                    command.sourceId,
                    command.runId,
                    next_revision,
                    candidate.id,
                    command.baseRevision,
                    str(command.mutationKey),
                    command.scope.userId,
                ),
            )
            updated = await (
                await conn.execute(
                    """
                    UPDATE harness_run
                       SET current_revision = %s, stage = 'render', status = 'running',
                           workflow_id = %s, workflow_run_id = %s,
                           error_message = NULL, updated_at = now()
                     WHERE id = %s AND current_revision = %s
                     RETURNING id
                    """,
                    (
                        next_revision,
                        request.workflow.workflow_id,
                        request.workflow.workflow_run_id,
                        command.runId,
                        command.baseRevision,
                    ),
                )
            ).fetchone()
            if updated is None:
                raise RunStateConflict("review revision compare-and-set was lost")
            state = State.applied
            resulting_revision = next_revision
            render_request = RenderRevisionRequest(
                run=RunRef(
                    scope_organization_id=command.scope.organizationId,
                    scope_user_id=command.scope.userId,
                    source_id=command.sourceId,
                    run_id=command.runId,
                ),
                edit=candidate,
                revision=next_revision,
            )
        output = ChapterReviewOutput(
            message=message,
            mutationKey=command.mutationKey,
            revision=resulting_revision or (int(run["current_revision"]) or None),
            runId=command.runId,
            state=state,
        )
        await conn.execute(
            """
            INSERT INTO chapter_review_event
                (organization_id, source_id, run_id, mutation_key, action, base_revision,
                 payload, result, state, resulting_revision, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
            """,
            (
                command.scope.organizationId,
                command.sourceId,
                command.runId,
                str(command.mutationKey),
                str(command.action),
                command.baseRevision,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                output.model_dump_json(),
                str(state),
                resulting_revision,
                command.scope.userId,
            ),
        )
        return CommitReviewMutationResult(output=output, render=render_request)


async def _existing_render_request(
    conn: AsyncConnection[dict[str, Any]],
    command: ChapterReviewInput | TopicEditorialPatchInput,
    run: Mapping[str, Any],
    revision: int | None,
) -> RenderRevisionRequest | None:
    if revision is None or run["stage"] != "render" or int(run["current_revision"]) != revision:
        return None
    artifact = await (
        await conn.execute(
            """
            SELECT a.* FROM chapter_revision r
            JOIN harness_artifact a ON a.id = r.artifact_id
             WHERE r.run_id = %s AND r.revision = %s
            """,
            (command.runId, revision),
        )
    ).fetchone()
    if artifact is None:
        return None
    return RenderRevisionRequest(
        run=RunRef(
            scope_organization_id=command.scope.organizationId,
            scope_user_id=command.scope.userId,
            source_id=command.sourceId,
            run_id=command.runId,
        ),
        edit=_artifact_ref(artifact),
        revision=revision,
    )


def _artifact_ref(row: Mapping[str, Any]) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=row["id"],
        kind=HarnessArtifactKind(str(row["kind"])),
        fingerprint=str(row["fingerprint"]),
        sha256=str(row["sha256"]),
        sizeBytes=int(row["size_bytes"]),
        storageKey=str(row["storage_key"]),
    )


async def accept_export(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    revision: int,
    edit_artifact_id: UUID,
    edit_sha256: str,
    export_artifact_id: UUID,
) -> RunSnapshot:
    """Mark only the still-current fully reviewed revision ready after manifest publication."""
    async with db.scoped(database_url, scope) as conn:
        await _lock_ready_source(conn, source_id)
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (run_id, source_id),
            )
        ).fetchone()
        if run is None:
            raise IdentityConflict("export run is absent from the source scope")
        revision_row = await (
            await conn.execute(
                """
                SELECT r.artifact_id, a.sha256
                  FROM chapter_revision r
                  JOIN harness_artifact a ON a.id = r.artifact_id
                 WHERE r.run_id = %s AND r.source_id = %s AND r.revision = %s
                """,
                (run_id, source_id, revision),
            )
        ).fetchone()
        if revision_row is None or revision_row["artifact_id"] != edit_artifact_id:
            raise IdentityConflict("export edit is absent from the named chapter revision")
        if str(revision_row["sha256"]) != edit_sha256:
            raise IdentityConflict("export edit hash differs from the named chapter revision")
        export = await (
            await conn.execute(
                """
                SELECT metadata FROM harness_artifact
                 WHERE id = %s AND source_id = %s AND kind = 'export'
                """,
                (export_artifact_id, source_id),
            )
        ).fetchone()
        if export is None or export["metadata"] != {
            "editSha256": edit_sha256,
            "format": "chapter-export/1",
            "revision": revision,
            "runId": str(run_id),
        }:
            raise IdentityConflict("export artifact metadata does not name this revision")
        if run["status"] == "ready" and int(run["accepted_revision"] or 0) == revision:
            return _snapshot(run)
        if int(run["current_revision"]) != revision:
            raise RunStateConflict("late export cannot accept a newer chapter revision")
        if run["status"] in {"cancelled", "failed", "outcome_unknown"}:
            raise RunStateConflict("terminal or uncertain run cannot accept an export")
        updated = await (
            await conn.execute(
                """
                UPDATE harness_run
                   SET accepted_revision = %s, stage = 'ready', status = 'ready',
                       error_message = NULL, updated_at = now()
                 WHERE id = %s AND current_revision = %s
                 RETURNING *
                """,
                (revision, run_id, revision),
            )
        ).fetchone()
        if updated is None:
            raise RunStateConflict("export acceptance compare-and-set was lost")
        return _snapshot(updated)
