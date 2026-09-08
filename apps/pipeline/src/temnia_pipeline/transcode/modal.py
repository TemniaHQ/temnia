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

from temnia_pipeline.transcode import (
    CONTRACT_VERSION,
    LadderJob,
    LadderProgress,
    LadderResult,
    stored_ladder,
)
from temnia_pipeline.transcode.modal_client import Done, Failed, Running, Unknown, Unreachable

if TYPE_CHECKING:
    from obstore.store import S3Store

    from temnia_pipeline.settings import TranscodeSettings
    from temnia_pipeline.transcode import ProgressCallback
    from temnia_pipeline.transcode.modal_client import ModalClient

log = logging.getLogger("temnia.transcode.modal")

POLL_SECONDS = 10.0

# Consecutive polls Modal may be unreachable for before the attempt gives up
# and lets Temporal retry it: three minutes at the ten-second tick, inside the
# five-minute heartbeat timeout. Every tick still heartbeats the call id, so
# the retry reattaches to the same call and never spawns another.
UNREACHABLE_TICKS = 18

# The exceptions that mean the encode itself is wrong, not the infrastructure.
# A source ffmpeg cannot read, or an output that came out short, fails the same
# way on the next container and on the next worker, so it is terminal on
# attempt one. `RealModalClient.status` puts the type name in front of the
# message, which is what makes this a rule about the exception rather than
# about its wording.
TERMINAL_TYPES = ("FfmpegError", "TruncatedOutputError", "RemoteProtocolError")
_TERMINAL_PREFIXES = tuple(f"{name}:" for name in TERMINAL_TYPES)

# The fallback, for a message that reached us without a type name in front of
# it: an older deployment, or a failure Modal itself worded.
TERMINAL_MARKERS = ("ffmpeg", "truncated")


class DeploymentError(RuntimeError):
    """The deployed app is missing, unreachable, or speaks a different contract."""


def _where(settings: TranscodeSettings) -> str:
    app = f"Modal app {settings.modal_app!r}"
    if settings.modal_environment is None:
        return app
    return f"{app} in environment {settings.modal_environment!r}"


async def assert_deployment(client: ModalClient, settings: TranscodeSettings) -> None:
    """Refuse to boot against a Modal app that is absent or out of step.

    The failure has to be at boot. A worker that starts with a bad token and
    only discovers it on the first source turns a wrong environment variable
    into a silent queue; Dokploy rolls back a container that exits non-zero,
    which is the behaviour we want. Same reason as the database probe.
    """
    try:
        deployed = await client.version()
    except Exception as error:
        msg = (
            f"cannot reach the {_where(settings)}: {error}. Check MODAL_TOKEN_ID and "
            "MODAL_TOKEN_SECRET, and that `uv run modal deploy temnia_pipeline.modal_app` "
            "has run for this environment."
        )
        raise DeploymentError(msg) from error
    if deployed != CONTRACT_VERSION:
        msg = (
            f"the {_where(settings)} speaks media contract {deployed!r} and this worker "
            f"speaks {CONTRACT_VERSION!r}. Deploy the Modal app and the pipeline image "
            "from the same commit."
        )
        raise DeploymentError(msg)


def transcode_failure(message: str) -> ApplicationError:
    """A deterministic ladder failure: terminal, and the message is safe to show."""
    return ApplicationError(message, non_retryable=True, type="TranscodeFailure")


def modal_failure(message: str) -> ApplicationError:
    """An infrastructure failure: retryable under the activity's own policy."""
    return ApplicationError(message, type="ModalFailure")


