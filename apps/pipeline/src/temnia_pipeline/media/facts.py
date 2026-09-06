"""What the probe found about a video stream, without importing a decoder.

`probe.py` produces `VideoFacts` and needs PyAV to do it; the ladder only
consumes them. Keeping the record here means the Modal image can plan and run
a ladder from facts the worker already probed, on `media.hls` alone, without
carrying PyAV and its bundled ffmpeg libraries into a CUDA image.
"""

from __future__ import annotations

from dataclasses import dataclass

# Not a type-checking-only import: pydantic resolves these annotations at
# runtime when `LadderJob` carries a `VideoFacts`, and a name that exists only
# for the type checker fails there (AGENTS.md, Python pipeline).
from fractions import Fraction  # noqa: TC003


@dataclass(frozen=True, slots=True)
class VideoFacts:
    """What the ladder needs about the video stream."""

    width: int
    height: int
    fps: Fraction
    variable_frame_rate: bool
    codec: str
