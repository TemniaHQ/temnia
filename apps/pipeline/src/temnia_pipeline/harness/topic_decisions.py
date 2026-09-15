"""One Temporal activity per editorial decision for `standalone-topics/8`.

A decision activity builds one bounded prompt from inline source windows, runs the short model
loop (one round for coverage decisions, a few tool rounds for author, review and repair), admits
the typed answer against what was actually delivered to the model, and returns one artifact
reference or one coverage gap. The ledger's reserve, dispatch and settle path runs inside every
model request exactly as before; the parent workflow only ever sees plans, references and gaps.

Typed stops (money, provider, unknown outcome, source, cancellation) leave the activity as
non-retryable application errors that the run workflow maps to a status. Everything else is
handled here: transient provider failures retry and fall back, invalid or ungrounded answers get
one correction carrying the exact diagnostic, and a decision that still cannot be admitted
becomes a coverage gap the assembly records and the reviewer sees.
"""

# Refusal text is part of the editorial boundary.
# ruff: noqa: C901, EM101, EM102, N815, PLR0911, PLR0912, PLR0913, PLR0915, SLF001, TRY003

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits
from temporalio import activity
from temporalio.exceptions import ApplicationError

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    TopicAuthorPackagingPlan,
    TopicAuthorPackagingShard,
    TopicOpportunityInventoryPlan,
    TopicOpportunityInventoryShard,
    TopicPortfolioReviewV4,
    TopicRepairPlan,
    TopicRepairShard,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSourceIndex,
    TopicSourceReviewPlan,
    TopicSourceReviewShard,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V8
from temnia_pipeline.harness.gateway import GatewayPolicyError
from temnia_pipeline.harness.models import (
    DECISION_AGENTS_V8,
    HarnessModelDeps,
    KnownProviderRejection,
    ModelPersistenceError,
    TransientProviderFailure,
)
from temnia_pipeline.harness.routes import ContextWindowExceeded, NoEligibleRoute, RouteEntry
from temnia_pipeline.harness.topic_author_packaging import (
    admit_author_shard,
    author_work_item,
    build_author_plan,
    inventory_for_work_item,
)
from temnia_pipeline.harness.topic_editorial import editorial_routes
from temnia_pipeline.harness.topic_inventory import (
    admit_inventory_shard,
    build_inventory_plan,
    inventory_section,
)
from temnia_pipeline.harness.topic_repair import (
    admit_repair_shard,
    build_repair_plan,
    repair_work_item,
)
from temnia_pipeline.harness.topic_selection import (
    assess_selection,
    selection_cold_key,
    selection_cold_prompt,
    selection_semantic_key,
)
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.topic_selection_runtime import (
    SelectionAssessmentResult,
    SelectionCallPlan,
    SelectionContext,
    selection_call_config,
    selection_call_inputs,
)
from temnia_pipeline.harness.topic_source_review import (
    admit_source_review_shard,
    build_source_review_plan,
    source_review_work_item,
)
from temnia_pipeline.harness.topic_windows import (
    AUTHOR_PROMPT_VERSION,
    COLD_PROMPT_VERSION,
    DECISION_RECORD_FORMAT,
    INVENTORY_PROMPT_VERSION,
    MAX_ROUNDS,
    REPAIR_PROMPT_VERSION,
    RESERVED_OUTPUT_TOKENS,
    REVIEW_PROMPT_VERSION,
    TOOLS_BY_KIND,
    CoverageGap,
    DecisionKind,
    InventoryManifestV8,
    WindowFitError,
    assemble_author_v8,
    assemble_inventory_v8,
    assemble_repair_v8,
    assemble_review_v8,
    author_window_prompt,
    inventory_window_prompt,
    project_run,
    projection_sentence,
    read_sentence_ids,
    repair_window_prompt,
    review_window_prompt,
    validate_claims,
)
from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from temnia_pipeline.harness.activities import HarnessActivities
    from temnia_pipeline.harness.runtime_types import RunSnapshot

SCHEMA_VERSIONS: dict[str, str] = {
    "inventory": "topic-selection-draft/1",
    "author": "topic-selection-draft/1",
    "cold": "topic-selection-cold-review/1",
    "review": "topic-selection-portfolio/4",
    "repair": "topic-selection-patch/3",
}
PROMPT_VERSIONS: dict[str, str] = {
    "inventory": INVENTORY_PROMPT_VERSION,
    "author": AUTHOR_PROMPT_VERSION,
    "cold": COLD_PROMPT_VERSION,
    "review": REVIEW_PROMPT_VERSION,
    "repair": REPAIR_PROMPT_VERSION,
}
SOURCE_ROLES: dict[str, str] = {"author": "author", "review": "reviewer", "repair": "repair"}
DECISION_REJECTION_FORMAT = "topic-decision-rejection/1"
DECISION_CLAIMS_FORMAT = "topic-decision-claims/1"
INVENTORY_PLAN_FORMAT = "topic-opportunity-inventory-plan/1"
AUTHOR_PLAN_FORMAT = "topic-author-packaging-plan/1"
REVIEW_PLAN_FORMAT = "topic-source-review-plan/1"
REPAIR_PLAN_FORMAT = "topic-repair-plan/1"
# Attempts and fallbacks. One correction after a rejected answer, two same-route retries on a
# transient failure, then the next route; three route positions per seat before the run stops.
MAX_ATTEMPTS = 2
SAME_ROUTE_ATTEMPTS = 2
MAX_ROUTE_POSITIONS = 3
TRANSIENT_BACKOFF_SECONDS = 20.0
HEARTBEAT_SECONDS = 15.0
TYPED_STOPS: tuple[type[Exception], ...] = (
    ledger.BudgetExceeded,
    ledger.DispatchLimitExceeded,
    ledger.OutcomeUnknown,
    ledger.IdentityConflict,
    ledger.LostOwnership,
    ledger.SourceDeleting,
)


class DecisionRequest(BaseModel):
    """One planned decision: the exact context and the item it owns."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    kind: DecisionKind
    item_id: str


class DecisionResult(BaseModel):
    """An admitted decision record or a coverage gap, plus the settled route positions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: DecisionKind
    item_id: str
    stage: str
    artifact: HarnessArtifactRef | None = None
    gap: CoverageGap | None = None
    family: str | None = None
    author_index: int = 0
    verifier_index: int = 0
    reused: bool = False


class DecisionClaimsV8(BaseModel):
    """What the model was given and what it read, so admission is auditable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-decision-claims/1"] = DECISION_CLAIMS_FORMAT
    stage: str
    inlineSentenceCount: int
    readSentenceIds: tuple[str, ...]
    requiredSentenceCount: int
    rounds: int


class DecisionRecordV8(BaseModel):
    """The admitted typed answer, its shard where one exists, and its provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-decision/1"] = DECISION_RECORD_FORMAT
    kind: DecisionKind
    itemId: str
    stage: str
    family: str
    routeId: str
    output: dict[str, Any]
    shard: dict[str, Any] | None = None
    response: HarnessArtifactRef
    claims: HarnessArtifactRef


