"""Read-only export of one source-scoped chapter evaluation bundle."""

# The exporter validates every persisted identity before emitting a portable fact set.
# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0915, TRY003, TRY004

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, LiteralString, cast

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.evals.chapters import (
    AttemptFact,
    CheckArtifact,
    EditorialVerificationArtifact,
    EditorialVerificationBody,
    EvaluationBundle,
    ImmutableArtifactFact,
    Provenance,
    ReviewEvent,
    validate_bundle,
)
from temnia_pipeline.harness import artifacts

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from obstore.store import S3Store
    from psycopg import AsyncConnection

MAX_EXPORT_ATTEMPTS = 128
MAX_EXPORT_EVENTS = 1000
MAX_EXPORT_ARTIFACTS = 512
MAX_EXPORT_JSON_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Snapshot:
    run: Mapping[str, Any]
    source: Mapping[str, Any]
    observed_at: datetime
    revision: Mapping[str, Any] | None
    descriptor_id: UUID | None
    verification_id: UUID | None
    rows: Mapping[UUID, Mapping[str, Any]]
    dependencies: Mapping[UUID, tuple[UUID, ...]]
    attempts: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row["metadata"]
    if not isinstance(value, dict):
        raise ValueError("accepted artifact metadata is not a JSON object")
    return cast("Mapping[str, Any]", value)


def _artifact_ref(row: Mapping[str, Any]) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        fingerprint=str(row["fingerprint"]),
        id=row["id"],
        kind=HarnessArtifactKind(str(row["kind"])),
        sha256=str(row["sha256"]),
        sizeBytes=int(row["size_bytes"]),
        storageKey=str(row["storage_key"]),
    )


def _artifact_fact(row: Mapping[str, Any]) -> ImmutableArtifactFact:
    return ImmutableArtifactFact(
        id=row["id"],
        source_id=row["source_id"],
        kind=cast("Any", str(row["kind"])),
        fingerprint=str(row["fingerprint"]),
        sha256=str(row["sha256"]),
    )


async def _one_or_none(
    conn: AsyncConnection[dict[str, Any]],
    query: LiteralString,
    params: Sequence[object],
    *,
    name: str,
) -> Mapping[str, Any] | None:
    rows = await (await conn.execute(query, params)).fetchall()
    if len(rows) > 1:
        raise ValueError(f"current run has ambiguous {name} artifacts")
    return rows[0] if rows else None


