"""Unit tests for the media planning code that needs no ffmpeg binary.

The probe runs here too: it is PyAV, not a subprocess, and the facts it fills
in are what the ladder plans and picks a decoder from.
"""

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from temnia_pipeline.media import derive, hls, peaks
from temnia_pipeline.media.probe import VideoFacts, probe

# The only real media in the repository lives with the web app's Playwright
# fixtures. Probing one of them here beats a second copy of the same bytes.
FIXTURES = Path(__file__).resolve().parents[2] / "web" / "e2e" / "fixtures"


def facts(
    height: int,
    fps: float = 30,
    *,
    vfr: bool = False,
    codec: str = "h264",
    pix_fmt: str | None = "yuv420p",
) -> VideoFacts:
    return VideoFacts(
        width=height * 16 // 9,
        height=height,
        fps=Fraction(str(fps)),
        variable_frame_rate=vfr,
        codec=codec,
        pix_fmt=pix_fmt,
    )


def test_the_probe_records_the_codec_and_the_pixel_format() -> None:
    """Both strings decide the decoder, and the Modal image has no probe of its own."""
    _result, video = probe(FIXTURES / "master-12s.mp4")
    assert video is not None
    assert video.codec == "h264"
    assert video.pix_fmt == "yuv420p"


def test_ladder_caps_at_1080_and_adds_proxies() -> None:
    rungs = hls.plan_rungs(facts(2160))
    assert [r.height for r in rungs] == [1080, 720, 360]
    assert rungs[0].maxrate_k == 6000


def test_ladder_for_a_720p_source_has_no_720_proxy() -> None:
    rungs = hls.plan_rungs(facts(720))
    assert [r.height for r in rungs] == [720, 360]


def test_ladder_for_a_tiny_source_is_one_rung() -> None:
    rungs = hls.plan_rungs(facts(240))
    assert [r.height for r in rungs] == [240]


def test_high_fps_raises_the_top_maxrate() -> None:
    assert hls.top_maxrate_k(1080, Fraction(60)) == 9000


def test_ladder_args_are_one_pass_with_iframes_and_audio_group(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(1080))
    args = hls.ladder_args(
        tmp_path / "master.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
    )
    joined = " ".join(args)
    assert joined.count("-f hls") == 2
    assert "split=4" in joined
    assert "-hls_segment_type fmp4" in joined
    expected_map = (
        "v:0,agroup:aud,name:top v:1,agroup:aud,name:720p v:2,agroup:aud,name:360p"
        " a:0,agroup:aud,name:audio,default:yes"
    )
    assert expected_map in joined
    assert "single_file+independent_segments" in joined
    assert "-g 60 -keyint_min 60" in joined


def test_vfr_sources_are_forced_to_constant_rate(tmp_path: Path) -> None:
    args = hls.ladder_args(
        tmp_path / "m.mov",
        tmp_path / "hls",
        facts(720, 29.97, vfr=True),
        has_audio=False,
        rungs=hls.plan_rungs(facts(720, 29.97, vfr=True)),
        iframes=True,
    )
    assert "fps=2997/100," in " ".join(args)


def test_playlist_coverage_assertion(tmp_path: Path) -> None:
    playlist = tmp_path / "index.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:2.000,\nseg1\n#EXTINF:2.000,\nseg2\n#EXT-X-ENDLIST\n")
    assert hls.assert_covers(playlist, 4.0) == pytest.approx(4.0)
    with pytest.raises(hls.TruncatedOutputError):
        hls.assert_covers(playlist, 100.0)
    playlist.write_text("#EXTM3U\n#EXTINF:2.000,\nseg1\n")
    with pytest.raises(hls.TruncatedOutputError):
        hls.assert_covers(playlist, 2.0)


def test_tolerance_is_one_percent_or_twelve_seconds() -> None:
    assert hls.duration_tolerance(60) == 12.0
    assert hls.duration_tolerance(7200) == 72.0


def test_iframe_playlist_marking(tmp_path: Path) -> None:
    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXTINF:2.0,\nx\n#EXT-X-ENDLIST\n"
    )
    hls.mark_iframe_playlist(playlist)
    lines = playlist.read_text().splitlines()
    assert lines[3] == "#EXT-X-I-FRAMES-ONLY"
    hls.mark_iframe_playlist(playlist)
    assert playlist.read_text().count("#EXT-X-I-FRAMES-ONLY") == 1


