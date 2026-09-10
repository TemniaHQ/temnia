"""Verify V2 post-render judgments against their immutable inputs and paid response."""

# This package-level adapter shares the artifact read fence with its owner.
# ruff: noqa: EM101, TRY003, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import TextPart

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterRenders,
    HarnessArtifactKind,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.editorial import (
    EDITORIAL_VERDICT_SCHEMA_VERSION,
    EditorialVerdictV2,
    ground_editorial_verdict,
)
from temnia_pipeline.harness.editorial_policy import EDITORIAL_POLICY
from temnia_pipeline.harness.editorial_versions import (
    LOCAL_TIMING_EVIDENCE_GENERATION,
    editorial_prompt_generation,
)
from temnia_pipeline.harness.rendering import kept_sections
from temnia_pipeline.harness.validators import HarnessValidationError, rounded_milliseconds

if TYPE_CHECKING:
    from uuid import UUID

    from temnia_pipeline.contracts import HarnessArtifactRef
    from temnia_pipeline.harness.activities import HarnessActivities
    from temnia_pipeline.harness.runtime_types import FinalizeVerificationRequest


class EditorialReceiptReader:
    """One scoped read context, checking every supplied reference against storage."""

    def __init__(self, owner: HarnessActivities, request: FinalizeVerificationRequest) -> None:
        self.owner = owner
        self.request = request
        self.scope = Scope(
            organizationId=request.run.scope_organization_id,
            userId=request.run.scope_user_id,
        )

    async def load(
        self, artifact_id: UUID, *, kind: str, reference: HarnessArtifactRef | None = None
    ) -> tuple[artifacts.HarnessArtifact, object]:
        """Read accepted bytes, preserving the source deletion and content-hash fences."""
        accepted = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.scope,
            source_id=self.request.run.source_id,
            artifact_id=artifact_id,
        )
        if accepted.kind != kind or (
            reference is not None and self.owner._artifact_ref(accepted) != reference
        ):
            raise HarnessValidationError(
                "editorial artifact reference differs from accepted storage"
            )
        raw = await artifacts.read_artifact_json(
            self.owner.ctx.settings.database_url,
            scope=self.scope,
            source_id=self.request.run.source_id,
            store=self.owner.ctx.store,
            artifact_id=accepted.id,
        )
        return accepted, raw


