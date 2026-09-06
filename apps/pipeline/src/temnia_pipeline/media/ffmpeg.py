"""Run ffmpeg and ffprobe as subprocesses, with progress and stderr capture.

Errors quote the tail of stderr with every `?query` stripped, because ffmpeg
echoes its input URL and that URL may carry a signature.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

PROGRESS_ARGS = ("-progress", "pipe:1", "-nostats")
_OUT_TIME_US = re.compile(r"^out_time_us=(\d+)$")
_QUERY = re.compile(r"\?\S+")
STDERR_TAIL = 4000


class FfmpegError(RuntimeError):
    """ffmpeg exited non-zero; the message is sanitised."""


def sanitize(text: str) -> str:
    """Strip query strings (presigned signatures) and bound the length."""
    return _QUERY.sub("?…", text)[-STDERR_TAIL:]


async def run_ffmpeg(
    ffmpeg: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
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

    async def read_stderr() -> bytes:
        return await stderr_pipe.read()

    _, stderr, code = await asyncio.gather(read_progress(), read_stderr(), process.wait())
    text = stderr.decode("utf-8", "replace")
    if code != 0:
        msg = f"ffmpeg exited {code}: {sanitize(text)}"
        raise FfmpegError(msg)
    return text


async def run_ffprobe(ffprobe: str, args: list[str], *, cwd: Path | None = None) -> str:
    """Run ffprobe and return stdout."""
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-hide_banner",
        "-v",
        "error",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        msg = f"ffprobe exited {process.returncode}: {sanitize(stderr.decode('utf-8', 'replace'))}"
        raise FfmpegError(msg)
    return stdout.decode("utf-8", "replace")
