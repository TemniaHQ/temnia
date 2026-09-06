"""Waveform peaks in peaks.js's JSON v2 format, from the local audio extract.

Two rules carried from the legacy as evidence:
- the x-axis spans the media duration, not the audio stream; short audio is
  padded with silent buckets so the overview and the video timeline agree;
- samples_per_pixel scales with duration so the data is always at least
  ~4096 buckets wide: peaks.js downsamples but never upsamples, and shorter
  data falls back to a fixed px-per-second layout whose axis drifts from the
  player.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import TYPE_CHECKING

import numpy as np

from temnia_pipeline.media.ffmpeg import FfmpegError, sanitize

if TYPE_CHECKING:
    from pathlib import Path

SAMPLE_RATE = 8000
TARGET_WIDTH_PX = 4096
MIN_SAMPLES_PER_PIXEL = 4


def samples_per_pixel(duration_seconds: float) -> int:
    """Buckets sized so a full-length render is about TARGET_WIDTH_PX wide."""
    return max(MIN_SAMPLES_PER_PIXEL, math.ceil(duration_seconds * SAMPLE_RATE / TARGET_WIDTH_PX))


async def decode_mono_pcm(ffmpeg: str, audio: Path) -> np.ndarray:
    """Decode to 8 kHz mono signed 16-bit PCM in memory (2 h is 115 MB)."""
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(audio),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        msg = f"ffmpeg pcm decode exited {process.returncode}: {sanitize(stderr.decode())}"
        raise FfmpegError(msg)
    return np.frombuffer(stdout, dtype="<i2")


def bucket(pcm: np.ndarray, spp: int, duration_seconds: float) -> np.ndarray:
    """Min/max per bucket as int8, padded with silence to the media duration."""
    length = math.ceil(duration_seconds * SAMPLE_RATE / spp)
    needed = length * spp
    samples = pcm[:needed]
    if len(samples) < needed:
        samples = np.concatenate([samples, np.zeros(needed - len(samples), dtype="<i2")])
    blocks = samples.reshape(length, spp)
    mins = np.floor(blocks.min(axis=1) / 256).astype(np.int8)
    maxs = np.floor(blocks.max(axis=1) / 256).astype(np.int8)
    return np.stack([mins, maxs], axis=1).reshape(-1)


async def write_peaks(
    ffmpeg: str, audio: Path, out: Path, duration_seconds: float
) -> dict[str, int]:
    """Write peaks.json and return its metadata."""
    spp = samples_per_pixel(duration_seconds)
    pcm = await decode_mono_pcm(ffmpeg, audio)
    data = bucket(pcm, spp, duration_seconds)
    length = len(data) // 2
    payload = {
        "version": 2,
        "channels": 1,
        "sample_rate": SAMPLE_RATE,
        "samples_per_pixel": spp,
        "bits": 8,
        "length": length,
        "data": data.tolist(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    return {"samples_per_pixel": spp, "length": length, "sample_rate": SAMPLE_RATE}
