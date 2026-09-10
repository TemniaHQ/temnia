"""Shot observations in the selected master's presentation timestamp domain."""

# ruff: noqa: EM101, TRY003

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

from temnia_pipeline.media.ffmpeg import run_ffmpeg

if TYPE_CHECKING:
    from temnia_pipeline.media.chapters import MediaTimelineFacts

DETECTOR_VERSION = "source-scdet/1"
EMIT_FLOOR = 3.0
DECISION_THRESHOLD = 10.0
MAX_SCORE = 100.0
SCALE_HEIGHT = 240
_TIME = re.compile(r"\bpts_time:(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b")
_SCORE = re.compile(r"^lavfi\.scd\.score=(-?\d+(?:\.\d+)?)$")


@dataclass(frozen=True, slots=True)
class NativeShot:
    """A detector timestamp and its native scdet score, not a probability."""

    time: Fraction
    score: float


def parse_source_scdet(text: str) -> tuple[NativeShot, ...]:
    """Read signed source PTS without the old proxy parser's millisecond rounding."""
    result: list[NativeShot] = []
    current: Fraction | None = None
    for line in text.splitlines():
        if match := _TIME.search(line):
            if current is not None:
                raise ValueError("shot metadata timestamp has no score")
            current = Fraction(match.group(1))
        elif match := _SCORE.fullmatch(line.strip()):
            if current is None:
                raise ValueError("shot metadata score has no timestamp")
            score = float(match.group(1))
            if not EMIT_FLOOR <= score <= MAX_SCORE:
                raise ValueError("shot metadata score is outside the detector range")
            if result and current <= result[-1].time:
                raise ValueError("shot metadata timestamps are not strictly increasing")
            result.append(NativeShot(time=current, score=score))
            current = None
    if current is not None:
        raise ValueError("shot metadata timestamp has no score")
    if text.strip() and not result:
        raise ValueError("shot metadata contains no recognized observations")
    return tuple(result)


async def detect_source_shots(
    source_path: Path, *, timeline: MediaTimelineFacts, ffmpeg: str
) -> tuple[NativeShot, ...]:
    """Run the existing scdet algorithm on the verified selected video stream.

    copyts retains the input PTS; the evidence adapter subtracts source_start.
    This is a master-domain baseline, not byte-parity with the old HLS proxy.
    The caller owns timeout, source verification, lease and activity heartbeat.
    """
    if not timeline.has_video or timeline.video_stream_index is None:
        raise ValueError("selected source has no video stream")
    with tempfile.TemporaryDirectory(prefix="temnia-source-shots-") as directory:
        work = Path(directory)
        await run_ffmpeg(
            ffmpeg,
            [
                "-copyts",
                "-i",
                str(source_path.absolute()),
                "-map",
                f"0:{timeline.video_stream_index}",
                "-an",
                "-sn",
                "-vf",
                (
                    f"scale=-2:{SCALE_HEIGHT},scdet=t={EMIT_FLOOR},"
                    "metadata=select:key=lavfi.scd.time,metadata=print:file=scdet.txt"
                ),
                "-f",
                "null",
                "-",
            ],
            cwd=work,
        )
        metadata = work / "scdet.txt"
        if not metadata.exists():
            raise ValueError("detector completed without a metadata artifact")
        return parse_source_scdet(metadata.read_text())
