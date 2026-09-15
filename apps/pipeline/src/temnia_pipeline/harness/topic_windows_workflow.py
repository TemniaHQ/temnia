"""`TopicSelectionWorkflowV8`: a flat parent that orchestrates bounded decisions.

The workflow holds only plans, artifact references, gaps and the settled route positions. Every
model round runs inside one `run_topic_decision_v8` activity, so a four-hour source produces a
few kilobytes of history per decision rather than a copy of every request. Typed stops raised by
the decision activity (budget, dispatch, unknown outcome, route exhaustion) propagate to the base
run fence, which records the status the panel shows.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.contracts import (
        ChapterRunInput,
        ChapterRunOutput,
        HarnessArtifactRef,
    )
    from temnia_pipeline.harness.editorial_policy import (
        TOPIC_SELECTION_POLICY_V8,
        EditorialPolicy,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.run_failures import ACTIVITY_RETRY as RETRY
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
    from temnia_pipeline.harness.topic_decisions import (
        AssembleRequestV8,
        AssessmentRequestV8,
        AuthorAssemblyV8,
        AuthorPlanResultV8,
        DecisionRequest,
        DecisionResult,
        InventoryAssemblyV8,
        PlanResultV8,
        RepairAssemblyV8,
        RepairPlanResultV8,
        ReviewPlanResultV8,
    )
    from temnia_pipeline.harness.topic_runtime import TopicCompilation
    from temnia_pipeline.harness.topic_selection import selection_cold_key
    from temnia_pipeline.harness.topic_selection_runtime import (
        SelectionAssessmentResult,
        SelectionContext,
        SelectionStopRequest,
        SourceIndexBuildResult,
    )
    from temnia_pipeline.harness.topic_windows import MAX_ROUNDS, DecisionKind
    from temnia_pipeline.harness.topic_workflow import TopicRunWorkflow

# How many decisions of one stage run at once. The worker's per-route in-flight gate bounds what
# reaches the provider; this bounds the workflow's outstanding activities.
DECISION_FAN_OUT = 3
# One model round is allowed ten minutes on the slowest qualified transport, plus admission.
ROUND_SECONDS = 600
MAX_DECISION_SECONDS = 45 * 60
DECISION_HEARTBEAT = timedelta(seconds=90)
SHORT = timedelta(minutes=2)


def decision_timeout(kind: DecisionKind) -> timedelta:
    """A decision's activity deadline follows its round allowance, never a fixed ceiling."""
    return timedelta(seconds=min(MAX_DECISION_SECONDS, MAX_ROUNDS[kind] * ROUND_SECONDS + 120))


