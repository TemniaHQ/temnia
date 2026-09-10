"""Candidate grounding, retained inference and one-admission execution invariants."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from temnia_pipeline.chapter_llama.checkpoints import prepare_execution, publish_outcome
from temnia_pipeline.chapter_llama.cli import dispatch_once
from temnia_pipeline.chapter_llama.client import ChapterLlamaModalClient, DeploymentIdentity
from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaInput,
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    InferenceResult,
    InputSentence,
    ResourceProfile,
    canonical,
    parse_predictions,
    validate_result,
)
from temnia_pipeline.chapter_llama.inference import TransformersEngine, build_prompt, snapshot
from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.evals.segment_report import build_report
from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
)
from temnia_pipeline.substrate.chapter_llama import ChapterLlamaSegmenter, input_from_layers
from temnia_pipeline.substrate.factory import make_segmenter
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter

BUILD = "a" * 64
FIXTURES = Path(__file__).parent / "fixtures" / "substrate"


def request() -> ChapterLlamaInput:
    return ChapterLlamaInput(
        duration_ms=40_000,
        sentences=(
            InputSentence(id="s0", start_ms=250, end_ms=9000, text="First complete topic."),
            InputSentence(
                id="s1", start_ms=10_100, end_ms=19_000, text="More about the first topic."
            ),
            InputSentence(
                id="s2", start_ms=20_400, end_ms=39_000, text="A completely different topic."
            ),
        ),
    )


def result_for(
    value: ChapterLlamaInput, raw: str = "00:00:00 - First\n00:00:20 - Second"
) -> InferenceResult:
    return InferenceResult(
        input_sha256=value.sha256,
        config=value.config,
        raw_output=raw,
        predictions=parse_predictions(raw, value),
        input_tokens=123,
        output_tokens=15,
        elapsed_seconds=1,
    )


def job_for(value: ChapterLlamaInput | None = None) -> ChapterLlamaJob:
    return ChapterLlamaJob(
        organization_id=UUID(int=1),
        source_id=UUID(int=2),
        operation_id=UUID(int=3),
        attempt_id=UUID(int=4),
        expected_build=BUILD,
        input=value or request(),
    )


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


def test_predictions_are_suggestions_mapped_to_real_sentences() -> None:
    predictions = parse_predictions("00:00:00 - Opening\n00:00:21 - Change", request())
    assert [(p.proposed_ms, p.sentence_id, p.sentence_start_ms) for p in predictions] == [
        (0, "s0", 250),
        (21_000, "s2", 20_400),
    ]
    assert len(parse_predictions("00:00:00 - One coherent subject", request())) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Here are the chapters:\n00:00:00 - Opening",
        "00:00:05 - Omitted opening",
        "00:00:00 - A\n00:00:00 - B",
        "00:00:00 - A\n00:00:40 - Outside",
        "00:00:00 - A\n00:00:21 - B\n00:00:10 - Reordered",
        "00:00:00 - A\n00:00:01 - Same sentence",
        "00:60:00 - Bad timestamp",
        "00:00:00 - ",
        "00:00:00 - A\n[00:00:20] B",
    ],
)
def test_malformed_or_ungrounded_predictions_are_not_silently_dropped(raw: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_predictions(raw, request())


def test_saved_response_is_bound_to_exact_text_times_model_and_mapping() -> None:
    value = request()
    result = result_for(value)
    validate_result(result, value)
    changed = value.model_copy(
        update={
            "sentences": (
                value.sentences[0].model_copy(update={"text": "Different words."}),
                *value.sentences[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="identity"):
        validate_result(result, changed)
    forged = result.model_copy(update={"predictions": result.predictions[:1]})
    with pytest.raises(ValueError, match="mappings"):
        validate_result(forged, value)


def test_empty_input_does_not_load_any_model() -> None:
    engine = TransformersEngine(root=Path("/missing-pinned-models"))
    empty = ChapterLlamaInput(duration_ms=10_000, sentences=())
    assert engine.infer(empty).predictions == ()
    with pytest.raises(ValueError, match="empty transcript"):
        parse_predictions("00:00:00 - Hallucination", empty)


def test_prompt_contains_every_sentence_and_original_timestamp() -> None:
    prompt = build_prompt(request())
    assert "duration 00:00:40" in prompt
    for item in request().sentences:
        assert item.text in prompt
    assert "00:00:20: A completely different topic." in prompt


def test_snapshot_missing_files_fails_before_any_loader_or_network(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="pinned"):
        snapshot(tmp_path, "owner/model", "b" * 40, ("model.safetensors",))


async def test_one_admission_survives_container_restart_and_reuses_committed_bytes() -> None:
    store, job = MemoryStore(), job_for()
    assert await prepare_execution(store, job, build=BUILD, call_id="fc1", task_id="ta1") is None
    with pytest.raises(AdmissionOutcomeUnknownError):
        await prepare_execution(store, job, build=BUILD, call_id="fc1", task_id="ta2")
    outcome = ChapterLlamaOutcome(
        job_sha256=job.sha256,
        build=BUILD,
        modal_call_id="fc1",
        modal_task_id="ta1",
        status="ok",
        result=result_for(job.input),
    )
    await publish_outcome(store, job, outcome)
    assert await prepare_execution(store, job, build=BUILD, call_id="fc2", task_id="ta3") == outcome
    with pytest.raises(CheckpointCollisionError):
        await publish_outcome(store, job, outcome.model_copy(update={"modal_task_id": "other"}))


async def test_changed_request_cannot_adopt_an_existing_attempt() -> None:
    store, job = MemoryStore(), job_for()
    await prepare_execution(store, job, build=BUILD, call_id="fc1", task_id="ta1")
    outcome = ChapterLlamaOutcome(
        job_sha256=job.sha256,
        build=BUILD,
        modal_call_id="fc1",
        modal_task_id="ta1",
        status="failed",
        error_code="InvalidGenerationError",
        error_message="malformed",
        rejected_output="bad retained paid answer",
        rejected_input_tokens=30,
        rejected_output_tokens=4,
    )
    await publish_outcome(store, job, outcome)
    changed = job.model_copy(update={"operation_id": UUID(int=5)})
    with pytest.raises(CheckpointCollisionError):
        await prepare_execution(store, changed, build=BUILD, call_id="fc2", task_id="ta2")
    assert b"bad retained paid answer" in store.objects[job.checkpoint_key]


class AmbiguousClient(ChapterLlamaModalClient):
    def __init__(self) -> None:
        super().__init__(environment="staging")
        self.dispatches = 0

    async def deployment_identity(self) -> DeploymentIdentity:
        return DeploymentIdentity(build=BUILD, config=request().config, resources=ResourceProfile())

    async def spawn(self, job: ChapterLlamaJob) -> str:
        _ = job
        self.dispatches += 1
        message = "transport lost after dispatch"
        raise OSError(message)


async def test_ambiguous_spawn_keeps_intent_and_cannot_redispatch_same_state(
    tmp_path: Path,
) -> None:
    client = AmbiguousClient()
    path = tmp_path / "attempt.json"
    with pytest.raises(OSError, match="transport lost"):
        await dispatch_once(
            client, request(), organization_id=UUID(int=1), source_id=UUID(int=2), state_path=path
        )
    assert path.exists()
    with pytest.raises(FileExistsError):
        await dispatch_once(
            client, request(), organization_id=UUID(int=1), source_id=UUID(int=2), state_path=path
        )
    assert client.dispatches == 1


def test_explicit_eval_candidate_replays_an_exact_recording(tmp_path: Path) -> None:
    transcript_path = FIXTURES / "two-topics.transcript.json"
    transcript = TranscriptV1.model_validate_json(transcript_path.read_bytes())
    layers = LegacyRulesSegmenter().segment(transcript.words)
    value = input_from_layers(layers, duration_ms=transcript.durationMs)
    midpoint = value.sentences[len(value.sentences) // 2].start_ms // 1000
    raw = f"00:00:00 - Rugby\n00:{midpoint // 60:02}:{midpoint % 60:02} - Investing"
    recording = tmp_path / "result.json"
    recording.write_bytes(canonical(result_for(value, raw).model_dump(mode="json")))
    report = build_report(
        transcript_path, specs=["legacy", f"chapter-llama:sentences_from=legacy,result={recording}"]
    )
    assert len(report.rows[1].layers.candidates) == 1
    assert report.rows[1].layers.provenance.params["mode"] == "recorded"
    assert report.rows[1].layers.provenance.params["inference"] == result_for(
        value, raw
    ).model_dump(mode="json")


def test_factory_validates_recursive_and_unknown_configuration() -> None:
    with pytest.raises(ValueError, match="sentences_from"):
        make_segmenter("chapter-llama", sentences_from="chapter-llama")
    with pytest.raises(ValueError, match="takes no parameter"):
        make_segmenter("chapter-llama", unknown=True)
    assert isinstance(
        make_segmenter("chapter-llama", sentences_from="legacy"), ChapterLlamaSegmenter
    )
