"""Cancelable ffmpeg streaming of speech audio as mono 16 kHz float32 windows."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator  # noqa: TC003
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

import numpy as np

SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 512
BYTES_PER_SAMPLE = 4
_TERMINATE_SECONDS = 5


class SpeechDecodeError(RuntimeError):
    """The decoder failed before producing a complete stream."""


@dataclass(frozen=True, slots=True)
class PcmWindow:
    """One model-sized window, with the real length retained before zero padding."""

    samples: np.ndarray
    valid_samples: int
    start_sample: int


def speech_pcm_command(source: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Build the only ffmpeg command used by independent speech coverage."""
    return [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(source),
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


async def stream_pcm_windows(source: Path, *, ffmpeg: str = "ffmpeg") -> AsyncIterator[PcmWindow]:
    """Yield 512-sample windows and zero-pad only the final short window.

    The generator owns its subprocess. Closing or cancelling it always stops
    ffmpeg and drains stderr, including when the consumer leaves early.
    """
    process = await asyncio.create_subprocess_exec(
        *speech_pcm_command(source, ffmpeg),
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
    finally:
        await _stop(process)
        if not stderr_task.done():
            stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
