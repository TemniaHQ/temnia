from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

from temnia_pipeline.speech.activities import (
    _frozen_plan_config,  # pyright: ignore[reportPrivateUsage]
)
from temnia_pipeline.speech.activities_v2 import (
    SpeechActivitiesV2,
    _await_terminal,  # pyright: ignore[reportPrivateUsage]
    _drain_tasks,  # pyright: ignore[reportPrivateUsage]
    validate_fresh_checkpoint_v2,
)
from temnia_pipeline.speech.assignment import assign_word_speakers_v386, assignment_for
from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
)
from temnia_pipeline.speech.checkpoints_v2 import (
    admission_for_v2,
    admission_key_v2,
    artifact_ref_v2,
    checkpoint_for_v2,
    prepare_execution_v2,
    publish_checkpoint_v2,
    read_input_checkpoint_v2,
)
from temnia_pipeline.speech.contracts import (
    CheckpointSource,
    ExecutionIdentity,
    StageTelemetry,
    canonical_json,
)
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    SpeechCheckpointV2,
    SpeechStageJobV2,
    SpeechStageResultV2,
    StageConfigV2,
    StageV2,
)
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.speech.stages_v2 import (
    alignment_model_cache,
    run_align_v2,
    run_recognize_v2,
    run_speaker_turns_v2,
)
from temnia_pipeline.speech.telemetry import TelemetryRecorder
from temnia_pipeline.transcription.checkpointed import TranscriptionPlan

PREFIX = "org/a/source/b/"
BUILD = "v2-build"
OPERATION = UUID("11111111-1111-1111-1111-111111111111")
ATTEMPT = UUID("22222222-2222-2222-2222-222222222222")


def profile() -> SpeechResourceProfile:
    return SpeechResourceProfile()


def manifest() -> SpeechModelManifest:
    digest = "a" * 64
    return SpeechModelManifest(
        sha256=digest,
        file_count=7,
        total_bytes=1234,
        model_root=f"/models/frozen/{digest}",
    )


def v1_plan() -> TranscriptionPlan:
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech",
        environment=None,
        protocol="temnia-speech/1",
        build="old-build",
        budget_micros=1,
        rate_micros_per_hour=1,
        stage_timeout_seconds=3600,
        startup_timeout_seconds=120,
        dispatch_limit=5,
        detector="silero",
        detector_revision="revision",
        detector_sha256="d" * 64,
    )


def test_v1_frozen_run_config_does_not_gain_v2_null_fields() -> None:
    body = _frozen_plan_config(v1_plan())
    assert not {
        "resourceProfile",
        "modelManifest",
        "executionTopology",
        "recognizeV2",
        "speakerTurns",
        "allowOomRecovery",
    }.intersection(body)


def test_v2_requires_explicit_oom_policy() -> None:
    raw = v1_plan().model_dump(mode="json", by_alias=True)
    raw.update(
        {
            "protocol": "temnia-speech/2",
            "rateMicrosPerHour": profile().minimum_rate_micros_per_hour,
            "resourceProfile": profile().model_dump(mode="json", by_alias=True),
            "modelManifest": manifest().model_dump(mode="json", by_alias=True),
            "executionTopology": "parallel",
            "recognizeV2": RecognizeConfigV2().model_dump(mode="json", by_alias=True),
            "speakerTurns": SpeakerTurnsConfig().model_dump(mode="json", by_alias=True),
        }
    )
    with pytest.raises(ValueError, match="requires resources"):
        TranscriptionPlan.model_validate(raw)
    raw["allowOomRecovery"] = False
    assert TranscriptionPlan.model_validate(raw).allow_oom_recovery is False


async def test_parallel_drain_survives_repeated_parent_cancellation() -> None:
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def child() -> object:
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await asyncio.sleep(0.02)
            cleanup_finished.set()

    child_task: asyncio.Task[object] = asyncio.create_task(child())

    async def parent() -> None:
        await _drain_tasks([child_task], cancel=True)

    parent_task = asyncio.create_task(parent())
    await cleanup_started.wait()
    parent_task.cancel()
    parent_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent_task
    assert cleanup_finished.is_set()


async def test_terminal_accounting_survives_repeated_parent_cancellation() -> None:
    commit_started = asyncio.Event()
    commit_finished = asyncio.Event()
    allow_commit = asyncio.Event()

    async def commit() -> None:
        commit_started.set()
        await allow_commit.wait()
        commit_finished.set()

    parent = asyncio.create_task(_await_terminal(commit()))
    await commit_started.wait()
    parent.cancel()
    parent.cancel()
    allow_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert commit_finished.is_set()