class DecisionRejectionV8(BaseModel):
    """A settled, paid answer that admission refused; retained beside the corrected one."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-decision-rejection/1"] = DECISION_REJECTION_FORMAT
    kind: DecisionKind
    itemId: str
    stage: str
    family: str
    routeId: str
    output: dict[str, Any] | None
    diagnostics: tuple[str, ...]
    response: HarnessArtifactRef | None


class PlanResultV8(BaseModel):
    """The inventory plan and the pre-spend projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    inventory_plan: HarnessArtifactRef
    section_ids: tuple[str, ...]
    projection: HarnessArtifactRef
    projected_calls: int
    projected_cost_micros: int
    sentence: str


class AssembleRequestV8(BaseModel):
    """Every decision result of one stage, in plan order."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    results: tuple[DecisionResult, ...]


class InventoryAssemblyV8(BaseModel):
    """The assembled inventory reference and its recorded gaps."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    gaps: tuple[CoverageGap, ...]
    opportunity_count: int


class AuthorPlanResultV8(BaseModel):
    """The frozen author work items."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    work_item_ids: tuple[str, ...]


class AuthorAssemblyV8(BaseModel):
    """The accepted selection, or the reason no packaging was admitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    selection: HarnessArtifactRef | None = None
    manifest: HarnessArtifactRef
    draft: TopicSelectionDraft | None = None
    semantic_key: str | None = None
    families: tuple[str, ...] = ()
    gaps: tuple[CoverageGap, ...] = ()
    reason: str | None = None


class ReviewPlanResultV8(BaseModel):
    """The frozen bounded review work items."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    work_item_ids: tuple[str, ...]


class AssessmentRequestV8(BaseModel):
    """Every cold and review decision of one iteration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    cold: tuple[DecisionResult, ...]
    review: tuple[DecisionResult, ...]


class RepairPlanResultV8(BaseModel):
    """The frozen repair components, or the reason none could be planned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    work_item_ids: tuple[str, ...] = ()
    reason: str | None = None


class RepairAssemblyV8(BaseModel):
    """The repaired selection, or the reason the selection is unchanged."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    selection: HarnessArtifactRef | None = None
    manifest: HarnessArtifactRef | None = None
    draft: TopicSelectionDraft | None = None
    semantic_key: str | None = None
    families: tuple[str, ...] = ()
    gaps: tuple[CoverageGap, ...] = ()
    reason: str | None = None


