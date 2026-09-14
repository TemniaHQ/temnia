"""The GPU render runner: spawn or reattach, poll, verify every output, refuse a bad byte."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore import put_async
from obstore.store import MemoryStore
from temporalio.exceptions import ApplicationError

from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.media.chapters import ChapterRenderConfig
from temnia_pipeline.render_remote import (
    ModalRenderer,
    RenderJob,
    RenderOutput,
    RenderProgress,
    RenderResult,
    RenderSectionJob,
    download_output,
)
from temnia_pipeline.transcode.modal_client import Done, Failed, Running, Unknown, Unreachable

CallStatus = Done | Failed | Running | Unknown | Unreachable

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store


def _job() -> RenderJob:
    return RenderJob(
        master_key="org/o/source/s/original/master.mp4",
        master_sha256="a" * 64,
        size_bytes=10,
        timeline={"duration": {"numerator": 10, "denominator": 1}},
        config={"video_codec": "h264_nvenc", "video_preset": "p5"},
        sections=[
            RenderSectionJob(
                section_id="one",
                start_numerator=0,
                start_denominator=1,
                end_numerator=3,
                end_denominator=1,
                output_key="org/o/source/s/harness/remote-render/one.mp4",
            )
        ],
        expected_seconds=3.0,
    )


@dataclass
class FakeClient:
    statuses: list[CallStatus]
    spawned: list[RenderJob] = field(default_factory=list[RenderJob])
    checked: list[str] = field(default_factory=list[str])

    async def spawn(self, job: RenderJob) -> str:
        self.spawned.append(job)
        return f"call-{len(self.spawned)}"

    async def status(self, call_id: str) -> CallStatus:
        self.checked.append(call_id)
        return self.statuses.pop(0)

    async def progress(self, call_id: str) -> RenderProgress | None:
        _ = call_id
        return RenderProgress(stage="render", percent=50, section_id="one")


async def _published(store: S3Store, key: str, body: bytes) -> RenderOutput:
    await put_async(store, key, body)
    return RenderOutput(
        section_id="one",
        output_key=key,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )


async def test_runner_spawns_polls_verifies_and_the_download_checks_the_hash(
    tmp_path: Path,
) -> None:
    store = cast("S3Store", MemoryStore())
    job = _job()
    output = await _published(store, job.sections[0].output_key, b"rendered bytes")
    done = Done(result=cast("Any", RenderResult(outputs=[output], encoder="h264_nvenc")))
    client = FakeClient(statuses=[Running(), done])
    heartbeats: list[tuple[str, int]] = []

    async def on_progress(note: RenderProgress, call_id: str) -> None:
        heartbeats.append((call_id, note.percent))

    renderer = ModalRenderer(client, store, poll_seconds=0)
    result = await renderer.run(job, on_progress=on_progress)
    assert result.call_id == "call-1"
    assert heartbeats
    assert all(call == "call-1" for call, _ in heartbeats)
    destination = tmp_path / "chapter.mp4"
    await download_output(store, result.outputs[0], destination)
    assert destination.read_bytes() == b"rendered bytes"
    with pytest.raises(ApplicationError, match="differ from the reported hash"):
        await download_output(
            store, output.model_copy(update={"sha256": "b" * 64}), tmp_path / "bad.mp4"
        )


async def test_runner_reattaches_to_a_live_call_and_respawns_a_forgotten_one() -> None:
    store = cast("S3Store", MemoryStore())
    job = _job()
    output = await _published(store, job.sections[0].output_key, b"x")
    done = Done(result=cast("Any", RenderResult(outputs=[output], encoder="h264_nvenc")))
    live = FakeClient(statuses=[Running(), done])

    async def quiet(_note: RenderProgress, _call: str) -> None:
        return None

    result = await ModalRenderer(live, store, poll_seconds=0).run(
        job, on_progress=quiet, resume="earlier"
    )
    assert live.spawned == []
    assert result.call_id == "earlier"
    forgotten = FakeClient(statuses=[Unknown(), done])
    result = await ModalRenderer(forgotten, store, poll_seconds=0).run(
        job, on_progress=quiet, resume="gone"
    )
    assert len(forgotten.spawned) == 1
    assert result.call_id == "call-1"


async def test_a_short_object_or_an_ffmpeg_refusal_is_terminal() -> None:
    store = cast("S3Store", MemoryStore())
    job = _job()
    await put_async(store, job.sections[0].output_key, b"short")
    lying = RenderOutput(
        section_id="one", output_key=job.sections[0].output_key, sha256="c" * 64, size_bytes=99
    )
    done = Done(result=cast("Any", RenderResult(outputs=[lying], encoder="h264_nvenc")))

    async def quiet(_note: RenderProgress, _call: str) -> None:
        return None

    with pytest.raises(ApplicationError, match="short in the store") as short:
        await ModalRenderer(FakeClient(statuses=[done]), store, poll_seconds=0).run(
            job, on_progress=quiet
        )
    assert short.value.non_retryable
    with pytest.raises(ApplicationError, match="ffmpeg") as refused:
        await ModalRenderer(
            FakeClient(statuses=[Failed("FfmpegError: ffmpeg exited 1")]), store, poll_seconds=0
        ).run(job, on_progress=quiet)
    assert refused.value.non_retryable
    with pytest.raises(ApplicationError, match="no record") as lost:
        await ModalRenderer(FakeClient(statuses=[Unknown()]), store, poll_seconds=0).run(
            job, on_progress=quiet
        )
    assert not lost.value.non_retryable


def test_nvenc_config_uses_constant_quality_and_its_own_presets() -> None:
    nvenc = ChapterRenderConfig(video_codec="h264_nvenc", video_preset="p5")
    assert nvenc.video_args()[:2] == ["-c:v", "h264_nvenc"]
    assert "-cq:v" in nvenc.video_args()
    assert "-crf:v" not in nvenc.video_args()
    x264 = ChapterRenderConfig()
    assert "-crf:v" in x264.video_args()
    with pytest.raises(ValueError, match="p1 to p7"):
        ChapterRenderConfig(video_codec="h264_nvenc", video_preset="medium")
    with pytest.raises(ValueError, match="libx264 or h264_nvenc"):
        ChapterRenderConfig(video_codec="libvpx")


def test_local_rendering_refuses_the_gpu_encoder() -> None:
    settings = HarnessSettings.from_env(
        {"HARNESS_RENDER_BACKEND": "local", "HARNESS_RENDER_ENCODER": "h264_nvenc"}
    )
    assert settings.render_backend == "local"
    assert settings.render_encoder == "h264_nvenc"
    with pytest.raises(ValueError, match="must be local or modal"):
        HarnessSettings.from_env({"HARNESS_RENDER_BACKEND": "gpu"})


async def test_an_undeployed_render_function_is_reported_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modal.exception import NotFoundError  # noqa: PLC0415

    from temnia_pipeline import render_remote  # noqa: PLC0415
    from temnia_pipeline.render_remote import (  # noqa: PLC0415
        RealRenderClient,
        RenderFunctionAbsent,
    )
    from temnia_pipeline.settings import TranscodeSettings  # noqa: PLC0415

    class Absent:
        class spawn:  # noqa: N801 - mirrors the SDK's attribute
            @staticmethod
            async def aio(_payload: object) -> object:
                message = "no such function"
                raise NotFoundError(message)

    def absent(_settings: object) -> Absent:
        return Absent()

    monkeypatch.setattr(render_remote, "_function", absent)
    settings = TranscodeSettings(
        backend="modal", modal_app="temnia-media", modal_environment="staging", progress_dict="p"
    )
    with pytest.raises(RenderFunctionAbsent, match="temnia-media/render_sections is not deployed"):
        await RealRenderClient(settings, "render-notes").spawn(_job())
