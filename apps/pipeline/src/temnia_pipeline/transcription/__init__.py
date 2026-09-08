"""The seam transcription runs behind: WhisperX on a Modal GPU, or a recording.

The workflow, the database rows, and the UI cannot tell the two apart. A
provider is started, polled, and asked for progress; what it eventually hands
back is the engine's own untouched response, which the normaliser turns into
the one shape the rest of Temnia knows (`TranscriptV1`).

Nothing in this module may import temporalio, psycopg, obstore, or the modal
SDK. Two callers depend on that: the deployed Modal function imports it to
read a job and build a result inside an image that carries none of them, and
`workflows.py` imports the result models through the Temporal sandbox, which
refuses a workflow module that reaches a database or a store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from collections.abc import Collection

TRANSCRIPT_SUBDIR = "transcript/"

_WIRE = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class UnsupportedLanguageError(Exception):
    """The engine detected a language it has no alignment model for.

    Deterministic: the same audio fails the same way on the next container, so
    it is terminal on attempt one and the code is shown to the user.
    """


def assert_alignable(language: str, available: Collection[str]) -> None:
    """Refuse a language no alignment model covers, naming the code.

    `available` is whatever the engine itself says it can align, never a list
    copied into this repository: whisperx's alignment table changes between
    releases, and a stale copy here would refuse a language the deployed
    version handles perfectly well, or promise one it does not.
    """
    if language in available:
        return
    msg = (
        f"this recording is in a language we cannot align yet (code: {language}). "
        f"{len(available)} languages are supported by the deployed model."
    )
    raise UnsupportedLanguageError(msg)


class TranscribeJob(BaseModel):
    """Everything a transcription needs, and nothing that identifies a tenant.

    The Modal function is scope-blind compute: the worker is the only authority
    on which organization a prefix belongs to, and hands over a prefix it has
    already decided.
    """

    model_config = _WIRE

    audio_key: str
    artifact_prefix: str
    attempt: int
    duration_ms: int

    @property
    def raw_key(self) -> str:
        """Where this attempt's untouched engine response is kept.

        One key per attempt, never overwritten: it is the record a fixture is
        cut from and what S12's calibration round reads back.
        """
        return f"{self.artifact_prefix}{TRANSCRIPT_SUBDIR}raw-{self.attempt}.json"


class TranscribeRaw(BaseModel):
    """What a provider hands back when a run finishes.

    `raw` is the engine's own response, unaltered. It is several megabytes for
    a long episode, so it never crosses the Temporal boundary: the activity
    writes it to `raw_key` and passes the key on.
    """

    model_config = _WIRE

    raw: dict[str, Any]
    raw_key: str | None = None
    language: str
    gpu_seconds: float = 0.0
    gpu: str = "none"


class TranscribeRecord(BaseModel):
    """The small record the transcribe activity returns to the workflow."""

    model_config = _WIRE

    raw_key: str
    language: str
    gpu_seconds: float
    gpu: str
    attempt: int
    call_id: str | None = None


class TranscriptionProgress(BaseModel):
    """How far a run has got, in the stages the surface shows."""

    model_config = _WIRE

    stage: str
    percent: int = Field(ge=0, le=100)


@dataclass(frozen=True, slots=True)
class Running:
    """The run is still going."""


@dataclass(frozen=True, slots=True)
class Done:
    """The run finished; `raw` is the engine's own response."""

    raw: TranscribeRaw


@dataclass(frozen=True, slots=True)
class Failed:
    """The run raised; the message is `TypeName: text` and decides the retry."""

    message: str


@dataclass(frozen=True, slots=True)
class Unreachable:
    """The provider could not be asked; nothing is known about the run.

    A connection that dropped, a token the API refused, a stream that ended:
    the run may be healthy on its GPU. Never a `Failed`, because a runner that
    treated it as one started a second run beside the first (S2 review, I05).
    """

    message: str


@dataclass(frozen=True, slots=True)
class Unknown:
    """The provider has never heard of this handle, or its result has expired."""


RunStatus = Running | Done | Failed | Unknown | Unreachable


class TranscriptionProvider(Protocol):
    """What the transcribe activity needs, and all it may know about an engine.

    Start returns a handle rather than a result, for the same reason the ladder
    spawns: a handle outlives the TCP connection and the worker process, so a
    worker restarted mid-run reattaches to the transcription already on a GPU
    instead of paying for it twice.
    """

    @property
    def name(self) -> str:
        """The engine's name, recorded on the revision."""
        ...

    @property
    def model(self) -> str:
        """The model the engine ran."""
        ...

    @property
    def version(self) -> str:
        """The engine's version, so a re-run is comparable."""
        ...

    async def start(self, job: TranscribeJob) -> str:
        """Begin a run; returns the handle to poll."""
        ...

    async def status(self, handle: str) -> RunStatus:
        """Poll one run without waiting for it."""
        ...

    async def progress(self, handle: str) -> TranscriptionProgress | None:
        """The last progress note the run wrote, if it has written any."""
        ...
