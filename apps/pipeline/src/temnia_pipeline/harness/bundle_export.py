"""Read-only export of one source-scoped chapter evaluation bundle."""

# The exporter validates every persisted identity before emitting a portable fact set.
# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0915, TRY003, TRY004

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, LiteralString, cast
from uuid import UUID

from temnia_pipeline import db
from temnia_pipeline.chapter_llama.candidate import CANDIDATE_MODEL, CandidatePayload
from temnia_pipeline.chapter_llama.contracts import digest
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
    ChapterLlamaCandidateArtifact,
    CheckArtifact,
    EditorialVerificationArtifact,
    EditorialVerificationBody,
    EvaluationBundle,
    ImmutableArtifactFact,
    ProposalDiagnosticArtifact,
    Provenance,
    ReviewEvent,
    SummaryGroundingArtifact,
    validate_bundle,
)
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_summary_prompt,
    render_summary_reduction_prompt,
)
from temnia_pipeline.harness.proposal_diagnostics import (
    DIAGNOSTIC_FORMAT,
    ProposalDiagnosticReport,
    validate_diagnostic_report,
)
from temnia_pipeline.harness.summary_grounding import (
    GROUNDING_FORMAT,
    SummaryGroundingRefusal,
    SummaryGroundingReportType,
    coverage_fallback_window_count,
    fallback_unit_count,
    ground_summary,
    normalized_summary_from_response,
    read_summary_grounding_report,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

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
    grounding_ids: tuple[UUID, ...]
    diagnostic_ids: tuple[UUID, ...]
    rows: Mapping[UUID, Mapping[str, Any]]
    dependencies: Mapping[UUID, tuple[UUID, ...]]
    attempts: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row["metadata"]
    if not isinstance(value, dict):
        raise ValueError("accepted artifact metadata is not a JSON object")
    return cast("Mapping[str, Any]", value)


def _chapter_llama_response_id(row: Mapping[str, Any]) -> UUID | None:
    """Discover retained failures without treating their output as an accepted result."""
    usage = row.get("usage")
    if not isinstance(usage, dict) or "candidateArtifactId" not in usage:
        return None
    if (
        row.get("provider") != "modal"
        or row.get("family") != "llama"
        or row.get("model") != CANDIDATE_MODEL
        or row.get("state") not in {"succeeded", "failed_known"}
    ):
        raise ValueError("retained Chapter-Llama response has no matching terminal attempt")
    raw_id = cast("object", usage["candidateArtifactId"])
    if not isinstance(raw_id, str):
        raise ValueError("retained Chapter-Llama artifact ID must be a canonical UUID")
    response_id = UUID(raw_id)
    if str(response_id) != raw_id:
        raise ValueError("retained Chapter-Llama artifact ID must be a canonical UUID")
    expected_result = response_id if row.get("state") == "succeeded" else None
    if row.get("result_artifact_id") != expected_result:
        raise ValueError("retained Chapter-Llama response differs from its attempt result")
    return response_id


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
        size_bytes=int(row["size_bytes"]),
        storage_key=str(row["storage_key"]),
    )


def _summary_grounding_report(value: object) -> SummaryGroundingReportType:
    """Validate a decoded JSON value with Pydantic's strict JSON conversions."""
    try:
        return read_summary_grounding_report(value)
    except SummaryGroundingRefusal as error:
        raise ValueError("summary grounding body is invalid") from error


