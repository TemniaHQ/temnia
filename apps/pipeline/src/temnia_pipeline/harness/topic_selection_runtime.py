"""Immutable activity inputs for the second standalone editorial program."""

# Pydantic resolves these runtime DTO annotations while Temporal registers activities.
# ruff: noqa: TC001, TC003

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from temnia_pipeline.contracts import (
    ChapterRunInput,
    HarnessArtifactRef,
    TopicAuthorPackagingManifest,
    TopicAuthorPackagingPlan,
    TopicAuthorPackagingShard,
    TopicOpportunityInventoryManifest,
    TopicOpportunityInventoryPlan,
    TopicOpportunityInventoryShard,
    TopicPortfolioReviewV4,
    TopicRepairManifest,
    TopicRepairPlan,
    TopicRepairShard,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSourceReviewManifest,
    TopicSourceReviewPlan,
    TopicSourceReviewShard,
)
from temnia_pipeline.harness.routes import RouteEntry
from temnia_pipeline.harness.runtime_types import RunRef

SelectionProgramVersion = Literal[
    "standalone-topics/3",
    "standalone-topics/4",
    "standalone-topics/5",
    "standalone-topics/6",
    "standalone-topics/7",
]
SourceToolRole = Literal["inventory", "author", "source_reviewer", "cold_reviewer", "repair"]
SourceInspectionFormat = Literal[
    "topic-source-inspection/1",
    "topic-source-inspection/2",
    "topic-source-inspection/3",
    "topic-source-inspection/4",
]


class SourceInspectionCall(BaseModel):
    """One successful, bounded source-tool result retained without repeating source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_name: Literal[
        "browse_source",
        "search_source",
        "read_source",
        "inspect_candidate",
        "read_media_evidence",
        "read_editorial_context",
    ]
    arguments: dict[str, Any]
    node_ids: tuple[str, ...] = ()
    sentence_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    complete: bool
    next_cursor: int | None = None
    next_sentence_id: str | None = None
    next_context_cursor: str | None = None
    next_character_offset: int | None = None


class SourceInspectionTrace(BaseModel):
    """Tool-backed source access associated with one final typed model answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: SourceInspectionFormat = "topic-source-inspection/1"
    index_sha256: str
    role: SourceToolRole
    stage: str
    checkpoint_sha256: str | None = None
    request_sequence: int = 0
    calls: tuple[SourceInspectionCall, ...] = ()
    retained_sentence_ids: tuple[str, ...] = ()
    evicted_sentence_count: int = 0
    observed_sentence_ids: tuple[str, ...] = ()
    delivered_context_ids: tuple[str, ...] = ()
    delivered_fragment_ids: tuple[str, ...] = ()


class SyntheticSourceInspectionRecord(BaseModel):
    """Explicit test provenance; never asserts that a model consumed evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    synthetic: Literal[True] = True
    role: SourceToolRole
    stage: str
    note: str = "Recorded fixture; model evidence consumption was not measured."


class SelectionContext(BaseModel):
    """Every model input is an exact, scoped artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunRef
    evidence: HarnessArtifactRef
    program_version: SelectionProgramVersion = "standalone-topics/3"
    rubric: HarnessArtifactRef | None = None
    source_index: HarnessArtifactRef | None = None
    inventory_plan: HarnessArtifactRef | None = None
    inventory_section_id: str | None = None
    inventory: HarnessArtifactRef | None = None
    inventory_attempted: bool = False
    inventory_diagnostics: tuple[str, ...] = ()
    author_plan: HarnessArtifactRef | None = None
    author_work_item_id: str | None = None
    author_families: tuple[str, ...] = ()
    selection: HarnessArtifactRef | None = None
    source_review_plan: HarnessArtifactRef | None = None
    source_review_work_item_id: str | None = None
    assessment: HarnessArtifactRef | None = None
    repair_plan: HarnessArtifactRef | None = None
    repair_work_item_id: str | None = None
    navigation: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    candidate_id: str | None = None
    iteration: int = 0
    # Fallback positions inside the seat pools; advanced by the workflow when a route
    # keeps failing transiently, sticky for the rest of the run.
    author_index: int = 0
    verifier_index: int = 0
    request_attempt: int = 0
    recovery_feedback: tuple[str, ...] = ()
    resume_indexed: bool = False


