from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from fractions import Fraction
from typing import TYPE_CHECKING

import av
import pytest

from temnia_pipeline.contracts import ChapterEditSpec, HarnessEvidence
from temnia_pipeline.harness.rendering import (
    build_captions,
    kept_sections,
    media_fingerprint,
    run_render_batch,
)
from temnia_pipeline.media import ffmpeg as ffmpeg_module
from temnia_pipeline.media.chapter_checks import (
    check_chapter_captions,
    check_chapter_media,
    technical_checks_pass,
)
from temnia_pipeline.media.chapters import (
    ChapterRenderConfig,
    InvalidTimelineError,
    MediaTimelineFacts,
    inspect_timeline,
    render_chapter,
)
from temnia_pipeline.media.ffmpeg import PROCESS_STOP_SECONDS, FfmpegError, run_ffmpeg, run_ffprobe

if TYPE_CHECKING:
    from pathlib import Path


async def test_render_batch_cancels_and_drains_sibling_on_failure() -> None:
    sibling_started = asyncio.Event()
    sibling_stopped = asyncio.Event()

    async def sibling() -> str:
        sibling_started.set()
        try:
            await asyncio.Future()
        finally:
            sibling_stopped.set()
        return "unreachable"

    async def failure() -> str:
        await sibling_started.wait()
        message = "render fixture failed"
        raise RuntimeError(message)

    with pytest.raises(ExceptionGroup) as raised:
        await run_render_batch((sibling, failure))
    assert any(
        isinstance(error, RuntimeError) and str(error) == "render fixture failed"
        for error in raised.value.exceptions
    )
    assert sibling_stopped.is_set()


async def fixture(path: Path, args: list[str]) -> Path:
    await run_ffmpeg("ffmpeg", [*args, str(path)], timeout_seconds=30)
    return path


async def ntsc_delayed_odd(tmp_path: Path) -> Path:
    return await fixture(
        tmp_path / "ntsc-delayed-odd.mkv",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=321x239:rate=30000/1001:duration=4",
            "-itsoffset",
            "0.250",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=3.75",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "yuv444p",
            "-c:a",
            "pcm_s16le",
            "-avoid_negative_ts",
            "disabled",
        ],
    )


@pytest.mark.timeout(30)
async def test_ntsc_non_keyframe_adjacent_cuts_preserve_delayed_audio_and_odd_size(
    tmp_path: Path,
) -> None:
    source = await ntsc_delayed_odd(tmp_path)
    timeline = await inspect_timeline(source)
    assert timeline.frame_rate == Fraction(30_000, 1001)
    assert (timeline.width, timeline.height) == (321, 239)
    assert timeline.audio_start == Fraction(1, 4)

    boundary = Fraction(61 * 1001, 30_000)
    intervals = ((Fraction(0), boundary), (boundary, Fraction(103 * 1001, 30_000)))
    measured = Fraction(0)
    for index, (start, end) in enumerate(intervals):
        output = tmp_path / f"chapter-{index}.mp4"
        await render_chapter(
            "ffmpeg",
            source,
            output,
            start=start,
            end=end,
            timeline=timeline,
            timeout_seconds=30,
        )
        checks = await check_chapter_media(
            output,
            section_id=f"section-{index}",
            edit_sha256="a" * 64,
            source=timeline,
            start=start,
            end=end,
            timeout_seconds=30,
        )
        assert technical_checks_pass(checks, source=timeline, captions_expected=False), [
            (check.name, check.message)
            for check in checks.technicalChecks
            if check.status == "fail"
        ]
        rendered = await inspect_timeline(output)
        assert (rendered.width, rendered.height) == (322, 240)
        measured += rendered.duration
    frame_rate = timeline.frame_rate
    assert frame_rate is not None
    tolerance = Fraction(2, 1) / frame_rate
    assert abs(measured - (intervals[-1][1] - intervals[0][0])) <= 2 * tolerance


