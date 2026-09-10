"""Real FFmpeg decoding proves that VAD samples retain the chapter source clock."""

from __future__ import annotations

import os
import sys
from fractions import Fraction
from typing import TYPE_CHECKING

import numpy as np
import pytest

from temnia_pipeline.media.chapters import inspect_timeline
from temnia_pipeline.media.ffmpeg import run_ffmpeg
from temnia_pipeline.media.speech_pcm import (
    SAMPLE_RATE,
    SpeechDecodeError,
    source_pcm_sample_count,
    speech_pcm_command,
    stream_pcm_windows,
)

if TYPE_CHECKING:
    from pathlib import Path

    from temnia_pipeline.media.chapters import MediaTimelineFacts


async def _pcm(source: Path, timeline: MediaTimelineFacts) -> np.ndarray:
    windows = [
        window.samples[: window.valid_samples]
        async for window in stream_pcm_windows(source, timeline=timeline)
    ]
    samples = np.concatenate(windows)
    assert len(samples) == source_pcm_sample_count(timeline)
    return samples


@pytest.mark.timeout(10)
async def test_pcm_early_close_terminates_and_drains_its_subprocess(tmp_path: Path) -> None:
    pid_path = tmp_path / "decoder.pid"
    executable = tmp_path / "fake-ffmpeg"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, struct, time\n"
        f"open({str(pid_path)!r}, 'w').write(str(os.getpid()))\n"
        "os.write(1, struct.pack('<512f', *([0.0] * 512)))\n"
        "time.sleep(60)\n"
    )
    executable.chmod(0o755)
    windows = stream_pcm_windows(tmp_path / "unused", ffmpeg=str(executable))
    first = await anext(windows)
    assert first.valid_samples == 512
    pid = int(pid_path.read_text())
    await windows.aclose()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.timeout(30)
@pytest.mark.parametrize("broken_sample", [False, True])
async def test_partial_decoding_cannot_claim_complete_source_coverage(
    tmp_path: Path, *, broken_sample: bool
) -> None:
    source = tmp_path / "audio.m4a"
    await run_ffmpeg(
        "ffmpeg", ["-f", "lavfi", "-i", "sine=duration=1", str(source)], timeout_seconds=20
    )
    timeline = await inspect_timeline(source)
    executable = tmp_path / "partial-ffmpeg"
    executable.write_text(
        f"#!{sys.executable}\nimport os,struct\nos.write(1, struct.pack('<512f', *([0.0] * 512)))\n"
        + ("os.write(1, b'x')\n" if broken_sample else "")
    )
    executable.chmod(0o755)
    expected = "inside a float32 sample" if broken_sample else "exact source grid"
    with pytest.raises(SpeechDecodeError, match=expected):
        _ = [
            window
            async for window in stream_pcm_windows(
                source, ffmpeg=str(executable), timeline=timeline
            )
        ]


def _level(samples: np.ndarray, start: float, end: float) -> float:
    region = samples[round(start * SAMPLE_RATE) : round(end * SAMPLE_RATE)]
    return float(np.sqrt(np.mean(np.square(region))))


@pytest.mark.parametrize("clock_shift", [Fraction(0), Fraction(2), Fraction(-1, 2)])
@pytest.mark.timeout(30)
async def test_delayed_audio_keeps_its_offset_on_signed_source_clocks(
    tmp_path: Path, clock_shift: Fraction
) -> None:
    source = tmp_path / "offset.mkv"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=32x32:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1.5",
            "-filter_complex",
            (
                f"[0:v]setpts=PTS+({clock_shift})/TB[v];"
                f"[1:a]asetpts=PTS+({clock_shift + Fraction(1, 4)})/TB[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-copyts",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            "-avoid_negative_ts",
            "disabled",
            str(source),
        ],
        timeout_seconds=20,
    )
    timeline = await inspect_timeline(source)
    if clock_shift < 0:
        # The video clock quantizes the requested negative offset to its grid.
        assert timeline.source_start < 0
    else:
        assert timeline.source_start == clock_shift
    assert timeline.audio_start == clock_shift + Fraction(1, 4)
    assert timeline.audio_start is not None
    samples = await _pcm(source, timeline)
    audible_start = float(np.flatnonzero(np.abs(samples) > 0.01)[0]) / SAMPLE_RATE
    assert audible_start == pytest.approx(
        float(timeline.audio_start - timeline.source_start), abs=0.003
    )
    assert _level(samples, 0.02, 0.20) < 0.00001
    assert _level(samples, 0.30, 0.60) > 0.05
    if timeline.duration > Fraction(9, 5):
        assert _level(samples, 1.80, float(timeline.duration)) < 0.00001


@pytest.mark.timeout(30)
@pytest.mark.parametrize("gap_end", [0.97, 1.3])
async def test_internal_audio_timestamp_gap_is_filled_without_compressing_time(
    tmp_path: Path,
    gap_end: float,
) -> None:
    source = tmp_path / "gap.mkv"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=32x32:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=2",
            "-filter_complex",
            f"[1:a]aselect='not(between(t,0.9,{gap_end}))'[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-copyts",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        timeout_seconds=20,
    )
    samples = await _pcm(source, await inspect_timeline(source))
    assert _level(samples, 0.4, 0.6) > 0.05
    assert _level(samples, 0.94, 0.96) < 0.00001
    assert _level(samples, 1.5, 1.7) > 0.05


@pytest.mark.timeout(30)
async def test_audio_only_source_has_exact_grid_and_legacy_command_is_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "audio.m4a"
    await run_ffmpeg(
        "ffmpeg",
        ["-f", "lavfi", "-i", "sine=frequency=700:sample_rate=44100:duration=0.751", str(source)],
        timeout_seconds=20,
    )
    timeline = await inspect_timeline(source)
    assert not timeline.has_video
    samples = await _pcm(source, timeline)
    assert _level(samples, 0.1, 0.6) > 0.05
    assert speech_pcm_command(source) == [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