async def validate_postrender_editorial_receipt(  # noqa: C901, PLR0912, PLR0915
    owner: HarnessActivities, request: FinalizeVerificationRequest
) -> tuple[EditorialVerdictV2, artifacts.HarnessArtifact]:
    """Accept only the actual independent model output for this exact rendered edit."""
    reader = EditorialReceiptReader(owner, request)
    ref = request.run
    run = await runs.get_run(
        owner.ctx.settings.database_url,
        scope=reader.scope,
        source_id=ref.source_id,
        run_id=ref.run_id,
    )
    if run.editorial_policy != EDITORIAL_POLICY:
        raise HarnessValidationError("legacy run cannot publish a V2 editorial verification")
    _, edit_raw = await reader.load(request.edit.id, kind="edit", reference=request.edit)
    edit = ChapterEditSpec.model_validate(edit_raw)
    evidence_artifact, evidence_raw = await reader.load(edit.evidenceArtifactId, kind="evidence")
    evidence = HarnessEvidence.model_validate(evidence_raw)
    _, descriptor_raw = await reader.load(
        request.rendered.descriptor.id, kind="render", reference=request.rendered.descriptor
    )
    descriptor = ChapterRenders.model_validate(descriptor_raw)
    if (
        request.edit.kind != HarnessArtifactKind.edit
        or edit.sourceId != ref.source_id
        or evidence.sourceId != ref.source_id
        or edit.evidenceSha256 != evidence_artifact.sha256
        or run.evidence_artifact_id != evidence_artifact.id
        or descriptor.runId != ref.run_id
        or descriptor.editSha256 != request.edit.sha256
    ):
        raise HarnessValidationError("editorial evidence, edit and render lineage do not agree")
    kept = kept_sections(edit)
    if [section.section_id for section in kept] != [
        render.sectionId for render in descriptor.renders
    ] or request.rendered.has_kept_sections != bool(kept):
        raise HarnessValidationError("editorial descriptor does not cover the kept edit sections")
    reports: list[object] = []
    for section, render in zip(kept, descriptor.renders, strict=True):
        if (
            render.editSha256 != request.edit.sha256
            or render.checks is None
            or render.durationMs != rounded_milliseconds(section.duration)
        ):
            raise HarnessValidationError("editorial render belongs to another physical section")
        _, checks_raw = await reader.load(render.checks.id, kind="checks", reference=render.checks)
        reports.append(checks_raw)
    if reports != list(request.rendered.technical_report):
        raise HarnessValidationError("editorial technical report differs from the rendered checks")
    verdict = EditorialVerdictV2.model_validate_json(artifacts.canonical_json(request.verdict))
    async with db.scoped(owner.ctx.settings.database_url, reader.scope) as conn:
        rows = await (
            await conn.execute(
                """SELECT a.id, a.sha256, o.id AS operation_id, o.input_hash,
                      t.id AS attempt_id, t.provider, t.model, t.family, t.route
                 FROM harness_operation o
                 JOIN harness_artifact a ON a.id = o.result_artifact_id
                 JOIN harness_attempt t ON t.operation_id = o.id
                   AND t.result_artifact_id = a.id AND t.state = 'succeeded'
                WHERE o.run_id = %s AND o.source_id = %s AND o.kind = 'model'
                  AND o.stage = %s AND o.status = 'succeeded' AND a.kind = 'model_response'""",
                (ref.run_id, ref.source_id, f"verify:revision:{request.revision}"),
            )
        ).fetchall()
        families = await (
            await conn.execute(
                """SELECT DISTINCT a.family FROM harness_attempt a
                 JOIN harness_operation o ON o.id = a.operation_id
                WHERE a.run_id = %s AND a.source_id = %s AND a.family IS NOT NULL
                  AND o.stage NOT LIKE 'verify:%%'""",
                (ref.run_id, ref.source_id),
            )
        ).fetchall()
    if len(rows) != 1:
        raise HarnessValidationError("editorial verification requires one accepted model response")
    row = rows[0]
    retained, response_raw = await reader.load(row["id"], kind="model_response")
    metadata = retained.metadata
    prompt_generation = editorial_prompt_generation(metadata.get("promptVersion"))
    route_id = metadata.get("routeId")
    if not isinstance(route_id, str):
        raise HarnessValidationError("editorial response has no qualified route identity")
    route = run.route_snapshot.route(route_id)
    if (
        request.verifier_family in {str(item["family"]) for item in families}
        or row["family"] != request.verifier_family
        or route.family != request.verifier_family
        or row["provider"] != route.provider
        or row["model"] != route.gateway_model
        or row["route"] != route.model_dump(mode="json")
        or metadata.get("route") != route.model_dump(mode="json")
        or retained.sha256 != row["sha256"]
        or metadata.get("attemptId") != str(row["attempt_id"])
        or metadata.get("operationId") != str(row["operation_id"])
        or metadata.get("runId") != str(ref.run_id)
        or metadata.get("programVersion") != "chapter-workflow/1"
        or metadata.get("schemaVersion") != EDITORIAL_VERDICT_SCHEMA_VERSION
        or set(retained.dependency_ids)
        != {evidence_artifact.id, request.edit.id, request.rendered.descriptor.id}
    ):
        raise HarnessValidationError(
            "editorial response is not independent or has unrelated lineage"
        )
    request_hash = metadata.get("requestHash")
    if not isinstance(request_hash, str):
        raise HarnessValidationError("editorial response is missing its exact request identity")
    _, input_hash, _ = ledger.operation_identity(
        run_id=ref.run_id,
        kind=ledger.OperationKind.MODEL,
        inputs={
            "descriptorArtifactId": str(request.rendered.descriptor.id),
            "descriptorSha256": request.rendered.descriptor.sha256,
            "editArtifactId": str(request.edit.id),
            "editSha256": request.edit.sha256,
            "requestHash": request_hash,
        },
        config={},
    )
    if row["input_hash"] != input_hash:
        raise HarnessValidationError("editorial response was generated for different edit inputs")
    try:
        response = MODEL_RESPONSE_ADAPTER.validate_python(response_raw)
        parts = [part for part in response.parts if isinstance(part, TextPart)]
        recovered = (
            EditorialVerdictV2.model_validate_json(parts[0].content) if len(parts) == 1 else None
        )
    except (TypeError, ValueError) as error:
        raise HarnessValidationError(
            "editorial output differs from its retained response"
        ) from error
    if recovered != verdict:
        raise HarnessValidationError("editorial output differs from its retained response")
    ground_editorial_verdict(
        evidence=evidence,
        edit=edit,
        verdict=verdict,
        require_local_timing_evidence=prompt_generation >= LOCAL_TIMING_EVIDENCE_GENERATION,
    )
    return verdict, retained
