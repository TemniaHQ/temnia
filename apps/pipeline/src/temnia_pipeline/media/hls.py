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

The same plan runs on the worker's CPU ffmpeg and on an L4 inside a Modal
function. Two things change there. The video rungs encode with `h264_nvenc`,
while the intra-only rendition stays on libx264 because one frame every two
seconds is not worth a GPU. And when the source is one NVDEC can read
(`cuda_decodable`), decode and scale move onto the GPU as well: the first
staging ladder decoded and scaled on the CPU and spent 22 minutes on a 2:31
1080p25 master with the four cores pinned and the GPU near twenty percent, so
the CPU, not the encoder, was the ladder. Everything NVDEC cannot read —
ProRes, 10-bit, 4:2:2 — keeps the CPU graph, and so does a run whose CUDA
attempt failed.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from temnia_pipeline.media.ffmpeg import run_ffmpeg
from temnia_pipeline.media.hls_inventory import local_inventory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from fractions import Fraction
    from pathlib import Path

    from temnia_pipeline.media.facts import VideoFacts

SEGMENT_SECONDS = 2
KEYFRAME_SECONDS = 2
IFRAME_HEIGHT = 360
AUDIO_BITRATE = "128k"
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
IFRAMES_RENDITION = "iframes"
_EXTINF = re.compile(r"^#EXTINF:([0-9.]+)", re.MULTILINE)

Encoder = Literal["libx264", "h264_nvenc"]
Decoder = Literal["cpu", "cuda"]
ProducedBy = Literal["local", "modal"]

# What NVDEC on an L4 decodes, narrowed to what a master actually arrives as.
# The card also has VP9 and VC-1 decoders and 10-bit HEVC, and none of them is
# listed: a source that would in fact decode on the GPU but is missing here
# costs the encode a few minutes, while one listed here that the card refuses
# costs a failed ffmpeg run and a second, slower pass. The list grows when a
# real source measures it, not when a specification says it should work.
CUDA_DECODE_CODECS = frozenset({"h264", "hevc", "av1"})
# 8-bit 4:2:0 only. `nv12` is what NVDEC hands the filter graph; `yuv420p` is
# what the container declares for the same thing.
CUDA_DECODE_PIXEL_FORMATS = frozenset({"yuv420p", "nv12"})

# NVENC's -cq is not libx264's -crf: different rate-control scales, so the same
# number is a different picture. These are a first cut at the ladder's own CRF
# values; the VMAF comparison against the libx264 rungs on a two-minute staging
# excerpt (docs/plans/s2-360-view.md §8) is what calibrates them.
NVENC_CQ = {"top": 20, "720p": 22, "360p": 23}


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


def cuda_decodable(video: VideoFacts | None) -> bool:
    """True when the GPU may decode this source, decided from the probe alone.

    Nothing about the container is trusted beyond the codec name and the pixel
    format, and both have to be in the lists above. An audio-only job, an
    unknown pixel format, and anything the probe could not name all come out
    False, which is the CPU path: the wrong answer here is a failed ffmpeg run
    on a billing GPU, and the fallback that catches it pays for the decode
    twice.
    """
    if video is None:
        return False
    return video.codec in CUDA_DECODE_CODECS and video.pix_fmt in CUDA_DECODE_PIXEL_FORMATS


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


def nvenc_cq(rung: Rung) -> int:
    """The rung's NVENC quality target; its CRF is the fallback for a new rung."""
    return NVENC_CQ.get(rung.name, rung.crf)


def _x264_rung_args(index: int, rung: Rung) -> list[str]:
    """Capped CRF for one rung on the CPU encoder."""
    return [
        f"-preset:v:{index}",
        rung.preset,
        f"-crf:v:{index}",
        str(rung.crf),
        f"-maxrate:v:{index}",
        f"{rung.maxrate_k}k",
        f"-bufsize:v:{index}",
        f"{rung.maxrate_k * 2}k",
    ]