async def test_expired_cancel_records_racing_completion_unknown_before_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_cancel_started = asyncio.Event()
    recovery_started = asyncio.Event()
    allow_recovery = asyncio.Event()
    terminal_recorded = asyncio.Event()
    return_modes: list[bool] = []
    instance = SpeechActivitiesV2.__new__(SpeechActivitiesV2)

    async def cancel_and_wait(
        _client: object,
        _lease: object,
        _handle: str,
        *,
        return_finished: bool,
    ) -> object:
        return_modes.append(return_finished)
        if return_finished:
            first_cancel_started.set()
            await asyncio.Future()
        recovery_started.set()
        await allow_recovery.wait()
        terminal_recorded.set()
        message = "racing completion recorded outcome unknown"
        raise ApplicationError(
            message,
            non_retryable=True,
            type="ProviderOutcomeUnknown",
        )

    monkeypatch.setattr(instance, "_cancel_and_wait", cancel_and_wait)
    parent = asyncio.create_task(
        instance._cancel_expired_v2(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
            cast("Any", object()), cast("Any", object()), "call-1"
        )
    )
    await first_cancel_started.wait()
    parent.cancel()
    await recovery_started.wait()
    parent.cancel()
    allow_recovery.set()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert terminal_recorded.is_set()
    assert return_modes == [True, False]


async def test_parallel_branch_failure_does_not_detach_cleanup_on_parent_cancel() -> None:
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    instance = SpeechActivitiesV2.__new__(SpeechActivitiesV2)

    async def fail() -> object:
        await asyncio.sleep(0)
        message = "known branch failure"
        raise RuntimeError(message)

    async def remote_branch() -> object:
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await asyncio.sleep(0.02)
            cleanup_finished.set()

    parent = asyncio.create_task(
        instance._parallel_branches(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
            fail(), remote_branch()
        )
    )
    await cleanup_started.wait()
    parent.cancel()
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert cleanup_finished.is_set()


def test_v2_activity_registration_retains_v1_and_adds_two_names() -> None:
    instance = SpeechActivitiesV2.__new__(SpeechActivitiesV2)
    methods = instance.activities()
    names = [getattr(method, "__temporal_activity_definition").name for method in methods]
    assert names[-2:] == ["checkpointed_transcribe_v2", "assemble_checkpointed_transcript_v2"]
    assert "checkpointed_transcribe" in names


def job(
    stage: StageV2,
    attempt: UUID,
    configuration: StageConfigV2,
    input_checkpoint: ArtifactRefV2 | None = None,
) -> SpeechStageJobV2:
    return SpeechStageJobV2(
        build=BUILD,
        operation_id=OPERATION if stage == "recognize" else UUID(int=OPERATION.int + attempt.int),
        attempt_id=attempt,
        stage=stage,
        artifact_prefix=PREFIX,
        audio_key=PREFIX + "audio/source.m4a",
        audio_sha256="b" * 64,
        audio_size_bytes=123,
        duration_ms=12_000,
        checkpoint_key=PREFIX + f"transcript/v2/checkpoints/{stage}/{attempt}.json",
        configuration=configuration,
        input_checkpoint=input_checkpoint,
        resource_profile=profile(),
        model_manifest=manifest(),
        execution_topology="parallel",
    )


