"""Activity messages for standalone videos; existing run accounting stays authoritative."""

# Runtime schemas require annotation imports.
# ruff: noqa: TC001
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    TopicEditSpec,
)
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