class EditorialWorkInput(BaseModel):
    """A child owns one editorial decision; continuation carries identities, not history."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request: ChapterRunInput
    context: SelectionContext
    activity_name: str
    resumed_context: SelectionContext | None = None

    def identity(self) -> str:
        """Stable through continuation, tied to the exact original editorial assignment."""
        value = self.model_copy(update={"resumed_context": None})
        return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


class EditorialWorkResult(BaseModel):
    """Only an admitted artifact or a retained diagnostic returns to the parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    saved: dict[str, Any] | None = None
    context: SelectionContext
    reason: str | None = None
    limited: bool = False


class EditorialWorkSave(BaseModel):
    """Persist an admitted decision for exact reuse by a later parent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    work: EditorialWorkInput
    result: EditorialWorkResult


class EditorialProgress(BaseModel):
    """The last accepted editorial selection, independently of any review render."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    base_revision: int = 0
    phase: Literal["review", "render"] = "review"
    compiled: HarnessArtifactRef | None = None
    seen_keys: tuple[str, ...] = ()


class EditorialResume(BaseModel):
    """Validated progress with the accepted selection loaded for orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    progress: EditorialProgress
    draft: TopicSelectionDraft


class EditorialResumeLookup(BaseModel):
    """Typed absent/present progress across the Temporal activity boundary."""

    resume: EditorialResume | None = None


class EditorialWorkLookup(BaseModel):
    """Typed absent/present admitted work across the Temporal activity boundary."""

    result: EditorialWorkResult | None = None


class TopicSourceIndexUseRecord(BaseModel):
    """Run-scoped evidence that one exact stable source index was consumed."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-source-index-use/1"] = "topic-source-index-use/1"
    run_id: uuid.UUID
    source_id: uuid.UUID
    evidence: HarnessArtifactRef
    source_index: HarnessArtifactRef
    reused: bool


class SourceIndexBuildResult(BaseModel):
    """Workflow result for a built or verified reusable source index."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    use_record: HarnessArtifactRef
    reused: bool


class SelectionCallPlan(BaseModel):
    """Prepared native request and immutable accounting facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt: str
    stage: str
    prompt_version: str
    program_version: SelectionProgramVersion = "standalone-topics/3"
    schema_version: str
    author: RouteEntry
    verifier: RouteEntry
    input_artifacts: tuple[HarnessArtifactRef, ...]
    source_index: HarnessArtifactRef | None = None
    source_tool_role: SourceToolRole | None = None
    candidate_selection: HarnessArtifactRef | None = None
    media_evidence: HarnessArtifactRef | None = None
    allowed_browse_parent_ids: tuple[str, ...] = ()
    allowed_candidate_ids: tuple[str, ...] = ()
    allowed_sentence_ids: tuple[str, str] | None = None
    editorial_context: HarnessArtifactRef | None = None
    synthetic_payload: dict[str, object] | None = None


class SourceCheckpointLoadRequest(BaseModel):
    """Locate the latest compact state for one exact indexed seat call."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    plan: SelectionCallPlan


class SourceCheckpointLoadResult(BaseModel):
    """Bounded checkpoint bytes returned to workflow code for a known retry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    checkpoint: dict[str, Any] | None = None


class SelectionSaveRequest(BaseModel):
    """A settled initial draft or grounded patch, never an unowned replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    draft: TopicSelectionDraft | None = None
    patch: TopicSelectionPatchV3 | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class SelectionSaveResult(BaseModel):
    """A refused answer retains the exact prior selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    selection: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    diagnostics: tuple[str, ...] = ()
    semantic_key: str | None = None
    draft: TopicSelectionDraft | None = None


class OpportunityInventorySaveRequest(BaseModel):
    """A settled source-wide map before the author sees any packaging choice."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    inventory: TopicSelectionDraft | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class OpportunityInventoryPlanResult(BaseModel):
    """Deterministic section work plan and its immutable artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    plan: TopicOpportunityInventoryPlan


