"""Durable standalone-topic program: plan, cold/source review, grounded repair, render."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from datetime import timedelta
from typing import cast

from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        HarnessRunStatus,
        TopicColdReview,
        TopicSourceReview,
    )
    from temnia_pipeline.harness.editorial_policy import TOPIC_POLICY
    from temnia_pipeline.harness.models import (
        TOPIC_AGENTS,
        HarnessModelDeps,
        topic_cold_review_v1,
        topic_propose_v1,
        topic_source_review_v1,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.run_failures import known_failure_details
    from temnia_pipeline.harness.runtime_types import (
        AcceptInitialRevisionRequest,
        BuildEvidenceRequest,
        ClaimRepairRequest,
        EvidenceResult,
        MarkRunFailedRequest,
        RenderRevisionRequest,
        ResumeRunAssets,
        RunRef,
        RunSnapshot,
        StageUpdate,
        StartRunRequest,
        StartRunResult,
        WorkflowIdentity,
    )
    from temnia_pipeline.harness.topic_editorial import (
        COLD_PROMPT,
        SOURCE_PROMPT,
        TOPIC_PROGRAM,
        TOPIC_PROMPT,
        assessment_has_grounded_failure,
        cold_review_key,
        topic_semantic_key,
    )
    from temnia_pipeline.harness.topic_runtime import (
        SaveTopicAssessment,
        SaveTopicProposal,
        TopicAssessmentResult,
        TopicCallPlan,
        TopicCompilation,
        TopicContext,
        TopicProposalResult,
        TopicRenderResult,
    )

RETRY = RetryPolicy(maximum_attempts=3)


def topic_model_deps(
    request: ChapterRunInput, plan: TopicCallPlan, *, stage: str, prompt_version: str, author: bool
) -> HarnessModelDeps:
    """Bind the exact prompt, artifact inputs and reserved family to accounting."""
    route = plan.author if author else plan.verifier
    return HarnessModelDeps(
        scope=request.scope,
        source_id=request.sourceId,
        run_id=request.runId,
        stage=stage,
        program_version=TOPIC_PROGRAM,
        prompt_version=prompt_version,
        schema_version=prompt_version,
        route=route,
        operation_inputs={
            "artifacts": [
                {"id": str(ref.id), "sha256": ref.sha256} for ref in plan.input_artifacts
            ],
            "promptSha256": hashlib.sha256(plan.prompt.encode()).hexdigest(),
        },
        operation_config={
            "maxOutputTokens": request.config.maxOutputTokens,
            "reservedVerifierFamily": plan.verifier.family,
        },
        input_artifact_ids=tuple(ref.id for ref in plan.input_artifacts),
        dispatch_limit=request.config.maxDispatches,
        synthetic_payload=plan.synthetic_payload,
    )


@workflow.defn
class TopicRunWorkflow(PydanticAIWorkflow):
    """A new entrypoint keeps historical chapter partitions and replay semantics intact."""

    __pydantic_ai_agents__ = TOPIC_AGENTS

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Execute the finite topic program while preserving unknown-outcome fences."""
        try:
            return await self.program(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status, message = known_failure_details(error)
            info = workflow.info()
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "mark_chapter_run_failed",
                    MarkRunFailedRequest(
                        run=self.ref(request),
                        workflow=WorkflowIdentity(
                            workflow_id=info.workflow_id, workflow_run_id=info.run_id
                        ),
                        error_message=message,
                        status=status,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    task_queue=control_task_queue(info.task_queue),
                    result_type=bool,
                )
            raise
        finally:
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "cleanup_chapter_source_cache",
                    self.ref(request),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    result_type=bool,
                )

    @staticmethod
    def ref(request: ChapterRunInput) -> RunRef:
        """Build the scoped durable run reference."""
        return RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        )

    async def prepare(self, context: TopicContext) -> TopicCallPlan:
        """Prepare a source-bound editorial call in an activity."""
        return await workflow.execute_activity(
            "prepare_topic_plan",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=TopicCallPlan,
        )

    async def finish(
        self,
        request: ChapterRunInput,
        *,
        evidence: HarnessArtifactRef,
        edit: HarnessArtifactRef | None,
        revision: int,
        message: str | None,
    ) -> ChapterRunOutput:
        """End with reviewable output without inventing human acceptance."""
        run = await workflow.execute_activity(
            "update_chapter_run_stage",
            StageUpdate(
                scope_organization_id=request.scope.organizationId,
                scope_user_id=request.scope.userId,
                source_id=request.sourceId,
                run_id=request.runId,
                expected_stage="render" if revision else "planning",
                expected_revision=revision,
                next_stage="needs_review",
                status=HarnessRunStatus.needs_review,
                error_message=message,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control_task_queue(workflow.info().task_queue),
            result_type=RunSnapshot,
        )
        return ChapterRunOutput(
            runId=request.runId,
            revision=revision or None,
            status=run.status,
            evidenceArtifact=evidence,
            editArtifact=edit,
            errorMessage=message,
        )

    async def render(
        self,
        request: ChapterRunInput,
        *,
        evidence: HarnessArtifactRef,
        edit: HarnessArtifactRef,
        revision: int,
        reasons: tuple[str, ...] = (),
    ) -> ChapterRunOutput:
        """Render current independent videos and retain their technical outcome."""
        rendered = await workflow.execute_activity(
            "render_topic_revision",
            RenderRevisionRequest(run=self.ref(request), edit=edit, revision=revision),
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=TopicRenderResult,
        )
        message = "; ".join(reasons) or None
        if not rendered.count:
            message = message or (
                "No independently usable video was compiled. "
                "Review the retained proposal and findings."
            )
        elif not rendered.technical_passed:
            message = "; ".join(
                filter(None, (message, "Some rendered videos need technical review."))
            )
        return await self.finish(
            request,
            evidence=evidence,
            edit=edit,
            revision=revision,
            message=message[:2000] if message else None,
        )

    async def program(  # noqa: C901, PLR0912, PLR0915
        self, request: ChapterRunInput
    ) -> ChapterRunOutput:
        """Plan globally, review independently, repair grounded failures and compile."""
        info = workflow.info()
        control = control_task_queue(info.task_queue)
        started = await workflow.execute_activity(
            "start_chapter_run",
            StartRunRequest(
                request=request,
                editorial_policy=TOPIC_POLICY,
                workflow=WorkflowIdentity(
                    workflow_id=info.workflow_id, workflow_run_id=info.run_id
                ),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control,
            result_type=StartRunResult,
        )
        run = started.run
        ref = self.ref(request)
        if run.current_revision:
            assets = await workflow.execute_activity(
                "get_chapter_resume_assets",
                ref,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
                task_queue=control,
                result_type=ResumeRunAssets,
            )
            return await self.render(
                request, evidence=assets.evidence, edit=assets.edit, revision=assets.revision
            )
        evidence = await workflow.execute_activity(
            "build_chapter_evidence",
            BuildEvidenceRequest(run=ref),
            start_to_close_timeout=timedelta(hours=6),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=EvidenceResult,
        )
        if evidence.lexical_state == "empty":
            return await self.finish(
                request,
                evidence=evidence.artifact,
                edit=None,
                revision=0,
                message="The accepted transcript has no words to ground a standalone topic.",
            )
        navigation = None
        context = TopicContext(run=ref, evidence=evidence.artifact, navigation=navigation)
        seen: set[str] = set()
        rejected: set[str] = set()
        cold_cache: dict[str, tuple[TopicColdReview, str]] = {}
        final_assessment: TopicAssessmentResult | None = None
        for iteration in range(request.config.maxRepairs + 1):
            previous_context = context
            plan = await self.prepare(context)
            stage = f"proposal:topic:{iteration}"
            proposed = await topic_propose_v1.run(
                plan.prompt,
                deps=topic_model_deps(
                    request, plan, stage=stage, prompt_version=TOPIC_PROMPT, author=True
                ),
                model_settings={"max_tokens": request.config.maxOutputTokens},
            )
            saved = await workflow.execute_activity(
                "save_topic_proposal",
                SaveTopicProposal(
                    context=context,
                    proposal=proposed.output,
                    model_stage=stage,
                    author_family=plan.author.family,
                    verifier_family=plan.verifier.family,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=TopicProposalResult,
            )
            if saved.validation is not None:
                rejection_key = topic_semantic_key(proposed.output)
                repeated = rejection_key in rejected
                rejected.add(rejection_key)
                if repeated or iteration == request.config.maxRepairs:
                    stop = (
                        "Repeated invalid source selection."
                        if repeated
                        else "Repair allowance exhausted."
                    )
                    return await self.finish(
                        request,
                        evidence=evidence.artifact,
                        edit=None,
                        revision=0,
                        message=(
                            "Initial topic proposal remains source-invalid: "
                            f"{saved.validation_error}. "
                            f"{stop} No critic was dispatched. "
                            f"Retained diagnostic: {saved.validation.id}."
                        )[:2000],
                    )
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
                    retry_policy=RETRY,
                    task_queue=control,
                    result_type=RunSnapshot,
                )
                context = TopicContext(
                    run=ref,
                    evidence=evidence.artifact,
                    navigation=navigation,
                    proposal_validation=saved.validation,
                    iteration=iteration + 1,
                )
                continue
            proposal_ref = saved.artifact
            if proposal_ref is None:
                message = "topic proposal activity returned no accepted result"
                raise RuntimeError(message)
            context = TopicContext(
                run=ref,
                evidence=evidence.artifact,
                proposal=proposal_ref,
                iteration=iteration,
                navigation=navigation,
            )
            semantic_key = topic_semantic_key(proposed.output)
            if semantic_key in seen:
                # Retain the assessed identity; the repeated response stays in the ledger.
                context = previous_context
                break
            seen.add(semantic_key)
            cold_reviews: list[TopicColdReview] = []
            cold_stages: list[str] = []
            refusal = None
            source_review: TopicSourceReview | None = None
            source_stage = None
            for candidate in proposed.output.candidates:
                key = cold_review_key(candidate)
                cached = cold_cache.get(key)
                if cached is None:
                    cold_context = context.model_copy(update={"candidate_id": candidate.id})
                    cold_plan = await self.prepare(cold_context)
                    cold_stage = f"verify:topic:cold:{key}"
                    try:
                        judged = await topic_cold_review_v1.run(
                            cold_plan.prompt,
                            deps=topic_model_deps(
                                request,
                                cold_plan,
                                stage=cold_stage,
                                prompt_version=COLD_PROMPT,
                                author=False,
                            ),
                            model_settings={"max_tokens": request.config.maxOutputTokens},
                        )
                    except UnexpectedModelBehavior:
                        refusal = (
                            "A cold reviewer response did not match the required judgment schema."
                        )
                        continue
                    cached = (judged.output, cold_stage)
                    cold_cache[key] = cached
                cold_reviews.append(cached[0])
                cold_stages.append(cached[1])
            if proposed.output.candidates:
                source_plan = await self.prepare(context)
                source_stage = f"verify:topic:source:{iteration}"
                try:
                    judged_source = await topic_source_review_v1.run(
                        source_plan.prompt,
                        deps=topic_model_deps(
                            request,
                            source_plan,
                            stage=source_stage,
                            prompt_version=SOURCE_PROMPT,
                            author=False,
                        ),
                        model_settings={"max_tokens": request.config.maxOutputTokens},
                    )
                    source_review = judged_source.output
                except UnexpectedModelBehavior:
                    refusal = (
                        "The source-context reviewer response did not match "
                        "the required judgment schema."
                    )
            else:
                refusal = "The source produced no proposed standalone videos."
            final_assessment = await workflow.execute_activity(
                "save_topic_assessment",
                SaveTopicAssessment(
                    context=context,
                    cold_reviews=tuple(cold_reviews),
                    cold_stages=tuple(cold_stages),
                    source_review=source_review,
                    source_stage=source_stage,
                    refusal=refusal,
                    author_family=plan.author.family,
                    verifier_family=plan.verifier.family,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=TopicAssessmentResult,
            )
            final_assessment = cast("TopicAssessmentResult", final_assessment)
            context = context.model_copy(update={"assessment": final_assessment.artifact})
            if (
                final_assessment.all_passed
                or iteration == request.config.maxRepairs
                or not assessment_has_grounded_failure(final_assessment.assessment)
            ):
                break
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
                retry_policy=RETRY,
                task_queue=control,
                result_type=RunSnapshot,
            )
        if final_assessment is None or context.assessment is None:
            return await self.finish(
                request,
                evidence=evidence.artifact,
                edit=None,
                revision=0,
                message="Topic planning stopped without a complete retained assessment.",
            )
        compiled = await workflow.execute_activity(
            "compile_topic_portfolio",
            context,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
            result_type=TopicCompilation,
        )
        await workflow.execute_activity(
            "accept_initial_chapter_revision",
            AcceptInitialRevisionRequest(
                run=ref, request_key=request.requestKey, edit_artifact_id=compiled.artifact.id
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control,
            result_type=RunSnapshot,
        )
        reasons = compiled.refusals
        if not final_assessment.all_passed:
            reasons = (*reasons, "Some topic candidates still have unresolved editorial findings.")
        return await self.render(
            request, evidence=evidence.artifact, edit=compiled.artifact, revision=1, reasons=reasons
        )
