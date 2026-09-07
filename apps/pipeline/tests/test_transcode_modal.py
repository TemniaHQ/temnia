"""The Modal-backed ladder, driven by a fake client and a fake store.

There is no Modal account on the build machine and there is not meant to be
one: what these tests prove is the worker's half of the contract, which is
where the retry, reattach, and verification decisions live.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import TYPE_CHECKING, cast

import pytest
from temporalio.exceptions import ApplicationError

from temnia_pipeline.media import hls
from temnia_pipeline.media.facts import VideoFacts
from temnia_pipeline.settings import TranscodeSettings
from temnia_pipeline.transcode import (
    CONTRACT_VERSION,
    LadderJob,
    LadderProgress,
    LadderResult,
    ProgressCallback,
)
from temnia_pipeline.transcode import modal_client as modal_client_module
from temnia_pipeline.transcode.modal import (
    DeploymentError,
    ModalTranscoder,
    assert_deployment,
    classify,
)
from temnia_pipeline.transcode.modal_client import (
    CallStatus,
    Done,
    Failed,
    RealModalClient,
    Running,
    Unknown,
)

if TYPE_CHECKING:
    from obstore.store import S3Store

PREFIX = "org/0192e8a0-0000-7000-8000-000000000001/source/0192e8a0-0000-7000-8000-0000000000aa/"
DURATION = 151.0

JOB = LadderJob(
    master_key=PREFIX + "master/master.mov",
    artifact_prefix=PREFIX,
    size_bytes=1024,
    video=VideoFacts(
        width=1920, height=1080, fps=Fraction(25), variable_frame_rate=False, codec="h264"
    ),
    has_audio=True,
    expected_seconds=DURATION,
)

FULL_LADDER = {"top": 151.0, "720p": 151.0, "360p": 151.0, "audio": 151.04, "iframes": 150.0}


def manifest(renditions: dict[str, float] | None = None) -> hls.LadderManifest:
    return hls.LadderManifest(
        renditions=FULL_LADDER if renditions is None else renditions,
        iframes=True,
        segment_seconds=hls.SEGMENT_SECONDS,
        total_bytes=987_654,
        encoder="h264_nvenc",
        produced_by="modal",
        call_id="fc-original",
    )


class FakeStore:
    """Just the two reads `stored_ladder` makes, keyed like the real bucket."""

    def __init__(self, objects: dict[str, str] | None = None) -> None:
        self.objects = dict(objects or {})

    def publish(self, ladder: hls.LadderManifest) -> None:
        self.objects[PREFIX + "hls/master.m3u8"] = "#EXTM3U\n"
        self.objects[PREFIX + "hls/manifest.json"] = ladder.model_dump_json(by_alias=True)


async def _read_text(store: S3Store, key: str) -> str | None:
    return cast("FakeStore", store).objects.get(key)


async def _key_exists(store: S3Store, key: str) -> bool:
    return key in cast("FakeStore", store).objects


@pytest.fixture(autouse=True)
def _fake_storage(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # a pytest fixture is called by name, not by reference
    """`stored_ladder` is the only storage the transcoder touches."""
    monkeypatch.setattr("temnia_pipeline.transcode.read_text", _read_text)
    monkeypatch.setattr("temnia_pipeline.transcode.key_exists", _key_exists)


class FakeModalClient:
    """A scripted call: `statuses` are handed out one poll at a time."""

    def __init__(
        self,
        statuses: list[CallStatus] | None = None,
        *,
        resumed: dict[str, CallStatus] | None = None,
        progress: list[LadderProgress] | None = None,
        on_spawn: object = None,
        on_poll: object = None,
    ) -> None:
        self.statuses = list(statuses or [])
        self.resumed = dict(resumed or {})
        self.progress_notes = list(progress or [])
        self.spawns: list[LadderJob] = []
        self.polled: list[str] = []
        self.on_spawn = on_spawn
        self.on_poll = on_poll

    async def spawn(self, job: LadderJob) -> str:
        self.spawns.append(job)
        if callable(self.on_spawn):
            self.on_spawn()
        return f"fc-spawn-{len(self.spawns)}"

    async def status(self, call_id: str) -> CallStatus:
        self.polled.append(call_id)
        if callable(self.on_poll):
            self.on_poll()
        if call_id in self.resumed:
            return self.resumed.pop(call_id)
        return self.statuses.pop(0) if self.statuses else Running()

    async def progress(self, call_id: str) -> LadderProgress | None:
        _ = call_id
        return self.progress_notes.pop(0) if self.progress_notes else None

    async def version(self) -> str:
        return CONTRACT_VERSION


def transcoder(client: FakeModalClient, store: FakeStore) -> ModalTranscoder:
    return ModalTranscoder(client, cast("S3Store", store), poll_seconds=0)


def result_of(ladder: hls.LadderManifest) -> LadderResult:
    return LadderResult(
        renditions=ladder.renditions,
        total_bytes=ladder.total_bytes,
        manifest_key=PREFIX + "hls/manifest.json",
        encoder=ladder.encoder,
        call_id=ladder.call_id,
    )


def collect(seen: list[tuple[LadderProgress, str | None]]) -> ProgressCallback:
    async def on_progress(progress: LadderProgress, call_id: str | None) -> None:
        seen.append((progress, call_id))

    return on_progress


async def test_a_fresh_spawn_reports_progress_and_returns_the_published_ladder() -> None:
    store = FakeStore()
    published = manifest()

    client = FakeModalClient(
        statuses=[Running(), Done(result_of(published))],
        progress=[
            LadderProgress(stage="hls", percent=40),
            LadderProgress(stage="publish", percent=90),
        ],
        on_spawn=lambda: store.publish(published),
    )
    seen: list[tuple[LadderProgress, str | None]] = []
    result = await transcoder(client, store).run(JOB, on_progress=collect(seen), resume=None)

    assert len(client.spawns) == 1
    assert result.renditions == FULL_LADDER
    assert result.total_bytes == 987_654
    assert result.encoder == "h264_nvenc"
    # The call id is what a retry reattaches by, so every tick carries it.
    assert [note.percent for note, _ in seen] == [40, 90]
    assert {call_id for _, call_id in seen} == {"fc-spawn-1"}


async def test_every_tick_heartbeats_the_call_id_before_the_first_note_exists() -> None:
    """The silence between spawn and the first note is where the double spend was.

    An L4 can take minutes to schedule and cold start, and `on_progress` is the
    only place the activity heartbeats. A tick that reported nothing would run
    the activity's five-minute heartbeat timeout out and hand the retry no call
    id, which spawns a second GPU job for the same source.
    """
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Running(), Running(), Done(result_of(published))],
        on_spawn=lambda: store.publish(published),
    )
    seen: list[tuple[LadderProgress, str | None]] = []
    await transcoder(client, store).run(JOB, on_progress=collect(seen), resume=None)

    assert [(note.stage, note.percent) for note, _ in seen] == [("hls", 0)] * 3
    assert [call_id for _, call_id in seen] == ["fc-spawn-1"] * 3


async def test_a_tick_with_no_new_note_repeats_the_last_one() -> None:
    """A Dict read that fails while the encode is healthy must not stop the heartbeat."""
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Running(), Running(), Done(result_of(published))],
        progress=[LadderProgress(stage="hls", percent=40)],
        on_spawn=lambda: store.publish(published),
    )
    seen: list[tuple[LadderProgress, str | None]] = []
    await transcoder(client, store).run(JOB, on_progress=collect(seen), resume=None)

    assert [note.percent for note, _ in seen] == [40, 40, 40]


async def test_a_reattached_call_heartbeats_its_id_from_the_first_tick() -> None:
    """The id a retry reattached by has to survive into this attempt's heartbeats."""
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Done(result_of(published))],
        resumed={"fc-earlier": Running()},
        on_poll=lambda: store.publish(published),
    )
    seen: list[tuple[LadderProgress, str | None]] = []
    await transcoder(client, store).run(JOB, on_progress=collect(seen), resume="fc-earlier")

    assert [call_id for _, call_id in seen] == ["fc-earlier"]


