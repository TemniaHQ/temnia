"""Bounded evidence and cancellation behavior for the speech benchmark driver."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SCRIPT = Path(__file__).parents[1] / "scripts/benchmark_checkpointed_speech.py"
SPEC = importlib.util.spec_from_file_location("benchmark_checkpointed_speech", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - broken test installation
    message = "could not load checkpointed speech benchmark driver"
    raise RuntimeError(message)
driver = cast("Any", importlib.util.module_from_spec(SPEC))
SPEC.loader.exec_module(driver)


@workflow.defn(sandboxed=False)
class WaitingBenchmarkWorkflow:
    """A run that terminates only when the benchmark driver sends cancellation."""

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: False)


class StreamedResult:
    def __init__(self, body: bytes, *, reported_size: int | None = None) -> None:
        self.body = body
        self.meta = {"size": len(body) if reported_size is None else reported_size}

    async def stream(self, *, min_chunk_size: int) -> AsyncIterator[bytes]:
        assert min_chunk_size > 0
        midpoint = len(self.body) // 2
        for chunk in (self.body[:midpoint], self.body[midpoint:]):
            if chunk:
                yield chunk


def _case(scope: SimpleNamespace) -> SimpleNamespace:
    source_id = UUID("0192e8a0-0000-7000-8000-000000000777")
    return SimpleNamespace(
        source_id=source_id,
        object_prefix=f"org/{scope.organizationId}/source/{source_id}/",
    )


async def test_preserves_all_artifacts_and_transcript_operational_objects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = SimpleNamespace(organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"))
    case = _case(scope)
    artifact_key = case.object_prefix + "harness/speech-assignment/result.json"
    admission_key = case.object_prefix + "transcript/admissions/recognize/attempt.json"
    audio_key = case.object_prefix + "audio/audio.m4a"
    objects: dict[str, bytes] = {
        artifact_key: b'{"assignment":"exact"}',
        admission_key: b'{"claimed":true}',
        audio_key: b"large source audio is intentionally not duplicated",
    }
    artifacts: list[dict[str, object]] = [
        {
            "id": UUID("0192e8a0-0000-7000-8000-000000000778"),
            "storage_key": artifact_key,
            "sha256": hashlib.sha256(objects[artifact_key]).hexdigest(),
            "size_bytes": len(objects[artifact_key]),
        }
    ]

    async def list_objects(*_args: object, **_kwargs: object) -> list[tuple[str, int]]:
        return [(key, len(body)) for key, body in objects.items()]

    async def get_async(_store: object, key: str) -> StreamedResult:
        return StreamedResult(objects[key])

    monkeypatch.setattr(driver, "resolve_scope", lambda: scope)
    monkeypatch.setattr(driver.storage, "list_objects", list_objects)
    monkeypatch.setattr(driver.obs, "get_async", get_async)
    case_dir = tmp_path / "case"

    preserved = await driver._preserve_objects(  # noqa: SLF001
        SimpleNamespace(store=object()), case, case_dir, artifacts
    )

    assert {item["key"] for item in preserved} == {artifact_key, admission_key}
    artifact_record = next(item for item in preserved if item["key"] == artifact_key)
    assert artifact_record["artifactIds"] == [str(artifacts[0]["id"])]
    for record in preserved:
        path = case_dir / record["localPath"]
        assert path.read_bytes() == objects[record["key"]]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_preservation_refuses_out_of_scope_and_growing_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = SimpleNamespace(organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"))
    case = _case(scope)
    key = case.object_prefix + "harness/coverage/result.json"
    accepted = b"accepted"
    artifact: dict[str, object] = {
        "id": UUID("0192e8a0-0000-7000-8000-000000000779"),
        "storage_key": key,
        "sha256": hashlib.sha256(accepted).hexdigest(),
        "size_bytes": len(accepted),
    }

    async def list_growing(*_args: object, **_kwargs: object) -> list[tuple[str, int]]:
        return [(key, len(accepted))]

    async def get_growing(_store: object, _key: str) -> StreamedResult:
        return StreamedResult(accepted + b"-grew", reported_size=len(accepted))

    monkeypatch.setattr(driver, "resolve_scope", lambda: scope)
    monkeypatch.setattr(driver.storage, "list_objects", list_growing)
    monkeypatch.setattr(driver.obs, "get_async", get_growing)
    with pytest.raises(OSError, match="grew"):
        await driver._preserve_objects(  # noqa: SLF001
            SimpleNamespace(store=object()), case, tmp_path / "growing", [artifact]
        )

    escaped: dict[str, object] = dict(artifact, storage_key="org/another/source/object.json")
    with pytest.raises(ValueError, match="escaped"):
        await driver._preserve_objects(  # noqa: SLF001
            SimpleNamespace(store=object()), case, tmp_path / "escaped", [escaped]
        )


async def test_cancel_drain_survives_repeated_parent_cancellation() -> None:
    cancel_started = asyncio.Event()
    release = asyncio.Event()

    class Handle:
        async def cancel(self, *, reason: str = "") -> None:
            assert reason
            cancel_started.set()
            await release.wait()

        async def result(self, *, follow_runs: bool = True) -> object:
            assert follow_runs is False
            return object()

    task = asyncio.create_task(driver._cancel_and_drain(Handle()))  # noqa: SLF001
    await cancel_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    assert await task == "terminal"


async def test_cancel_transport_failure_is_reported_as_outcome_unknown() -> None:
    class Handle:
        async def cancel(self, *, reason: str = "") -> None:
            del reason
            message = "cancel acknowledgement was lost"
            raise OSError(message)

        async def result(self, *, follow_runs: bool = True) -> object:
            raise AssertionError(follow_runs)

    assert (
        await driver._cancel_and_drain(Handle(), timeout_seconds=0.1)  # noqa: SLF001
        == "outcome_unknown"
    )


async def test_real_temporal_run_is_cancelled_before_worker_exit() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        queue = f"benchmark-cancel-{uuid4()}"
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[WaitingBenchmarkWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await environment.client.start_workflow(
                WaitingBenchmarkWorkflow.run,
                id=f"benchmark-cancel-{uuid4()}",
                task_queue=queue,
            )
            assert await driver._cancel_and_drain(handle) == "terminal"  # noqa: SLF001
            with pytest.raises(WorkflowFailureError):
                await handle.result(follow_runs=False)


async def test_benchmark_plan_disables_oom_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Plan:
        protocol = "temnia-speech/2"
        allow_oom_recovery = True

        def model_copy(self, *, update: dict[str, object]) -> Plan:
            copied = Plan()
            copied.allow_oom_recovery = cast("bool", update["allow_oom_recovery"])
            return copied

    async def choose(_self: object, _request: object) -> Plan:
        return Plan()

    monkeypatch.setattr(driver.SpeechActivitiesV2, "choose_transcription_plan", choose)
    activities = driver.V2QualificationSpeechActivities(SimpleNamespace(), inject_lost_result=False)

    plan = await activities.choose_transcription_plan(cast("Any", object()))

    assert plan.allow_oom_recovery is False


async def test_cache_only_client_refuses_the_provider_spawn_boundary() -> None:
    client = driver.RecoveryNoSpawnSpeechClient(SimpleNamespace())

    with pytest.raises(driver.BenchmarkExecutionError, match="spawn boundary"):
        await client.spawn(object(), object())


def test_cost_facts_exclude_incomplete_reused_checkpoint_telemetry() -> None:
    profile = SimpleNamespace()
    benchmark_variant = SimpleNamespace(resource_profile=profile)
    attempts = [
        {
            "id": UUID("0192e8a0-0000-7000-8000-000000000780"),
            "estimated_cost_micros": 10,
            "actual_cost_micros": None,
            "cost_status": "unknown",
            "usage": {
                "telemetry": {
                    "complete": False,
                    "elapsedSeconds": 0.1,
                    "metricsUnavailable": ["checkpoint_reused_without_original_container_metrics"],
                }
            },
        }
    ]

    facts = driver._cost_facts(attempts, benchmark_variant)  # noqa: SLF001

    assert facts[0]["elapsedSeconds"] is None
    assert facts[0]["measuredResourceEstimateMicros"] is None
    assert facts[0]["telemetryComplete"] is False
