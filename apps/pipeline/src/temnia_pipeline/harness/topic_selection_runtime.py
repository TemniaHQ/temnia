"""Immutable activity inputs for the second standalone editorial program."""

# Pydantic resolves these runtime DTO annotations while Temporal registers activities.
# ruff: noqa: TC001

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    TopicPortfolioReviewV4,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
)
from temnia_pipeline.harness.routes import RouteEntry
from temnia_pipeline.harness.runtime_types import RunRef

SelectionProgramVersion = Literal["standalone-topics/3"]


class SelectionContext(BaseModel):
    """Every model input is an exact, scoped artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunRef
    evidence: HarnessArtifactRef
    program_version: SelectionProgramVersion = "standalone-topics/3"
    rubric: HarnessArtifactRef | None = None
    inventory: HarnessArtifactRef | None = None
    inventory_attempted: bool = False
    inventory_diagnostics: tuple[str, ...] = ()
    selection: HarnessArtifactRef | None = None
    assessment: HarnessArtifactRef | None = None
    navigation: HarnessArtifactRef | None = None
    rejection: HarnessArtifactRef | None = None
    candidate_id: str | None = None
    iteration: int = 0


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
    synthetic_payload: dict[str, object] | None = None


class SelectionSaveRequest(BaseModel):
    """A settled initial draft or grounded patch, never an unowned replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: SelectionContext
    draft: TopicSelectionDraft | None = None
    patch: TopicSelectionPatchV3 | None = None
    schema_error: str | None = None


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
    unavailable_cold_ids: tuple[str, ...] = ()
    source_review: TopicPortfolioReviewV4 | None = None
    source_dispatched: bool = True
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
