"""Finite opportunity discovery, independent portfolio judgment and scoped correction."""

# Orchestration refusals name their exact unavailable state at the boundary.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Literal

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.exceptions import UsageLimitExceeded
    from pydantic_ai.usage import UsageLimits

    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
        HarnessRunStatus,
        TopicSelectionColdReview,
        TopicSelectionDraft,
    )
    from temnia_pipeline.harness.editorial_policy import (
        TOPIC_SELECTION_POLICY_V3,
        TOPIC_SELECTION_POLICY_V4,
        TOPIC_SELECTION_POLICY_V5,
        TOPIC_SELECTION_POLICY_V6,
        TOPIC_SELECTION_POLICY_V7,
        EditorialPolicy,
    )
    from temnia_pipeline.harness.models import (
        TOPIC_SELECTION_AGENTS,
        TOPIC_SELECTION_V4_AGENTS,
        TOPIC_SELECTION_V5_AGENTS,
        TOPIC_SELECTION_V6_AGENTS,
        TOPIC_SELECTION_V7_AGENTS,
        HarnessModelDeps,
        topic_opportunity_inventory_v3,
        topic_opportunity_inventory_v4,
        topic_opportunity_inventory_v5,
        topic_opportunity_inventory_v6,
        topic_opportunity_inventory_v7,
        topic_selection_author_v3,
        topic_selection_author_v4,
        topic_selection_author_v5,
        topic_selection_author_v6,
        topic_selection_author_v7,
        topic_selection_cold_v3,
        topic_selection_cold_v4,
        topic_selection_cold_v5,
        topic_selection_cold_v6,
        topic_selection_cold_v7,
        topic_selection_patch_v3,
        topic_selection_patch_v4,
        topic_selection_patch_v5,
        topic_selection_patch_v6,
        topic_selection_patch_v7,
        topic_selection_source_v4,
        topic_selection_source_v5,
        topic_selection_source_v6,
        topic_selection_source_v7,
        topic_selection_source_v8,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.run_failures import failure_cause
    from temnia_pipeline.harness.runtime_types import (
        AcceptInitialRevisionRequest,
        BuildEvidenceRequest,
        ClaimRepairRequest,
        EvidenceResult,
        ResumeRunAssets,
        RunSnapshot,
        StageUpdate,
        StartRunRequest,
        StartRunResult,
        WorkflowIdentity,
    )
    from temnia_pipeline.harness.source_index import source_inspection_trace
    from temnia_pipeline.harness.source_progress import (
        SourceProgressCheckpoint,
        messages_from_checkpoint,
    )
    from temnia_pipeline.harness.topic_runtime import TopicCompilation
    from temnia_pipeline.harness.topic_selection import (
        selection_cold_key,
        selection_semantic_key,
    )
    from temnia_pipeline.harness.topic_selection_runtime import (
        AuthorPackagingManifestRequest,
        AuthorPackagingManifestResult,
        AuthorPackagingPlanResult,
        AuthorPackagingShardSaveRequest,
        AuthorPackagingShardSaveResult,
        ColdReviewSaveRequest,
        ColdReviewSaveResult,
        EditorialProgress,
        EditorialResumeLookup,
        EditorialWorkInput,
        EditorialWorkLookup,
        EditorialWorkResult,
        EditorialWorkSave,
        OpportunityInventoryManifestRequest,
        OpportunityInventoryManifestResult,
        OpportunityInventoryPlanResult,
        OpportunityInventorySaveRequest,
        OpportunityInventoryShardSaveRequest,
        OpportunityInventoryShardSaveResult,
        RepairManifestRequest,
        RepairManifestResult,
        RepairPlanResult,
        RepairShardSaveRequest,
        RepairShardSaveResult,
        SelectionAssessmentResult,
        SelectionCallPlan,
        SelectionContext,
        SelectionReviewRequest,
        SelectionSaveRequest,
        SelectionSaveResult,
        SelectionStopRequest,
        SourceCheckpointLoadRequest,
        SourceCheckpointLoadResult,
        SourceIndexBuildResult,
        SourceInspectionTrace,
        SourceReviewManifestRequest,
        SourceReviewManifestResult,
        SourceReviewPlanResult,
        SourceReviewShardSaveRequest,
        SourceReviewShardSaveResult,
        effective_topic_output_tokens,
        selection_call_config,
        selection_call_inputs,
    )
    from temnia_pipeline.harness.topic_workflow import RETRY, TopicRunWorkflow


Seat = Literal["author", "verifier"]
type ColdObservation = tuple[
    TopicSelectionColdReview, str, SelectionContext, SourceInspectionTrace | None
]


class SeatRoutesExhausted(RuntimeError):  # noqa: N818 - the name crosses Temporal as a type
    """Every qualified route for one seat failed transiently, each after backoff."""


class InvalidSeatOutput(RuntimeError):  # noqa: N818 - workflow-local typed failure state
    """A settled response was invalid after a route fallback; retain the route position."""

    def __init__(self, error: Exception, context: SelectionContext) -> None:
        super().__init__(failure_sentence(error))
        self.context = context


class ContinueIndexedWork(RuntimeError):  # noqa: N818 - workflow-local continuation
    """A request quantum ended with durable evidence, not an editorial failure."""

    def __init__(self, context: SelectionContext) -> None:
        super().__init__("Continue the indexed work item from its durable checkpoint.")
        self.context = context.model_copy(update={"resume_indexed": True})


def no_eligible_route(error: Exception) -> bool:
    """The seat pool has no route at the requested fallback position."""
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "NoEligibleRoute"


def failure_sentence(error: Exception) -> str:
    """The exact sentence the raising site wrote, across the Temporal boundary."""
    cause = failure_cause(error)
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
        editorial_context=plan.editorial_context,
        source_tool_role=plan.source_tool_role,
        candidate_selection=plan.candidate_selection,
        media_evidence=plan.media_evidence,
        allowed_browse_parent_ids=plan.allowed_browse_parent_ids,
        allowed_candidate_ids=plan.allowed_candidate_ids,
        allowed_sentence_ids=plan.allowed_sentence_ids,
        dispatch_limit=request.config.maxDispatches,
        synthetic_payload=plan.synthetic_payload,
    )