@pytest.mark.timeout(30)
async def test_video_only_audio_only_vfr_and_rotation_are_explicit(tmp_path: Path) -> None:
    video_only = await fixture(
        tmp_path / "video-only.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=2",
            "-an",
            "-c:v",
            "libx264",
        ],
    )
    audio_only = await fixture(
        tmp_path / "audio-only.m4a",
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-vn",
            "-c:a",
            "aac",
        ],
    )
    vfr = await fixture(
        tmp_path / "vfr.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=3",
            "-vf",
            "select=not(mod(n\\,3))+not(mod(n\\,5))",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
        ],
    )
    rotated_base = await fixture(
        tmp_path / "rotation-base.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=2",
            "-an",
            "-c:v",
            "libx264",
        ],
    )
    rotated = await fixture(
        tmp_path / "rotated.mp4",
        [
            "-display_rotation:v:0",
            "90",
            "-i",
            str(rotated_base),
            "-c",
            "copy",
        ],
    )
    video_facts = await inspect_timeline(video_only)
    audio_facts = await inspect_timeline(audio_only)
    vfr_facts = await inspect_timeline(vfr)
    rotation_facts = await inspect_timeline(rotated)
    assert (video_facts.has_video, video_facts.has_audio) == (True, False)
    assert (audio_facts.has_video, audio_facts.has_audio) == (False, True)
    assert audio_facts.sample_rate == 44_100
    assert vfr_facts.variable_frame_rate
    assert rotation_facts.rotation == 90

    for name, source, facts in (
        ("video", video_only, video_facts),
        ("audio", audio_only, audio_facts),
        ("rotation", rotated, rotation_facts),
    ):
        output = tmp_path / f"{name}-chapter.mp4"
        await render_chapter(
            "ffmpeg",
            source,
            output,
            start=Fraction(0),
            end=Fraction(1),
            timeline=facts,
            timeout_seconds=30,
        )
        checks = await check_chapter_media(
            output,
            section_id=name,
            edit_sha256="b" * 64,
            source=facts,
            start=Fraction(0),
            end=Fraction(1),
            timeout_seconds=30,
        )
        assert technical_checks_pass(checks, source=facts, captions_expected=False), [
            (check.name, check.message)
            for check in checks.technicalChecks
            if check.status == "fail"
        ]


def first_frame_rgb(path: Path) -> tuple[float, float, float]:
    with av.open(str(path)) as container:
        frame = next(container.decode(video=0)).to_rgb().to_ndarray()
    return (
        float(frame[:, :, 0].mean()),
        float(frame[:, :, 1].mean()),
        float(frame[:, :, 2].mean()),
    )


@pytest.mark.timeout(30)
async def test_negative_start_and_seek_branch_reach_the_expected_content(tmp_path: Path) -> None:
    source = await fixture(
        tmp_path / "negative-start.mkv",
        [
            "-f",
            "lavfi",
            "-i",
            "color=red:size=320x240:rate=25:duration=6",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=320x240:rate=25:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=700:sample_rate=48000:duration=12",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a]asetpts=PTS-0.25/TB[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            "-copyts",
            "-avoid_negative_ts",
            "disabled",
        ],
    )
    facts = await inspect_timeline(source)
    assert facts.source_start == Fraction(-1, 4)
    start, end = Fraction(25, 4), Fraction(29, 4)
    assert start > 5
    output = tmp_path / "sought-blue.mp4"
    await render_chapter(
        "ffmpeg",
        source,
        output,
        start=start,
        end=end,
        timeline=facts,
        timeout_seconds=30,
    )
    red, green, blue = first_frame_rgb(output)
    assert blue > red * 2
    assert blue > green * 2
    checks = await check_chapter_media(
        output,
        section_id="seek",
        edit_sha256="c" * 64,
        source=facts,
        start=start,
        end=end,
        timeout_seconds=30,
    )
    assert technical_checks_pass(checks, source=facts, captions_expected=False), [
        (check.name, check.message) for check in checks.technicalChecks if check.status == "fail"
    ]


@pytest.mark.timeout(30)
async def test_corrupt_packets_fail_strict_full_decode(tmp_path: Path) -> None:
    source = await fixture(
        tmp_path / "source.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=3",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
        ],
    )
    body = bytearray(source.read_bytes())
    body[len(body) * 2 // 3 : len(body) * 2 // 3 + 2000] = b"\xff" * 2000
    corrupted = tmp_path / "corrupted.mp4"
    corrupted.write_bytes(body)
    source_facts = await inspect_timeline(source)
    checks = await check_chapter_media(
        corrupted,
        section_id="corrupt",
        edit_sha256="d" * 64,
        source=source_facts,
        start=Fraction(0),
        end=Fraction(3),
        timeout_seconds=30,
    )
    assert not technical_checks_pass(checks, source=source_facts, captions_expected=False)
    assert any(
        check.name == "full_decode" and check.status == "fail" for check in checks.technicalChecks
    )


@pytest.mark.timeout(10)
async def test_cancelled_child_is_killed_and_reaped(tmp_path: Path) -> None:
    assert PROCESS_STOP_SECONDS <= 5
    pid_file = tmp_path / "pid"
    executable = tmp_path / "stubborn-ffmpeg"
    executable.write_text(
        "#!/bin/sh\n"
        f"echo $$ > '{pid_file}'\n"
        "trap '' TERM\n"
        "while true; do echo noisy-error-line >&2; sleep 0.05; done\n"
    )
    executable.chmod(0o755)
    task = asyncio.create_task(run_ffmpeg(str(executable), []))
    async with asyncio.timeout(2):
        while not pid_file.exists():  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.timeout(10)
async def test_progress_callback_cancellation_kills_and_reaps_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "callback-pid"
    executable = tmp_path / "progress-ffmpeg"
    executable.write_text(
        "#!/bin/sh\n"
        f"echo $$ > '{pid_file}'\n"
        "trap '' TERM\n"
        "echo out_time_us=1000000\n"
        "while true; do echo noisy-error-line >&2; sleep 0.05; done\n"
    )
    executable.chmod(0o755)

    async def cancelled(_seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_ffmpeg(str(executable), [], on_progress=cancelled)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.timeout(10)
async def test_render_timeout_removes_partial_output(tmp_path: Path) -> None:
    executable = tmp_path / "partial-ffmpeg"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[-1]).write_bytes(b'partial')\n"
        "while True: time.sleep(0.05)\n"
    )
    executable.chmod(0o755)
    output = tmp_path / "partial.mp4"
    with pytest.raises(TimeoutError):
        await render_chapter(
            str(executable),
            tmp_path / "unused-source.mp4",
            output,
            start=Fraction(0),
            end=Fraction(1),
            timeline=_timeline(),
            timeout_seconds=0.05,
        )
    assert not output.exists()