def _nvenc_rung_args(index: int, rung: Rung) -> list[str]:
    """Capped VBR for one rung on the GPU encoder, at the same peak bitrate.

    `-forced-idr` and `-no-scenecut` are what make `-force_key_frames` produce
    real IDR frames at the same instants on every rung; without them NVENC
    answers a forced key frame with a plain I frame and inserts scene cuts of
    its own, and a player switching rungs mid-stream lands mid-GOP.
    `-sc_threshold` is libx264's spelling of the same thing and is not an
    option here.
    """
    return [
        f"-c:v:{index}",
        "h264_nvenc",
        f"-preset:v:{index}",
        "p5",
        f"-tune:v:{index}",
        "hq",
        f"-rc:v:{index}",
        "vbr",
        f"-cq:v:{index}",
        str(nvenc_cq(rung)),
        f"-b:v:{index}",
        "0",
        f"-maxrate:v:{index}",
        f"{rung.maxrate_k}k",
        f"-bufsize:v:{index}",
        f"{rung.maxrate_k * 2}k",
        f"-spatial-aq:v:{index}",
        "1",
        f"-temporal-aq:v:{index}",
        "1",
        f"-rc-lookahead:v:{index}",
        "20",
        f"-bf:v:{index}",
        "3",
        f"-forced-idr:v:{index}",
        "1",
        f"-no-scenecut:v:{index}",
        "1",
        f"-profile:v:{index}",
        "high",
    ]


