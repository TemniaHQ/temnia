import asyncio
import json
import math
from uuid import UUID

import pytest

from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
    checkpoint_for,
    prepare_execution,
    publish_checkpoint,
    read_input_checkpoint,
    reuse_checkpoint,
)
from temnia_pipeline.speech.contracts import (
    AlignConfig,
    RecognizeConfig,
    SpeechStageJob,
    canonical_json,
)

OPERATION = UUID("11111111-1111-1111-1111-111111111111")
ATTEMPT = UUID("22222222-2222-2222-2222-222222222222")
PREFIX = "org/a/source/b/"


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


class AmbiguousCreateStore(MemoryStore):
    async def create(self, key: str, body: bytes) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        message = "connection dropped after create"
        raise OSError(message)


def recognize_job() -> SpeechStageJob:
    return SpeechStageJob(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        stage="recognize",
        artifact_prefix=PREFIX,
        audio_key=PREFIX + "audio/source.m4a",
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        duration_ms=12_000,
        checkpoint_key=PREFIX + f"transcript/checkpoints/recognize/{ATTEMPT}.json",
        configuration=RecognizeConfig(),
    )


async def test_checkpoint_create_and_identical_redelivery_reuse() -> None:
    store = MemoryStore()
    job = recognize_job()
    checkpoint = checkpoint_for(job, "build", {"language": "en", "segments": []})
    first = await publish_checkpoint(store, job, checkpoint)
    second = await publish_checkpoint(store, job, checkpoint)
    reused = await reuse_checkpoint(store, job)
    assert first == second == reused
    assert first.size_bytes == len(store.objects[first.key])


async def test_checkpoint_collision_and_corruption_are_terminal() -> None:
    store = MemoryStore()
    job = recognize_job()
    store.objects[job.checkpoint_key] = b"not json"
    with pytest.raises(CheckpointCollisionError, match="invalid checkpoint"):
        await reuse_checkpoint(store, job)

    checkpoint = checkpoint_for(job, "build", {"language": "en", "segments": []})
    store.objects[job.checkpoint_key] = canonical_json(
        checkpoint.model_dump(mode="json", by_alias=True)
    )
    changed = checkpoint.model_copy(update={"payload": {"language": "fr", "segments": []}})
    with pytest.raises(CheckpointCollisionError, match="different bytes"):
        await publish_checkpoint(store, job, changed)


async def test_downstream_ref_hash_and_source_are_verified() -> None:
    store = MemoryStore()
    upstream_job = recognize_job()
    checkpoint = checkpoint_for(upstream_job, "build", {"language": "en", "segments": []})
    ref = await publish_checkpoint(store, upstream_job, checkpoint)
    align_attempt = UUID("33333333-3333-3333-3333-333333333333")
    align_job = SpeechStageJob(
        operation_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        attempt_id=align_attempt,
        stage="align",
        artifact_prefix=PREFIX,
        audio_key=PREFIX + "audio/source.m4a",
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        duration_ms=12_000,
        checkpoint_key=PREFIX + f"transcript/checkpoints/align/{align_attempt}.json",
        configuration=AlignConfig(language="en"),
        input_checkpoint=ref,
    )
    loaded = await read_input_checkpoint(store, align_job)
    assert loaded is not None
    assert loaded.stage == "recognize"
    bad = ref.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(CheckpointCollisionError, match="bytes do not match"):
        await read_input_checkpoint(store, align_job.model_copy(update={"input_checkpoint": bad}))
    wrong_config = ref.model_copy(update={"configuration_sha256": "f" * 64})
    with pytest.raises(CheckpointCollisionError, match="configuration identity"):
        await read_input_checkpoint(
            store, align_job.model_copy(update={"input_checkpoint": wrong_config})
        )

    # A later workflow may reuse the immutable predecessor by semantic hash;
    # neither its operation nor attempt identity is expected to match.
    cross_run = align_job.model_copy(
        update={"operation_id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")}
    )
    assert (await read_input_checkpoint(store, cross_run)) == checkpoint


