"""Modal client for one physical checkpointed speech stage call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from temnia_pipeline.modal_errors import transport_errors
from temnia_pipeline.settings import DEFAULT_SPEECH_PROGRESS_DICT
from temnia_pipeline.speech.contracts import PROTOCOL, SpeechStageJob, SpeechStageResult, Stage

if TYPE_CHECKING:
    from temnia_pipeline.settings import TranscriptionSettings


class SpeechDeploymentIdentity(BaseModel):
    """The additive app identity frozen into each new workflow plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    protocol: str
    build: str


@dataclass(frozen=True, slots=True)
class CallRunning:
    """The known handle has no terminal result yet."""


@dataclass(frozen=True, slots=True)
class CallFinished:
    """The app returned a validated success or application-failure envelope."""

    result: SpeechStageResult


@dataclass(frozen=True, slots=True)
class CallUnknown:
    """The handle is absent or its output expired."""


@dataclass(frozen=True, slots=True)
class CallCancelled:
    """Modal explicitly reports that the input was cancelled."""


@dataclass(frozen=True, slots=True)
class CallUnreachable:
    """The provider could not be queried; its physical outcome is unknown."""

    message: str


CallState = CallRunning | CallFinished | CallUnknown | CallCancelled | CallUnreachable


def _function(name: str, settings: TranscriptionSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        settings.speech_modal_app,
        name,
        environment_name=settings.modal_environment,
    )


def _call(call_id: str) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.FunctionCall.from_id(call_id)


def _progress(settings: TranscriptionSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Dict.from_name(
        DEFAULT_SPEECH_PROGRESS_DICT,
        create_if_missing=True,
        environment_name=settings.modal_environment,
    )


class SpeechModalClient:
    """One app-level spawn invocation; the ledger fences it outside this class.

    Modal may retry the transport with its own stable idempotency key. The app's
    function execution retry policy is zero, and this client never invokes spawn
    again after an ambiguous return.
    """

    def __init__(self, settings: TranscriptionSettings) -> None:
        self.settings = settings

    async def deployment_identity(self) -> SpeechDeploymentIdentity:
        """Read and strictly validate the deployed additive app identity."""
        payload = await _function("deployment_identity", self.settings).remote.aio()
        return SpeechDeploymentIdentity.model_validate(payload, strict=True)

    async def spawn(self, stage: Stage, job: SpeechStageJob) -> str:
        """Dispatch one stage once and return its durable handle."""
        call = await _function(stage, self.settings).spawn.aio(
            job.model_dump(mode="json", by_alias=True)
        )
        return str(call.object_id)

    async def status(self, handle: str) -> CallState:  # noqa: PLR0911
        """Distinguish a running, terminal, absent, and unreachable call."""
        from modal.exception import (  # noqa: PLC0415
            InputCancellation,
            NotFoundError,
            OutputExpiredError,
        )

        try:
            payload = await _call(handle).get.aio(timeout=0)
        except TimeoutError:
            return CallRunning()
        except (NotFoundError, OutputExpiredError):
            return CallUnknown()
        except InputCancellation:
            return CallCancelled()
        except transport_errors() as error:
            return CallUnreachable(f"{type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001
            return CallUnreachable(f"{type(error).__name__}: {error}")
        try:
            return CallFinished(SpeechStageResult.model_validate(payload))
        except ValidationError as error:
            return CallUnreachable(f"RemoteProtocolError: {error}")

    async def progress(self, attempt_id: str) -> dict[str, object] | None:
        """Read the app's best-effort measured progress note."""
        try:
            payload = await _progress(self.settings).get.aio(attempt_id)
        except Exception:  # noqa: BLE001
            return None
        return cast("dict[str, object]", payload) if isinstance(payload, dict) else None

    async def cancel(self, handle: str) -> None:
        """Request remote cancellation; caller must confirm a terminal state."""
        await _call(handle).cancel.aio()


class SpeechDeploymentError(RuntimeError):
    """The configured app cannot safely serve the checkpointed protocol."""


async def assert_checkpointed_deployment(
    client: SpeechModalClient, settings: TranscriptionSettings
) -> SpeechDeploymentIdentity:
    """Fail worker boot when the opt-in app is absent or speaks another build protocol."""
    if settings.provider != "modal-checkpointed":
        return SpeechDeploymentIdentity(protocol=PROTOCOL, build="unused")
    if not settings.speech_expected_build:
        message = "MODAL_SPEECH_BUILD must identify the checkpointed deployment"
        raise SpeechDeploymentError(message)
    try:
        identity = await client.deployment_identity()
    except Exception as error:
        message = f"cannot read {settings.speech_modal_app} deployment identity: {error}"
        raise SpeechDeploymentError(message) from error
    if identity.protocol != settings.speech_protocol:
        message = (
            f"{settings.speech_modal_app} speaks {identity.protocol!r}; "
            f"worker requires {settings.speech_protocol!r}"
        )
        raise SpeechDeploymentError(message)
    if identity.build != settings.speech_expected_build:
        message = (
            f"{settings.speech_modal_app} build is {identity.build!r}; "
            f"worker requires {settings.speech_expected_build!r}"
        )
        raise SpeechDeploymentError(message)
    return identity


async def assert_frozen_deployment(
    client: SpeechModalClient,
    *,
    app: str,
    protocol: str,
    build: str,
) -> SpeechDeploymentIdentity:
    """Refuse a mutable named app that no longer matches the workflow snapshot."""
    try:
        identity = await client.deployment_identity()
    except Exception as error:
        message = f"cannot revalidate {app} before dispatch: {error}"
        raise SpeechDeploymentError(message) from error
    if identity.protocol != protocol or identity.build != build:
        message = (
            f"{app} deployment changed before dispatch: "
            f"expected {protocol}/{build}, got {identity.protocol}/{identity.build}"
        )
        raise SpeechDeploymentError(message)
    return identity
