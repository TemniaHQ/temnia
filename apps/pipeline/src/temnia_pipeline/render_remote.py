"""Topic video rendering on the Modal GPU: the job contract, the client seam, the runner.

The worker keeps the exact-interval semantics (`render_chapter`), verification and
publication; the card does the decode and encode. One call renders every missing section
of a revision from one download of the master, and its outputs land under the run's render
prefix in the store. The call id rides on the activity's heartbeat so a worker restart
reattaches instead of paying for a second render.
"""

# ruff: noqa: EM102, TRY003

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from obstore import get_async, head_async
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.exceptions import ApplicationError

from temnia_pipeline.modal_errors import transport_errors
from temnia_pipeline.modal_protocol import RemoteFailure, read_outcome
from temnia_pipeline.transcode.modal_client import (
    Done,
    Failed,
    Running,
    Unknown,
    Unreachable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.settings import TranscodeSettings

log = logging.getLogger("temnia.render")

RENDER_FUNCTION = "render_sections"
DEFAULT_RENDER_PROGRESS_DICT = "temnia-render-progress"
POLL_SECONDS = 10.0
UNREACHABLE_TICKS = 18
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


ProgressCallback = Callable[[RenderProgress, str], Awaitable[None]]


class RenderClient(Protocol):
    """What the runner needs from Modal, and all it may know about it."""

    async def spawn(self, job: RenderJob) -> str:
        """Start the deployed render function; returns the call id."""
        ...

    async def status(self, call_id: str) -> Done | Failed | Running | Unknown | Unreachable:
        """Poll one call without waiting for it."""
        ...

    async def progress(self, call_id: str) -> RenderProgress | None:
        """The last progress note the call wrote, if any."""
        ...


@dataclass(frozen=True, slots=True)
class RenderDone:
    """The call returned its outputs."""

    result: RenderResult


def _function(settings: TranscodeSettings) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        settings.modal_app, RENDER_FUNCTION, environment_name=settings.modal_environment
    )


