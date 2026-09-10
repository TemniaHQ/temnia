"""Grounded editorial assessments and scoped repair compilation.

Model dispatch stays in the durable workflow. These activities verify immutable
inputs, select qualified seats, and retain the findings behind each correction.
"""

# Refusals are deliberately content-free and adjacent to their invariants.
# ruff: noqa: EM101, TRY003, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai import TextPart
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import ChapterProposal, HarnessArtifactRef, HarnessEvidence, Scope
from temnia_pipeline.harness import artifacts, runs
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.compiler import compile_chapters
from temnia_pipeline.harness.editorial import (
    EDITORIAL_ASSESSMENT_PROMPT_VERSION,
    EDITORIAL_REPAIR_PROMPT_VERSION,
    EDITORIAL_REPAIR_SCHEMA_VERSION,
    EDITORIAL_VERDICT_SCHEMA_VERSION,
    EditorialRepairV1,
    EditorialVerdictV2,
    editorial_compiled_fingerprint,
    ground_editorial_verdict,
    preserved_editorial_constraints,
    render_editorial_assessment_prompt,
    render_editorial_repair_prompt,
    validate_compiled_editorial_repair,
    validate_editorial_repair,
)
from temnia_pipeline.harness.editorial_policy import EDITORIAL_POLICY
from temnia_pipeline.harness.editorial_runtime import (
    CompileEditorialRepairRequest,
    EditorialAssessment,
    EditorialContextRequest,
    EditorialRepairResult,
    FinalizeEditorialRequest,
)
from temnia_pipeline.harness.editorial_versions import (
    LOCAL_TIMING_EVIDENCE_GENERATION,
    editorial_prompt_generation,
)
from temnia_pipeline.harness.models import CompactChapterProposal, canonical_chapter_proposal
from temnia_pipeline.harness.routes import (
    ContextWindowExceeded,
    NoEligibleRoute,
    estimate_cost,
    select_route,
    select_verifier_route,
)
from temnia_pipeline.harness.runtime_types import (
    CompileProposalRequest,
    CompileProposalResult,
    RunSnapshot,
    VerificationPlan,
)
from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from temnia_pipeline.harness.activities import HarnessActivities