@workflow.defn(name="TopicSelectionWorkflowV8")
class TopicSelectionWorkflowV8(TopicRunWorkflow):
    """Bounded inline windows, one activity per decision, complete-with-gaps assembly."""

    __pydantic_ai_agents__ = ()
    policy: EditorialPolicy = TOPIC_SELECTION_POLICY_V8

    @workflow.run
    async def run(self, request: ChapterRunInput) -> ChapterRunOutput:
        """Keep v8 history and activity names disjoint from the historical programs."""
        return await super().run(request)

    # ------------------------------------------------------------------ helpers

    async def decide_all(
        self, kind: DecisionKind, item_ids: tuple[str, ...], context: SelectionContext
    ) -> tuple[list[DecisionResult], SelectionContext]:
        """Run one stage's decisions with bounded fan-out; settle every sibling before raising."""
        gate = asyncio.Semaphore(DECISION_FAN_OUT)

        async def decide(item_id: str) -> DecisionResult:
            async with gate:
                return await workflow.execute_activity(
                    "run_topic_decision_v8",
                    DecisionRequest(context=context, kind=kind, item_id=item_id),
                    start_to_close_timeout=decision_timeout(kind),
                    heartbeat_timeout=DECISION_HEARTBEAT,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    result_type=DecisionResult,
                )

        settled = await asyncio.gather(*(decide(item) for item in item_ids), return_exceptions=True)
        results: list[DecisionResult] = []
        failure: BaseException | None = None
        for outcome in settled:
            if isinstance(outcome, BaseException):
                failure = failure or outcome
            else:
                results.append(outcome)
        if failure is not None:
            raise failure
        carried = context.model_copy(
            update={
                "author_index": max(
                    (item.author_index for item in results), default=context.author_index
                ),
                "verifier_index": max(
                    (item.verifier_index for item in results), default=context.verifier_index
                ),
            }
        )
        return results, carried

    async def claim_repair(self, run: RunSnapshot, request: ChapterRunInput) -> RunSnapshot:
        """Use the run-owned repair allowance; the ledger refuses a fourth."""
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

    async def activity[T](self, name: str, payload: Any, result_type: type[T]) -> T:  # noqa: ANN401
        """A short deterministic activity with the standard retry policy."""
        return await workflow.execute_activity(
            name,
            payload,
            start_to_close_timeout=SHORT,
            retry_policy=RETRY,
            result_type=result_type,
        )

    # ------------------------------------------------------------------ program

    async def program(self, request: ChapterRunInput) -> ChapterRunOutput:  # noqa: C901, PLR0912, PLR0915
        """Inventory, package, review, repair, compile and render, with gaps recorded."""
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
            raise RuntimeError(  # noqa: TRY003
                "selection workflow cannot reinterpret another program generation"  # noqa: EM101
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
            run=self.ref(request), evidence=evidence.artifact, program_version=self.policy
        )
        rubric = await self.activity("prepare_topic_selection_rubric", context, HarnessArtifactRef)
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
        coverage: list[str] = []

        # 1. Plan and project, then inventory every section.
        planned = await self.activity("prepare_topic_plan_v8", context, PlanResultV8)
        context = context.model_copy(update={"inventory_plan": planned.inventory_plan})
        results, context = await self.decide_all("inventory", planned.section_ids, context)
        inventory = await self.activity(
            "assemble_topic_inventory_v8",
            AssembleRequestV8(context=context, results=tuple(results)),
            InventoryAssemblyV8,
        )
        context = context.model_copy(update={"inventory": inventory.artifact})
        coverage.extend(
            f"Inventory of {gap.itemId} is unavailable: {gap.reason}" for gap in inventory.gaps
        )

        # 2. Package every section's opportunities into candidates.
        author_plan = await self.activity(
            "prepare_topic_author_plan_v8", context, AuthorPlanResultV8
        )
        context = context.model_copy(update={"author_plan": author_plan.artifact})
        results, context = await self.decide_all("author", author_plan.work_item_ids, context)
        authored = await self.activity(
            "assemble_topic_author_v8",
            AssembleRequestV8(context=context, results=tuple(results)),
            AuthorAssemblyV8,
        )
        coverage.extend(
            f"Packaging of {gap.itemId} is unavailable: {gap.reason}" for gap in authored.gaps
        )
        if authored.selection is None or authored.draft is None:
            return await self.finish(
                request,
                evidence=evidence.artifact,
                edit=None,
                revision=0,
                message=(authored.reason or "Author packaging is incomplete.")[:2000],
            )
        context = context.model_copy(
            update={"selection": authored.selection, "author_families": authored.families}
        )
        draft = authored.draft
        seen = {authored.semantic_key}

        # 3. Review, repair, review again; the loop ends, the run never stops here.
        cold_cache: dict[str, DecisionResult] = {}
        stop_reasons: list[str] = []
        final: SelectionAssessmentResult | None = None
        while True:
            rubric_sha = context.rubric.sha256 if context.rubric is not None else ""
            keys = {
                candidate.id: selection_cold_key(candidate, rubric_sha)
                for candidate in draft.proposal.candidates
            }
            pending = tuple(cid for cid, key in keys.items() if key not in cold_cache)
            cold_results, context = await self.decide_all("cold", pending, context)
            for result in cold_results:
                cold_cache[keys[result.item_id]] = result
            review_plan = await self.activity(
                "prepare_topic_review_plan_v8", context, ReviewPlanResultV8
            )
            context = context.model_copy(update={"source_review_plan": review_plan.artifact})
            review_results, context = await self.decide_all(
                "review", review_plan.work_item_ids, context
            )
            final = await self.activity(
                "assemble_topic_assessment_v8",
                AssessmentRequestV8(
                    context=context,
                    cold=tuple(cold_cache[keys[cid]] for cid in keys),
                    review=tuple(review_results),
                ),
                SelectionAssessmentResult,
            )
            context = context.model_copy(update={"assessment": final.artifact})
            if str(final.assessment.executionStatus) == "complete" or not final.actionable:
                break
            if run.repair_count >= request.config.maxRepairs:
                stop_reasons.append(
                    "The configured repair allowance ended with unresolved editorial findings."
                )
                break
            run = await self.claim_repair(run, request)
            repair_context = context.model_copy(update={"iteration": context.iteration + 1})
            repair_plan = await self.activity(
                "prepare_topic_repair_plan_v8", repair_context, RepairPlanResultV8
            )
            if repair_plan.artifact is None:
                stop_reasons.append(repair_plan.reason or "Repair planning produced no component.")
                break
            repair_context = repair_context.model_copy(update={"repair_plan": repair_plan.artifact})
            repair_results, repair_context = await self.decide_all(
                "repair", repair_plan.work_item_ids, repair_context
            )
            repaired = await self.activity(
                "assemble_topic_repair_v8",
                AssembleRequestV8(context=repair_context, results=tuple(repair_results)),
                RepairAssemblyV8,
            )
            coverage.extend(
                f"Repair of {gap.itemId} is unavailable: {gap.reason}" for gap in repaired.gaps
            )
            if repaired.selection is None or repaired.draft is None:
                stop_reasons.append(repaired.reason or "Repair did not change the selection.")
                break
            if repaired.semantic_key in seen:
                stop_reasons.append(
                    "Repair repeated the same editorial selection without improvement."
                )
                break
            seen.add(repaired.semantic_key)
            draft = repaired.draft
            context = repair_context.model_copy(
                update={
                    "selection": repaired.selection,
                    "assessment": None,
                    "source_review_plan": None,
                    "repair_plan": None,
                    "author_families": tuple(
                        dict.fromkeys((*repair_context.author_families, *repaired.families))
                    ),
                }
            )
        assert final is not None  # noqa: S101 - the loop assigns before any break
        if stop_reasons:
            final = await self.activity(
                "stop_topic_selection",
                SelectionStopRequest(context=context, reasons=tuple(stop_reasons)),
                SelectionAssessmentResult,
            )
            context = context.model_copy(update={"assessment": final.artifact})

        # 4. Compile safe cuts from selected candidates and render for human review.
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
        reasons = [*compiled.refusals, *stop_reasons, *coverage]
        if str(final.assessment.executionStatus) != "complete":
            reasons.append(
                "Selection or opportunity assessment is incomplete; review the retained findings."
            )
        elif not compiled.edit.videos:
            reasons.append(
                "Source review selected no standalone video; opportunity dispositions are retained."
            )
        return await self.render(
            request,
            evidence=evidence.artifact,
            edit=compiled.artifact,
            revision=1,
            reasons=tuple(dict.fromkeys(reasons)),
        )
