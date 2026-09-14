"""Prepare and launch controlled production topic workflows without another paid runtime.

The private manifest is operator intent. Database pins, Temporal history and the existing
harness ledger remain authoritative for execution, expenses and unknown outcomes.
"""

# Explicit refusal messages belong beside the immutable identity they protect.
# ruff: noqa: EM101, TRY003, TC003
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self, cast
from uuid import UUID, uuid4, uuid5

from pydantic import Field, model_validator
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    Backend,
    ChapterRunConfig,
    ChapterRunInput,
    Scope,
    TopicEditorialRubric,
)
from temnia_pipeline.evals.common import SHA256, EvaluationModel
from temnia_pipeline.evals.topics import TopicProgramManifest, digest
from temnia_pipeline.harness import runs, topic_selection
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.harness.queues import control_task_queue
from temnia_pipeline.harness.routes import RouteSnapshot, load_route_snapshot, snapshot_gateway
from temnia_pipeline.harness.runtime_types import (
    PinnedSource,
    PinnedTranscript,
    RunSnapshot,
    StartRunRequest,
    WorkflowIdentity,
)
from temnia_pipeline.harness.settings import HarnessSettings, TopicShotDetector
from temnia_pipeline.harness.topic_editorial import EDITORIAL_BRIEF, editorial_routes
from temnia_pipeline.harness.topic_program import current_program
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import TemporalSettings

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Nonempty = Annotated[str, Field(min_length=1)]
WORKFLOW_TYPES = {
    "standalone-topics/3": "TopicSelectionWorkflow",
    "standalone-topics/4": "TopicSelectionWorkflowV4",
    "standalone-topics/5": "TopicSelectionWorkflowV5",
    "standalone-topics/6": "TopicSelectionWorkflowV6",
    "standalone-topics/7": "TopicSelectionWorkflowV7",
}
WORKFLOW_TYPE = WORKFLOW_TYPES["standalone-topics/3"]
MEMO_KEY = "temniaExperimentSha256"
INTENT_MEMO_KEY = "temniaIntentSha256"
RPC_TIMEOUT = timedelta(seconds=30)


class FilePin(EvaluationModel):
    """Private local bytes required by the unchanged worker's boot configuration."""

    path: Nonempty
    sha256: SHA256

    @classmethod
    def read(cls, path: str) -> FilePin:
        """Freeze an absolute reference and its bytes; never copy credentials."""
        resolved = Path(path).expanduser().resolve(strict=True)
        return cls(path=str(resolved), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())

    def verify(self) -> None:
        """Refuse a changed proof or route file before workflow dispatch."""
        if FilePin.read(self.path) != self:
            raise IdentityConflict("a frozen experiment file changed")


class SourceCase(EvaluationModel):
    """One complete source, with expected evidence kept separate from observed evidence."""

    id: Identifier
    source_id: UUID
    source_group: Nonempty
    split: Literal["development", "held_out", "qualification"]
    prior_exposure: Nonempty
    expected_source_sha256: SHA256 | None = None
    expected_source_fingerprint: SHA256 | None = None
    expected_evidence_sha256: SHA256 | None = None


class ArmSpec(EvaluationModel):
    """An explicitly selected author/repair and cold/source reviewer configuration."""

    id: Identifier
    config: ChapterRunConfig
    route_snapshot_path: Nonempty
    recorded_fixture_path: str | None = None
    author_route_id: Nonempty
    reviewer_route_id: Nonempty


