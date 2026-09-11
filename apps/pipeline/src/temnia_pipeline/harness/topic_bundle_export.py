"""Read-only, source-scoped standalone-topic artifact closure export."""

# Snapshot SQL and invariant failures are intentionally explicit.
# ruff: noqa: C901, EM101, PLR0915, PLR0913, TC003, TRY003

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessEvidence,
    Scope,
    TopicAssessment,
    TopicEditSpec,
    TopicProposal,
    TopicRenders,
    TopicSelectionAssessment,
    TopicSelectionRecord,
)
from temnia_pipeline.evals.chapters import AttemptFact
from temnia_pipeline.evals.topics import (
    StageObservation,
    TopicArtifact,
    TopicAttempt,
    TopicConfiguration,
    TopicEvaluationBundle,
    TopicReviewEvent,
    TopicRevision,
    artifact_model,
    digest,
    validate_topic_bundle,
)
from temnia_pipeline.harness import artifacts

if TYPE_CHECKING:
    from obstore.store import S3Store


@dataclass(frozen=True, slots=True)
class _TopicSnapshot:
    run: dict[str, Any]
    source: dict[str, Any]
    observed: dict[str, Any]
    revision_rows: list[dict[str, Any]]
    events: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    rows: dict[UUID, dict[str, Any]]
    dependencies: dict[UUID, tuple[UUID, ...]]


async def _read_topic_snapshot(database_url: str, *, scope: Scope, run_id: UUID) -> _TopicSnapshot:
    """Take a finite repeatable-read snapshot with the current organization/user scope."""
    pool = await db.get_pool(database_url)
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        await conn.execute(
            "SELECT set_config('app.organization_id', %s, true), "
            "set_config('app.user_id', %s, true)",
            (str(scope.organizationId), str(scope.userId)),
        )
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id=%s AND lane='chapters'", (run_id,)
            )
        ).fetchone()
        if run is None:
            raise ValueError("topic run is absent from the current scope")
        policy = run["route_snapshot"].get("editorialPolicy")
        if policy not in {"standalone-topics/1", "standalone-topics/2"}:
            raise ValueError("requested run is not a standalone-topic policy")
        source_id = run["source_id"]
        source = await (
            await conn.execute("SELECT * FROM source WHERE id=%s", (source_id,))
        ).fetchone()
        observed = await (
            await conn.execute("SELECT transaction_timestamp() AS observed_at")
        ).fetchone()
        if source is None or observed is None:
            raise ValueError("scoped source or observation time is absent")
        revision_rows = await (
            await conn.execute(
                "SELECT * FROM chapter_revision WHERE run_id=%s AND source_id=%s ORDER BY revision",
                (run_id, source_id),
            )
        ).fetchall()
        events = await (
            await conn.execute(
                "SELECT * FROM chapter_review_event WHERE run_id=%s "
                "AND source_id=%s ORDER BY created_at,id",
                (run_id, source_id),
            )
        ).fetchall()
        attempts = await (
            await conn.execute(
                """SELECT a.*, o.stage AS operation_stage, r.state AS reservation_state
            FROM harness_attempt a JOIN harness_operation o ON o.id=a.operation_id
              AND o.organization_id=a.organization_id AND o.source_id=a.source_id
            LEFT JOIN harness_reservation r ON r.attempt_id=a.id
              AND r.organization_id=a.organization_id AND r.source_id=a.source_id
            WHERE a.run_id=%s AND a.source_id=%s ORDER BY a.created_at,a.id""",
                (run_id, source_id),
            )
        ).fetchall()
        roots = await (
            await conn.execute(
                "SELECT * FROM harness_artifact WHERE source_id=%s "
                "AND metadata->>'runId'=%s ORDER BY created_at,id",
                (source_id, str(run_id)),
            )
        ).fetchall()
        rows = {row["id"]: row for row in roots}
        pending = {row["artifact_id"] for row in revision_rows} | {
            row["result_artifact_id"]
            for row in attempts
            if row.get("result_artifact_id") is not None
        }
        if run.get("evidence_artifact_id") is not None:
            pending.add(run["evidence_artifact_id"])
        dependencies: dict[UUID, tuple[UUID, ...]] = {}
        pending |= rows.keys()
        visited: set[UUID] = set()
        while pending:
            batch = pending - visited
            if not batch:
                break
            loaded = await (
                await conn.execute(
                    "SELECT * FROM harness_artifact WHERE source_id=%s AND id=ANY(%s)",
                    (source_id, list(batch)),
                )
            ).fetchall()
            if {row["id"] for row in loaded} != batch:
                raise ValueError("topic artifact dependency is absent from its source scope")
            rows.update({row["id"]: row for row in loaded})
            edges = await (
                await conn.execute(
                    "SELECT artifact_id,input_artifact_id FROM harness_artifact_dependency "
                    "WHERE artifact_id=ANY(%s) ORDER BY artifact_id,input_artifact_id",
                    (list(batch),),
                )
            ).fetchall()
            for artifact_id in batch:
                dependencies[artifact_id] = tuple(
                    edge["input_artifact_id"]
                    for edge in edges
                    if edge["artifact_id"] == artifact_id
                )
            visited |= batch
            pending = {edge["input_artifact_id"] for edge in edges} - visited
    return _TopicSnapshot(
        run, source, observed, revision_rows, events, attempts, rows, dependencies
    )


