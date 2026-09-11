"""Exercise optional detector plumbing without claiming a real detector audition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

from temnia_pipeline.harness import shot_evidence
from temnia_pipeline.media import pyscene_shots
from temnia_pipeline.media.chapters import inspect_timeline
from temnia_pipeline.media.ffmpeg import run_ffmpeg
from test_harness_shot_evidence import ARTIFACT, MASTER, SCOPE, SOURCE, TIMELINE

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from numpy.typing import NDArray

    from temnia_pipeline.media.chapters import MediaTimelineFacts


@dataclass
class _Time:
    pts: int
    time_base: Fraction


class _FrameTime:
    def __init__(self, timecode: _Time | int, fps: Fraction) -> None:
        self.pts = timecode.pts if isinstance(timecode, _Time) else timecode
        self.time_base = timecode.time_base if isinstance(timecode, _Time) else 1 / fps


class _Stats:
    def __init__(self, base_timecode: _FrameTime) -> None:
        self.base_timecode = base_timecode

    def register_metrics(self, _keys: list[str]) -> None:
        pass

    def get_metrics(self, _time: _FrameTime, _keys: list[str]) -> list[float]:
        return [51.0, 75.0]


class _Detector:
    def __init__(self, **kwargs: object) -> None:
        assert kwargs["min_scene_len"] == 0.0
        assert kwargs["adaptive_threshold"] == 3.0
        self.calls = 0
        self.stats_manager: _Stats | None = None

    def get_metrics(self) -> list[str]:
        return [pyscene_shots.RATIO_METRIC, pyscene_shots.CONTENT_METRIC]

    def process_frame(
        self, *, timecode: _FrameTime, frame_img: NDArray[np.uint8]
    ) -> list[_FrameTime]:
        assert frame_img.shape == (2, 2, 3)
        self.calls += 1
        return [timecode] if self.calls == 2 else []

    def post_process(self, _timecode: _FrameTime) -> list[_FrameTime]:
        return []


def _available() -> dict[str, Any]:
    return {"status": "available", "reason": None, "installedVersion": "0.7.1"}


def _fake_import(_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        AdaptiveDetector=_Detector, StatsManager=_Stats, Timecode=_Time, FrameTimecode=_FrameTime
    )


def _frames(
    _path: Path, timeline: MediaTimelineFacts, _stop: Event
) -> Iterator[tuple[Fraction, NDArray[np.uint8]]]:
    # VFR timestamps intentionally do not equal frame_number / the nominal 25 fps.
    for offset in (Fraction(0), Fraction(81, 1000), Fraction(13, 100)):
        yield timeline.source_start + offset, np.zeros((2, 2, 3), dtype=np.uint8)


async def test_frame_api_preserves_signed_native_pts_and_vfr_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pyscene_shots, "pyscenedetect_availability", _available)
    monkeypatch.setattr(pyscene_shots, "import_module", _fake_import)
    monkeypatch.setattr(pyscene_shots, "decoded_source_frames", _frames)
    rows = await pyscene_shots.detect_adaptive_source_shots(tmp_path / "master", timeline=TIMELINE)
    assert rows == (pyscene_shots.NativeAdaptiveShot(Fraction(-419, 1000), 51.0, 75.0),)


async def test_missing_dependency_cannot_be_an_empty_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unavailable() -> dict[str, Any]:
        return {"status": "unavailable", "reason": "dependency_not_installed"}

    monkeypatch.setattr(pyscene_shots, "pyscenedetect_availability", unavailable)
    with pytest.raises(RuntimeError, match="dependency_not_installed"):
        await pyscene_shots.detect_adaptive_source_shots(tmp_path / "master", timeline=TIMELINE)


async def test_cancellation_joins_the_decoder_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started, stopped = Event(), Event()

    def run(_path: Path, _timeline: MediaTimelineFacts, stop: Event) -> tuple[()]:
        started.set()
        stop.wait(timeout=2)
        stopped.set()
        return ()

    monkeypatch.setattr(pyscene_shots, "pyscenedetect_availability", _available)
    monkeypatch.setattr(pyscene_shots, "_run_detection", run)
    task = asyncio.create_task(
        pyscene_shots.detect_adaptive_source_shots(tmp_path / "master", timeline=TIMELINE)
    )
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.timeout(30)
async def test_decoder_uses_the_selected_track_and_real_pts(tmp_path: Path) -> None:
    source = tmp_path / "tracks.mkv"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=white:s=64x64:r=25:d=1",
            "-map",
            "0:v",
            "-map",
            "1:v",
            "-c:v",
            "ffv1",
            str(source),
        ],
    )
    timeline = replace(await inspect_timeline(source), video_stream_index=1)
    frames = list(pyscene_shots.decoded_source_frames(source, timeline, Event()))
    assert len(frames) == 25
    assert [row[0] for row in frames] == [Fraction(frame, 25) for frame in range(25)]
    assert frames[0][1].min() >= 250


@pytest.mark.skipif(
    pyscene_shots.pyscenedetect_availability()["status"] != "available",
    reason="qualified optional scenedetect-headless 0.7.1 is not installed",
)
@pytest.mark.timeout(30)
async def test_installed_adaptive_detector_detects_a_real_cut_with_source_offset(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cut.mkv"
    await run_ffmpeg(
        "ffmpeg",
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=white:s=64x64:r=25:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS+2/TB[v]",
            "-map",
            "[v]",
            "-c:v",
            "ffv1",
            str(source),
        ],
    )
    timeline = await inspect_timeline(source)
    shots = await pyscene_shots.detect_adaptive_source_shots(source, timeline=timeline)
    assert len(shots) == 1
    assert shots[0].time == 3
    assert shots[0].time - timeline.source_start == 1
    assert shots[0].adaptive_ratio >= pyscene_shots.ADAPTIVE_THRESHOLD
    assert shots[0].content_value >= pyscene_shots.MIN_CONTENT


@pytest.mark.parametrize("available", [True, False])
async def test_challenger_has_distinct_bound_cache_and_native_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, available: bool
) -> None:
    saved: dict[str, Any] = {}
    identities: list[str] = []
    record = SimpleNamespace(id=ARTIFACT, sha256="c" * 64)

    async def find(*_args: object, **kwargs: object) -> None:
        identities.append(cast("Any", kwargs["identity"]).fingerprint)

    async def publish(*_args: object, **kwargs: object) -> SimpleNamespace:
        saved.update(cast("dict[str, Any]", kwargs["content"]))
        return record

    def availability() -> dict[str, Any]:
        return (
            _available()
            if available
            else {"status": "unavailable", "reason": "dependency_not_installed"}
        )

    async def detect(
        *_args: object, **_kwargs: object
    ) -> tuple[pyscene_shots.NativeAdaptiveShot, ...]:
        assert available
        return (pyscene_shots.NativeAdaptiveShot(Fraction(-419, 1000), 51.0, 75.0),)

    monkeypatch.setattr(pyscene_shots, "pyscenedetect_availability", availability)
    monkeypatch.setattr(shot_evidence.artifacts, "find_artifact", find)
    monkeypatch.setattr(shot_evidence.artifacts, "publish_json", publish)
    monkeypatch.setattr(shot_evidence, "detect_adaptive_source_shots", detect)
    args: dict[str, Any] = {
        "scope": SCOPE,
        "source_id": SOURCE,
        "store": object(),
        "source_path": tmp_path / "master",
        "source_object": MASTER,
        "timeline": TIMELINE,
        "ffmpeg": "unused",
        "detector": "pyscenedetect-adaptive",
    }
    result = await shot_evidence.build_source_shot_evidence("unused", **args)
    assert result.status == ("measured" if available else "unavailable")
    assert result.shot_times_ms == ((81,) if available else ())
    if available:
        assert result.shots[0].score == 0.2
        assert saved["observations"][0]["metrics"] == {
            pyscene_shots.RATIO_METRIC: 51.0,
            pyscene_shots.CONTENT_METRIC: 75.0,
        }
    baseline_binding = shot_evidence.source_shot_binding(
        scope=SCOPE,
        source_id=SOURCE,
        source_object=MASTER,
        timeline=TIMELINE,
        ffmpeg_sha256="b" * 64,
    )
    assert identities[0] != shot_evidence.artifacts.fingerprint_for(
        kind=shot_evidence.FORMAT, inputs=baseline_binding, config={"format": shot_evidence.FORMAT}
    )
