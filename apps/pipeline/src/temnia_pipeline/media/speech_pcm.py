"""Cancelable ffmpeg streaming of speech audio as mono 16 kHz float32 windows."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator  # noqa: TC003
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from temnia_pipeline.media.chapters import MediaTimelineFacts

SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 512
BYTES_PER_SAMPLE = 4
_TERMINATE_SECONDS = 5
SOURCE_PCM_VERSION = "source-grid-pcm/1-ffmpeg-8.1.2"


class SpeechDecodeError(RuntimeError):
    """The decoder failed before producing a complete stream."""


@dataclass(frozen=True, slots=True)
class PcmWindow:
    """One model-sized window, with the real length retained before zero padding."""

    samples: np.ndarray
    valid_samples: int
    start_sample: int


def source_pcm_sample_count(timeline: MediaTimelineFacts) -> int:
    """Represent the selected source duration to the nearest 16 kHz sample."""
    if timeline.duration <= 0:
        raise ValueError("source PCM duration must be positive")
    return round(timeline.duration * SAMPLE_RATE)


def speech_pcm_command(
    source: Path, ffmpeg: str = "ffmpeg", *, timeline: MediaTimelineFacts | None = None
) -> list[str]:
    """Build the only ffmpeg command used by independent speech coverage."""
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
    ]
    if timeline is not None:
        if (
            not timeline.has_audio
            or timeline.audio_stream_index is None
            or timeline.audio_start is None
        ):
            raise ValueError("source PCM requires an inspected selected audio stream")
        command.extend(["-copyts"])
    command.extend(
        [
            "-i",
            str(source),
        ]
    )
    if timeline is not None:
        sample_count = source_pcm_sample_count(timeline)
        absolute_start = timeline.source_start
        absolute_end = absolute_start + timeline.duration
        # The common source origin is retained; do not subtract the first audio
        # PTS. async=1 fills/trims timestamp gaps, without stretching playback.
        command.extend(
            [
                "-map",
                f"0:{timeline.audio_stream_index}",
                "-af",
                (
                    f"atrim=start={float(absolute_start):.12f}:end={float(absolute_end):.12f},"
                    f"asetpts=PTS-({absolute_start.numerator}/{absolute_start.denominator})/TB,"
                    f"aresample={SAMPLE_RATE}:async=1:min_hard_comp=0:first_pts=0,"
                    f"apad=whole_len={sample_count},atrim=end_sample={sample_count}"
                ),
            ]
        )
    command.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "pipe:1",
        ]
    )
    return command


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Bound shutdown so cancellation cannot orphan a decoder."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        async with asyncio.timeout(_TERMINATE_SECONDS):
            await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()


async def stream_pcm_windows(
    source: Path, *, ffmpeg: str = "ffmpeg", timeline: MediaTimelineFacts | None = None
) -> AsyncGenerator[PcmWindow]:
    """Yield 512-sample windows and zero-pad only the final short window.

    The generator owns its subprocess. Closing or cancelling it always stops
    ffmpeg and drains stderr, including when the consumer leaves early.
    """
    process = await asyncio.create_subprocess_exec(
        *speech_pcm_command(source, ffmpeg, timeline=timeline),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        await _stop(process)
        raise SpeechDecodeError("ffmpeg did not expose stdout and stderr")
    stderr_task = asyncio.create_task(process.stderr.read())
    pending = bytearray()
    start_sample = 0
    window_bytes = WINDOW_SAMPLES * BYTES_PER_SAMPLE
    try:
        while chunk := await process.stdout.read(64 * 1024):
            pending.extend(chunk)
            while len(pending) >= window_bytes:
                raw = bytes(pending[:window_bytes])
                del pending[:window_bytes]
                samples = np.frombuffer(raw, dtype="<f4").copy()
                yield PcmWindow(
                    samples=samples, valid_samples=WINDOW_SAMPLES, start_sample=start_sample
                )
                start_sample += WINDOW_SAMPLES
        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace").strip()
        if return_code != 0:
            detail = stderr[-1000:] if stderr else "no decoder detail"
            raise SpeechDecodeError(f"ffmpeg exited {return_code}: {detail}")
        if timeline is not None and len(pending) % BYTES_PER_SAMPLE:
            raise SpeechDecodeError("decoded source PCM ends inside a float32 sample")
        if pending:
            valid_samples = len(pending) // BYTES_PER_SAMPLE
            usable = valid_samples * BYTES_PER_SAMPLE
            samples = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
            if usable:
                samples[:valid_samples] = np.frombuffer(bytes(pending[:usable]), dtype="<f4")
            yield PcmWindow(
                samples=samples,
                valid_samples=valid_samples,
                start_sample=start_sample,
            )
            start_sample += valid_samples
        if timeline is not None and start_sample != source_pcm_sample_count(timeline):
            raise SpeechDecodeError("decoded source PCM does not cover its exact source grid")
    finally:
        await _stop(process)
        if not stderr_task.done():
            stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