class _Prepared(BaseModel):
    """Everything one model call needs, built once per attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    plan: SelectionCallPlan
    delivered: frozenset[str]
    output_type: Any
    base_stage: str
    synthetic: str


def _distinct(references: Sequence[HarnessArtifactRef]) -> tuple[HarnessArtifactRef, ...]:
    """Artifact references by identity, first occurrence wins; refs are not hashable."""
    seen: set[object] = set()
    ordered: list[HarnessArtifactRef] = []
    for reference in references:
        if reference.id not in seen:
            seen.add(reference.id)
            ordered.append(reference)
    return tuple(ordered)


def _sentence(error: BaseException) -> str:
    return str(error).strip() or type(error).__name__


class TopicDecisionActivities:
    """The `standalone-topics/8` activities: plan, decide, assemble."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner
        self.selection = TopicSelectionActivities(owner)
        self.topics = self.selection.topics

    # ------------------------------------------------------------------ shared helpers

    async def _read_typed(
        self,
        context: SelectionContext,
        ref: HarnessArtifactRef,
        model: type[Any],
        *,
        format_name: str,
        dependencies: Sequence[HarnessArtifactRef],
    ) -> Any:  # noqa: ANN401
        await self.selection.require_record(
            context, ref, format_name=format_name, dependencies=dependencies
        )
        return model.model_validate(await self.selection.read(context, ref))

    async def _inventory_plan(self, context: SelectionContext) -> TopicOpportunityInventoryPlan:
        if context.inventory_plan is None or context.source_index is None:
            raise HarnessValidationError("this decision requires the inventory plan")
        plan = await self._read_typed(
            context,
            context.inventory_plan,
            TopicOpportunityInventoryPlan,
            format_name=INVENTORY_PLAN_FORMAT,
            dependencies=(context.evidence, context.source_index),
        )
        return cast("TopicOpportunityInventoryPlan", plan)

    async def _inventory(self, context: SelectionContext) -> InventoryManifestV8:
        if (
            context.inventory is None
            or context.inventory_plan is None
            or context.source_index is None
        ):
            raise HarnessValidationError("this decision requires the assembled inventory")
        manifest = await self._read_typed(
            context,
            context.inventory,
            InventoryManifestV8,
            format_name=InventoryManifestV8.model_fields["format"].default,
            dependencies=(context.evidence, context.source_index, context.inventory_plan),
        )
        return cast("InventoryManifestV8", manifest)

    async def _author_plan(self, context: SelectionContext) -> TopicAuthorPackagingPlan:
        if context.author_plan is None or context.inventory is None:
            raise HarnessValidationError("this decision requires the author plan")
        plan = await self._read_typed(
            context,
            context.author_plan,
            TopicAuthorPackagingPlan,
            format_name=AUTHOR_PLAN_FORMAT,
            dependencies=(context.evidence, context.inventory),
        )
        return cast("TopicAuthorPackagingPlan", plan)

    async def _review_plan(self, context: SelectionContext) -> TopicSourceReviewPlan:
        if context.source_review_plan is None or context.selection is None:
            raise HarnessValidationError("this decision requires the review plan")
        plan = await self._read_typed(
            context,
            context.source_review_plan,
            TopicSourceReviewPlan,
            format_name=REVIEW_PLAN_FORMAT,
            dependencies=(context.evidence, context.selection),
        )
        return cast("TopicSourceReviewPlan", plan)

    async def _repair_plan(self, context: SelectionContext) -> TopicRepairPlan:
        if context.repair_plan is None or context.selection is None or context.assessment is None:
            raise HarnessValidationError("this decision requires the repair plan")
        plan = await self._read_typed(
            context,
            context.repair_plan,
            TopicRepairPlan,
            format_name=REPAIR_PLAN_FORMAT,
            dependencies=(context.evidence, context.selection, context.assessment),
        )
        return cast("TopicRepairPlan", plan)

    async def _assessment(self, context: SelectionContext) -> TopicSelectionAssessment:
        assessment = await self.selection.assessment(context)
        if assessment is None:
            raise HarnessValidationError("this decision requires the current assessment")
        return assessment

    def _routes(
        self, run: RunSnapshot, context: SelectionContext, *, shift: int, seat: str
    ) -> tuple[RouteEntry, RouteEntry]:
        snapshot = runs.apply_route_preferences(run.route_snapshot, run.route_preferences)
        return editorial_routes(
            snapshot,
            author_index=context.author_index + (shift if seat == "author" else 0),
            verifier_index=context.verifier_index + (shift if seat == "verifier" else 0),
            author_families=context.author_families,
            reserve_reviewer=True,
        )

    async def _existing_record(
        self, context: SelectionContext, base_stage: str
    ) -> HarnessArtifactRef | None:
        """An admitted record for this exact stage, from an earlier execution of the run."""
        async with db.scoped(
            self.owner.ctx.settings.database_url, self.topics.scope(self.selection.common(context))
        ) as conn:
            row = await (
                await conn.execute(
                    """SELECT id FROM harness_artifact
                        WHERE source_id=%s AND kind='checks'
                          AND metadata->>'format'=%s
                          AND metadata->>'runId'=%s
                          AND metadata->>'decisionStage'=%s
                        ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (
                        context.run.source_id,
                        DECISION_RECORD_FORMAT,
                        str(context.run.run_id),
                        base_stage,
                    ),
                )
            ).fetchone()
        if row is None:
            return None
        record = await artifacts._artifact_for_read(  # pyright: ignore[reportPrivateUsage]
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.selection.common(context)),
            source_id=context.run.source_id,
            artifact_id=row["id"],
        )
        return self.owner._artifact_ref(record)  # pyright: ignore[reportPrivateUsage]

    @staticmethod
    async def _pulse(label: str) -> None:
        while True:
            activity.heartbeat(label)
            await asyncio.sleep(HEARTBEAT_SECONDS)

    # ------------------------------------------------------------------ planning

    @activity.defn(name="prepare_topic_plan_v8")
    async def prepare_plan(self, context: SelectionContext) -> PlanResultV8:
        """Publish the section roster and the projected work before any paid call."""
        if context.program_version != TOPIC_SELECTION_POLICY_V8:
            raise HarnessValidationError("window planning requires standalone-topics/8")
        if context.source_index is None or context.rubric is None:
            raise HarnessValidationError("window planning requires the index and rubric")
        run, _, rubric, _ = await self.selection.load(context)
        if rubric is None:
            raise HarnessValidationError("window planning requires the frozen rubric")
        index = await self.selection.source_index(context)
        plan = build_inventory_plan(index, index_sha256=context.source_index.sha256)
        plan_ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=plan.format,
            content=plan,
            dependencies=(context.evidence, context.source_index),
            metadata={
                "programVersion": context.program_version,
                "sectionCount": len(plan.sections),
            },
        )
        author, verifier = self._routes(run, context, shift=0, seat="verifier")
        projection = project_run(
            index,
            rubric,
            plan,
            index_sha256=context.source_index.sha256,
            author_route=author,
            verifier_route=verifier,
            budget_micros=run.budget_micros,
            max_repairs=run.config.maxRepairs,
        )
        sentence = projection_sentence(projection)
        projection_ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=projection.format,
            content=projection,
            dependencies=(context.evidence, context.source_index, plan_ref),
            metadata={"programVersion": context.program_version},
        )
        await runs.record_run_projection(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.selection.common(context)),
            source_id=context.run.source_id,
            run_id=context.run.run_id,
            projection={
                **projection.model_dump(mode="json"),
                "sentence": sentence,
                "authorRouteId": author.id,
                "verifierRouteId": verifier.id,
            },
        )
        return PlanResultV8(
            inventory_plan=plan_ref,
            section_ids=tuple(section.sectionId for section in plan.sections),
            projection=projection_ref,
            projected_calls=projection.projectedCalls,
            projected_cost_micros=projection.projectedCostMicros,
            sentence=sentence,
        )

    @activity.defn(name="prepare_topic_author_plan_v8")
    async def prepare_author_plan(self, context: SelectionContext) -> AuthorPlanResultV8:
        """One author work item per section batch of at most twelve inventoried opportunities."""
        await self.selection.load(context)
        if context.source_index is None or context.inventory is None:
            raise HarnessValidationError("author planning requires the assembled inventory")
        inventory_plan = await self._inventory_plan(context)
        manifest = await self._inventory(context)
        plan = build_author_plan(
            inventory_plan,
            manifest.inventory,
            index_sha256=context.source_index.sha256,
            inventory_sha256=context.inventory.sha256,
        )
        ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=plan.format,
            content=plan,
            dependencies=(context.evidence, context.source_index, context.inventory),
            metadata={
                "programVersion": context.program_version,
                "workItemCount": len(plan.workItems),
            },
        )
        return AuthorPlanResultV8(
            artifact=ref, work_item_ids=tuple(item.workItemId for item in plan.workItems)
        )

    @activity.defn(name="prepare_topic_review_plan_v8")
    async def prepare_review_plan(self, context: SelectionContext) -> ReviewPlanResultV8:
        """Bounded local, omission, overlap and handoff review items over the accepted selection."""
        _, evidence, _, record = await self.selection.load(context)
        if record is None or context.selection is None or context.source_index is None:
            raise HarnessValidationError("review planning requires the accepted selection")
        index = await self.selection.source_index(context)
        plan = build_source_review_plan(
            evidence,
            index,
            record.draft,
            index_sha256=context.source_index.sha256,
            selection_sha256=context.selection.sha256,
            paged_context=True,
        )
        ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=plan.format,
            content=plan,
            dependencies=(context.evidence, context.source_index, context.selection),
            metadata={
                "programVersion": context.program_version,
                "iteration": context.iteration,
                "workItemCount": len(plan.workItems),
            },
        )
        return ReviewPlanResultV8(
            artifact=ref, work_item_ids=tuple(item.workItemId for item in plan.workItems)
        )

    @activity.defn(name="prepare_topic_repair_plan_v8")
    async def prepare_repair_plan(self, context: SelectionContext) -> RepairPlanResultV8:
        """Connected required-finding components; an oversized component is a reason, not a stop."""
        _, evidence, _, record = await self.selection.load(context)
        if (
            record is None
            or context.selection is None
            or context.assessment is None
            or context.source_index is None
        ):
            raise HarnessValidationError("repair planning requires the assessed selection")
        assessment = await self._assessment(context)
        index = await self.selection.source_index(context)
        try:
            plan = build_repair_plan(
                evidence,
                index,
                record,
                assessment,
                index_sha256=context.source_index.sha256,
                selection_sha256=context.selection.sha256,
                assessment_sha256=context.assessment.sha256,
            )
        except HarnessValidationError as error:
            return RepairPlanResultV8(reason=f"Repair planning refused: {error}")
        ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=plan.format,
            content=plan,
            dependencies=(
                context.evidence,
                context.source_index,
                context.selection,
                context.assessment,
            ),
            metadata={
                "programVersion": context.program_version,
                "iteration": context.iteration,
                "workItemCount": len(plan.workItems),
            },
        )
        return RepairPlanResultV8(
            artifact=ref, work_item_ids=tuple(item.workItemId for item in plan.workItems)
        )

    # ------------------------------------------------------------------ the decision

    async def _prepare(
        self,
        request: DecisionRequest,
        *,
        run: RunSnapshot,
        evidence: HarnessEvidence,
        record: TopicSelectionRecord | None,
        index: TopicSourceIndex | None,
        author: RouteEntry,
        verifier: RouteEntry,
        attempt: int,
        feedback: tuple[str, ...],
    ) -> _Prepared:
        context = request.context
        kind = request.kind
        rubric = record.rubric if record is not None else None
        if rubric is None:
            if context.rubric is None:
                raise HarnessValidationError("every decision requires the frozen rubric")
            _, _, loaded, _ = await self.selection.load(context)
            rubric = loaded
        if rubric is None or context.rubric is None:
            raise HarnessValidationError("every decision requires the frozen rubric")
        index_ref = context.source_index
        index_sha = index_ref.sha256 if index_ref is not None else ""
        inputs: list[HarnessArtifactRef] = [context.evidence, context.rubric]
        output_type: Any
        if kind == "inventory":
            if index is None or index_ref is None or context.inventory_plan is None:
                raise HarnessValidationError("inventory decisions require the index and plan")
            plan = await self._inventory_plan(context)
            section = inventory_section(plan, request.item_id)
            prompt, delivered = inventory_window_prompt(
                index, rubric, section, index_sha256=index_sha
            )
            inputs.extend((index_ref, context.inventory_plan))
            base_stage = f"verify:selection:inventory:{section.sectionId}"
            output_type = TopicSelectionDraft
            synthetic = f"topic_v8_inventory_{section.sectionId}"
        elif kind == "author":
            if (
                index is None
                or index_ref is None
                or context.inventory_plan is None
                or context.inventory is None
                or context.author_plan is None
            ):
                raise HarnessValidationError("author decisions require inventory and plan")
            plan = await self._author_plan(context)
            work_item = author_work_item(plan, request.item_id)
            manifest = await self._inventory(context)
            assignment = inventory_for_work_item(manifest.inventory, work_item)
            prompt, delivered = author_window_prompt(
                index, rubric, work_item, assignment, index_sha256=index_sha
            )
            inputs.extend(
                (index_ref, context.inventory_plan, context.inventory, context.author_plan)
            )
            base_stage = f"proposal:selection:author:{work_item.workItemId}"
            output_type = TopicSelectionDraft
            synthetic = f"topic_v8_author_{work_item.workItemId}"
        elif kind == "cold":
            if record is None or context.selection is None:
                raise HarnessValidationError("cold decisions require the accepted selection")
            candidate = next(
                (item for item in record.draft.proposal.candidates if item.id == request.item_id),
                None,
            )
            if candidate is None:
                raise HarnessValidationError("cold review candidate is absent from the selection")
            prompt = selection_cold_prompt(evidence, candidate, rubric, indexed=False)
            positions = {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}
            first = positions[candidate.firstSentenceId]
            last = positions[candidate.lastSentenceId]
            delivered = {sentence.id for sentence in evidence.sentences[first : last + 1]}
            inputs.append(context.selection)
            base_stage = (
                f"verify:selection:cold:{selection_cold_key(candidate, context.rubric.sha256)}"
            )
            output_type = TopicSelectionColdReview
            synthetic = f"topic_v8_cold_{candidate.id}"
        elif kind == "review":
            if (
                record is None
                or index is None
                or index_ref is None
                or context.selection is None
                or context.source_review_plan is None
            ):
                raise HarnessValidationError("review decisions require selection, index and plan")
            plan = await self._review_plan(context)
            work_item = source_review_work_item(plan, request.item_id)
            prompt, delivered = review_window_prompt(
                index, rubric, record.draft, work_item, index_sha256=index_sha
            )
            inputs.extend((index_ref, context.selection, context.source_review_plan))
            base_stage = f"verify:selection:source:{context.iteration}:{work_item.workItemId}"
            output_type = TopicPortfolioReviewV4
            synthetic = f"topic_v8_review_{work_item.workItemId}_{context.iteration}"
        elif kind == "repair":
            if (
                record is None
                or index is None
                or index_ref is None
                or context.selection is None
                or context.assessment is None
                or context.repair_plan is None
            ):
                raise HarnessValidationError("repair decisions require the assessed selection")
            plan = await self._repair_plan(context)
            assessment = await self._assessment(context)
            work_item = repair_work_item(plan, request.item_id)
            prompt, delivered = repair_window_prompt(
                index,
                evidence,
                record,
                assessment,
                plan=plan,
                work_item=work_item,
                index_sha256=index_sha,
            )
            inputs.extend((index_ref, context.selection, context.assessment, context.repair_plan))
            base_stage = f"repair:selection:{context.iteration}:{work_item.workItemId}"
            output_type = TopicSelectionPatchV3
            synthetic = f"topic_v8_repair_{work_item.workItemId}_{context.iteration}"
        else:  # pragma: no cover - the Literal bounds this
            raise HarnessValidationError(f"unknown decision kind {kind}")
        stage = base_stage + (f":retry-{attempt}" if attempt else "")
        if feedback:
            prompt = (
                "RECOVERY: a previous settled answer to this exact assignment was not admitted. "
                "Correct these specific problems and return the complete typed answer; do not "
                "repeat an unsupported answer.\n" + "\n".join(feedback)[:8000] + "\n\n" + prompt
            )
        tools = TOOLS_BY_KIND[kind]
        role = SOURCE_ROLES.get(kind)
        synthetic_payload = self.owner._recorded_output(synthetic)  # pyright: ignore[reportPrivateUsage]
        plan_value = SelectionCallPlan(
            prompt=prompt,
            stage=stage,
            prompt_version=PROMPT_VERSIONS[kind],
            program_version="standalone-topics/8",
            schema_version=SCHEMA_VERSIONS[kind],
            author=author,
            verifier=verifier,
            input_artifacts=_distinct(inputs),
            source_index=index_ref if tools else None,
            source_tool_role=cast("Any", role) if tools else None,
            synthetic_payload=synthetic_payload,
        )
        _ = run
        return _Prepared(
            plan=plan_value,
            delivered=frozenset(delivered),
            output_type=output_type,
            base_stage=base_stage,
            synthetic=synthetic,
        )

    async def _admit(
        self,
        request: DecisionRequest,
        prepared: _Prepared,
        *,
        run: RunSnapshot,
        evidence: HarnessEvidence,
        record: TopicSelectionRecord | None,
        index: TopicSourceIndex | None,
        output: BaseModel,
        messages: Sequence[Any],
        family: str,
        route_id: str,
    ) -> HarnessArtifactRef:
        """Check the answer's claims and ownership, then retain it as one decision record."""
        context = request.context
        kind = request.kind
        plan = prepared.plan
        index_sha = context.source_index.sha256 if context.source_index is not None else ""
        read_ids: set[str] = (
            read_sentence_ids(messages, index_sha256=index_sha) if index_sha else set()
        )
        delivered = set(prepared.delivered) | read_ids
        validate_claims(evidence, delivered=delivered, outputs=[output])
        response = await self.selection.response_ref(context, plan, output)
        claims = DecisionClaimsV8(
            stage=plan.stage,
            inlineSentenceCount=len(prepared.delivered),
            readSentenceIds=tuple(sorted(read_ids)),
            requiredSentenceCount=len(delivered),
            rounds=sum(1 for message in messages if type(message).__name__ == "ModelResponse"),
        )
        claims_ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=claims.format,
            content=claims,
            dependencies=(*plan.input_artifacts, response),
            metadata={"programVersion": context.program_version, "stage": plan.stage},
        )
        shard: BaseModel | None = None
        if kind == "inventory":
            inventory_plan = await self._inventory_plan(context)
            assert context.inventory_plan is not None  # noqa: S101 - checked in _prepare
            shard = admit_inventory_shard(
                evidence,
                inventory_plan,
                plan_sha256=context.inventory_plan.sha256,
                section_id=request.item_id,
                inventory=cast("TopicSelectionDraft", output),
            )
        elif kind == "author":
            author_plan = await self._author_plan(context)
            manifest = await self._inventory(context)
            assert context.author_plan is not None  # noqa: S101
            shard = admit_author_shard(
                evidence,
                manifest.inventory,
                author_plan,
                plan_sha256=context.author_plan.sha256,
                work_item_id=request.item_id,
                draft=cast("TopicSelectionDraft", output),
                generator_family=family,
            )
        elif kind == "cold":
            assert record is not None  # noqa: S101
            assert context.selection is not None  # noqa: S101
            review = cast("TopicSelectionColdReview", output)
            if review.candidateId != request.item_id:
                raise HarnessValidationError("cold review names the wrong candidate")
            if family in context.author_families:
                raise HarnessValidationError("cold reviewer participated in authoring")
            assessed = assess_selection(
                evidence,
                record,
                context.selection.sha256,
                cold_reviews=[review],
                source_review=None,
                author_family="+".join(context.author_families) or plan.author.family,
                verifier_family=family,
            )
            if len(assessed.coldReviews) != 1:
                raise HarnessValidationError(
                    "cold review is not grounded: " + "; ".join(assessed.reasons)[:1200]
                )
        elif kind == "review":
            assert record is not None  # noqa: S101
            assert index is not None  # noqa: S101
            assert context.source_review_plan is not None  # noqa: S101
            if family in context.author_families:
                raise HarnessValidationError("source reviewer participated in authoring")
            review_plan = await self._review_plan(context)
            shard = admit_source_review_shard(
                evidence,
                index,
                record.draft,
                review_plan,
                plan_sha256=context.source_review_plan.sha256,
                work_item_id=request.item_id,
                review=cast("TopicPortfolioReviewV4", output),
                reviewer_family=family,
                response_artifact=response,
                inspection_artifact=claims_ref,
            )
        elif kind == "repair":
            assert record is not None  # noqa: S101
            assert context.assessment is not None  # noqa: S101
            assert context.source_index is not None  # noqa: S101
            repair_plan = await self._repair_plan(context)
            assessment = await self._assessment(context)
            work_item = repair_work_item(repair_plan, request.item_id)
            shard = admit_repair_shard(
                evidence,
                record,
                assessment,
                repair_plan,
                work_item,
                cast("TopicSelectionPatchV3", output),
                index_sha256=context.source_index.sha256,
                assessment_sha256=context.assessment.sha256,
                response_artifact=response,
                inspection_artifact=claims_ref,
                author_family=family,
            )
        decision = DecisionRecordV8(
            kind=kind,
            itemId=request.item_id,
            stage=plan.stage,
            family=family,
            routeId=route_id,
            output=output.model_dump(mode="json"),
            shard=shard.model_dump(mode="json") if shard is not None else None,
            response=response,
            claims=claims_ref,
        )
        _ = run
        return await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=decision.format,
            content=decision,
            dependencies=(*plan.input_artifacts, response, claims_ref),
            metadata={
                "programVersion": context.program_version,
                "decisionKind": kind,
                "decisionItem": request.item_id,
                "decisionStage": prepared.base_stage,
                "stage": plan.stage,
                "family": family,
            },
        )

    async def _reject(
        self,
        request: DecisionRequest,
        prepared: _Prepared,
        *,
        output: BaseModel | None,
        diagnostics: tuple[str, ...],
        family: str,
        route_id: str,
    ) -> HarnessArtifactRef:
        context = request.context
        response: HarnessArtifactRef | None = None
        with contextlib.suppress(HarnessValidationError, ValueError):
            response = await self.selection.response_ref(context, prepared.plan, output)
        rejection = DecisionRejectionV8(
            kind=request.kind,
            itemId=request.item_id,
            stage=prepared.plan.stage,
            family=family,
            routeId=route_id,
            output=output.model_dump(mode="json") if output is not None else None,
            diagnostics=diagnostics,
            response=response,
        )
        return await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=rejection.format,
            content=rejection,
            dependencies=(
                *prepared.plan.input_artifacts,
                *((response,) if response is not None else ()),
            ),
            metadata={
                "programVersion": context.program_version,
                "decisionKind": request.kind,
                "decisionItem": request.item_id,
                "stage": prepared.plan.stage,
            },
        )

    @activity.defn(name="run_topic_decision_v8")
    async def run_decision(self, request: DecisionRequest) -> DecisionResult:
        """Decide one bounded item: prepare, call, admit; correct once; fall back; never loop."""
        context = request.context
        kind = request.kind
        if context.program_version != TOPIC_SELECTION_POLICY_V8:
            raise HarnessValidationError("window decisions require standalone-topics/8")
        run, evidence, _, record = await self.selection.load(context)
        index = (
            await self.selection.source_index(context) if context.source_index is not None else None
        )
        seat = "author" if kind in {"author", "repair"} else "verifier"
        pulse = (
            asyncio.create_task(self._pulse(f"{kind}:{request.item_id}"))
            if activity.in_activity()
            else None
        )
        try:
            attempt = 0
            feedback: tuple[str, ...] = ()
            shift = 0
            same_route = 0
            retained: list[HarnessArtifactRef] = []
            base_stage = ""
            while True:
                try:
                    author, verifier = self._routes(run, context, shift=shift, seat=seat)
                except NoEligibleRoute as error:
                    if shift == 0:
                        raise ApplicationError(
                            _sentence(error), type="NoEligibleRoute", non_retryable=True
                        ) from error
                    raise ApplicationError(
                        f"Every eligible {seat} route failed for {kind} {request.item_id}: "
                        + _sentence(error),
                        type="DecisionRoutesExhausted",
                        non_retryable=True,
                    ) from error
                route = verifier if seat == "verifier" else author
                try:
                    prepared = await self._prepare(
                        request,
                        run=run,
                        evidence=evidence,
                        record=record,
                        index=index,
                        author=author,
                        verifier=verifier,
                        attempt=attempt,
                        feedback=feedback,
                    )
                except WindowFitError as error:
                    return self._gap(
                        request,
                        stage="",
                        reason=_sentence(error),
                        retained=retained,
                        context=context,
                        shift=shift,
                        seat=seat,
                        family=None,
                    )
                base_stage = prepared.base_stage
                if attempt == 0 and shift == 0:
                    existing = await self._existing_record(context, base_stage)
                    if existing is not None:
                        return DecisionResult(
                            kind=kind,
                            item_id=request.item_id,
                            stage=base_stage,
                            artifact=existing,
                            family=route.family,
                            author_index=context.author_index,
                            verifier_index=context.verifier_index,
                            reused=True,
                        )
                deps = self._build_deps(context, run, prepared.plan, kind)
                reserved = min(
                    RESERVED_OUTPUT_TOKENS[kind],
                    route.max_output_tokens,
                    run.config.maxOutputTokens,
                )
                agent = DECISION_AGENTS_V8[kind]
                try:
                    result = await agent.run(
                        prepared.plan.prompt,
                        deps=deps,
                        model_settings={"max_tokens": reserved},
                        usage_limits=UsageLimits(request_limit=MAX_ROUNDS[kind]),
                    )
                except TransientProviderFailure as error:
                    same_route += 1
                    if same_route >= SAME_ROUTE_ATTEMPTS:
                        same_route = 0
                        shift += 1
                        if shift >= MAX_ROUTE_POSITIONS:
                            raise ApplicationError(
                                f"Every eligible {seat} route failed transiently for {kind} "
                                f"{request.item_id}; last: {_sentence(error)}",
                                type="DecisionRoutesExhausted",
                                non_retryable=True,
                            ) from error
                        continue
                    await asyncio.sleep(TRANSIENT_BACKOFF_SECONDS)
                    continue
                except (KnownProviderRejection, ContextWindowExceeded, GatewayPolicyError) as error:
                    shift += 1
                    same_route = 0
                    if shift >= MAX_ROUTE_POSITIONS:
                        return self._gap(
                            request,
                            stage=prepared.plan.stage,
                            reason=f"No eligible route accepted this request: {_sentence(error)}",
                            retained=retained,
                            context=context,
                            shift=shift,
                            seat=seat,
                            family=route.family,
                        )
                    continue
                except UsageLimitExceeded as error:
                    rounds = MAX_ROUNDS[kind]
                    return self._gap(
                        request,
                        stage=prepared.plan.stage,
                        reason=(
                            f"The decision did not finish within {rounds} model rounds: "
                            + _sentence(error)
                        ),
                        retained=retained,
                        context=context,
                        shift=shift,
                        seat=seat,
                        family=route.family,
                    )
                except UnexpectedModelBehavior as error:
                    diagnostic = f"The answer did not match the required schema: {_sentence(error)}"
                    retained.append(
                        await self._reject(
                            request,
                            prepared,
                            output=None,
                            diagnostics=(diagnostic,),
                            family=route.family,
                            route_id=route.id,
                        )
                    )
                    attempt += 1
                    feedback = (diagnostic,)
                    if attempt >= MAX_ATTEMPTS:
                        return self._gap(
                            request,
                            stage=prepared.plan.stage,
                            reason=diagnostic,
                            retained=retained,
                            context=context,
                            shift=shift,
                            seat=seat,
                            family=route.family,
                        )
                    continue
                except TYPED_STOPS as error:
                    raise ApplicationError(
                        _sentence(error), type=type(error).__name__, non_retryable=True
                    ) from error
                except ModelPersistenceError as error:
                    raise ApplicationError(
                        _sentence(error), type="ModelPersistenceError", non_retryable=True
                    ) from error
                output = cast("BaseModel", result.output)
                try:
                    artifact = await self._admit(
                        request,
                        prepared,
                        run=run,
                        evidence=evidence,
                        record=record,
                        index=index,
                        output=output,
                        messages=result.all_messages(),
                        family=route.family,
                        route_id=route.id,
                    )
                except (HarnessValidationError, ValidationError, ValueError) as error:
                    diagnostic = _sentence(error)
                    retained.append(
                        await self._reject(
                            request,
                            prepared,
                            output=output,
                            diagnostics=(diagnostic,),
                            family=route.family,
                            route_id=route.id,
                        )
                    )
                    attempt += 1
                    feedback = (diagnostic,)
                    if attempt >= MAX_ATTEMPTS:
                        return self._gap(
                            request,
                            stage=prepared.plan.stage,
                            reason=diagnostic,
                            retained=retained,
                            context=context,
                            shift=shift,
                            seat=seat,
                            family=route.family,
                        )
                    continue
                return DecisionResult(
                    kind=kind,
                    item_id=request.item_id,
                    stage=prepared.plan.stage,
                    artifact=artifact,
                    family=route.family,
                    author_index=context.author_index + (shift if seat == "author" else 0),
                    verifier_index=context.verifier_index + (shift if seat == "verifier" else 0),
                )
        finally:
            if pulse is not None:
                pulse.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pulse

    def _build_deps(
        self, context: SelectionContext, run: RunSnapshot, plan: SelectionCallPlan, kind: str
    ) -> HarnessModelDeps:
        route = plan.verifier if plan.stage.startswith("verify:") else plan.author
        tools = TOOLS_BY_KIND[kind]
        return HarnessModelDeps(
            scope=self.topics.scope(self.selection.common(context)),
            source_id=run.source_id,
            run_id=run.id,
            stage=plan.stage,
            program_version=plan.program_version,
            prompt_version=plan.prompt_version,
            schema_version=plan.schema_version,
            route=route,
            operation_inputs=selection_call_inputs(plan),
            operation_config=selection_call_config(plan, run.config.maxOutputTokens),
            input_artifact_ids=tuple(ref.id for ref in plan.input_artifacts),
            source_index=plan.source_index,
            source_tool_role=plan.source_tool_role,
            dispatch_limit=run.config.maxDispatches,
            synthetic_payload=plan.synthetic_payload,
            source_tools=tuple(tools),
            checkpointed=False,
        )

    @staticmethod
    def _gap(
        request: DecisionRequest,
        *,
        stage: str,
        reason: str,
        retained: Sequence[HarnessArtifactRef],
        context: SelectionContext,
        shift: int,
        seat: str,
        family: str | None,
    ) -> DecisionResult:
        return DecisionResult(
            kind=request.kind,
            item_id=request.item_id,
            stage=stage,
            gap=CoverageGap(
                kind=request.kind,
                itemId=request.item_id,
                reason=reason[:1200],
                stage=stage,
                retainedArtifacts=tuple(retained),
            ),
            family=family,
            author_index=context.author_index + (shift if seat == "author" else 0),
            verifier_index=context.verifier_index + (shift if seat == "verifier" else 0),
        )

    # ------------------------------------------------------------------ assembly

    async def _decision(
        self, context: SelectionContext, result: DecisionResult
    ) -> DecisionRecordV8 | None:
        if result.artifact is None:
            return None
        record = await self._read_typed(
            context,
            result.artifact,
            DecisionRecordV8,
            format_name=DECISION_RECORD_FORMAT,
            dependencies=(context.evidence,),
        )
        decision = cast("DecisionRecordV8", record)
        if decision.kind != result.kind or decision.itemId != result.item_id:
            raise HarnessValidationError("decision record belongs to another assignment")
        return decision

    @staticmethod
    def _gaps(results: Sequence[DecisionResult]) -> tuple[CoverageGap, ...]:
        return tuple(result.gap for result in results if result.gap is not None)

    @activity.defn(name="assemble_topic_inventory_v8")
    async def assemble_inventory(self, request: AssembleRequestV8) -> InventoryAssemblyV8:
        """The whole-source inventory from admitted sections; gaps are recorded, not fatal."""
        context = request.context
        _, evidence, _, _ = await self.selection.load(context)
        if context.source_index is None or context.inventory_plan is None:
            raise HarnessValidationError("inventory assembly requires the plan")
        plan = await self._inventory_plan(context)
        shards: dict[str, tuple[TopicSelectionDraft, HarnessArtifactRef]] = {}
        for result in request.results:
            decision = await self._decision(context, result)
            if decision is None or decision.shard is None or result.artifact is None:
                continue
            shard = TopicOpportunityInventoryShard.model_validate(decision.shard)
            admitted = admit_inventory_shard(
                evidence,
                plan,
                plan_sha256=context.inventory_plan.sha256,
                section_id=result.item_id,
                inventory=shard.inventory,
            )
            if admitted != shard:
                raise HarnessValidationError("inventory shard content changed after admission")
            shards[result.item_id] = (shard.inventory, result.artifact)
        gaps = self._gaps(request.results)
        manifest = assemble_inventory_v8(
            evidence,
            plan,
            index_sha256=context.source_index.sha256,
            plan_sha256=context.inventory_plan.sha256,
            shards=shards,
            gaps=gaps,
        )
        ref = await self.topics.publish(
            self.selection.common(context),
            kind="proposal",
            format_name=manifest.format,
            content=manifest,
            dependencies=(
                context.evidence,
                context.rubric,
                context.source_index,
                context.inventory_plan,
                *manifest.shardArtifacts,
            )
            if context.rubric is not None
            else (context.evidence, context.source_index, context.inventory_plan),
            metadata={
                "programVersion": context.program_version,
                "sectionCount": len(plan.sections),
                "gapCount": len(gaps),
                "opportunityCount": len(manifest.inventory.opportunities),
            },
        )
        return InventoryAssemblyV8(
            artifact=ref, gaps=gaps, opportunity_count=len(manifest.inventory.opportunities)
        )

    async def _publish_selection(
        self,
        context: SelectionContext,
        draft: TopicSelectionDraft,
        *,
        rubric: Any,  # noqa: ANN401
        dependencies: Sequence[HarnessArtifactRef],
        families: Sequence[str],
    ) -> HarnessArtifactRef:
        if context.rubric is None:
            raise HarnessValidationError("a selection requires the frozen rubric")
        record = TopicSelectionRecord.model_validate(
            {
                "draft": draft.model_dump(mode="json"),
                "evidenceSha256": context.evidence.sha256,
                "format": "topic-selection/2",
                "origin": "model",
                "parentSelectionSha256": context.selection.sha256 if context.selection else None,
                "rubric": rubric.model_dump(mode="json"),
                "rubricSha256": context.rubric.sha256,
                "runId": str(context.run.run_id),
            }
        )
        return await self.topics.publish(
            self.selection.common(context),
            kind="proposal",
            format_name=record.format,
            content=record,
            dependencies=_distinct((context.evidence, context.rubric, *dependencies)),
            metadata={
                "programVersion": context.program_version,
                "generatorFamily": "+".join(families) or "deterministic-empty-packaging",
            },
        )

    @activity.defn(name="assemble_topic_author_v8")
    async def assemble_author(self, request: AssembleRequestV8) -> AuthorAssemblyV8:
        """The accepted selection from admitted author items; gap items stay unpackaged."""
        context = request.context
        _, evidence, rubric, _ = await self.selection.load(context)
        if (
            rubric is None
            or context.source_index is None
            or context.inventory is None
            or context.author_plan is None
            or context.selection is not None
        ):
            raise HarnessValidationError("author assembly requires the fresh plan state")
        plan = await self._author_plan(context)
        manifest = await self._inventory(context)
        shards: dict[str, tuple[TopicSelectionDraft, str, HarnessArtifactRef]] = {}
        for result in request.results:
            decision = await self._decision(context, result)
            if decision is None or decision.shard is None or result.artifact is None:
                continue
            shard = TopicAuthorPackagingShard.model_validate(decision.shard)
            admitted = admit_author_shard(
                evidence,
                manifest.inventory,
                plan,
                plan_sha256=context.author_plan.sha256,
                work_item_id=result.item_id,
                draft=shard.draft,
                generator_family=shard.generatorFamily,
            )
            if admitted != shard:
                raise HarnessValidationError("author shard content changed after admission")
            shards[result.item_id] = (shard.draft, shard.generatorFamily, result.artifact)
        gaps = self._gaps(request.results)
        assembled = assemble_author_v8(
            evidence,
            manifest.inventory,
            plan,
            index_sha256=context.source_index.sha256,
            inventory_sha256=context.inventory.sha256,
            plan_sha256=context.author_plan.sha256,
            shards=shards,
            gaps=gaps,
        )
        manifest_ref = await self.topics.publish(
            self.selection.common(context),
            kind="proposal",
            format_name=assembled.format,
            content=assembled,
            dependencies=(
                context.evidence,
                context.source_index,
                context.inventory,
                context.author_plan,
                *assembled.shardArtifacts,
            ),
            metadata={
                "programVersion": context.program_version,
                "gapCount": len(gaps),
                "candidateCount": len(assembled.selection.proposal.candidates),
            },
        )
        if plan.workItems and not shards:
            return AuthorAssemblyV8(
                manifest=manifest_ref,
                gaps=gaps,
                reason=(
                    "No author work item produced an admitted packaging; the inventory and every "
                    "retained answer are kept for review. " + "; ".join(gap.reason for gap in gaps)
                )[:1200],
            )
        selection_ref = await self._publish_selection(
            context,
            assembled.selection,
            rubric=rubric,
            dependencies=(
                context.source_index,
                context.inventory,
                context.author_plan,
                manifest_ref,
            ),
            families=assembled.generatorFamilies,
        )
        return AuthorAssemblyV8(
            selection=selection_ref,
            manifest=manifest_ref,
            draft=assembled.selection,
            semantic_key=selection_semantic_key(assembled.selection),
            families=assembled.generatorFamilies,
            gaps=gaps,
        )

    @activity.defn(name="assemble_topic_assessment_v8")
    async def assemble_assessment(self, request: AssessmentRequestV8) -> SelectionAssessmentResult:
        """Ground every admitted cold and review decision into one assessment."""
        context = request.context
        _, evidence, _, record = await self.selection.load(context)
        if (
            record is None
            or context.selection is None
            or context.rubric is None
            or context.source_index is None
            or context.source_review_plan is None
        ):
            raise HarnessValidationError("assessment requires the reviewed selection")
        index = await self.selection.source_index(context)
        review_plan = await self._review_plan(context)
        reasons: list[str] = []
        responses: list[HarnessArtifactRef] = []
        cold_reviews: list[TopicSelectionColdReview] = []
        reviewer_families: list[str] = []
        for result in request.cold:
            decision = await self._decision(context, result)
            if decision is None:
                if result.gap is not None:
                    reasons.append(
                        f"Cold review of {result.item_id} is unavailable: {result.gap.reason}"
                    )
                continue
            cold_reviews.append(TopicSelectionColdReview.model_validate(decision.output))
            responses.append(decision.response)
            if decision.family not in reviewer_families:
                reviewer_families.append(decision.family)
        shards: dict[
            str, tuple[TopicPortfolioReviewV4, str, HarnessArtifactRef, HarnessArtifactRef]
        ] = {}
        for result in request.review:
            decision = await self._decision(context, result)
            if decision is None or decision.shard is None or result.artifact is None:
                continue
            shard = TopicSourceReviewShard.model_validate(decision.shard)
            admitted = admit_source_review_shard(
                evidence,
                index,
                record.draft,
                review_plan,
                plan_sha256=context.source_review_plan.sha256,
                work_item_id=result.item_id,
                review=shard.review,
                reviewer_family=shard.reviewerFamily,
                response_artifact=shard.responseArtifact,
                inspection_artifact=shard.inspectionArtifact,
            )
            if admitted != shard:
                raise HarnessValidationError("review shard content changed after admission")
            shards[result.item_id] = (
                shard.review,
                shard.reviewerFamily,
                result.artifact,
                shard.responseArtifact,
            )
            if shard.reviewerFamily not in reviewer_families:
                reviewer_families.append(shard.reviewerFamily)
        review_gaps = self._gaps(request.review)
        manifest = assemble_review_v8(
            evidence,
            record.draft,
            review_plan,
            index_sha256=context.source_index.sha256,
            selection_sha256=context.selection.sha256,
            plan_sha256=context.source_review_plan.sha256,
            shards=shards,
            gaps=review_gaps,
        )
        manifest_ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=manifest.format,
            content=manifest,
            dependencies=(
                context.evidence,
                context.rubric,
                context.source_index,
                context.selection,
                context.source_review_plan,
                *manifest.shardArtifacts,
            ),
            metadata={
                "programVersion": context.program_version,
                "iteration": context.iteration,
                "gapCount": len(review_gaps),
            },
        )
        responses.extend(manifest.responseArtifacts)
        reasons.extend(
            f"Source review of {gap.itemId} is unavailable: {gap.reason}" for gap in review_gaps
        )
        proposer_families = context.author_families or ("deterministic-empty-packaging",)
        if set(reviewer_families) & set(proposer_families):
            raise HarnessValidationError("an assessment reviewer participated in authoring")
        assessment = assess_selection(
            evidence,
            record,
            context.selection.sha256,
            cold_reviews=cold_reviews,
            source_review=manifest.review,
            author_family="+".join(proposer_families),
            verifier_family="+".join(reviewer_families) or None,
            response_artifacts=tuple(responses),
            reasons=reasons,
        )
        ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=assessment.format,
            content=assessment,
            dependencies=(
                context.evidence,
                context.rubric,
                context.selection,
                context.source_review_plan,
                manifest_ref,
                *responses,
            ),
            metadata={"selectionSha256": context.selection.sha256, "iteration": context.iteration},
        )
        return SelectionAssessmentResult(
            artifact=ref,
            assessment=assessment,
            actionable=any(str(item.severity) == "required" for item in assessment.findings),
        )

    @activity.defn(name="assemble_topic_repair_v8")
    async def assemble_repair(self, request: AssembleRequestV8) -> RepairAssemblyV8:
        """Apply every admitted, disjoint component as one revision; gaps change nothing."""
        context = request.context
        _, evidence, rubric, record = await self.selection.load(context)
        if (
            record is None
            or rubric is None
            or context.rubric is None
            or context.selection is None
            or context.assessment is None
            or context.repair_plan is None
            or context.source_index is None
        ):
            raise HarnessValidationError("repair assembly requires the assessed selection")
        plan = await self._repair_plan(context)
        assessment = await self._assessment(context)
        shards: dict[
            str, tuple[TopicSelectionPatchV3, str, HarnessArtifactRef, HarnessArtifactRef]
        ] = {}
        for result in request.results:
            decision = await self._decision(context, result)
            if decision is None or decision.shard is None or result.artifact is None:
                continue
            shard = TopicRepairShard.model_validate(decision.shard)
            work_item = repair_work_item(plan, result.item_id)
            admitted = admit_repair_shard(
                evidence,
                record,
                assessment,
                plan,
                work_item,
                shard.patch,
                index_sha256=context.source_index.sha256,
                assessment_sha256=context.assessment.sha256,
                response_artifact=shard.responseArtifact,
                inspection_artifact=shard.inspectionArtifact,
                author_family=shard.authorFamily,
            )
            if admitted != shard:
                raise HarnessValidationError("repair shard content changed after admission")
            shards[result.item_id] = (
                shard.patch,
                shard.authorFamily,
                result.artifact,
                shard.responseArtifact,
            )
        gaps = self._gaps(request.results)
        try:
            manifest = assemble_repair_v8(
                evidence, record, assessment, plan, shards=shards, gaps=gaps
            )
        except HarnessValidationError as error:
            return RepairAssemblyV8(
                gaps=gaps,
                reason=(f"Repair changed nothing: {error}. " + "; ".join(g.reason for g in gaps))[
                    :1200
                ],
            )
        manifest_ref = await self.topics.publish(
            self.selection.common(context),
            kind="checks",
            format_name=manifest.format,
            content=manifest,
            dependencies=(
                context.evidence,
                context.rubric,
                context.source_index,
                context.selection,
                context.assessment,
                context.repair_plan,
                *manifest.shardArtifacts,
            ),
            metadata={
                "programVersion": context.program_version,
                "iteration": context.iteration,
                "gapCount": len(gaps),
            },
        )
        selection_ref = await self._publish_selection(
            context,
            manifest.draft,
            rubric=rubric,
            dependencies=(
                context.source_index,
                context.selection,
                context.assessment,
                context.repair_plan,
                manifest_ref,
            ),
            families=manifest.authorFamilies,
        )
        return RepairAssemblyV8(
            selection=selection_ref,
            manifest=manifest_ref,
            draft=manifest.draft,
            semantic_key=selection_semantic_key(manifest.draft),
            families=manifest.authorFamilies,
            gaps=gaps,
        )

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register the window program's activities beside the historical ones."""
        return (
            self.prepare_plan,
            self.prepare_author_plan,
            self.prepare_review_plan,
            self.prepare_repair_plan,
            self.run_decision,
            self.assemble_inventory,
            self.assemble_author,
            self.assemble_assessment,
            self.assemble_repair,
        )


def decision_identity(request: DecisionRequest) -> str:
    """Stable identity for one planned decision, for logs and tests."""
    return hashlib.sha256(
        f"{request.context.run.run_id}:{request.kind}:{request.item_id}:{request.context.iteration}".encode()
    ).hexdigest()