async def test_a_running_call_is_reattached_to_rather_than_spawned_again() -> None:
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Done(result_of(published))],
        resumed={"fc-earlier": Running()},
        on_poll=lambda: store.publish(published),
    )
    result = await transcoder(client, store).run(JOB, on_progress=collect([]), resume="fc-earlier")

    assert client.spawns == []
    assert client.polled == ["fc-earlier", "fc-earlier"]
    assert result.call_id == "fc-original"


async def test_a_call_that_finished_while_the_worker_was_gone_is_taken_as_it_is() -> None:
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Done(result_of(published))],
        resumed={"fc-earlier": Done(result_of(published))},
        on_poll=lambda: store.publish(published),
    )
    result = await transcoder(client, store).run(JOB, on_progress=collect([]), resume="fc-earlier")

    assert client.spawns == []
    assert result.total_bytes == 987_654


@pytest.mark.parametrize("earlier", [Failed("the container died"), Unknown()])
async def test_a_dead_call_is_respawned(earlier: CallStatus) -> None:
    store = FakeStore()
    published = manifest()
    client = FakeModalClient(
        statuses=[Done(result_of(published))],
        resumed={"fc-earlier": earlier},
        on_spawn=lambda: store.publish(published),
    )
    result = await transcoder(client, store).run(JOB, on_progress=collect([]), resume="fc-earlier")

    assert len(client.spawns) == 1
    assert result.renditions == FULL_LADDER


