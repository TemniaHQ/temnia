from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from temnia_pipeline.contracts import TranscriptProvider
from temnia_pipeline.speech.checkpoints import checkpoint_for, publish_checkpoint
from temnia_pipeline.speech.contracts import (
    AlignConfig,
    ArtifactRef,
    DiarizeConfig,
    RecognizeConfig,
    SpeechCheckpointV1,
    SpeechStageJob,
    Stage,
)
from temnia_pipeline.speech.stages import run_align, run_diarize, run_recognize
from temnia_pipeline.speech.telemetry import TelemetryRecorder
from temnia_pipeline.transcription.normalize import normalize_whisperx

OPERATION = UUID("11111111-1111-1111-1111-111111111111")
OPERATIONS = {
    "recognize": OPERATION,
    "align": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    "diarize": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
}
PREFIX = "org/a/source/b/"
AUDIO = PREFIX + "audio/source.m4a"


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def read(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def create(self, key: str, body: bytes) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        return True


class Turns:
    def to_dict(self, orientation: str) -> list[dict[str, object]]:
        assert orientation == "records"
        return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


class Recognizer:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine
        self.model_path = "/models/models--Systran--faster-whisper-large-v3/snapshots/" + "1" * 40

    def transcribe(self, _audio: object, **kwargs: object) -> dict[str, object]:
        self.engine.calls.append("recognize")
        callback = kwargs["progress_callback"]
        assert callable(callback)
        callback(75.0)
        return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}


class Diarizer:
    def __init__(self, engine: FakeEngine, **_kwargs: object) -> None:
        self.engine = engine

    def __call__(self, _audio: object, **kwargs: object) -> Turns:
        self.engine.calls.append("diarize")
        callback = kwargs["progress_callback"]
        assert callable(callback)
        callback(100.0)
        return Turns()


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.diarize = SimpleNamespace(DiarizationPipeline=self.make_diarizer)

    def make_diarizer(self, **kwargs: object) -> Diarizer:
        return Diarizer(self, **kwargs)

    def load_audio(self, path: str) -> str:
        return path

    def load_model(self, *_args: object, **_kwargs: object) -> Recognizer:
        return Recognizer(self)

    def load_align_model(self, **_kwargs: object) -> tuple[str, dict[str, object]]:
        return "model", {}

    def align(self, segments: list[object], *_args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append("align")
        callback = kwargs["progress_callback"]
        assert callable(callback)
        callback(100.0)
        first = cast("dict[str, object]", segments[0])
        aligned: list[dict[str, object]] = [
            {
                **first,
                "words": [{"word": "hi", "start": 0.0, "end": 1.0, "score": 0.9}],
            }
        ]
        return {"segments": aligned, "word_segments": aligned[0]["words"]}

    def assign_word_speakers(
        self, _turns: Turns, raw: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        segments = cast("list[dict[str, object]]", raw["segments"])
        segments[0]["speaker"] = "SPEAKER_00"
        words = cast("list[dict[str, object]]", segments[0]["words"])
        words[0]["speaker"] = "SPEAKER_00"
        return raw


def job(
    stage: Stage,
    attempt: str,
    configuration: RecognizeConfig | AlignConfig | DiarizeConfig,
    input_checkpoint: ArtifactRef | None = None,
) -> SpeechStageJob:
    return SpeechStageJob(
        operation_id=OPERATIONS[stage],
        attempt_id=UUID(attempt),
        stage=stage,
        artifact_prefix=PREFIX,
        audio_key=AUDIO,
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        duration_ms=1_000,
        checkpoint_key=PREFIX + f"transcript/checkpoints/{stage}/{attempt}.json",
        configuration=configuration,
        input_checkpoint=input_checkpoint,
    )


async def load(store: MemoryStore, key: str) -> SpeechCheckpointV1:
    return SpeechCheckpointV1.model_validate_json(store.objects[key])


async def test_stages_are_independent_and_final_raw_normalizes() -> None:
    store = MemoryStore()
    engine = FakeEngine()
    audio_file = Path("/does/not/need/to/exist")
    recognize = job("recognize", "22222222-2222-2222-2222-222222222222", RecognizeConfig())
    recognize_ref = await run_recognize(
        engine, store, recognize, audio_file, "build", TelemetryRecorder()
    )
    recognized = await load(store, recognize_ref.key)
    assert recognized.model_provenance.requested_model == "large-v3"
    assert recognized.model_provenance.resolved_model == "Systran/faster-whisper-large-v3"
    assert recognized.model_provenance.resolved_revision == "1" * 40
    assert recognized.model_provenance.weight_sha256 is None
    assert recognized.model_provenance.identity_status == "observed_unpinned"
    assert recognized.model_provenance.reuse_scope == "run"
    assert "whisperx" in recognized.model_provenance.library_versions
    align = job(
        "align",
        "33333333-3333-3333-3333-333333333333",
        AlignConfig(language="en"),
        recognize_ref,
    )
    align_ref = await run_align(engine, store, align, audio_file, "build", TelemetryRecorder())
    diarize = job(
        "diarize",
        "44444444-4444-4444-4444-444444444444",
        DiarizeConfig(),
        align_ref,
    )
    diarize_ref = await run_diarize(
        engine, store, diarize, audio_file, "build", TelemetryRecorder(), "token"
    )
    final = await load(store, diarize_ref.key)
    transcript = normalize_whisperx(
        final.payload["raw"],
        1_000,
        TranscriptProvider(name="whisperx", model="large-v3", version="3.8.6"),
    )
    assert [word.text for word in transcript.words] == ["hi"]
    assert len({recognize.operation_id, align.operation_id, diarize.operation_id}) == 3
    assert final.payload["diarization"] == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    assert engine.calls == ["recognize", "align", "diarize"]


async def test_repeated_delivery_reuses_before_model_load() -> None:
    store = MemoryStore()
    engine = FakeEngine()
    request = job("recognize", "22222222-2222-2222-2222-222222222222", RecognizeConfig())
    checkpoint = checkpoint_for(request, "build", {"language": "en", "segments": []})
    expected = await publish_checkpoint(store, request, checkpoint)
    found = await run_recognize(
        engine, store, request, Path("missing"), "build", TelemetryRecorder()
    )
    assert found == expected
    assert engine.calls == []
