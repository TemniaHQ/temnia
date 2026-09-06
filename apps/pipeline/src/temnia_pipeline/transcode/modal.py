"""The ladder on a Modal L4, driven from the worker by call id.

Spawn, then poll. A blocking remote call would tie fifty minutes of GPU work
to one TCP connection and one worker process; a call id is a handle that
outlives both, so a worker restarted mid-ladder reattaches to the encode
already running instead of paying for it twice. The id reaches the retry
through the activity's heartbeat details.

The function publishes the ladder to storage itself, which is why the worker
verifies the result by reading it back: on the S1 exit run the publish was 21
of the VPS's 67 minutes, and the bytes never touching this box is most of what
the move to Modal buys.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from temporalio.exceptions import ApplicationError

from temnia_pipeline.transcode import LadderJob, LadderResult, stored_ladder
from temnia_pipeline.transcode.modal_client import Done, Failed, Running, Unknown

if TYPE_CHECKING:
    from obstore.store import S3Store

    from temnia_pipeline.transcode import ProgressCallback
    from temnia_pipeline.transcode.modal_client import ModalClient

log = logging.getLogger("temnia.transcode.modal")

POLL_SECONDS = 10.0

# Words that mean the encode itself is wrong, not the infrastructure. A source
# ffmpeg cannot read, or an output that came out short, fails the same way on
# the next container and on the next worker, so it is terminal on attempt one.
TERMINAL_MARKERS = ("ffmpeg", "truncated")


class ModalFailureError(RuntimeError):
    """Modal could not run the call; another attempt is worth making."""


def transcode_failure(message: str) -> ApplicationError:
    """A deterministic ladder failure: terminal, and the message is safe to show."""
    return ApplicationError(message, non_retryable=True, type="TranscodeFailure")


def modal_failure(message: str) -> ApplicationError:
    """An infrastructure failure: retryable under the activity's own policy."""
    return ApplicationError(message, type="ModalFailure")


def classify(message: str) -> ApplicationError:
    """Terminal when the message blames the encode, retryable otherwise."""
    lowered = message.lower()
    if any(marker in lowered for marker in TERMINAL_MARKERS):
        return transcode_failure(message)
    return modal_failure(message)


class ModalTranscoder:
    """Run the ladder on Modal and verify what it published."""

    def __init__(
        self, client: ModalClient, store: S3Store, poll_seconds: float = POLL_SECONDS
    ) -> None:
        self.client = client
        self.store = store
        self.poll_seconds = poll_seconds

    async def reuse(self, job: LadderJob) -> LadderResult | None:
        """A ladder already published under this prefix, or None."""
        return await stored_ladder(self.store, job)

    async def run(
        self, job: LadderJob, *, on_progress: ProgressCallback, resume: str | None = None
    ) -> LadderResult:
        """Reattach or spawn, then poll until the call ends, reporting progress."""
        published = await self.reuse(job)
        if published is not None:
            log.info("ladder already published under %s; skipping Modal", job.hls_prefix)
            return published
        call_id = await self._attach(job, resume)
        return await self._poll(job, call_id, on_progress)

    async def _attach(self, job: LadderJob, resume: str | None) -> str:
        """Keep an earlier attempt's call when it is still alive, otherwise spawn."""
        if resume is None:
            return await self.client.spawn(job)
        status = await self.client.status(resume)
        if isinstance(status, Running | Done):
            log.info("reattaching to Modal call %s", resume)
            return resume
        # A failed or forgotten call has nothing to wait for. Spawning again is
        # safe: the function writes under one prefix and the manifest goes up
        # last, so the worst case is work done twice, never a half ladder that
        # looks whole.
        log.info("Modal call %s is %s; spawning a new one", resume, type(status).__name__.lower())
        return await self.client.spawn(job)

    async def _poll(
        self, job: LadderJob, call_id: str, on_progress: ProgressCallback
    ) -> LadderResult:
        while True:
            progress = await self.client.progress(call_id)
            if progress is not None:
                await on_progress(progress, call_id)
            status = await self.client.status(call_id)
            match status:
                case Done(result=result):
                    return await self._verify(job, call_id, result)
                case Failed(message=message):
                    raise classify(message)
                case Unknown():
                    msg = f"Modal has no record of call {call_id}"
                    raise modal_failure(msg)
                case Running():
                    await asyncio.sleep(self.poll_seconds)

    async def _verify(self, job: LadderJob, call_id: str, result: LadderResult) -> LadderResult:
        """Trust the storage, not the return value.

        The function asserts every playlist before it uploads, and this reads
        the manifest back out of the store afterwards. Two checks of the same
        thing, because the one failure that reached users in the legacy was an
        encode that reported success on a truncated output.
        """
        published = await self.reuse(job)
        if published is None:
            msg = (
                f"the ladder from Modal call {call_id} is not complete in storage: "
                f"{job.manifest_key} is missing, short, or without its master playlist"
            )
            raise transcode_failure(msg)
        return published.model_copy(update={"call_id": result.call_id or call_id})