async def _read_snapshot(
    database_url: str,
    *,
    scope: Scope,
    run_id: UUID,
) -> _Snapshot:
    """Capture every row identity in one finite repeatable-read transaction."""
    pool = await db.get_pool(database_url)
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        await conn.execute(
            "SELECT set_config('app.organization_id', %s, true),"
            " set_config('app.user_id', %s, true)",
            (str(scope.organizationId), str(scope.userId)),
        )
        run = await (
            await conn.execute(
                """
                SELECT * FROM harness_run
                 WHERE id = %s AND lane = 'chapters'
                """,
                (run_id,),
            )
        ).fetchone()
        if run is None:
            raise ValueError("chapter run is absent from the seeded scope")
        source_id = run["source_id"]
        source = await (
            await conn.execute("SELECT * FROM source WHERE id = %s", (source_id,))
        ).fetchone()
        if source is None:
            raise ValueError("chapter run source is absent from the seeded scope")
        observed = await (
            await conn.execute("SELECT transaction_timestamp() AS observed_at")
        ).fetchone()
        if observed is None:
            raise RuntimeError("database did not return the snapshot timestamp")
        current_revision = int(run["current_revision"])
        revision = None
        edit_id = None
        if current_revision:
            revision = await (
                await conn.execute(
                    """
                    SELECT * FROM chapter_revision
                     WHERE run_id = %s AND source_id = %s AND revision = %s
                    """,
                    (run_id, source_id, current_revision),
                )
            ).fetchone()
            if revision is None:
                raise ValueError("current chapter revision row is absent")
            edit_id = revision["artifact_id"]

        descriptor = None
        if edit_id is not None:
            descriptor = await _one_or_none(
                conn,
                """
                SELECT a.* FROM harness_artifact a
                JOIN harness_artifact_dependency d
                  ON d.artifact_id = a.id AND d.input_artifact_id = %s
                 WHERE a.source_id = %s AND a.kind = 'render'
                   AND a.metadata->>'format' = 'chapter-renders/1'
                   AND a.metadata->>'runId' = %s
                 ORDER BY a.created_at DESC LIMIT 2
                """,
                (edit_id, source_id, str(run_id)),
                name="current render descriptor",
            )
        descriptor_id = descriptor["id"] if descriptor is not None else None
        verification = None
        if descriptor_id is not None:
            verification = await _one_or_none(
                conn,
                """
                SELECT a.* FROM harness_artifact a
                JOIN harness_artifact_dependency d
                  ON d.artifact_id = a.id AND d.input_artifact_id = %s
                 WHERE a.source_id = %s AND a.kind = 'checks'
                   AND a.metadata->>'format' = 'chapter-verification/1'
                   AND a.metadata->>'runId' = %s
                   AND a.metadata->>'revision' = %s
                 ORDER BY a.created_at DESC LIMIT 2
                """,
                (descriptor_id, source_id, str(run_id), str(current_revision)),
                name="current editorial verification",
            )
        verification_id = verification["id"] if verification is not None else None

        attempts = await (
            await conn.execute(
                """
                SELECT a.*, r.state AS reservation_state
                  FROM harness_attempt a
                  LEFT JOIN harness_reservation r
                    ON r.organization_id = a.organization_id
                   AND r.source_id = a.source_id AND r.attempt_id = a.id
                 WHERE a.run_id = %s AND a.source_id = %s
                 ORDER BY a.created_at, a.id
                 LIMIT %s
                """,
                (run_id, source_id, MAX_EXPORT_ATTEMPTS + 1),
            )
        ).fetchall()
        if len(attempts) > MAX_EXPORT_ATTEMPTS:
            raise ValueError(f"run exceeds {MAX_EXPORT_ATTEMPTS} physical attempts")
        events = await (
            await conn.execute(
                """
                SELECT action, state, base_revision, resulting_revision, created_at
                  FROM chapter_review_event
                 WHERE run_id = %s AND source_id = %s
                 ORDER BY created_at, id
                 LIMIT %s
                """,
                (run_id, source_id, MAX_EXPORT_EVENTS + 1),
            )
        ).fetchall()
        if len(events) > MAX_EXPORT_EVENTS:
            raise ValueError(f"run exceeds {MAX_EXPORT_EVENTS} review events")

        root_ids = {
            value
            for value in (
                run.get("evidence_artifact_id"),
                edit_id,
                descriptor_id,
                verification_id,
                *(row.get("result_artifact_id") for row in attempts),
            )
            if value is not None
        }
        dependency_rows: Sequence[Mapping[str, Any]] = ()
        if descriptor_id is not None or verification_id is not None:
            dependency_rows = await (
                await conn.execute(
                    """
                    SELECT artifact_id, input_artifact_id
                      FROM harness_artifact_dependency
                     WHERE artifact_id = ANY(%s)
                     ORDER BY artifact_id, input_artifact_id
                    """,
                    ([value for value in (descriptor_id, verification_id) if value],),
                )
            ).fetchall()
        all_ids = root_ids | {row["input_artifact_id"] for row in dependency_rows}
        if len(all_ids) > MAX_EXPORT_ARTIFACTS:
            raise ValueError(f"run export exceeds {MAX_EXPORT_ARTIFACTS} artifact identities")
        artifact_rows: Sequence[Mapping[str, Any]] = ()
        if all_ids:
            artifact_rows = await (
                await conn.execute(
                    "SELECT * FROM harness_artifact WHERE source_id = %s AND id = ANY(%s)",
                    (source_id, list(all_ids)),
                )
            ).fetchall()
        rows = {row["id"]: row for row in artifact_rows}
        if set(rows) != all_ids:
            raise ValueError("a run artifact or dependency is absent from its source scope")
        total_bytes = sum(
            int(row["size_bytes"])
            for row in artifact_rows
            if str(row["kind"]) in {"evidence", "edit", "checks", "model_response"}
            or row["id"] == descriptor_id
        )
        if total_bytes > MAX_EXPORT_JSON_BYTES:
            raise ValueError(
                f"run JSON artifacts exceed the {MAX_EXPORT_JSON_BYTES}-byte export ceiling"
            )
        dependencies: dict[UUID, list[UUID]] = {}
        for row in dependency_rows:
            dependencies.setdefault(row["artifact_id"], []).append(row["input_artifact_id"])
        return _Snapshot(
            run=run,
            source=source,
            observed_at=observed["observed_at"],
            revision=revision,
            descriptor_id=descriptor_id,
            verification_id=verification_id,
            rows=rows,
            dependencies={key: tuple(value) for key, value in dependencies.items()},
            attempts=tuple(attempts),
            events=tuple(events),
        )