async def test_ffprobe_reads_split_stdout_through_eof(tmp_path: Path) -> None:
    executable = tmp_path / "streaming-ffprobe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "for marker in ('a', 'b', 'c'):\n"
        "    sys.stdout.write(marker * 32768)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.01)\n"
    )
    executable.chmod(0o755)
    body = await run_ffprobe(str(executable), [])
    assert body == "a" * 32768 + "b" * 32768 + "c" * 32768


@pytest.mark.timeout(10)
async def test_ffprobe_over_limit_kills_and_reaps_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit = 64 * 1024
    monkeypatch.setattr(ffmpeg_module, "FFPROBE_STDOUT_BYTES", limit)
    pid_file = tmp_path / "ffprobe-pid"
    executable = tmp_path / "oversize-ffprobe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "for _ in range(4):\n"
        "    sys.stdout.buffer.write(b'x' * 32768)\n"
        "    sys.stdout.buffer.flush()\n"
        "while True: time.sleep(0.05)\n"
    )
    executable.chmod(0o755)
    with pytest.raises(FfmpegError, match="output exceeds"):
        await run_ffprobe(str(executable), [])
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_missing_track_start_refuses_before_encoder(tmp_path: Path) -> None:
    output = tmp_path / "never-created.mp4"
    with pytest.raises(InvalidTimelineError, match="video stream has no start"):
        await render_chapter(
            str(tmp_path / "must-not-run"),
            tmp_path / "source.mp4",
            output,
            start=Fraction(0),
            end=Fraction(1),
            timeline=replace(_timeline(), video_start=None),
            timeout_seconds=1,
        )
    assert not output.exists()


def test_caption_bounds_reject_invalid_actual_vtt(tmp_path: Path) -> None:
    good = tmp_path / "good.vtt"
    good.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nHello\n")
    assert check_chapter_captions(good, section_id="s", duration=Fraction(1)).status == "pass"
    bad = tmp_path / "bad.vtt"
    bad.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.001\nHello\n")
    assert check_chapter_captions(bad, section_id="s", duration=Fraction(1)).status == "fail"


def _edit(boundary_ms: int, *, title: str = "One") -> ChapterEditSpec:
    return ChapterEditSpec.model_validate(
        {
            "boundaries": [
                {
                    "candidateId": None,
                    "id": "b0",
                    "reasons": [],
                    "requiresReview": False,
                    "time": {"numerator": 0, "denominator": 1},
                    "timeMs": 0,
                },
                {
                    "candidateId": "c1",
                    "id": "b1",
                    "reasons": [],
                    "requiresReview": False,
                    "time": {"numerator": boundary_ms, "denominator": 1000},
                    "timeMs": boundary_ms,
                },
                {
                    "candidateId": "c2",
                    "id": "b2",
                    "reasons": [],
                    "requiresReview": False,
                    "time": {"numerator": 4, "denominator": 1},
                    "timeMs": 4000,
                },
                {
                    "candidateId": None,
                    "id": "b3",
                    "reasons": [],
                    "requiresReview": False,
                    "time": {"numerator": 6, "denominator": 1},
                    "timeMs": 6000,
                },
            ],
            "compilerVersion": "fixture",
            "durationMs": 6000,
            "evidenceArtifactId": "30000000-0000-0000-0000-000000000003",
            "evidenceSha256": "a" * 64,
            "sections": [
                {
                    "endBoundaryId": "b1",
                    "flags": [],
                    "id": "s1",
                    "kind": "keep",
                    "quoteWordIds": [],
                    "reason": "",
                    "reviewState": "proposed",
                    "startBoundaryId": "b0",
                    "title": title,
                },
                {
                    "endBoundaryId": "b2",
                    "flags": [],
                    "id": "s2",
                    "kind": "keep",
                    "quoteWordIds": [],
                    "reason": "",
                    "reviewState": "proposed",
                    "startBoundaryId": "b1",
                    "title": "Two",
                },
                {
                    "endBoundaryId": "b3",
                    "flags": [],
                    "id": "s3",
                    "kind": "keep",
                    "quoteWordIds": [],
                    "reason": "",
                    "reviewState": "proposed",
                    "startBoundaryId": "b2",
                    "title": "Three",
                },
            ],
            "sourceAudioSampleRate": 48000,
            "sourceFrameRate": {"numerator": 25, "denominator": 1},
            "sourceId": "10000000-0000-0000-0000-000000000001",
            "version": 1,
        }
    )


