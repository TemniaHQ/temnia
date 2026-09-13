"""Small serializable activity boundaries for chapter orchestration."""

# Pydantic resolves these annotation types while constructing strict schemas.
# ruff: noqa: TC001, TC003

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from temnia_pipeline.contracts import (
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
from temnia_pipeline.harness.routes import AdmissionVersion, RouteSnapshot


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