async def export_evaluation_bundle(
    database_url: str,
    *,
    scope: Scope,
    store: S3Store,
    run_id: UUID,
    split: Literal["tuning", "test", "qualification"] = "qualification",
) -> EvaluationBundle:
    """Build and validate one bundle without writing the database or object store."""
    snapshot = await _read_snapshot(database_url, scope=scope, run_id=run_id)
    run = snapshot.run
    source = snapshot.source
    source_id = run["source_id"]
    bodies: dict[UUID, object] = {}
    json_ids = {
        artifact_id
        for artifact_id, row in snapshot.rows.items()
        if str(row["kind"]) in {"evidence", "edit", "checks", "model_response"}
        or artifact_id == snapshot.descriptor_id
    }
    for artifact_id in json_ids:
        bodies[artifact_id] = await artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=artifact_id,
        )

    evidence = None
    evidence_sha = None
    evidence_id = run.get("evidence_artifact_id")
    if evidence_id is not None:
        evidence = HarnessEvidence.model_validate(bodies[evidence_id])
        evidence_sha = str(snapshot.rows[evidence_id]["sha256"])
    edit = None
    edit_sha = None
    edit_revision = None
    edit_id = None
    if snapshot.revision is not None:
        edit_id = snapshot.revision["artifact_id"]
        edit = ChapterEditSpec.model_validate(bodies[edit_id])
        edit_sha = str(snapshot.rows[edit_id]["sha256"])
        edit_revision = int(snapshot.revision["revision"])

    renders = None
    descriptor_sha = None
    checks: list[CheckArtifact] = []
    if snapshot.descriptor_id is not None:
        descriptor_row = snapshot.rows[snapshot.descriptor_id]
        renders = ChapterRenders.model_validate(bodies[snapshot.descriptor_id])
        descriptor_sha = str(descriptor_row["sha256"])
        descriptor_metadata = _metadata(descriptor_row)
        if (
            descriptor_metadata.get("runId") != str(run_id)
            or descriptor_metadata.get("editSha256") != edit_sha
        ):
            raise ValueError("current render descriptor metadata is stale")
        descriptor_dependencies = set(snapshot.dependencies[snapshot.descriptor_id])
        if edit_id not in descriptor_dependencies:
            raise ValueError("current render descriptor does not depend on the current edit")
        for render in renders.renders:
            if render.checks is None:
                raise ValueError("current render descriptor omits a section check artifact")
            for ref in (render.media, render.captions, render.checks):
                if ref is None:
                    continue
                row = snapshot.rows.get(ref.id)
                if (
                    row is None
                    or ref.id not in descriptor_dependencies
                    or _artifact_ref(row) != ref
                ):
                    raise ValueError("render descriptor dependency identity is invalid")
            check_row = snapshot.rows[render.checks.id]
            checks.append(
                CheckArtifact(
                    artifact_id=render.checks.id,
                    section_id=render.sectionId,
                    sha256=str(check_row["sha256"]),
                    checks=ChapterChecks.model_validate(bodies[render.checks.id]),
                )
            )

    verification = None
    if snapshot.verification_id is not None:
        if edit_id is None or snapshot.descriptor_id is None:
            raise ValueError("editorial verification has no current edit and descriptor")
        verification_row = snapshot.rows[snapshot.verification_id]
        dependency_ids = snapshot.dependencies[snapshot.verification_id]
        dependency_rows = [snapshot.rows[value] for value in dependency_ids]
        model_rows = [row for row in dependency_rows if str(row["kind"]) == "model_response"]
        if (
            len(model_rows) != 1
            or edit_id not in dependency_ids
            or snapshot.descriptor_id not in dependency_ids
        ):
            raise ValueError("current editorial verification lineage is incomplete")
        metadata = _metadata(verification_row)
        if metadata.get("editSha256") != edit_sha:
            raise ValueError("current editorial verification metadata is stale")
        verifier_family = metadata.get("verifierFamily")
        if not isinstance(verifier_family, str) or not verifier_family:
            raise ValueError("current editorial verification has no verifier family")
        verification = EditorialVerificationArtifact(
            artifact=_artifact_fact(verification_row),
            body=EditorialVerificationBody.model_validate(bodies[snapshot.verification_id]),
            run_id=run_id,
            revision=int(run["current_revision"]),
            verifier_family=verifier_family,
            edit=_artifact_fact(snapshot.rows[edit_id]),
            descriptor=_artifact_fact(snapshot.rows[snapshot.descriptor_id]),
            model_response=_artifact_fact(model_rows[0]),
        )

    model_versions: dict[str, str] = {}
    if evidence is not None:
        model_versions.update(evidence.modelVersions)
    prompt_versions: dict[str, str] = {}
    attempt_facts: list[AttemptFact] = []
    for row in snapshot.attempts:
        response_metadata: Mapping[str, Any] = {}
        result_id = row.get("result_artifact_id")
        if result_id is not None:
            result_row = snapshot.rows[result_id]
            response_metadata = _metadata(result_row)
            prompt_version = response_metadata.get("promptVersion")
            if isinstance(prompt_version, str) and prompt_version:
                prompt_versions[str(row["operation_id"])] = prompt_version
        provider = str(row["provider"])
        route: Mapping[str, Any] = (
            cast("Mapping[str, Any]", row["route"]) if isinstance(row["route"], dict) else {}
        )
        attempt_facts.append(
            AttemptFact(
                id=row["id"],
                operation_id=row["operation_id"],
                attempt_number=int(row["attempt_number"]),
                state=cast("Any", str(row["state"])),
                provider=provider,
                model=row.get("model"),
                family=row.get("family"),
                route_id=cast("str | None", route.get("id")),
                synthetic=(
                    bool(response_metadata["synthetic"])
                    if "synthetic" in response_metadata
                    else provider in {"recorded", "synthetic-recorded"}
                ),
                replayed=response_metadata.get("cassetteMode") == "replay",
                estimated_cost_micros=int(row["estimated_cost_micros"]),
                actual_cost_micros=row.get("actual_cost_micros"),
                cost_status=cast("Any", str(row["cost_status"])),
                reservation_active=row.get("reservation_state") == "active",
                response_present=result_id is not None,
                remote_handle=row.get("remote_handle"),
                result_artifact_id=result_id,
                usage=cast("dict[str, Any]", row["usage"]),
                dispatched_at=row.get("dispatched_at"),
                finished_at=row.get("finished_at"),
            )
        )
        model_versions[f"attempt:{row['id']}"] = str(row.get("model") or "not_observed")

    source_timeline = evidence.config.get("sourceTimeline") if evidence is not None else None
    timeline: Mapping[str, Any] = (
        cast("Mapping[str, Any]", source_timeline) if isinstance(source_timeline, dict) else {}
    )
    source_has_video = (
        bool(timeline.get("hasVideo"))
        if evidence is not None
        else source.get("video_codec") is not None
    )
    source_has_audio = (
        bool(timeline.get("hasAudio"))
        if evidence is not None
        else source.get("audio_codec") is not None
    )
    duration_ms = int(source.get("duration_ms") or 0)
    if duration_ms <= 0:
        raise ValueError("source duration was not observed before bundle export")
    if not source_has_video and not source_has_audio:
        raise ValueError("source selected audio/video presence was not observed")
    config: Mapping[str, Any] = (
        cast("Mapping[str, Any]", run["config"]) if isinstance(run["config"], dict) else {}
    )
    source_object = evidence.config.get("sourceObject") if evidence is not None else None
    source_object_map: Mapping[str, Any] = (
        cast("Mapping[str, Any]", source_object) if isinstance(source_object, dict) else {}
    )
    source_version = source_object_map.get("sha256")
    if not isinstance(source_version, str) or not source_version:
        source_version = "not_observed_before_evidence"
    compiler_version = edit.compilerVersion if edit is not None else "not_observed_before_edit"
    bundle = EvaluationBundle(
        format="temnia-chapter-evaluation-bundle/1",
        source_id=source_id,
        source_fingerprint=evidence.sourceFingerprint if evidence is not None else None,
        duration_ms=duration_ms,
        source_has_video=source_has_video,
        source_has_audio=source_has_audio,
        run_id=run_id,
        observed_at=snapshot.observed_at,
        status=str(run["status"]),
        current_revision=int(run["current_revision"]),
        accepted_revision=run.get("accepted_revision"),
        evidence_sha256=evidence_sha,
        evidence=evidence,
        edit_sha256=edit_sha,
        edit_revision=edit_revision,
        edit=edit,
        render_descriptor_sha256=descriptor_sha,
        renders=renders,
        checks=tuple(checks),
        editorial_verification=verification,
        review_events=tuple(
            ReviewEvent(
                action=str(row["action"]),
                state=cast("Any", str(row["state"])),
                base_revision=int(row["base_revision"]),
                resulting_revision=row.get("resulting_revision"),
                created_at=row["created_at"],
            )
            for row in snapshot.events
        ),
        attempts=tuple(attempt_facts),
        provenance=Provenance(
            source_version=source_version,
            model_versions=model_versions,
            compiler_version=compiler_version,
            prompt_versions=prompt_versions,
            route_snapshot_id=str(config.get("routeSnapshotId") or "not_observed"),
        ),
        split=split,
    )
    validate_bundle(bundle)
    return bundle