def _timeline() -> MediaTimelineFacts:
    return MediaTimelineFacts(
        duration=Fraction(6),
        container_start=Fraction(0),
        source_start=Fraction(0),
        has_video=True,
        has_audio=True,
        video_stream_index=0,
        audio_stream_index=1,
        video_start=Fraction(0),
        audio_start=Fraction(0),
        video_duration=Fraction(6),
        audio_duration=Fraction(6),
        frame_rate=Fraction(25),
        video_time_base=Fraction(1, 12800),
        audio_time_base=Fraction(1, 48000),
        sample_rate=48000,
        width=320,
        height=240,
        rotation=0,
        audio_channels=2,
        audio_layout="stereo",
        variable_frame_rate=False,
        video_codec="h264",
        audio_codec="aac",
        sample_aspect_ratio=Fraction(1),
    )


def test_boundary_change_invalidates_only_two_media_intervals_and_title_none() -> None:
    before = kept_sections(_edit(2000))
    after = kept_sections(_edit(2200, title="Renamed"))
    config = ChapterRenderConfig()
    before_hashes = [
        media_fingerprint(
            source_fingerprint="f" * 64,
            section=section,
            timeline=_timeline(),
            config=config,
        )
        for section in before
    ]
    after_hashes = [
        media_fingerprint(
            source_fingerprint="f" * 64,
            section=section,
            timeline=_timeline(),
            config=config,
        )
        for section in after
    ]
    assert before_hashes[0] != after_hashes[0]
    assert before_hashes[1] != after_hashes[1]
    assert before_hashes[2] == after_hashes[2]
    assert media_fingerprint(
        source_fingerprint="f" * 64,
        section=before[0],
        timeline=_timeline(),
        config=config,
    ) == media_fingerprint(
        source_fingerprint="f" * 64,
        section=kept_sections(_edit(2000, title="Only title changed"))[0],
        timeline=_timeline(),
        config=config,
    )


def test_caption_text_is_clipped_sanitized_and_uses_stable_speaker_label() -> None:
    evidence = HarnessEvidence.model_validate(
        {
            "audioSampleRate": 48000,
            "boundaries": [],
            "config": {"speakerIdentities": {"SPEAKER_00": {"label": "Alex <Host>"}}},
            "durationMs": 2000,
            "frameRate": None,
            "modelVersions": {},
            "pauses": [],
            "sentences": [],
            "shots": [],
            "sourceFingerprint": "f" * 64,
            "sourceId": "10000000-0000-0000-0000-000000000001",
            "sourceStart": {"numerator": 0, "denominator": 1},
            "speechCoverage": {
                "detector": None,
                "detectorHash": None,
                "detectorRevision": None,
                "intervals": [],
                "status": "unknown",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": "20000000-0000-0000-0000-000000000002",
            "transcriptRevision": 1,
            "transcriptSha256": "a" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": [
                {
                    "confidence": 0.9,
                    "endMs": 700,
                    "id": "w1",
                    "lineageIds": [],
                    "speaker": "SPEAKER_00",
                    "startMs": 400,
                    "text": "<b>Hello</b>",
                    "timing": "aligned",
                    "wordIndex": 0,
                },
                {
                    "confidence": 0.9,
                    "endMs": 1200,
                    "id": "w2",
                    "lineageIds": [],
                    "speaker": "SPEAKER_00",
                    "startMs": 800,
                    "text": "there --> friend",
                    "timing": "aligned",
                    "wordIndex": 1,
                },
            ],
        }
    )
    section = kept_sections(_edit(1500))[0]
    section = type(section)(section.section_id, Fraction(1, 2), Fraction(1))
    captions = build_captions(evidence, section)
    assert "Alex &lt;Host&gt;:" in captions.body
    assert "&lt;b&gt;Hello&lt;/b&gt;" in captions.body
    assert "there → friend" in captions.body
    assert "00:00:00.000 --> 00:00:00.500" in captions.body
    assert len(captions.warnings) == 2