async def test_a_ladder_already_in_storage_never_reaches_modal() -> None:
    store = FakeStore()
    store.publish(manifest())
    client = FakeModalClient()

    assert await transcoder(client, store).reuse(JOB) is not None
    result = await transcoder(client, store).run(JOB, on_progress=collect([]), resume=None)

    assert client.spawns == []
    assert client.polled == []
    assert result.encoder == "h264_nvenc"


async def test_a_manifest_without_its_master_playlist_is_not_reuse() -> None:
    store = FakeStore({PREFIX + "hls/manifest.json": manifest().model_dump_json(by_alias=True)})
    assert await transcoder(FakeModalClient(), store).reuse(JOB) is None


async def test_a_short_manifest_is_not_reuse() -> None:
    store = FakeStore()
    store.publish(manifest(renditions={**FULL_LADDER, "720p": 80.0}))
    assert await transcoder(FakeModalClient(), store).reuse(JOB) is None


async def test_a_result_whose_ladder_is_short_fails_terminally() -> None:
    store = FakeStore()
    short = manifest(renditions={**FULL_LADDER, "360p": 60.0})
    client = FakeModalClient(
        statuses=[Done(result_of(short))], on_spawn=lambda: store.publish(short)
    )
    with pytest.raises(ApplicationError) as caught:
        await transcoder(client, store).run(JOB, on_progress=collect([]), resume=None)

    assert caught.value.type == "TranscodeFailure"
    assert caught.value.non_retryable
    assert "not complete in storage" in str(caught.value)


async def test_a_result_the_function_never_published_fails_terminally() -> None:
    store = FakeStore()
    client = FakeModalClient(statuses=[Done(result_of(manifest()))])
    with pytest.raises(ApplicationError) as caught:
        await transcoder(client, store).run(JOB, on_progress=collect([]), resume=None)

    assert caught.value.type == "TranscodeFailure"


@pytest.mark.parametrize(
    "message",
    [
        # The type name the client puts in front is the primary rule, and the
        # second of these is the case the wording alone would have missed: it
        # never says "truncated" outside the exception's own name.
        "FfmpegError: exited 1: Invalid data found when processing input",
        "TruncatedOutputError: 720p/index.m3u8 covers 60.0s of 151.0s",
        # Without a prefix, the word markers still catch it.
        "ffmpeg exited 1: Invalid data found when processing input",
        "the output is truncated",
    ],
)
async def test_an_encode_failure_is_terminal(message: str) -> None:
    client = FakeModalClient(statuses=[Failed(message)])
    with pytest.raises(ApplicationError) as caught:
        await transcoder(client, FakeStore()).run(JOB, on_progress=collect([]), resume=None)

    assert caught.value.type == "TranscodeFailure"
    assert caught.value.non_retryable


@pytest.mark.parametrize(
    "message",
    ["Task exited with code 137", "grpc: connection reset by peer", "no GPU capacity available"],
)
async def test_an_infrastructure_failure_is_retryable(message: str) -> None:
    client = FakeModalClient(statuses=[Failed(message)])
    with pytest.raises(ApplicationError) as caught:
        await transcoder(client, FakeStore()).run(JOB, on_progress=collect([]), resume=None)

    assert caught.value.type == "ModalFailure"
    assert not caught.value.non_retryable


