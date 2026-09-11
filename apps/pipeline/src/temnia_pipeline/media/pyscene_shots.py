"""Optional AdaptiveDetector challenger over explicitly selected PyAV source frames.

The 0.7.1 frame-processing API preserves PTS-backed timecodes. We use it directly
because open_video may fall back to OpenCV and does not select the harness track.
See https://www.scenedetect.com/docs/latest/api/detectors.html and api/common.html.
"""

# ruff: noqa: EM101, TRY003

from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from fractions import Fraction
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from threading import Event
from typing import TYPE_CHECKING, Any, Protocol, cast

import av

from temnia_pipeline.media.source_shots import SCALE_HEIGHT

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray

    from temnia_pipeline.media.chapters import MediaTimelineFacts

PYSCENEDETECT_VERSION = "0.7.1"
ADAPTER_VERSION = "source-pyscenedetect-adaptive/1"
ADAPTIVE_THRESHOLD = 3.0
MIN_CONTENT = 15.0
WINDOW_WIDTH = 2
MAX_ADAPTIVE_RATIO = 255.0
RATIO_METRIC = "adaptive_ratio (w=2)"
CONTENT_METRIC = "content_val"


@dataclass(frozen=True, slots=True)
class NativeAdaptiveShot:
    """Original input PTS and both native detector metrics at an emitted cut."""

    time: Fraction
    adaptive_ratio: float
    content_value: float


def pyscenedetect_availability() -> dict[str, Any]:
    """Dependency availability is separate from measured editorial suitability."""
    installed: dict[str, str] = {}
    for package in ("scenedetect", "scenedetect-headless"):
        with suppress(PackageNotFoundError):
            installed[package] = version(package)
    if not installed:
        reason = "dependency_not_installed"
    elif len(installed) != 1:
        reason = "conflicting_distributions"
    elif "scenedetect-headless" not in installed:
        reason = "headless_distribution_required"
    elif installed["scenedetect-headless"] != PYSCENEDETECT_VERSION:
        reason = "unqualified_dependency_version"
    else:
        reason = None
    return {
        "status": "available" if reason is None else "unavailable",
        "detector": "pyscenedetect.AdaptiveDetector",
        "requiredVersion": PYSCENEDETECT_VERSION,
        "installedDistributions": installed,
        "installedVersion": next(iter(installed.values()), None),
        "reason": reason,
        "auditionStatus": "not_auditioned",
    }


def adaptive_detector_identity() -> dict[str, Any]:
    """Record explicit parameters and runtime versions; these are not quality scores."""
    runtime: dict[str, str | None] = {}
    for package in ("av", "numpy", "opencv-python-headless"):
        try:
            runtime[package] = version(package)
        except PackageNotFoundError:
            runtime[package] = None
    return {
        "name": "pyscenedetect.AdaptiveDetector",
        "adapterVersion": ADAPTER_VERSION,
        "availability": pyscenedetect_availability(),
        "runtimeVersions": runtime,
        "avLibraryVersions": {key: list(value) for key, value in av.library_versions.items()},
        "backend": "direct-pyav-selected-stream",
        "height": SCALE_HEIGHT,
        "resize": "pyav-bilinear-bgr24-even-width",
        "adaptiveThreshold": ADAPTIVE_THRESHOLD,
        "minContentVal": MIN_CONTENT,
        "windowWidth": WINDOW_WIDTH,
        "minSceneLenSeconds": 0.0,
        "minSceneLenReason": "retain-neighbouring-observations-for-semantic-cut-review",
        "weights": {"delta_hue": 1.0, "delta_sat": 1.0, "delta_lum": 1.0, "delta_edges": 0.0},
        "lumaOnly": False,
        "kernelSize": None,
        "nativeScore": RATIO_METRIC,
        "auxiliaryNativeScore": CONTENT_METRIC,
        "scoreNormalization": "adaptive-ratio-divided-by-255;not-a-quality-probability",
        "timestamps": "decoded-input-pts;source-relative-Timecode;no-frame-number-conversion",
        "edgeWindow": "first-and-last-windowWidth-frames-lack-full-adaptive-neighbourhood",
    }