def test_peaks_scale_with_duration_and_pad_to_media_length() -> None:
    assert peaks.samples_per_pixel(10) == 20
    assert peaks.samples_per_pixel(7200) == 14063
    spp = peaks.samples_per_pixel(2)
    pcm = np.array([0, 256, -512, 1024] * 100, dtype="<i2")
    data = peaks.bucket(pcm, spp, 2.0)
    assert len(data) == 2 * (2 * peaks.SAMPLE_RATE // spp)
    assert data.dtype == np.int8
    assert data[-1] == 0


def test_thumbnail_interval_caps_the_strip() -> None:
    assert derive.strip_interval(30) == 10
    assert derive.strip_interval(7200) == 60
    assert derive.poster_offset(7200) == 30.0
    assert derive.poster_offset(40) == 10.0


def test_scdet_parsing_pairs_time_with_score() -> None:
    text = (
        "frame:12 pts:24024 pts_time:0.4004\n"
        "lavfi.scd.mafd=12.3\n"
        "lavfi.scd.score=15.5\n"
        "lavfi.scd.time=0.4\n"
        "frame:40 pts:80080 pts_time:1.335\n"
        "lavfi.scd.score=2.0\n"
    )
    assert derive.parse_scdet(text) == [{"t": 0.4, "score": 15.5}]


def _write_playlist(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = "".join(f"#EXTINF:2.000,\nseg_{i:05d}.m4s\n" for i in range(int(seconds / 2)))
    path.write_text(
        f'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXT-X-INDEPENDENT-SEGMENTS\n{entries}#EXT-X-ENDLIST\n'
    )
    (path.parent / "init.mp4").write_bytes(b"init")
    for i in range(int(seconds / 2)):
        (path.parent / f"seg_{i:05d}.m4s").write_bytes(b"media")


def test_a_complete_ladder_from_an_earlier_attempt_is_recognised(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(720))
    out = tmp_path / "hls"
    (out).mkdir()
    (out / "master.m3u8").write_text(
        "#EXTM3U\n"
        + "".join(
            f"#EXT-X-STREAM-INF:BANDWIDTH=1000\n{name}/index.m3u8\n"
            for name in [*(rung.name for rung in rungs), "audio", "iframes"]
        )
    )
    for rung in rungs:
        _write_playlist(out / rung.name / "index.m3u8", 120)
    _write_playlist(out / "audio" / "index.m3u8", 120)
    _write_playlist(out / "iframes" / "index.m3u8", 118)
    assert hls.ladder_is_complete(out, rungs, has_audio=True, iframes=True, expected_seconds=120)
    # A missing segment cannot be reused even with intact playlists.
    segment = out / "top" / "seg_00000.m4s"
    segment.unlink()
    assert not hls.ladder_is_complete(
        out, rungs, has_audio=True, iframes=True, expected_seconds=120
    )
    segment.write_bytes(b"media")
    # A truncated rung, or a missing one, means encode again.
    _write_playlist(out / "360p" / "index.m3u8", 60)
    assert not hls.ladder_is_complete(
        out, rungs, has_audio=True, iframes=True, expected_seconds=120
    )
    (out / "master.m3u8").unlink()
    assert not hls.ladder_is_complete(
        out, rungs, has_audio=True, iframes=True, expected_seconds=120
    )


def test_nvenc_rungs_switch_encoder_and_keep_the_shared_gop(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(1080))
    args = hls.ladder_args(
        tmp_path / "master.mov",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
        encoder="h264_nvenc",
    )
    joined = " ".join(args)
    for i, rung in enumerate(rungs):
        assert f"-c:v:{i} h264_nvenc" in joined
        assert f"-cq:v:{i} {hls.NVENC_CQ[rung.name]}" in joined
        assert f"-b:v:{i} 0" in joined
        assert f"-maxrate:v:{i} {rung.maxrate_k}k" in joined
        assert f"-forced-idr:v:{i} 1" in joined
        assert f"-no-scenecut:v:{i} 1" in joined
        assert f"-profile:v:{i} high" in joined
    # The forced keyframes and the GOP are what make the rungs interchangeable;
    # they are the same string on both encoders.
    assert "-g 60 -keyint_min 60" in joined
    assert "-force_key_frames expr:gte(t,n_forced*2)" in joined
    # libx264-only options must not reach NVENC, and the decode side is unchanged.
    assert "-sc_threshold" not in joined
    assert "-crf:v:" not in joined
    assert "-c:v libx264 -preset veryfast" in joined  # the I-frame rendition
    assert "split=4" in joined
    assert "scale=-2:1080" in joined


# 8-bit 4:2:0 in one of the three codecs NVDEC reads on an L4.
CUDA_DECODABLE = [("h264", "yuv420p"), ("h264", "nv12"), ("hevc", "yuv420p"), ("av1", "yuv420p")]

CUDA_UNDECODABLE = [
    # 10-bit and 4:2:2 are the two shapes a graded or camera master arrives in,
    # and neither is 8-bit 4:2:0.
    ("hevc", "yuv420p10le"),
    ("h264", "yuv422p"),
    ("h264", "yuv444p"),
    # Codecs the ladder is not letting the card near, whatever it claims.
    ("prores", "yuv422p10le"),
    ("dnxhd", "yuv422p"),
    ("vp9", "yuv420p"),
    ("mpeg2video", "yuv420p"),
    # A container that named no pixel format is not a guess worth making.
    ("h264", None),
]


@pytest.mark.parametrize(("codec", "pix_fmt"), CUDA_DECODABLE)
def test_eight_bit_420_h264_hevc_and_av1_decode_on_the_gpu(codec: str, pix_fmt: str) -> None:
    assert hls.cuda_decodable(facts(1080, codec=codec, pix_fmt=pix_fmt))


@pytest.mark.parametrize(("codec", "pix_fmt"), CUDA_UNDECODABLE)
def test_everything_else_takes_the_cpu_path(codec: str, pix_fmt: str | None) -> None:
    assert not hls.cuda_decodable(facts(1080, codec=codec, pix_fmt=pix_fmt))


def test_an_audio_only_job_never_asks_for_the_gpu_decoder() -> None:
    assert hls.cuda_decodable(None) is False


def test_the_cuda_graph_keeps_every_rung_on_the_gpu(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(1080))
    args = hls.ladder_args(
        tmp_path / "master.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
        encoder="h264_nvenc",
        decoder="cuda",
    )
    joined = " ".join(args)
    graph = args[args.index("-filter_complex") + 1]

    # The hwaccel options are input options: they only mean anything before -i.
    assert args[:5] == ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i"]
    # One split, on CUDA frames, then a GPU scaler on every branch.
    assert graph.startswith("[0:v]split=4[s0][s1][s2][s3];")
    for rung in rungs:
        assert f"scale_cuda=-2:{rung.height}:passthrough=0" in graph
    # Not one software scale is left on a branch that stays on the card.
    assert "scale=-2:" not in graph
    # The I-frame rendition is libx264, so that branch and only that branch
    # comes back to system memory, after it has thinned to one frame per GOP.
    assert graph.endswith("[s3]fps=1/2,scale_cuda=-2:360:passthrough=0,hwdownload,format=nv12[v3]")
    assert "-c:v libx264 -preset veryfast" in joined
    # `-pix_fmt yuv420p` on the rungs would ask the CUDA branches for a
    # conversion nothing but an explicit hwdownload can make. The one left is
    # the I-frame output's own, downstream of that hwdownload.
    assert joined.count("-pix_fmt yuv420p") == 1
    # Everything the ladder is made of is untouched by the move.
    for i, rung in enumerate(rungs):
        assert f"-c:v:{i} h264_nvenc" in joined
        assert f"-cq:v:{i} {hls.NVENC_CQ[rung.name]}" in joined
    assert "-g 60 -keyint_min 60" in joined
    assert "-force_key_frames expr:gte(t,n_forced*2)" in joined
    assert "-c:a aac -b:a 128k" in joined
    assert (
        "v:0,agroup:aud,name:top v:1,agroup:aud,name:720p v:2,agroup:aud,name:360p"
        " a:0,agroup:aud,name:audio,default:yes"
    ) in joined
    assert joined.count("-f hls") == 2


def test_a_vfr_source_still_gets_its_constant_rate_on_the_gpu(tmp_path: Path) -> None:
    video = facts(720, 29.97, vfr=True)
    args = hls.ladder_args(
        tmp_path / "m.mp4",
        tmp_path / "hls",
        video,
        has_audio=False,
        rungs=hls.plan_rungs(video),
        iframes=True,
        encoder="h264_nvenc",
        decoder="cuda",
    )
    graph = args[args.index("-filter_complex") + 1]
    assert "[s0]fps=2997/100,scale_cuda=-2:720:passthrough=0[v0]" in graph


def test_the_cuda_decoder_refuses_an_encoder_that_cannot_take_its_frames(tmp_path: Path) -> None:
    """libx264 downstream of CUDA frames is a graph ffmpeg cannot build."""
    with pytest.raises(ValueError, match="h264_nvenc"):
        hls.ladder_args(
            tmp_path / "m.mp4",
            tmp_path / "hls",
            facts(1080),
            has_audio=False,
            rungs=hls.plan_rungs(facts(1080)),
            iframes=True,
            decoder="cuda",
        )


def test_the_cpu_graph_is_unchanged_by_the_decoder_argument(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(1080))
    default = hls.ladder_args(
        tmp_path / "m.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
        encoder="h264_nvenc",
    )
    explicit = hls.ladder_args(
        tmp_path / "m.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
        encoder="h264_nvenc",
        decoder="cpu",
    )
    assert default == explicit
    assert "-hwaccel" not in default
    assert "scale=-2:1080" in " ".join(default)


def test_nvenc_cq_falls_back_to_the_rung_crf() -> None:
    assert hls.nvenc_cq(hls.Rung("top", 1080, 20, 6000, "fast")) == 20
    assert hls.nvenc_cq(hls.Rung("540p", 540, 21, 1800, "veryfast")) == 21


def test_libx264_rungs_are_unchanged_by_the_encoder_argument(tmp_path: Path) -> None:
    rungs = hls.plan_rungs(facts(1080))
    default = hls.ladder_args(
        tmp_path / "m.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
    )
    explicit = hls.ladder_args(
        tmp_path / "m.mp4",
        tmp_path / "hls",
        facts(1080),
        has_audio=True,
        rungs=rungs,
        iframes=True,
        encoder="libx264",
    )
    assert default == explicit
    assert "-sc_threshold 0" in " ".join(default)


FULL_LADDER = {"top": 120.0, "360p": 120.0, "audio": 120.04, "iframes": 118.0}


def _manifest(renditions: dict[str, float] | None = None) -> hls.LadderManifest:
    return hls.LadderManifest(
        renditions=FULL_LADDER if renditions is None else renditions,
        iframes=True,
        segment_seconds=hls.SEGMENT_SECONDS,
        total_bytes=4096,
        encoder="h264_nvenc",
        produced_by="modal",
        decoder="cuda",
        call_id="fc-123",
    )


def test_manifest_round_trips_through_camel_case_json(tmp_path: Path) -> None:
    written = hls.write_manifest(tmp_path, _manifest())
    assert written.name == hls.MANIFEST_NAME
    text = written.read_text()
    assert '"segmentSeconds"' in text
    assert '"producedBy":"modal"' in text
    assert '"callId":"fc-123"' in text
    assert '"decoder":"cuda"' in text
    back = hls.read_manifest(text)
    assert back == _manifest()
    assert back.version == 1
    assert back.encoder == "h264_nvenc"
    assert back.decoder == "cuda"


def test_a_manifest_written_before_the_gpu_decode_reads_back_as_cpu() -> None:
    """Every S1 and early S2 ladder decoded on the CPU, and a reuse check must
    still read one rather than raise inside `stored_ladder`.
    """
    text = _manifest().model_dump_json(by_alias=True, exclude={"decoder"})
    assert hls.read_manifest(text).decoder == "cpu"


def test_manifest_covers_uses_the_same_tolerance_as_assert_covers() -> None:
    assert hls.manifest_covers(_manifest(), 120.0)
    # 1% or 12 s, whichever is larger: 108 s of a 120 s source is inside it.
    assert hls.manifest_covers(_manifest(renditions={"top": 108.0}), 120.0)
    assert not hls.manifest_covers(_manifest(renditions={"top": 107.9}), 120.0)
    # The I-frame rendition is one frame short of the last GOP by construction.
    assert hls.manifest_covers(_manifest(renditions={"iframes": 106.0}), 120.0)
    assert not hls.manifest_covers(_manifest(renditions={"iframes": 105.9}), 120.0)
    # An empty ladder covers nothing, whatever the tolerance says.
    assert not hls.manifest_covers(_manifest(renditions={}), 120.0)
