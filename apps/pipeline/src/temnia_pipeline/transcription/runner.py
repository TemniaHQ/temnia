"""Drive a provider: reattach or start, then poll until the run ends.

The loop is above the seam because both providers are polled the same way and
only the thing being polled differs. What lives here is the part with the
consequences: which failures are terminal, and the rule that every tick
reports, so the activity has something to heartbeat and the next attempt has a
handle to reattach to.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from temporalio.exceptions import ApplicationError

from temnia_pipeline.transcription import Done, Failed, Running, TranscriptionProgress, Unknown

if TYPE_CHECKING:
    from temnia_pipeline.transcription import (
        TranscribeJob,
        TranscribeRaw,
        TranscriptionProvider,
    )

log = logging.getLogger("temnia.transcription.runner")

POLL_SECONDS = 10.0
# The recorded provider answers from memory; a ten-second tick would spend the
# gate's minutes waiting for a file it already holds.
RECORDED_POLL_SECONDS = 0.02

# Failures that mean the recording or the request is wrong rather than the
# infrastructure. They fail the same way on the next container and the next
# worker, so they are terminal on attempt one instead of being retried three
# times at GPU prices. The provider puts the exception's type name in front of
# its message, which is what makes this a rule about the exception rather than
# about its wording.
TERMINAL_TYPES = (
    "UnsupportedLanguageError",
    "RecordingNotFoundError",
    "TranscriptContractError",
    "InvalidMediaError",
    "ValidationError",
)
_TERMINAL_PREFIXES = tuple(f"{name}:" for name in TERMINAL_TYPES)

# The handle rides in the activity's heartbeat under this key, which is where
# a retried attempt reads it from.
CALL_ID = "call_id"

ProgressCallback = Callable[[TranscriptionProgress, str | None], Awaitable[None]]


def transcription_failure(message: str) -> ApplicationError:
    """A deterministic transcription failure: terminal, and safe to show."""
    return ApplicationError(message, non_retryable=True, type="TranscriptionFailure")


def provider_failure(message: str) -> ApplicationError:
    """An infrastructure failure: retryable under the activity's own policy."""
    return ApplicationError(message, type="TranscriptionProviderFailure")


def classify(message: str) -> ApplicationError:
    """Terminal when the request is wrong, retryable otherwise."""
    if message.startswith(_TERMINAL_PREFIXES):
        return transcription_failure(message)
    return provider_failure(message)


def classify_exception(error: Exception) -> ApplicationError:
    """The same rule for an exception raised in this process rather than on a GPU."""
    return classify(f"{type(error).__name__}: {error}")


class TranscriptionRunner:
    """Start or reattach, then poll one run to its end."""

    def __init__(self, provider: TranscriptionProvider, poll_seconds: float = POLL_SECONDS) -> None:
        self.provider = provider
        self.poll_seconds = poll_seconds

    async def run(
        self, job: TranscribeJob, *, on_progress: ProgressCallback, resume: str | None = None
    ) -> TranscribeRaw:
        """Produce the engine's response for this job."""
        handle = await self._attach(job, resume)
        return await self._poll(handle, on_progress)

    async def _attach(self, job: TranscribeJob, resume: str | None) -> str:
        """Keep an earlier attempt's run when it is still alive, otherwise start one."""
        if resume is None:
            return await self._start(job)
        try:
            status = await self.provider.status(resume)
        except Exception as error:  # noqa: BLE001
            log.warning("could not poll %s (%s); starting again", resume, error)
            return await self._start(job)
        if isinstance(status, Running | Done):
            log.info("reattaching to transcription %s", resume)
            return resume
        log.info(
            "transcription %s is %s; starting a new run", resume, type(status).__name__.lower()
        )
        return await self._start(job)

    async def _start(self, job: TranscribeJob) -> str:
        try:
            return await self.provider.start(job)
        except ApplicationError:
            raise
        except Exception as error:
            raise classify_exception(error) from error

    async def _poll(self, handle: str, on_progress: ProgressCallback) -> TranscribeRaw:
        # Every tick reports, whether or not the run has written a note.
        # `on_progress` is the only place the activity heartbeats and the only
        # way the handle reaches the next attempt, so a tick that stays quiet
        # is a tick spending the heartbeat timeout: an L4 can take minutes to
        # schedule and cold start before its first note, and a Dict read can
        # fail while the transcription is perfectly healthy. Either silence
        # would time the activity out and hand the retry no handle, which is
        # the second GPU job this design exists to avoid.
        latest = TranscriptionProgress(stage="model", percent=0)
        while True:
            note = await self.provider.progress(handle)
            if note is not None:
                latest = note
            await on_progress(latest, handle)
            status = await self.provider.status(handle)
            match status:
                case Done(raw=raw):
                    return raw
                case Failed(message=message):
                    raise classify(message)
                case Unknown():
                    msg = f"the provider has no record of transcription {handle}"
                    raise provider_failure(msg)
                case Running():
                    await asyncio.sleep(self.poll_seconds)