def execution_limit(error: Exception) -> bool:
    """Only known admission limits authorize retaining partial work; uncertainty propagates."""
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name in {
        "BudgetExceeded",
        "DispatchLimitExceeded",
        "ContextWindowExceeded",
        "SourceProgressLimitExceeded",
        "UsageLimitExceeded",
    }


def invalid_model_output(error: Exception) -> bool:
    """Recognize a retained paid response that failed typed normalization across Temporal."""
    if isinstance(error, InvalidSeatOutput):
        return True
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "UnexpectedModelBehavior"


def validation_refusal(error: Exception) -> bool:
    """A deterministic plan or admission refusal is reviewable, not an infrastructure failure."""
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name in {"HarnessValidationError", "ValidationError", "ValueError"}


def transient_provider_failure(error: Exception) -> bool:
    """A lost stream whose charge settled: a fresh paid attempt is allowed, unknowns are not."""
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "TransientProviderFailure"


def unavailable_request_route(error: Exception) -> bool:
    """A conclusive route incompatibility can try another eligible route without replay risk."""
    cause = failure_cause(error)
    name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
    return name == "ContextWindowExceeded" or (
        name == "KnownProviderRejection"
        and not any(status in str(cause) for status in ("HTTP 401", "HTTP 402"))
    )


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
INVENTORY_FAN_OUT = 3
AUTHOR_FAN_OUT = 3
SOURCE_REVIEW_FAN_OUT = 3
REPAIR_FAN_OUT = 3
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
    continue_indexed_work = False
    repair_base_count = 0

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
                repair_base_count=self.repair_base_count,
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
        execution_limited = False
        try:
            result, plan, settled = await self.run_seat(
                topic_opportunity_inventory_v3, request, context, "verifier"
            )
            context = self.carry_routes(context, settled)
            inventory = result.output
        except Exception as error:
            if isinstance(error, InvalidSeatOutput):
                context = self.carry_routes(context, error.context)
            if invalid_model_output(error):
                diagnostics = (
                    (
                        "Independent source opportunity inventory did not match its required "
                        "schema; authoring continued with that missing observation explicit."
                    ),
                )
            elif execution_limit(error):
                execution_limited = True
                diagnostics = (
                    (
                        "Execution capacity prevented independent source opportunity inventory; "
                        "authoring continued with that missing observation explicit."
                    ),
                )
            else:
                raise
        if execution_limited:
            return context.model_copy(
                update={
                    "inventory_attempted": True,
                    "inventory_diagnostics": diagnostics,
                }
            )
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

    async def run_seat(  # noqa: C901, PLR0912, PLR0915
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
        checkpoint: SourceProgressCheckpoint | None = None
        for _ in range(MAX_SEAT_ROUTES):
            try:
                plan = await self.prepare_selection(context)
            except Exception as error:
                if no_eligible_route(error) and tried:
                    break
                if unavailable_request_route(error):
                    last_failure = failure_sentence(error)
                    tried.append(f"pool position {getattr(context, index_field)}")
                    context = context.model_copy(
                        update={
                            index_field: getattr(context, index_field) + 1,
                            **({"verifier_index": 0} if seat == "author" else {}),
                        }
                    )
                    continue
                raise
            stage = plan.stage
            route = plan.author if seat == "author" else plan.verifier
            deps = selection_model_deps(request, plan)
            if context.resume_indexed and checkpoint is None:
                recovered = await workflow.execute_activity(
                    "load_topic_source_checkpoint",
                    SourceCheckpointLoadRequest(context=context, plan=plan),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RETRY,
                    result_type=SourceCheckpointLoadResult,
                )
                if recovered.checkpoint is None:
                    raise RuntimeError("indexed continuation lost its durable checkpoint")
                checkpoint = SourceProgressCheckpoint.model_validate(recovered.checkpoint)
            settings = {
                "max_tokens": effective_topic_output_tokens(request.config.maxOutputTokens, route)
            }
            for attempt in range(SAME_ROUTE_ATTEMPTS):
                try:
                    history = (
                        messages_from_checkpoint(checkpoint, prompt=plan.prompt)
                        if checkpoint is not None
                        else None
                    )
                    result = await agent.run(
                        None if history is not None else plan.prompt,
                        message_history=history,
                        deps=deps,
                        model_settings=settings,
                        usage_limits=UsageLimits(
                            request_limit=32 if self.continue_indexed_work else 256
                        ),
                    )
                except UsageLimitExceeded as error:
                    if self.continue_indexed_work and plan.source_tool_role is not None:
                        raise ContinueIndexedWork(context) from error
                    raise
                except Exception as error:
                    if invalid_model_output(error):
                        raise InvalidSeatOutput(error, context) from error
                    if unavailable_request_route(error):
                        last_failure = failure_sentence(error)
                        break
                    if not transient_provider_failure(error):
                        raise
                    last_failure = failure_sentence(error)
                    if plan.source_tool_role is not None:
                        recovered = await workflow.execute_activity(
                            "load_topic_source_checkpoint",
                            SourceCheckpointLoadRequest(context=context, plan=plan),
                            start_to_close_timeout=timedelta(minutes=2),
                            retry_policy=RETRY,
                            result_type=SourceCheckpointLoadResult,
                        )
                        if recovered.checkpoint is None:
                            raise RuntimeError(
                                "known indexed request failure has no durable checkpoint"
                            ) from error
                        checkpoint = SourceProgressCheckpoint.model_validate(recovered.checkpoint)
                    if attempt < SAME_ROUTE_ATTEMPTS - 1:
                        await workflow.sleep(advised_pause(last_failure, SAME_ROUTE_BACKOFF))
                    continue
                return result, plan, context
            tried.append(route.id)
            context = context.model_copy(
                update={
                    index_field: getattr(context, index_field) + 1,
                    **({"verifier_index": 0} if seat == "author" else {}),
                }
            )
        exhausted = (
            f"Every eligible {seat} route was unavailable for the {stage} call "
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

    async def recover_shard(  # noqa: PLR0913
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        *,
        agent: Any,  # noqa: ANN401
        seat: Seat,
        activity_name: str,
        request_type: Any,  # noqa: ANN401
        result_type: Any,  # noqa: ANN401
        output_field: str,
    ) -> tuple[Any, SelectionContext, str | None, bool]:
        """Give a settled invalid work item three distinct, accountable correction attempts.

        Every rejection is retained by the stage's normal save activity. A retry gets its own
        stage/request identity and the exact diagnostic. Unknown paid outcomes propagate without
        retry; no recovery path can bypass the ledger's exposure or budget fence.
        """
        feedback: tuple[str, ...] = context.recovery_feedback
        saved = None
        limited = False
        current = context
        for attempt in range(context.request_attempt, 3):
            current = context.model_copy(
                update={
                    "request_attempt": attempt,
                    "recovery_feedback": feedback,
                    "resume_indexed": context.resume_indexed and attempt == context.request_attempt,
                }
            )
            try:
                result, plan, settled = await self.run_seat(agent, request, current, seat)
                current = self.carry_routes(current, settled)
                inspection = (
                    source_inspection_trace(
                        result.all_messages(),
                        index_sha256=current.source_index.sha256,
                        role=plan.source_tool_role,
                        stage=plan.stage,
                    )
                    if current.source_index is not None and plan.source_tool_role is not None
                    else None
                )
                payload = request_type(
                    **{
                        "context": current,
                        output_field: result.output,
                        "inspection": inspection,
                    }
                )
            except Exception as error:
                if isinstance(error, ContinueIndexedWork):
                    raise
                if isinstance(error, InvalidSeatOutput):
                    current = self.carry_routes(current, error.context)
                context = self.carry_routes(context, current)
                if invalid_model_output(error):
                    payload = request_type(context=current, schema_error=failure_sentence(error))
                elif execution_limit(error):
                    cause = failure_cause(error)
                    name = (
                        cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
                    )
                    feedback = (failure_sentence(error),)
                    limited = True
                    if name in {"BudgetExceeded", "DispatchLimitExceeded"}:
                        return None, current, feedback[0], True
                    continue
                else:
                    raise
            saved = await workflow.execute_activity(
                activity_name,
                payload,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=result_type,
            )
            context = self.carry_routes(context, current)
            if saved.artifact is not None:
                return saved, current, None, False
            feedback = tuple(saved.diagnostics)
        return (
            saved,
            current,
            "Three correction attempts were exhausted: " + "; ".join(feedback),
            limited,
        )

    @staticmethod
    async def settle_shards(tasks: Any) -> list[Any]:  # noqa: ANN401
        """Drain dispatched siblings before propagating a fatal/unknown result."""
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def author_selection(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        run: RunSnapshot,
    ) -> tuple[SelectionSaveResult | None, SelectionContext, RunSnapshot, str | None]:
        """Run the historical whole-selection author and its admission correction loop."""
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
                if isinstance(error, InvalidSeatOutput):
                    context = self.carry_routes(context, error.context)
                if execution_limit(error):
                    return (
                        None,
                        context,
                        run,
                        (
                            "Execution capacity ended during indexed author discovery; the "
                            "durable progress checkpoints are retained and no ungrounded "
                            "selection was admitted."
                        ),
                    )
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
                return (
                    None,
                    context,
                    run,
                    "Initial selection remains invalid after admission corrections; discovery "
                    "is incomplete. " + "; ".join(saved.diagnostics)[:1200],
                )
            run = await self.claim_repair(run, request)
            context = context.model_copy(
                update={"rejection": saved.rejection, "iteration": context.iteration + 1}
            )
        return accepted, context, run, None

    async def review_selection(  # noqa: C901, PLR0912, PLR0915
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        draft: TopicSelectionDraft,
        cache: dict[str, ColdObservation],
    ) -> SelectionAssessmentResult:
        """Review local value and the original source, including an empty author selection."""
        cold_reviews: list[TopicSelectionColdReview] = []
        cold_candidate_ids: list[str] = []
        cold_stages: list[str] = []
        cold_contexts: list[SelectionContext] = []
        cold_inspections: list[SourceInspectionTrace | None] = []
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
        ) -> ColdObservation:
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
                return result.output, plan.stage, settled, None

        outcomes = await asyncio.gather(
            *(review_one(candidate) for candidate in pending), return_exceptions=True
        )
        context = context.model_copy(update={"verifier_index": settled_index["verifier_index"]})
        for candidate, outcome in zip(pending, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                if invalid_model_output(outcome):
                    if isinstance(outcome, InvalidSeatOutput):
                        context = context.model_copy(
                            update={
                                "verifier_index": max(
                                    context.verifier_index, outcome.context.verifier_index
                                )
                            }
                        )
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
            cold_contexts.append(cached[2])
            cold_inspections.append(cached[3])
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
                if isinstance(error, InvalidSeatOutput):
                    context = self.carry_routes(context, error.context)
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
                cold_contexts=tuple(cold_contexts),
                cold_inspections=tuple(cold_inspections),
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

    async def repair_selection(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
    ) -> tuple[SelectionSaveResult | None, SelectionContext, str | None, bool]:
        """Run the historical whole-assessment repair call for programs through v6."""
        patch_context = context.model_copy(update={"iteration": context.iteration + 1})
        try:
            result, patch_plan, settled = await self.run_seat(
                self.patch_agent, request, patch_context, "author"
            )
            patch_context = self.carry_routes(patch_context, settled)
            if patch_context.program_version in {
                TOPIC_SELECTION_POLICY_V5,
                TOPIC_SELECTION_POLICY_V6,
            }:
                patch_context = patch_context.model_copy(
                    update={
                        "verifier_index": 0,
                        "author_families": tuple(
                            dict.fromkeys(
                                (*patch_context.author_families, patch_plan.author.family)
                            )
                        ),
                    }
                )
            save = SelectionSaveRequest(context=patch_context, patch=result.output)
        except Exception as error:
            if isinstance(error, InvalidSeatOutput):
                patch_context = self.carry_routes(patch_context, error.context)
            if invalid_model_output(error):
                return (
                    None,
                    patch_context,
                    (
                        "Repair response was incomplete or invalid; the prior assessed selection "
                        "is retained for review."
                    ),
                    False,
                )
            if not execution_limit(error):
                raise
            return (
                None,
                patch_context,
                "Execution capacity ended before repair; the prior assessed selection is retained.",
                True,
            )
        return await self.save_selection(save), patch_context, None, False

    async def program(self, request: ChapterRunInput) -> ChapterRunOutput:
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
        self.repair_base_count = run.repair_count if self.policy == TOPIC_SELECTION_POLICY_V7 else 0
        if run.editorial_policy != self.policy:
            raise RuntimeError("selection workflow cannot reinterpret another program generation")
        if self.policy == TOPIC_SELECTION_POLICY_V7:
            lookup = await workflow.execute_activity(
                "load_topic_editorial_progress",
                self.ref(request),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=EditorialResumeLookup,
            )
            resumed = lookup.resume
            if resumed is not None and resumed.progress.phase == "review":
                context = resumed.progress.context.model_copy(
                    update={
                        "assessment": None,
                        "rejection": None,
                        "source_review_plan": None,
                        "source_review_work_item_id": None,
                        "repair_plan": None,
                        "repair_work_item_id": None,
                    }
                )
                return await self.review_and_compile(
                    request, context, resumed.draft, run, resumed.progress.seen_keys
                )
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
            program_version=self.policy,
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
            result_type=SourceIndexBuildResult,
        )
        context = context.model_copy(update={"source_index": source_index.artifact})
        context = await self.prepare_author_context(request, context)
        if self.policy in {
            TOPIC_SELECTION_POLICY_V4,
            TOPIC_SELECTION_POLICY_V5,
            TOPIC_SELECTION_POLICY_V6,
            TOPIC_SELECTION_POLICY_V7,
        } and (context.inventory is None):
            return await self.finish(
                request,
                evidence=evidence.artifact,
                edit=None,
                revision=0,
                message=(
                    "Bounded opportunity inventory is incomplete; completed section shards and "
                    "durable source checkpoints are retained. "
                    + "; ".join(context.inventory_diagnostics)[:1200]
                ),
            )
        accepted, context, run, author_error = await self.author_selection(request, context, run)
        if accepted is None:
            return await self.finish(
                request,
                evidence=evidence.artifact,
                edit=None,
                revision=0,
                message=author_error or "Author packaging is incomplete.",
            )
        if accepted.selection is None or accepted.draft is None:
            raise RuntimeError("selection admission returned no accepted draft")
        draft = accepted.draft
        context = context.model_copy(update={"selection": accepted.selection, "rejection": None})
        return await self.review_and_compile(
            request, context, draft, run, (selection_semantic_key(draft),)
        )

    async def persist_progress(self, progress: EditorialProgress) -> None:
        """Keep editorial resume distinct from rendering a compiled revision."""
        if self.policy == TOPIC_SELECTION_POLICY_V7:
            await workflow.execute_activity(
                "save_topic_editorial_progress",
                progress,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
            )

    async def review_and_compile(  # noqa: C901, PLR0912, PLR0915
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        draft: TopicSelectionDraft,
        run: RunSnapshot,
        seen_keys: tuple[str, ...],
    ) -> ChapterRunOutput:
        """Resume independent review and repair from the last accepted editorial selection."""
        seen = set(seen_keys)
        control = control_task_queue(workflow.info().task_queue)
        if str(run.stage) != "planning":
            run = await workflow.execute_activity(
                "update_chapter_run_stage",
                StageUpdate(
                    **self.ref(request).model_dump(),
                    expected_stage=run.stage,
                    expected_revision=run.current_revision,
                    next_stage="planning",
                    status=HarnessRunStatus.running,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                task_queue=control,
                retry_policy=RETRY,
                result_type=RunSnapshot,
            )
        repair_ceiling = self.repair_base_count + request.config.maxRepairs
        await self.persist_progress(
            EditorialProgress(
                context=context, base_revision=run.current_revision, seen_keys=tuple(sorted(seen))
            )
        )
        cache: dict[str, ColdObservation] = {}
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
            if run.repair_count >= repair_ceiling:
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
            saved, patch_context, repair_error, repair_limited = await self.repair_selection(
                request, context
            )
            if saved is None:
                limited = repair_limited
                stop_reasons.append(repair_error or "Repair did not produce a complete patch.")
                break
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
            seen.add(selection_semantic_key(saved.draft))
            draft = saved.draft
            rejection_diagnostics = ()
            pending_assessment = None
            cache.clear()
            context = patch_context.model_copy(
                update={
                    "selection": saved.selection,
                    "assessment": None,
                    "rejection": None,
                    "source_review_plan": None,
                    "source_review_work_item_id": None,
                    "repair_plan": None,
                    "repair_work_item_id": None,
                }
            )
            await self.persist_progress(
                EditorialProgress(
                    context=context,
                    base_revision=run.current_revision,
                    seen_keys=tuple(sorted(seen)),
                )
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
        progress = EditorialProgress(
            context=context,
            base_revision=run.current_revision,
            compiled=compiled.artifact,
            phase="render" if str(final.assessment.executionStatus) == "complete" else "review",
            seen_keys=tuple(sorted(seen)),
        )
        await self.persist_progress(progress)
        accepted_run = await workflow.execute_activity(
            "accept_initial_chapter_revision",
            AcceptInitialRevisionRequest(
                run=self.ref(request),
                request_key=request.requestKey,
                edit_artifact_id=compiled.artifact.id,
                base_revision=run.current_revision,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            task_queue=control,
            result_type=RunSnapshot,
        )
        await self.persist_progress(
            progress.model_copy(update={"base_revision": accepted_run.current_revision})
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
            evidence=context.evidence,
            edit=compiled.artifact,
            revision=accepted_run.current_revision,
            reasons=(*compiled.refusals, *stop_reasons),
        )


@workflow.defn(name="TopicSelectionWorkflowV4")
class TopicSelectionWorkflowV4(TopicSelectionWorkflow):
    """Bound source discovery per section, then reuse the reviewed v3 packaging cycle."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_V4_AGENTS
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V4
    inventory_agent = topic_opportunity_inventory_v4
    author_agent = topic_selection_author_v4
    cold_agent = topic_selection_cold_v4
    source_agent = topic_selection_source_v5
    patch_agent = topic_selection_patch_v4

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Keep a distinct Temporal type and agent activity set for v4 history."""
        return await super().run(request)

    async def prepare_author_context(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
    ) -> SelectionContext:
        """Inventory every planned section with bounded fan-out before author packaging."""
        prepared = await workflow.execute_activity(
            "prepare_topic_inventory_plan_v4",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=OpportunityInventoryPlanResult,
        )
        context = context.model_copy(update={"inventory_plan": prepared.artifact})
        fan_out = asyncio.Semaphore(INVENTORY_FAN_OUT)

        async def inventory_one(section_id: str) -> tuple[Any, SelectionContext, str | None]:
            async with fan_out:
                saved, settled, reason, _ = await self.recover_shard(
                    request,
                    context.model_copy(update={"inventory_section_id": section_id}),
                    agent=self.inventory_agent,
                    seat="verifier",
                    activity_name="save_topic_inventory_shard_v4",
                    request_type=OpportunityInventoryShardSaveRequest,
                    result_type=OpportunityInventoryShardSaveResult,
                    output_field="inventory",
                )
                return saved, settled, reason

        outcomes = await self.settle_shards(
            inventory_one(section.sectionId) for section in prepared.plan.sections
        )
        settled_verifier = max(
            (settled.verifier_index for _, settled, _ in outcomes),
            default=context.verifier_index,
        )
        diagnostics = tuple(dict.fromkeys(reason for _, _, reason in outcomes if reason))
        artifacts = tuple(
            saved.artifact
            for saved, _, _ in outcomes
            if saved is not None and saved.artifact is not None
        )
        base = context.model_copy(
            update={
                "inventory_attempted": True,
                "inventory_diagnostics": diagnostics,
                "inventory_section_id": None,
                "verifier_index": settled_verifier,
            }
        )
        if len(artifacts) != len(prepared.plan.sections):
            return base
        assembled = await workflow.execute_activity(
            "assemble_topic_inventory_v4",
            OpportunityInventoryManifestRequest(context=base, shard_artifacts=artifacts),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=OpportunityInventoryManifestResult,
        )
        return base.model_copy(update={"inventory": assembled.artifact})


@workflow.defn(name="TopicSelectionWorkflowV5")
class TopicSelectionWorkflowV5(TopicSelectionWorkflowV4):
    """Bound both source inventory and author packaging before portfolio review."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_V5_AGENTS
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V5
    inventory_agent = topic_opportunity_inventory_v5
    author_agent = topic_selection_author_v5
    cold_agent = topic_selection_cold_v5
    source_agent = topic_selection_source_v6
    patch_agent = topic_selection_patch_v5

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Keep v5 history and model activities disjoint from prior programs."""
        return await super().run(request)

    async def author_selection(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        run: RunSnapshot,
    ) -> tuple[SelectionSaveResult | None, SelectionContext, RunSnapshot, str | None]:
        """Package every planned opportunity batch before publishing one selection."""
        prepared = await workflow.execute_activity(
            "prepare_topic_author_plan_v5",
            context,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=AuthorPackagingPlanResult,
        )
        context = context.model_copy(update={"author_plan": prepared.artifact})
        fan_out = asyncio.Semaphore(AUTHOR_FAN_OUT)

        async def author_one(work_item_id: str) -> tuple[Any, SelectionContext, str | None]:
            async with fan_out:
                saved, settled, reason, _ = await self.recover_shard(
                    request,
                    context.model_copy(update={"author_work_item_id": work_item_id}),
                    agent=self.author_agent,
                    seat="author",
                    activity_name="save_topic_author_shard_v5",
                    request_type=AuthorPackagingShardSaveRequest,
                    result_type=AuthorPackagingShardSaveResult,
                    output_field="draft",
                )
                return saved, settled, reason

        outcomes = await self.settle_shards(
            author_one(item.workItemId) for item in prepared.plan.workItems
        )
        settled_author = max(
            (settled.author_index for _, settled, _ in outcomes),
            default=context.author_index,
        )
        diagnostics = tuple(dict.fromkeys(reason for _, _, reason in outcomes if reason))
        artifacts = tuple(
            saved.artifact
            for saved, _, _ in outcomes
            if saved is not None and saved.artifact is not None
        )
        base = context.model_copy(
            update={
                "author_work_item_id": None,
                "author_index": settled_author,
            }
        )
        if len(artifacts) != len(prepared.plan.workItems):
            return (
                None,
                base,
                run,
                "Bounded author packaging is incomplete; completed work-item shards and durable "
                "source checkpoints are retained. " + "; ".join(diagnostics)[:1200],
            )
        assembled = await workflow.execute_activity(
            "assemble_topic_author_v5",
            AuthorPackagingManifestRequest(context=base, shard_artifacts=artifacts),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=AuthorPackagingManifestResult,
        )
        families = tuple(value.root for value in assembled.manifest.generatorFamilies)
        accepted = SelectionSaveResult(
            selection=assembled.selection,
            semantic_key=selection_semantic_key(assembled.draft),
            draft=assembled.draft,
        )
        return (
            accepted,
            base.model_copy(update={"author_families": families, "verifier_index": 0}),
            run,
            None,
        )


@workflow.defn(name="TopicSelectionWorkflowV6")
class TopicSelectionWorkflowV6(TopicSelectionWorkflowV5):
    """Bound inventory, author packaging and independent source review."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_V6_AGENTS
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V6
    inventory_agent = topic_opportunity_inventory_v6
    author_agent = topic_selection_author_v6
    cold_agent = topic_selection_cold_v6
    source_agent = topic_selection_source_v7
    patch_agent = topic_selection_patch_v6

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Keep v6 history and model activities disjoint from prior programs."""
        return await super().run(request)

    async def review_selection(  # noqa: C901, PLR0912, PLR0915
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        draft: TopicSelectionDraft,
        cache: dict[str, ColdObservation],
    ) -> SelectionAssessmentResult:
        """Admit cold reviews, then require every bounded source-review shard."""
        cold_reviews: list[TopicSelectionColdReview] = []
        cold_candidate_ids: list[str] = []
        cold_stages: list[str] = []
        cold_contexts: list[SelectionContext] = []
        cold_inspections: list[SourceInspectionTrace | None] = []
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
        cold_fan_out = asyncio.Semaphore(COLD_REVIEW_FAN_OUT)
        settled_index = {"verifier_index": context.verifier_index}

        async def review_cold(candidate: Any) -> ColdObservation:  # noqa: ANN401
            async with cold_fan_out:
                cold_context = context.model_copy(
                    update={
                        "candidate_id": candidate.id,
                        "verifier_index": settled_index["verifier_index"],
                    }
                )
                if context.program_version == TOPIC_SELECTION_POLICY_V7:
                    saved, settled, reason, _ = await self.recover_shard(
                        request,
                        cold_context,
                        agent=self.cold_agent,
                        seat="verifier",
                        activity_name="save_topic_cold_review_v7",
                        request_type=ColdReviewSaveRequest,
                        result_type=ColdReviewSaveResult,
                        output_field="review",
                    )
                    settled_index["verifier_index"] = max(
                        settled_index["verifier_index"], settled.verifier_index
                    )
                    if saved is None or saved.review is None or saved.artifact is None:
                        raise ApplicationError(
                            reason or "Cold judgment could not be completed after correction.",
                            type="SourceProgressLimitExceeded",
                            non_retryable=True,
                        )
                    stage = f"verify:selection:cold:{selection_cold_key(candidate, rubric_sha)}"
                    if settled.request_attempt:
                        stage += f":retry-{settled.request_attempt}"
                    return saved.review, stage, settled, saved.inspection
                result, plan, settled = await self.run_seat(
                    self.cold_agent, request, cold_context, "verifier"
                )
                settled_index["verifier_index"] = max(
                    settled_index["verifier_index"], settled.verifier_index
                )
                return result.output, plan.stage, settled, None

        cold_outcomes = await asyncio.gather(
            *(review_cold(candidate) for candidate in pending), return_exceptions=True
        )
        context = context.model_copy(update={"verifier_index": settled_index["verifier_index"]})
        for candidate, outcome in zip(pending, cold_outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                if invalid_model_output(outcome):
                    if isinstance(outcome, InvalidSeatOutput):
                        context = context.model_copy(
                            update={
                                "verifier_index": max(
                                    context.verifier_index, outcome.context.verifier_index
                                )
                            }
                        )
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
            cold_contexts.append(cached[2])
            cold_inspections.append(cached[3])

        manifest_ref: HarnessArtifactRef | None = None
        if not limited:
            prepared = await workflow.execute_activity(
                "prepare_topic_source_review_plan_v6",
                context,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=SourceReviewPlanResult,
            )
            context = context.model_copy(update={"source_review_plan": prepared.artifact})
            review_fan_out = asyncio.Semaphore(SOURCE_REVIEW_FAN_OUT)

            async def review_one(work_item_id: str) -> tuple[Any, SelectionContext, str | None]:
                async with review_fan_out:
                    saved, settled, reason, _ = await self.recover_shard(
                        request,
                        context.model_copy(
                            update={
                                "source_review_work_item_id": work_item_id,
                                "verifier_index": settled_index["verifier_index"],
                            }
                        ),
                        agent=self.source_agent,
                        seat="verifier",
                        activity_name="save_topic_source_review_shard_v6",
                        request_type=SourceReviewShardSaveRequest,
                        result_type=SourceReviewShardSaveResult,
                        output_field="review",
                    )
                    settled_index["verifier_index"] = max(
                        settled_index["verifier_index"], settled.verifier_index
                    )
                    return saved, settled, reason

            outcomes = await self.settle_shards(
                review_one(item.workItemId) for item in prepared.plan.workItems
            )
            context = context.model_copy(
                update={
                    "source_review_work_item_id": None,
                    "verifier_index": max(
                        (settled.verifier_index for _, settled, _ in outcomes),
                        default=context.verifier_index,
                    ),
                }
            )
            shard_refs = tuple(
                saved.artifact
                for saved, _, _ in outcomes
                if saved is not None and saved.artifact is not None
            )
            failures = tuple(dict.fromkeys(reason for _, _, reason in outcomes if reason))
            reasons.extend(failures)
            if len(shard_refs) == len(prepared.plan.workItems):
                assembled = await workflow.execute_activity(
                    "assemble_topic_source_review_v6",
                    SourceReviewManifestRequest(context=context, shard_artifacts=shard_refs),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RETRY,
                    result_type=SourceReviewManifestResult,
                )
                manifest_ref = assembled.artifact
            else:
                reasons.append(
                    "Bounded source review is incomplete; no partial shard finding can authorize "
                    "repair."
                )
                limited = any(saved is None for saved, _, _ in outcomes)
        self.settled_context = context
        return await workflow.execute_activity(
            "save_topic_selection_assessment",
            SelectionReviewRequest(
                context=context,
                cold_reviews=tuple(cold_reviews),
                cold_candidate_ids=tuple(cold_candidate_ids),
                cold_stages=tuple(cold_stages),
                cold_contexts=tuple(cold_contexts),
                cold_inspections=tuple(cold_inspections),
                unavailable_cold_ids=tuple(unavailable),
                source_dispatched=False,
                source_review_manifest=manifest_ref,
                reasons=tuple(reasons),
                execution_limited=limited,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=SelectionAssessmentResult,
        )


@workflow.defn(name="TopicSelectionWorkflowV7")
class TopicSelectionWorkflowV7(TopicSelectionWorkflowV6):
    """Bound inventory, author, source review and atomic connected-component repair."""

    __pydantic_ai_agents__ = ()
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V7
    inventory_agent = topic_opportunity_inventory_v7
    author_agent = topic_selection_author_v7
    cold_agent = topic_selection_cold_v7
    source_agent = topic_selection_source_v8
    patch_agent = topic_selection_patch_v7

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Keep v7 history and model activities disjoint from prior programs."""
        return await super().run(request)

    async def recover_shard(  # noqa: PLR0913
        self,
        request: ChapterRunInput,
        context: SelectionContext,
        *,
        agent: Any,  # noqa: ANN401, ARG002
        seat: Seat,  # noqa: ARG002
        activity_name: str,
        request_type: Any,  # noqa: ANN401, ARG002
        result_type: Any,  # noqa: ANN401
        output_field: str,  # noqa: ARG002
    ) -> tuple[Any, SelectionContext, str | None, bool]:
        """Run each indexed decision in its own bounded Temporal history."""
        work = EditorialWorkInput(request=request, context=context, activity_name=activity_name)
        identity = work.identity()
        result = await workflow.execute_child_workflow(
            "TopicEditorialWorkWorkflow",
            work,
            id=f"{workflow.info().workflow_id}:editorial:{identity}",
            result_type=EditorialWorkResult,
        )
        return (
            result_type.model_validate(result.saved) if result.saved is not None else None,
            result.context,
            result.reason,
            result.limited,
        )

    async def repair_selection(
        self,
        request: ChapterRunInput,
        context: SelectionContext,
    ) -> tuple[SelectionSaveResult | None, SelectionContext, str | None, bool]:
        """Admit every bounded component before one aggregate patch changes the selection."""
        patch_context = context.model_copy(update={"iteration": context.iteration + 1})
        try:
            prepared = await workflow.execute_activity(
                "prepare_topic_repair_plan_v7",
                patch_context,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=RepairPlanResult,
            )
        except Exception as error:
            if not validation_refusal(error):
                raise
            return (
                None,
                patch_context,
                "Repair planning refused the coupled finding graph: " + failure_sentence(error),
                False,
            )
        patch_context = patch_context.model_copy(update={"repair_plan": prepared.artifact})
        repair_fan_out = asyncio.Semaphore(REPAIR_FAN_OUT)
        settled_index = {"author_index": patch_context.author_index}

        async def repair_one(work_item_id: str) -> tuple[Any, SelectionContext, str | None, bool]:
            async with repair_fan_out:
                outcome = await self.recover_shard(
                    request,
                    patch_context.model_copy(
                        update={
                            "repair_work_item_id": work_item_id,
                            "author_index": settled_index["author_index"],
                        }
                    ),
                    agent=self.patch_agent,
                    seat="author",
                    activity_name="save_topic_repair_shard_v7",
                    request_type=RepairShardSaveRequest,
                    result_type=RepairShardSaveResult,
                    output_field="patch",
                )
                settled_index["author_index"] = max(
                    settled_index["author_index"], outcome[1].author_index
                )
                return outcome

        outcomes = await self.settle_shards(
            repair_one(item.workItemId) for item in prepared.plan.workItems
        )
        patch_context = patch_context.model_copy(
            update={
                "repair_work_item_id": None,
                "author_index": max(
                    (settled.author_index for _, settled, _, _ in outcomes),
                    default=patch_context.author_index,
                ),
            }
        )
        shard_refs = tuple(
            saved.artifact
            for saved, _, _, _ in outcomes
            if saved is not None and saved.artifact is not None
        )
        failures = tuple(dict.fromkeys(reason for _, _, reason, _ in outcomes if reason))
        if len(shard_refs) != len(prepared.plan.workItems):
            return (
                None,
                patch_context,
                "Bounded repair is incomplete; no component changed the assessed selection. "
                + "; ".join(failures),
                any(limited for _, _, _, limited in outcomes),
            )
        try:
            assembled = await workflow.execute_activity(
                "assemble_topic_repair_v7",
                RepairManifestRequest(context=patch_context, shard_artifacts=shard_refs),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
                result_type=RepairManifestResult,
            )
        except Exception as error:
            if not validation_refusal(error):
                raise
            return (
                None,
                patch_context,
                "Atomic repair assembly refused every component: " + failure_sentence(error),
                False,
            )
        patch_context = patch_context.model_copy(
            update={
                "verifier_index": 0,
                "author_families": tuple(
                    dict.fromkeys(
                        (
                            *patch_context.author_families,
                            *(value.root for value in assembled.manifest.authorFamilies),
                        )
                    )
                ),
            }
        )
        return (
            SelectionSaveResult(
                selection=assembled.selection,
                semantic_key=assembled.semantic_key,
                draft=assembled.draft,
            ),
            patch_context,
            None,
            False,
        )


@workflow.defn(name="TopicEditorialWorkWorkflow")
class TopicEditorialWorkWorkflow(TopicSelectionWorkflow):
    """An indexed decision continues across histories without replaying unknown paid calls."""

    __pydantic_ai_agents__ = TOPIC_SELECTION_V7_AGENTS
    continue_indexed_work = True

    @workflow.run
    async def run(self, work: EditorialWorkInput) -> EditorialWorkResult:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Execute one decision or continue it from the last immutable request checkpoint."""
        cached = await workflow.execute_activity(
            "load_topic_editorial_work",
            work,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
            result_type=EditorialWorkLookup,
        )
        if cached.result is not None:
            return cached.result
        stages: dict[str, tuple[Any, Seat, Any, Any, str]] = {
            "save_topic_inventory_shard_v4": (
                TopicSelectionWorkflowV7.inventory_agent,
                "verifier",
                OpportunityInventoryShardSaveRequest,
                OpportunityInventoryShardSaveResult,
                "inventory",
            ),
            "save_topic_author_shard_v5": (
                TopicSelectionWorkflowV7.author_agent,
                "author",
                AuthorPackagingShardSaveRequest,
                AuthorPackagingShardSaveResult,
                "draft",
            ),
            "save_topic_cold_review_v7": (
                TopicSelectionWorkflowV7.cold_agent,
                "verifier",
                ColdReviewSaveRequest,
                ColdReviewSaveResult,
                "review",
            ),
            "save_topic_source_review_shard_v6": (
                TopicSelectionWorkflowV7.source_agent,
                "verifier",
                SourceReviewShardSaveRequest,
                SourceReviewShardSaveResult,
                "review",
            ),
            "save_topic_repair_shard_v7": (
                TopicSelectionWorkflowV7.patch_agent,
                "author",
                RepairShardSaveRequest,
                RepairShardSaveResult,
                "patch",
            ),
        }
        try:
            agent, seat, request_type, result_type, output_field = stages[work.activity_name]
            saved, context, reason, limited = await self.recover_shard(
                work.request,
                work.resumed_context or work.context,
                agent=agent,
                seat=seat,
                activity_name=work.activity_name,
                request_type=request_type,
                result_type=result_type,
                output_field=output_field,
            )
        except ContinueIndexedWork as continuation:
            workflow.continue_as_new(
                work.model_copy(update={"resumed_context": continuation.context})
            )
        except Exception as error:
            cause = failure_cause(error)
            name = cause.type if isinstance(cause, ApplicationError) else type(cause).__name__
            raise ApplicationError(
                failure_sentence(error), type=name, non_retryable=True
            ) from error
        result = EditorialWorkResult(
            saved=saved.model_dump(mode="json") if saved is not None else None,
            context=context,
            reason=reason,
            limited=limited,
        )
        if saved is not None and saved.artifact is not None:
            await workflow.execute_activity(
                "save_topic_editorial_work",
                EditorialWorkSave(work=work, result=result),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RETRY,
            )
        return result
