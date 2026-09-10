"""Private activity contracts for the versioned editorial program."""

# Runtime schemas need their annotation imports.
# ruff: noqa: TC001
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.contracts import HarnessArtifactRef
from temnia_pipeline.harness.editorial import EditorialRepairV1, EditorialVerdictV2
from temnia_pipeline.harness.runtime_types import (
    CompiledRevision,
    CompileProposalResult,
    RenderRevisionResult,
    RunRef,
)


class EditorialContextRequest(BaseModel):
    """Exact compiled candidate and optional grounded finding/render lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run: RunRef
    evidence: HarnessArtifactRef
    compiled: CompiledRevision
    generation_families: tuple[str, ...]
    iteration: Annotated[int, Field(ge=0)]
    assessment: HarnessArtifactRef | None = None
    rendered: RenderRevisionResult | None = None


class FinalizeEditorialRequest(BaseModel):
    """One retained independent model judgment to ground and publish."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    context: EditorialContextRequest
    verdict: EditorialVerdictV2
    model_stage: str
    verifier_family: str


class EditorialAssessment(BaseModel):
    """Grounded judgment and stable candidate identity for cycle detection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact: HarnessArtifactRef
    verdict: EditorialVerdictV2
    candidate_sha256: str


class CompileEditorialRepairRequest(BaseModel):
    """Replacement evaluated against its original immutable finding scope."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    context: EditorialContextRequest
    repair: EditorialRepairV1
    model_stage: str
    generator_family: str
    seen_candidate_hashes: tuple[str, ...] = ()
    preserve_existing_boundaries: bool = Field(default=False, exclude_if=lambda value: not value)


class EditorialRepairResult(BaseModel):
    """A canonical compiled correction or a retained finite refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    result: CompileProposalResult
    candidate_sha256: str | None = None