def test_fresh_result_binds_exact_job_ref_and_execution_identity() -> None:
    request = job("recognize", ATTEMPT, RecognizeConfigV2())
    execution = ExecutionIdentity(
        modal_call_id="call-1",
        modal_task_id="task-1",
        admission_key=admission_key_v2(request),
    )
    checkpoint = checkpoint_for_v2(
        request,
        BUILD,
        {"language": "en", "segments": []},
        execution,
    )
    body = canonical_json(checkpoint.model_dump(mode="json", by_alias=True))
    reference = artifact_ref_v2(
        request.checkpoint_key,
        request.stage,
        checkpoint.configuration_sha256,
        body,
    )
    admission_body = canonical_json(
        admission_for_v2(
            request,
            BUILD,
            modal_call_id=execution.modal_call_id,
            modal_task_id=execution.modal_task_id,
        ).model_dump(mode="json", by_alias=True)
    )
    result = SpeechStageResultV2(
        build=BUILD,
        status="ok",
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        stage=request.stage,
        modal_call_id="call-1",
        modal_task_id="task-1",
        execution_identity=execution,
        checkpoint=reference,
        telemetry=StageTelemetry(),
        resource_profile=request.resource_profile,
        model_manifest=request.model_manifest,
        execution_topology=request.execution_topology,
    )
    assert (
        validate_fresh_checkpoint_v2(request, result, body, admission_body, handle="call-1")
        == checkpoint
    )

    reused = result.model_copy(
        update={
            "checkpoint_reused": True,
            "modal_task_id": "responding-restart-task",
            "telemetry": StageTelemetry(
                complete=False,
                metrics_unavailable=["checkpoint_reused_without_original_container_metrics"],
            ),
        }
    )
    assert (
        validate_fresh_checkpoint_v2(request, reused, body, admission_body, handle="call-1")
        == checkpoint
    )

    wrong_fresh_task = result.model_copy(update={"modal_task_id": "other-task"})
    with pytest.raises(CheckpointCollisionError, match="execution identity"):
        validate_fresh_checkpoint_v2(
            request, wrong_fresh_task, body, admission_body, handle="call-1"
        )

    wrong_ref = result.model_copy(
        update={"checkpoint": reference.model_copy(update={"sha256": "f" * 64})}
    )
    with pytest.raises(CheckpointCollisionError, match="execution identity"):
        validate_fresh_checkpoint_v2(request, wrong_ref, body, admission_body, handle="call-1")

    wrong_operation = checkpoint.model_copy(
        update={"operation_id": UUID(int=request.operation_id.int + 1)}
    )
    wrong_job_body = canonical_json(wrong_operation.model_dump(mode="json", by_alias=True))
    with pytest.raises(CheckpointCollisionError, match="identity mismatch"):
        validate_fresh_checkpoint_v2(
            request, result, wrong_job_body, admission_body, handle="call-1"
        )

    for changed_execution in (
        execution.model_copy(update={"modal_call_id": "call-other"}),
        execution.model_copy(update={"modal_task_id": "task-other"}),
        execution.model_copy(update={"admission_key": admission_key_v2(request) + ".other"}),
    ):
        changed = checkpoint.model_copy(update={"execution": changed_execution})
        changed_body = canonical_json(changed.model_dump(mode="json", by_alias=True))
        changed_ref = artifact_ref_v2(
            request.checkpoint_key,
            request.stage,
            changed.configuration_sha256,
            changed_body,
        )
        changed_result = result.model_copy(update={"checkpoint": changed_ref})
        with pytest.raises(CheckpointCollisionError, match="execution identity"):
            validate_fresh_checkpoint_v2(
                request,
                changed_result,
                changed_body,
                admission_body,
                handle="call-1",
            )

    with pytest.raises(CheckpointCollisionError, match="admission"):
        validate_fresh_checkpoint_v2(request, result, body, b"{}", handle="call-1")


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