async def test_a_call_that_vanishes_mid_poll_is_retryable() -> None:
    client = FakeModalClient(statuses=[Unknown()])
    with pytest.raises(ApplicationError) as caught:
        await transcoder(client, FakeStore()).run(JOB, on_progress=collect([]), resume=None)

    assert caught.value.type == "ModalFailure"
    assert "no record of call" in str(caught.value)


def test_a_job_serialises_to_the_wire_names_the_function_reads() -> None:
    payload = json.loads(JOB.model_dump_json(by_alias=True))
    assert payload["masterKey"].endswith("master.mov")
    assert payload["expectedSeconds"] == DURATION
    assert payload["video"]["variableFrameRate"] is False
    assert payload["video"]["fps"] == "25"
    assert LadderJob.model_validate(payload) == JOB


def test_the_scratch_directory_is_named_after_the_source() -> None:
    assert JOB.scratch_name == "0192e8a0-0000-7000-8000-0000000000aa"
    assert JOB.hls_prefix == PREFIX + "hls/"
    assert JOB.manifest_key == PREFIX + "hls/manifest.json"


class AuthErrorClient(FakeModalClient):
    """A client whose token the server rejects."""

    async def version(self) -> str:
        msg = "token id 'ak-...' not found"
        raise RuntimeError(msg)


class OldDeploymentClient(FakeModalClient):
    """A Modal app deployed from an older commit."""

    async def version(self) -> str:
        return "0"


SETTINGS = TranscodeSettings(
    backend="modal",
    modal_app="temnia-media",
    modal_environment="staging",
    progress_dict="temnia-ladder-progress",
)


async def test_a_matching_deployment_lets_the_worker_boot() -> None:
    await assert_deployment(FakeModalClient(), SETTINGS)


async def test_a_deployment_from_another_commit_refuses_the_boot() -> None:
    with pytest.raises(DeploymentError) as caught:
        await assert_deployment(OldDeploymentClient(), SETTINGS)

    assert str(caught.value) == (
        "the Modal app 'temnia-media' in environment 'staging' speaks media contract '0' "
        f"and this worker speaks {CONTRACT_VERSION!r}. Deploy the Modal app and the pipeline "
        "image from the same commit."
    )


async def test_a_rejected_token_refuses_the_boot_and_names_the_variables() -> None:
    with pytest.raises(DeploymentError) as caught:
        await assert_deployment(AuthErrorClient(), SETTINGS)

    assert str(caught.value) == (
        "cannot reach the Modal app 'temnia-media' in environment 'staging': "
        "token id 'ak-...' not found. Check MODAL_TOKEN_ID and MODAL_TOKEN_SECRET, "
        "and that `uv run modal deploy temnia_pipeline.modal_app` has run for this "
        "environment."
    )


class _RaisingGet:
    """Stands in for the SDK's `FunctionCall.get`, for a call that raised."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    # The parameter is the SDK's, and `status` passes it by name.
    async def aio(self, timeout: float) -> object:  # noqa: ASYNC109
        _ = timeout
        raise self.error


class _RaisingCall:
    """Stands in for the SDK's `FunctionCall`."""

    def __init__(self, error: BaseException) -> None:
        self.get = _RaisingGet(error)


async def test_a_raised_exception_reaches_the_classifier_with_its_type_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`str(error)` alone drops the one fact that decides terminal from retryable.

    This message never contains the word "truncated" outside the exception's
    own name, and the ladder is not going to come out any longer on the next
    container, so being retried would be three GPU jobs for one wrong output.
    """
    error = hls.TruncatedOutputError("720p/index.m3u8 covers 60.0s of 151.0s")

    def fake_call(call_id: str) -> object:
        _ = call_id
        return _RaisingCall(error)

    monkeypatch.setattr(modal_client_module, "_call", fake_call)
    status = await RealModalClient(SETTINGS).status("fc-1")

    assert isinstance(status, Failed)
    assert status.message == "TruncatedOutputError: 720p/index.m3u8 covers 60.0s of 151.0s"
    assert classify(status.message).non_retryable
