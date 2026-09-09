"""Finite Temporal entrypoints for chapter run and review commands."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast

from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError
from temporalio.workflow import ParentClosePolicy

if TYPE_CHECKING:
    from uuid import UUID

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from temnia_pipeline.contracts import (
        ChapterReviewAction,
        ChapterReviewInput,
        ChapterReviewOutput,
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        HarnessRunStatus,
    )
    from temnia_pipeline.harness.models import (
        COMPACT_PROPOSAL_SCHEMA_VERSION,
        HARNESS_AGENTS,
        HarnessModelDeps,
        KnownProviderRejection,
        canonical_chapter_proposal,
        chapter_propose_v1,
        chapter_propose_v2,
        chapter_summarize_v1,
        chapter_verify_v1,
        compact_synthetic_proposal,
    )
    from temnia_pipeline.harness.prompts import (
        COMPACT_PROPOSE_PROMPT_VERSION,
        PROPOSE_PROMPT_VERSION,
        SUMMARIZE_PROMPT_VERSION,
        VERIFY_PROMPT_VERSION,
        render_compact_proposal_prompt,
        render_proposal_repair_prompt,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.routes import (
        ContextWindowExceeded,
        NoEligibleRoute,
        estimate_cost,
        select_route,
    )
    from temnia_pipeline.harness.runtime_types import (
        AcceptInitialRevisionRequest,
        BuildEvidenceRequest,
        ClaimRepairRequest,
        CommitReviewMutationRequest,
        CommitReviewMutationResult,
        CompiledRevision,
        CompileProposalRequest,
        CompileProposalResult,
        EvidenceResult,
        ExportRevisionRequest,
        ExportRevisionResult,
        FinalizeVerificationRequest,
        GlobalProposalPlan,
        MarkRunFailedRequest,
        PreparedReviewMutation,
        PrepareGlobalProposalRequest,
        PreparePlanningRequest,
        PrepareVerificationRequest,
        ProposalDiagnostic,
        ProposalDiagnosticRequest,
        ProposalPlan,
        RenderRevisionRequest,
        RenderRevisionResult,
        ResumeRunAssets,
        ReuseRevisionRenderOutcome,
        ReuseRevisionRenderRequest,
        RunRef,
        RunSnapshot,
        StageUpdate,
        StartRunRequest,
        StartRunResult,
        ValidatedSummary,
        ValidateSummaryRequest,
        VerificationPlan,
        WorkflowIdentity,
    )

ACTIVITY_RETRY = RetryPolicy(maximum_attempts=3)


def _is_known_provider_rejection(error: ActivityError) -> bool:
    cause = error.cause
    return isinstance(cause, ApplicationError) and cause.type == "KnownProviderRejection"


def _known_failure_details(
    error: Exception,
) -> tuple[Literal["failed", "budget_paused"], str]:
    cause = error.cause if isinstance(error, ActivityError) else error
    error_type = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    if error_type == "BudgetExceeded":
        return "budget_paused", "The run budget cannot cover the next qualified operation."
    if error_type == "DispatchLimitExceeded":
        return "failed", "The run reached its configured physical dispatch limit."
    return "failed", "The chapter workflow stopped after a known activity failure."


@workflow.defn
class ChapterRunWorkflow(PydanticAIWorkflow):
    """Create/refetch the immutable run before executing its finite stage program."""

    __pydantic_ai_agents__ = HARNESS_AGENTS

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Run the finite program and persist any known terminal activity failure."""
        try:
            return await self._run_program(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._record_known_failure(request, error)
            raise

    async def _run_program(  # noqa: C901, PLR0911, PLR0912, PLR0915
        self, request: ChapterRunInput
    ) -> ChapterRunOutput:
        """Build evidence, make one guarded proposal, and accept revision one."""
        info = workflow.info()
        control_queue = control_task_queue(info.task_queue)
        started = await workflow.execute_activity(
            "start_chapter_run",
            StartRunRequest(
                request=request,
                workflow=WorkflowIdentity(
                    workflow_id=info.workflow_id,
                    workflow_run_id=info.run_id,
                ),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=StartRunResult,
            task_queue=control_queue,
        )
        run = started.run
        ref = RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        )
        if not started.created and run.current_revision > 0:
            return await self._resume_existing(request, run, ref, control_queue)
        evidence = await workflow.execute_activity(
            "build_chapter_evidence",
            BuildEvidenceRequest(run=ref),
            start_to_close_timeout=timedelta(hours=6),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=EvidenceResult,
        )
        if evidence.lexical_state == "empty":
            run = await workflow.execute_activity(
                "update_chapter_run_stage",
                StageUpdate(
                    scope_organization_id=request.scope.organizationId,
                    scope_user_id=request.scope.userId,
                    source_id=request.sourceId,
                    run_id=request.runId,
                    expected_stage="planning",
                    expected_revision=0,
                    next_stage="needs_review",
                    status=HarnessRunStatus.needs_review,
                    error_message="Transcript evidence contains no lexical material.",
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=RunSnapshot,
                task_queue=control_queue,
            )
            await self._cleanup_source_cache(ref)
            return ChapterRunOutput(
                editArtifact=None,
                errorMessage=run.error_message,
                evidenceArtifact=evidence.artifact,
                revision=None,
                runId=run.id,
                status=run.status,
            )
        plan = await workflow.execute_activity(
            "prepare_chapter_proposal",
            PreparePlanningRequest(run=ref, evidence=evidence.artifact),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ACTIVITY_RETRY,
            result_type=ProposalPlan,
        )
        proposal_prompt = plan.windows[0].prompt
        proposal_route = plan.route
        proposal_fixture = plan.synthetic_payload
        generation_families = {proposal_route.family}
        hierarchy_level = 0
        proposal_grounding_artifacts: tuple[HarnessArtifactRef, ...] = ()
        if len(plan.windows) > 1:
            validate_summaries = workflow.patched("chapter-summary-validation-v1")
            generation_families.add(plan.summary_route.family)
            summaries: list[dict[str, object]] = []
            grounding_artifacts: list[HarnessArtifactRef] = []
            for window in plan.windows:
                model_stage = f"summary:{window.id}"
                try:
                    summary_result = await chapter_summarize_v1.run(
                        window.prompt,
                        deps=HarnessModelDeps(
                            scope=request.scope,
                            source_id=request.sourceId,
                            run_id=request.runId,
                            stage=model_stage,
                            program_version="chapter-workflow/1",
                            prompt_version=SUMMARIZE_PROMPT_VERSION,
                            schema_version="hierarchical-summary/1",
                            route=plan.summary_route,
                            operation_inputs={
                                "evidenceArtifactId": str(evidence.artifact.id),
                                "evidenceSha256": evidence.artifact.sha256,
                                "firstSentenceId": window.first_sentence_id,
                                "lastSentenceId": window.last_sentence_id,
                                "windowId": window.id,
                            },
                            operation_config={
                                "hierarchyLevel": 1,
                                "maxOutputTokens": request.config.maxOutputTokens,
                            },
                            input_artifact_ids=(evidence.artifact.id,),
                            dispatch_limit=request.config.maxDispatches,
                            synthetic_payload=plan.synthetic_summary_payload,
                        ),
                        model_settings={"max_tokens": request.config.maxOutputTokens},
                    )
                except UnexpectedModelBehavior:
                    if not validate_summaries:
                        raise
                    return await self._planning_refusal(
                        request,
                        evidence.artifact,
                        control_queue,
                        "A summary response did not match the required structure.",
                    )
                summary = summary_result.output.model_dump(mode="json")
                if validate_summaries:
                    validated = await workflow.execute_activity(
                        "validate_chapter_summary",
                        ValidateSummaryRequest(
                            run=ref,
                            evidence=evidence.artifact,
                            window=window,
                            summary=summary,
                            model_stage=model_stage,
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=ACTIVITY_RETRY,
                        result_type=ValidatedSummary,
                    )
                    if validated.refusal is not None:
                        return await self._planning_refusal(
                            request,
                            evidence.artifact,
                            control_queue,
                            validated.refusal,
                        )
                    if validated.summary is None or validated.artifact is None:
                        message = "summary validation returned no accepted or refused outcome"
                        raise RuntimeError(message)
                    summary = validated.summary
                    grounding_artifacts.append(validated.artifact)
                summaries.append(summary)
            global_plan = await workflow.execute_activity(
                "prepare_global_chapter_proposal",
                PrepareGlobalProposalRequest(
                    run=ref,
                    evidence=evidence.artifact,
                    windows=plan.windows,
                    summaries=tuple(summaries),
                    grounding_artifacts=(tuple(grounding_artifacts) if validate_summaries else ()),
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=GlobalProposalPlan,
            )
            while global_plan.prompt is None:
                if global_plan.refusal is not None:
                    run = await workflow.execute_activity(
                        "update_chapter_run_stage",
                        StageUpdate(
                            scope_organization_id=request.scope.organizationId,
                            scope_user_id=request.scope.userId,
                            source_id=request.sourceId,
                            run_id=request.runId,
                            expected_stage="planning",
                            expected_revision=0,
                            next_stage="needs_review",
                            status=HarnessRunStatus.needs_review,
                            error_message=global_plan.refusal,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=ACTIVITY_RETRY,
                        result_type=RunSnapshot,
                        task_queue=control_queue,
                    )
                    await self._cleanup_source_cache(ref)
                    return ChapterRunOutput(
                        editArtifact=None,
                        errorMessage=run.error_message,
                        evidenceArtifact=evidence.artifact,
                        revision=None,
                        runId=run.id,
                        status=run.status,
                    )
                if not global_plan.reduction_windows:
                    message = "hierarchy returned neither a prompt nor reduction windows"
                    raise RuntimeError(message)
                summaries = []
                grounding_artifacts = []
                for window in global_plan.reduction_windows:
                    model_stage = f"summary:level:{global_plan.hierarchy_level}:{window.id}"
                    operation_inputs: dict[str, object] = {
                        "evidenceArtifactId": str(evidence.artifact.id),
                        "evidenceSha256": evidence.artifact.sha256,
                        "firstSentenceId": window.first_sentence_id,
                        "lastSentenceId": window.last_sentence_id,
                        "windowId": window.id,
                    }
                    input_artifact_ids = (evidence.artifact.id,)
                    if validate_summaries:
                        operation_inputs["groundingArtifacts"] = [
                            {"id": str(item.id), "sha256": item.sha256}
                            for item in global_plan.input_artifacts
                        ]
                        input_artifact_ids = (
                            evidence.artifact.id,
                            *(item.id for item in global_plan.input_artifacts),
                        )
                    try:
                        summary_result = await chapter_summarize_v1.run(
                            window.prompt,
                            deps=HarnessModelDeps(
                                scope=request.scope,
                                source_id=request.sourceId,
                                run_id=request.runId,
                                stage=model_stage,
                                program_version="chapter-workflow/1",
                                prompt_version=SUMMARIZE_PROMPT_VERSION,
                                schema_version="hierarchical-summary/1",
                                route=plan.summary_route,
                                operation_inputs=operation_inputs,
                                operation_config={
                                    "hierarchyLevel": global_plan.hierarchy_level,
                                    "maxOutputTokens": request.config.maxOutputTokens,
                                },
                                input_artifact_ids=input_artifact_ids,
                                dispatch_limit=request.config.maxDispatches,
                                synthetic_payload=plan.synthetic_summary_payload,
                            ),
                            model_settings={"max_tokens": request.config.maxOutputTokens},
                        )
                    except UnexpectedModelBehavior:
                        if not validate_summaries:
                            raise
                        return await self._planning_refusal(
                            request,
                            evidence.artifact,
                            control_queue,
                            "A summary response did not match the required structure.",
                        )
                    summary = summary_result.output.model_dump(mode="json")
                    if validate_summaries:
                        validated = await workflow.execute_activity(
                            "validate_chapter_summary",
                            ValidateSummaryRequest(
                                run=ref,
                                evidence=evidence.artifact,
                                window=window,
                                summary=summary,
                                model_stage=model_stage,
                                hierarchy_level=global_plan.hierarchy_level,
                                input_artifacts=global_plan.input_artifacts,
                            ),
                            start_to_close_timeout=timedelta(minutes=2),
                            retry_policy=ACTIVITY_RETRY,
                            result_type=ValidatedSummary,
                        )
                        if validated.refusal is not None:
                            return await self._planning_refusal(
                                request,
                                evidence.artifact,
                                control_queue,
                                validated.refusal,
                            )
                        if validated.summary is None or validated.artifact is None:
                            message = "summary validation returned no accepted or refused outcome"
                            raise RuntimeError(message)
                        summary = validated.summary
                        grounding_artifacts.append(validated.artifact)
                    summaries.append(summary)
                global_plan = await workflow.execute_activity(
                    "prepare_global_chapter_proposal",
                    PrepareGlobalProposalRequest(
                        run=ref,
                        evidence=evidence.artifact,
                        windows=global_plan.reduction_windows,
                        summaries=tuple(summaries),
                        hierarchy_level=global_plan.hierarchy_level,
                        grounding_artifacts=(
                            tuple(grounding_artifacts) if validate_summaries else ()
                        ),
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=GlobalProposalPlan,
                )
            hierarchy_level = global_plan.hierarchy_level
            proposal_prompt = global_plan.prompt
            proposal_route = global_plan.route
            proposal_fixture = global_plan.synthetic_payload
            if validate_summaries:
                proposal_grounding_artifacts = cast(
                    "GlobalProposalPlan", global_plan
                ).input_artifacts
        compact_proposal = bool(workflow.patched("chapter-compact-proposal-v1"))
        if compact_proposal:
            try:
                proposal_prompt = render_compact_proposal_prompt(proposal_prompt)
                estimate_cost(
                    proposal_route,
                    payload_bytes=len(proposal_prompt.encode()),
                    max_output_tokens=request.config.maxOutputTokens,
                )
            except ContextWindowExceeded:
                return await self._planning_refusal(
                    request,
                    evidence.artifact,
                    control_queue,
                    "The compact proposal request exceeds the bounded model context.",
                )
            proposal_fixture = compact_synthetic_proposal(proposal_fixture)
        model_stage = "proposal:v2:global" if compact_proposal else "proposal:global"
        proposal_prompt_version = (
            COMPACT_PROPOSE_PROMPT_VERSION if compact_proposal else PROPOSE_PROMPT_VERSION
        )
        proposal_schema_version = (
            COMPACT_PROPOSAL_SCHEMA_VERSION if compact_proposal else "chapter-proposal/1"
        )
        proposal_program_version = (
            "chapter-workflow/2" if compact_proposal else "chapter-workflow/1"
        )
        proposal_base_prompt = proposal_prompt
        proposal_repair_artifacts: tuple[HarnessArtifactRef, ...] = ()
        compiled: CompiledRevision | None = None
        semantic_error = "The proposal did not satisfy the exact-cover contract."
        failover_index: int = 0
        while compiled is None:
            semantic_failure = True
            diagnostic: ProposalDiagnostic | None = None
            proposal_operation_inputs: dict[str, object] = {
                "evidenceArtifactId": str(evidence.artifact.id),
                "evidenceSha256": evidence.artifact.sha256,
                "firstSentenceId": plan.windows[0].first_sentence_id,
                "lastSentenceId": plan.windows[-1].last_sentence_id,
                "windowCount": len(plan.windows),
            }
            proposal_input_artifacts: tuple[HarnessArtifactRef, ...] = ()
            if proposal_grounding_artifacts:
                proposal_operation_inputs["groundingArtifacts"] = [
                    {"id": str(item.id), "sha256": item.sha256}
                    for item in proposal_grounding_artifacts
                ]
                proposal_input_artifacts = proposal_grounding_artifacts
            if proposal_repair_artifacts:
                proposal_operation_inputs["repairArtifacts"] = [
                    {"id": str(item.id), "sha256": item.sha256}
                    for item in proposal_repair_artifacts
                ]
                proposal_input_artifacts = proposal_input_artifacts + proposal_repair_artifacts
            proposal_input_artifact_ids: tuple[UUID, ...] = (
                evidence.artifact.id,
                *(item.id for item in proposal_input_artifacts),
            )
            proposal_operation_config: dict[str, object] = {
                "hierarchyLevel": hierarchy_level,
                "maxOutputTokens": request.config.maxOutputTokens,
                "repairIndex": run.repair_count,
                "requestKey": str(request.requestKey),
            }
            try:
                proposal_agent = chapter_propose_v2 if compact_proposal else chapter_propose_v1
                proposal_result = await proposal_agent.run(
                    proposal_prompt,
                    deps=HarnessModelDeps(
                        scope=request.scope,
                        source_id=request.sourceId,
                        run_id=request.runId,
                        stage=model_stage,
                        program_version=proposal_program_version,
                        prompt_version=proposal_prompt_version,
                        schema_version=proposal_schema_version,
                        route=proposal_route,
                        operation_inputs=proposal_operation_inputs,
                        operation_config=proposal_operation_config,
                        input_artifact_ids=proposal_input_artifact_ids,
                        dispatch_limit=request.config.maxDispatches,
                        synthetic_payload=proposal_fixture,
                    ),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
            except UnexpectedModelBehavior:
                semantic_error = "The model response did not match the strict proposal schema."
                if compact_proposal:
                    diagnostic = await self._diagnose_proposal(
                        ProposalDiagnosticRequest(
                            run=ref,
                            evidence=evidence.artifact,
                            model_stage=model_stage,
                            route=proposal_route,
                            prompt_version=proposal_prompt_version,
                            schema_version=proposal_schema_version,
                            max_output_tokens=request.config.maxOutputTokens,
                            program_version=proposal_program_version,
                            operation_inputs=proposal_operation_inputs,
                            operation_config=proposal_operation_config,
                            input_artifacts=proposal_input_artifacts,
                        )
                    )
                    semantic_error = diagnostic.message
            except KnownProviderRejection:
                semantic_failure = False
                semantic_error = "The qualified provider conclusively rejected the request."
            except ActivityError as error:
                if not _is_known_provider_rejection(error):
                    raise
                semantic_failure = False
                semantic_error = "The qualified provider conclusively rejected the request."
            else:
                compile_result = await workflow.execute_activity(
                    "compile_chapter_proposal",
                    CompileProposalRequest(
                        run=ref,
                        evidence=evidence.artifact,
                        proposal=(
                            canonical_chapter_proposal(proposal_result.output)
                            if compact_proposal
                            else proposal_result.output
                        ),
                        generator_family=proposal_route.family,
                        model_stage=model_stage,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=CompileProposalResult,
                )
                if compile_result.compiled is not None:
                    compiled = compile_result.compiled
                    break
                semantic_error = compile_result.refusal or semantic_error
                if compact_proposal:
                    diagnostic = await self._diagnose_proposal(
                        ProposalDiagnosticRequest(
                            run=ref,
                            evidence=evidence.artifact,
                            model_stage=model_stage,
                            route=proposal_route,
                            prompt_version=proposal_prompt_version,
                            schema_version=proposal_schema_version,
                            max_output_tokens=request.config.maxOutputTokens,
                            program_version=proposal_program_version,
                            operation_inputs=proposal_operation_inputs,
                            operation_config=proposal_operation_config,
                            input_artifacts=proposal_input_artifacts,
                            compiler_refusal=semantic_error,
                        )
                    )
                    semantic_error = diagnostic.message
            if semantic_failure and run.repair_count >= request.config.maxRepairs:
                return await self._planning_refusal(
                    request, evidence.artifact, control_queue, semantic_error
                )
            repair_prompt = proposal_prompt
            repair_input_artifacts: tuple[HarnessArtifactRef, ...] = ()
            if semantic_failure and compact_proposal:
                if diagnostic is None:
                    message = "semantic proposal failure has no diagnostic"
                    raise RuntimeError(message)
                try:
                    repair_prompt = render_proposal_repair_prompt(
                        proposal_base_prompt,
                        feedback={
                            "code": diagnostic.code,
                            "compilerCode": diagnostic.compiler_code,
                            "issues": [
                                issue.model_dump(mode="json") for issue in diagnostic.issues
                            ],
                            "message": diagnostic.message,
                        },
                        diagnostic_artifact=(
                            diagnostic.artifact.id,
                            diagnostic.artifact.sha256,
                        ),
                        response_artifact=(diagnostic.response.id, diagnostic.response.sha256),
                    )
                except ContextWindowExceeded:
                    return await self._planning_refusal(
                        request,
                        evidence.artifact,
                        control_queue,
                        "The diagnosed proposal repair exceeds the bounded model context.",
                    )
                repair_input_artifacts = (diagnostic.artifact, diagnostic.response)
            excluded = set(generation_families)
            repair_route = None
            while repair_route is None:
                try:
                    candidate = select_route(
                        run.route_snapshot,
                        "propose",
                        excluded_families=frozenset(excluded),
                    )
                except NoEligibleRoute:
                    return await self._planning_refusal(
                        request,
                        evidence.artifact,
                        control_queue,
                        "No independent proposal family remains for the bounded repair.",
                    )
                try:
                    estimate_cost(
                        candidate,
                        payload_bytes=len(repair_prompt.encode()),
                        max_output_tokens=request.config.maxOutputTokens,
                    )
                except ContextWindowExceeded:
                    excluded.add(candidate.family)
                    continue
                repair_route = candidate
            if semantic_failure:
                info = workflow.info()
                run = await workflow.execute_activity(
                    "claim_chapter_repair",
                    ClaimRepairRequest(
                        run=ref,
                        workflow=WorkflowIdentity(
                            workflow_id=info.workflow_id,
                            workflow_run_id=info.run_id,
                        ),
                        expected_repair_count=run.repair_count,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=RunSnapshot,
                    task_queue=control_queue,
                )
            proposal_route = repair_route
            generation_families.add(repair_route.family)
            if semantic_failure:
                proposal_prompt = repair_prompt
                if compact_proposal:
                    proposal_repair_artifacts = cast(
                        "tuple[HarnessArtifactRef, ...]", repair_input_artifacts
                    )
                    model_stage = f"proposal:v2:repair:{run.repair_count}"
                else:
                    model_stage = f"proposal:repair:{run.repair_count}"
            else:
                failover_index = failover_index + 1
                model_stage = (
                    f"proposal:v2:failover:{failover_index}"
                    if compact_proposal
                    else f"proposal:failover:{failover_index}"
                )
        if compiled is None:
            message = "proposal loop ended without a compiled revision"
            raise RuntimeError(message)
        run = await workflow.execute_activity(
            "accept_initial_chapter_revision",
            AcceptInitialRevisionRequest(
                run=ref,
                request_key=request.requestKey,
                edit_artifact_id=compiled.edit_artifact.id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        rendered = await workflow.execute_activity(
            "render_chapter_revision",
            RenderRevisionRequest(
                run=ref,
                edit=compiled.edit_artifact,
                revision=run.current_revision,
            ),
            start_to_close_timeout=timedelta(hours=5),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RenderRevisionResult,
        )
        review_message: str | None = None
        if rendered.technical_passed and rendered.has_kept_sections:
            verification = await workflow.execute_activity(
                "prepare_chapter_verification",
                PrepareVerificationRequest(
                    run=ref,
                    evidence=evidence.artifact,
                    proposal=compiled.proposal_artifact,
                    edit=compiled.edit_artifact,
                    rendered=rendered,
                    generation_families=tuple(sorted(generation_families)),
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=VerificationPlan,
            )
            if verification.prompt is None or verification.route is None:
                review_message = verification.refusal or "Independent verification is unavailable."
            else:
                verdict = await chapter_verify_v1.run(
                    verification.prompt,
                    deps=HarnessModelDeps(
                        scope=request.scope,
                        source_id=request.sourceId,
                        run_id=request.runId,
                        stage=f"verify:revision:{run.current_revision}",
                        program_version="chapter-workflow/1",
                        prompt_version=VERIFY_PROMPT_VERSION,
                        schema_version="editorial-verdict/1",
                        route=verification.route,
                        operation_inputs={
                            "descriptorArtifactId": str(rendered.descriptor.id),
                            "descriptorSha256": rendered.descriptor.sha256,
                            "editArtifactId": str(compiled.edit_artifact.id),
                            "editSha256": compiled.edit_artifact.sha256,
                        },
                        operation_config={
                            "maxOutputTokens": request.config.maxOutputTokens,
                            "revision": run.current_revision,
                        },
                        input_artifact_ids=(
                            evidence.artifact.id,
                            compiled.edit_artifact.id,
                            rendered.descriptor.id,
                        ),
                        dispatch_limit=request.config.maxDispatches,
                        synthetic_payload=verification.synthetic_payload,
                    ),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
                await workflow.execute_activity(
                    "finalize_chapter_verification",
                    FinalizeVerificationRequest(
                        run=ref,
                        revision=run.current_revision,
                        edit=compiled.edit_artifact,
                        rendered=rendered,
                        verdict=verdict.output.model_dump(mode="json"),
                        verifier_family=verification.route.family,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=HarnessArtifactRef,
                )
                if verdict.output.status != "passed":
                    review_message = "; ".join(verdict.output.reasons)[:2000] or (
                        "Editorial verification requires review."
                    )
        elif rendered.technical_passed:
            review_message = "All sections are drops and require deliberate human acceptance."
        else:
            review_message = "One or more rendered chapters failed technical checks."
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="render",
                expected_revision=run.current_revision,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=review_message,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        return ChapterRunOutput(
            editArtifact=compiled.edit_artifact,
            errorMessage=run.error_message,
            evidenceArtifact=evidence.artifact,
            revision=run.current_revision or None,
            runId=run.id,
            status=run.status,
        )

    async def _record_known_failure(self, request: ChapterRunInput, error: Exception) -> None:
        status, message = _known_failure_details(error)
        info = workflow.info()
        with contextlib.suppress(Exception):
            await workflow.execute_activity(
                "mark_chapter_run_failed",
                MarkRunFailedRequest(
                    run=RunRef(
                        scope_organization_id=request.scope.organizationId,
                        scope_user_id=request.scope.userId,
                        source_id=request.sourceId,
                        run_id=request.runId,
                    ),
                    workflow=WorkflowIdentity(
                        workflow_id=info.workflow_id,
                        workflow_run_id=info.run_id,
                    ),
                    error_message=message,
                    status=status,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=bool,
                task_queue=control_task_queue(info.task_queue),
            )
        ref = RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        )
        await self._cleanup_source_cache(ref)

    @staticmethod
    async def _diagnose_proposal(request: ProposalDiagnosticRequest) -> ProposalDiagnostic:
        """Inspect one retained response without any further provider request."""
        return await workflow.execute_activity(
            "diagnose_chapter_proposal",
            request,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ACTIVITY_RETRY,
            result_type=ProposalDiagnostic,
        )

    @staticmethod
    async def _cleanup_source_cache(ref: RunRef) -> None:
        """Best-effort terminal cleanup; a live activity lease safely returns false."""
        with contextlib.suppress(Exception):
            await workflow.execute_activity(
                "cleanup_chapter_source_cache",
                ref,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=bool,
            )

    async def _planning_refusal(
        self,
        request: ChapterRunInput,
        evidence: HarnessArtifactRef,
        control_queue: str,
        message: str,
    ) -> ChapterRunOutput:
        """Persist one finite planning halt without accepting an invented revision."""
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="planning",
                expected_revision=0,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=message[:2000],
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        await self._cleanup_source_cache(
            RunRef(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
            )
        )
        return ChapterRunOutput(
            editArtifact=None,
            errorMessage=run.error_message,
            evidenceArtifact=evidence,
            revision=None,
            runId=run.id,
            status=run.status,
        )

    async def _resume_existing(  # noqa: PLR0912
        self,
        request: ChapterRunInput,
        run: RunSnapshot,
        ref: RunRef,
        control_queue: str,
    ) -> ChapterRunOutput:
        """Continue one known-state current revision without rebuilding older revisions."""
        assets = await workflow.execute_activity(
            "get_chapter_resume_assets",
            ref,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=ResumeRunAssets,
            task_queue=control_queue,
        )
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage=run.stage,
                expected_revision=assets.revision,
                next_stage="render",
                status=HarnessRunStatus.running,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        reuse = ReuseRevisionRenderOutcome()
        if assets.base_revision is not None:
            reuse = await workflow.execute_activity(
                "reuse_chapter_revision_render",
                ReuseRevisionRenderRequest(
                    run=ref,
                    predecessor_revision=assets.base_revision,
                    edit=assets.edit,
                    revision=assets.revision,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
                result_type=ReuseRevisionRenderOutcome,
            )
        if reuse.reused is not None:
            rendered = reuse.reused
        else:
            rendered = await workflow.execute_activity(
                "render_chapter_revision",
                RenderRevisionRequest(run=ref, edit=assets.edit, revision=assets.revision),
                start_to_close_timeout=timedelta(hours=5),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=RenderRevisionResult,
            )
        review_message: str | None = None
        export_ready = False
        if reuse.reused is not None and reuse.all_sections_accepted and rendered.technical_passed:
            exported = await workflow.execute_activity(
                "export_chapter_revision",
                ExportRevisionRequest(
                    run=ref,
                    revision=assets.revision,
                    edit=assets.edit,
                    descriptor=rendered.descriptor,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
                result_type=ExportRevisionResult,
            )
            export_ready = exported.ready
        elif reuse.reused is not None:
            review_message = "Human review remains incomplete for one or more sections."
        elif rendered.technical_passed and not rendered.has_kept_sections:
            review_message = "All sections are drops and require deliberate human acceptance."
        elif rendered.technical_passed:
            verification = await workflow.execute_activity(
                "prepare_chapter_verification",
                PrepareVerificationRequest(
                    run=ref,
                    evidence=assets.evidence,
                    proposal=assets.edit,
                    edit=assets.edit,
                    rendered=rendered,
                    generation_families=(),
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=VerificationPlan,
            )
            if verification.prompt is None or verification.route is None:
                review_message = verification.refusal or "Independent verification is unavailable."
            else:
                verdict = await chapter_verify_v1.run(
                    verification.prompt,
                    deps=HarnessModelDeps(
                        scope=request.scope,
                        source_id=request.sourceId,
                        run_id=request.runId,
                        stage=f"verify:revision:{assets.revision}",
                        program_version="chapter-workflow/1",
                        prompt_version=VERIFY_PROMPT_VERSION,
                        schema_version="editorial-verdict/1",
                        route=verification.route,
                        operation_inputs={
                            "descriptorArtifactId": str(rendered.descriptor.id),
                            "descriptorSha256": rendered.descriptor.sha256,
                            "editArtifactId": str(assets.edit.id),
                            "editSha256": assets.edit.sha256,
                        },
                        operation_config={
                            "maxOutputTokens": request.config.maxOutputTokens,
                            "revision": assets.revision,
                        },
                        input_artifact_ids=(
                            assets.evidence.id,
                            assets.edit.id,
                            rendered.descriptor.id,
                        ),
                        dispatch_limit=request.config.maxDispatches,
                        synthetic_payload=verification.synthetic_payload,
                    ),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
                await workflow.execute_activity(
                    "finalize_chapter_verification",
                    FinalizeVerificationRequest(
                        run=ref,
                        revision=assets.revision,
                        edit=assets.edit,
                        rendered=rendered,
                        verdict=verdict.output.model_dump(mode="json"),
                        verifier_family=verification.route.family,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=HarnessArtifactRef,
                )
                if verdict.output.status != "passed":
                    review_message = "; ".join(verdict.output.reasons)[:2000] or (
                        "Editorial verification requires review."
                    )
                else:
                    exported = await workflow.execute_activity(
                        "export_chapter_revision",
                        ExportRevisionRequest(
                            run=ref,
                            revision=assets.revision,
                            edit=assets.edit,
                            descriptor=rendered.descriptor,
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=ACTIVITY_RETRY,
                        result_type=ExportRevisionResult,
                    )
                    export_ready = exported.ready
        else:
            review_message = "One or more rendered chapters failed technical checks."
        if export_ready:
            run = await workflow.execute_activity(
                "get_chapter_run",
                ref,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=RunSnapshot,
                task_queue=control_queue,
            )
            return ChapterRunOutput(
                editArtifact=assets.edit,
                errorMessage=run.error_message,
                evidenceArtifact=assets.evidence,
                revision=assets.revision,
                runId=run.id,
                status=run.status,
            )
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="render",
                expected_revision=assets.revision,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=review_message,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=control_queue,
        )
        return ChapterRunOutput(
            editArtifact=assets.edit,
            errorMessage=run.error_message,
            evidenceArtifact=assets.evidence,
            revision=assets.revision,
            runId=run.id,
            status=run.status,
        )


@workflow.defn
class ChapterReviewWorkflow:
    """Apply one mutation-keyed review command in a finite activity."""

    @workflow.run
    async def run(self, request: ChapterReviewInput) -> ChapterReviewOutput:
        """Run one review command and persist known failures owned by this execution."""
        try:
            return await self._run_program(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._record_known_failure(request, error)
            raise

    async def _run_program(  # noqa: C901, PLR0912, PLR0915
        self, request: ChapterReviewInput
    ) -> ChapterReviewOutput:
        """Return the exact persisted review result for this mutation UUID."""
        queue = control_task_queue(workflow.info().task_queue)
        ref = RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        )
        if request.action in {
            ChapterReviewAction.cancel,
            ChapterReviewAction.raise_budget,
            ChapterReviewAction.retry,
        }:
            before = await workflow.execute_activity(
                "get_chapter_run",
                ref,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=RunSnapshot,
                task_queue=queue,
            )
            result = await workflow.execute_activity(
                "apply_chapter_review",
                request,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=ChapterReviewOutput,
                task_queue=queue,
            )
            if request.action == ChapterReviewAction.cancel and result.state == "applied":
                after = await workflow.execute_activity(
                    "get_chapter_run",
                    ref,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=RunSnapshot,
                    task_queue=queue,
                )
                owner_changed = (
                    after.workflow_id != before.workflow_id
                    or after.workflow_run_id != before.workflow_run_id
                )
                if (
                    before.status
                    not in {
                        HarnessRunStatus.pending,
                        HarnessRunStatus.running,
                    }
                    and not owner_changed
                ):
                    await self._cleanup_source_cache(ref)
                    return result
                handle = workflow.get_external_workflow_handle(
                    after.workflow_id,
                    run_id=after.workflow_run_id,
                )
                await handle.cancel()
                await self._cleanup_source_cache(ref)
            should_resume = result.state == "applied" and (
                request.action == ChapterReviewAction.retry
                or (
                    request.action == ChapterReviewAction.raise_budget
                    and before.status == HarnessRunStatus.budget_paused
                )
            )
            if should_resume:
                resume = ChapterRunInput(
                    brief=before.brief,
                    budgetMicros=before.initial_budget_micros,
                    config=before.config,
                    requestKey=before.request_key,
                    runId=before.id,
                    scope=request.scope,
                    sourceId=before.source_id,
                )
                with contextlib.suppress(WorkflowAlreadyStartedError):
                    await workflow.start_child_workflow(
                        "ChapterRunWorkflow",
                        resume,
                        id=f"chapter-resume-{before.id}-{request.mutationKey}",
                        task_queue=workflow.info().task_queue,
                        parent_close_policy=ParentClosePolicy.ABANDON,
                    )
            return result
        prepared = await workflow.execute_activity(
            "prepare_chapter_review",
            request,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ACTIVITY_RETRY,
            result_type=PreparedReviewMutation,
        )
        committed = await workflow.execute_activity(
            "commit_chapter_review",
            CommitReviewMutationRequest(
                prepared=prepared,
                workflow=WorkflowIdentity(
                    workflow_id=workflow.info().workflow_id,
                    workflow_run_id=workflow.info().run_id,
                ),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=CommitReviewMutationResult,
            task_queue=queue,
        )
        if committed.render is None:
            return committed.output
        if prepared.evidence is None:
            message = "applied chapter review has no evidence artifact"
            raise RuntimeError(message)
        reuse = ReuseRevisionRenderOutcome()
        if request.action in {ChapterReviewAction.accept, ChapterReviewAction.reject}:
            reuse = await workflow.execute_activity(
                "reuse_chapter_revision_render",
                ReuseRevisionRenderRequest(
                    run=ref,
                    predecessor_revision=request.baseRevision,
                    edit=committed.render.edit,
                    revision=committed.render.revision,
                    required=True,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
                result_type=ReuseRevisionRenderOutcome,
            )
        if reuse.reused is not None:
            rendered = reuse.reused
        else:
            rendered = await workflow.execute_activity(
                "render_chapter_revision",
                committed.render,
                start_to_close_timeout=timedelta(hours=5),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=RenderRevisionResult,
            )
        review_message: str | None = None
        export_ready = False
        if reuse.reused is not None and reuse.all_sections_accepted and rendered.technical_passed:
            exported = await workflow.execute_activity(
                "export_chapter_revision",
                ExportRevisionRequest(
                    run=ref,
                    revision=committed.render.revision,
                    edit=committed.render.edit,
                    descriptor=rendered.descriptor,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
                result_type=ExportRevisionResult,
            )
            export_ready = exported.ready
        elif reuse.reused is not None:
            review_message = "Human review remains incomplete for one or more sections."
        elif rendered.technical_passed and not rendered.has_kept_sections:
            review_message = "All sections are drops and require deliberate human acceptance."
        elif rendered.technical_passed:
            verification = await workflow.execute_activity(
                "prepare_chapter_verification",
                PrepareVerificationRequest(
                    run=ref,
                    evidence=prepared.evidence,
                    proposal=committed.render.edit,
                    edit=committed.render.edit,
                    rendered=rendered,
                    generation_families=(),
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=VerificationPlan,
            )
            if verification.prompt is None or verification.route is None:
                review_message = verification.refusal or "Independent verification is unavailable."
            else:
                verdict = await chapter_verify_v1.run(
                    verification.prompt,
                    deps=HarnessModelDeps(
                        scope=request.scope,
                        source_id=request.sourceId,
                        run_id=request.runId,
                        stage=f"verify:revision:{committed.render.revision}",
                        program_version="chapter-workflow/1",
                        prompt_version=VERIFY_PROMPT_VERSION,
                        schema_version="editorial-verdict/1",
                        route=verification.route,
                        operation_inputs={
                            "descriptorArtifactId": str(rendered.descriptor.id),
                            "descriptorSha256": rendered.descriptor.sha256,
                            "editArtifactId": str(committed.render.edit.id),
                            "editSha256": committed.render.edit.sha256,
                        },
                        operation_config={
                            "maxOutputTokens": verification.output_cap,
                            "revision": committed.render.revision,
                        },
                        input_artifact_ids=(
                            committed.render.edit.id,
                            rendered.descriptor.id,
                        ),
                        dispatch_limit=verification.dispatch_limit,
                        synthetic_payload=verification.synthetic_payload,
                    ),
                    model_settings={"max_tokens": verification.output_cap},
                )
                await workflow.execute_activity(
                    "finalize_chapter_verification",
                    FinalizeVerificationRequest(
                        run=ref,
                        revision=committed.render.revision,
                        edit=committed.render.edit,
                        rendered=rendered,
                        verdict=verdict.output.model_dump(mode="json"),
                        verifier_family=verification.route.family,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=ACTIVITY_RETRY,
                    result_type=HarnessArtifactRef,
                )
                if verdict.output.status != "passed":
                    review_message = "; ".join(verdict.output.reasons)[:2000] or (
                        "Editorial verification requires review."
                    )
                else:
                    exported = await workflow.execute_activity(
                        "export_chapter_revision",
                        ExportRevisionRequest(
                            run=ref,
                            revision=committed.render.revision,
                            edit=committed.render.edit,
                            descriptor=rendered.descriptor,
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=ACTIVITY_RETRY,
                        result_type=ExportRevisionResult,
                    )
                    export_ready = exported.ready
        else:
            review_message = "One or more rendered chapters failed technical checks."
        if export_ready:
            return committed.output
        await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="render",
                expected_revision=committed.render.revision,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=review_message,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY,
            result_type=RunSnapshot,
            task_queue=queue,
        )
        return committed.output

    async def _record_known_failure(self, request: ChapterReviewInput, error: Exception) -> None:
        status, message = _known_failure_details(error)
        info = workflow.info()
        with contextlib.suppress(Exception):
            await workflow.execute_activity(
                "mark_chapter_run_failed",
                MarkRunFailedRequest(
                    run=RunRef(
                        scope_organization_id=request.scope.organizationId,
                        scope_user_id=request.scope.userId,
                        source_id=request.sourceId,
                        run_id=request.runId,
                    ),
                    workflow=WorkflowIdentity(
                        workflow_id=info.workflow_id,
                        workflow_run_id=info.run_id,
                    ),
                    error_message=message,
                    status=status,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACTIVITY_RETRY,
                result_type=bool,
                task_queue=control_task_queue(info.task_queue),
            )
        await self._cleanup_source_cache(
            RunRef(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
            )
        )

    @staticmethod
    async def _cleanup_source_cache(ref: RunRef) -> None:
        """Best-effort terminal cleanup; a live activity lease safely returns false."""
        with contextlib.suppress(Exception):
            await workflow.execute_activity(
                "cleanup_chapter_source_cache",
                ref,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
                result_type=bool,
            )