class EditorialActivities:
    """Additive activity set; old policies keep their original program."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner

    @staticmethod
    def _scope(request: EditorialContextRequest) -> Scope:
        return Scope(
            organizationId=request.run.scope_organization_id,
            userId=request.run.scope_user_id,
        )

    async def _read(self, request: EditorialContextRequest, ref: HarnessArtifactRef) -> object:
        raw = await artifacts.read_artifact_json(
            self.owner.ctx.settings.database_url,
            scope=self._scope(request),
            source_id=request.run.source_id,
            store=self.owner.ctx.store,
            artifact_id=ref.id,
        )
        if hashlib.sha256(artifacts.canonical_json(raw)).hexdigest() != ref.sha256:
            raise artifacts.ArtifactIntegrityError(
                "editorial input hash differs from its reference"
            )
        return raw

    async def _load(
        self, request: EditorialContextRequest
    ) -> tuple[RunSnapshot, HarnessEvidence, ChapterProposal]:
        self.owner._require_enabled()
        run = await runs.get_run(
            self.owner.ctx.settings.database_url,
            scope=self._scope(request),
            source_id=request.run.source_id,
            run_id=request.run.run_id,
        )
        if run.editorial_policy != EDITORIAL_POLICY:
            raise HarnessValidationError("run did not freeze the editorial policy")
        evidence_raw, proposal_raw, edit_raw = await asyncio.gather(
            self._read(request, request.evidence),
            self._read(request, request.compiled.proposal_artifact),
            self._read(request, request.compiled.edit_artifact),
        )
        if edit_raw != request.compiled.edit.model_dump(mode="json"):
            raise artifacts.ArtifactIntegrityError("editorial edit differs from accepted bytes")
        if (
            request.compiled.edit.evidenceArtifactId != request.evidence.id
            or request.compiled.edit.evidenceSha256 != request.evidence.sha256
            or run.evidence_artifact_id != request.evidence.id
            or request.compiled.edit.sourceId != request.run.source_id
        ):
            raise HarnessValidationError("editorial candidate has unrelated evidence")
        evidence = HarnessEvidence.model_validate(evidence_raw)
        if evidence.sourceId != request.run.source_id:
            raise HarnessValidationError("editorial evidence belongs to another source")
        return run, evidence, ChapterProposal.model_validate(proposal_raw)

    async def _finding(self, request: EditorialContextRequest) -> EditorialVerdictV2:
        if request.assessment is None:
            raise HarnessValidationError("repair requires an immutable assessment")
        raw = cast("dict[str, Any]", await self._read(request, request.assessment))
        if (
            raw.get("format") != "chapter-editorial-assessment/2"
            or raw.get("editSha256") != request.compiled.edit_artifact.sha256
            or raw.get("runId") != str(request.run.run_id)
            or raw.get("evidenceSha256") != request.evidence.sha256
        ):
            raise HarnessValidationError("repair assessment does not own this candidate")
        return EditorialVerdictV2.model_validate_json(artifacts.canonical_json(raw["verdict"]))

    async def _generation_families(self, request: EditorialContextRequest) -> frozenset[str]:
        async with db.scoped(self.owner.ctx.settings.database_url, self._scope(request)) as conn:
            rows = await (
                await conn.execute(
                    """SELECT DISTINCT a.family FROM harness_attempt a
                       JOIN harness_operation o ON o.id = a.operation_id
                       WHERE a.run_id = %s AND a.source_id = %s AND a.family IS NOT NULL
                       AND o.stage NOT LIKE 'verify:%%'""",
                    (request.run.run_id, request.run.source_id),
                )
            ).fetchall()
        return frozenset([*request.generation_families, *(str(row["family"]) for row in rows)])

    @activity.defn(name="prepare_chapter_editorial")
    async def prepare(self, request: EditorialContextRequest) -> VerificationPlan:
        """Prepare assessment or repair against the exact compiled candidate."""
        run, evidence, proposal = await self._load(request)
        try:
            verifier = select_verifier_route(
                run.route_snapshot,
                "verify",
                generation_families=await self._generation_families(request),
            )
            if request.assessment is None:
                route = verifier
                prompt = render_editorial_assessment_prompt(
                    evidence=evidence,
                    edit=request.compiled.edit,
                    brief=run.brief,
                    technical_report=(
                        {"sections": list(request.rendered.technical_report)}
                        if request.rendered is not None
                        else None
                    ),
                )
                fixture = "editorial_assess"
            else:
                verdict = await self._finding(request)
                route = select_route(
                    run.route_snapshot, "propose", excluded_families=frozenset({verifier.family})
                )
                prompt = render_editorial_repair_prompt(
                    evidence=evidence,
                    edit=request.compiled.edit,
                    proposal=proposal,
                    verdict=verdict,
                    brief=run.brief,
                    allow_source_edge_drops=True,
                )
                fixture = "editorial_repair"
            estimate_cost(
                route,
                payload_bytes=len(prompt.encode()),
                max_output_tokens=run.config.maxOutputTokens,
            )
        except (ContextWindowExceeded, NoEligibleRoute, HarnessValidationError) as error:
            return VerificationPlan(refusal=str(error)[:2000])
        return VerificationPlan(
            prompt=prompt,
            route=route,
            synthetic_payload=self.owner._recorded_output(fixture),
            output_cap=run.config.maxOutputTokens,
            dispatch_limit=run.config.maxDispatches,
            editorial_prompt_version=(
                EDITORIAL_REPAIR_PROMPT_VERSION
                if request.assessment is not None
                else EDITORIAL_ASSESSMENT_PROMPT_VERSION
            ),
        )

    async def _model_response(  # noqa: C901
        self,
        request: EditorialContextRequest,
        stage: str,
        *,
        output: EditorialVerdictV2 | EditorialRepairV1,
        family: str,
        run: RunSnapshot,
    ) -> tuple[UUID, str, int]:
        repair = isinstance(output, EditorialRepairV1)
        if not repair and family in await self._generation_families(request):
            raise HarnessValidationError("editorial verifier is not independent of generation")
        if repair:
            if request.assessment is None:
                raise HarnessValidationError("editorial repair has no independent assessment")
            assessment = cast("dict[str, Any]", await self._read(request, request.assessment))
            verifier_family = assessment.get("verifierFamily")
            if not isinstance(verifier_family, str) or verifier_family == family:
                raise HarnessValidationError("editorial repair shares its verifier's family")
        async with db.scoped(self.owner.ctx.settings.database_url, self._scope(request)) as conn:
            rows = await (
                await conn.execute(
                    """SELECT a.id, a.sha256, a.fingerprint, o.id AS operation_id,
                              t.id AS attempt_id, t.provider, t.model, t.family, t.route
                       FROM harness_operation o
                       JOIN harness_artifact a ON a.id = o.result_artifact_id
                       JOIN harness_attempt t ON t.operation_id = o.id
                         AND t.result_artifact_id = a.id AND t.state = 'succeeded'
                       WHERE o.run_id = %s AND o.source_id = %s AND o.stage = %s
                       AND o.kind = 'model' AND o.status = 'succeeded'
                       AND a.kind = 'model_response'""",
                    (request.run.run_id, request.run.source_id, stage),
                )
            ).fetchall()
        if len(rows) != 1:
            raise HarnessValidationError("editorial stage requires one accepted model response")
        row = rows[0]
        retained = await artifacts.find_artifact(
            self.owner.ctx.settings.database_url,
            scope=self._scope(request),
            source_id=request.run.source_id,
            identity=artifacts.ArtifactIdentity(
                kind="model_response", fingerprint=str(row["fingerprint"])
            ),
        )
        if retained is None or retained.id != row["id"] or retained.sha256 != row["sha256"]:
            raise artifacts.ArtifactIntegrityError(
                "editorial response identity differs from storage"
            )
        metadata = retained.metadata
        prompt_generation = editorial_prompt_generation(
            metadata.get("promptVersion"), repair=repair
        )
        route_id = metadata.get("routeId")
        if not isinstance(route_id, str):
            raise HarnessValidationError("editorial response has no route identity")
        route = run.route_snapshot.route(route_id)
        expected_dependencies = {
            request.evidence.id,
            request.compiled.proposal_artifact.id,
            request.compiled.edit_artifact.id,
        }
        if request.assessment is not None:
            expected_dependencies.add(request.assessment.id)
        if request.rendered is not None:
            expected_dependencies.add(request.rendered.descriptor.id)
        if (
            row["family"] != family
            or route.family != family
            or row["provider"] != route.provider
            or row["model"] != route.gateway_model
            or row["route"] != route.model_dump(mode="json")
            or metadata.get("route") != route.model_dump(mode="json")
            or metadata.get("attemptId") != str(row["attempt_id"])
            or metadata.get("operationId") != str(row["operation_id"])
            or metadata.get("runId") != str(request.run.run_id)
            or metadata.get("programVersion") != "chapter-editorial-workflow/1"
            or metadata.get("schemaVersion")
            != (EDITORIAL_REPAIR_SCHEMA_VERSION if repair else EDITORIAL_VERDICT_SCHEMA_VERSION)
            or set(retained.dependency_ids) != expected_dependencies
        ):
            raise HarnessValidationError(
                "editorial model response lineage differs from its request"
            )
        raw = await self._read(request, self.owner._artifact_ref(retained))
        try:
            response = MODEL_RESPONSE_ADAPTER.validate_python(raw)
            parts = [part for part in response.parts if isinstance(part, TextPart)]
            recovered = (
                type(output).model_validate_json(parts[0].content) if len(parts) == 1 else None
            )
        except (TypeError, ValueError) as error:
            raise HarnessValidationError(
                "editorial output differs from its retained response"
            ) from error
        if recovered != output:
            raise HarnessValidationError("editorial output differs from its retained response")
        return retained.id, retained.sha256, prompt_generation

    @activity.defn(name="finalize_chapter_editorial")
    async def finalize(self, request: FinalizeEditorialRequest) -> EditorialAssessment:
        """Ground every finding and retain full evidence/response lineage."""
        context = request.context
        run, evidence, proposal = await self._load(context)
        model_id, model_sha, prompt_generation = await self._model_response(
            context,
            request.model_stage,
            output=request.verdict,
            family=request.verifier_family,
            run=run,
        )
        ground_editorial_verdict(
            evidence=evidence,
            edit=context.compiled.edit,
            verdict=request.verdict,
            require_local_timing_evidence=prompt_generation >= LOCAL_TIMING_EVIDENCE_GENERATION,
        )
        inputs: dict[str, object] = {
            "runId": str(context.run.run_id),
            "evidenceSha256": context.evidence.sha256,
            "editSha256": context.compiled.edit_artifact.sha256,
            "modelResponseArtifactId": str(model_id),
            "modelResponseSha256": model_sha,
            "verifierFamily": request.verifier_family,
        }
        dependencies = [context.evidence.id, context.compiled.edit_artifact.id, model_id]
        if context.rendered is not None:
            inputs["descriptorSha256"] = context.rendered.descriptor.sha256
            dependencies.append(context.rendered.descriptor.id)
        fingerprint = artifacts.fingerprint_for(
            kind="checks", inputs=inputs, config={"format": "chapter-editorial-assessment/2"}
        )
        content = {
            **inputs,
            "format": "chapter-editorial-assessment/2",
            "verdict": request.verdict.model_dump(mode="json"),
        }
        accepted = await artifacts.publish_json(
            self.owner.ctx.settings.database_url,
            scope=self._scope(context),
            source_id=context.run.source_id,
            store=self.owner.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="checks", fingerprint=fingerprint),
            content=content,
            metadata={
                "format": "chapter-editorial-assessment/2",
                "runId": str(context.run.run_id),
                "editSha256": context.compiled.edit_artifact.sha256,
                "verifierFamily": request.verifier_family,
                "iteration": context.iteration,
            },
            dependency_ids=tuple(dependencies),
        )
        return EditorialAssessment(
            artifact=self.owner._artifact_ref(accepted),
            verdict=request.verdict,
            candidate_sha256=editorial_compiled_fingerprint(proposal, context.compiled.edit),
        )

    @activity.defn(name="compile_chapter_editorial_repair")
    async def compile_repair(self, request: CompileEditorialRepairRequest) -> EditorialRepairResult:
        """Refuse unrelated, no-op and cyclic changes before accepting a correction."""
        context = request.context
        run, evidence, proposal = await self._load(context)
        verdict = await self._finding(context)
        _, _, prompt_generation = await self._model_response(
            context,
            request.model_stage,
            output=request.repair,
            family=request.generator_family,
            run=run,
        )
        compact = CompactChapterProposal.model_validate_json(
            request.repair.model_dump_json(exclude={"boundaryChoices"})
        )
        try:
            validated = validate_editorial_repair(
                evidence=evidence,
                original_proposal=proposal,
                original_edit=context.compiled.edit,
                verdict=verdict,
                replacement_proposal=canonical_chapter_proposal(compact),
                boundary_choices=request.repair.boundaryChoices,
                seen_candidate_hashes=request.seen_candidate_hashes,
                allow_source_edge_drops=True,
                require_supplied_timing_options=prompt_generation
                >= LOCAL_TIMING_EVIDENCE_GENERATION,
            )
            constraints = (
                {
                    (choice.leftLastSentenceId, choice.rightFirstSentenceId): choice.candidateId
                    for choice in validated.boundaryChoices
                }
                if request.preserve_existing_boundaries
                else preserved_editorial_constraints(
                    original_proposal=proposal,
                    original_edit=context.compiled.edit,
                    replacement_proposal=validated.proposal,
                    boundary_choices=validated.boundaryChoices,
                )
            )
            compiled = compile_chapters(
                evidence,
                validated.proposal,
                evidence_artifact_id=context.evidence.id,
                evidence_sha256=context.evidence.sha256,
                boundary_constraints=constraints,
                preserved_proposal=proposal if request.preserve_existing_boundaries else None,
                preserved_edit=context.compiled.edit
                if request.preserve_existing_boundaries
                else None,
            )
            candidate_sha256 = validate_compiled_editorial_repair(
                original_proposal=proposal,
                original_edit=context.compiled.edit,
                replacement_proposal=validated.proposal,
                replacement_edit=compiled,
                verdict=verdict,
                seen_candidate_hashes=request.seen_candidate_hashes,
            )
        except HarnessValidationError as error:
            return EditorialRepairResult(result=CompileProposalResult(refusal=str(error)[:2000]))
        if context.assessment is None:
            raise HarnessValidationError("repair has no grounded assessment")
        result = await self.owner.compile_chapter_proposal(
            CompileProposalRequest(
                run=context.run,
                evidence=context.evidence,
                proposal=validated.proposal,
                generator_family=request.generator_family,
                model_stage=request.model_stage,
                boundary_constraints=tuple(
                    (left, right, candidate) for (left, right), candidate in constraints.items()
                ),
                editorial_dependencies=(context.assessment, context.compiled.edit_artifact),
                prior_proposal_artifact=context.compiled.proposal_artifact
                if request.preserve_existing_boundaries
                else None,
                prior_edit_artifact=context.compiled.edit_artifact
                if request.preserve_existing_boundaries
                else None,
            )
        )
        return EditorialRepairResult(result=result, candidate_sha256=candidate_sha256)

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register the additive heavy-queue activity set."""
        return (self.prepare, self.finalize, self.compile_repair)
