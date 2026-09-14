"""Finite opportunity discovery, independent portfolio judgment and scoped correction."""

# Orchestration refusals name their exact unavailable state at the boundary.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Literal

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        TopicSelectionColdReview,
        TopicSelectionDraft,
    )
    from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3, EditorialPolicy
    from temnia_pipeline.harness.models import (
        TOPIC_SELECTION_AGENTS,
        HarnessModelDeps,
        topic_opportunity_inventory_v3,
        topic_selection_author_v3,
        topic_selection_cold_v3,
        topic_selection_patch_v3,
        topic_selection_source_v4,
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
    from temnia_pipeline.harness.source_index import source_inspection_trace
    from temnia_pipeline.harness.topic_runtime import TopicCompilation
    from temnia_pipeline.harness.topic_selection import selection_cold_key
    from temnia_pipeline.harness.topic_selection_runtime import (
        OpportunityInventorySaveRequest,
        SelectionAssessmentResult,
        SelectionCallPlan,
        SelectionContext,
        SelectionReviewRequest,
        SelectionSaveRequest,
        SelectionSaveResult,
        SelectionStopRequest,
        effective_topic_output_tokens,
        selection_call_config,
        selection_call_inputs,
    )
    from temnia_pipeline.harness.topic_workflow import RETRY, TopicRunWorkflow


Seat = Literal["author", "verifier"]


class SeatRoutesExhausted(RuntimeError):  # noqa: N818 - the name crosses Temporal as a type
    """Every qualified route for one seat failed transiently, each after backoff."""


def no_eligible_route(error: Exception) -> bool:
    """The seat pool has no route at the requested fallback position."""
    cause = error.cause if isinstance(error, ActivityError) else error
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "NoEligibleRoute"


def failure_sentence(error: Exception) -> str:
    """The exact sentence the raising site wrote, across the Temporal boundary."""
    cause = error.cause if isinstance(error, ActivityError) else error
    message = cause.message if isinstance(cause, ApplicationError) else str(cause)
    return message.strip()


def selection_model_deps(request: ChapterRunInput, plan: SelectionCallPlan) -> HarnessModelDeps:
    """Share exact call identity with receipt validation and the existing budgeted model."""
    return HarnessModelDeps(
        scope=request.scope,
        source_id=request.sourceId,
        run_id=request.runId,
        stage=plan.stage,
        program_version=plan.program_version,
        prompt_version=plan.prompt_version,
        schema_version=plan.schema_version,
        route=plan.verifier if plan.stage.startswith("verify:") else plan.author,
        operation_inputs=selection_call_inputs(plan),
        operation_config=selection_call_config(plan, request.config.maxOutputTokens),
        input_artifact_ids=tuple(ref.id for ref in plan.input_artifacts),
        source_index=plan.source_index,
        source_tool_role=plan.source_tool_role,
        dispatch_limit=request.config.maxDispatches,
        synthetic_payload=plan.synthetic_payload,
    )


def execution_limit(error: Exception) -> bool:
    """Only known admission limits authorize retaining partial work; uncertainty propagates."""
    cause = error.cause if isinstance(error, ActivityError) else error
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name in {"BudgetExceeded", "DispatchLimitExceeded", "ContextWindowExceeded"}


def invalid_model_output(error: Exception) -> bool:
    """Recognize a retained paid response that failed typed normalization across Temporal."""
    cause = error.cause if isinstance(error, ActivityError) else error
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "UnexpectedModelBehavior"


def transient_provider_failure(error: Exception) -> bool:
    """A lost stream whose charge settled: a fresh paid attempt is allowed, unknowns are not."""
    cause = error.cause if isinstance(error, ActivityError) else error
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "TransientProviderFailure"


# A transient failure (throttling, an outage, a lost stream whose charge settled) is
# retried once on the same route after a pause, then the seat moves to the next qualified
# route in its pool. Pools hold at most a few routes; the cap keeps the loop finite even
# when a test double never reports an empty pool.
SAME_ROUTE_ATTEMPTS = 2
SAME_ROUTE_BACKOFF = timedelta(seconds=20)
MAX_SEAT_ROUTES = 4
# Cold reviews are independent per candidate; this bounds the workflow's fan-out, and the
# worker's per-route gate bounds what actually reaches the provider.
COLD_REVIEW_FAN_OUT = 3
PAUSE_ADVICE = re.compile(r"pause of (\d+) s")


def advised_pause(sentence: str, floor: timedelta) -> timedelta:
    """Honour the provider's Retry-After when it is longer than the fixed backoff."""
    match = PAUSE_ADVICE.search(sentence)
    if match is None:
        return floor
    return max(floor, timedelta(seconds=int(match.group(1))))


@workflow.defn(name="TopicSelectionWorkflow")
class TopicSelectionWorkflow(TopicRunWorkflow):
    """New history and model activity names leave the original program replayable."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_AGENTS
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V3
    settled_context: SelectionContext
    author_agent = topic_selection_author_v3
    cold_agent = topic_selection_cold_v3
    source_agent = topic_selection_source_v4
    patch_agent = topic_selection_patch_v3

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

    async def prepare_author_context(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
    ) -> SelectionContext:
        """Run one independent source inventory, then degrade visibly if it is unavailable."""
        diagnostics: tuple[str, ...] = ()
        inventory = None
        result = None
        plan = None
        try:
            result, plan, settled = await self.run_seat(
                topic_opportunity_inventory_v3, request, context, "verifier"
            )
            context = self.carry_routes(context, settled)
            inventory = result.output
        except Exception as error:
            if invalid_model_output(error):
                diagnostics = (
                    (
                        "Independent source opportunity inventory did not match its required "
                        "schema; authoring continued with that missing observation explicit."
                    ),
                )
            elif execution_limit(error):
                diagnostics = (
                    (
                        "Execution capacity prevented independent source opportunity inventory; "
                        "authoring continued with that missing observation explicit."
                    ),
                )
            else:
                raise
        saved = await workflow.execute_activity(
            "save_topic_opportunity_inventory_v3",
            OpportunityInventorySaveRequest(
                context=context,
                inventory=inventory,
                schema_error=diagnostics[0] if diagnostics else None,
                inspection=(
                    source_inspection_trace(
                        result.all_messages(),
                        index_sha256=context.source_index.sha256,
                        role="inventory",
                        stage=plan.stage,
                    )
                    if result is not None
                    and plan is not None
                    and inventory is not None
                    and context.source_index is not None
                    else None
                ),
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=SelectionSaveResult,
        )
        if saved.selection is None:
            diagnostics = (*diagnostics, *saved.diagnostics)
        return context.model_copy(
            update={
                "inventory": saved.selection,
                "inventory_attempted": True,
                "inventory_diagnostics": tuple(dict.fromkeys(diagnostics)),
            }
        )

    async def run_seat(
        self,
        agent: Any,  # noqa: ANN401
        request: ChapterRunInput,
        context: SelectionContext,
        seat: Seat,
    ) -> tuple[Any, SelectionCallPlan, SelectionContext]:
        """Make one seat's call on the current route; retry transients, then fall back.

        Returns the result, the plan it was made with, and the context carrying the
        fallback position that succeeded, which callers keep for the rest of the run.
        """
        index_field = "author_index" if seat == "author" else "verifier_index"
        tried: list[str] = []
        last_failure = ""
        stage = seat
        for _ in range(MAX_SEAT_ROUTES):
            try:
                plan = await self.prepare_selection(context)
            except Exception as error:
                if no_eligible_route(error) and tried:
                    break
                raise
            stage = plan.stage
            route = plan.author if seat == "author" else plan.verifier
            deps = selection_model_deps(request, plan)
            settings = {
                "max_tokens": effective_topic_output_tokens(request.config.maxOutputTokens, route)
            }
            for attempt in range(SAME_ROUTE_ATTEMPTS):
                try:
                    result = await agent.run(plan.prompt, deps=deps, model_settings=settings)
                except Exception as error:
                    if not transient_provider_failure(error):
                        raise
                    last_failure = failure_sentence(error)
                    if attempt < SAME_ROUTE_ATTEMPTS - 1:
                        await workflow.sleep(advised_pause(last_failure, SAME_ROUTE_BACKOFF))
                    continue
                return result, plan, context
            tried.append(route.id)
            context = context.model_copy(update={index_field: getattr(context, index_field) + 1})
        exhausted = (
            f"Every qualified {seat} route failed transiently for the {stage} call "
            f"({', '.join(tried)}); last: {last_failure}"
        )
        raise SeatRoutesExhausted(exhausted)

    @staticmethod
    def carry_routes(base: SelectionContext, updated: SelectionContext) -> SelectionContext:
        """Keep the fallback positions a seat call settled on."""
        return base.model_copy(
            update={
                "author_index": updated.author_index,
                "verifier_index": updated.verifier_index,
            }
        )

    async def review_selection(  # noqa: C901, PLR0912, PLR0915
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
        rubric_sha = context.rubric.sha256
        pending = [
            candidate
            for candidate in draft.proposal.candidates
            if selection_cold_key(candidate, rubric_sha) not in cache
        ]
        fan_out = asyncio.Semaphore(COLD_REVIEW_FAN_OUT)
        settled_index = {"verifier_index": context.verifier_index}

        async def review_one(
            candidate: Any,  # noqa: ANN401
        ) -> tuple[TopicSelectionColdReview, str]:
            async with fan_out:
                cold_context = context.model_copy(
                    update={
                        "candidate_id": candidate.id,
                        "verifier_index": settled_index["verifier_index"],
                    }
                )
                result, plan, settled = await self.run_seat(
                    self.cold_agent, request, cold_context, "verifier"
                )
                settled_index["verifier_index"] = max(
                    settled_index["verifier_index"], settled.verifier_index
                )
                return result.output, plan.stage

        outcomes = await asyncio.gather(
            *(review_one(candidate) for candidate in pending), return_exceptions=True
        )
        context = context.model_copy(update={"verifier_index": settled_index["verifier_index"]})
        for candidate, outcome in zip(pending, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                if invalid_model_output(outcome):
                    unavailable.append(candidate.id)
                    reasons.append(
                        f"Cold review of {candidate.id} did not match its required schema."
                    )
                    continue
                if not execution_limit(outcome):
                    raise outcome
                if not limited:
                    limited = True
                    reasons.append("Execution capacity prevented the remaining candidate reviews.")
                continue
            cache[selection_cold_key(candidate, rubric_sha)] = outcome
        for candidate in draft.proposal.candidates:
            cached = cache.get(selection_cold_key(candidate, rubric_sha))
            if cached is None:
                continue
            cold_reviews.append(cached[0])
            cold_candidate_ids.append(candidate.id)
            cold_stages.append(cached[1])
        source_review = None
        source_inspection = None
        source_dispatched = False
        if not limited:
            try:
                result, plan, settled = await self.run_seat(
                    self.source_agent, request, context, "verifier"
                )
                context = self.carry_routes(context, settled)
                source_dispatched = True
                source_review = result.output
                source_inspection = (
                    source_inspection_trace(
                        result.all_messages(),
                        index_sha256=context.source_index.sha256,
                        role="source_reviewer",
                        stage=plan.stage,
                    )
                    if context.source_index is not None
                    else None
                )
            except Exception as error:
                if invalid_model_output(error):
                    source_dispatched = True
                    reasons.append(
                        "Source and opportunity review did not match its required schema."
                    )
                elif not execution_limit(error):
                    raise
                else:
                    limited = True
                    reasons.append(
                        "Execution capacity prevented the source and opportunity review."
                    )
        self.settled_context = context
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
                source_inspection=source_inspection,
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
                editorial_policy=self.policy,
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
        if run.editorial_policy != self.policy:
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
        context = SelectionContext(
            run=self.ref(request),
            evidence=evidence.artifact,
            program_version=TOPIC_SELECTION_POLICY_V3,
        )
        rubric = await workflow.execute_activity(
            "prepare_topic_selection_rubric",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=HarnessArtifactRef,
        )
        context = context.model_copy(update={"rubric": rubric})
        source_index = await workflow.execute_activity(
            "build_topic_source_index",
            context,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=HarnessArtifactRef,
        )
        context = context.model_copy(update={"source_index": source_index})
        context = await self.prepare_author_context(request, context)
        accepted: SelectionSaveResult | None = None
        while accepted is None:
            try:
                result, plan, settled = await self.run_seat(
                    self.author_agent, request, context, "author"
                )
                context = self.carry_routes(context, settled)
                save = SelectionSaveRequest(
                    context=context,
                    draft=result.output,
                    inspection=(
                        source_inspection_trace(
                            result.all_messages(),
                            index_sha256=context.source_index.sha256,
                            role="author",
                            stage=plan.stage,
                        )
                        if context.source_index is not None
                        else None
                    ),
                )
            except Exception as error:
                if not invalid_model_output(error):
                    raise
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
        pending_assessment: SelectionAssessmentResult | None = None
        stop_reasons: list[str] = []
        rejection_diagnostics: tuple[str, ...] = ()
        limited = False
        while True:
            if pending_assessment is None:
                pending_assessment = await self.review_selection(request, context, draft, cache)
                context = self.carry_routes(context, self.settled_context).model_copy(
                    update={"assessment": pending_assessment.artifact}
                )
            final = pending_assessment
            if str(final.assessment.executionStatus) == "complete" or not final.actionable:
                break
            if run.repair_count >= request.config.maxRepairs:
                limited = True
                stop_reasons.append(
                    "The configured repair allowance ended with unresolved editorial findings."
                )
                if rejection_diagnostics:
                    stop_reasons.append(
                        "The last repair was refused without changing the prior assessed "
                        "selection: " + "; ".join(rejection_diagnostics)
                    )
                break
            run = await self.claim_repair(run, request)
            patch_context = context.model_copy(update={"iteration": context.iteration + 1})
            try:
                result, _, settled = await self.run_seat(
                    self.patch_agent, request, patch_context, "author"
                )
                patch_context = self.carry_routes(patch_context, settled)
                save = SelectionSaveRequest(context=patch_context, patch=result.output)
            except Exception as error:
                if invalid_model_output(error):
                    stop_reasons.append(
                        "Repair response was incomplete or invalid; the prior assessed selection "
                        "is retained for review."
                    )
                    break
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
                rejection_diagnostics = saved.diagnostics
                context = patch_context.model_copy(update={"rejection": saved.rejection})
                continue
            if saved.semantic_key in seen:
                stop_reasons.append(
                    "Repair repeated the same editorial selection without improvement."
                )
                break
            if saved.selection is None or saved.draft is None:
                raise RuntimeError("selection repair returned no valid replacement")
            seen.add(saved.semantic_key)
            draft = saved.draft
            rejection_diagnostics = ()
            pending_assessment = None
            context = patch_context.model_copy(
                update={"selection": saved.selection, "assessment": None, "rejection": None}
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
