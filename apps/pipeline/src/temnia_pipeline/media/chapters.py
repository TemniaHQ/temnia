"""Accurate chapter media cuts on one source-relative rational timeline."""

# ruff: noqa: EM101, EM102, PLR0913, TRY003

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, cast

from temnia_pipeline.media.ffmpeg import run_ffmpeg, run_ffprobe
from temnia_pipeline.media.probe import COVER_ART_CODECS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

RENDERER_VERSION = "chapter-media/1-ffmpeg-8.1.2"
AAC_FRAME_SAMPLES = 1024
MAX_AAC_CHANNELS = 6
MAX_X264_CRF = 51
ACCURATE_SEEK_PREROLL_SECONDS = Fraction(5)


class InvalidTimelineError(ValueError):
    """Media has no bounded, decodable timeline suitable for editing."""


@dataclass(frozen=True, slots=True)
class MediaTimelineFacts:
    """Exact selected-stream facts; all times are signed seconds."""

    duration: Fraction
    container_start: Fraction
    source_start: Fraction
    has_video: bool
    has_audio: bool
    video_stream_index: int | None
    audio_stream_index: int | None
    video_start: Fraction | None
    audio_start: Fraction | None
    video_duration: Fraction | None
    audio_duration: Fraction | None
    frame_rate: Fraction | None
    video_time_base: Fraction | None
    audio_time_base: Fraction | None
    sample_rate: int | None
    width: int | None
    height: int | None
    rotation: int | None
    audio_channels: int | None
    audio_layout: str | None
    variable_frame_rate: bool
    video_codec: str | None
    audio_codec: str | None
    sample_aspect_ratio: Fraction | None = None


@dataclass(frozen=True, slots=True)
class ChapterRenderConfig:
    """Versioned output settings included in every media fingerprint."""

    version: str = RENDERER_VERSION
    video_codec: str = "libx264"
    video_preset: str = "medium"
    video_crf: int = 18
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    movflags: str = "+faststart"

    def __post_init__(self) -> None:
        """Refuse settings outside the implemented codec contract."""
        if self.video_codec != "libx264" or self.audio_codec != "aac":
            raise ValueError("chapter renderer supports only libx264 video and AAC audio")
        if not 0 <= self.video_crf <= MAX_X264_CRF:
            raise ValueError("video CRF must be between 0 and 51")


def _fraction(value: object, name: str, *, positive: bool = False) -> Fraction:
    if value is None or isinstance(value, bool):
        raise InvalidTimelineError(f"media {name} is missing")
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise InvalidTimelineError(f"media {name} is invalid") from error
    if positive and result <= 0:
        raise InvalidTimelineError(f"media {name} must be positive")
    return result


def _optional_time(value: object, time_base: Fraction, name: str) -> Fraction | None:
    if value is None or value == "N/A":
        return None
    return _fraction(value, name) * time_base


def _stream_start(stream: dict[str, object], time_base: Fraction, name: str) -> Fraction | None:
    start = _optional_time(stream.get("start_pts"), time_base, name)
    if start is not None:
        return start
    value = stream.get("start_time")
    return _fraction(value, name) if value is not None and value != "N/A" else None


def _optional_rate(value: object, name: str) -> Fraction | None:
    if value is None or value in ("N/A", "0/0"):
        return None
    return _fraction(value, name, positive=True)


def _optional_ratio(value: object, name: str) -> Fraction | None:
    if value is None or value in ("N/A", "0:1"):
        return None
    return _optional_rate(str(value).replace(":", "/"), name)


def _stream_duration(stream: dict[str, object], time_base: Fraction) -> Fraction | None:
    duration_ts = stream.get("duration_ts")
    if duration_ts is not None and duration_ts != "N/A":
        return _fraction(duration_ts, "stream duration") * time_base
    duration = stream.get("duration")
    if duration is None or duration == "N/A":
        tags = stream.get("tags")
        tagged = cast("dict[str, object]", tags).get("DURATION") if isinstance(tags, dict) else None
        if not isinstance(tagged, str):
            return None
        try:
            hours, minutes, seconds = tagged.split(":", 2)
            return Fraction(int(hours) * 3600 + int(minutes) * 60) + Fraction(seconds)
        except (ValueError, ZeroDivisionError) as error:
            raise InvalidTimelineError("media tagged stream duration is invalid") from error
    return _fraction(duration, "stream duration", positive=True)


def _side_data_rotation(item: dict[str, object]) -> int | None:
    side_data = item.get("side_data_list")
    if isinstance(side_data, list):
        for raw in cast("list[object]", side_data):
            if isinstance(raw, dict) and "rotation" in raw:
                return round(float(str(cast("dict[str, object]", raw)["rotation"]))) % 360
    return None


def _rotation(stream: dict[str, object]) -> int | None:
    rotation = _side_data_rotation(stream)
    if rotation is not None:
        return rotation
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        return round(float(str(cast("dict[str, object]", tags)["rotate"]))) % 360
    return None


