"""Small serializable activity boundaries for chapter orchestration."""

# Pydantic resolves these annotation types while constructing strict schemas.
# ruff: noqa: TC001, TC003

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterProposal,
    ChapterReviewInput,
    ChapterReviewOutput,
    ChapterRunConfig,
    ChapterRunInput,
    HarnessArtifactRef,
    HarnessRunStatus,
    TopicEditorialPatchInput,
    TranscriptRevisionAnnotations,
)
from temnia_pipeline.harness.editorial_policy import EditorialPolicy
from temnia_pipeline.harness.routes import AdmissionVersion, RouteEntry, RouteSnapshot


class WorkflowIdentity(BaseModel):
    """Temporal execution identity persisted on a harness run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workflow_id: Annotated[str, Field(min_length=1, max_length=512)]
    workflow_run_id: Annotated[str, Field(min_length=1, max_length=512)]


class StartRunRequest(BaseModel):
    """Immutable web intent plus the Temporal execution claiming it."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request: ChapterRunInput
    workflow: WorkflowIdentity
    editorial_policy: EditorialPolicy = "standalone-topics/3"
    evaluation_program: dict[str, JsonValue] | None = None


class PinnedTranscript(BaseModel):
    """Accepted immutable transcript revision selected at run creation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transcript_id: UUID
    revision: Annotated[int, Field(gt=0)]
    storage_key: Annotated[str, Field(min_length=1, max_length=2048)]
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    annotations: TranscriptRevisionAnnotations | None = None
    machine_revision: Annotated[int | None, Field(gt=0)] = None
    legacy_speaker_labels: dict[str, str] = Field(default_factory=dict)


class PinnedSource(BaseModel):
    """Immutable source object facts captured before evidence construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    storage_key: Annotated[str, Field(min_length=1, max_length=2048)]
    size_bytes: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(gt=0)]
    etag: str | None = None
    version_id: str | None = None


class RunSnapshot(BaseModel):
    """Compact current run state returned between finite activities."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    workflow_id: Annotated[str, Field(min_length=1, max_length=512)]
    workflow_run_id: Annotated[str, Field(min_length=1, max_length=512)]
    source_id: UUID
    request_key: UUID
    initial_budget_micros: Annotated[int, Field(ge=0)]
    config: ChapterRunConfig
    brief: Annotated[str, Field(max_length=100000)]
    status: HarnessRunStatus
    stage: str | None
    budget_micros: Annotated[int, Field(ge=0)]
    spent_micros: Annotated[int, Field(ge=0)]
    reserved_micros: Annotated[int, Field(ge=0)]
    dispatch_count: Annotated[int, Field(ge=0)]
    repair_count: Annotated[int, Field(ge=0)]
    current_revision: Annotated[int, Field(ge=0)]
    accepted_revision: Annotated[int | None, Field(gt=0)]
    evidence_artifact_id: UUID | None
    error_message: str | None
    source: PinnedSource
    transcript: PinnedTranscript
    route_snapshot: RouteSnapshot
    evaluation_program: dict[str, JsonValue] | None = None
    evaluation_program_sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    editorial_policy: EditorialPolicy = "standalone-topics/3"
    topic_shot_detector: Literal["pyscenedetect-adaptive", "scdet"] = "scdet"
    # The admission arithmetic frozen at run start; runs that predate the key used the
    # one-byte-one-token estimate and keep reading as `admission/1`.
    admission: AdmissionVersion = "admission/1"


class StartRunResult(BaseModel):
    """Run start/refetch result with its pinned route and transcript facts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunSnapshot
    created: bool


class RunRef(BaseModel):
    """Scoped identity for one compact run read."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope_organization_id: UUID
    scope_user_id: UUID
    source_id: UUID
    run_id: UUID


class BuildEvidenceRequest(BaseModel):
    """Scoped immutable run identity for evidence construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    segmenter: Literal["sat"] = "sat"


class StageUpdate(BaseModel):
    """Compare-and-set request that prevents late activities reviving a run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope_organization_id: UUID
    scope_user_id: UUID
    source_id: UUID
    run_id: UUID
    expected_stage: str | None
    expected_revision: Annotated[int, Field(ge=0)]
    next_stage: Annotated[str | None, Field(max_length=128)]
    status: HarnessRunStatus
    error_message: Annotated[str | None, Field(max_length=2000)] = None


class ClaimRepairRequest(BaseModel):
    """Idempotent repair slot claim by the workflow execution that owns the run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    workflow: WorkflowIdentity
    expected_repair_count: Annotated[int, Field(ge=0)]


class MarkRunFailedRequest(BaseModel):
    """Safe terminal failure fence for the workflow execution that owns a run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    workflow: WorkflowIdentity
    error_message: Annotated[str, Field(min_length=1, max_length=2000)]
    status: Literal["failed", "budget_paused"] = "failed"


class EvidenceResult(BaseModel):
    """Published evidence plus only the bounded planning facts Temporal needs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact: HarnessArtifactRef
    sentence_count: Annotated[int, Field(ge=0)]
    word_count: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(gt=0)]
    lexical_state: Literal["present", "empty"]


