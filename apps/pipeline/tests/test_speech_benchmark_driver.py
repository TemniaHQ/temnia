"""Bounded evidence and cancellation behavior for the speech benchmark driver."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Self, cast
from uuid import UUID, uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SCRIPT = Path(__file__).parents[1] / "scripts/benchmark_checkpointed_speech.py"
SPEC = importlib.util.spec_from_file_location("benchmark_checkpointed_speech", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - broken test installation
    message = "could not load checkpointed speech benchmark driver"
    raise RuntimeError(message)
driver = cast("Any", importlib.util.module_from_spec(SPEC))
SPEC.loader.exec_module(driver)


def _manifest() -> Any:  # noqa: ANN401
    model = SpeechModelManifest(
        sha256="a" * 64,
        file_count=1,
        total_bytes=1,
        model_root=f"/models/frozen/{'a' * 64}",
        image_assets_sha256="b" * 64,
    )
    variants: list[Any] = []
    for identity, cpu, progress, topology, rate, app in (
        ("A", 4, "synchronous_control", "serial", 1_250_000, "benchmark-a"),
        ("B", 4, "coalesced", "serial", 1_250_000, "benchmark-b"),
        ("C", 8, "coalesced", "serial", 1_450_000, "benchmark-c"),
        ("D", 8, "coalesced", "parallel", 1_450_000, "benchmark-c"),
    ):
        variants.append(
            driver.BenchmarkVariant(
                id=identity,
                app=app,
                resource_profile=SpeechResourceProfile(
                    cpu_cores=cast("Any", cpu),
                    stage_timeout_seconds=900,
                    progress_mode=cast("Any", progress),
                ),
                execution_topology=cast("Any", topology),
                rate_micros_per_hour=rate,
            )
        )
    return driver.BenchmarkDeploymentManifest(
        source_build_id="c" * 64,
        model_manifest=model,
        variants=tuple(variants),
    )


def _original_snapshot() -> tuple[dict[str, object], Any]:
    variant = _manifest().variants[0]
    operations: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    checkpoint_ids: dict[str, UUID] = {}
    for stage in ("recognize", "align", "speaker_turns"):
        operation_id = uuid4()
        artifact_id = uuid4()
        checkpoint_ids[stage] = artifact_id
        operations.append(
            {
                "id": operation_id,
                "stage": stage,
                "status": "succeeded",
                "result_artifact_id": artifact_id,
            }
        )
        artifacts.append(
            {
                "id": artifact_id,
                "kind": "speech_checkpoint",
                "metadata": {
                    "format": "speech-checkpoint/2",
                    "stage": stage,
                    "operationId": str(operation_id),
                },
            }
        )
    operations.append(
        {
            "id": uuid4(),
            "stage": "assign_speakers",
            "status": "pending",
            "result_artifact_id": None,
        }
    )
    evidence_id = uuid4()
    artifacts.append(
        {
            "id": evidence_id,
            "kind": "speech_checkpoint",
            "metadata": {"format": "speech-evidence/1"},
        }
    )
    attempts = [
        {
            "id": uuid4(),
            "stage": stage,
            "state": "succeeded",
            "dispatched_at": "2026-09-09T00:00:00Z",
            "estimated_cost_micros": variant.reservation_micros,
            "actual_cost_micros": None,
            "cost_status": "unknown",
            "reservation_state": "active",
            "reservation_amount_micros": variant.reservation_micros,
        }
        for stage in ("recognize", "align", "speaker_turns")
    ]
    snapshot: dict[str, object] = {
        "run": {
            "id": uuid4(),
            "status": "failed",
            "budget_micros": variant.case_exposure_micros,
            "dispatch_count": 3,
            "spent_micros": 0,
            "reserved_micros": variant.case_exposure_micros,
            "workflow_run_id": str(uuid4()),
        },
        "transcript": {"status": "failed"},
        "attempts": attempts,
        "operations": operations,
        "artifacts": artifacts,
        "dependencies": [
            {
                "artifact_id": checkpoint_ids["align"],
                "input_artifact_id": checkpoint_ids["recognize"],
            },
            {"artifact_id": evidence_id, "input_artifact_id": uuid4()},
        ],
        "transcriptRevisions": [],
    }
    return snapshot, variant


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


def test_unstarted_acknowledgment_requires_the_resume_flag() -> None:
    with pytest.raises(ValueError, match="requires --resume-failed-case"):
        driver._validate_recovery_arguments(  # noqa: SLF001
            SimpleNamespace(
                acknowledge_unstarted_recovery="a" * 64,
                resume_failed_case=None,
            )
        )


def test_original_gpu_selector_allows_source_evidence_and_unrelated_lineage() -> None:
    snapshot, variant = _original_snapshot()

    facts = driver._original_recovery_facts(  # noqa: SLF001
        snapshot, variant=variant, require_failed_transcript=True
    )

    assert set(facts["checkpointArtifactIdsByStage"]) == {
        "recognize",
        "align",
        "speaker_turns",
    }
    artifacts = cast("list[dict[str, object]]", snapshot["artifacts"])
    checkpoint = next(
        artifact
        for artifact in artifacts
        if cast("dict[str, object]", artifact["metadata"]).get("stage") == "recognize"
    )
    cast("dict[str, object]", checkpoint["metadata"])["operationId"] = str(uuid4())
    with pytest.raises(driver.BenchmarkExecutionError, match="artifact identity"):
        driver._original_recovery_facts(  # noqa: SLF001
            snapshot, variant=variant, require_failed_transcript=True
        )


def test_recovering_journal_allows_only_the_recorded_transform() -> None:
    manifest = _manifest()
    organization_id = uuid4()
    built = driver.build_journal(
        experiment_id="bench-recovery-20260909",
        organization_id=organization_id,
        manifest=manifest,
    )
    first = built.cases[0].model_copy(update={"status": "failed", "error_type": "UndefinedColumn"})
    original = built.model_copy(
        update={
            "initial_worker_source_build_id": None,
            "worker_source_build_id": None,
            "reserved_exposure_micros": first.configured_exposure_micros,
            "reserved_dispatches": 3,
            "cases": (first, *built.cases[1:]),
        }
    )
    current = original.model_copy(
        update={
            "initial_worker_source_build_id": manifest.source_build_id,
            "worker_source_build_id": "d" * 64,
            "cases": (
                first.model_copy(update={"status": "recovering"}),
                *original.cases[1:],
            ),
        }
    )

    driver._validate_permitted_recovering_journal(  # noqa: SLF001
        original=original,
        current=current,
        worker_source_build_id="d" * 64,
        deployment_source_build_id=manifest.source_build_id,
    )
    changed = current.model_copy(update={"reserved_dispatches": 4})
    with pytest.raises(ValueError, match="unreviewed mutation"):
        driver._validate_permitted_recovering_journal(  # noqa: SLF001
            original=original,
            current=changed,
            worker_source_build_id="d" * 64,
            deployment_source_build_id=manifest.source_build_id,
        )


async def test_unstarted_acknowledgment_is_immutable_and_refuses_ledger_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot, variant = _original_snapshot()
    manifest = _manifest()
    organization_id = uuid4()
    case = driver.build_journal(
        experiment_id="bench-recovery-20260909",
        organization_id=organization_id,
        manifest=manifest,
    ).cases[0]
    facts = driver._original_recovery_facts(  # noqa: SLF001
        snapshot, variant=variant, require_failed_transcript=True
    )

    async def snapshot_result(*_args: object, **_kwargs: object) -> dict[str, object]:
        return snapshot

    async def one_run(*_args: object, **_kwargs: object) -> None:
        return None

    async def connect(*_args: object, **_kwargs: object) -> object:
        return object()

    async def latest(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "workflowId": case.workflow_id,
            "runId": facts["workflowRunId"],
            "status": "FAILED",
        }

    monkeypatch.setattr(driver, "_database_snapshot", snapshot_result)
    monkeypatch.setattr(driver, "_assert_one_original_database_run", one_run)
    monkeypatch.setattr(driver.Client, "connect", connect)
    monkeypatch.setattr(driver, "_assert_latest_original_failed", latest)
    monkeypatch.setattr(driver, "REVIEWED_ORIGINAL_LEDGER_SHA256", facts["immutableLedgerSha256"])
    kwargs = {
        "base": SimpleNamespace(),
        "temporal": SimpleNamespace(address="temporal", namespace="benchmark"),
        "case": case,
        "variant": variant,
        "output_dir": tmp_path,
        "current_journal_sha256": "c" * 64,
        "original_journal_sha256": "o" * 64,
        "worker_source_build_id": "w" * 64,
    }
    await driver._acknowledge_unstarted_recovery(**kwargs)  # noqa: SLF001
    receipt = tmp_path / "cases" / case.key / "recovery" / "unstarted-recovery-acknowledgment.json"
    original_receipt = receipt.read_bytes()
    await driver._acknowledge_unstarted_recovery(**kwargs)  # noqa: SLF001
    assert receipt.read_bytes() == original_receipt

    cast("dict[str, object]", snapshot["run"])["reserved_micros"] = 1
    with pytest.raises(driver.BenchmarkExecutionError, match="failed admission"):
        await driver._acknowledge_unstarted_recovery(**kwargs)  # noqa: SLF001


async def test_recovery_start_failure_preserves_intent_and_total_failure_receipt(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot, variant = _original_snapshot()
    manifest = _manifest()
    scope = driver.resolve_scope()
    case = driver.build_journal(
        experiment_id="bench-recovery-20260909",
        organization_id=scope.organizationId,
        manifest=manifest,
    ).cases[0]
    facts = driver._original_recovery_facts(  # noqa: SLF001
        snapshot, variant=variant, require_failed_transcript=True
    )

    class ClientStub:
        async def start_workflow(self, *_args: object, **_kwargs: object) -> object:
            message = "start acceptance was not observed"
            raise OSError(message)

    class ActivitiesStub:
        def __init__(self) -> None:
            self.observed_run_ids: list[object] = []

        def activities(self) -> list[object]:
            return []

    class WorkerStub:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def snapshot_result(*_args: object, **_kwargs: object) -> dict[str, object]:
        return snapshot

    async def preserve(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    async def deployment(*_args: object, **_kwargs: object) -> None:
        return None

    async def connect(*_args: object, **_kwargs: object) -> ClientStub:
        return ClientStub()

    async def latest(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "workflowId": case.workflow_id,
            "runId": facts["workflowRunId"],
            "status": "FAILED",
        }

    def context_factory(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(settings=SimpleNamespace(transcription=object()))

    def speech_client_factory(_settings: object) -> object:
        return object()

    def transcribe_factory(_ctx: object) -> ActivitiesStub:
        return ActivitiesStub()

    def qualifier_factory(*_args: object, **_kwargs: object) -> ActivitiesStub:
        return ActivitiesStub()

    monkeypatch.setattr(driver, "_database_snapshot", snapshot_result)
    monkeypatch.setattr(driver, "_preserve_objects", preserve)
    monkeypatch.setattr(driver, "_variant_context", context_factory)
    monkeypatch.setattr(driver, "assert_checkpointed_deployment", deployment)
    monkeypatch.setattr(driver, "SpeechModalClient", speech_client_factory)
    monkeypatch.setattr(driver.Client, "connect", connect)
    monkeypatch.setattr(driver, "Transcribe", transcribe_factory)
    monkeypatch.setattr(driver, "V2QualificationSpeechActivities", qualifier_factory)
    monkeypatch.setattr(driver, "Worker", WorkerStub)
    monkeypatch.setattr(driver, "_assert_latest_original_failed", latest)

    with pytest.raises(driver.BenchmarkExecutionError, match="did not finish ready"):
        await driver._recover_failed_case(  # noqa: SLF001
            base=SimpleNamespace(),
            temporal=SimpleNamespace(address="temporal", namespace="benchmark"),
            manifest=manifest,
            variant=variant,
            case=case,
            source=SimpleNamespace(duration_ms=1_000),
            output_dir=tmp_path,
            task_queue_prefix="benchmark",
            worker_source_build_id="w" * 64,
            current_journal_sha256="c" * 64,
            original_journal_sha256="o" * 64,
            expected_original_ledger_sha256=cast("str", facts["immutableLedgerSha256"]),
        )

    recovery_dir = tmp_path / "cases" / case.key / "recovery"
    intent = json.loads((recovery_dir / "recovery-start-intent.json").read_bytes())
    attempt = json.loads((recovery_dir / "recovery-attempt.json").read_bytes())
    assert intent["originalImmutableLedgerSha256"] == facts["immutableLedgerSha256"]
    assert intent["currentJournalSha256"] == "c" * 64
    assert attempt["errorType"] == "OSError"
    assert attempt["recoveryRunId"] is None
    assert attempt["recovery"]["run"] is None
    assert attempt["noAdditionalExposure"] is False
    assert attempt["originalImmutableLedgerSha256"] == facts["immutableLedgerSha256"]
    for path in (
        recovery_dir / "recovery-start-intent.json",
        recovery_dir / "recovery-attempt.json",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    async def one_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(driver, "_assert_one_original_database_run", one_run)
    monkeypatch.setattr(driver, "REVIEWED_ORIGINAL_LEDGER_SHA256", facts["immutableLedgerSha256"])
    with pytest.raises(driver.BenchmarkExecutionError, match="already has an execution receipt"):
        await driver._acknowledge_unstarted_recovery(  # noqa: SLF001
            base=SimpleNamespace(),
            temporal=SimpleNamespace(address="temporal", namespace="benchmark"),
            case=case,
            variant=variant,
            output_dir=tmp_path,
            current_journal_sha256="c" * 64,
            original_journal_sha256="o" * 64,
            worker_source_build_id="w" * 64,
        )


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