def test_stage_job_is_strict_and_key_is_attempt_bound() -> None:
    raw = recognize_job().model_dump(mode="json", by_alias=True)
    raw["checkpointKey"] = PREFIX + "transcript/checkpoints/recognize/shared.json"
    with pytest.raises(ValueError, match="checkpointKey"):
        SpeechStageJob.model_validate(raw)
    raw = json.loads(recognize_job().model_dump_json(by_alias=True))
    raw["surprise"] = True
    with pytest.raises(ValueError, match="Extra inputs"):
        SpeechStageJob.model_validate(raw)


def test_config_rejects_nonfinite_and_raw_scores_become_diagnostic_nulls() -> None:
    with pytest.raises(ValueError, match="finite number"):
        RecognizeConfig(vad_options={"onset": math.nan})
    checkpoint = checkpoint_for(
        recognize_job(),
        "build",
        {
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 1.0, "words": [{"word": "hello", "score": math.nan}]}
            ],
        },
    )
    score = checkpoint.payload["segments"][0]["words"][0]["score"]
    assert score is None
    assert checkpoint.payload_diagnostics.nonfinite_count == 1
    assert checkpoint.payload_diagnostics.nonfinite_paths == ["$/segments/0/words/0/score"]
    assert b"NaN" not in canonical_json(checkpoint.model_dump(mode="json", by_alias=True))


async def test_execution_admission_is_single_winner_and_refuses_restarted_container() -> None:
    store = MemoryStore()
    job = recognize_job()

    async def claim(task: str) -> object:
        return await prepare_execution(
            store,
            job,
            "build",
            modal_call_id="call-1",
            modal_task_id=task,
        )

    results = await asyncio.gather(
        claim("container-1"), claim("container-2"), return_exceptions=True
    )
    admitted = [result for result in results if not isinstance(result, BaseException)]
    refused = [result for result in results if isinstance(result, BaseException)]
    assert len(admitted) == len(refused) == 1
    assert isinstance(refused[0], AdmissionOutcomeUnknownError)
    assert "matching prior execution" in str(refused[0])


async def test_ambiguous_admission_create_and_mismatched_build_never_admit() -> None:
    job = recognize_job()
    ambiguous = AmbiguousCreateStore()
    with pytest.raises(AdmissionOutcomeUnknownError, match="create outcome is unknown"):
        await prepare_execution(
            ambiguous,
            job,
            "build",
            modal_call_id="call-1",
            modal_task_id="container-1",
        )

    with pytest.raises(AdmissionOutcomeUnknownError, match="identity collision"):
        await prepare_execution(
            ambiguous,
            job,
            "different-build",
            modal_call_id="call-1",
            modal_task_id="container-2",
        )


async def test_exact_checkpoint_is_stronger_than_an_existing_admission() -> None:
    store = MemoryStore()
    job = recognize_job()
    _none, execution = await prepare_execution(
        store,
        job,
        "build",
        modal_call_id="call-1",
        modal_task_id="container-1",
    )
    assert execution is not None
    checkpoint = checkpoint_for(
        job,
        "build",
        {"language": "en", "segments": []},
        execution,
    )
    expected = await publish_checkpoint(store, job, checkpoint)
    reused, second_execution = await prepare_execution(
        store,
        job,
        "build",
        modal_call_id="call-1",
        modal_task_id="container-2",
    )
    assert reused == expected
    assert second_execution is None


async def test_crash_after_admission_cannot_enter_model_twice() -> None:
    store = MemoryStore()
    job = recognize_job()
    model_calls: list[str] = []

    async def container(task_id: str) -> None:
        _checkpoint, execution = await prepare_execution(
            store,
            job,
            "build",
            modal_call_id="call-1",
            modal_task_id=task_id,
        )
        assert execution is not None
        model_calls.append(task_id)

    await container("container-that-crashes-after-claim")
    with pytest.raises(AdmissionOutcomeUnknownError, match="matching prior execution"):
        await container("rescheduled-container")
    assert model_calls == ["container-that-crashes-after-claim"]
