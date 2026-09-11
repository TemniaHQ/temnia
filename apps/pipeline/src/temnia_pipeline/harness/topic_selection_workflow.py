"""Finite opportunity discovery, independent portfolio judgment and scoped correction."""

# Orchestration refusals name their exact unavailable state at the boundary.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        TopicSelectionColdReview,
        TopicSelectionDraft,
    )
    from temnia_pipeline.harness.chapter_llama_activity import CandidateRequest, CandidateResult
    from temnia_pipeline.harness.models import (
        TOPIC_SELECTION_AGENTS,
        HarnessModelDeps,
        topic_selection_author_v2,
        topic_selection_cold_v2,
        topic_selection_patch_v2,
        topic_selection_source_v2,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.runtime_types import (
        AcceptInitialRevisionRequest,
        BuildEvidenceRequest,
        ClaimRepairRequest,
        EvidenceResult,
        ResumeRunAssets,
        RunSnapshot,
        StartRunRequest,
        StartRunResult,
        WorkflowIdentity,
    )
    from temnia_pipeline.harness.topic_runtime import TopicCompilation
    from temnia_pipeline.harness.topic_selection import SELECTION_POLICY, selection_cold_key
    from temnia_pipeline.harness.topic_selection_runtime import (
        SelectionAssessmentResult,
        SelectionCallPlan,
        SelectionContext,
        SelectionReviewRequest,
        SelectionSaveRequest,
        SelectionSaveResult,
        SelectionStopRequest,
        selection_call_config,
        selection_call_inputs,
    )
    from temnia_pipeline.harness.topic_workflow import RETRY, TopicRunWorkflow


def selection_model_deps(request: ChapterRunInput, plan: SelectionCallPlan) -> HarnessModelDeps:
    """Share exact call identity with receipt validation and the existing budgeted model."""
    return HarnessModelDeps(
        scope=request.scope,
        source_id=request.sourceId,
        run_id=request.runId,
        stage=plan.stage,
        program_version=SELECTION_POLICY,
        prompt_version=plan.prompt_version,
        schema_version=plan.schema_version,
        route=plan.verifier if plan.stage.startswith("verify:") else plan.author,
        operation_inputs=selection_call_inputs(plan),
        operation_config=selection_call_config(plan, request.config.maxOutputTokens),
        input_artifact_ids=tuple(ref.id for ref in plan.input_artifacts),
        dispatch_limit=request.config.maxDispatches,
        synthetic_payload=plan.synthetic_payload,
    )


def execution_limit(error: Exception) -> bool:
    """Only known admission limits authorize retaining partial work; uncertainty propagates."""
    cause = error.cause if isinstance(error, ActivityError) else error
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name in {"BudgetExceeded", "DispatchLimitExceeded", "ContextWindowExceeded"}


@workflow.defn
class TopicSelectionWorkflow(TopicRunWorkflow):
    """New history and model activity names leave the original program replayable."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_AGENTS

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Reuse owned failure/cancellation/cache cleanup without changing v1 commands."""
        return await super().run(request)

    async def prepare_selection(self, context: SelectionContext) -> SelectionCallPlan:
        """Prepare one immutable editorial request without making a model call."""
        return await workflow.execute_activity(
            "prepare_topic_selection_call",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=SelectionCallPlan,
        )

    async def claim_repair(self, run: RunSnapshot, request: ChapterRunInput) -> RunSnapshot:
        """Use the existing run-owned repair allowance for admission or semantic changes."""
        info = workflow.info()
        return await workflow.execute_activity(
            "claim_chapter_repair",
            ClaimRepairRequest(
                run=self.ref(request),
                expected_repair_count=run.repair_count,
                workflow=WorkflowIdentity(
                    workflow_id=info.workflow_id, workflow_run_id=info.run_id
                ),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control_task_queue(info.task_queue),
            result_type=RunSnapshot,
        )

    async def save_selection(self, request: SelectionSaveRequest) -> SelectionSaveResult:
        """Publish an accepted draft or an exact refused-response diagnostic."""
        return await workflow.execute_activity(
            "save_topic_selection",
            request,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=SelectionSaveResult,
        )

    async def review_selection(  # noqa: C901
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        draft: TopicSelectionDraft,
        cache: dict[str, tuple[TopicSelectionColdReview, str]],
    ) -> SelectionAssessmentResult:
        """Review local value and the original source, including an empty author selection."""
        cold_reviews: list[TopicSelectionColdReview] = []
        cold_candidate_ids: list[str] = []
        cold_stages: list[str] = []
        unavailable: list[str] = []
        reasons: list[str] = []
        limited = False
        if context.rubric is None:
            raise RuntimeError("selection review requires the frozen rubric")
        for candidate in draft.proposal.candidates:
            key = selection_cold_key(candidate, context.rubric.sha256)
            cached = cache.get(key)
            if cached is None:
                cold_context = context.model_copy(update={"candidate_id": candidate.id})
                try:
                    plan = await self.prepare_selection(cold_context)
                    result = await topic_selection_cold_v2.run(
                        plan.prompt,
                        deps=selection_model_deps(request, plan),
                        model_settings={"max_tokens": request.config.maxOutputTokens},
                    )
                except UnexpectedModelBehavior:
                    unavailable.append(candidate.id)
                    reasons.append(
                        f"Cold review of {candidate.id} did not match its required schema."
                    )
                    continue
                except Exception as error:
                    if not execution_limit(error):
                        raise
                    limited = True
                    reasons.append("Execution capacity prevented the remaining candidate reviews.")
                    break
                cached = (result.output, plan.stage)
                cache[key] = cached
            cold_reviews.append(cached[0])
            cold_candidate_ids.append(candidate.id)
            cold_stages.append(cached[1])
        source_review = None
        source_dispatched = False
        if not limited:
            try:
                source_plan = await self.prepare_selection(context)
                result = await topic_selection_source_v2.run(
                    source_plan.prompt,
                    deps=selection_model_deps(request, source_plan),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
                source_dispatched = True
                source_review = result.output
            except UnexpectedModelBehavior:
                source_dispatched = True
                reasons.append("Source and opportunity review did not match its required schema.")
            except Exception as error:
                if not execution_limit(error):
                    raise
                limited = True
                reasons.append("Execution capacity prevented the source and opportunity review.")
        return await workflow.execute_activity(
            "save_topic_selection_assessment",
            SelectionReviewRequest(
                context=context,
                cold_reviews=tuple(cold_reviews),
                cold_candidate_ids=tuple(cold_candidate_ids),
                cold_stages=tuple(cold_stages),
                unavailable_cold_ids=tuple(unavailable),
                source_review=source_review,
                source_dispatched=source_dispatched,
                reasons=tuple(reasons),
                execution_limited=limited,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=SelectionAssessmentResult,
        )

    async def program(self, request: ChapterRunInput) -> ChapterRunOutput:  # noqa: C901, PLR0912, PLR0915
        """Complete a traceable selection cycle before compiling reviewable videos."""
        info = workflow.info()
        control = control_task_queue(info.task_queue)
        started = await workflow.execute_activity(
            "start_chapter_run",
            StartRunRequest(
                request=request,
                editorial_policy=SELECTION_POLICY,
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
        if run.editorial_policy != SELECTION_POLICY:
            raise RuntimeError("selection workflow cannot reinterpret another program generation")
        if run.current_revision:
            assets = await workflow.execute_activity(
                "get_chapter_resume_assets",
                self.ref(request),
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
            BuildEvidenceRequest(run=self.ref(request)),
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
                message="The accepted transcript has no words to ground topic discovery.",
            )
        context = SelectionContext(run=self.ref(request), evidence=evidence.artifact)
        rubric = await workflow.execute_activity(
            "prepare_topic_selection_rubric",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=HarnessArtifactRef,
        )
        context = context.model_copy(update={"rubric": rubric})
        if run.chapter_llama_config is not None:
            navigation = await workflow.execute_activity(
                "generate_chapter_llama_candidate",
                CandidateRequest(
                    run=self.ref(request),
                    evidence=evidence.artifact,
                    configuration=run.chapter_llama_config,
                ),
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
                result_type=CandidateResult,
            )
            context = context.model_copy(update={"navigation": navigation.artifact})
        accepted: SelectionSaveResult | None = None
        while accepted is None:
            plan = await self.prepare_selection(context)
            try:
                result = await topic_selection_author_v2.run(
                    plan.prompt,
                    deps=selection_model_deps(request, plan),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
                save = SelectionSaveRequest(context=context, draft=result.output)
            except UnexpectedModelBehavior:
                save = SelectionSaveRequest(
                    context=context,
                    schema_error="Author response did not match the selection schema.",
                )
            saved = await self.save_selection(save)
            if saved.rejection is None:
                accepted = saved
                break
            if run.repair_count >= request.config.maxRepairs:
                return await self.finish(
                    request,
                    evidence=evidence.artifact,
                    edit=None,
                    revision=0,
                    message="Initial selection remains invalid after admission corrections; "
                    "discovery is incomplete. " + "; ".join(saved.diagnostics)[:1200],
                )
            run = await self.claim_repair(run, request)
            context = context.model_copy(
                update={"rejection": saved.rejection, "iteration": context.iteration + 1}
            )
        if accepted.selection is None or accepted.draft is None:
            raise RuntimeError("selection admission returned no accepted draft")
        draft = accepted.draft
        context = context.model_copy(update={"selection": accepted.selection, "rejection": None})
        seen = {accepted.semantic_key}
        cache: dict[str, tuple[TopicSelectionColdReview, str]] = {}
        final: SelectionAssessmentResult
        stop_reasons: list[str] = []
        limited = False
        while True:
            final = await self.review_selection(request, context, draft, cache)
            context = context.model_copy(update={"assessment": final.artifact})
            if str(final.assessment.executionStatus) == "complete" or not final.actionable:
                break
            if run.repair_count >= request.config.maxRepairs:
                limited = True
                stop_reasons.append(
                    "The configured repair allowance ended with unresolved editorial findings."
                )
                break
            run = await self.claim_repair(run, request)
            patch_context = context.model_copy(update={"iteration": context.iteration + 1})
            try:
                plan = await self.prepare_selection(patch_context)
                result = await topic_selection_patch_v2.run(
                    plan.prompt,
                    deps=selection_model_deps(request, plan),
                    model_settings={"max_tokens": request.config.maxOutputTokens},
                )
                save = SelectionSaveRequest(context=patch_context, patch=result.output)
            except UnexpectedModelBehavior:
                save = SelectionSaveRequest(
                    context=patch_context,
                    schema_error="Repair response did not match the patch schema.",
                )
            except Exception as error:
                if not execution_limit(error):
                    raise
                limited = True
                stop_reasons.append(
                    "Execution capacity ended before repair; "
                    "the prior assessed selection is retained."
                )
                break
            saved = await self.save_selection(save)
            if saved.rejection is not None:
                stop_reasons.append(
                    "An invalid repair was retained without changing the prior assessed selection."
                )
                break
            if saved.semantic_key in seen:
                stop_reasons.append(
                    "Repair repeated the same editorial selection without improvement."
                )
                break
            if saved.selection is None or saved.draft is None:
                raise RuntimeError("selection repair returned no valid replacement")
            seen.add(saved.semantic_key)
            draft = saved.draft
            context = patch_context.model_copy(
                update={"selection": saved.selection, "assessment": None}
            )
        if stop_reasons:
            final = await workflow.execute_activity(
                "stop_topic_selection",
                SelectionStopRequest(
                    context=context, reasons=tuple(stop_reasons), execution_limited=limited
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=SelectionAssessmentResult,
            )
            context = context.model_copy(update={"assessment": final.artifact})
        compiled = await workflow.execute_activity(
            "compile_topic_selection",
            context,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
            result_type=TopicCompilation,
        )
        await workflow.execute_activity(
            "accept_initial_chapter_revision",
            AcceptInitialRevisionRequest(
                run=self.ref(request),
                request_key=request.requestKey,
                edit_artifact_id=compiled.artifact.id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control,
            result_type=RunSnapshot,
        )
        if str(final.assessment.executionStatus) != "complete":
            stop_reasons.append(
                "Selection or opportunity assessment is incomplete; review the retained findings."
            )
        elif not compiled.edit.videos:
            stop_reasons.append(
                "Source review selected no standalone video; opportunity dispositions are retained."
            )
        return await self.render(
            request,
            evidence=evidence.artifact,
            edit=compiled.artifact,
            revision=1,
            reasons=(*compiled.refusals, *stop_reasons),
        )