class ResumeRunAssets(BaseModel):
    """Current immutable assets used by a known-state operational resume."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence: HarnessArtifactRef
    edit: HarnessArtifactRef
    revision: Annotated[int, Field(gt=0)]
    base_revision: Annotated[int | None, Field(gt=0)] = None


class PlanningWindow(BaseModel):
    """One contiguous, bounded model planning window."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Annotated[str, Field(min_length=1, max_length=256)]
    first_sentence_id: Annotated[str, Field(min_length=1, max_length=256)]
    last_sentence_id: Annotated[str, Field(min_length=1, max_length=256)]
    sentence_count: Annotated[int, Field(gt=0)]
    prompt: Annotated[str, Field(min_length=1, max_length=524288)]


class PreparePlanningRequest(BaseModel):
    """Evidence identity used to prepare one bounded proposal request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    extra_context_bytes: Annotated[int, Field(ge=0, le=524288)] = 0


class ProposalPlan(BaseModel):
    """Bounded direct or first-level hierarchy calls and frozen route facts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    windows: tuple[PlanningWindow, ...]
    route: RouteEntry
    summary_route: RouteEntry
    synthetic_payload: dict[str, object] | None = None
    synthetic_summary_payload: dict[str, object] | None = None


class ValidateSummaryRequest(BaseModel):
    """One retained model summary and its immutable grounding lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    window: PlanningWindow
    summary: dict[str, object]
    model_stage: Annotated[str, Field(min_length=1, max_length=128)]
    hierarchy_level: Annotated[int, Field(ge=1, le=8)] = 1
    input_artifacts: tuple[HarnessArtifactRef, ...] = ()


class ValidatedSummary(BaseModel):
    """Grounded summary value or a finite content-free semantic refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: dict[str, object] | None = None
    artifact: HarnessArtifactRef | None = None
    refusal: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def _one_outcome(self) -> ValidatedSummary:
        accepted = self.summary is not None and self.artifact is not None
        if accepted == (self.refusal is not None):
            message = "validated summary requires exactly one accepted or refused outcome"
            raise ValueError(message)
        return self


class PrepareGlobalProposalRequest(BaseModel):
    """Grounded first-level summaries used to build the global proposal prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    windows: tuple[PlanningWindow, ...]
    summaries: tuple[dict[str, object], ...]
    hierarchy_level: Annotated[int, Field(ge=1, le=8)] = 1
    grounding_artifacts: tuple[HarnessArtifactRef, ...] = ()


class GlobalProposalPlan(BaseModel):
    """Bounded global prompt produced after validating hierarchy lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prompt: Annotated[str | None, Field(min_length=1, max_length=524288)] = None
    route: RouteEntry
    synthetic_payload: dict[str, object] | None = None
    reduction_windows: tuple[PlanningWindow, ...] = ()
    hierarchy_level: Annotated[int, Field(ge=1, le=8)] = 1
    refusal: Annotated[str | None, Field(max_length=2000)] = None
    input_artifacts: tuple[HarnessArtifactRef, ...] = ()