def _proposal_diagnostic_report(value: object) -> ProposalDiagnosticReport:
    """Validate a decoded diagnostic with Pydantic's strict JSON conversions."""
    return ProposalDiagnosticReport.model_validate_json(
        artifacts.canonical_json(value), strict=True
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

        grounding_rows = await (
            await conn.execute(
                """
                SELECT * FROM harness_artifact
                 WHERE source_id = %s AND kind = 'checks'
                   AND metadata->>'format' = %s
                   AND metadata->>'runId' = %s
                 ORDER BY created_at, id
                 LIMIT %s
                """,
                (source_id, GROUNDING_FORMAT, str(run_id), MAX_EXPORT_ARTIFACTS + 1),
            )
        ).fetchall()
        if len(grounding_rows) > MAX_EXPORT_ARTIFACTS:
            raise ValueError(f"run exceeds {MAX_EXPORT_ARTIFACTS} summary grounding artifacts")
        grounding_ids = tuple(row["id"] for row in grounding_rows)

        diagnostic_rows = await (
            await conn.execute(
                """
                SELECT * FROM harness_artifact
                 WHERE source_id = %s AND kind = 'checks'
                   AND metadata->>'format' = %s
                   AND metadata->>'runId' = %s
                 ORDER BY created_at, id
                 LIMIT %s
                """,
                (source_id, DIAGNOSTIC_FORMAT, str(run_id), MAX_EXPORT_ARTIFACTS + 1),
            )
        ).fetchall()
        if len(diagnostic_rows) > MAX_EXPORT_ARTIFACTS:
            raise ValueError(f"run exceeds {MAX_EXPORT_ARTIFACTS} proposal diagnostic artifacts")
        diagnostic_ids = tuple(row["id"] for row in diagnostic_rows)

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

        candidate_ids = tuple(
            response_id
            for row in attempts
            if (response_id := _chapter_llama_response_id(row)) is not None
        )
        root_ids = {
            value
            for value in (
                run.get("evidence_artifact_id"),
                edit_id,
                descriptor_id,
                verification_id,
                *grounding_ids,
                *diagnostic_ids,
                *candidate_ids,
                *(row.get("result_artifact_id") for row in attempts),
            )
            if value is not None
        }
        dependency_rows: Sequence[Mapping[str, Any]] = ()
        dependency_roots = tuple(
            value
            for value in (
                descriptor_id,
                verification_id,
                *grounding_ids,
                *diagnostic_ids,
                *candidate_ids,
                *(
                    row.get("result_artifact_id")
                    for row in attempts
                    if row.get("provider") == "modal" and row.get("family") == "llama"
                ),
            )
            if value is not None
        )
        if dependency_roots:
            dependency_rows = await (
                await conn.execute(
                    """
                    SELECT artifact_id, input_artifact_id
                      FROM harness_artifact_dependency
                     WHERE artifact_id = ANY(%s)
                     ORDER BY artifact_id, input_artifact_id
                    """,
                    (list(dependency_roots),),
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
            grounding_ids=grounding_ids,
            diagnostic_ids=diagnostic_ids,
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

    summary_grounding: list[SummaryGroundingArtifact] = []
    accepted_response_ids = {
        row["result_artifact_id"]
        for row in snapshot.attempts
        if row.get("result_artifact_id") is not None and row.get("state") == "succeeded"
    }
    proposal_diagnostics: list[ProposalDiagnosticArtifact] = []
    accepted_responses = {
        row["result_artifact_id"]: row
        for row in snapshot.attempts
        if row.get("result_artifact_id") is not None and row.get("state") == "succeeded"
    }
    chapter_llama_candidates: list[ChapterLlamaCandidateArtifact] = []
    for attempt_row in snapshot.attempts:
        response_id = _chapter_llama_response_id(attempt_row)
        if response_id is None:
            continue
        response_row = snapshot.rows[response_id]
        metadata = _metadata(response_row)
        if metadata.get("schemaVersion") != "chapter-llama-candidate/1":
            raise ValueError("retained Chapter-Llama artifact has an invalid schema identity")
        candidate = CandidatePayload.model_validate(bodies[response_id])
        route = candidate.configuration.model_dump(mode="json")
        inputs = {
            "evidenceId": str(candidate.evidence_id),
            "evidenceSha256": candidate.evidence_sha256,
            "inputSha256": candidate.input_sha256,
        }
        if (
            candidate.job.organization_id != scope.organizationId
            or candidate.job.source_id != source_id
            or candidate.evidence_id != evidence_id
            or set(snapshot.dependencies.get(response_id, ())) != {evidence_id}
            or candidate.job.attempt_id != attempt_row["id"]
            or candidate.job.operation_id != attempt_row["operation_id"]
            or attempt_row.get("route") != route
            or attempt_row.get("request_hash") != digest({"inputs": inputs, "configuration": route})
            or metadata.get("runId") != str(run_id)
            or metadata.get("operationId") != str(attempt_row["operation_id"])
            or metadata.get("promptVersion")
            != candidate.configuration.deployment.config.prompt_version
        ):
            raise ValueError("Chapter-Llama candidate dependency or source scope is invalid")
        chapter_llama_candidates.append(
            ChapterLlamaCandidateArtifact(
                artifact=_artifact_fact(response_row),
                evidence=_artifact_fact(snapshot.rows[candidate.evidence_id]),
                body=candidate,
            )
        )
    for diagnostic_id in snapshot.diagnostic_ids:
        diagnostic_row = snapshot.rows[diagnostic_id]
        body = _proposal_diagnostic_report(bodies[diagnostic_id])
        metadata = _metadata(diagnostic_row)
        expected_metadata = {
            "attemptId": str(body.attemptId),
            "code": body.code,
            "evidenceArtifactId": str(body.evidence.id),
            "format": body.format,
            "maxOutputTokens": body.maxOutputTokens,
            "modelStage": body.modelStage,
            "operationId": str(body.operationId),
            "promptVersion": body.promptVersion,
            "responseArtifactId": str(body.response.id),
            "routeId": body.routeId,
            "runId": str(body.runId),
            "schemaVersion": body.schemaVersion,
        }
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ValueError("proposal diagnostic artifact metadata is invalid")
        if body.runId != run_id or body.evidence.id != evidence_id:
            raise ValueError("proposal diagnostic belongs to different run evidence")
        accepted_attempt = accepted_responses.get(body.response.id)
        if accepted_attempt is None:
            raise ValueError("proposal diagnostic response is not an accepted run attempt")
        response_row = snapshot.rows.get(body.response.id)
        if response_row is None:
            raise ValueError("proposal diagnostic response artifact is absent")
        response_metadata = _metadata(response_row)
        route = (
            cast("Mapping[str, Any]", accepted_attempt["route"])
            if isinstance(accepted_attempt.get("route"), dict)
            else {}
        )
        if (
            body.attemptId != accepted_attempt["id"]
            or body.operationId != accepted_attempt["operation_id"]
            or body.providerResponseId != accepted_attempt.get("remote_handle")
            or response_metadata.get("attemptId") != str(accepted_attempt["id"])
            or response_metadata.get("operationId") != str(accepted_attempt["operation_id"])
            or response_metadata.get("runId") != str(run_id)
            or response_metadata.get("routeId") != body.routeId
            or route.get("id") != body.routeId
            or response_metadata.get("promptVersion") != body.promptVersion
            or response_metadata.get("schemaVersion") != body.schemaVersion
            or response_metadata.get("maxOutputTokens") != body.maxOutputTokens
        ):
            raise ValueError("proposal diagnostic response lineage is invalid")
        response = MODEL_RESPONSE_ADAPTER.validate_python(bodies[body.response.id])
        if body.providerResponseId != response.provider_response_id:
            raise ValueError("proposal diagnostic response facts are invalid")
        try:
            validate_diagnostic_report(body, response)
        except ValueError as error:
            raise ValueError(
                "proposal diagnostic differs from its retained response body"
            ) from error
        refs = (body.evidence, body.response, *body.inputArtifacts)
        ref_ids = [ref.id for ref in refs]
        dependency_ids = snapshot.dependencies.get(diagnostic_id, ())
        if (
            len(ref_ids) != len(set(ref_ids))
            or set(dependency_ids) != set(ref_ids)
            or len(dependency_ids) != len(ref_ids)
        ):
            raise ValueError("proposal diagnostic artifact dependency closure is invalid")
        dependency_facts: list[ImmutableArtifactFact] = []
        for ref in refs:
            dependency_row = snapshot.rows.get(ref.id)
            if dependency_row is None or _artifact_ref(dependency_row) != ref:
                raise ValueError("proposal diagnostic dependency identity is invalid")
            dependency_facts.append(_artifact_fact(dependency_row))
        for ref in body.inputArtifacts:
            input_row = snapshot.rows[ref.id]
            input_metadata = _metadata(input_row)
            if input_row["kind"] == "model_response":
                if ref.id not in accepted_response_ids or input_metadata.get("runId") != str(
                    run_id
                ):
                    raise ValueError("proposal diagnostic input response lineage is invalid")
                continue
            if (
                input_row["kind"] != "checks"
                or ref.id == diagnostic_id
                or input_metadata.get("runId") != str(run_id)
                or input_metadata.get("format") not in {GROUNDING_FORMAT, DIAGNOSTIC_FORMAT}
            ):
                raise ValueError("proposal diagnostic input artifact lineage is invalid")
        proposal_diagnostics.append(
            ProposalDiagnosticArtifact(
                artifact=_artifact_fact(diagnostic_row),
                body=body,
                dependencies=tuple(dependency_facts),
            )
        )
    for grounding_id in snapshot.grounding_ids:
        grounding_row = snapshot.rows[grounding_id]
        body = _summary_grounding_report(bodies[grounding_id])
        metadata = _metadata(grounding_row)
        expected_metadata = {
            "format": body.format,
            "runId": str(body.runId),
            "windowId": body.windowId,
            "hierarchyLevel": body.hierarchyLevel,
            "modelStage": body.modelStage,
            "fallbackUnitCount": fallback_unit_count(body),
            "fallbackQuoteCount": sum(
                len(fallback.rejectedQuoteWordIds) for fallback in body.fallbacks
            ),
        }
        coverage_count = coverage_fallback_window_count(body)
        if coverage_count:
            expected_metadata["coverageFallbackWindowCount"] = coverage_count
        elif "coverageFallbackWindowCount" in metadata:
            raise ValueError("summary grounding artifact metadata is invalid")
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ValueError("summary grounding artifact metadata is invalid")
        if body.runId != run_id or body.evidence.id != evidence_id:
            raise ValueError("summary grounding artifact belongs to a different run evidence")
        if body.rawResponse.id not in accepted_response_ids:
            raise ValueError("summary grounding response is not an accepted run attempt")
        try:
            source_summary = normalized_summary_from_response(bodies[body.rawResponse.id])
        except SummaryGroundingRefusal as error:
            raise ValueError("summary grounding raw response body is invalid") from error
        source_summary_sha256 = hashlib.sha256(
            artifacts.canonical_json(source_summary.model_dump(mode="json"))
        ).hexdigest()
        if source_summary_sha256 != body.sourceSummarySha256:
            raise ValueError("summary grounding source summary hash is invalid")
        if evidence is None:
            raise ValueError("summary grounding artifact has no accepted evidence body")
        language_value = evidence.config.get("detectedLanguage")
        detected_language = (
            language_value.strip()
            if isinstance(language_value, str) and language_value.strip()
            else None
        )
        positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
        try:
            window_start = positions[body.firstSentenceId]
            window_end = positions[body.lastSentenceId]
        except KeyError as error:
            raise ValueError("summary grounding window names a foreign sentence") from error
        if body.hierarchyLevel == 1:
            source_sentences = evidence.sentences[window_start : window_end + 1]
            expected_prompt = render_summary_prompt(
                PromptWindow(
                    sourceId=source_id,
                    evidenceSha256=body.evidence.sha256,
                    windowId=body.windowId,
                    firstSentenceId=body.firstSentenceId,
                    lastSentenceId=body.lastSentenceId,
                    sentences=tuple(
                        PromptSentence(
                            id=sentence.id,
                            text=sentence.text,
                            firstWordId=sentence.wordIds[0].root,
                            lastWordId=sentence.wordIds[-1].root,
                            speakers=tuple(sentence.speakers),
                        )
                        for sentence in source_sentences
                    ),
                ),
                detected_language=detected_language,
            )
            allowed_model_anchors = frozenset(
                word.root
                for sentence in source_sentences
                for word in (sentence.wordIds[0], sentence.wordIds[-1])
            )
        else:
            input_reports = [
                _summary_grounding_report(bodies[ref.id]) for ref in body.inputArtifacts
            ]
            contained_reports = sorted(
                (
                    report
                    for report in input_reports
                    if report.firstSentenceId in positions
                    and report.lastSentenceId in positions
                    and window_start <= positions[report.firstSentenceId]
                    and positions[report.lastSentenceId] <= window_end
                ),
                key=lambda report: positions[report.firstSentenceId],
            )
            expected_prompt = render_summary_reduction_prompt(
                source_id=source_id,
                evidence_sha256=body.evidence.sha256,
                summaries=[
                    report.normalizedSummary.model_dump(mode="json") for report in contained_reports
                ],
                hierarchy_level=body.hierarchyLevel,
                detected_language=detected_language,
            )
            allowed_model_anchors = frozenset(
                quote
                for report in contained_reports
                for unit in report.normalizedSummary.units
                for quote in unit.quoteWordIds
            )
        if hashlib.sha256(expected_prompt.encode()).hexdigest() != body.windowPromptSha256:
            raise ValueError("summary grounding prompt hash is invalid")
        try:
            expected_grounding = ground_summary(
                evidence=evidence,
                window_first_sentence_id=body.firstSentenceId,
                window_last_sentence_id=body.lastSentenceId,
                window_sentence_count=body.windowSentenceCount,
                summary=source_summary,
                allowed_model_anchors=allowed_model_anchors,
                hierarchy_level=body.hierarchyLevel,
            )
        except SummaryGroundingRefusal as error:
            raise ValueError("summary grounding report cannot be reproduced") from error
        if (
            expected_grounding.summary != body.normalizedSummary
            or expected_grounding.fallbacks != body.fallbacks
            or expected_grounding.coverageDiagnostic != getattr(body, "coverageDiagnostic", None)
            or expected_grounding.coverageFallback != getattr(body, "coverageFallback", None)
        ):
            raise ValueError("summary grounding fallback differs from its raw response")
        refs = (body.evidence, body.rawResponse, *body.inputArtifacts)
        ref_ids = [ref.id for ref in refs]
        if len(ref_ids) != len(set(ref_ids)):
            raise ValueError("summary grounding artifact names duplicate dependencies")
        dependency_ids = snapshot.dependencies.get(grounding_id, ())
        if set(dependency_ids) != set(ref_ids) or len(dependency_ids) != len(ref_ids):
            raise ValueError("summary grounding artifact dependency closure is invalid")
        dependency_facts: list[ImmutableArtifactFact] = []
        for ref in refs:
            dependency_row = snapshot.rows.get(ref.id)
            if dependency_row is None or _artifact_ref(dependency_row) != ref:
                raise ValueError("summary grounding artifact dependency identity is invalid")
            dependency_facts.append(_artifact_fact(dependency_row))
        summary_grounding.append(
            SummaryGroundingArtifact(
                artifact=_artifact_fact(grounding_row),
                body=body,
                dependencies=tuple(dependency_facts),
            )
        )

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
            body=EditorialVerificationBody.model_validate_json(
                artifacts.canonical_json(bodies[snapshot.verification_id])
            ),
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
        response_id = result_id or _chapter_llama_response_id(row)
        if response_id is not None:
            result_row = snapshot.rows[response_id]
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
                response_present=response_id is not None,
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
        summary_grounding=tuple(summary_grounding),
        chapter_llama_candidates=tuple(chapter_llama_candidates),
        proposal_diagnostics=tuple(proposal_diagnostics),
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