def test_v2_jobs_have_disjoint_keys_and_exact_dependency_rules() -> None:
    recognize = job("recognize", ATTEMPT, RecognizeConfigV2())
    recognize_checkpoint = checkpoint_for_v2(recognize, BUILD, {"language": "en", "segments": []})
    body = json.dumps(
        recognize_checkpoint.model_dump(mode="json", by_alias=True),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    reference = ArtifactRefV2(
        key=recognize.checkpoint_key,
        sha256=__import__("hashlib").sha256(body).hexdigest(),
        size_bytes=len(body),
        stage="recognize",
        configuration_sha256=recognize.configuration_sha256,
    )
    align_attempt = UUID("33333333-3333-3333-3333-333333333333")
    assert job("align", align_attempt, AlignConfigV2(language="en"), reference).stage == "align"
    turns_attempt = UUID("44444444-4444-4444-4444-444444444444")
    assert job("speaker_turns", turns_attempt, SpeakerTurnsConfig()).input_checkpoint is None

    with pytest.raises(ValueError, match="must not have inputCheckpoint"):
        job("speaker_turns", turns_attempt, SpeakerTurnsConfig(), reference)
    with pytest.raises(ValueError, match="requires a recognize"):
        job("align", align_attempt, AlignConfigV2(language="en"))
    raw = recognize.model_dump(mode="json", by_alias=True)
    raw["checkpointKey"] = PREFIX + f"transcript/checkpoints/recognize/{ATTEMPT}.json"
    with pytest.raises(ValueError, match="checkpointKey"):
        SpeechStageJobV2.model_validate(raw)


async def test_v2_checkpoint_binds_resources_manifest_topology_and_admission() -> None:
    store = MemoryStore()
    request = job("recognize", ATTEMPT, RecognizeConfigV2())
    reused, execution = await prepare_execution_v2(
        store,
        request,
        BUILD,
        modal_call_id="call-1",
        modal_task_id="task-1",
    )
    assert reused is None
    assert execution is not None
    checkpoint = checkpoint_for_v2(
        request,
        BUILD,
        {"language": "en", "segments": []},
        execution,
    )
    reference = await publish_checkpoint_v2(store, request, checkpoint)
    reused, execution = await prepare_execution_v2(
        store,
        request,
        BUILD,
        modal_call_id="call-1",
        modal_task_id="rescheduled-task",
    )
    assert reused == reference
    assert execution == checkpoint.execution

    admission_key = admission_key_v2(request)
    valid_admission = store.objects[admission_key]
    store.objects[admission_key] = b"{}"
    with pytest.raises(AdmissionOutcomeUnknownError, match="admission cannot be verified"):
        await prepare_execution_v2(
            store,
            request,
            BUILD,
            modal_call_id="call-1",
            modal_task_id="another-restart-task",
        )
    store.objects[admission_key] = valid_admission

    changed = request.model_copy(
        update={"resource_profile": profile().model_copy(update={"cpu_cores": 8})}
    )
    with pytest.raises(CheckpointCollisionError, match="identity mismatch"):
        await prepare_execution_v2(
            store,
            changed,
            BUILD,
            modal_call_id="call-1",
            modal_task_id="task-2",
        )


async def test_v2_input_checkpoint_rejects_different_manifest() -> None:
    store = MemoryStore()
    recognize = job("recognize", ATTEMPT, RecognizeConfigV2())
    reference = await publish_checkpoint_v2(
        store,
        recognize,
        checkpoint_for_v2(recognize, BUILD, {"language": "en", "segments": []}),
    )
    align = job(
        "align",
        UUID("33333333-3333-3333-3333-333333333333"),
        AlignConfigV2(language="en"),
        reference,
    )
    assert (await read_input_checkpoint_v2(store, align)) is not None
    other_digest = "c" * 64
    changed = align.model_copy(
        update={
            "model_manifest": SpeechModelManifest(
                sha256=other_digest,
                file_count=7,
                total_bytes=1234,
                model_root=f"/models/frozen/{other_digest}",
            )
        }
    )
    with pytest.raises(CheckpointCollisionError, match="identity mismatch"):
        await read_input_checkpoint_v2(store, changed)


def test_v2_alignment_cache_refuses_unfrozen_torchaudio_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognize = job("recognize", ATTEMPT, RecognizeConfigV2())
    reference = ArtifactRefV2(
        key=recognize.checkpoint_key,
        sha256="9" * 64,
        size_bytes=1,
        stage="recognize",
        configuration_sha256=recognize.configuration_sha256,
    )
    english = job(
        "align",
        UUID("33333333-3333-3333-3333-333333333333"),
        AlignConfigV2(language="en"),
        reference,
    )
    expected = Path(manifest().model_root) / "hub" / "checkpoints"

    def is_file(path: Path) -> bool:
        return path == expected / "wav2vec2_fairseq_base_ls960_asr_ls960.pth"

    monkeypatch.setattr(Path, "is_file", is_file)
    assert alignment_model_cache(english) == expected

    french = english.model_copy(update={"configuration": AlignConfigV2(language="fr")})
    with pytest.raises(FileNotFoundError, match="not declared for fr"):
        alignment_model_cache(french)
    hugging_face = english.model_copy(
        update={
            "configuration": AlignConfigV2(
                language="nl", model="facebook/wav2vec2-large-xlsr-53-dutch"
            )
        }
    )
    assert alignment_model_cache(hugging_face) == Path(manifest().model_root) / "hub"


def test_cpu_assignment_matches_tagged_overlap_nearest_and_tie_rules() -> None:
    # Expected labels were produced by the exact v3.8.6 IntervalTree and
    # assign_word_speakers functions from whisperx/diarize.py.
    transcript: dict[str, object] = {
        "language": "en",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "one two three",
                "words": [
                    {"word": "one", "start": 0.0, "end": 1.0},
                    {"word": "two", "start": 1.0, "end": 2.0},
                    {"word": "three"},
                ],
            },
            {"start": 3.9, "end": 4.1, "text": "nearest"},
        ],
    }
    turns = [
        {"start": 0.0, "end": 1.0, "speaker": "FIRST"},
        {"start": 0.0, "end": 1.0, "speaker": "SECOND"},
        {"start": 1.0, "end": 2.0, "speaker": "SECOND"},
        {"start": 6.0, "end": 7.0, "speaker": "LATE"},
    ]
    result = assign_word_speakers_v386(transcript, turns)
    segments = cast("list[dict[str, Any]]", result["segments"])
    assert segments[0]["speaker"] == "SECOND"
    assert [word.get("speaker") for word in segments[0]["words"]] == [
        "FIRST",
        "SECOND",
        None,
    ]
    # Segment midpoint 4.0 is equally distant from turn midpoints 1.5 and 6.5;
    # numpy.argmin in the tagged source chooses the first sorted turn.
    assert segments[1]["speaker"] == "SECOND"
    assert transcript["segments"] != result["segments"]


