"""The HLS ladder: one ffmpeg pass from the local master to every rendition.

Rungs: the native height capped at 1080 at CRF 20, then 720p and 360p proxies
when the source is taller than each, all capped-CRF (`-crf` with `-maxrate`
and a 2x `-bufsize`, Apple's VOD peak rule), 2-second GOPs with forced
keyframes so every rung cuts at the same instants, fMP4 segments with
independent segments, and an audio group at 128k stereo. The same pass writes
a separate intra-only I-frame rendition (one frame per GOP, 360p, single
file with byte ranges): ffmpeg's `iframes_only` flag cannot produce a
companion playlist over the ladder's own segments (verified on 8.1.2, it
writes whole-segment byte ranges), so a dedicated rendition is the honest
way to give the scrubber and the S8 filmstrips keyframe access.

Duration is verified on every playlist before anything is uploaded: ffmpeg
treats a dropped input as end-of-file and exits 0, so a truncated ladder
looks healthy until a player reads it (the legacy shipped 58% of a 2-hour
source that way). Reading from local disk removes the network cause; the
assertion stays because it is the thing that keeps a miss from reaching users.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from temnia_pipeline.media.ffmpeg import run_ffmpeg

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from fractions import Fraction
    from pathlib import Path

    from temnia_pipeline.media.probe import VideoFacts

SEGMENT_SECONDS = 2
KEYFRAME_SECONDS = 2
IFRAME_HEIGHT = 360
AUDIO_BITRATE = "128k"
_EXTINF = re.compile(r"^#EXTINF:([0-9.]+)", re.MULTILINE)


class TruncatedOutputError(RuntimeError):
    """A playlist covers less than the source; the output must not ship."""


@dataclass(frozen=True, slots=True)
class Rung:
    """One video rendition."""

    name: str
    height: int
    crf: int
    maxrate_k: int
    preset: str


def top_maxrate_k(height: int, fps: Fraction) -> int:
    """Peak bitrate for the top rung by height, 1.5x above 40 fps."""
    if height >= 1080:  # noqa: PLR2004
        base = 6000
    elif height >= 720:  # noqa: PLR2004
        base = 4500
    elif height >= 480:  # noqa: PLR2004
        base = 2000
    else:
        base = 1200
    return int(base * 1.5) if fps > 40 else base  # noqa: PLR2004


def plan_rungs(video: VideoFacts) -> list[Rung]:
    """The ladder for this source, top rung first (Safari plays the first variant)."""
    top = min(video.height, 1080)
    top = top - (top % 2)
    rungs = [Rung("top", top, 20, top_maxrate_k(top, video.fps), "fast")]
    if top > 720:  # noqa: PLR2004
        rungs.append(Rung("720p", 720, 22, 3000, "veryfast"))
    if top > 360:  # noqa: PLR2004
        rungs.append(Rung("360p", 360, 23, 900, "veryfast"))
    return rungs


def gop_frames(fps: Fraction) -> int:
    """Frames per keyframe interval, rounded to the nearest whole frame."""
    return max(1, round(float(fps) * KEYFRAME_SECONDS))


def ladder_args(  # noqa: PLR0913
    master: Path,
    out_dir: Path,
    video: VideoFacts | None,
    *,
    has_audio: bool,
    rungs: list[Rung],
    iframes: bool,
) -> list[str]:
    """Build the single ffmpeg invocation for the ladder, audio, and I-frames."""
    args: list[str] = ["-i", str(master)]
    if video is None:
        # Audio-only source: one audio rendition is the whole ladder.
        return [
            *args,
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "hls",
            "-hls_time",
            str(SEGMENT_SECONDS),
            "-hls_playlist_type",
            "vod",
            "-hls_list_size",
            "0",
            "-hls_segment_type",
            "fmp4",
            "-hls_flags",
            "independent_segments",
            "-hls_segment_filename",
            str(out_dir / "audio" / "seg_%05d.m4s"),
            "-master_pl_name",
            "master.m3u8",
            "-var_stream_map",
            "a:0,name:audio,default:yes",
            str(out_dir / "%v" / "index.m3u8"),
        ]

    splits = len(rungs) + (1 if iframes else 0)
    labels = [f"[v{i}]" for i in range(splits)]
    chains = [f"[0:v]split={splits}" + "".join(f"[s{i}]" for i in range(splits))]
    fps_filter = f"fps={video.fps}," if video.variable_frame_rate else ""
    for i, rung in enumerate(rungs):
        chains.append(f"[s{i}]{fps_filter}scale=-2:{rung.height}[v{i}]")
    if iframes:
        i = len(rungs)
        chains.append(f"[s{i}]{fps_filter}fps=1/{KEYFRAME_SECONDS},scale=-2:{IFRAME_HEIGHT}[v{i}]")
    args += ["-filter_complex", ";".join(chains)]
    for label in labels[: len(rungs)]:
        args += ["-map", label]
    if has_audio:
        args += ["-map", "0:a:0"]
    gop = gop_frames(video.fps)
    args += [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{KEYFRAME_SECONDS})",
    ]
    for i, rung in enumerate(rungs):
        args += [
            f"-preset:v:{i}",
            rung.preset,
            f"-crf:v:{i}",
            str(rung.crf),
            f"-maxrate:v:{i}",
            f"{rung.maxrate_k}k",
            f"-bufsize:v:{i}",
            f"{rung.maxrate_k * 2}k",
        ]
    if has_audio:
        args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", "-ar", "48000"]
    stream_map = " ".join(
        f"v:{i},agroup:aud,name:{rung.name}" if has_audio else f"v:{i},name:{rung.name}"
        for i, rung in enumerate(rungs)
    )
    if has_audio:
        stream_map += " a:0,agroup:aud,name:audio,default:yes"
    args += [
        "-f",
        "hls",
        "-hls_time",
        str(SEGMENT_SECONDS),
        "-hls_playlist_type",
        "vod",
        "-hls_list_size",
        "0",
        "-hls_segment_type",
        "fmp4",
        "-hls_flags",
        "independent_segments",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_segment_filename",
        str(out_dir / "%v" / "seg_%05d.m4s"),
        "-master_pl_name",
        "master.m3u8",
        "-var_stream_map",
        stream_map,
        str(out_dir / "%v" / "index.m3u8"),
    ]
    if iframes:
        iframe_dir = out_dir / "iframes"
        args += [
            "-map",
            labels[-1],
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "26",
            "-g",
            "1",
            "-x264-params",
            "keyint=1:min-keyint=1:scenecut=0",
            "-f",
            "hls",
            "-hls_time",
            str(KEYFRAME_SECONDS),
            "-hls_playlist_type",
            "vod",
            "-hls_list_size",
            "0",
            "-hls_segment_type",
            "fmp4",
            "-hls_flags",
            "single_file+independent_segments",
            "-hls_fmp4_init_filename",
            "init.mp4",
            "-hls_segment_filename",
            str(iframe_dir / "iframes.mp4"),
            str(iframe_dir / "index.m3u8"),
        ]
    return args


async def transcode_ladder(  # noqa: PLR0913
    ffmpeg: str,
    master: Path,
    out_dir: Path,
    video: VideoFacts | None,
    *,
    has_audio: bool,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> list[Rung]:
    """Run the ladder and return the rungs it produced."""
    rungs = plan_rungs(video) if video else []
    out_dir.mkdir(parents=True, exist_ok=True)
    for rung in rungs:
        (out_dir / rung.name).mkdir(exist_ok=True)
    if has_audio:
        (out_dir / "audio").mkdir(exist_ok=True)
    if video:
        (out_dir / "iframes").mkdir(exist_ok=True)
    args = ladder_args(
        master, out_dir, video, has_audio=has_audio, rungs=rungs, iframes=video is not None
    )
    await run_ffmpeg(ffmpeg, args, on_progress=on_progress)
    if video:
        mark_iframe_playlist(out_dir / "iframes" / "index.m3u8")
        append_iframe_variant(out_dir / "master.m3u8", out_dir / "iframes", duration_hint=None)
    return rungs


def playlist_seconds(playlist: Path) -> float:
    """Sum of EXTINF, the playable length a player will report."""
    text = playlist.read_text()
    if "#EXT-X-ENDLIST" not in text:
        msg = f"{playlist.name} has no ENDLIST; the encode did not finish"
        raise TruncatedOutputError(msg)
    return sum(float(m.group(1)) for m in _EXTINF.finditer(text))


def duration_tolerance(expected_seconds: float) -> float:
    """1% of the source or 12 s, whichever is larger; AAC priming adds nothing near that."""
    return max(12.0, expected_seconds * 0.01)


def assert_covers(playlist: Path, expected_seconds: float, *, floor_seconds: float = 0.0) -> float:
    """Fail unless the playlist covers the source within tolerance; returns its length."""
    actual = playlist_seconds(playlist)
    if actual < expected_seconds - duration_tolerance(expected_seconds) - floor_seconds:
        msg = (
            f"{playlist.parent.name}/{playlist.name} covers {actual:.1f}s of "
            f"{expected_seconds:.1f}s; the output is truncated"
        )
        raise TruncatedOutputError(msg)
    return actual


def mark_iframe_playlist(playlist: Path) -> None:
    """Declare the intra-only rendition as an I-frame playlist."""
    text = playlist.read_text()
    if "#EXT-X-I-FRAMES-ONLY" in text:
        return
    lines = text.splitlines()
    insert_at = next(
        (i + 1 for i, line in enumerate(lines) if line.startswith("#EXT-X-INDEPENDENT-SEGMENTS")),
        1,
    )
    lines.insert(insert_at, "#EXT-X-I-FRAMES-ONLY")
    playlist.write_text("\n".join(lines) + "\n")


def append_iframe_variant(master: Path, iframe_dir: Path, *, duration_hint: float | None) -> None:
    """Add the I-frame stream to the master playlist."""
    text = master.read_text()
    if "#EXT-X-I-FRAME-STREAM-INF" in text:
        return
    size = (iframe_dir / "iframes.mp4").stat().st_size
    seconds = duration_hint or playlist_seconds(iframe_dir / "index.m3u8") or 1.0
    bandwidth = max(1, int(size * 8 / seconds))
    width = _iframe_width(iframe_dir)
    line = (
        f"#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{IFRAME_HEIGHT},"
        f'CODECS="avc1.64001e",URI="iframes/index.m3u8"'
    )
    if "#EXT-X-VERSION:" in text:
        text = re.sub(r"#EXT-X-VERSION:\d+", "#EXT-X-VERSION:7", text, count=1)
    master.write_text(text.rstrip("\n") + "\n" + line + "\n")


def _iframe_width(iframe_dir: Path) -> int:
    marker = iframe_dir / "width.txt"
    if marker.exists():
        return int(marker.read_text().strip())
    return 640
