"""Controlled launch identity, readiness and uncertain-start tests without paid inference."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import ValidationError

from qualification_fixtures import _qualified  # pyright: ignore[reportPrivateUsage]
from temnia_pipeline.contracts import Backend, ChapterRunConfig, HarnessRunStatus
from temnia_pipeline.evals.topics import digest
from temnia_pipeline.harness import topic_experiment as experiment
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.harness.routes import load_route_snapshot
from temnia_pipeline.harness.runtime_types import (
    PinnedSource,
    PinnedTranscript,
    RunSnapshot,
    StartRunRequest,
    StartRunResult,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_editorial import editorial_routes
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import TemporalSettings

if TYPE_CHECKING:
    from temnia_pipeline.contracts import Scope
    from temnia_pipeline.evals.topics import TopicProgramManifest
    from temnia_pipeline.harness.routes import RouteSnapshot

SOURCE_ID = UUID(int=111)
TRANSCRIPT_ID = UUID(int=112)
FIXTURES = Path(__file__).parent / "fixtures" / "harness"


def source_case() -> experiment.SourceCase:
    return experiment.SourceCase(
        id="discussion",
        source_id=SOURCE_ID,
        source_group="synthetic-complete-recording",
        split="qualification",
        prior_exposure="Invented transcript used only for operational tests.",
    )


def frozen_source(case: experiment.SourceCase | None = None) -> experiment.FrozenSource:
    scope = resolve_scope()
    return experiment.FrozenSource(
        case=case or source_case(),
        source=PinnedSource(
            storage_key=f"org/{scope.organizationId}/source/{SOURCE_ID}/master.mp4",
            duration_ms=4000,
            size_bytes=100,
        ),
        transcript=PinnedTranscript(
            transcript_id=TRANSCRIPT_ID,
            revision=1,
            sha256="b" * 64,
            storage_key=f"org/{scope.organizationId}/source/{SOURCE_ID}/transcript/1.json",
            size_bytes=50,
        ),
    )


def spec(tmp_path: Path) -> experiment.ExperimentSpec:
    route_path = tmp_path / "routes.json"
    route_path.write_bytes((FIXTURES / "routes.synthetic.json").read_bytes())
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_bytes((FIXTURES / "topic.synthetic.json").read_bytes())
    snapshot = load_route_snapshot(route_path)
    author, reviewer = editorial_routes(snapshot)
    arm = experiment.ArmSpec(
        id="control",
        config=ChapterRunConfig(
            backend=Backend.recorded,
            routeSnapshotId=snapshot.snapshot_id,
            maxDispatches=32,
            maxOutputTokens=8192,
            maxRepairs=1,
            maxRenderConcurrency=2,
            evidenceWindowSentences=80,
        ),
        route_snapshot_path=str(route_path),
        recorded_fixture_path=str(fixture_path),
        author_route_id=author.id,
        reviewer_route_id=reviewer.id,
    )
    return experiment.ExperimentSpec(
        name="synthetic-test",
        budget_micros=1_000_000,
        worker_max_run_budget_micros=10_000_000,
        temporal_address="localhost:56233",
        temporal_namespace="isolated-test-namespace",
        arms=(arm,),
        sources=(source_case(),),
    )


async def prepare(
    value: experiment.ExperimentSpec, monkeypatch: pytest.MonkeyPatch
) -> experiment.PreparedExperiment:
    async def observe(
        _url: str, case: experiment.SourceCase, _scope: Scope
    ) -> experiment.FrozenSource:
        return frozen_source(case)

    monkeypatch.setattr(experiment, "observe_source", observe)
    return await experiment.prepare_experiment(
        value, database_url="unused", environment={"AI_GATEWAY_API_KEY": "unit-test-only"}
    )


def runtime(
    prepared: experiment.PreparedExperiment,
) -> tuple[HarnessSettings, TemporalSettings]:
    arm = prepared.arms[0]
    settings = HarnessSettings.from_env(
        {
            "AI_GATEWAY_API_KEY": "unit-test-only",
            **experiment.worker_environment(prepared.spec, arm),
        }
    )
    return settings, TemporalSettings(
        prepared.spec.temporal_address, prepared.spec.temporal_namespace, arm.pipeline_queue
    )


class FakeRuns:
    def __init__(self, prepared: experiment.PreparedExperiment) -> None:
        self.prepared = prepared
        self.value: RunSnapshot | None = None
        self.pins = (prepared.sources[0].source, prepared.sources[0].transcript)
        self.creates = 0
        self.change_at_create = False

    async def find(self, _url: str, **_kwargs: object) -> RunSnapshot | None:
        return self.value

    async def ready(self, _url: str, **_kwargs: object) -> tuple[PinnedSource, PinnedTranscript]:
        return self.pins

    async def start(
        self,
        _url: str,
        *,
        start: StartRunRequest,
        settings: HarnessSettings,
        route_snapshot: RouteSnapshot,
    ) -> StartRunResult:
        self.creates += 1
        assert self.value is None, "launcher must read active runs, never try to reclaim them"
        transcript = self.pins[1]
        if self.change_at_create:
            transcript = transcript.model_copy(update={"revision": 2})
        self.value = RunSnapshot(
            id=start.request.runId,
            source_id=start.request.sourceId,
            request_key=start.request.requestKey,
            brief=start.request.brief or "",
            config=start.request.config,
            initial_budget_micros=start.request.budgetMicros,
            budget_micros=start.request.budgetMicros,
            status=HarnessRunStatus.pending,
            stage="evidence",
            current_revision=0,
            accepted_revision=None,
            dispatch_count=0,
            repair_count=0,
            spent_micros=0,
            reserved_micros=0,
            error_message=None,
            evidence_artifact_id=None,
            route_snapshot=route_snapshot,
            source=self.pins[0],
            transcript=transcript,
            workflow_id=start.workflow.workflow_id,
            workflow_run_id=start.workflow.workflow_run_id,
            editorial_policy=start.editorial_policy,
            topic_shot_detector=settings.topic_shot_detector,
            evaluation_program=start.evaluation_program,
            evaluation_program_sha256=digest(start.evaluation_program),
        )
        return StartRunResult(run=self.value, created=True)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(experiment.runs, "find_run", self.find)
        monkeypatch.setattr(experiment.runs, "ready_source_pins", self.ready)
        monkeypatch.setattr(experiment.runs, "start_or_refetch_run", self.start)


class FakeDriver:
    def __init__(self) -> None:
        self.observed: experiment.WorkflowObservation | None = None
        self.starts: list[experiment.PreparedExecution] = []
        self.reads = 0
        self.uncertain_after_start = False
        self.accept_start = True
        self.read_unavailable = False

    async def describe(self, workflow_id: str) -> experiment.WorkflowObservation | None:
        self.reads += 1
        if self.read_unavailable:
            raise TimeoutError
        if self.observed is not None:
            assert self.observed.workflow_id == workflow_id
        return self.observed

    async def start(
        self, execution: experiment.PreparedExecution, queue: str, experiment_sha256: str
    ) -> None:
        self.starts.append(execution)
        if self.accept_start:
            self.observed = experiment.WorkflowObservation(
                workflow_id=execution.workflow_id,
                run_id="actual-temporal-run",
                workflow_type=execution.workflow_type,
                task_queue=queue,
                status="RUNNING",
                experiment_sha256=experiment_sha256,
                intent_sha256=digest(execution.request.model_dump(mode="json")),
            )
        if self.uncertain_after_start:
            raise TimeoutError


async def launch(
    prepared: experiment.PreparedExperiment, driver: FakeDriver
) -> experiment.ExecutionStatus:
    settings, temporal = runtime(prepared)
    return await experiment.run_execution(
        prepared,
        prepared.executions[0],
        database_url="unused",
        settings=settings,
        temporal=temporal,
        driver=driver,
    )


async def test_private_create_only_manifest_freezes_matrix_and_complete_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = spec(tmp_path)
    value = value.model_copy(
        update={"arms": (*value.arms, value.arms[0].model_copy(update={"id": "challenger"}))}
    )
    prepared = await prepare(value, monkeypatch)
    path = tmp_path / "prepared.json"
    experiment.write_prepared(path, prepared)
    restored = experiment.read_prepared(path)
    assert restored == prepared
    assert path.stat().st_mode & 0o777 == 0o600
    assert len({item.workflow_id for item in restored.executions}) == 2
    assert len({item.request.runId for item in restored.executions}) == 2
    assert len({item.request.requestKey for item in restored.executions}) == 2
    assert len({arm.pipeline_queue for arm in restored.arms}) == 2
    assert restored.sources[0].known_source_sha256 is None
    assert set(restored.program.stages) == {
        "topic_inventory",
        "topic_author",
        "topic_cold",
        "topic_source",
        "topic_patch",
    }
    assert restored.program.stages["topic_patch"].seat == "author"
    assert restored.program.stages["topic_source"].seat == "reviewer"
    with pytest.raises(FileExistsError):
        experiment.write_prepared(path, prepared)
    assert experiment.read_prepared(path) == prepared
    body = json.loads(path.read_bytes())
    body["experiment"]["executions"][0]["workflowId"] = "different"
    path.write_text(json.dumps(body))
    with pytest.raises(IdentityConflict, match="checksum differs"):
        experiment.read_prepared(path)


async def test_v3_experiment_freezes_inventory_workflow_and_worker_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(
        spec(tmp_path).model_copy(update={"program_version": "standalone-topics/3"}),
        monkeypatch,
    )
    assert prepared.program.policy == "standalone-topics/3"
    assert "topic_inventory" in prepared.program.stages
    assert prepared.executions[0].workflow_type == "TopicSelectionWorkflow"
    settings, _ = runtime(prepared)
    assert settings.enabled is True


@pytest.mark.parametrize("field", ["arms", "sources"])
@pytest.mark.parametrize("operation", ["empty", "duplicate"])
def test_empty_or_duplicate_matrix_refuses(tmp_path: Path, field: str, operation: str) -> None:
    body = spec(tmp_path).model_dump(mode="json", by_alias=True)
    body[field] = [] if operation == "empty" else [*body[field], body[field][0]]
    with pytest.raises(ValidationError):
        experiment.ExperimentSpec.model_validate_json(json.dumps(body))


async def test_unknown_accepted_start_reentry_uses_same_run_without_reclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    driver.uncertain_after_start = True
    first = await launch(prepared, driver)
    assert first.launch_state == "started"
    assert database.value is not None
    database.value = database.value.model_copy(
        update={"status": HarnessRunStatus.running, "workflow_run_id": "actual-temporal-run"}
    )
    again = await launch(prepared, driver)
    assert again.run_id == first.run_id
    assert again.run_status == "running"
    assert database.creates == len(driver.starts) == 1


async def test_unknown_unaccepted_start_retains_exact_intent_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    driver.accept_start = False
    driver.uncertain_after_start = True
    result = await launch(prepared, driver)
    assert result.launch_state == "start_unknown"
    assert database.creates == 1
    driver.accept_start = True
    again = await launch(prepared, driver)
    assert again.launch_state == "started"
    assert database.creates == 1
    assert driver.starts == [prepared.executions[0], prepared.executions[0]]


async def test_unknown_status_never_authorizes_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    driver.read_unavailable = True
    result = await launch(prepared, driver)
    assert result.launch_state == "start_unknown"
    assert driver.starts == []


@pytest.mark.parametrize("when", ["before_create", "during_create"])
async def test_changed_source_pin_refuses_before_workflow_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    if when == "before_create":
        database.pins = (database.pins[0], database.pins[1].model_copy(update={"revision": 2}))
    else:
        database.change_at_create = True
    driver = FakeDriver()
    with pytest.raises(IdentityConflict, match=r"source changed|pins differ"):
        await launch(prepared, driver)
    assert driver.starts == []
    assert database.creates == (0 if when == "before_create" else 1)


@pytest.mark.parametrize("change", ["queue", "output", "detector", "programme", "file", "route"])
async def test_incompatible_runtime_refuses_before_precreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    settings, temporal = runtime(prepared)
    if change == "queue":
        temporal = replace(temporal, task_queue="temnia-pipeline")
    elif change == "output":
        settings = replace(settings, max_output_tokens=256)
    elif change == "detector":
        settings = replace(settings, topic_shot_detector="pyscenedetect-adaptive")
    elif change == "programme":

        def stale_program(_version: str) -> TopicProgramManifest:
            return prepared.program.model_copy(update={"implementation_sha256": "f" * 64})

        monkeypatch.setattr(
            experiment,
            "current_program",
            stale_program,
        )
    elif change == "file":
        path = Path(prepared.arms[0].snapshot_file.path)
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        settings = replace(settings, route_snapshot_id="f" * 64)
    driver = FakeDriver()
    with pytest.raises(IdentityConflict):
        await experiment.run_execution(
            prepared,
            prepared.executions[0],
            database_url="unused",
            settings=settings,
            temporal=temporal,
            driver=driver,
        )
    assert database.creates == 0
    assert driver.starts == []


async def test_status_is_read_only_and_does_not_require_current_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    monkeypatch.setattr(experiment, "current_program", lambda: None)
    result = await experiment.read_status(
        prepared, prepared.executions[0], database_url="unused", driver=driver
    )
    assert result.launch_state == "prepared"
    assert database.creates == 0
    assert driver.starts == []


async def test_claimed_run_missing_from_temporal_is_fenced_without_restarting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    await launch(prepared, driver)
    assert database.value is not None
    database.value = database.value.model_copy(
        update={"status": HarnessRunStatus.pending, "workflow_run_id": "prior-actual-run"}
    )
    driver.observed = None
    result = await launch(prepared, driver)
    assert result.launch_state == "fenced"
    assert len(driver.starts) == 1


async def test_existing_workflow_wrong_memo_or_queue_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await prepare(spec(tmp_path), monkeypatch)
    database = FakeRuns(prepared)
    database.install(monkeypatch)
    driver = FakeDriver()
    await launch(prepared, driver)
    assert driver.observed is not None
    driver.observed = driver.observed.model_copy(update={"experiment_sha256": "f" * 64})
    with pytest.raises(IdentityConflict, match="Temporal execution"):
        await launch(prepared, driver)
    assert len(driver.starts) == 1


async def test_gateway_arm_requires_resolved_seats_and_exact_output_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This existing qualifier helper uses mocked HTTP and invented responses, never paid inference.
    snapshot, _, requests = await _qualified(tmp_path)
    value = spec(tmp_path)
    route_path = tmp_path / "production-routes.json"
    route_path.write_text(snapshot.model_dump_json())
    author, reviewer = editorial_routes(snapshot)
    arm = value.arms[0].model_copy(
        update={
            "config": value.arms[0].config.model_copy(
                update={
                    "backend": Backend.gateway,
                    "maxOutputTokens": 256,
                    "routeSnapshotId": snapshot.snapshot_id,
                }
            ),
            "route_snapshot_path": str(route_path),
            "recorded_fixture_path": None,
            "author_route_id": author.id,
            "reviewer_route_id": reviewer.id,
        }
    )
    value = value.model_copy(update={"arms": (arm,)})
    prepared = await prepare(value, monkeypatch)
    settings, temporal = runtime(prepared)
    experiment.assert_runtime(prepared, prepared.arms[0], settings, temporal)
    assert len(requests) == 15
    wrong_seat = value.model_copy(
        update={"arms": (arm.model_copy(update={"author_route_id": reviewer.id}),)}
    )
    with pytest.raises(IdentityConflict, match="resolved author/reviewer"):
        await prepare(wrong_seat, monkeypatch)
    wrong_snapshot = value.model_copy(
        update={
            "arms": (
                arm.model_copy(
                    update={"config": arm.config.model_copy(update={"routeSnapshotId": "0" * 64})}
                ),
            )
        }
    )
    with pytest.raises(RuntimeError, match="route snapshot hash differs"):
        await prepare(wrong_snapshot, monkeypatch)


@pytest.mark.parametrize("changed", ["source_bytes", "source_fingerprint", "evidence"])
def test_late_observed_evidence_mismatch_is_incomparable_and_retains_costs(changed: str) -> None:
    source = frozen_source(
        source_case().model_copy(
            update={
                "expected_source_sha256": "a" * 64,
                "expected_source_fingerprint": "c" * 64,
                "expected_evidence_sha256": "d" * 64,
            }
        )
    )
    status = experiment.ExecutionStatus(
        arm_id="control",
        case_id="discussion",
        run_id=UUID(int=113),
        workflow_id="prepared-workflow",
        launch_state="started",
        run_status="failed",
        spent_micros=4321,
        reserved_micros=987,
    )
    actual = {
        "source_sha256": "a" * 64,
        "source_fingerprint": "c" * 64,
        "evidence_sha256": "d" * 64,
    }
    actual[
        {
            "source_bytes": "source_sha256",
            "source_fingerprint": "source_fingerprint",
            "evidence": "evidence_sha256",
        }[changed]
    ] = "f" * 64
    compared = experiment.compare_evidence(source, status, **actual)
    assert compared.source_identity == "mismatch"
    assert compared.spent_micros == status.spent_micros
    assert compared.reserved_micros == status.reserved_micros
    assert compared.run_status == "failed"
    assert "incomparable" in compared.reasons[0]
    assert source.case.expected_source_sha256 == "a" * 64


def test_unknown_source_hash_is_not_retroactively_claimed_as_frozen() -> None:
    status = experiment.ExecutionStatus(
        arm_id="control",
        case_id="discussion",
        run_id=UUID(int=113),
        workflow_id="prepared-workflow",
        launch_state="started",
    )
    observed = experiment.compare_evidence(
        frozen_source(),
        status,
        source_sha256="a" * 64,
        source_fingerprint="c" * 64,
        evidence_sha256="d" * 64,
    )
    assert observed.source_identity == "unknown"
    assert observed.observed_source_sha256 == "a" * 64