def test_cpu_assignment_rejects_nonfinite_turns_and_preserves_empty_input() -> None:
    raw: dict[str, object] = {"language": "en", "segments": [{"text": "untimed"}]}
    assert assign_word_speakers_v386(raw, []) == raw
    with pytest.raises(ValueError, match="invalid interval"):
        assign_word_speakers_v386(
            raw,
            [{"start": 0.0, "end": math.inf, "speaker": "speaker"}],
        )


class _Diarizer:
    def __call__(self, *_args: object, **_kwargs: object) -> object:
        return _Frame()


class _Frame:
    def to_dict(self, _orientation: str) -> list[dict[str, object]]:
        return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


class _Engine:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.diarize = SimpleNamespace(DiarizationPipeline=self._diarizer)

    def _diarizer(self, **_kwargs: object) -> _Diarizer:
        self.calls.append("speaker_turns")
        return _Diarizer()

    def load_audio(self, _path: str) -> list[float]:
        return [0.0]

    def load_model(self, *_args: object, **_kwargs: object) -> object:
        self.calls.append("recognize")
        return _Recognizer()

    def load_align_model(self, **_kwargs: object) -> tuple[object, dict[str, object]]:
        self.calls.append("align")
        return object(), {"language": "en", "dictionary": {}, "type": "test"}

    def align(self, segments: list[object], *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"segments": segments, "word_segments": []}


class _Recognizer:
    def transcribe(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "language": "en",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        }


async def test_v2_stage_chain_keeps_raw_turns_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    engine = _Engine()
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")

    def cache_path(_job: SpeechStageJobV2) -> Path:
        return tmp_path

    monkeypatch.setattr(
        "temnia_pipeline.speech.stages_v2.alignment_model_cache",
        cache_path,
    )
    recognize_job = job("recognize", ATTEMPT, RecognizeConfigV2())
    recognized = await run_recognize_v2(
        engine,
        store,
        recognize_job,
        audio,
        BUILD,
        TelemetryRecorder(),
        ExecutionIdentity(modal_call_id="call-r", modal_task_id="task-r", admission_key="r"),
    )
    turns_job = job(
        "speaker_turns",
        UUID("44444444-4444-4444-4444-444444444444"),
        SpeakerTurnsConfig(),
    )
    turns = await run_speaker_turns_v2(
        engine,
        store,
        turns_job,
        audio,
        BUILD,
        TelemetryRecorder(),
        "token",
    )
    align_job = job(
        "align",
        UUID("33333333-3333-3333-3333-333333333333"),
        AlignConfigV2(language="en"),
        recognized,
    )
    aligned = await run_align_v2(
        engine,
        store,
        align_job,
        audio,
        BUILD,
        TelemetryRecorder(),
    )
    turns_body = store.objects[turns.key]
    turns_checkpoint = SpeechCheckpointV2.model_validate_json(turns_body)
    aligned_checkpoint = SpeechCheckpointV2.model_validate_json(store.objects[aligned.key])
    assignment = assignment_for(
        build=BUILD,
        source=CheckpointSource(
            audio_key=PREFIX + "audio/source.m4a",
            audio_sha256="b" * 64,
            audio_size_bytes=123,
            duration_ms=12_000,
        ),
        alignment_checkpoint=aligned,
        speaker_turns_checkpoint=turns,
        resource_profile=profile(),
        model_manifest=manifest(),
        execution_topology="parallel",
        aligned_payload=aligned_checkpoint.payload,
        diarization=turns_checkpoint.payload["diarization"],
    )
    words = cast("list[dict[str, Any]]", assignment.raw["segments"])
    assert words[0]["speaker"] == "SPEAKER_00"
    assert engine.calls == ["recognize", "speaker_turns", "align"]
