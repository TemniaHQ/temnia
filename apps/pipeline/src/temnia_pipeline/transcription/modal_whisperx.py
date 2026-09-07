"""WhisperX on a Modal L4, driven from the worker by call id.

Everything Modal-specific for transcription is here, so the runner is testable
without an account and the worker never imports the SDK when it is replaying
recordings. The SDK import is inside each method for that reason: a wrong
`TRANSCRIPTION_PROVIDER` should not be able to stop a working worker starting.

Spawn, then poll. A blocking remote call would tie half an hour of GPU work to
one TCP connection and one worker process; a call id is a handle that outlives
both, so a worker restarted mid-transcription reattaches to the run already on
the GPU instead of paying for it twice.

Untested until staging by design, like its ladder sibling: there is no Modal
account on the build machine, and a mock of a network client proves nothing
about the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from temnia_pipeline.modal_errors import transport_errors
from temnia_pipeline.transcription import (
    Done,
    Failed,
    Running,
    TranscribeRaw,
    TranscriptionProgress,
    Unknown,
    Unreachable,
)

if TYPE_CHECKING:
    from temnia_pipeline.settings import TranscriptionSettings
    from temnia_pipeline.transcription import RunStatus, TranscribeJob

TRANSCRIBE_FUNCTION = "transcribe"

NAME = "whisperx"
MODEL = "large-v3"
# The pinned release in `modal_app.py`'s image. It rides on every revision, so
# a calibration round can tell two runs of different engine versions apart.
VERSION = "3.8.6"


# The SDK's lookups return unparameterised generics (`Function[..., Unknown,
# Unknown]`), which pyright strict reports on every attribute reached through
# them. Confining that to two one-line helpers typed `Any` keeps the rest of
# the module checked; neither touches the network, they build lazy handles.
def _function(name: str, settings: TranscriptionSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        settings.modal_app, name, environment_name=settings.modal_environment
    )


def _call(call_id: str) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.FunctionCall.from_id(call_id)


def _progress_dict(settings: TranscriptionSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Dict.from_name(
        settings.progress_dict,
        create_if_missing=True,
        environment_name=settings.modal_environment,
    )


class ModalWhisperXProvider:
    """A `TranscriptionProvider` over the deployed `transcribe` function."""

    def __init__(self, settings: TranscriptionSettings) -> None:
        self.settings = settings

    @property
    def name(self) -> str:
        """The engine, as recorded on every revision it produces."""
        return NAME

    @property
    def model(self) -> str:
        """The Whisper model the deployed function loads."""
        return MODEL

    @property
    def version(self) -> str:
        """The whisperx release pinned in the image."""
        return VERSION

    async def start(self, job: TranscribeJob) -> str:
        """Spawn rather than call: a call id survives a worker restart."""
        call = await _function(TRANSCRIBE_FUNCTION, self.settings).spawn.aio(
            job.model_dump(mode="json", by_alias=True)
        )
        return str(call.object_id)

    async def status(self, handle: str) -> RunStatus:
        """Poll with a zero timeout: still running is a TimeoutError, not an error."""
        from modal.exception import NotFoundError, OutputExpiredError  # noqa: PLC0415

        try:
            payload = await _call(handle).get.aio(timeout=0)
        except TimeoutError:
            return Running()
        except (NotFoundError, OutputExpiredError):
            return Unknown()
        except transport_errors() as error:
            # We could not ask. The run may be fine; only a caller that knows
            # the difference can avoid starting it again (S2 review, I05).
            return Unreachable(f"{type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001
            # Anything the function raised arrives here, including the
            # deterministic language failure the caller has to tell apart from
            # an infrastructure fault. The type name goes in front of the
            # message because that is the only part that reliably says which of
            # the two it is; `str(error)` alone leaves the caller guessing from
            # wording.
            return Failed(f"{type(error).__name__}: {error}")
        return Done(TranscribeRaw.model_validate(payload))

    async def progress(self, handle: str) -> TranscriptionProgress | None:
        """The function's own progress note; a missing one is not a fault.

        Progress is cosmetic. A Dict that has expired, or a read that fails
        while the transcription is healthy, must not fail the activity.
        """
        try:
            payload = await _progress_dict(self.settings).get.aio(handle)
        except Exception:  # noqa: BLE001
            return None
        if payload is None:
            return None
        return TranscriptionProgress.model_validate(payload)
