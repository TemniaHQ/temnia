"""Replay a real WhisperX response instead of calling a GPU.

This is what `pnpm worker` and the gate run. It is a recording, not a mock: a
synthetic three-sentence answer would agree with whatever the normaliser
happens to do, and the shapes that actually break it (a word with no
timestamps, a NaN score, an empty segment, a word whose speaker only the
enclosing segment knows) only appear in something the engine really produced.

Selecting a recording:

1. `TRANSCRIPTION_RECORDING`, an explicit file, wins. The gate uses it,
   because a recording keyed by the audio's checksum is not reproducible: the
   extract is re-encoded by whichever ffmpeg the image carries, and a bumped
   ffmpeg would change the checksum and silently stop matching.
2. otherwise the audio object is fetched and hashed, and the recordings
   directory is searched for a file whose `_audioSha256` is that hash. That is
   the path for recording against a specific stored object by hand.

A run with no recording fails loudly, naming the checksum and the directory,
because the alternative is a gate that passes with no transcript at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import obstore as obs

from temnia_pipeline.transcription import (
    Done,
    Running,
    TranscribeJob,
    TranscribeRaw,
    TranscriptionProgress,
    Unknown,
)

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.settings import TranscriptionSettings
    from temnia_pipeline.transcription import RunStatus

log = logging.getLogger("temnia.transcription.recorded")

NAME = "recorded"
MODEL = "recorded"
VERSION = "1"
AUDIO_SHA_FIELD = "_audioSha256"

# The stages a run walks through, in order. The recorded provider hands out one
# per poll so the surface, the progress row, and the Playwright specs all see a
# real sequence rather than a jump from queued to ready.
STAGES = ("download", "model", "transcribe", "align", "diarize", "write")


class RecordingNotFoundError(FileNotFoundError):
    """No recording matches this audio; deterministic, so terminal."""


def _load(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text())
    if not isinstance(loaded, dict):
        msg = f"{path} is not a WhisperX response object"
        raise RecordingNotFoundError(msg)
    return loaded  # pyright: ignore[reportUnknownVariableType]


def find_recording(settings: TranscriptionSettings, audio_sha256: str) -> dict[str, Any]:
    """The recording for this audio, by explicit path or by checksum."""
    if settings.recording is not None:
        if not settings.recording.exists():
            msg = f"TRANSCRIPTION_RECORDING points at {settings.recording}, which does not exist"
            raise RecordingNotFoundError(msg)
        return _load(settings.recording)
    directory = settings.recordings_dir
    if directory.is_dir():
        for candidate in sorted(directory.glob("*.json")):
            recorded = _load(candidate)
            if recorded.get(AUDIO_SHA_FIELD) == audio_sha256:
                log.info("replaying %s for audio %s", candidate.name, audio_sha256[:12])
                return recorded
    msg = (
        f"no recorded transcription for audio sha256 {audio_sha256}. Add a WhisperX "
        f'response with "{AUDIO_SHA_FIELD}": "{audio_sha256}" to {directory}, or set '
        "TRANSCRIPTION_RECORDING to one file."
    )
    raise RecordingNotFoundError(msg)


async def audio_sha256(store: S3Store, key: str) -> str:
    """Stream the audio object and hash it.

    Fetched even when a path override has already chosen the recording: it is
    the one check that ingest really produced the extract this job names, and
    it is how the checksum to name a new recording by is discovered.
    """
    result = await obs.get_async(store, key)
    digest = hashlib.sha256()
    async for chunk in result.stream(min_chunk_size=8 * 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


class _Run:
    """One replayed run: where it has got to, and what it will answer with."""

    __slots__ = ("polls", "raw", "started")

    def __init__(self, raw: TranscribeRaw) -> None:
        self.raw = raw
        self.polls = 0
        self.started = time.monotonic()


class RecordedProvider:
    """A `TranscriptionProvider` that replays a recorded response."""

    def __init__(self, settings: TranscriptionSettings, store: S3Store) -> None:
        self.settings = settings
        self.store = store
        self._runs: dict[str, _Run] = {}

    @property
    def name(self) -> str:
        """The engine's name as recorded on the revision."""
        return NAME

    @property
    def model(self) -> str:
        """Named `recorded` on purpose: a revision must never claim it came from a GPU."""
        return MODEL

    @property
    def version(self) -> str:
        """The replay format's version."""
        return VERSION

    async def start(self, job: TranscribeJob) -> str:
        """Fetch and hash the audio, find its recording, and hold it for the poller."""
        sha = await audio_sha256(self.store, job.audio_key)
        recorded = find_recording(self.settings, sha)
        language = recorded.get("language")
        raw = TranscribeRaw(
            raw=recorded,
            raw_key=None,
            language=str(language) if isinstance(language, str) and language else "en",
        )
        handle = f"rec-{job.attempt}-{sha[:16]}"
        self._runs[handle] = _Run(raw)
        return handle

    async def status(self, handle: str) -> RunStatus:
        """Running for one poll per stage, then the recording."""
        run = self._runs.get(handle)
        if run is None:
            # A worker restart loses the in-memory run. Unknown is the honest
            # answer, and it makes the activity start over, which is free here.
            return Unknown()
        if run.polls < len(STAGES):
            run.polls += 1
            return Running()
        return Done(run.raw)

    async def progress(self, handle: str) -> TranscriptionProgress | None:
        """One stage per poll, so every state the surface renders is exercised."""
        run = self._runs.get(handle)
        if run is None:
            return None
        stage = STAGES[min(run.polls, len(STAGES) - 1)]
        percent = int(100 * min(run.polls + 1, len(STAGES)) / len(STAGES))
        return TranscriptionProgress(stage=stage, percent=percent)
