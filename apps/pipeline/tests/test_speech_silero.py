from collections.abc import AsyncIterator

import numpy as np
import pytest

from temnia_pipeline.media.speech_pcm import WINDOW_SAMPLES, PcmWindow
from temnia_pipeline.speech.silero import SileroOnnx, VadConfig, detect_speech


class FakeSession:
    def __init__(self) -> None:
        self.inputs: list[dict[str, np.ndarray]] = []

    def run(self, output_names: None, inputs: dict[str, np.ndarray]) -> list[object]:
        assert output_names is None
        self.inputs.append({key: value.copy() for key, value in inputs.items()})
        state = inputs["state"] + 1
        return [np.array([[0.9]], dtype=np.float32), state]


async def windows() -> AsyncIterator[PcmWindow]:
    first = np.arange(WINDOW_SAMPLES, dtype=np.float32)
    yield PcmWindow(first, WINDOW_SAMPLES, 0)
    second = np.full(WINDOW_SAMPLES, 9, dtype=np.float32)
    yield PcmWindow(second, 100, WINDOW_SAMPLES)


async def test_upstream_context_state_and_final_duration_clamp() -> None:
    session = FakeSession()
    evidence = await detect_speech(
        windows(),
        SileroOnnx(session),
        VadConfig(min_speech_ms=0, min_silence_ms=0, speech_pad_ms=30),
    )
    assert session.inputs[0]["input"].shape == (1, 576)
    assert np.all(session.inputs[0]["input"][0, :64] == 0)
    assert np.array_equal(
        session.inputs[1]["input"][0, :64], np.arange(WINDOW_SAMPLES, dtype=np.float32)[-64:]
    )
    assert np.all(session.inputs[0]["state"] == 0)
    assert np.all(session.inputs[1]["state"] == 1)
    assert session.inputs[0]["sr"].dtype == np.int64
    assert evidence.duration_ms == round(612 * 1_000 / 16_000)
    assert evidence.intervals[-1].end_ms == evidence.duration_ms


def test_invalid_model_probability_is_refused() -> None:
    class Invalid(FakeSession):
        def run(self, output_names: None, inputs: dict[str, np.ndarray]) -> list[object]:
            result = super().run(output_names, inputs)
            result[0] = np.array([[np.nan]], dtype=np.float32)
            return result

    with pytest.raises(ValueError, match="invalid probability"):
        SileroOnnx(Invalid()).probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
