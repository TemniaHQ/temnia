"""Activity messages for standalone videos; existing run accounting stays authoritative."""

# Runtime schemas require annotation imports.
# ruff: noqa: TC001, TC003
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    TopicAssessment,
    TopicColdReview,
    TopicEditSpec,
    TopicProposal,
    TopicSourceReview,
)
from temnia_pipeline.harness.routes import RouteEntry
from temnia_pipeline.harness.runtime_types import RunRef


class TopicContext(BaseModel):
    """Exact immutable inputs to one topic activity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: RunRef
    evidence: HarnessArtifactRef
    proposal: HarnessArtifactRef | None = None
    assessment: HarnessArtifactRef | None = None
    candidate_id: str | None = None
    navigation: HarnessArtifactRef | None = None
    proposal_validation: HarnessArtifactRef | None = None
    iteration: int = 0

    @model_validator(mode="after")
    def validation_is_initial_only(self) -> TopicContext:
        """A rejected initial proposal cannot masquerade as an accepted review input."""
        if self.proposal_validation is not None and any(
            value is not None for value in (self.proposal, self.assessment, self.candidate_id)
        ):
            message = "initial proposal validation cannot accompany an accepted proposal or review"
            raise ValueError(message)
        return self


class TopicCallPlan(BaseModel):
    """Prepared prompt and frozen independent editorial seats."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str
    author: RouteEntry
    verifier: RouteEntry
    input_artifacts: tuple[HarnessArtifactRef, ...]
    synthetic_payload: dict[str, object] | None = None


class SaveTopicProposal(BaseModel):
    """Native proposal output plus its paid operation identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: TopicContext
    proposal: TopicProposal
    model_stage: str
    author_family: str
    verifier_family: str


class TopicProposalValidation(BaseModel):
    """Reproducible source rejection of an exact settled initial model response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["topic-proposal-validation/1"] = "topic-proposal-validation/1"
    program_version: Literal["standalone-topics/1"] = "standalone-topics/1"
    prompt_version: Literal["standalone-topic-editor/1"] = "standalone-topic-editor/1"
    run_id: UUID
    evidence_id: UUID
    evidence_sha256: str
    model_stage: str
    model_response: HarnessArtifactRef
    prior_validation: HarnessArtifactRef | None = None
    proposal: TopicProposal
    error_message: str


class TopicProposalResult(BaseModel):
    """Either an accepted proposal or an owned diagnostic; never both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: HarnessArtifactRef | None = None
    validation: HarnessArtifactRef | None = None
    validation_error: str | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> TopicProposalResult:
        """Keep refusal distinguishable from an empty, accepted portfolio."""
        if (self.artifact is None) == (self.validation is None) or (
            (self.validation is None) != (self.validation_error is None)
        ):
            message = "topic result requires an accepted artifact or a diagnostic and error"
            raise ValueError(message)
        return self


class SaveTopicAssessment(BaseModel):
    """Cold and source judgments with their accepted operation identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: TopicContext
    cold_reviews: tuple[TopicColdReview, ...]
    cold_stages: tuple[str, ...]
    source_review: TopicSourceReview | None
    source_stage: str | None
    refusal: str | None = None
    author_family: str
    verifier_family: str


class TopicAssessmentResult(BaseModel):
    """Retained candidate findings, without implying human acceptance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: HarnessArtifactRef
    assessment: TopicAssessment
    all_passed: bool


class TopicCompilation(BaseModel):
    """Independent compiled executions and explicit unsafe-cut refusals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: HarnessArtifactRef
    edit: TopicEditSpec
    refusals: tuple[str, ...] = ()


class TopicRenderResult(BaseModel):
    """Rendered portfolio descriptor and technical outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    descriptor: HarnessArtifactRef
    technical_passed: bool
    count: int
