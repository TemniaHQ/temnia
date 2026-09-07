"""The provider seam: how a recording is found, and how a run is driven.

The Modal provider itself is untested until staging by design (there is no
account on the build machine, and a mock of a network client proves nothing
about the network). What is tested here is the worker's half: reattach,
classification, the rule that every tick reports, and the settings that decide
which provider is built at all.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

import pytest
from temporalio.exceptions import ApplicationError

from temnia_pipeline.settings import (
    DEFAULT_TRANSCRIPT_DICT,
    PipelineSettings,
    TranscriptionSettings,
)
from temnia_pipeline.transcription import (
    Done,
    Failed,
    Running,
    TranscribeJob,
    TranscribeRaw,
    TranscriptionProgress,
    Unknown,
)
from temnia_pipeline.transcription.factory import make_transcription
from temnia_pipeline.transcription.recorded import (
    STAGES,
    RecordedProvider,
    RecordingNotFoundError,
    find_recording,
)
from temnia_pipeline.transcription.runner import (
    RECORDED_POLL_SECONDS,
    TranscriptionRunner,
    classify,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.transcription import RunStatus

PREFIX = "org/0192e8a0-0000-7000-8000-000000000001/source/0192e8a0-0000-7000-8000-0000000000aa/"
AUDIO = b"not really audio, but the provider only hashes it"
AUDIO_SHA = hashlib.sha256(AUDIO).hexdigest()

JOB = TranscribeJob(
    audio_key=PREFIX + "audio/audio.m4a",
    artifact_prefix=PREFIX,
    attempt=1,
    duration_ms=40_116,
)

ENVIRONMENT = (
    "TRANSCRIPTION_PROVIDER",
    "TRANSCRIPTION_RECORDING",
    "TRANSCRIPTION_RECORDINGS_DIR",
    "MODAL_APP",
    "MODAL_ENVIRONMENT",
    "MODAL_TRANSCRIPT_PROGRESS_DICT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # a pytest fixture is called by name, not by reference
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def settings(directory: Path, recording: Path | None = None) -> TranscriptionSettings:
    return TranscriptionSettings(
        provider="recorded",
        modal_app="temnia-media",
        modal_environment=None,
        progress_dict=DEFAULT_TRANSCRIPT_DICT,
        recordings_dir=directory,
        recording=recording,
    )


def write_recording(directory: Path, name: str, sha: str | None, language: str = "en") -> Path:
    path = directory / name
    path.write_text(
        json.dumps(
            {
                "_audioSha256": sha,
                "language": language,
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "SPEAKER_00",
                        "words": [
                            {"word": "hello", "start": 0.0, "end": 1.0, "score": 0.9},
                        ],
                    }
                ],
            }
        )
    )
    return path


class FakeStore:
    """The one read the recorded provider makes."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects


class _Result:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def stream(self, min_chunk_size: int = 0):  # noqa: ANN202, ARG002
        yield self.payload


async def _get_async(store: S3Store, key: str) -> _Result:
    objects = cast("FakeStore", store).objects
    if key not in objects:
        raise FileNotFoundError(key)
    return _Result(objects[key])


