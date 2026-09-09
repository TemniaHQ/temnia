"""Technical checks over actual rendered chapter files."""

# ruff: noqa: EM101, PLR0913, TRY003, TRY301

from __future__ import annotations

import re
from collections import Counter
from fractions import Fraction
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import (
    ChapterCheck,
    ChapterChecks,
    EditorialStatus,
    Status,
)
from temnia_pipeline.media.chapters import AAC_FRAME_SAMPLES, MediaTimelineFacts, inspect_timeline
from temnia_pipeline.media.ffmpeg import FfmpegError, run_ffmpeg

if TYPE_CHECKING:
    from pathlib import Path

_VTT_TIMING = re.compile(
    r"^(?P<sh>\d{2,}):(?P<sm>\d{2}):(?P<ss>\d{2})\.(?P<sms>\d{3})"
    r" --> "
    r"(?P<eh>\d{2,}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})$"
)
SEXAGESIMAL_BASE = 60


def duration_tolerance(timeline: MediaTimelineFacts) -> Fraction:
    """Two output video frames or two AAC frames, whichever is present/larger."""
    tolerances: list[Fraction] = []
    if timeline.frame_rate:
        tolerances.append(Fraction(2, 1) / timeline.frame_rate)
    if timeline.sample_rate:
        tolerances.append(Fraction(2 * AAC_FRAME_SAMPLES, timeline.sample_rate))
    if not tolerances:
        raise ValueError("media timeline has no frame or sample grid")
    return max(tolerances)


def _check(
    section_id: str,
    name: str,
    *,
    passed: bool,
    expected: Fraction | int | None,
    measured: Fraction | int | None,
    message: str,
) -> ChapterCheck:
    return ChapterCheck(
        expected=float(expected) if expected is not None else None,
        measured=float(measured) if measured is not None else None,
        message=message,
        name=name,
        sectionId=section_id,
        status=Status.pass_ if passed else Status.fail,
    )


def _track_checks(
    section_id: str,
    label: str,
    *,
    source_start: Fraction,
    source_track_start: Fraction | None,
    source_track_duration: Fraction | None,
    output_start: Fraction,
    output_track_start: Fraction | None,
    output_track_duration: Fraction | None,
    chapter_start: Fraction,
    chapter_duration: Fraction,
    tolerance: Fraction,
) -> list[ChapterCheck]:
    if source_track_start is None or output_track_start is None:
        return [
            _check(
                section_id,
                f"{label}_start_offset",
                passed=False,
                expected=source_track_start,
                measured=output_track_start,
                message=f"{label} start time is required for precise offset verification",
            ),
            _check(
                section_id,
                f"{label}_end",
                passed=False,
                expected=None,
                measured=None,
                message=f"{label} endpoint is unknown because its start time is missing",
            ),
        ]
    expected_start = max(Fraction(0), source_track_start - source_start - chapter_start)
    measured_start = output_track_start - output_start
    checks = [
        _check(
            section_id,
            f"{label}_start_offset",
            passed=abs(measured_start - expected_start) <= tolerance,
            expected=expected_start,
            measured=measured_start,
            message=f"{label} start offset must retain the source track offset",
        )
    ]
    if source_track_duration is not None and output_track_duration is not None:
        source_end = source_track_start - source_start + source_track_duration
        expected_end = min(chapter_duration, max(Fraction(0), source_end - chapter_start))
        measured_end = measured_start + output_track_duration
        checks.append(
            _check(
                section_id,
                f"{label}_end",
                passed=abs(measured_end - expected_end) <= tolerance,
                expected=expected_end,
                measured=measured_end,
                message=f"{label} endpoint must match the clipped source endpoint",
            )
        )
    else:
        checks.append(
            _check(
                section_id,
                f"{label}_end",
                passed=False,
                expected=source_track_duration,
                measured=output_track_duration,
                message=f"{label} duration is required for precise endpoint verification",
            )
        )
    return checks