class ExperimentSpec(EvaluationModel):
    """Operator input; no scope or credential can be supplied through this file."""

    format: Literal["temnia-topic-experiment-spec/1"] = "temnia-topic-experiment-spec/1"
    name: Identifier
    brief: Annotated[str, Field(max_length=100000)] = EDITORIAL_BRIEF
    budget_micros: Annotated[int, Field(gt=0, le=9007199254740991)]
    worker_max_run_budget_micros: Annotated[int, Field(gt=0)]
    temporal_address: Nonempty
    temporal_namespace: Nonempty
    topic_shot_detector: TopicShotDetector = "scdet"
    program_version: Literal[
        "standalone-topics/3",
        "standalone-topics/4",
        "standalone-topics/5",
        "standalone-topics/6",
        "standalone-topics/7",
    ] = "standalone-topics/3"
    sources: Annotated[tuple[SourceCase, ...], Field(min_length=1)]
    arms: Annotated[tuple[ArmSpec, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        """Reject ambiguous factor labels and repeated source denominators."""
        if len({arm.id for arm in self.arms}) != len(self.arms):
            raise ValueError("experiment arm IDs must be unique")
        if len({case.id for case in self.sources}) != len(self.sources):
            raise ValueError("experiment source case IDs must be unique")
        if len({case.source_id for case in self.sources}) != len(self.sources):
            raise ValueError("one source cannot appear as multiple experiment cases")
        if self.budget_micros > self.worker_max_run_budget_micros:
            raise ValueError("experiment budget exceeds its declared worker maximum")
        return self


class FrozenSource(EvaluationModel):
    """Ready database pins and prior verified source facts, with no guessed hashes."""

    case: SourceCase
    source: PinnedSource
    transcript: PinnedTranscript
    known_source_sha256: SHA256 | None = None
    known_source_fingerprint: SHA256 | None = None


class FrozenArm(EvaluationModel):
    """An arm has its own unguessable queue and exact immutable file/configuration pins."""

    spec: ArmSpec
    snapshot: RouteSnapshot
    snapshot_file: FilePin
    fixture_file: FilePin | None
    pipeline_queue: Nonempty
    control_queue: Nonempty


class PreparedExecution(EvaluationModel):
    """One immutable intent is reused through every uncertain start and re-entry."""

    arm_id: Identifier
    case_id: Identifier
    workflow_id: Nonempty
    workflow_type: Nonempty = WORKFLOW_TYPE
    request: ChapterRunInput

    @property
    def preparation_identity(self) -> WorkflowIdentity:
        """Leave a new row pending until the real workflow execution claims it."""
        return WorkflowIdentity(
            workflow_id=self.workflow_id,
            workflow_run_id=f"prepared:{self.request.requestKey}",
        )


class PreparedExperiment(EvaluationModel):
    """A private frozen experiment, not a claim that any workflow has started."""

    format: Literal["temnia-topic-experiment/1"] = "temnia-topic-experiment/1"
    id: UUID
    prepared_at: datetime
    spec: ExperimentSpec
    scope: Scope
    rubric: TopicEditorialRubric
    program: TopicProgramManifest
    sources: tuple[FrozenSource, ...]
    arms: tuple[FrozenArm, ...]
    executions: tuple[PreparedExecution, ...]

    @property
    def sha256(self) -> str:
        """Identity persisted in each Temporal execution memo."""
        return digest(self.model_dump(mode="json", by_alias=True))

    def execution(self, arm_id: str, case_id: str) -> PreparedExecution:
        """Resolve only a declared prepared case; never invent a new run on re-entry."""
        for execution in self.executions:
            if execution.arm_id == arm_id and execution.case_id == case_id:
                return execution
        raise ValueError("unknown experiment arm/source case")

    @model_validator(mode="after")
    def consistent(self) -> Self:
        """Validate the entire matrix when reading, including an edited manifest file."""
        if tuple(arm.spec for arm in self.arms) != self.spec.arms:
            raise ValueError("prepared arms differ from experiment intent")
        if tuple(source.case for source in self.sources) != self.spec.sources:
            raise ValueError("prepared sources differ from experiment intent")
        if self.rubric.originalInstructions != self.spec.brief:
            raise ValueError("prepared rubric differs from the frozen brief")
        if self.program.policy != self.spec.program_version:
            raise ValueError("experiment programme differs from its frozen generation")
        expected_executions = tuple(
            make_execution(self.id, arm, source, self.spec, self.scope)
            for arm in self.arms
            for source in self.sources
        )
        if self.executions != expected_executions:
            raise ValueError("prepared execution identities differ from the frozen matrix")
        for arm in self.arms:
            if arm.pipeline_queue != arm_queue(self.id, arm.spec.id):
                raise ValueError("experiment queue is not isolated to its frozen arm")
            if arm.control_queue != control_task_queue(arm.pipeline_queue):
                raise ValueError("experiment control queue differs from its pipeline queue")
            if arm.snapshot.snapshot_id != arm.spec.config.routeSnapshotId:
                raise ValueError("arm configuration differs from its frozen snapshot")
            assert_routes(arm.snapshot, arm.spec)
        return self


def arm_queue(experiment_id: UUID, arm_id: str) -> str:
    """Different experiments and different arm settings cannot accidentally share queues."""
    return f"temnia-topic-experiment-{experiment_id}-{arm_id}"


def assert_routes(snapshot: RouteSnapshot, spec: ArmSpec) -> None:
    """Resolve the same family exclusion and ordering used by the production workflow."""
    author, reviewer = editorial_routes(snapshot)
    if (author.id, reviewer.id) != (spec.author_route_id, spec.reviewer_route_id):
        raise IdentityConflict("resolved author/reviewer routes differ from the declared arm")


def make_execution(
    experiment_id: UUID,
    arm: FrozenArm,
    source: FrozenSource,
    spec: ExperimentSpec,
    scope: Scope,
) -> PreparedExecution:
    """Derive stable distinct identities once from the frozen experiment and case labels."""
    key = f"{arm.spec.id}/{source.case.id}"
    run_id = uuid5(experiment_id, f"run/{key}")
    return PreparedExecution(
        arm_id=arm.spec.id,
        case_id=source.case.id,
        workflow_id=f"topic-experiment/{uuid5(experiment_id, f'workflow/{key}')}",
        workflow_type=WORKFLOW_TYPES[spec.program_version],
        request=ChapterRunInput(
            brief=spec.brief,
            budgetMicros=spec.budget_micros,
            config=arm.spec.config,
            requestKey=uuid5(experiment_id, f"request/{key}"),
            runId=run_id,
            sourceId=source.case.source_id,
            scope=scope,
        ),
    )


def worker_environment(spec: ExperimentSpec, arm: FrozenArm) -> dict[str, str]:
    """Return non-secret exact worker overrides; operator retains existing infrastructure env."""
    config = arm.spec.config
    return {
        # An arm's exact snapshot and limits, never the image's committed deployment file.
        "HARNESS_CONFIG_PATH": "",
        "HARNESS_ENABLED": "1",
        "HARNESS_BACKEND": config.backend.value,
        "HARNESS_GATEWAY": snapshot_gateway(arm.snapshot),
        "HARNESS_ALLOW_RECORDED": "1" if config.backend == Backend.recorded else "0",
        "HARNESS_ROUTE_SNAPSHOT_ID": config.routeSnapshotId,
        "HARNESS_ROUTE_SNAPSHOT_PATH": arm.snapshot_file.path,
        "HARNESS_MAX_RUN_BUDGET_MICROS": str(spec.worker_max_run_budget_micros),
        "HARNESS_MAX_DISPATCHES": str(config.maxDispatches),
        "HARNESS_MAX_REPAIRS": str(config.maxRepairs),
        "HARNESS_MAX_OUTPUT_TOKENS": str(config.maxOutputTokens),
        "HARNESS_EVIDENCE_WINDOW_SENTENCES": str(config.evidenceWindowSentences),
        "HARNESS_MAX_RENDER_CONCURRENCY": str(config.maxRenderConcurrency),
        "HARNESS_TOPIC_SHOT_DETECTOR": spec.topic_shot_detector,
        "HARNESS_RECORDED_FIXTURE_PATH": arm.fixture_file.path if arm.fixture_file else "",
        "HARNESS_CHAPTER_LLAMA_CONFIG_JSON": "",
        "TEMPORAL_ADDRESS": spec.temporal_address,
        "TEMPORAL_NAMESPACE": spec.temporal_namespace,
        "TEMPORAL_TASK_QUEUE": arm.pipeline_queue,
    }


async def observe_source(database_url: str, case: SourceCase, scope: Scope) -> FrozenSource:
    """Observe existing scoped ready source pins and retained evidence metadata without writes."""
    source, transcript = await runs.ready_source_pins(
        database_url, scope=scope, source_id=case.source_id
    )
    async with db.scoped(database_url, scope) as conn:
        row = await (
            await conn.execute(
                """
                SELECT metadata FROM harness_artifact
                 WHERE source_id = %s AND kind = 'evidence'
                   AND transcript_id = %s AND transcript_revision = %s
                 ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (case.source_id, transcript.transcript_id, transcript.revision),
            )
        ).fetchone()
    metadata = cast("dict[str, object]", row["metadata"]) if row is not None else {}
    known_sha = metadata.get("sourceSha256")
    known_fingerprint = metadata.get("sourceFingerprint")
    if case.expected_source_sha256 and known_sha and case.expected_source_sha256 != known_sha:
        raise IdentityConflict("expected source bytes differ from retained source evidence")
    if (
        case.expected_source_fingerprint
        and known_fingerprint
        and case.expected_source_fingerprint != known_fingerprint
    ):
        raise IdentityConflict("expected source fingerprint differs from retained evidence")
    return FrozenSource(
        case=case,
        source=source,
        transcript=transcript,
        known_source_sha256=cast("str | None", known_sha),
        known_source_fingerprint=cast("str | None", known_fingerprint),
    )


async def prepare_experiment(
    spec: ExperimentSpec, *, database_url: str, environment: Mapping[str, str]
) -> PreparedExperiment:
    """Qualify every frozen arm locally before observing sources or creating any run."""
    experiment_id = uuid4()
    arms: list[FrozenArm] = []
    for arm_spec in spec.arms:
        snapshot_file = FilePin.read(arm_spec.route_snapshot_path)
        snapshot = load_route_snapshot(Path(snapshot_file.path))
        arm = FrozenArm(
            spec=arm_spec,
            snapshot=snapshot,
            snapshot_file=snapshot_file,
            fixture_file=(
                FilePin.read(arm_spec.recorded_fixture_path)
                if arm_spec.recorded_fixture_path
                else None
            ),
            pipeline_queue=arm_queue(experiment_id, arm_spec.id),
            control_queue=control_task_queue(arm_queue(experiment_id, arm_spec.id)),
        )
        settings = HarnessSettings.from_env({**environment, **worker_environment(spec, arm)})
        if settings.validate_boot() != snapshot or settings.allowed_config() != arm_spec.config:
            raise IdentityConflict("arm settings do not load their exact declared snapshot")
        assert_routes(snapshot, arm_spec)
        arms.append(arm)
    scope = resolve_scope()
    sources = tuple([await observe_source(database_url, case, scope) for case in spec.sources])
    return PreparedExperiment(
        id=experiment_id,
        prepared_at=datetime.now(UTC),
        spec=spec,
        scope=scope,
        rubric=topic_selection.make_rubric(spec.brief),
        program=current_program(spec.program_version),
        arms=tuple(arms),
        sources=sources,
        executions=tuple(
            make_execution(experiment_id, arm, source, spec, scope)
            for arm in arms
            for source in sources
        ),
    )


def write_prepared(path: Path, prepared: PreparedExperiment) -> None:
    """Create private intent once; a collision must not replace any previous run identities."""
    payload = canonical_json(
        {"sha256": prepared.sha256, "experiment": prepared.model_dump(mode="json", by_alias=True)}
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def read_prepared(path: Path) -> PreparedExperiment:
    """Validate frozen intent and its checksum without changing local or remote state."""
    from pydantic import BaseModel, ConfigDict  # noqa: PLC0415

    class Envelope(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        sha256: SHA256
        experiment: PreparedExperiment

    parsed: object = json.loads(path.read_bytes())
    if not isinstance(parsed, dict):
        raise IdentityConflict("prepared experiment checksum differs from its frozen intent")
    raw = cast("dict[str, object]", parsed)
    raw_experiment = raw.get("experiment")
    if set(raw) != {"sha256", "experiment"} or not isinstance(raw_experiment, dict):
        raise IdentityConflict("prepared experiment checksum differs from its frozen intent")
    experiment_payload = cast("dict[str, object]", raw_experiment)
    if digest(experiment_payload) != raw.get("sha256"):
        raise IdentityConflict("prepared experiment checksum differs from its frozen intent")
    envelope = Envelope.model_validate_json(path.read_bytes())
    return envelope.experiment


def assert_runtime(
    prepared: PreparedExperiment,
    arm: FrozenArm,
    settings: HarnessSettings,
    temporal: TemporalSettings,
) -> None:
    """Validate the launcher runtime; the unchanged worker also enforces run config at claim."""
    if prepared.scope != resolve_scope():
        raise IdentityConflict("experiment scope differs from the active scope resolver")
    if temporal != TemporalSettings(
        prepared.spec.temporal_address, prepared.spec.temporal_namespace, arm.pipeline_queue
    ):
        raise IdentityConflict("Temporal runtime is not the experiment's isolated arm queue")
    if prepared.program != current_program(prepared.spec.program_version):
        raise IdentityConflict(
            "current implementation/prompt/schema differs from prepared programme"
        )
    for pin in (arm.snapshot_file, arm.fixture_file):
        if pin is not None:
            pin.verify()
    if (
        not settings.enabled
        or settings.allowed_config() != arm.spec.config
        or settings.topic_shot_detector != prepared.spec.topic_shot_detector
        or settings.max_run_budget_micros != prepared.spec.worker_max_run_budget_micros
    ):
        raise IdentityConflict("worker runtime settings differ from the frozen experiment")
    for actual_path, expected_file in (
        (settings.route_snapshot_path, arm.snapshot_file),
        (settings.recorded_fixture_path, arm.fixture_file),
    ):
        if (actual_path is None) != (expected_file is None) or (
            actual_path is not None
            and expected_file is not None
            and FilePin.read(str(actual_path)) != expected_file
        ):
            raise IdentityConflict("worker proof/configuration files differ from frozen files")
    if settings.validate_boot() != arm.snapshot:
        raise IdentityConflict("worker routes differ from the qualified frozen snapshot")
    assert_routes(arm.snapshot, arm.spec)


def assert_pinned_run(
    prepared: PreparedExperiment, execution: PreparedExecution, run: RunSnapshot
) -> None:
    """A prepared manifest cannot authorize a paid run on a newer source or transcript."""
    source = next(source for source in prepared.sources if source.case.id == execution.case_id)
    arm = next(arm for arm in prepared.arms if arm.spec.id == execution.arm_id)
    request = execution.request
    if (
        run.id != request.runId
        or run.source_id != request.sourceId
        or run.request_key != request.requestKey
        or run.initial_budget_micros != request.budgetMicros
        or run.config != request.config
        or run.brief != request.brief
        or run.workflow_id != execution.workflow_id
        or run.source != source.source
        or run.transcript != source.transcript
        or run.route_snapshot != arm.snapshot
        or run.editorial_policy != prepared.program.policy
        or run.topic_shot_detector != prepared.spec.topic_shot_detector
        or run.evaluation_program != prepared.program.model_dump(mode="json", by_alias=True)
        or run.evaluation_program_sha256
        != digest(prepared.program.model_dump(mode="json", by_alias=True))
    ):
        raise IdentityConflict("durable run pins differ from the prepared experiment")


class WorkflowObservation(EvaluationModel):
    """Only observed Temporal facts, never a fabricated successful start."""

    workflow_id: str
    run_id: str
    workflow_type: str
    task_queue: str
    status: str
    experiment_sha256: str | None
    intent_sha256: str | None


class WorkflowDriver(Protocol):
    """The launcher can start once or inspect; it cannot run models or reset paid work."""

    async def describe(self, workflow_id: str) -> WorkflowObservation | None:
        """Return absent only after a definite Temporal NOT_FOUND."""
        ...

    async def start(self, execution: PreparedExecution, queue: str, experiment_sha256: str) -> None:
        """Start only the declared immutable identity, refusing all duplicate executions."""
        ...


class TemporalDriver:
    """Production Temporal client with the same Pydantic payload converter as the worker."""

    def __init__(self, client: Client) -> None:
        self.client = client

    @classmethod
    async def connect(cls, settings: TemporalSettings) -> TemporalDriver:
        """Connect without starting a worker or mutating namespace configuration."""
        async with asyncio.timeout(RPC_TIMEOUT.total_seconds()):
            return cls(
                await Client.connect(
                    settings.address,
                    namespace=settings.namespace,
                    data_converter=pydantic_data_converter,
                )
            )

    async def describe(self, workflow_id: str) -> WorkflowObservation | None:
        """Treat connection failures as unknown, never as an absent workflow."""
        try:
            description = await self.client.get_workflow_handle(workflow_id).describe(
                rpc_timeout=RPC_TIMEOUT
            )
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        memo = await description.memo()
        return WorkflowObservation(
            workflow_id=description.id,
            run_id=description.run_id,
            workflow_type=description.workflow_type,
            task_queue=description.task_queue,
            status=description.status.name if description.status is not None else "UNKNOWN",
            experiment_sha256=memo.get(MEMO_KEY),
            intent_sha256=memo.get(INTENT_MEMO_KEY),
        )

    async def start(self, execution: PreparedExecution, queue: str, experiment_sha256: str) -> None:
        """Never select ALLOW_DUPLICATE: closed executions retain the same logical run."""
        await self.client.start_workflow(
            execution.workflow_type,
            execution.request,
            id=execution.workflow_id,
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            rpc_timeout=RPC_TIMEOUT,
            memo={
                MEMO_KEY: experiment_sha256,
                INTENT_MEMO_KEY: digest(execution.request.model_dump(mode="json")),
            },
        )


def assert_workflow(
    prepared: PreparedExperiment, execution: PreparedExecution, observed: WorkflowObservation
) -> None:
    """An existing ID is reusable only when its programme, input and queue also match."""
    queue = next(arm.pipeline_queue for arm in prepared.arms if arm.spec.id == execution.arm_id)
    if (
        observed.workflow_id != execution.workflow_id
        or observed.workflow_type != execution.workflow_type
        or observed.task_queue != queue
        or observed.experiment_sha256 != prepared.sha256
        or observed.intent_sha256 != digest(execution.request.model_dump(mode="json"))
    ):
        raise IdentityConflict("existing Temporal execution differs from prepared intent")


class ExecutionStatus(EvaluationModel):
    """Read-only operational state; source comparability is separate from model quality."""

    arm_id: str
    case_id: str
    run_id: UUID
    workflow_id: str
    launch_state: Literal["prepared", "prepared_unstarted", "started", "start_unknown", "fenced"]
    temporal_status: str | None = None
    run_status: str | None = None
    run_stage: str | None = None
    spent_micros: int = 0
    reserved_micros: int = 0
    source_identity: Literal["pending_evidence", "matched", "unknown", "mismatch"] = (
        "pending_evidence"
    )
    observed_source_sha256: str | None = None
    observed_source_fingerprint: str | None = None
    observed_evidence_sha256: str | None = None
    reasons: tuple[str, ...] = ()


async def read_status(
    prepared: PreparedExperiment,
    execution: PreparedExecution,
    *,
    database_url: str,
    driver: WorkflowDriver,
) -> ExecutionStatus:
    """Observe database and Temporal only; no start, ownership claim, or file write."""
    if prepared.scope != resolve_scope():
        raise IdentityConflict("experiment scope differs from the active scope resolver")
    run = await runs.find_run(
        database_url,
        scope=prepared.scope,
        source_id=execution.request.sourceId,
        run_id=execution.request.runId,
    )
    reasons: list[str] = []
    try:
        observed = await driver.describe(execution.workflow_id)
    except (RPCError, TimeoutError):
        observed = None
        reasons.append("Temporal status is unknown; inspect this same prepared workflow ID.")
    if observed is not None:
        assert_workflow(prepared, execution, observed)
    state: Literal["prepared", "prepared_unstarted", "started", "start_unknown", "fenced"] = (
        "start_unknown" if reasons else "started" if observed else "prepared"
    )
    if run is not None:
        assert_pinned_run(prepared, execution, run)
        if observed is None and not reasons:
            state = (
                "prepared_unstarted"
                if run.workflow_run_id == execution.preparation_identity.workflow_run_id
                else "fenced"
            )
    status = ExecutionStatus(
        arm_id=execution.arm_id,
        case_id=execution.case_id,
        run_id=execution.request.runId,
        workflow_id=execution.workflow_id,
        launch_state=state,
        temporal_status=observed.status if observed else None,
        run_status=run.status.value if run else None,
        run_stage=run.stage if run else None,
        spent_micros=run.spent_micros if run else 0,
        reserved_micros=run.reserved_micros if run else 0,
        reasons=tuple(reasons),
    )
    return await observe_evidence(prepared, execution, run, status, database_url=database_url)


async def observe_evidence(
    prepared: PreparedExperiment,
    execution: PreparedExecution,
    run: RunSnapshot | None,
    status: ExecutionStatus,
    *,
    database_url: str,
) -> ExecutionStatus:
    """Compare subsequently retained verified evidence facts without rewriting expectations."""
    if run is None or run.evidence_artifact_id is None:
        return status
    async with db.scoped(database_url, prepared.scope) as conn:
        row = await (
            await conn.execute(
                """
                SELECT sha256, metadata FROM harness_artifact
                 WHERE id = %s AND source_id = %s AND kind = 'evidence'
                """,
                (run.evidence_artifact_id, run.source_id),
            )
        ).fetchone()
    if row is None:
        raise IdentityConflict("run evidence is absent from its source scope")
    source = next(source for source in prepared.sources if source.case.id == execution.case_id)
    metadata = cast("dict[str, str]", row["metadata"])
    return compare_evidence(
        source,
        status,
        source_sha256=metadata.get("sourceSha256"),
        source_fingerprint=metadata.get("sourceFingerprint"),
        evidence_sha256=str(row["sha256"]),
    )


def compare_evidence(
    source: FrozenSource,
    status: ExecutionStatus,
    *,
    source_sha256: str | None,
    source_fingerprint: str | None,
    evidence_sha256: str,
) -> ExecutionStatus:
    """Retain late source mismatches as operational facts, not model-quality scores."""
    expected = (
        source.case.expected_source_sha256 or source.known_source_sha256,
        source.case.expected_source_fingerprint or source.known_source_fingerprint,
        source.case.expected_evidence_sha256,
    )
    actual = (source_sha256, source_fingerprint, evidence_sha256)
    mismatched = any(
        want is not None and want != got for want, got in zip(expected, actual, strict=True)
    )
    state = "mismatch" if mismatched else "unknown" if expected[0] is None else "matched"
    reasons = status.reasons
    if mismatched:
        reasons += ("Observed source/evidence differs from frozen expectations; incomparable.",)
    elif expected[0] is None:
        reasons += ("Source bytes had no pre-run hash; controlled byte identity remains unknown.",)
    return status.model_copy(
        update={
            "source_identity": state,
            "observed_source_sha256": actual[0],
            "observed_source_fingerprint": actual[1],
            "observed_evidence_sha256": actual[2],
            "reasons": reasons,
        }
    )


async def run_execution(  # noqa: PLR0913
    prepared: PreparedExperiment,
    execution: PreparedExecution,
    *,
    database_url: str,
    settings: HarnessSettings,
    temporal: TemporalSettings,
    driver: WorkflowDriver,
) -> ExecutionStatus:
    """Precreate, compare source pins, then start or inspect the exact prepared workflow."""
    arm = next(arm for arm in prepared.arms if arm.spec.id == execution.arm_id)
    assert_runtime(prepared, arm, settings, temporal)
    run = await runs.find_run(
        database_url,
        scope=prepared.scope,
        source_id=execution.request.sourceId,
        run_id=execution.request.runId,
    )
    if run is None:
        source = next(source for source in prepared.sources if source.case.id == execution.case_id)
        current = await runs.ready_source_pins(
            database_url, scope=prepared.scope, source_id=execution.request.sourceId
        )
        if current != (source.source, source.transcript):
            raise IdentityConflict("ready source changed since experiment preparation")
        started = await runs.start_or_refetch_run(
            database_url,
            start=StartRunRequest(
                request=execution.request,
                workflow=execution.preparation_identity,
                editorial_policy=cast("Any", prepared.spec.program_version),
                evaluation_program=prepared.program.model_dump(mode="json", by_alias=True),
            ),
            settings=settings,
            route_snapshot=arm.snapshot,
        )
        run = started.run
    assert_pinned_run(prepared, execution, run)
    try:
        observed = await driver.describe(execution.workflow_id)
    except (RPCError, TimeoutError):
        # An uncertain read never authorizes a new start.
        return await read_status(prepared, execution, database_url=database_url, driver=driver)
    if observed is not None:
        assert_workflow(prepared, execution, observed)
    elif (
        run.status.value == "pending"
        and run.workflow_run_id == execution.preparation_identity.workflow_run_id
    ):
        try:
            await driver.start(execution, arm.pipeline_queue, prepared.sha256)
        except (RPCError, TimeoutError, WorkflowAlreadyStartedError):
            # The same ID and REJECT_DUPLICATE are retained on every subsequent invocation.
            status = await read_status(
                prepared, execution, database_url=database_url, driver=driver
            )
            if status.launch_state in {"prepared", "prepared_unstarted"}:
                return status.model_copy(
                    update={
                        "launch_state": "start_unknown",
                        "reasons": (
                            *status.reasons,
                            "Start outcome is unknown; reuse this exact prepared intent.",
                        ),
                    }
                )
            return status
    return await read_status(prepared, execution, database_url=database_url, driver=driver)