async def export_topic_bundle(
    database_url: str,
    *,
    scope: Scope,
    store: S3Store,
    run_id: UUID,
    split: str = "qualification",
    recording_group: str | None = None,
) -> TopicEvaluationBundle:
    """Capture rows consistently, then hash-check immutable JSON outside the transaction."""
    snapshot = await _read_topic_snapshot(database_url, scope=scope, run_id=run_id)
    run, source, observed = snapshot.run, snapshot.source, snapshot.observed
    revision_rows, events, attempts = snapshot.revision_rows, snapshot.events, snapshot.attempts
    rows, dependencies = snapshot.rows, snapshot.dependencies
    source_id = run["source_id"]
    policy = run["route_snapshot"]["editorialPolicy"]
    exported: list[TopicArtifact] = []
    for artifact_id, row in sorted(
        rows.items(), key=lambda item: (item[1]["created_at"], str(item[0]))
    ):
        metadata = row["metadata"]
        format_name = metadata.get("format", "")
        # Provider responses stay exact references; the evaluator does not expose reasoning.
        include = str(row["kind"]) in {"evidence", "edit", "checks"} or format_name in {
            "topic-renders/1",
            "chapter-renders/1",
            "topic-export/1",
            "topic-selection/2",
            "topic-rubric/1",
        }
        body = None
        if include:
            body = await artifacts.read_artifact_json(
                database_url, scope=scope, source_id=source_id, store=store, artifact_id=artifact_id
            )
            if not isinstance(body, dict) or digest(cast("dict[str, Any]", body)) != str(
                row["sha256"]
            ):
                raise ValueError("exported topic artifact body differs from its frozen identity")
        exported.append(
            TopicArtifact(
                id=artifact_id,
                source_id=source_id,
                sha256=str(row["sha256"]),
                fingerprint=str(row["fingerprint"]),
                kind=str(row["kind"]),
                size_bytes=int(row["size_bytes"]),
                storage_key=str(row["storage_key"]),
                metadata=metadata,
                dependencies=dependencies.get(artifact_id, ()),
                body=cast("Any", body),
                body_status="included"
                if include
                else "retained_reference"
                if str(row["kind"]) == "model_response"
                else "binary_reference",
            )
        )
    by_id = {artifact.id: artifact for artifact in exported}
    models = {artifact.id: artifact_model(artifact) for artifact in exported}
    evidence_artifact = (
        by_id.get(run["evidence_artifact_id"]) if run.get("evidence_artifact_id") else None
    )
    evidence = HarnessEvidence.model_validate(evidence_artifact.body) if evidence_artifact else None
    current = next(
        (row for row in revision_rows if row["revision"] == run["current_revision"]), None
    )
    edit_artifact = by_id[current["artifact_id"]] if current else None
    edit = models[edit_artifact.id] if edit_artifact else None
    if edit_artifact is not None and not isinstance(edit, TopicEditSpec):
        raise ValueError("current topic revision does not contain a topic edit")
    selections = [
        artifact
        for artifact in exported
        if isinstance(models[artifact.id], (TopicSelectionRecord, TopicProposal))
    ]
    selected_sha = (
        edit_artifact.metadata.get("selectionSha256")
        or edit_artifact.metadata.get("proposalSha256")
        if edit_artifact
        else None
    )
    selection_artifact = (
        next((artifact for artifact in selections if artifact.sha256 == selected_sha), None)
        if selected_sha
        else selections[-1]
        if selections
        else None
    )
    selection = models[selection_artifact.id] if selection_artifact else None
    assessments = [
        artifact
        for artifact in exported
        if isinstance(models[artifact.id], (TopicAssessment, TopicSelectionAssessment))
        and selection_artifact is not None
        and (
            getattr(models[artifact.id], "selectionSha256", None)
            or getattr(models[artifact.id], "proposalSha256", None)
        )
        == selection_artifact.sha256
    ]
    assessment_artifact = assessments[-1] if assessments else None
    assessment = models[assessment_artifact.id] if assessment_artifact else None
    descriptors = [
        artifact
        for artifact in exported
        if isinstance(models[artifact.id], TopicRenders)
        and edit_artifact is not None
        and cast("TopicRenders", models[artifact.id]).editSha256 == edit_artifact.sha256
    ]
    if len(descriptors) > 1:
        raise ValueError("current topic edit has ambiguous render descriptors")
    descriptor = descriptors[0] if descriptors else None
    proposal = (
        selection.draft.proposal
        if isinstance(selection, TopicSelectionRecord)
        else selection
        if isinstance(selection, TopicProposal)
        else None
    )
    final_candidates = (
        proposal.candidates
        if proposal is not None
        else [video.candidate for video in edit.videos]
        if isinstance(edit, TopicEditSpec)
        else []
    )
    if isinstance(assessment, TopicSelectionAssessment) and assessment.portfolioReview is not None:
        declined = {
            decision.candidateId
            for decision in assessment.portfolioReview.selection
            if decision.disposition == "decline"
        }
        final_candidates = [
            candidate for candidate in final_candidates if candidate.id not in declined
        ]
    route_wrapper = run["route_snapshot"]
    snapshot = route_wrapper.get("snapshot")
    rubric_sha = (
        selection.rubricSha256
        if isinstance(selection, TopicSelectionRecord)
        else digest(
            {
                "policy": policy,
                "authorBrief": run["brief"],
                "reviewerAudience": "interested general viewer",
            }
        )
    )
    attempt_facts: list[TopicAttempt] = []
    author_routes: dict[str, Any] = {}
    reviewer_routes: dict[str, Any] = {}
    prompt_versions: set[str] = set()
    schema_versions: set[str] = set()
    programs: set[str] = set()
    for row in attempts:
        result = by_id.get(row["result_artifact_id"]) if row.get("result_artifact_id") else None
        metadata = result.metadata if result else {}
        route: dict[str, Any] = (
            cast("dict[str, Any]", row["route"]) if isinstance(row.get("route"), dict) else {}
        )
        for field, target in (
            ("promptVersion", prompt_versions),
            ("schemaVersion", schema_versions),
            ("programVersion", programs),
        ):
            if isinstance(metadata.get(field), str):
                target.add(str(metadata[field]))
        stage = str(row["operation_stage"])
        if row.get("model"):
            target_routes = reviewer_routes if stage.startswith("verify") else author_routes
            target_routes[str(route.get("id") or row.get("model"))] = route or {
                "model": row["model"],
                "provider": row["provider"],
                "family": row.get("family"),
            }
        provider = str(row["provider"])
        attempt_facts.append(
            TopicAttempt(
                stage=stage,
                fact=AttemptFact(
                    id=row["id"],
                    operation_id=row["operation_id"],
                    attempt_number=int(row["attempt_number"]),
                    state=cast("Any", str(row["state"])),
                    provider=provider,
                    model=row.get("model"),
                    family=row.get("family"),
                    route_id=route.get("id"),
                    synthetic=provider in {"recorded", "synthetic-recorded"},
                    replayed=metadata.get("cassetteMode") == "replay",
                    estimated_cost_micros=int(row["estimated_cost_micros"]),
                    actual_cost_micros=row.get("actual_cost_micros"),
                    cost_status=cast("Any", str(row["cost_status"])),
                    reservation_active=row.get("reservation_state") == "active",
                    response_present=result is not None,
                    result_artifact_id=row.get("result_artifact_id"),
                    usage=row["usage"],
                    dispatched_at=row.get("dispatched_at"),
                    finished_at=row.get("finished_at"),
                ),
            )
        )
    stages = [
        StageObservation(
            stage="discovery",
            status="complete" if selection_artifact else "not_run",
            artifact_sha256s=(selection_artifact.sha256,) if selection_artifact else (),
            reason=(
                "A retained proposal records completed authoring, "
                "not proof of exhaustive understanding."
            ),
        ),
        StageObservation(
            stage="selection",
            status="complete"
            if isinstance(assessment, TopicSelectionAssessment)
            and assessment.executionStatus == "complete"
            else "partial"
            if assessment_artifact
            else "not_run",
            artifact_sha256s=(assessment_artifact.sha256,) if assessment_artifact else (),
            reason="Independent reviews are measured separately from author output.",
        ),
        StageObservation(
            stage="compilation",
            status="complete" if edit_artifact else "not_run",
            artifact_sha256s=(edit_artifact.sha256,) if edit_artifact else (),
            reason="Current immutable compiled revision presence.",
        ),
        StageObservation(
            stage="rendering",
            status="complete" if descriptor else "not_run",
            artifact_sha256s=(descriptor.sha256,) if descriptor else (),
            reason=(
                "Current exact edit render descriptor presence; media quality requires observation."
            ),
        ),
    ]
    if isinstance(assessment, TopicAssessment):
        all_reviewed = bool(assessment.candidates) and all(
            candidate.coldReview is not None and candidate.sourceReview is not None
            for candidate in assessment.candidates
        )
        stages[1] = StageObservation(
            stage="selection",
            status="complete" if all_reviewed else "partial",
            artifact_sha256s=(assessment_artifact.sha256,) if assessment_artifact else (),
            reason="V1 reviews supplied candidates and does not establish opportunity coverage.",
        )
    evidence_config = evidence.config if evidence else {}
    configuration = TopicConfiguration(
        configuration_id=str(run["config"].get("routeSnapshotId") or digest(route_wrapper)),
        policy=policy,
        source_sha256=route_wrapper.get("pinnedSource", {}).get("sha256"),
        transcript_sha256=evidence.transcriptSha256
        if evidence
        else route_wrapper.get("pinnedTranscript", {}).get("sha256"),
        rubric_sha256=rubric_sha,
        route_snapshot_sha256=digest(snapshot) if snapshot else None,
        route_snapshot=snapshot,
        program_identity=cast("Any", {"policy": policy, "programs": sorted(programs)}),
        author_identity=author_routes,
        reviewer_identity=reviewer_routes,
        prompt_identity=cast("Any", {"versions": sorted(prompt_versions)}),
        schema_identity=cast("Any", {"versions": sorted(schema_versions)}),
        media_identity={
            "compilerVersion": edit.compilerVersion if isinstance(edit, TopicEditSpec) else None,
            "evidenceConfig": evidence_config,
        },
        execution_identity={
            "config": run["config"],
            "initialBudgetMicros": route_wrapper.get("initialBudgetMicros"),
        },
    )
    bundle = TopicEvaluationBundle(
        source_id=source_id,
        recording_group=recording_group or str(source_id),
        source_fingerprint=evidence.sourceFingerprint if evidence else None,
        duration_ms=evidence.durationMs if evidence else int(source.get("duration_ms") or 0),
        run_id=run_id,
        observed_at=observed["observed_at"],
        split=cast("Any", split),
        status=str(run["status"]),
        current_revision=int(run["current_revision"]),
        accepted_revision=run.get("accepted_revision"),
        configuration=configuration,
        evidence_sha256=evidence_artifact.sha256 if evidence_artifact else None,
        evidence=evidence,
        artifacts=tuple(exported),
        revisions=tuple(
            TopicRevision(
                revision=int(row["revision"]),
                artifact_id=row["artifact_id"],
                parent_revision=row.get("base_revision"),
                created_at=row["created_at"],
            )
            for row in revision_rows
        ),
        review_events=tuple(
            TopicReviewEvent(
                id=row["id"],
                action=str(row["action"]),
                state=cast("Any", str(row["state"])),
                base_revision=int(row["base_revision"]),
                resulting_revision=row.get("resulting_revision"),
                created_at=row["created_at"],
                payload=row["payload"],
            )
            for row in events
        ),
        attempts=tuple(attempt_facts),
        stages=tuple(stages),
        final_candidate_sha256s=tuple(
            digest(candidate.model_dump(mode="json")) for candidate in final_candidates
        ),
        final_selection_sha256=selection_artifact.sha256 if selection_artifact else None,
        final_edit_sha256=edit_artifact.sha256 if edit_artifact else None,
        final_renders_sha256=descriptor.sha256 if descriptor else None,
        limitations=(
            (
                "Provider responses and binary media are immutable references; "
                "this export does not claim playback inspection."
            ),
        ),
    )
    validate_topic_bundle(bundle)
    return bundle