async def check_chapter_media(
    path: Path,
    *,
    section_id: str,
    edit_sha256: str,
    source: MediaTimelineFacts,
    start: Fraction,
    end: Fraction,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    timeout_seconds: float = 600,
) -> ChapterChecks:
    """Fully decode and inspect one output; failures remain structured facts."""
    try:
        output = await inspect_timeline(path, ffprobe=ffprobe)
    except Exception as error:  # noqa: BLE001
        return ChapterChecks(
            editorialReasons=[],
            editorialStatus=EditorialStatus.not_run,
            editSha256=edit_sha256,
            technicalChecks=[
                ChapterCheck(
                    expected=None,
                    measured=None,
                    message=f"rendered media cannot be inspected: {type(error).__name__}: {error}",
                    name="inspect",
                    sectionId=section_id,
                    status=Status.fail,
                )
            ],
            verifierFamily=None,
            version=1,
        )
    checks: list[ChapterCheck] = []
    try:
        decoder_errors = await run_ffmpeg(
            ffmpeg,
            [
                "-xerror",
                "-err_detect",
                "explode",
                "-i",
                str(path),
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=timeout_seconds,
        )
    except (FfmpegError, TimeoutError) as error:
        checks.append(
            ChapterCheck(
                expected=None,
                measured=None,
                message=f"full decode failed: {error}",
                name="full_decode",
                sectionId=section_id,
                status=Status.fail,
            )
        )
    else:
        clean_decode = not decoder_errors.strip()
        checks.append(
            ChapterCheck(
                expected=None,
                measured=None,
                message=(
                    "all selected packets decoded successfully"
                    if clean_decode
                    else f"decoder reported an error: {decoder_errors}"
                ),
                name="full_decode",
                sectionId=section_id,
                status=Status.pass_ if clean_decode else Status.fail,
            )
        )
    expected_duration = end - start
    tolerance = duration_tolerance(output)
    checks.extend(
        [
            _check(
                section_id,
                "duration",
                passed=abs(output.duration - expected_duration) <= tolerance,
                expected=expected_duration,
                measured=output.duration,
                message=f"duration tolerance is {float(tolerance):.9f} seconds",
            ),
            _check(
                section_id,
                "video_presence",
                passed=output.has_video == source.has_video,
                expected=int(source.has_video),
                measured=int(output.has_video),
                message="output video presence must match the selected source stream",
            ),
            _check(
                section_id,
                "audio_presence",
                passed=output.has_audio == source.has_audio,
                expected=int(source.has_audio),
                measured=int(output.has_audio),
                message="output audio presence must match the selected source stream",
            ),
        ]
    )
    if output.has_video:
        expected_width = ((source.width or 0) + 1) // 2 * 2
        expected_height = ((source.height or 0) + 1) // 2 * 2
        checks.extend(
            [
                _check(
                    section_id,
                    "video_codec_h264",
                    passed=output.video_codec == "h264",
                    expected=None,
                    measured=None,
                    message=f"measured video codec {output.video_codec!r}; expected h264",
                ),
                _check(
                    section_id,
                    "video_width",
                    passed=output.width == expected_width,
                    expected=expected_width,
                    measured=output.width,
                    message="odd source width may be padded by at most one pixel",
                ),
                _check(
                    section_id,
                    "video_height",
                    passed=output.height == expected_height,
                    expected=expected_height,
                    measured=output.height,
                    message="odd source height may be padded by at most one pixel",
                ),
                _check(
                    section_id,
                    "rotation",
                    passed=output.rotation == source.rotation,
                    expected=source.rotation,
                    measured=output.rotation,
                    message="display rotation metadata must be preserved",
                ),
                _check(
                    section_id,
                    "sample_aspect_ratio",
                    passed=output.sample_aspect_ratio == source.sample_aspect_ratio,
                    expected=source.sample_aspect_ratio,
                    measured=output.sample_aspect_ratio,
                    message="sample aspect ratio must be preserved",
                ),
            ]
        )
        checks.extend(
            _track_checks(
                section_id,
                "video",
                source_start=source.source_start,
                source_track_start=source.video_start,
                source_track_duration=source.video_duration,
                output_start=output.source_start,
                output_track_start=output.video_start,
                output_track_duration=output.video_duration,
                chapter_start=start,
                chapter_duration=expected_duration,
                tolerance=tolerance,
            )
        )
    if output.has_audio:
        checks.extend(
            [
                _check(
                    section_id,
                    "audio_codec_aac",
                    passed=output.audio_codec == "aac",
                    expected=None,
                    measured=None,
                    message=f"measured audio codec {output.audio_codec!r}; expected aac",
                ),
                _check(
                    section_id,
                    "audio_layout",
                    passed=(
                        source.audio_layout is None or output.audio_layout == source.audio_layout
                    ),
                    expected=None,
                    measured=None,
                    message=(
                        f"measured audio layout {output.audio_layout!r}; "
                        f"expected {source.audio_layout!r}"
                    ),
                ),
                _check(
                    section_id,
                    "audio_channels",
                    passed=output.audio_channels == source.audio_channels,
                    expected=source.audio_channels,
                    measured=output.audio_channels,
                    message="audio channel count must be preserved",
                ),
            ]
        )
        checks.extend(
            _track_checks(
                section_id,
                "audio",
                source_start=source.source_start,
                source_track_start=source.audio_start,
                source_track_duration=source.audio_duration,
                output_start=output.source_start,
                output_track_start=output.audio_start,
                output_track_duration=output.audio_duration,
                chapter_start=start,
                chapter_duration=expected_duration,
                tolerance=tolerance,
            )
        )
    return ChapterChecks(
        editorialReasons=[],
        editorialStatus=EditorialStatus.not_run,
        editSha256=edit_sha256,
        technicalChecks=checks,
        verifierFamily=None,
        version=1,
    )


def required_technical_checks(
    source: MediaTimelineFacts, *, captions_expected: bool = True
) -> frozenset[str]:
    """Return the complete check-name contract for the selected source streams."""
    names = {
        "full_decode",
        "duration",
        "video_presence",
        "audio_presence",
    }
    if captions_expected:
        names.add("caption_bounds")
    if source.has_video:
        names.update(
            {
                "video_codec_h264",
                "video_width",
                "video_height",
                "rotation",
                "sample_aspect_ratio",
                "video_start_offset",
                "video_end",
            }
        )
    if source.has_audio:
        names.update(
            {
                "audio_codec_aac",
                "audio_layout",
                "audio_channels",
                "audio_start_offset",
                "audio_end",
            }
        )
    return frozenset(names)


def technical_checks_pass(
    checks: ChapterChecks,
    *,
    source: MediaTimelineFacts,
    captions_expected: bool = True,
) -> bool:
    """Require exactly one passing/warning result for every applicable check name."""
    counts = Counter(check.name for check in checks.technicalChecks)
    required = required_technical_checks(source, captions_expected=captions_expected)
    return all(counts[name] == 1 for name in required) and all(
        check.status != Status.fail for check in checks.technicalChecks
    )


def _vtt_seconds(match: re.Match[str], prefix: str) -> Fraction:
    hours = int(match.group(prefix + "h"))
    minutes = int(match.group(prefix + "m"))
    seconds = int(match.group(prefix + "s"))
    millis = int(match.group(prefix + "ms"))
    if minutes >= SEXAGESIMAL_BASE or seconds >= SEXAGESIMAL_BASE:
        raise ValueError("WebVTT timestamp minutes and seconds must be below 60")
    return Fraction(hours * 3600 + minutes * 60 + seconds) + Fraction(millis, 1000)


def check_chapter_captions(path: Path, *, section_id: str, duration: Fraction) -> ChapterCheck:
    """Parse actual cue lines and require positive bounds inside the chapter."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "WEBVTT":
            raise ValueError("caption file has no WEBVTT header")
        cue_count = 0
        previous_end = Fraction(0)
        for line in lines[1:]:
            if "-->" not in line:
                continue
            match = _VTT_TIMING.fullmatch(line)
            if match is None:
                raise ValueError("caption file contains an invalid cue timing line")
            start = _vtt_seconds(match, "s")
            end = _vtt_seconds(match, "e")
            if start < previous_end or end <= start or end > duration:
                raise ValueError("caption cue is overlapping, nonpositive, or out of bounds")
            previous_end = end
            cue_count += 1
    except (OSError, UnicodeError, ValueError) as error:
        return _check(
            section_id,
            "caption_bounds",
            passed=False,
            expected=duration,
            measured=None,
            message=f"caption verification failed: {error}",
        )
    return _check(
        section_id,
        "caption_bounds",
        passed=True,
        expected=duration,
        measured=cue_count,
        message=f"{cue_count} cues are ordered within the chapter duration",
    )
