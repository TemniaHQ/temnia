"""Exact selected-stream facts as a frozen identity, and back.

This module imports only the media planner. The Modal render container uses it to
check a master against the frozen evidence timeline, and that container carries none of
the worker's database, durable-runtime or PyAV dependencies.
"""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING, cast

from temnia_pipeline.media.chapters import MediaTimelineFacts

if TYPE_CHECKING:
    from collections.abc import Mapping


def fraction_json(value: Fraction) -> dict[str, int]:
    """An exact rational as JSON: numerator and denominator, never a float."""
    return {"numerator": value.numerator, "denominator": value.denominator}


def timeline_identity(timeline: MediaTimelineFacts) -> dict[str, object]:
    """Serialize exact selected-stream facts for an artifact fingerprint."""
    return {
        "audioChannels": timeline.audio_channels,
        "audioCodec": timeline.audio_codec,
        "audioDuration": (
            fraction_json(timeline.audio_duration) if timeline.audio_duration is not None else None
        ),
        "audioLayout": timeline.audio_layout,
        "audioStart": (
            fraction_json(timeline.audio_start) if timeline.audio_start is not None else None
        ),
        "audioStreamIndex": timeline.audio_stream_index,
        "audioTimeBase": (
            fraction_json(timeline.audio_time_base)
            if timeline.audio_time_base is not None
            else None
        ),
        "containerStart": fraction_json(timeline.container_start),
        "duration": fraction_json(timeline.duration),
        "frameRate": (
            fraction_json(timeline.frame_rate) if timeline.frame_rate is not None else None
        ),
        "hasAudio": timeline.has_audio,
        "hasVideo": timeline.has_video,
        "height": timeline.height,
        "rotation": timeline.rotation,
        "sampleAspectRatio": (
            fraction_json(timeline.sample_aspect_ratio)
            if timeline.sample_aspect_ratio is not None
            else None
        ),
        "sampleRate": timeline.sample_rate,
        "sourceStart": fraction_json(timeline.source_start),
        "videoStart": (
            fraction_json(timeline.video_start) if timeline.video_start is not None else None
        ),
        "videoCodec": timeline.video_codec,
        "videoDuration": (
            fraction_json(timeline.video_duration) if timeline.video_duration is not None else None
        ),
        "videoStreamIndex": timeline.video_stream_index,
        "videoTimeBase": (
            fraction_json(timeline.video_time_base)
            if timeline.video_time_base is not None
            else None
        ),
        "variableFrameRate": timeline.variable_frame_rate,
        "width": timeline.width,
    }


def timeline_from_identity(value: Mapping[str, object]) -> MediaTimelineFacts:
    """Rebuild exact selected-stream facts from their frozen identity."""

    def fraction(name: str, *, optional: bool = False) -> Fraction | None:
        raw = value.get(name)
        if raw is None and optional:
            return None
        if not isinstance(raw, dict):
            msg = "frozen timeline rational is invalid"
            raise TypeError(msg)
        parts = cast("dict[str, object]", raw)
        return Fraction(int(str(parts["numerator"])), int(str(parts["denominator"])))

    return MediaTimelineFacts(
        duration=cast("Fraction", fraction("duration")),
        container_start=cast("Fraction", fraction("containerStart")),
        source_start=cast("Fraction", fraction("sourceStart")),
        has_video=bool(value.get("hasVideo")),
        has_audio=bool(value.get("hasAudio")),
        video_stream_index=cast("int | None", value.get("videoStreamIndex")),
        audio_stream_index=cast("int | None", value.get("audioStreamIndex")),
        video_start=fraction("videoStart", optional=True),
        audio_start=fraction("audioStart", optional=True),
        video_duration=fraction("videoDuration", optional=True),
        audio_duration=fraction("audioDuration", optional=True),
        frame_rate=fraction("frameRate", optional=True),
        video_time_base=fraction("videoTimeBase", optional=True),
        audio_time_base=fraction("audioTimeBase", optional=True),
        sample_rate=cast("int | None", value.get("sampleRate")),
        width=cast("int | None", value.get("width")),
        height=cast("int | None", value.get("height")),
        rotation=cast("int | None", value.get("rotation")),
        audio_channels=cast("int | None", value.get("audioChannels")),
        audio_layout=cast("str | None", value.get("audioLayout")),
        variable_frame_rate=bool(value.get("variableFrameRate")),
        video_codec=cast("str | None", value.get("videoCodec")),
        audio_codec=cast("str | None", value.get("audioCodec")),
        sample_aspect_ratio=fraction("sampleAspectRatio", optional=True),
    )