async def _first_frame_rotation(path: Path, ffprobe: str) -> int:
    """Read display-orientation SEI when the container has no display matrix."""
    raw = await run_ffprobe(
        ffprobe,
        [
            "-select_streams",
            "v:0",
            "-read_intervals",
            "%+#1",
            "-show_frames",
            "-show_entries",
            "frame=side_data_list",
            "-of",
            "json",
            str(path),
        ],
    )
    try:
        frames = cast("list[dict[str, object]]", json.loads(raw).get("frames", []))
    except (AttributeError, TypeError, json.JSONDecodeError):
        return 0
    return next(
        (rotation for frame in frames if (rotation := _side_data_rotation(frame)) is not None),
        0,
    )


def _stream_index(stream: dict[str, object]) -> int:
    value = stream.get("index")
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTimelineError("media stream index is missing or invalid")
    return value


async def inspect_timeline(path: Path, *, ffprobe: str = "ffprobe") -> MediaTimelineFacts:
    """Inspect exact format/stream clocks with bounded ffprobe process ownership."""
    raw = await run_ffprobe(
        ffprobe,
        [
            "-show_entries",
            (
                "format=start_time,duration:"
                "stream=index,codec_type,codec_name,time_base,start_pts,duration_ts,"
                "start_time,duration,r_frame_rate,avg_frame_rate,width,height,sample_rate,"
                "channels,channel_layout,sample_aspect_ratio:stream_tags=rotate,DURATION:"
                "stream_side_data=rotation"
                ":stream_disposition=attached_pic"
            ),
            "-of",
            "json",
            str(path),
        ],
    )
    try:
        payload = cast("dict[str, object]", json.loads(raw))
        format_data = cast("dict[str, object]", payload["format"])
        streams = cast("list[dict[str, object]]", payload["streams"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise InvalidTimelineError("ffprobe returned no usable format timeline") from error
    duration = _fraction(format_data.get("duration"), "duration", positive=True)
    container_start = (
        _fraction(format_data["start_time"], "container start")
        if format_data.get("start_time") not in (None, "N/A")
        else Fraction(0)
    )
    videos = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video"
        and stream.get("codec_name") not in COVER_ART_CODECS
        and int(str(stream.get("width") or 0)) > 0
        and not bool(cast("dict[str, object]", stream.get("disposition") or {}).get("attached_pic"))
    ]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video = videos[0] if videos else None
    audio = audios[0] if audios else None
    if video is None and audio is None:
        raise InvalidTimelineError("media has no usable video or audio stream")

    video_tb = (
        _fraction(video.get("time_base"), "video time base", positive=True) if video else None
    )
    audio_tb = (
        _fraction(audio.get("time_base"), "audio time base", positive=True) if audio else None
    )
    average = _optional_rate(video.get("avg_frame_rate"), "average frame rate") if video else None
    nominal = _optional_rate(video.get("r_frame_rate"), "nominal frame rate") if video else None
    frame_rate = average or nominal
    if video and frame_rate is None:
        raise InvalidTimelineError("media video frame rate is missing")
    try:
        sample_rate = int(str(audio["sample_rate"])) if audio else None
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidTimelineError("media audio sample rate is missing or invalid") from error
    if sample_rate is not None and sample_rate <= 0:
        raise InvalidTimelineError("media audio sample rate must be positive")
    channels = int(str(audio["channels"])) if audio else None
    if channels is not None and not 1 <= channels <= MAX_AAC_CHANNELS:
        raise InvalidTimelineError(
            f"media has {channels} audio channels; chapter AAC supports 1..{MAX_AAC_CHANNELS}"
        )
    rotation = _rotation(video) if video else None
    if video and rotation is None:
        rotation = await _first_frame_rotation(path, ffprobe)
    return MediaTimelineFacts(
        duration=duration,
        container_start=container_start,
        source_start=container_start,
        has_video=video is not None,
        has_audio=audio is not None,
        video_stream_index=_stream_index(video) if video else None,
        audio_stream_index=_stream_index(audio) if audio else None,
        video_start=(
            _stream_start(video, cast("Fraction", video_tb), "video start") if video else None
        ),
        audio_start=(
            _stream_start(audio, cast("Fraction", audio_tb), "audio start") if audio else None
        ),
        video_duration=_stream_duration(video, cast("Fraction", video_tb)) if video else None,
        audio_duration=_stream_duration(audio, cast("Fraction", audio_tb)) if audio else None,
        frame_rate=frame_rate,
        video_time_base=video_tb,
        audio_time_base=audio_tb,
        sample_rate=sample_rate,
        width=int(str(video["width"])) if video else None,
        height=int(str(video["height"])) if video else None,
        rotation=rotation,
        audio_channels=channels,
        audio_layout=(str(audio.get("channel_layout") or "") or None) if audio else None,
        variable_frame_rate=bool(video and average != nominal),
        video_codec=str(video.get("codec_name")) if video else None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
        sample_aspect_ratio=(
            _optional_ratio(video.get("sample_aspect_ratio"), "sample aspect ratio")
            if video
            else None
        ),
    )


def _seconds(value: Fraction) -> str:
    if value < 0:
        raise ValueError("source-relative chapter times cannot be negative")
    return f"{float(value):.12f}".rstrip("0").rstrip(".") or "0"


def _signed_seconds(value: Fraction) -> str:
    return f"{float(value):.12f}".rstrip("0").rstrip(".") or "0"


async def render_chapter(  # noqa: C901, PLR0912, PLR0915
    ffmpeg: str,
    source: Path,
    output: Path,
    *,
    start: Fraction,
    end: Fraction,
    timeline: MediaTimelineFacts,
    config: ChapterRenderConfig | None = None,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    timeout_seconds: float,
) -> None:
    """Decode and reencode one exact source interval on a common timestamp origin.

    Input seeking retains five seconds of decode preroll and `-copyts` keeps
    the signed container clock. Both selected streams trim on that clock and
    subtract the same absolute section-start constant. This creates one local
    origin while retaining real A/V offsets; neither stream independently
    subtracts its own first timestamp.
    """
    if start < 0 or end <= start or end > timeline.duration:
        raise ValueError("chapter interval lies outside the inspected source duration")
    if timeout_seconds <= 0:
        raise ValueError("chapter render timeout must be positive")
    config = config or ChapterRenderConfig()
    if timeline.has_video and timeline.frame_rate is None:
        raise InvalidTimelineError("selected video stream has no frame rate")
    if timeline.has_video and timeline.video_start is None:
        raise InvalidTimelineError("selected video stream has no start timestamp")
    if timeline.has_audio and timeline.audio_start is None:
        raise InvalidTimelineError("selected audio stream has no start timestamp")
    output.parent.mkdir(parents=True, exist_ok=True)
    seek = max(Fraction(0), start - ACCURATE_SEEK_PREROLL_SECONDS)
    absolute_seek = timeline.source_start + seek
    absolute_start = timeline.source_start + start
    absolute_end = timeline.source_start + end
    args: list[str] = []
    if seek > 0:
        args.extend(["-seek_timestamp", "1", "-ss", _signed_seconds(absolute_seek)])
    args.extend(["-copyts", "-noautorotate", "-i", str(source)])
    filters: list[str] = []
    if timeline.video_stream_index is not None:
        video_local_start = max(
            Fraction(0),
            cast("Fraction", timeline.video_start) - timeline.source_start - start,
        )
        filters.append(
            f"[0:{timeline.video_stream_index}]"
            f"trim=start={_signed_seconds(absolute_start)}:"
            f"end={_signed_seconds(absolute_end)},"
            f"setpts=PTS-({_signed_seconds(absolute_start)})/TB,"
            "pad=ceil(iw/2)*2:ceil(ih/2)*2,"
            f"fps={cast('Fraction', timeline.frame_rate).numerator}/"
            f"{cast('Fraction', timeline.frame_rate).denominator}:"
            f"start_time={_seconds(video_local_start)}:round=near,"
            "format=yuv420p[v]"
        )
    if timeline.audio_stream_index is not None:
        filters.append(
            f"[0:{timeline.audio_stream_index}]"
            f"atrim=start={_signed_seconds(absolute_start)}:"
            f"end={_signed_seconds(absolute_end)},"
            f"asetpts=PTS-({_signed_seconds(absolute_start)})/TB[a]"
        )
    args.extend(["-filter_complex", ";".join(filters)])
    if timeline.has_video:
        args.extend(["-map", "[v]"])
    if timeline.has_audio:
        args.extend(["-map", "[a]"])
    args.extend(["-sn", "-dn", "-t", _seconds(end - start)])
    if timeline.has_video:
        args.extend(
            [
                "-fps_mode:v:0",
                "passthrough",
                "-c:v",
                config.video_codec,
                "-preset:v",
                config.video_preset,
                "-crf:v",
                str(config.video_crf),
            ]
        )
        if timeline.rotation:
            args.extend(
                [
                    "-bsf:v:0",
                    (f"h264_metadata=display_orientation=insert:rotate={timeline.rotation}"),
                ]
            )
    else:
        args.append("-vn")
    if timeline.has_audio:
        args.extend(
            [
                "-c:a",
                config.audio_codec,
                "-b:a",
                config.audio_bitrate,
                "-ar",
                str(cast("int", timeline.sample_rate)),
                "-ac",
                str(cast("int", timeline.audio_channels)),
            ]
        )
        if timeline.audio_layout:
            args.extend(["-channel_layout:a:0", timeline.audio_layout])
    else:
        args.append("-an")
    args.extend(
        [
            "-avoid_negative_ts",
            "disabled",
            "-movflags",
            config.movflags,
            "-use_editlist",
            "1",
            str(output),
        ]
    )
    try:
        await run_ffmpeg(
            ffmpeg,
            args,
            on_progress=on_progress,
            timeout_seconds=timeout_seconds,
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    if not output.is_file() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced no chapter media")
