"""Run ffmpeg and ffprobe as subprocesses, with progress and stderr capture.

Errors quote the tail of stderr with every `?query` stripped, because ffmpeg
echoes its input URL and that URL may carry a signature.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

PROGRESS_ARGS = ("-progress", "pipe:1", "-nostats")
_OUT_TIME_US = re.compile(r"^out_time_us=(\d+)$")
_QUERY = re.compile(r"\?\S+")
STDERR_TAIL = 4000
PROCESS_STOP_SECONDS = 5.0
FFPROBE_TIMEOUT_SECONDS = 60.0
FFPROBE_STDOUT_BYTES = 16 * 1024 * 1024


class FfmpegError(RuntimeError):
    """ffmpeg exited non-zero; the message is sanitised."""


def sanitize(text: str) -> str:
    """Strip query strings (presigned signatures) and bound the length."""
    return _QUERY.sub("?…", text)[-STDERR_TAIL:]


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate the owned process group, escalate within five seconds, and reap it."""
    if process.returncode is not None:
        await process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    try:
        async with asyncio.timeout(PROCESS_STOP_SECONDS):
            await process.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def _cleanup_process(
    process: asyncio.subprocess.Process, tasks: tuple[asyncio.Task[object], ...]
) -> None:
    await _stop_process(process)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _shield_cleanup(
    process: asyncio.subprocess.Process, tasks: tuple[asyncio.Task[object], ...]
) -> None:
    cleanup = asyncio.create_task(_cleanup_process(process, tasks))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


async def _read_tail(pipe: asyncio.StreamReader) -> bytes:
    tail = bytearray()
    while chunk := await pipe.read(64 * 1024):
        tail.extend(chunk)
        if len(tail) > STDERR_TAIL:
            del tail[:-STDERR_TAIL]
    return bytes(tail)


async def _read_bounded(pipe: asyncio.StreamReader, limit: int) -> bytes:
    body = bytearray()
    while chunk := await pipe.read(min(64 * 1024, limit + 1 - len(body))):
        body.extend(chunk)
        if len(body) > limit:
            message = f"ffprobe output exceeds {limit} bytes"
            raise FfmpegError(message)
    return bytes(body)


async def run_ffmpeg(
    ffmpeg: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Run ffmpeg; `on_progress` receives seconds of output produced so far.

    Progress comes from `-progress pipe:1`, reading only `out_time_us`
    (`out_time_ms` is microseconds too, despite its name). The callback is
    awaited on every block; the caller throttles.
    """
    command = [ffmpeg, "-hide_banner", "-y", "-v", "error", *PROGRESS_ARGS, *args]
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr_pipe = process.stdout, process.stderr
    if stdout is None or stderr_pipe is None:
        msg = "ffmpeg pipes were not created"
        raise RuntimeError(msg)

    async def read_progress() -> None:
        async for raw in stdout:
            match = _OUT_TIME_US.match(raw.decode("ascii", "replace").strip())
            if match and on_progress is not None:
                await on_progress(int(match.group(1)) / 1_000_000)

    progress_task = asyncio.create_task(read_progress())
    stderr_task = asyncio.create_task(_read_tail(stderr_pipe))
    wait_task = asyncio.create_task(process.wait())
    tasks: tuple[asyncio.Task[object], ...] = (progress_task, stderr_task, wait_task)
    try:
        if timeout_seconds is None:
            _, stderr, code = await asyncio.gather(*tasks)
        else:
            async with asyncio.timeout(timeout_seconds):
                _, stderr, code = await asyncio.gather(*tasks)
    except BaseException:
        await _shield_cleanup(process, tasks)
        raise
    text = stderr.decode("utf-8", "replace")
    if code != 0:
        msg = f"ffmpeg exited {code}: {sanitize(text)}"
        raise FfmpegError(msg)
    return text


async def run_ffprobe(
    ffprobe: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = FFPROBE_TIMEOUT_SECONDS,
) -> str:
    """Run ffprobe and return stdout."""
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-hide_banner",
        "-v",
        "error",
        *args,
        cwd=cwd,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_pipe, stderr_pipe = process.stdout, process.stderr
    if stdout_pipe is None or stderr_pipe is None:
        msg = "ffprobe pipes were not created"
        raise RuntimeError(msg)
    stdout_task = asyncio.create_task(_read_bounded(stdout_pipe, FFPROBE_STDOUT_BYTES))
    stderr_task = asyncio.create_task(_read_tail(stderr_pipe))
    wait_task = asyncio.create_task(process.wait())
    tasks: tuple[asyncio.Task[object], ...] = (stdout_task, stderr_task, wait_task)
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr, _code = await asyncio.gather(*tasks)
    except BaseException:
        await _shield_cleanup(process, tasks)
        raise
    if process.returncode != 0:
        msg = f"ffprobe exited {process.returncode}: {sanitize(stderr.decode('utf-8', 'replace'))}"
        raise FfmpegError(msg)
    return stdout.decode("utf-8", "replace")
