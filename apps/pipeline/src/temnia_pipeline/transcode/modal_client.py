"""The thin wrapper around the Modal SDK, and the seam the tests replace.

Everything Modal-specific is here, so `ModalTranscoder` is testable without an
account and the worker never imports the SDK when it is running the local
backend. The SDK import is inside each method for that reason: a wrong
`TRANSCODE_BACKEND` should not be able to make a working worker fail to start.

Untested until staging by design. There is no Modal account on the build
machine, and a mock of a network client proves nothing about the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from temnia_pipeline.modal_errors import transport_errors
from temnia_pipeline.transcode import LadderJob, LadderProgress, LadderResult

if TYPE_CHECKING:
    from temnia_pipeline.settings import TranscodeSettings

LADDER_FUNCTION = "ladder"
VERSION_FUNCTION = "version"


# The SDK's lookups return unparameterised generics (`Function[..., Unknown,
# Unknown]`), which pyright strict reports on every attribute reached through
# them. Confining that to three one-line helpers typed `Any` keeps the rest of
# the module checked; none of them touch the network, they build lazy handles.
def _function(name: str, settings: TranscodeSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        settings.modal_app, name, environment_name=settings.modal_environment
    )


def _call(call_id: str) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.FunctionCall.from_id(call_id)


def _progress_dict(settings: TranscodeSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Dict.from_name(
        settings.progress_dict,
        create_if_missing=True,
        environment_name=settings.modal_environment,
    )


@dataclass(frozen=True, slots=True)
class Running:
    """The call is still on a container."""


@dataclass(frozen=True, slots=True)
class Done:
    """The call returned; the ladder is published."""

    result: LadderResult


@dataclass(frozen=True, slots=True)
class Failed:
    """The call raised; the message is `TypeName: text` and decides the retry."""

    message: str


@dataclass(frozen=True, slots=True)
class Unknown:
    """Modal has never heard of this call id, or its result has expired."""


@dataclass(frozen=True, slots=True)
class Unreachable:
    """Modal could not be asked; the call may be running fine. Never a verdict."""

    message: str


CallStatus = Running | Done | Failed | Unknown | Unreachable


class ModalClient(Protocol):
    """What `ModalTranscoder` needs from Modal, and all it may know about it."""

    async def spawn(self, job: LadderJob) -> str:
        """Start the deployed ladder function; returns the call id."""
        ...

    async def status(self, call_id: str) -> CallStatus:
        """Poll one call without waiting for it."""
        ...

    async def progress(self, call_id: str) -> LadderProgress | None:
        """The last progress the function wrote, if it has written any."""
        ...

    async def version(self) -> str:
        """The deployed app's contract version."""
        ...


class RealModalClient:
    """`ModalClient` over the Modal SDK, on tokens from the environment."""

    def __init__(self, settings: TranscodeSettings) -> None:
        self.settings = settings

    async def spawn(self, job: LadderJob) -> str:
        """Spawn rather than call: a call id survives a worker restart, a blocking call does not."""
        call = await _function(LADDER_FUNCTION, self.settings).spawn.aio(
            job.model_dump(mode="json", by_alias=True)
        )
        return str(call.object_id)

    async def status(self, call_id: str) -> CallStatus:
        """Poll with a zero timeout: still running is a TimeoutError, not an error."""
        from modal.exception import NotFoundError, OutputExpiredError  # noqa: PLC0415

        try:
            payload = await _call(call_id).get.aio(timeout=0)
        except TimeoutError:
            return Running()
        except (NotFoundError, OutputExpiredError):
            return Unknown()
        except transport_errors() as error:
            # We could not ask. The encode may be fine; only a caller that
            # knows the difference can avoid spawning it again (S2 review, I05).
            return Unreachable(f"{type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001
            # Anything the function raised arrives here, including the
            # deterministic ffmpeg and truncation failures the caller has to
            # tell apart from an infrastructure fault. The type name goes in
            # front of the message because that is the only part that reliably
            # says which of the two it is; `str(error)` alone leaves the caller
            # guessing from wording. Safe to show: ffmpeg.py sanitises what it
            # puts in these messages.
            return Failed(f"{type(error).__name__}: {error}")
        return Done(LadderResult.model_validate(payload))

    async def progress(self, call_id: str) -> LadderProgress | None:
        """Read the function's own progress note; a missing one is not a fault.

        Progress is cosmetic. A Dict that has expired, or a read that fails
        while the encode is healthy, must not fail the activity.
        """
        try:
            payload = await _progress_dict(self.settings).get.aio(call_id)
        except Exception:  # noqa: BLE001
            return None
        if payload is None:
            return None
        return LadderProgress.model_validate(payload)

    async def version(self) -> str:
        """Call the deployed `version` function; used by the worker's boot probe."""
        return str(await _function(VERSION_FUNCTION, self.settings).remote.aio())