@pytest.fixture(autouse=True)
def _fake_storage(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # a pytest fixture is called by name, not by reference
    monkeypatch.setattr("temnia_pipeline.transcription.recorded.obs.get_async", _get_async)


def store() -> S3Store:
    return cast("S3Store", FakeStore({JOB.audio_key: AUDIO}))


class TestFindingARecording:
    def test_an_explicit_path_wins_and_needs_no_checksum(self, tmp_path: Path) -> None:
        """The gate's path: a checksum of a re-encoded extract is not reproducible."""
        chosen = write_recording(tmp_path, "speech.json", sha=None)
        found = find_recording(settings(tmp_path, chosen), "whatever")
        assert found["language"] == "en"

    def test_an_explicit_path_that_is_not_there_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(RecordingNotFoundError, match="TRANSCRIPTION_RECORDING"):
            find_recording(settings(tmp_path, tmp_path / "absent.json"), AUDIO_SHA)

    def test_otherwise_the_directory_is_searched_by_checksum(self, tmp_path: Path) -> None:
        write_recording(tmp_path, "other.json", sha="a" * 64)
        write_recording(tmp_path, "ours.json", sha=AUDIO_SHA, language="fr")
        assert find_recording(settings(tmp_path), AUDIO_SHA)["language"] == "fr"

    def test_no_match_names_the_checksum_and_the_directory(self, tmp_path: Path) -> None:
        """A gate that passed with no transcript at all is the failure this avoids."""
        write_recording(tmp_path, "other.json", sha="a" * 64)
        with pytest.raises(RecordingNotFoundError) as caught:
            find_recording(settings(tmp_path), AUDIO_SHA)
        assert AUDIO_SHA in str(caught.value)
        assert str(tmp_path) in str(caught.value)


class TestTheRecordedProvider:
    async def test_it_names_itself_and_never_claims_a_gpu_model(self, tmp_path: Path) -> None:
        provider = RecordedProvider(settings(tmp_path), store())
        assert provider.name == "recorded"
        assert provider.model == "recorded"

    async def test_it_hashes_the_audio_it_was_pointed_at(self, tmp_path: Path) -> None:
        """The one check that ingest really produced the extract this job names."""
        write_recording(tmp_path, "ours.json", sha=AUDIO_SHA)
        provider = RecordedProvider(settings(tmp_path), store())
        handle = await provider.start(JOB)
        assert AUDIO_SHA[:16] in handle

    async def test_a_missing_audio_object_fails_rather_than_replaying(self, tmp_path: Path) -> None:
        write_recording(tmp_path, "ours.json", sha=AUDIO_SHA)
        empty = cast("S3Store", FakeStore({}))
        provider = RecordedProvider(settings(tmp_path), empty)
        with pytest.raises(FileNotFoundError):
            await provider.start(JOB)

    async def test_it_walks_every_stage_before_finishing(self, tmp_path: Path) -> None:
        """The surface has words for each of these; a jump to ready would exercise none."""
        chosen = write_recording(tmp_path, "speech.json", sha=None)
        provider = RecordedProvider(settings(tmp_path, chosen), store())
        handle = await provider.start(JOB)
        seen: list[str] = []
        while True:
            note = await provider.progress(handle)
            assert note is not None
            seen.append(note.stage)
            status = await provider.status(handle)
            if isinstance(status, Done):
                break
        assert seen[: len(STAGES)] == list(STAGES)
        assert status.raw.language == "en"

    async def test_an_unknown_handle_is_unknown_not_a_crash(self, tmp_path: Path) -> None:
        provider = RecordedProvider(settings(tmp_path), store())
        assert await provider.status("rec-nope") == Unknown()
        assert await provider.progress("rec-nope") is None


class ScriptedProvider:
    """A provider whose statuses are handed out one poll at a time."""

    def __init__(
        self,
        statuses: Sequence[RunStatus],
        *,
        resumed: Mapping[str, RunStatus] | None = None,
        notes: Sequence[TranscriptionProgress] | None = None,
        start_error: Exception | None = None,
    ) -> None:
        self.statuses = list(statuses)
        self.resumed = dict(resumed or {})
        self.notes = list(notes or [])
        self.start_error = start_error
        self.starts = 0
        self.polled: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def version(self) -> str:
        return "1"

    async def start(self, job: TranscribeJob) -> str:
        if self.start_error is not None:
            raise self.start_error
        self.starts += 1
        return f"handle-{job.attempt}-{self.starts}"

    async def status(self, handle: str) -> RunStatus:
        # Popped, so a scripted answer covers the reattach check only and the
        # poll that follows reads the script like any other.
        if handle in self.resumed:
            return self.resumed.pop(handle)
        self.polled.append(handle)
        return self.statuses.pop(0) if self.statuses else Unknown()

    async def progress(self, handle: str) -> TranscriptionProgress | None:  # noqa: ARG002
        return self.notes.pop(0) if self.notes else None


def raw(language: str = "en") -> TranscribeRaw:
    return TranscribeRaw(raw={"segments": [], "language": language}, language=language)


class TestTheRunner:
    async def _run(
        self, provider: ScriptedProvider, resume: str | None = None
    ) -> tuple[TranscribeRaw, list[tuple[TranscriptionProgress, str | None]]]:
        reported: list[tuple[TranscriptionProgress, str | None]] = []

        async def on_progress(note: TranscriptionProgress, handle: str | None) -> None:
            reported.append((note, handle))

        runner = TranscriptionRunner(provider, poll_seconds=0.0)
        return await runner.run(JOB, on_progress=on_progress, resume=resume), reported

    async def test_every_tick_reports_even_before_the_run_writes_a_note(self) -> None:
        """A quiet tick spends the heartbeat timeout and loses the handle the retry needs."""
        provider = ScriptedProvider([Running(), Running(), Done(raw())], notes=[])
        _, reported = await self._run(provider)
        assert len(reported) == 3
        assert all(handle == "handle-1-1" for _, handle in reported)

    async def test_a_note_is_repeated_until_a_newer_one_arrives(self) -> None:
        provider = ScriptedProvider(
            [Running(), Running(), Done(raw())],
            notes=[TranscriptionProgress(stage="align", percent=60)],
        )
        _, reported = await self._run(provider)
        assert [note.stage for note, _ in reported] == ["align", "align", "align"]

    async def test_a_running_handle_is_reattached_to_rather_than_started_again(self) -> None:
        """The whole reason to spawn: a retry must not pay for the GPU twice."""
        provider = ScriptedProvider([Running(), Done(raw())], resumed={"handle-old": Running()})
        _, reported = await self._run(provider, resume="handle-old")
        assert provider.starts == 0
        assert all(handle == "handle-old" for _, handle in reported)

    async def test_a_finished_handle_is_also_reattached_to(self) -> None:
        provider = ScriptedProvider([Done(raw("de"))], resumed={"handle-old": Done(raw("de"))})
        result, _ = await self._run(provider, resume="handle-old")
        assert provider.starts == 0
        assert result.language == "de"

    async def test_a_forgotten_handle_starts_a_new_run(self) -> None:
        provider = ScriptedProvider([Done(raw())], resumed={"handle-old": Unknown()})
        _, reported = await self._run(provider, resume="handle-old")
        assert provider.starts == 1
        assert reported[0][1] == "handle-1-1"

    async def test_a_failed_handle_starts_a_new_run(self) -> None:
        provider = ScriptedProvider([Done(raw())], resumed={"handle-old": Failed("Whatever: boom")})
        _, _reported = await self._run(provider, resume="handle-old")
        assert provider.starts == 1

    async def test_a_provider_that_cannot_be_polled_starts_a_new_run(self) -> None:
        """A poll failing while reattaching must not be worse than not reattaching."""

        class Rude(ScriptedProvider):
            async def status(self, handle: str) -> RunStatus:
                if handle == "handle-old":
                    msg = "connection reset"
                    raise ConnectionError(msg)
                return await super().status(handle)

        provider = Rude([Done(raw())])
        _, _reported = await self._run(provider, resume="handle-old")
        assert provider.starts == 1

    async def test_a_vanished_run_is_retryable(self) -> None:
        provider = ScriptedProvider([Unknown()])
        with pytest.raises(ApplicationError) as caught:
            await self._run(provider)
        assert caught.value.non_retryable is False
        assert "no record of transcription" in str(caught.value)

    async def test_a_missing_recording_is_terminal_on_attempt_one(self) -> None:
        """Deterministic: the same audio fails the same way on the next container."""
        provider = ScriptedProvider([], start_error=RecordingNotFoundError("no recording for x"))
        with pytest.raises(ApplicationError) as caught:
            await self._run(provider)
        assert caught.value.non_retryable is True
        assert caught.value.type == "TranscriptionFailure"


class TestClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "UnsupportedLanguageError: this recording is in a language we cannot align yet",
            "RecordingNotFoundError: no recorded transcription for audio sha256 abc",
            "TranscriptContractError: a word ends at 99999 ms",
        ],
    )
    def test_a_wrong_request_is_terminal(self, message: str) -> None:
        assert classify(message).non_retryable is True

    @pytest.mark.parametrize(
        "message",
        [
            "ConnectionError: connection reset by peer",
            "RuntimeError: CUDA out of memory",
            "the container was preempted",
        ],
    )
    def test_infrastructure_is_retried(self, message: str) -> None:
        """A preempted container is worth another attempt; a wrong language never is."""
        assert classify(message).non_retryable is False

    def test_the_type_decides_before_the_wording(self) -> None:
        """A message mentioning a language must not be terminal just for saying so."""
        assert classify("ConnectionError: the language service is down").non_retryable is False


