"""Thumbnails and the shot grid, from the lowest local rung in one decode.

Thumbnails: a poster at min(25% of duration, 30 s) to dodge black lead-ins,
plus a strip of 320-px frames at an interval that caps the strip near 120
images. `format=yuvj420p` is required: the mjpeg encoder refuses limited-range
YUV. `fps=1/N:round=up` is required: without it a source shorter than N
seconds writes zero thumbnails and exits 0.

Shots: `scdet` scores every frame; frames at or above the emit floor are kept
with their score so the decision threshold (0.3 in ffmpeg's `select=scene`
scale, ~10 in scdet's 0-100 scale) can be retuned without re-decoding.
"""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING

from temnia_pipeline.media.ffmpeg import run_ffmpeg

if TYPE_CHECKING:
    from pathlib import Path

POSTER_WIDTH = 1280
STRIP_WIDTH = 320
STRIP_MAX_IMAGES = 120
STRIP_MIN_INTERVAL = 10
SCDET_EMIT_FLOOR = 3.0
SHOT_DECISION_THRESHOLD = 10.0
_SCD_LINE = re.compile(r"pts_time:(?P<t>[0-9.]+)")
_SCD_SCORE = re.compile(r"lavfi\.scd\.score=(?P<s>[0-9.]+)")


def strip_interval(duration_seconds: float) -> int:
    """Seconds between strip frames."""
    return max(STRIP_MIN_INTERVAL, math.ceil(duration_seconds / STRIP_MAX_IMAGES))


def poster_offset(duration_seconds: float) -> float:
    """Where the poster frame is taken."""
    return min(duration_seconds * 0.25, 30.0)


async def write_thumbnails(
    ffmpeg: str, rung_playlist: Path, out_dir: Path, duration_seconds: float
) -> dict[str, int]:
    """Poster plus strip; returns interval and count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(
        ffmpeg,
        [
            "-ss",
            f"{poster_offset(duration_seconds):.3f}",
            "-i",
            str(rung_playlist),
            "-frames:v",
            "1",
            "-vf",
            f"scale={POSTER_WIDTH}:-2,format=yuvj420p",
            "-q:v",
            "2",
            str(out_dir / "poster.jpg"),
        ],
    )
    interval = strip_interval(duration_seconds)
    await run_ffmpeg(
        ffmpeg,
        [
            "-i",
            str(rung_playlist),
            "-an",
            "-sn",
            "-vf",
            f"fps=1/{interval}:round=up,scale={STRIP_WIDTH}:-2,format=yuvj420p",
            "-q:v",
            "4",
            str(out_dir / "thumb_%05d.jpg"),
        ],
    )
    count = len(list(out_dir.glob("thumb_*.jpg")))
    return {"interval_seconds": interval, "count": count}


def parse_scdet(stderr_and_stdout: str) -> list[dict[str, float]]:
    """Pair each `pts_time` line with the `lavfi.scd.score` that follows it."""
    shots: list[dict[str, float]] = []
    current: float | None = None
    for line in stderr_and_stdout.splitlines():
        t = _SCD_LINE.search(line)
        if t:
            current = float(t.group("t"))
            continue
        s = _SCD_SCORE.search(line)
        if s and current is not None:
            score = float(s.group("s"))
            if score >= SCDET_EMIT_FLOOR:
                shots.append({"t": round(current, 3), "score": round(score, 3)})
            current = None
    return shots


async def write_shots(ffmpeg: str, rung_playlist: Path, out: Path) -> dict[str, float | int]:
    """Scene-change candidates with scores; the consumer applies the threshold."""
    metadata_file = out.parent / "scdet.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(
        ffmpeg,
        [
            "-i",
            str(rung_playlist),
            "-an",
            "-sn",
            "-vf",
            (
                f"scale=-2:240,scdet=t={SCDET_EMIT_FLOOR},metadata=select:key=lavfi.scd.time,"
                f"metadata=print:file={metadata_file}"
            ),
            "-f",
            "null",
            "-",
        ],
    )
    shots = parse_scdet(metadata_file.read_text()) if metadata_file.exists() else []
    payload = {
        "version": 1,
        "emit_floor": SCDET_EMIT_FLOOR,
        "decision_threshold": SHOT_DECISION_THRESHOLD,
        "shots": shots,
    }
    out.write_text(json.dumps(payload, separators=(",", ":")))
    metadata_file.unlink(missing_ok=True)
    return {"count": len(shots), "decision_threshold": SHOT_DECISION_THRESHOLD}