def _audio_only_args(master: Path, out_dir: Path) -> list[str]:
    """An audio-only source: one audio rendition is the whole ladder."""
    return [
        "-i",
        str(master),
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


def _filter_complex(
    video: VideoFacts, rungs: list[Rung], *, iframes: bool, decoder: Decoder
) -> tuple[str, list[str]]:
    """Split the decoded video once and scale it per rung; returns the graph and its labels.

    The two graphs are the same shape and differ only in where the frames
    live. `split` declares no pixel formats at all (ffmpeg 8.1 `split.c` has no
    query_formats and is flagged metadata-only), so it forwards CUDA frames by
    reference exactly as it forwards system ones and no `hwupload` or
    `split_cuda` is needed; `fps` is the same kind of filter. On the CUDA graph
    every branch then scales with `scale_cuda`, which takes the same `-2:H`
    expressions as `scale` (documented, and its own first example), and the
    rungs reach `h264_nvenc` without a frame ever crossing the bus.

    `passthrough=0` is on every `scale_cuda` on purpose. Its default, 1, hands
    the untouched decoder frame straight through when nothing needs doing,
    which is what the top rung at native size asks for; NVENC then holds that
    frame for the length of its lookahead, and with four branches doing it the
    decoder's frame pool empties and the run dies. ffmpeg's own documentation
    names this mode as the answer to "a filter and encode chain that otherwise
    exhausts the decoder's frame pool". The cost is one device-to-device copy
    per frame on the rung that is not being resized.

    The I-frame branch is the one place frames come back: it thins to one frame
    per GOP and scales on the GPU first, so `hwdownload` moves 0.5 frames a
    second at 360p, and `format=nv12` names the system format ffmpeg's
    documentation says may have to follow it.
    """
    splits = len(rungs) + (1 if iframes else 0)
    labels = [f"[v{i}]" for i in range(splits)]
    chains = [f"[0:v]split={splits}" + "".join(f"[s{i}]" for i in range(splits))]
    fps_filter = f"fps={video.fps}," if video.variable_frame_rate else ""
    scale = "scale_cuda" if decoder == "cuda" else "scale"
    options = ":passthrough=0" if decoder == "cuda" else ""
    download = ",hwdownload,format=nv12" if decoder == "cuda" else ""
    chains += [
        f"[s{i}]{fps_filter}{scale}=-2:{rung.height}{options}[v{i}]" for i, rung in enumerate(rungs)
    ]
    if iframes:
        i = len(rungs)
        chains.append(
            f"[s{i}]{fps_filter}fps=1/{KEYFRAME_SECONDS},"
            f"{scale}=-2:{IFRAME_HEIGHT}{options}{download}[v{i}]"
        )
    return ";".join(chains), labels


def _iframe_output_args(out_dir: Path, label: str) -> list[str]:
    """The intra-only rendition: one frame per GOP, single file, always libx264.

    NVENC has no advantage on half a frame per second, and a rendition where
    every frame is an IDR is exactly what the GPU's lookahead and B frames are
    there to avoid.
    """
    iframe_dir = out_dir / "iframes"
    return [
        "-map",
        label,
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


def _input_args(master: Path, decoder: Decoder) -> list[str]:
    """The input side: where the frames are decoded and where they come out.

    Both halves of the CUDA form matter and both are input options, so they go
    before `-i`. `-hwaccel cuda` on its own decodes on the card and then copies
    every frame back to system memory, which is the bus traffic the GPU graph
    exists to avoid; `-hwaccel_output_format cuda` is what leaves them there.
    """
    if decoder == "cpu":
        return ["-i", str(master)]
    return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(master)]


def _shared_video_args(encoder: Encoder, decoder: Decoder, gop: int) -> list[str]:
    """What every rung shares: the codec, the pixel format, and the GOP.

    The forced keyframes and the GOP are the same string on all four graphs;
    they are what makes a player's mid-stream rung switch land on an IDR.

    `-pix_fmt yuv420p` is only for frames in system memory. The rungs on the
    CUDA graph arrive as CUDA frames and NVENC negotiates the `cuda` pixel
    format for them; asking for `yuv420p` here would ask the filter chain for a
    conversion only an explicit `hwdownload` can make, and fail the run. The
    I-frame rendition keeps its own `-pix_fmt yuv420p` on its own output, where
    the frames have already come back.
    """
    args: list[str] = []
    if encoder == "libx264":
        args += ["-c:v", "libx264", "-profile:v", "high", "-sc_threshold", "0"]
    if decoder == "cpu":
        args += ["-pix_fmt", "yuv420p"]
    return [
        *args,
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-force_key_frames",
        f"expr:gte(t,n_forced*{KEYFRAME_SECONDS})",
    ]


def ladder_args(  # noqa: PLR0913
    master: Path,
    out_dir: Path,
    video: VideoFacts | None,
    *,
    has_audio: bool,
    rungs: list[Rung],
    iframes: bool,
    encoder: Encoder = "libx264",
    decoder: Decoder = "cpu",
) -> list[str]:
    """Build the single ffmpeg invocation for the ladder, audio, and I-frames."""
    if decoder == "cuda" and encoder != "h264_nvenc":
        msg = (
            "decoder='cuda' hands the rung encoder CUDA frames and only "
            f"'h264_nvenc' takes them; encoder={encoder!r} was asked for"
        )
        raise ValueError(msg)
    if video is None:
        return _audio_only_args(master, out_dir)
    args = _input_args(master, decoder)

    graph, labels = _filter_complex(video, rungs, iframes=iframes, decoder=decoder)
    args += ["-filter_complex", graph]
    for label in labels[: len(rungs)]:
        args += ["-map", label]
    if has_audio:
        args += ["-map", "0:a:0"]
    args += _shared_video_args(encoder, decoder, gop_frames(video.fps))
    for i, rung in enumerate(rungs):
        args += _x264_rung_args(i, rung) if encoder == "libx264" else _nvenc_rung_args(i, rung)
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
        args += _iframe_output_args(out_dir, labels[-1])
    return args


async def transcode_ladder(  # noqa: PLR0913
    ffmpeg: str,
    master: Path,
    out_dir: Path,
    video: VideoFacts | None,
    *,
    has_audio: bool,
    expected_seconds: float | None = None,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
    encoder: Encoder = "libx264",
    decoder: Decoder = "cpu",
) -> list[Rung]:
    """Run the ladder and return its rungs; a complete ladder from an earlier attempt is reused."""
    rungs = plan_rungs(video) if video else []
    if expected_seconds is not None and ladder_is_complete(
        out_dir,
        rungs,
        has_audio=has_audio,
        iframes=video is not None,
        expected_seconds=expected_seconds,
    ):
        return rungs
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for rung in rungs:
        (out_dir / rung.name).mkdir(exist_ok=True)
    if has_audio:
        (out_dir / "audio").mkdir(exist_ok=True)
    if video:
        (out_dir / "iframes").mkdir(exist_ok=True)
    args = ladder_args(
        master,
        out_dir,
        video,
        has_audio=has_audio,
        rungs=rungs,
        iframes=video is not None,
        encoder=encoder,
        decoder=decoder,
    )
    await run_ffmpeg(ffmpeg, args, on_progress=on_progress)
    if video:
        mark_iframe_playlist(out_dir / "iframes" / "index.m3u8")
        append_iframe_variant(out_dir / "master.m3u8", out_dir / "iframes", duration_hint=None)
    return rungs


def ladder_is_complete(
    out_dir: Path, rungs: list[Rung], *, has_audio: bool, iframes: bool, expected_seconds: float
) -> bool:
    """True when every playlist an earlier attempt should have written exists and covers the source.

    A retried transcode activity (a worker restart, a timeout in the publish
    step that follows) must not throw away fifty minutes of encoding.
    """
    if not (out_dir / "master.m3u8").exists():
        return False
    playlists = [out_dir / rung.name / "index.m3u8" for rung in rungs]
    if has_audio:
        playlists.append(out_dir / "audio" / "index.m3u8")
    if iframes:
        playlists.append(out_dir / "iframes" / "index.m3u8")
    try:
        for playlist in playlists:
            floor = KEYFRAME_SECONDS if playlist.parent.name == "iframes" else 0.0
            assert_covers(playlist, expected_seconds, floor_seconds=floor)
    except (OSError, TruncatedOutputError):
        return False
    try:
        local_inventory(out_dir, {playlist.parent.name for playlist in playlists})
    except (OSError, ValueError):
        return False
    return True


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


class LadderManifest(BaseModel):
    """What a finished ladder is, written to `manifest.json` after everything else.

    The manifest is the completion marker. A ladder produced on Modal is
    published by the function itself, so the worker cannot look at a work
    volume to decide whether an earlier attempt finished; it reads this object
    instead, and because it is uploaded last, its presence means every segment
    and playlist under the prefix is already there. `renditions` carries the
    length of every playlist, so the check is the same one `assert_covers`
    makes locally.

    Field names are the wire names the spec fixed (camelCase, as the generated
    contracts use); the Python attributes stay snake_case like the rest of the
    package.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    version: Literal[1] = MANIFEST_VERSION
    renditions: dict[str, float]
    iframes: bool
    segment_seconds: int
    total_bytes: int
    encoder: Encoder
    produced_by: ProducedBy
    # Which decoder actually produced this ladder, not which one was asked for:
    # a CUDA attempt that failed and fell back to the CPU records "cpu". It has
    # a default because every manifest written before the GPU decode existed is
    # a CPU one, and `stored_ladder` has to keep reading those back rather than
    # raising a validation error inside a reuse check.
    decoder: Decoder = "cpu"
    call_id: str | None = None
    # Optional only to parse older markers; reuse requires both fields.
    artifacts: dict[str, int] | None = None
    playlist_sha256: dict[str, str] | None = None


def write_manifest(out_dir: Path, manifest: LadderManifest) -> Path:
    """Write the manifest into the ladder directory; returns the file."""
    path = out_dir / MANIFEST_NAME
    path.write_text(manifest.model_dump_json(by_alias=True) + "\n")
    return path


def read_manifest(text: str) -> LadderManifest:
    """Parse a manifest read back from storage or disk."""
    return LadderManifest.model_validate(json.loads(text))


def manifest_covers(manifest: LadderManifest, expected_seconds: float) -> bool:
    """True when every playlist the manifest names covers the source within tolerance."""
    tolerance = duration_tolerance(expected_seconds)
    for name, seconds in manifest.renditions.items():
        floor = KEYFRAME_SECONDS if name == IFRAMES_RENDITION else 0.0
        if (
            not math.isfinite(seconds)
            or seconds <= 0
            or seconds < expected_seconds - tolerance - floor
        ):
            return False
    return bool(manifest.renditions)


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