class TestSettings:
    def test_the_default_provider_replays_a_recording(self) -> None:
        """A missing variable must never route work at a GPU, nor at nothing at all."""
        assert TranscriptionSettings.from_env().provider == "recorded"

    def test_the_modal_provider_reads_its_app_and_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "modal")
        monkeypatch.setenv("MODAL_ENVIRONMENT", "staging")
        read = TranscriptionSettings.from_env()
        assert read.provider == "modal"
        assert read.modal_environment == "staging"
        assert read.progress_dict == DEFAULT_TRANSCRIPT_DICT

    def test_the_progress_dict_is_not_the_ladders(self) -> None:
        """Two functions writing different shapes into one Dict is a bug that waits."""
        assert DEFAULT_TRANSCRIPT_DICT != "temnia-ladder-progress"

    def test_a_misspelt_provider_is_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "wisperx")
        with pytest.raises(ValueError, match="TRANSCRIPTION_PROVIDER is 'wisperx'"):
            TranscriptionSettings.from_env()

    def test_the_factory_builds_the_recorded_provider_without_touching_modal(
        self, tmp_path: Path
    ) -> None:
        monkeypatched = PipelineSettings.from_env()
        runner = make_transcription(monkeypatched, cast("S3Store", FakeStore({})))
        assert isinstance(runner.provider, RecordedProvider)
        assert runner.poll_seconds == RECORDED_POLL_SECONDS
        assert tmp_path.exists()
