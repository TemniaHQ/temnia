"""Probe a master with PyAV: the facts every later stage plans from.

The probe is a gate. A file with no decodable stream, or a non-positive
duration, never becomes a source. Embedded cover art (mjpeg "video") is not a
video stream. `r_frame_rate != avg_frame_rate` marks a variable-frame-rate
file, and the ladder then forces a constant rate so frame math holds.

The codec name and the pixel format ride along in `VideoFacts` because the
ladder decides from them whether the GPU can decode the source, and the Modal
image carries no probe of its own.
"""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING

import av
from av.error import FFmpegError

from temnia_pipeline.contracts import ProbeResult
from temnia_pipeline.media.facts import VideoFacts

if TYPE_CHECKING:
    from pathlib import Path

COVER_ART_CODECS = frozenset({"mjpeg", "png", "bmp", "gif"})

__all__ = ["COVER_ART_CODECS", "InvalidMediaError", "VideoFacts", "probe"]


class InvalidMediaError(ValueError):
    """The file cannot be ingested; the message is safe to show a user."""


def _fps(stream: av.VideoStream) -> tuple[Fraction, bool]:
    average = stream.average_rate
    base = stream.base_rate
    rate = average or base or Fraction(25, 1)
    if rate <= 0:
        rate = Fraction(25, 1)
    variable = bool(average and base and average != base)
    return Fraction(rate), variable


def probe(path: Path) -> tuple[ProbeResult, VideoFacts | None]:
    """Open the container, validate it, and return the facts."""
    try:
        container = av.open(str(path))
    except FFmpegError as error:
        msg = "The file could not be read as video or audio."
        raise InvalidMediaError(msg) from error
    with container:
        if container.duration is None or container.duration <= 0:
            msg = "The file has no duration; it may be truncated or not a media file."
            raise InvalidMediaError(msg)
        duration_ms = round(container.duration / av.time_base * 1000)
        video: VideoFacts | None = None
        for stream in container.streams.video:
            name = stream.codec_context.name
            if name in COVER_ART_CODECS or stream.width == 0:
                continue
            fps, variable = _fps(stream)
            video = VideoFacts(
                width=stream.width,
                height=stream.height,
                fps=fps,
                variable_frame_rate=variable,
                codec=name,
                pix_fmt=stream.codec_context.pix_fmt,
            )
            break
        audio = container.streams.audio[0] if container.streams.audio else None
        if video is None and audio is None:
            msg = "The file has no video or audio stream that can be decoded."
            raise InvalidMediaError(msg)
        result = ProbeResult(
            durationMs=duration_ms,
            width=video.width if video else None,
            height=video.height if video else None,
            fps=round(float(video.fps), 3) if video else None,
            audioChannels=audio.channels if audio else None,
            videoCodec=video.codec if video else None,
            audioCodec=audio.codec_context.name if audio else None,
            variableFrameRate=video.variable_frame_rate if video else False,
            sizeBytes=path.stat().st_size,
        )
        return result, video
