"""Unit tests for the media planning code that needs no ffmpeg."""

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from temnia_pipeline.media import derive, hls, peaks
from temnia_pipeline.media.probe import VideoFacts


def facts(height: int, fps: float = 30, *, vfr: bool = False) -> VideoFacts:
    return VideoFacts(
        width=height * 16 // 9,
        height=height,
        fps=Fraction(str(fps)),
        variable_frame_rate=vfr,
        codec="h264",
    )


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
