"""Real ffmpeg shot detection retains selected-stream presentation timestamps."""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING

import pytest

from temnia_pipeline.media.chapters import inspect_timeline
from temnia_pipeline.media.ffmpeg import run_ffmpeg
from temnia_pipeline.media.source_shots import detect_source_shots

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.timeout(30)
@pytest.mark.parametrize("offset", [0, 2])
async def test_real_source_pts_mapping_retains_the_original_clock(
    tmp_path: Path, offset: int
) -> None:
    source = tmp_path / "source.mkv"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=white:s=64x64:r=25:d=1",
            "-filter_complex",
            f"[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS+{offset}/TB[v]",
            "-map",
            "[v]",
            "-c:v",
            "ffv1",
            str(source),
        ],
    )
    timeline = await inspect_timeline(source)
    shots = await detect_source_shots(source, timeline=timeline, ffmpeg="ffmpeg")
    assert len(shots) == 1
    assert timeline.source_start == Fraction(offset)
    assert shots[0].time == Fraction(offset + 1)
    assert shots[0].time - timeline.source_start == 1


@pytest.mark.timeout(30)
async def test_real_uncut_video_is_a_successful_empty_measurement(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    await run_ffmpeg(
        "ffmpeg",
        ["-f", "lavfi", "-i", "color=black:s=64x64:r=25:d=1", "-c:v", "ffv1", str(source)],
    )
    timeline = await inspect_timeline(source)
    assert await detect_source_shots(source, timeline=timeline, ffmpeg="ffmpeg") == ()