class OpportunityInventoryShardSaveRequest(BaseModel):
    """One settled section answer and its exact indexed inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    inventory: TopicSelectionDraft | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class OpportunityInventoryShardSaveResult(BaseModel):
    """One admitted shard, or a retained diagnostic with no false completeness."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    shard: TopicOpportunityInventoryShard | None = None
    diagnostics: tuple[str, ...] = ()


class OpportunityInventoryShardRejection(BaseModel):
    """Settled section response refused by schema, inspection, or ownership admission."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-opportunity-inventory-shard-rejection/1"] = (
        "topic-opportunity-inventory-shard-rejection/1"
    )
    response: HarnessArtifactRef
    section_id: str
    inventory: TopicSelectionDraft | None = None
    diagnostics: tuple[str, ...]


class OpportunityInventoryManifestRequest(BaseModel):
    """Exact ordered shard references from one immutable inventory plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    shard_artifacts: tuple[HarnessArtifactRef, ...]


class OpportunityInventoryManifestResult(BaseModel):
    """Complete assembled inventory and its durable manifest identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    manifest: TopicOpportunityInventoryManifest


class AuthorPackagingPlanResult(BaseModel):
    """Deterministic bounded author work plan and its immutable identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    plan: TopicAuthorPackagingPlan


class AuthorPackagingShardSaveRequest(BaseModel):
    """One settled bounded author answer and its indexed inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    draft: TopicSelectionDraft | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class AuthorPackagingShardSaveResult(BaseModel):
    """One admitted author shard, or an exact retained refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    shard: TopicAuthorPackagingShard | None = None
    diagnostics: tuple[str, ...] = ()


class AuthorPackagingShardRejection(BaseModel):
    """Settled bounded author output refused before whole-selection assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-author-packaging-shard-rejection/1"] = (
        "topic-author-packaging-shard-rejection/1"
    )
    response: HarnessArtifactRef
    work_item_id: str
    draft: TopicSelectionDraft | None = None
    diagnostics: tuple[str, ...]


class AuthorPackagingManifestRequest(BaseModel):
    """Exact ordered author shards supplied to the complete-manifest gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    shard_artifacts: tuple[HarnessArtifactRef, ...]


class AuthorPackagingManifestResult(BaseModel):
    """Complete author manifest and ordinary accepted selection identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    selection: HarnessArtifactRef
    manifest: TopicAuthorPackagingManifest
    draft: TopicSelectionDraft


class ColdReviewSaveRequest(BaseModel):
    """One isolated cold answer and the speech delivered to its reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    review: TopicSelectionColdReview | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class ColdReviewSaveResult(BaseModel):
    """An admitted cold judgment or a retained diagnostic for correction."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    review: TopicSelectionColdReview | None = None
    inspection: SourceInspectionTrace | None = None
    diagnostics: tuple[str, ...] = ()


class ColdReviewRecord(BaseModel):
    """Exact cold observation and its admission outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-cold-observation/1"] = "topic-cold-observation/1"
    candidate_id: str
    response: HarnessArtifactRef
    inspection: HarnessArtifactRef | None = None
    review: TopicSelectionColdReview | None = None
    diagnostics: tuple[str, ...] = ()


class SourceReviewPlanResult(BaseModel):
    """Deterministic bounded source-review plan and immutable artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    plan: TopicSourceReviewPlan


class SourceReviewShardSaveRequest(BaseModel):
    """One settled bounded source-review answer and its indexed inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    review: TopicPortfolioReviewV4 | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class SourceReviewShardSaveResult(BaseModel):
    """One admitted review shard, or an exact retained refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    shard: TopicSourceReviewShard | None = None
    diagnostics: tuple[str, ...] = ()


class SourceReviewShardRejection(BaseModel):
    """Settled bounded reviewer output refused before portfolio assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-source-review-shard-rejection/1"] = (
        "topic-source-review-shard-rejection/1"
    )
    response: HarnessArtifactRef
    work_item_id: str
    review: TopicPortfolioReviewV4 | None = None
    diagnostics: tuple[str, ...]


class SourceReviewManifestRequest(BaseModel):
    """Exact ordered review shards supplied to the complete-manifest gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    shard_artifacts: tuple[HarnessArtifactRef, ...]


class SourceReviewManifestResult(BaseModel):
    """Complete assembled source-review manifest and portfolio."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    manifest: TopicSourceReviewManifest