def classify(message: str) -> ApplicationError:
    """Terminal when the encode itself is wrong, retryable otherwise.

    The exception type decides first, because the wording cannot be trusted to
    carry the answer: a `TruncatedOutputError` that says only which playlist is
    short would read as infrastructure and be retried three times at GPU
    prices. The word markers stay behind it as a fallback.
    """
    if message.startswith(_TERMINAL_PREFIXES):
        return transcode_failure(message)
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

    async def reuse(
        self,
        job: LadderJob,
        *,
        on_progress: ProgressCallback | None = None,
        resume: str | None = None,
    ) -> LadderResult | None:
        """A ladder already published under this prefix, or None."""
        return await stored_ladder(self.store, job, on_progress=on_progress, resume=resume)

    async def run(
        self, job: LadderJob, *, on_progress: ProgressCallback, resume: str | None = None
    ) -> LadderResult:
        """Reattach or spawn, then poll until the call ends, reporting progress."""
        published = await self.reuse(job, on_progress=on_progress, resume=resume)
        if published is not None:
            log.info("ladder already published under %s; skipping Modal", job.hls_prefix)
            return published
        call_id = await self._attach(job, on_progress, resume)
        return await self._poll(job, call_id, on_progress)

    async def _attach(
        self, job: LadderJob, on_progress: ProgressCallback, resume: str | None
    ) -> str:
        """Keep an earlier attempt's call when it may still be alive, otherwise spawn.

        The call id is heartbeated before Modal is asked anything, so an
        attempt that fails on its first status read still hands the id to the
        attempt after it. Only Modal's own word that the call is gone
        (`Unknown`) or over (`Failed`) spawns another; not being able to ask
        is a retry of this attempt, never a second GPU job.
        """
        if resume is None:
            return await self.client.spawn(job)
        await on_progress(LadderProgress(stage="hls", percent=0), resume)
        status = await self.client.status(resume)
        match status:
            case Running() | Done():
                log.info("reattaching to Modal call %s", resume)
                return resume
            case Failed(message=message) if classify(message).non_retryable:
                raise classify(message)
            case Unreachable(message=message):
                msg = f"could not reach Modal to check call {resume}: {message}"
                raise modal_failure(msg)
            case _:
                # A failed or forgotten call has nothing to wait for. Spawning
                # again is safe: the function writes under one prefix and the
                # manifest goes up last, so the worst case is work done twice,
                # never a half ladder that looks whole.
                log.info(
                    "Modal call %s is %s; spawning a new one",
                    resume,
                    type(status).__name__.lower(),
                )
                return await self.client.spawn(job)

    async def _poll(
        self, job: LadderJob, call_id: str, on_progress: ProgressCallback
    ) -> LadderResult:
        # Every tick reports, whether or not the container has written a note.
        # `on_progress` is the only place the activity heartbeats and the only
        # way the call id reaches the next attempt, so a tick that stays quiet
        # is a tick that spends the heartbeat timeout: an L4 can take minutes
        # to schedule and cold start before its first note, and a Dict read can
        # fail while the encode is perfectly healthy. Either silence would time
        # the activity out and hand the retry no call id to reattach to, which
        # is the second GPU job this whole design exists to avoid. Repeating a
        # note is free; the activity throttles its own database write.
        latest = LadderProgress(stage="hls", percent=0)
        unreachable = 0
        while True:
            note = await self.client.progress(call_id)
            if note is not None:
                latest = note
            await on_progress(latest, call_id)
            status = await self.client.status(call_id)
            match status:
                case Done(result=result):
                    return await self._verify(job, call_id, result, on_progress)
                case Failed(message=message):
                    raise classify(message)
                case Unknown():
                    msg = f"Modal has no record of call {call_id}"
                    raise modal_failure(msg)
                case Unreachable(message=message):
                    unreachable += 1
                    if unreachable >= UNREACHABLE_TICKS:
                        msg = (
                            f"Modal was unreachable for {unreachable} polls of call "
                            f"{call_id}: {message}"
                        )
                        raise modal_failure(msg)
                    log.warning("Modal call %s unreachable (%s); polling on", call_id, message)
                    await asyncio.sleep(self.poll_seconds)
                case Running():
                    unreachable = 0
                    await asyncio.sleep(self.poll_seconds)

    async def _verify(
        self, job: LadderJob, call_id: str, result: LadderResult, on_progress: ProgressCallback
    ) -> LadderResult:
        """Trust the storage, not the return value.

        The function asserts every playlist before it uploads, and this reads
        the manifest back out of the store afterwards. Two checks of the same
        thing, because the one failure that reached users in the legacy was an
        encode that reported success on a truncated output.
        """
        published = await self.reuse(job, on_progress=on_progress, resume=call_id)
        if published is None:
            msg = (
                f"the ladder from Modal call {call_id} is not complete in storage: "
                f"{job.manifest_key} is missing, short, or without its master playlist"
            )
            raise transcode_failure(msg)
        return published.model_copy(update={"call_id": result.call_id or call_id})
