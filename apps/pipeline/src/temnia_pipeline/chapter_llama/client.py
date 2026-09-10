"""Authenticated Modal transport; one spawn, retained handles, explicit uncertainty."""

# ruff: noqa: EM101, TRY003, ANN401
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.chapter_llama.contracts import (
    PROTOCOL,
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    ModelConfig,
    ResourceProfile,
)


class DeploymentIdentity(BaseModel):
    """Frozen code/model/resource identity for the caller's reservation and job."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["temnia-chapter-llama/1"] = PROTOCOL
    build: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: ModelConfig
    resources: ResourceProfile


class ChapterLlamaConfig(BaseModel):
    """An explicitly enabled and frozen deployment; absent means disabled."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    app_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    environment: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    deployment: DeploymentIdentity


@dataclass(frozen=True, slots=True)
class CallState:
    """Transport uncertainty cannot be mistaken for failed computation."""

    status: Literal["running", "finished", "unreachable", "unknown", "cancelled"]
    outcome: ChapterLlamaOutcome | None = None
    message: str | None = None


class ChapterLlamaModalClient:
    """One authenticated compute app, independent from default gateway routing."""

    def __init__(
        self, *, app_name: str = "temnia-chapter-llama", environment: str | None = None
    ) -> None:
        self.app_name = app_name
        self.environment = environment

    def _function(self, name: str) -> Any:
        import modal  # noqa: PLC0415

        return modal.Function.from_name(self.app_name, name, environment_name=self.environment)

    async def deployment_identity(self) -> DeploymentIdentity:
        """Resolve a deployed function, not an imported local module."""
        payload = await self._function("deployment_identity").remote.aio()
        return DeploymentIdentity.model_validate(payload)

    async def spawn(self, job: ChapterLlamaJob) -> str:
        """One dispatch; caller commits reservation/attempt intent before invoking."""
        call = await self._function("infer").spawn.aio(job.model_dump(mode="json"))
        return str(call.object_id)

    async def status(self, handle: str, *, job: ChapterLlamaJob) -> CallState:
        """Poll only this known handle; never respawn on an unknown response."""
        import modal  # noqa: PLC0415
        from modal.exception import (  # noqa: PLC0415
            InputCancellation,
            NotFoundError,
            OutputExpiredError,
        )

        try:
            call = cast("Any", modal.FunctionCall.from_id(handle))
            payload = await call.get.aio(timeout=0)
        except TimeoutError:
            return CallState("running")
        except (NotFoundError, OutputExpiredError):
            return CallState("unknown")
        except InputCancellation:
            return CallState("cancelled")
        except Exception as error:  # noqa: BLE001
            return CallState("unreachable", message=f"{type(error).__name__}: {error}")
        try:
            outcome = ChapterLlamaOutcome.model_validate(payload)
            if outcome.job_sha256 != job.sha256 or outcome.build != job.expected_build:
                raise ValueError("Chapter-Llama response identity differs from dispatched job")  # noqa: TRY301
        except Exception as error:  # noqa: BLE001
            return CallState("unreachable", message=f"RemoteProtocolError: {error}")
        return CallState("finished", outcome=outcome)