class RealRenderClient:
    """`RenderClient` over the Modal SDK; untested until staging by design, like the ladder's."""

    def __init__(self, settings: TranscodeSettings, progress_dict: str) -> None:
        self.settings = settings
        self.progress_dict = progress_dict

    async def spawn(self, job: RenderJob) -> str:
        """Spawn rather than call: a call id survives a worker restart."""
        call = await _function(self.settings).spawn.aio(job.model_dump(mode="json"))
        return str(call.object_id)

    async def status(self, call_id: str) -> Done | Failed | Running | Unknown | Unreachable:  # noqa: PLR0911
        """Poll with a zero timeout: still running is a TimeoutError, not an error."""
        import modal  # noqa: PLC0415
        from modal.exception import NotFoundError, OutputExpiredError  # noqa: PLC0415

        try:
            payload = await modal.FunctionCall.from_id(call_id).get.aio(timeout=0)
        except TimeoutError:
            return Running()
        except (NotFoundError, OutputExpiredError):
            return Unknown()
        except transport_errors() as error:
            return Unreachable(f"{type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001
            return Failed(f"{type(error).__name__}: {error}")
        try:
            outcome = read_outcome(payload)
            if isinstance(outcome, RemoteFailure):
                return Failed(outcome.description)
            return Done(RenderResult.model_validate(outcome.payload))  # type: ignore[arg-type]
        except ValidationError:
            return Failed(
                "RemoteProtocolError: invalid or incompatible Modal render result; "
                "redeploy app and worker together"
            )

    async def progress(self, call_id: str) -> RenderProgress | None:
        """Read the call's own note; a missing or expired note is not a fault."""
        import modal  # noqa: PLC0415

        try:
            notes = modal.Dict.from_name(
                self.progress_dict,
                create_if_missing=True,
                environment_name=self.settings.modal_environment,
            )
            payload = await notes.get.aio(call_id)
        except Exception:  # noqa: BLE001
            return None
        if payload is None:
            return None
        return RenderProgress.model_validate(payload)


def render_failure(message: str) -> ApplicationError:
    """A render that cannot succeed by retrying: the encoder refused the interval."""
    return ApplicationError(message, type="RenderFailure", non_retryable=True)


def modal_failure(message: str) -> ApplicationError:
    """Modal itself misbehaved; the activity retries and reattaches by call id."""
    return ApplicationError(message, type="ModalFailure", non_retryable=False)


def classify(message: str) -> ApplicationError:
    """Ffmpeg's own refusals are terminal; everything else is Modal's to retry."""
    lowered = message.lower()
    if "ffmpeg" in lowered or "truncated" in lowered or "interval" in lowered:
        return render_failure(message)
    return modal_failure(message)


class ModalRenderer:
    """Spawn or reattach one render call, poll it, then verify every output in the store."""

    def __init__(
        self, client: RenderClient, store: S3Store, poll_seconds: float = POLL_SECONDS
    ) -> None:
        self.client = client
        self.store = store
        self.poll_seconds = poll_seconds

    async def run(
        self, job: RenderJob, *, on_progress: ProgressCallback, resume: str | None = None
    ) -> RenderResult:
        """Reattach or spawn, poll until the call ends, then verify what it published."""
        call_id = await self._attach(job, on_progress, resume)
        latest = RenderProgress(stage="download", percent=0)
        unreachable = 0
        while True:
            note = await self.client.progress(call_id)
            if note is not None:
                latest = note
            await on_progress(latest, call_id)
            status = await self.client.status(call_id)
            match status:
                case Done(result=result):
                    verified = await self._verify(job, RenderResult.model_validate(result))
                    return verified.model_copy(update={"call_id": verified.call_id or call_id})
                case Failed(message=message):
                    raise classify(message)
                case Unknown():
                    raise modal_failure(f"Modal has no record of render call {call_id}")
                case Unreachable(message=message):
                    unreachable += 1
                    if unreachable >= UNREACHABLE_TICKS:
                        raise modal_failure(
                            f"Modal was unreachable for {unreachable} polls of render call "
                            f"{call_id}: {message}"
                        )
                    log.warning("render call %s unreachable (%s); polling on", call_id, message)
                    await asyncio.sleep(self.poll_seconds)
                case Running():
                    unreachable = 0
                    await asyncio.sleep(self.poll_seconds)

    async def _attach(
        self, job: RenderJob, on_progress: ProgressCallback, resume: str | None
    ) -> str:
        if resume is None:
            return await self.client.spawn(job)
        await on_progress(RenderProgress(stage="download", percent=0), resume)
        status = await self.client.status(resume)
        match status:
            case Running() | Done():
                log.info("reattaching to render call %s", resume)
                return resume
            case Failed(message=message) if classify(message).non_retryable:
                raise classify(message)
            case Unreachable(message=message):
                raise modal_failure(
                    f"could not reach Modal to check render call {resume}: {message}"
                )
            case _:
                log.info("render call %s is %s; spawning a new one", resume, type(status).__name__)
                return await self.client.spawn(job)

    async def _verify(self, job: RenderJob, result: RenderResult) -> RenderResult:
        """Every section's file must be in the store with the size and hash the call reported."""
        wanted = {section.section_id: section.output_key for section in job.sections}
        reported = {output.section_id: output for output in result.outputs}
        if set(wanted) != set(reported):
            message = "the render call did not report every requested section"
            raise render_failure(message)
        for section_id, output in reported.items():
            if output.output_key != wanted[section_id]:
                raise render_failure(f"section {section_id} was published under another key")
            meta = await head_async(self.store, output.output_key)
            if int(meta["size"]) != output.size_bytes:  # pyright: ignore[reportUnknownArgumentType]
                raise render_failure(f"section {section_id} is short in the store")
        return result


async def download_output(store: S3Store, output: RenderOutput, destination: Path) -> None:
    """Fetch one rendered section and refuse it unless its bytes hash as reported."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    result = await get_async(store, output.output_key)
    with destination.open("wb") as handle:
        async for chunk in result:
            handle.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != output.sha256:
        raise render_failure(f"section {output.section_id} bytes differ from the reported hash")
