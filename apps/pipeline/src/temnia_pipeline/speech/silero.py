"""Direct Silero v6.2.1 ONNX inference without the Torch-based Python package."""

# ruff: noqa: C901, EM102, TRY003

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np

from temnia_pipeline.media.speech_pcm import SAMPLE_RATE, WINDOW_SAMPLES, PcmWindow
from temnia_pipeline.speech.assets import verify_asset

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

SILERO_REVISION = "7e30209a3e901f9842f81b225f3e93d8199902b1"
SILERO_FILENAME = "silero_vad_16k_op15.onnx"
SILERO_SIZE_BYTES = 1_289_603
SILERO_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
SILERO_DETECTOR = "silero-vad-6.2.1-op15-16khz"
CONTEXT_SAMPLES = 64


class OnnxSession(Protocol):
    """The subset of onnxruntime.InferenceSession used by the pinned graph."""

    def run(self, output_names: None, inputs: dict[str, np.ndarray]) -> list[Any]:
        """Run one window and return probability plus recurrent state."""
        ...


@dataclass(frozen=True, slots=True)
class VadConfig:
    """Versioned provisional evidence thresholds copied from Silero defaults."""

    threshold: float = 0.5
    negative_threshold: float = 0.35
    min_speech_ms: int = 250
    min_silence_ms: int = 100
    speech_pad_ms: int = 30


DEFAULT_VAD_CONFIG = VadConfig()


@dataclass(frozen=True, slots=True)
class SpeechInterval:
    """One detected speech interval and summary of its window probabilities."""

    start_ms: int
    end_ms: int
    probability_min: float
    probability_mean: float
    probability_max: float


@dataclass(frozen=True, slots=True)
class SpeechEvidence:
    """Independent detector evidence; it is not a claim that other time is silence."""

    detector: str
    detector_revision: str
    detector_sha256: str
    duration_ms: int
    window_count: int
    intervals: list[SpeechInterval]
    probability_quantiles: dict[str, float]
    probability_histogram: list[int]


class SileroOnnx:
    """The upstream 16 kHz recurrent window contract in NumPy."""

    def __init__(self, session: OnnxSession) -> None:
        self._session = session
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)

    @classmethod
    def from_path(cls, path: Path) -> SileroOnnx:
        """Open a prepared local asset; runtime never downloads weights."""
        import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]  # noqa: PLC0415

        verify_asset(path.read_bytes())
        return cls(
            cast("OnnxSession", ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]))
        )

    def reset(self) -> None:
        """Reset between sources so recurrent state never crosses recordings."""
        self._state.fill(0)
        self._context.fill(0)

    def probability(self, samples: np.ndarray) -> float:
        """Run exactly one 512-sample float32 window with 64 samples of context."""
        if samples.shape != (WINDOW_SAMPLES,):
            raise ValueError(f"Silero requires {WINDOW_SAMPLES} samples, got {samples.shape}")
        window = np.asarray(samples, dtype=np.float32).reshape(1, WINDOW_SAMPLES)
        model_input = np.concatenate((self._context, window), axis=1)
        output = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        probability = np.asarray(output[0], dtype=np.float32)
        state = np.asarray(output[1], dtype=np.float32)
        value = float(probability.reshape(-1)[0])
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"Silero returned invalid probability {value!r}")
        if state.shape != (2, 1, 128) or not np.isfinite(state).all():
            message = "Silero returned invalid recurrent state"
            raise ValueError(message)
        self._state = state
        self._context = window[:, -CONTEXT_SAMPLES:].copy()
        return value


def _milliseconds(samples: int) -> int:
    return round(samples * 1000 / SAMPLE_RATE)


def probabilities_to_intervals(
    probabilities: list[float], duration_samples: int, config: VadConfig
) -> list[SpeechInterval]:
    """Apply Silero's hysteresis/min-duration/padding semantics to window evidence."""
    if not probabilities or duration_samples <= 0:
        return []
    min_speech = round(config.min_speech_ms * SAMPLE_RATE / 1000)
    min_silence = round(config.min_silence_ms * SAMPLE_RATE / 1000)
    pad = round(config.speech_pad_ms * SAMPLE_RATE / 1000)
    triggered = False
    start = 0
    silence_start: int | None = None
    raw: list[tuple[int, int]] = []
    for index, probability in enumerate(probabilities):
        position = index * WINDOW_SAMPLES
        if probability >= config.threshold and not triggered:
            triggered = True
            start = position
            silence_start = None
        if triggered and probability < config.negative_threshold:
            silence_start = position if silence_start is None else silence_start
            if position - silence_start >= min_silence:
                if silence_start - start >= min_speech:
                    raw.append((start, silence_start))
                triggered = False
                silence_start = None
        elif probability >= config.threshold:
            silence_start = None
    if triggered and duration_samples - start >= min_speech:
        raw.append((start, duration_samples))

    padded: list[tuple[int, int]] = []
    for index, (begin, end) in enumerate(raw):
        left = max(0, begin - pad)
        right = min(duration_samples, end + pad)
        if index and left < padded[-1][1]:
            midpoint = (padded[-1][1] + left) // 2
            previous = padded[-1]
            padded[-1] = (previous[0], midpoint)
            left = midpoint
        padded.append((left, right))

    intervals: list[SpeechInterval] = []
    for begin, end in padded:
        first = begin // WINDOW_SAMPLES
        last = min(len(probabilities), (end + WINDOW_SAMPLES - 1) // WINDOW_SAMPLES)
        values = probabilities[first:last] or [0.0]
        intervals.append(
            SpeechInterval(
                start_ms=_milliseconds(begin),
                end_ms=min(_milliseconds(end), _milliseconds(duration_samples)),
                probability_min=min(values),
                probability_mean=sum(values) / len(values),
                probability_max=max(values),
            )
        )
    return intervals


async def detect_speech(
    windows: AsyncIterable[PcmWindow],
    model: SileroOnnx,
    config: VadConfig = DEFAULT_VAD_CONFIG,
) -> SpeechEvidence:
    """Consume a PCM stream with continuous state and clamp the padded tail to real duration."""
    model.reset()
    probabilities: list[float] = []
    duration_samples = 0
    async for window in windows:
        probabilities.append(model.probability(window.samples))
        duration_samples = max(duration_samples, window.start_sample + window.valid_samples)
    values = np.asarray(probabilities, dtype=np.float64)
    quantiles = (
        {
            name: float(value)
            for name, value in zip(
                ("p10", "p50", "p90", "p99"),
                np.quantile(values, [0.1, 0.5, 0.9, 0.99]),
                strict=True,
            )
        }
        if probabilities
        else {}
    )
    histogram = (
        np.histogram(values, bins=np.linspace(0, 1, 11))[0].astype(int).tolist()
        if probabilities
        else [0] * 10
    )
    return SpeechEvidence(
        detector=SILERO_DETECTOR,
        detector_revision=SILERO_REVISION,
        detector_sha256=SILERO_SHA256,
        duration_ms=_milliseconds(duration_samples),
        window_count=len(probabilities),
        intervals=probabilities_to_intervals(probabilities, duration_samples, config),
        probability_quantiles=quantiles,
        probability_histogram=histogram,
    )