class RepairPlanResult(BaseModel):
    """Deterministic connected-component repair plan and immutable artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    plan: TopicRepairPlan


class RepairShardSaveRequest(BaseModel):
    """One settled component patch and its indexed source inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    patch: TopicSelectionPatchV3 | None = None
    schema_error: str | None = None
    inspection: SourceInspectionTrace | None = None


class RepairShardSaveResult(BaseModel):
    """One admitted repair component, or an exact retained refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    shard: TopicRepairShard | None = None
    diagnostics: tuple[str, ...] = ()


class RepairShardRejection(BaseModel):
    """Settled component response refused before atomic repair assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-repair-shard-rejection/1"] = "topic-repair-shard-rejection/1"
    response: HarnessArtifactRef
    work_item_id: str
    patch: TopicSelectionPatchV3 | None = None
    diagnostics: tuple[str, ...]


class RepairManifestRequest(BaseModel):
    """Exact ordered component shards supplied to the atomic manifest gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    shard_artifacts: tuple[HarnessArtifactRef, ...]


class RepairManifestResult(BaseModel):
    """Complete aggregate repair and its new accepted selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    manifest: TopicRepairManifest
    selection: HarnessArtifactRef
    draft: TopicSelectionDraft
    semantic_key: str


class SelectionRejection(BaseModel):
    """Source/schema rejection bound to a known successful provider response."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-selection-rejection/2"] = "topic-selection-rejection/2"
    response: HarnessArtifactRef
    stage: str
    draft: TopicSelectionDraft | None = None
    patch: TopicSelectionPatchV3 | None = None
    diagnostics: tuple[str, ...]


class SelectionReviewRequest(BaseModel):
    """Review observations retain their original call inputs and stages."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    cold_reviews: tuple[TopicSelectionColdReview, ...] = ()
    cold_candidate_ids: tuple[str, ...] = ()
    cold_stages: tuple[str, ...] = ()
    cold_contexts: tuple[SelectionContext, ...] = ()
    cold_inspections: tuple[SourceInspectionTrace | None, ...] = ()
    unavailable_cold_ids: tuple[str, ...] = ()
    source_review: TopicPortfolioReviewV4 | None = None
    source_dispatched: bool = True
    source_inspection: SourceInspectionTrace | None = None
    source_review_manifest: HarnessArtifactRef | None = None
    reasons: tuple[str, ...] = ()
    execution_limited: bool = False


class SelectionAssessmentResult(BaseModel):
    """The complete retained decision, not a human publication decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    assessment: TopicSelectionAssessment
    actionable: bool


class SelectionStopRequest(BaseModel):
    """Append terminal execution facts to the last valid assessment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    reasons: tuple[str, ...]
    execution_limited: bool = False


def selection_call_inputs(plan: SelectionCallPlan) -> dict[str, object]:
    """Reproduce the paid operation identity at dispatch and retained admission."""
    return {
        "artifacts": [{"id": str(ref.id), "sha256": ref.sha256} for ref in plan.input_artifacts],
        "allowedBrowseParentIds": list(plan.allowed_browse_parent_ids),
        "allowedCandidateIds": list(plan.allowed_candidate_ids),
        "promptSha256": hashlib.sha256(plan.prompt.encode()).hexdigest(),
    }


def effective_topic_output_tokens(requested_max: int, route: RouteEntry) -> int:
    """Honor both frozen ceilings only for the v2 selection programme."""
    if type(requested_max) is not int or requested_max <= 0:
        message = "requested topic output allowance must be a positive integer"
        raise ValueError(message)
    return min(requested_max, route.max_output_tokens)


def selection_call_config(plan: SelectionCallPlan, max_tokens: int) -> dict[str, object]:
    """The reviewer reservation and effective output are immutable call inputs."""
    route = plan.verifier if plan.stage.startswith("verify:") else plan.author
    return {
        "maxOutputTokens": effective_topic_output_tokens(max_tokens, route),
        "reservedVerifierFamily": plan.verifier.family,
    }
