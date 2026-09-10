"""Finite pre-render editorial program, with every paid call durably metered."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic_ai.exceptions import UnexpectedModelBehavior
from temporalio import workflow
from temporalio.common import RetryPolicy

from temnia_pipeline.harness.editorial import (
    EDITORIAL_REPAIR_SCHEMA_VERSION,
    EDITORIAL_VERDICT_SCHEMA_VERSION,
)
from temnia_pipeline.harness.editorial_runtime import (
    CompileEditorialRepairRequest,
    EditorialAssessment,
    EditorialContextRequest,
    EditorialRepairResult,
    FinalizeEditorialRequest,
)
from temnia_pipeline.harness.editorial_versions import editorial_dispatch_version
from temnia_pipeline.harness.models import (
    HarnessModelDeps,
    chapter_editorial_assess_v1,
    chapter_editorial_repair_v1,
)
from temnia_pipeline.harness.runtime_types import (
    ClaimRepairRequest,
    RunSnapshot,
    VerificationPlan,
    WorkflowIdentity,
)

if TYPE_CHECKING:
    from temnia_pipeline.contracts import ChapterRunInput, HarnessArtifactRef
    from temnia_pipeline.harness.runtime_types import CompiledRevision, RunRef

EDITORIAL_PROGRAM_VERSION = "chapter-editorial-workflow/1"
_RETRY = RetryPolicy(maximum_attempts=3)


def editorial_model_deps(
    request: ChapterRunInput,
    context: EditorialContextRequest,
    plan: VerificationPlan,
    *,
    stage: str,
    repair: bool = False,
) -> HarnessModelDeps:
    """Bind metering/idempotency to the actual edit and supporting findings."""
    if plan.route is None:
        raise ValueError("editorial call requires a qualified route")  # noqa: EM101, TRY003
    inputs: dict[str, object] = {
        "evidenceArtifactId": str(context.evidence.id),
        "evidenceSha256": context.evidence.sha256,
        "proposalArtifactId": str(context.compiled.proposal_artifact.id),
        "proposalSha256": context.compiled.proposal_artifact.sha256,
        "editArtifactId": str(context.compiled.edit_artifact.id),
        "editSha256": context.compiled.edit_artifact.sha256,
    }
    dependencies = [
        context.evidence.id,
        context.compiled.proposal_artifact.id,
        context.compiled.edit_artifact.id,
    ]
    if context.assessment is not None:
        inputs["assessmentArtifactId"] = str(context.assessment.id)
        inputs["assessmentSha256"] = context.assessment.sha256
        dependencies.append(context.assessment.id)
    if context.rendered is not None:
        inputs["descriptorArtifactId"] = str(context.rendered.descriptor.id)
        inputs["descriptorSha256"] = context.rendered.descriptor.sha256
        dependencies.append(context.rendered.descriptor.id)
    return HarnessModelDeps(
        scope=request.scope,
        source_id=request.sourceId,
        run_id=request.runId,
        stage=stage,
        program_version=EDITORIAL_PROGRAM_VERSION,
        prompt_version=editorial_dispatch_version(plan.editorial_prompt_version, repair=repair),
        schema_version=EDITORIAL_REPAIR_SCHEMA_VERSION
        if repair
        else EDITORIAL_VERDICT_SCHEMA_VERSION,
        route=plan.route,
        operation_inputs=inputs,
        operation_config={
            "iteration": context.iteration,
            "maxOutputTokens": request.config.maxOutputTokens,
            "requestKey": str(request.requestKey),
        },
        input_artifact_ids=tuple(dependencies),
        dispatch_limit=request.config.maxDispatches,
        synthetic_payload=plan.synthetic_payload,
    )


async def assess_and_repair(  # noqa: PLR0913, PLR0911
    request: ChapterRunInput,
    *,
    run: RunSnapshot,
    ref: RunRef,
    evidence: HarnessArtifactRef,
    compiled: CompiledRevision,
    generation_families: set[str],
    control_queue: str,
) -> tuple[RunSnapshot, CompiledRevision, str | None]:
    """Assess, repair in the allowed scope, recompile, then assess the result."""
    seen: list[str] = []
    iteration = 0
    while True:
        context = EditorialContextRequest(
            run=ref,
            evidence=evidence,
            compiled=compiled,
            generation_families=tuple(sorted(generation_families)),
            iteration=iteration,
        )
        plan = await workflow.execute_activity(
            "prepare_chapter_editorial",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
            result_type=VerificationPlan,
        )
        if plan.prompt is None or plan.route is None:
            return run, compiled, plan.refusal or "Editorial assessment is unavailable."
        stage = f"verify:editorial:{iteration}"
        try:
            verdict = await chapter_editorial_assess_v1.run(
                plan.prompt,
                deps=editorial_model_deps(request, context, plan, stage=stage),
                model_settings={"max_tokens": request.config.maxOutputTokens},
            )
        except UnexpectedModelBehavior:
            return run, compiled, "Editorial assessment did not satisfy its strict contract."
        assessment = await workflow.execute_activity(
            "finalize_chapter_editorial",
            FinalizeEditorialRequest(
                context=context,
                verdict=verdict.output,
                model_stage=stage,
                verifier_family=plan.route.family,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
            result_type=EditorialAssessment,
        )
        if assessment.verdict.status == "passed":
            return run, compiled, None
        message = "; ".join(finding.reason for finding in assessment.verdict.findings)[:2000]
        if (
            any(finding.disposition != "repairable" for finding in assessment.verdict.findings)
            or run.repair_count >= request.config.maxRepairs
        ):
            return run, compiled, message
        seen.append(assessment.candidate_sha256)
        repair_context = context.model_copy(update={"assessment": assessment.artifact})
        repair_plan = await workflow.execute_activity(
            "prepare_chapter_editorial",
            repair_context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
            result_type=VerificationPlan,
        )
        if repair_plan.prompt is None or repair_plan.route is None:
            return run, compiled, repair_plan.refusal or message
        info = workflow.info()
        run = await workflow.execute_activity(
            "claim_chapter_repair",
            ClaimRepairRequest(
                run=ref,
                workflow=WorkflowIdentity(
                    workflow_id=info.workflow_id, workflow_run_id=info.run_id
                ),
                expected_repair_count=run.repair_count,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        repair_stage = f"editorial:repair:{run.repair_count}"
        try:
            repair = await chapter_editorial_repair_v1.run(
                repair_plan.prompt,
                deps=editorial_model_deps(
                    request, repair_context, repair_plan, stage=repair_stage, repair=True
                ),
                model_settings={"max_tokens": request.config.maxOutputTokens},
            )
        except UnexpectedModelBehavior:
            return run, compiled, "Editorial repair did not satisfy its strict contract."
        result = await workflow.execute_activity(
            "compile_chapter_editorial_repair",
            CompileEditorialRepairRequest(
                context=repair_context,
                repair=repair.output,
                model_stage=repair_stage,
                generator_family=repair_plan.route.family,
                seen_candidate_hashes=tuple(seen),
                preserve_existing_boundaries=workflow.patched(
                    "chapter-editorial-retained-boundaries-v1"
                ),
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
            result_type=EditorialRepairResult,
        )
        if result.result.compiled is None:
            return run, compiled, result.result.refusal or message
        compiled = result.result.compiled
        if result.candidate_sha256 is not None:
            seen.append(result.candidate_sha256)
        generation_families.add(repair_plan.route.family)
        iteration += 1