def decoded_source_frames(
    source_path: Path, timeline: MediaTimelineFacts, stop: Event
) -> Iterator[tuple[Fraction, NDArray[np.uint8]]]:
    """Decode and scale only the bound video track, retaining its native PTS."""
    with av.open(str(source_path)) as container:
        stream = next(
            (row for row in container.streams.video if row.index == timeline.video_stream_index),
            None,
        )
        if stream is None:
            raise ValueError("selected source video stream is unavailable")
        for frame in container.decode(stream):
            if stop.is_set():
                return
            if frame.pts is None or frame.time_base is None:
                raise ValueError("decoded shot frame has no presentation timestamp")
            native_time = Fraction(frame.pts) * frame.time_base
            width = max(2, round(frame.width * SCALE_HEIGHT / frame.height / 2) * 2)
            scaled = frame.reformat(
                width=width, height=SCALE_HEIGHT, format="bgr24", interpolation="BILINEAR"
            )
            yield native_time, cast("NDArray[np.uint8]", scaled.to_ndarray())


class _TimedCut(Protocol):
    pts: int
    time_base: Fraction


def _validated_cut(
    cut: _TimedCut, metrics: list[Any], timeline: MediaTimelineFacts
) -> NativeAdaptiveShot:
    relative = Fraction(cut.pts) * Fraction(cut.time_base)
    ratio, content = metrics
    if (
        not isinstance(ratio, (float, int))
        or isinstance(ratio, bool)
        or not math.isfinite(ratio)
        or not ADAPTIVE_THRESHOLD <= ratio <= MAX_ADAPTIVE_RATIO
        or not isinstance(content, (float, int))
        or isinstance(content, bool)
        or not math.isfinite(content)
        or content < MIN_CONTENT
        or not 0 <= relative <= timeline.duration
    ):
        raise ValueError("adaptive detector cut lacks valid native metrics or source timing")
    return NativeAdaptiveShot(relative + timeline.source_start, float(ratio), float(content))


def _run_detection(
    source_path: Path, timeline: MediaTimelineFacts, stop: Event
) -> tuple[NativeAdaptiveShot, ...]:
    # Optional modules are loaded only after the dependency check in the public wrapper.
    api: Any = import_module("scenedetect")
    common: Any = import_module("scenedetect.common")
    detector = api.AdaptiveDetector(
        adaptive_threshold=ADAPTIVE_THRESHOLD,
        min_scene_len=0.0,
        window_width=WINDOW_WIDTH,
        min_content_val=MIN_CONTENT,
        luma_only=False,
        kernel_size=None,
    )
    rate = timeline.frame_rate
    if rate is None or rate <= 0:
        raise ValueError("source frame rate is unavailable for detector arithmetic")
    stats = api.StatsManager(base_timecode=common.FrameTimecode(0, fps=rate))
    stats.register_metrics(detector.get_metrics())
    detector.stats_manager = stats
    observations: list[NativeAdaptiveShot] = []
    previous: Fraction | None = None
    last_timecode: Any = None

    def collect(cuts: list[Any]) -> None:
        for cut in cuts:
            row = _validated_cut(
                cut, stats.get_metrics(cut, [RATIO_METRIC, CONTENT_METRIC]), timeline
            )
            if observations and row.time <= observations[-1].time:
                raise ValueError("adaptive detector cuts are not strictly increasing")
            observations.append(row)

    for native, pixels in decoded_source_frames(source_path, timeline, stop):
        if stop.is_set():
            return ()
        relative = native - timeline.source_start
        if not 0 <= relative <= timeline.duration or (previous is not None and native <= previous):
            raise ValueError("decoded shot frames have invalid or unordered source timestamps")
        previous = native
        # Timecode is rational and source-relative because FrameTimecode rejects negative values.
        # fps is required by detector arithmetic, never used to reconstruct emitted cut timestamps.
        last_timecode = common.FrameTimecode(
            common.Timecode(relative.numerator, Fraction(1, relative.denominator)), fps=rate
        )
        collect(detector.process_frame(timecode=last_timecode, frame_img=pixels))
    if stop.is_set():
        return ()
    if last_timecode is None:
        raise ValueError("selected source video decoded no frames")
    collect(detector.post_process(last_timecode))
    return tuple(observations)


async def detect_adaptive_source_shots(
    source_path: Path, *, timeline: MediaTimelineFacts
) -> tuple[NativeAdaptiveShot, ...]:
    """Run a qualified installation, stopping and joining the decoder on cancellation.

    Unsupported installations raise instead of returning a false empty measurement.
    The evidence wrapper records known failures as unavailable.
    """
    available = pyscenedetect_availability()
    if available["status"] != "available":
        raise RuntimeError(available["reason"])
    stop = Event()
    task = asyncio.create_task(asyncio.to_thread(_run_detection, source_path, timeline, stop))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        # Do not release the source lease while a cancelled decoder still reads the file.
        with suppress(Exception):
            await task
        raise
