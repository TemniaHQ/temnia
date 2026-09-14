"""The render job the worker sends to the card and the result it gets back.

Pydantic only: this module is imported inside the Modal render container, which carries
none of the worker's database, durable-runtime or PyAV dependencies.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RENDER_JOB_VERSION = "render-job/1"


class RenderSectionJob(BaseModel):
    """One exact source interval and where its file goes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    section_id: str
    start_numerator: int
    start_denominator: Annotated[int, Field(gt=0)]
    end_numerator: int
    end_denominator: Annotated[int, Field(gt=0)]
    output_key: str

    @property
    def start(self) -> Fraction:
        """The exact interval start on the source clock."""
        return Fraction(self.start_numerator, self.start_denominator)

    @property
    def end(self) -> Fraction:
        """The exact interval end on the source clock."""
        return Fraction(self.end_numerator, self.end_denominator)


class RenderJob(BaseModel):
    """Everything the container needs, and nothing it must look up."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["render-job/1"] = RENDER_JOB_VERSION
    master_key: str
    master_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size_bytes: Annotated[int, Field(gt=0)]
    timeline: dict[str, Any]
    config: dict[str, Any]
    sections: Annotated[list[RenderSectionJob], Field(min_length=1)]
    expected_seconds: Annotated[float, Field(gt=0)]


class RenderOutput(BaseModel):
    """One published section file and its hash, as the container measured it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    section_id: str
    output_key: str
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size_bytes: Annotated[int, Field(gt=0)]


class RenderResult(BaseModel):
    """What the call published; the runner verifies every object before trusting it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outputs: list[RenderOutput]
    encoder: str
    call_id: str | None = None


class RenderProgress(BaseModel):
    """The container's note: which section, how far."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: Literal["download", "render", "publish"]
    percent: Annotated[int, Field(ge=0, le=100)]
    section_id: str | None = None