class CompileProposalRequest(BaseModel):
    """Validated model proposal and immutable evidence identities to compile."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    proposal: ChapterProposal
    generator_family: Annotated[str, Field(min_length=1, max_length=128)]
    model_stage: Annotated[str, Field(min_length=1, max_length=128)]
    boundary_constraints: tuple[tuple[str, str, str], ...] = ()
    editorial_dependencies: tuple[HarnessArtifactRef, ...] = ()
    prior_proposal_artifact: HarnessArtifactRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prior_edit_artifact: HarnessArtifactRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _prior_pair(self) -> CompileProposalRequest:
        if (self.prior_proposal_artifact is None) != (self.prior_edit_artifact is None):
            message = "preservation requires both prior artifact references"
            raise ValueError(message)
        return self


class ProposalDiagnosticRequest(BaseModel):
    """Exact retained proposal response to inspect without another model call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    model_stage: Annotated[str, Field(min_length=1, max_length=128)]
    route: RouteEntry
    program_version: Annotated[str, Field(min_length=1, max_length=128)]
    prompt_version: Annotated[str, Field(min_length=1, max_length=128)]
    schema_version: Annotated[str, Field(min_length=1, max_length=128)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    operation_inputs: dict[str, object]
    operation_config: dict[str, object]
    input_artifacts: tuple[HarnessArtifactRef, ...] = ()
    compiler_refusal: Annotated[str | None, Field(min_length=1, max_length=2000)] = None


class ProposalDiagnosticIssue(BaseModel):
    """Content-free schema path and validation code safe for repair feedback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: Annotated[str, Field(min_length=1, max_length=256)]
    code: Annotated[str, Field(min_length=1, max_length=128)]


class ProposalDiagnostic(BaseModel):
    """Content-free reason and immutable evidence for one unusable proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact: HarnessArtifactRef
    response: HarnessArtifactRef
    code: Literal["output_limit", "invalid_json", "invalid_schema", "compiler_refusal"]
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    issues: Annotated[tuple[ProposalDiagnosticIssue, ...], Field(max_length=32)] = ()
    compiler_code: Annotated[str | None, Field(max_length=128)] = None


class CompiledRevision(BaseModel):
    """Immutable proposal/edit artifacts ready for revision CAS acceptance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_artifact: HarnessArtifactRef
    edit_artifact: HarnessArtifactRef
    edit: ChapterEditSpec
    revision: Annotated[int, Field(gt=0)]


class CompileProposalResult(BaseModel):
    """A compiled proposal or one bounded semantic refusal, never both."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    compiled: CompiledRevision | None = None
    refusal: Annotated[str | None, Field(min_length=1, max_length=2000)] = None

    @model_validator(mode="after")
    def _one_outcome(self) -> CompileProposalResult:
        if (self.compiled is None) == (self.refusal is None):
            message = "compile result requires exactly one outcome"
            raise ValueError(message)
        return self


class AcceptInitialRevisionRequest(BaseModel):
    """Compact control-queue request to accept one prepared initial edit."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    request_key: UUID
    edit_artifact_id: UUID


class RenderRevisionRequest(BaseModel):
    """Immutable edit identity for a heavy render/check activity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    edit: HarnessArtifactRef
    revision: Annotated[int, Field(gt=0)]


class RenderRevisionResult(BaseModel):
    """Current descriptor and bounded technical facts for editorial verification."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    descriptor: HarnessArtifactRef
    has_kept_sections: bool = True
    technical_report: tuple[dict[str, object], ...]
    technical_passed: bool


class ReuseRevisionRenderRequest(BaseModel):
    """A review-only revision eligible to reuse its predecessor's checked bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    predecessor_revision: Annotated[int, Field(gt=0)]
    edit: HarnessArtifactRef
    revision: Annotated[int, Field(gt=1)]
    required: bool = False


class ReuseRevisionRenderOutcome(BaseModel):
    """Verified metadata-only reuse, or an explicit content-change fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reused: RenderRevisionResult | None = None
    all_sections_accepted: bool = False


class PrepareVerificationRequest(BaseModel):
    """Current evidence/edit/render identities and all generating families."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    proposal: HarnessArtifactRef
    edit: HarnessArtifactRef
    rendered: RenderRevisionResult
    generation_families: tuple[str, ...]


class VerificationPlan(BaseModel):
    """A bounded independent verifier call, or an explicit review-only refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prompt: Annotated[str | None, Field(max_length=524288)] = None
    route: RouteEntry | None = None
    synthetic_payload: dict[str, object] | None = None
    refusal: Annotated[str | None, Field(max_length=2000)] = None
    output_cap: Annotated[int, Field(gt=0)] = 1
    dispatch_limit: Annotated[int, Field(gt=0, le=128)] = 1
    editorial_v2: bool = False
    editorial_prompt_version: Annotated[str | None, Field(min_length=1, max_length=128)] = Field(
        default=None, exclude_if=lambda value: value is None
    )


class FinalizeVerificationRequest(BaseModel):
    """Validated verifier verdict to publish with its measured dependencies."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    revision: Annotated[int, Field(gt=0)]
    edit: HarnessArtifactRef
    rendered: RenderRevisionResult
    verdict: dict[str, object]
    verifier_family: Annotated[str, Field(min_length=1, max_length=128)]


class ExportRevisionRequest(BaseModel):
    """Verified current edit and render descriptor eligible for human-complete export."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    revision: Annotated[int, Field(gt=0)]
    edit: HarnessArtifactRef
    descriptor: HarnessArtifactRef


class ExportRevisionResult(BaseModel):
    """Export publication outcome; absent means human decisions remain incomplete."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact: HarnessArtifactRef | None
    ready: bool


class PreparedReviewMutation(BaseModel):
    """Heavy pure review result and any immutable candidate edit artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request: ChapterReviewInput | TopicEditorialPatchInput
    candidate: HarnessArtifactRef | None
    evidence: HarnessArtifactRef | None = None
    message: Annotated[str, Field(max_length=2000)]


class CommitReviewMutationRequest(BaseModel):
    """Compact control-queue CAS for a prepared review mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prepared: PreparedReviewMutation
    workflow: WorkflowIdentity


class CommitReviewMutationResult(BaseModel):
    """Persisted review outcome plus a render request when revision advanced."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    output: ChapterReviewOutput
    render: RenderRevisionRequest | None


class ReviewEventResult(BaseModel):
    """Internal persisted review event result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: Literal["applied", "conflict", "refused"]
    revision: Annotated[int | None, Field(gt=0)]
    message: Annotated[str, Field(max_length=2000)]
