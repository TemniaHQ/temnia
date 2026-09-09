"""Finite experiment plan and durable local accounting for speech benchmarks."""

# Refusal messages live beside exact invariant checks, and Pydantic evaluates
# these runtime annotations while constructing the strict wire schemas.
# ruff: noqa: EM101, EM102, PLR2004, TC001, TC003, TRY003

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from collections.abc import AsyncGenerator, Generator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile

VariantId = Literal["A", "B", "C", "D"]
CaseKind = Literal["preflight", "long"]
CaseStatus = Literal["planned", "reserved", "completed", "failed"]

BENCHMARK_FORMAT = "temnia-speech-benchmark/1"
JOURNAL_FORMAT = "temnia-speech-benchmark-journal/1"
EXPERIMENT_EXPOSURE_CAP_MICROS = 14_000_000
EXPERIMENT_DISPATCH_CAP = 36
CALLS_PER_CASE = 3
EXPECTED_CASES = 12
REUSED_WITHOUT_ORIGINAL_METRICS = "checkpoint_reused_without_original_container_metrics"
BENCHMARK_CASE_ORDER: tuple[tuple[CaseKind, VariantId, int | None], ...] = (
    ("preflight", "A", None),
    ("preflight", "B", None),
    ("preflight", "C", None),
    ("preflight", "D", None),
    ("long", "A", 1),
    ("long", "B", 1),
    ("long", "C", 1),
    ("long", "D", 1),
    ("long", "D", 2),
    ("long", "C", 2),
    ("long", "B", 2),
    ("long", "A", 2),
)
SHORT_SOURCE_SHA256 = "31ae0e36255c429b9842b7fdc2cfe2edec9034096ac1b188be943173381a57af"
SHORT_SOURCE_SIZE = 795_054
SHORT_SOURCE_DURATION_MS = 40_116
LONG_SOURCE_SHA256 = "85615a2b6e04512cb333b80c4af6080a0f2017b355146b289303ed57bacc0c91"
LONG_SOURCE_SIZE = 110_024_458
LONG_SOURCE_DURATION_MS = 9_060_473
EXPERIMENT_LOCK_ROOT = Path("/private/tmp/temnia-speech-benchmark-locks")

_WIRE = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
    frozen=True,
    allow_inf_nan=False,
)


class BenchmarkVariant(BaseModel):
    """One reviewed deployment in the one-factor-at-a-time staircase."""

    model_config = _WIRE

    id: VariantId
    app: str = Field(min_length=1)
    resource_profile: SpeechResourceProfile
    execution_topology: Literal["serial", "parallel"]
    rate_micros_per_hour: int = Field(gt=0)

    @model_validator(mode="after")
    def exact_reviewed_variant(self) -> BenchmarkVariant:
        """Reject knobs outside the four variants reviewed before implementation."""
        expected = {
            "A": (4, "synchronous_control", "serial", 1_250_000),
            "B": (4, "coalesced", "serial", 1_250_000),
            "C": (8, "coalesced", "serial", 1_450_000),
            "D": (8, "coalesced", "parallel", 1_450_000),
        }[self.id]
        actual = (
            self.resource_profile.cpu_cores,
            self.resource_profile.progress_mode,
            self.execution_topology,
            self.rate_micros_per_hour,
        )
        if actual != expected:
            raise ValueError(f"variant {self.id} differs from the reviewed benchmark matrix")
        if (
            self.resource_profile.stage_timeout_seconds != 900
            or self.resource_profile.startup_timeout_seconds != 120
            or not self.resource_profile.single_use_containers
            or self.resource_profile.retries != 0
        ):
            raise ValueError("benchmark resources require 900s + 120s and single-use retry-zero")
        expected_reservation = 354_167 if self.resource_profile.cpu_cores == 4 else 410_834
        if self.reservation_micros != expected_reservation:
            raise ValueError("variant reservation differs from the reviewed integer ceiling")
        return self

    @property
    def reservation_micros(self) -> int:
        """Worst-case exposure for one physical function call."""
        return self.resource_profile.reservation_micros(self.rate_micros_per_hour)

    @property
    def case_exposure_micros(self) -> int:
        """Non-recyclable exposure for exactly three GPU calls."""
        return self.reservation_micros * CALLS_PER_CASE


class BenchmarkDeploymentManifest(BaseModel):
    """Strict operator input naming the four already-deployed variants."""

    model_config = _WIRE

    format: Literal["temnia-speech-benchmark/1"] = BENCHMARK_FORMAT
    source_build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_manifest: SpeechModelManifest
    variants: tuple[BenchmarkVariant, ...]

    @model_validator(mode="after")
    def complete_matrix(self) -> BenchmarkDeploymentManifest:
        """Require each finite variant and the reviewed deployment sharing."""
        if getattr(self.model_manifest, "image_assets_sha256", None) is None:
            raise ValueError("benchmark model manifest requires immutable image asset identity")
        if tuple(variant.id for variant in self.variants) != ("A", "B", "C", "D"):
            raise ValueError("benchmark manifest variants must be ordered exactly A, B, C, D")
        apps = {variant.id: variant.app for variant in self.variants}
        if len({apps["A"], apps["B"], apps["C"]}) != 3 or apps["D"] != apps["C"]:
            raise ValueError("A/B/C require distinct apps and D must reuse C's resource app")
        if self.total_exposure_micros != 13_770_018:
            raise ValueError("benchmark matrix exposure differs from the reviewed total")
        return self

    @property
    def sha256(self) -> str:
        """Canonical identity persisted before the first benchmark resource is created."""
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json", by_alias=True))
        ).hexdigest()

    @property
    def total_exposure_micros(self) -> int:
        """Four preflights plus two long trials for every variant."""
        return sum(variant.case_exposure_micros * 3 for variant in self.variants)


class FrozenSource(BaseModel):
    """Exact local bytes used by one class of benchmark case."""

    model_config = _WIRE

    kind: CaseKind
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    duration_ms: int = Field(gt=0)

    def verify(self) -> None:
        """Hash the input before creating a database row or storage object."""
        path = self.path.resolve(strict=True)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(8 * 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if size != self.size_bytes or digest.hexdigest() != self.sha256:
            raise ValueError(f"{self.kind} benchmark source differs from its frozen bytes")


class BenchmarkCase(BaseModel):
    """One fresh source and workflow in the deterministic experiment order."""

    model_config = _WIRE

    key: str = Field(min_length=1)
    variant_id: VariantId
    kind: CaseKind
    block: int | None = Field(default=None, ge=1, le=2)
    source_id: UUID
    workflow_id: str = Field(min_length=1)
    object_prefix: str = Field(min_length=1)
    configured_exposure_micros: int = Field(gt=0)
    max_dispatches: Literal[3] = CALLS_PER_CASE
    status: CaseStatus = "planned"
    error_type: str | None = None


class BenchmarkJournal(BaseModel):
    """Fsync-backed non-recyclable experiment admission record."""

    model_config = _WIRE

    format: Literal["temnia-speech-benchmark-journal/1"] = JOURNAL_FORMAT
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    deployment_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exposure_cap_micros: Literal[14000000] = EXPERIMENT_EXPOSURE_CAP_MICROS
    dispatch_cap: Literal[36] = EXPERIMENT_DISPATCH_CAP
    reserved_exposure_micros: int = Field(default=0, ge=0)
    reserved_dispatches: int = Field(default=0, ge=0)
    cases: tuple[BenchmarkCase, ...]

    @model_validator(mode="after")
    def bounded_unique_plan(self) -> BenchmarkJournal:
        """Refuse a malformed or partially duplicated experiment plan."""
        if len(self.cases) != EXPECTED_CASES:
            raise ValueError("benchmark journal requires exactly twelve cases")
        for field in ("key", "source_id", "workflow_id", "object_prefix"):
            if len({getattr(case, field) for case in self.cases}) != EXPECTED_CASES:
                raise ValueError(f"benchmark cases contain duplicate {field}")
        actual = [(case.kind, case.variant_id, case.block) for case in self.cases]
        if actual != list(BENCHMARK_CASE_ORDER):
            raise ValueError("benchmark cases differ from the reviewed deterministic order")
        if self.reserved_exposure_micros > self.exposure_cap_micros:
            raise ValueError("benchmark journal exceeds its exposure cap")
        if self.reserved_dispatches > self.dispatch_cap:
            raise ValueError("benchmark journal exceeds its dispatch cap")
        return self


@dataclass(slots=True)
class ExperimentLease:
    """Process-held lock plus durable one-time admission for an experiment id."""

    experiment_id: str
    path: Path
    descriptor: int
    held: bool = True

    def mark_admitted(self, *, journal_path: Path, manifest_sha256: str) -> None:
        """Persist the non-reusable experiment identity before remote mutation."""
        if not self.held:
            raise RuntimeError("benchmark experiment lease is no longer held")
        if os.fstat(self.descriptor).st_size != 0:
            raise ValueError("benchmark experiment id was already admitted")
        body = (
            canonical_json(
                {
                    "experimentId": self.experiment_id,
                    "journalPath": str(journal_path),
                    "manifestSha256": manifest_sha256,
                }
            )
            + b"\n"
        )
        remaining = memoryview(body)
        while remaining:
            written = os.write(self.descriptor, remaining)
            if written <= 0:
                raise OSError("benchmark admission marker write made no progress")
            remaining = remaining[written:]
        os.fsync(self.descriptor)

    def assert_held(self) -> None:
        """Reject journal mutation after or outside the experiment lock."""
        if not self.held:
            raise RuntimeError("benchmark journal mutation requires its experiment lease")


@contextmanager
def experiment_lease(
    experiment_id: str, *, lock_root: Path = EXPERIMENT_LOCK_ROOT
) -> Generator[ExperimentLease]:
    """Hold the stable host lock for the complete driver execution."""
    if not experiment_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in experiment_id
    ):
        raise ValueError("benchmark experiment id must be lowercase kebab case")
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_root.chmod(0o700)
    path = lock_root / f"{experiment_id}.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    lease = ExperimentLease(experiment_id, path, descriptor)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("benchmark experiment is already running on this host") from error
        if os.fstat(descriptor).st_size != 0:
            raise ValueError("benchmark experiment id was already admitted")
        yield lease
    finally:
        lease.held = False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _advisory_keys(experiment_id: str) -> tuple[int, int]:
    digest = hashlib.sha256(f"temnia:speech-benchmark:{experiment_id}".encode()).digest()
    return (
        int.from_bytes(digest[0:4], "big", signed=True),
        int.from_bytes(digest[4:8], "big", signed=True),
    )


@asynccontextmanager
async def database_experiment_lease(
    database_url: str, *, organization_id: UUID, experiment_id: str
) -> AsyncGenerator[None]:
    """Hold a database-wide experiment lock and refuse earlier admitted runs."""
    connection = cast(
        "AsyncConnection[dict[str, Any]]",
        await AsyncConnection.connect(
            database_url,
            row_factory=cast("Any", dict_row),
            autocommit=True,
        ),
    )
    first_key, second_key = _advisory_keys(experiment_id)
    acquired = False
    try:
        lock = await (
            await connection.execute(
                "SELECT pg_try_advisory_lock(%s, %s) AS acquired",
                (first_key, second_key),
            )
        ).fetchone()
        if lock is None or lock["acquired"] is not True:
            raise RuntimeError("benchmark experiment is already running against this database")
        acquired = True
        workflow_prefix = f"speech-benchmark-{experiment_id}-%"
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.organization_id', %s, true)",
                (str(organization_id),),
            )
            prior = await (
                await connection.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM transcript WHERE workflow_id LIKE %s
                    ) AS found
                    """,
                    (workflow_prefix,),
                )
            ).fetchone()
        if prior is None or prior["found"] is not False:
            raise ValueError("benchmark database already contains an admitted experiment run")
        yield
    finally:
        if acquired:
            with suppress(Exception):
                await connection.execute(
                    "SELECT pg_advisory_unlock(%s, %s)", (first_key, second_key)
                )
        await connection.close()


def build_journal(
    *,
    experiment_id: str,
    organization_id: UUID,
    manifest: BenchmarkDeploymentManifest,
) -> BenchmarkJournal:
    """Create fresh source and workflow identities for the complete fixed matrix."""
    variants = {variant.id: variant for variant in manifest.variants}
    cases: list[BenchmarkCase] = []
    for kind, variant_id, block in BENCHMARK_CASE_ORDER:
        source_id = uuid4()
        suffix = f"{kind}-{variant_id.lower()}" + (f"-{block}" if block else "")
        cases.append(
            BenchmarkCase(
                key=suffix,
                variant_id=variant_id,
                kind=kind,
                block=block,
                source_id=source_id,
                workflow_id=f"speech-benchmark-{experiment_id}-{suffix}",
                object_prefix=f"org/{organization_id}/source/{source_id}/",
                configured_exposure_micros=variants[variant_id].case_exposure_micros,
            )
        )
    return BenchmarkJournal(
        experiment_id=experiment_id,
        deployment_manifest_sha256=manifest.sha256,
        cases=tuple(cases),
    )


def assert_benchmark_environment(database_url: str, namespace: str, experiment_id: str) -> None:
    """Refuse shared databases/namespaces before any experiment mutation."""
    database_name = database_url.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]
    marker = experiment_id.replace("-", "_")
    if "speech_benchmark" not in database_name or marker not in database_name:
        raise ValueError("benchmark database must be dedicated to the exact experiment id")
    if "speech-benchmark" not in namespace or experiment_id not in namespace:
        raise ValueError("benchmark Temporal namespace must name the exact experiment id")


def write_private_bytes(path: Path, body: bytes, *, refuse_existing: bool = False) -> None:
    """Atomically preserve private evidence with file and directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(temporary, flags, 0o600), "wb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    if refuse_existing:
        try:
            os.link(temporary, path)
        except FileExistsError:
            temporary.unlink()
            raise FileExistsError(f"benchmark journal already exists: {path}") from None
        temporary.unlink()
    else:
        temporary.replace(path)
    path.chmod(0o600)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_private(path: Path, value: object, *, refuse_existing: bool = False) -> None:
    write_private_bytes(
        path,
        canonical_json(value) + b"\n",
        refuse_existing=refuse_existing,
    )


def initialize_journal(path: Path, journal: BenchmarkJournal, *, lease: ExperimentLease) -> None:
    """Persist the full experiment identity before any remote or database mutation."""
    lease.assert_held()
    if lease.experiment_id != journal.experiment_id:
        raise ValueError("benchmark lease and journal name different experiments")
    _write_private(path, journal.model_dump(mode="json", by_alias=True), refuse_existing=True)


def read_journal(path: Path) -> BenchmarkJournal:
    """Strictly read the durable experiment admission record."""
    return BenchmarkJournal.model_validate_json(path.read_bytes(), strict=True)


def reserve_case(path: Path, case_key: str, *, lease: ExperimentLease) -> BenchmarkJournal:
    """Consume one case's exposure before its source or workflow is created."""
    lease.assert_held()
    journal = read_journal(path)
    if lease.experiment_id != journal.experiment_id:
        raise ValueError("benchmark lease and journal name different experiments")
    target = next((case for case in journal.cases if case.key == case_key), None)
    if target is None:
        raise KeyError(f"unknown benchmark case {case_key}")
    if target.status != "planned":
        raise ValueError(f"benchmark case {case_key} was already admitted")
    target_index = journal.cases.index(target)
    if any(case.status != "completed" for case in journal.cases[:target_index]):
        raise ValueError("benchmark cases must be admitted in order after prior success")
    if any(case.status in {"reserved", "failed"} for case in journal.cases):
        raise ValueError("benchmark cannot continue after an active or failed case")
    exposure = journal.reserved_exposure_micros + target.configured_exposure_micros
    dispatches = journal.reserved_dispatches + target.max_dispatches
    if exposure > journal.exposure_cap_micros or dispatches > journal.dispatch_cap:
        raise ValueError("benchmark aggregate cap cannot admit this case")
    cases = tuple(
        case.model_copy(update={"status": "reserved"}) if case.key == case_key else case
        for case in journal.cases
    )
    updated = journal.model_copy(
        update={
            "reserved_exposure_micros": exposure,
            "reserved_dispatches": dispatches,
            "cases": cases,
        }
    )
    _write_private(path, updated.model_dump(mode="json", by_alias=True))
    return updated


def finish_case(
    path: Path,
    case_key: str,
    *,
    error_type: str | None,
    lease: ExperimentLease,
) -> BenchmarkJournal:
    """Record a terminal observation without returning any exposure credit."""
    lease.assert_held()
    journal = read_journal(path)
    if lease.experiment_id != journal.experiment_id:
        raise ValueError("benchmark lease and journal name different experiments")
    target = next((case for case in journal.cases if case.key == case_key), None)
    if target is None:
        raise KeyError(f"unknown benchmark case {case_key}")
    if target.status != "reserved":
        raise ValueError(f"benchmark case {case_key} is not reserved")
    cases = tuple(
        case.model_copy(
            update={"status": "failed" if error_type else "completed", "error_type": error_type}
        )
        if case.key == case_key
        else case
        for case in journal.cases
    )
    updated = journal.model_copy(update={"cases": cases})
    _write_private(path, updated.model_dump(mode="json", by_alias=True))
    return updated


def measured_resource_estimate_micros(
    profile: SpeechResourceProfile, elapsed_seconds: float
) -> int:
    """Estimate dated resource-floor cost without presenting it as an invoice."""
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("elapsed seconds must be finite and nonnegative")
    value = (
        Decimal(profile.minimum_rate_micros_per_hour)
        * Decimal(str(elapsed_seconds))
        / Decimal(3600)
    )
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def assert_case_completed(  # noqa: C901, PLR0912, PLR0915
    report: Mapping[str, object],
    *,
    variant: BenchmarkVariant,
    require_lost_result: bool,
) -> None:
    """Prove exactly three accepted calls and conservative cost accounting."""
    run = report.get("run")
    attempts = report.get("attempts")
    workflow = report.get("workflow")
    transcript = report.get("transcript")
    if (
        not isinstance(run, dict)
        or not isinstance(attempts, list)
        or not isinstance(workflow, dict)
        or not isinstance(transcript, dict)
    ):
        raise TypeError("benchmark report lacks run, attempt or workflow evidence")
    run_values = cast("dict[str, object]", run)
    attempt_values = cast("list[object]", attempts)
    workflow_values = cast("dict[str, object]", workflow)
    transcript_values = cast("dict[str, object]", transcript)
    if run_values.get("status") != "ready":
        raise ValueError("benchmark run is not ready")
    if run_values.get("budget_micros") != variant.case_exposure_micros:
        raise ValueError("benchmark run budget differs from its exact three-call exposure")
    if run_values.get("dispatch_count") != CALLS_PER_CASE:
        raise ValueError("benchmark run did not dispatch exactly three physical calls")
    if transcript_values.get("status") != "ready" or not isinstance(
        workflow_values.get("transcriptSha256"), str
    ):
        raise ValueError("benchmark transcript lacks an exact accepted revision body")
    if len(attempt_values) != CALLS_PER_CASE:
        raise ValueError("benchmark report contains an unexpected physical attempt count")
    expected_stages = {"recognize", "align", "speaker_turns"}
    found_stages: set[str] = set()
    retained = 0
    for item in attempt_values:
        if not isinstance(item, dict):
            raise TypeError("benchmark attempt is not an object")
        attempt = cast("dict[str, object]", item)
        if attempt.get("state") != "succeeded" or attempt.get("dispatched_at") is None:
            raise ValueError("benchmark attempt lacks terminal accepted dispatch evidence")
        if attempt.get("estimated_cost_micros") != variant.reservation_micros:
            raise ValueError("benchmark attempt reservation differs from its frozen profile")
        usage = attempt.get("usage")
        usage_values = cast("dict[str, object]", usage) if isinstance(usage, dict) else None
        telemetry = usage_values.get("telemetry") if usage_values is not None else None
        telemetry_values = (
            cast("dict[str, object]", telemetry) if isinstance(telemetry, dict) else None
        )
        unavailable = (
            telemetry_values.get("metricsUnavailable") if telemetry_values is not None else None
        )
        unavailable_values = (
            cast("list[object]", unavailable) if isinstance(unavailable, list) else []
        )
        elapsed = telemetry_values.get("elapsedSeconds") if telemetry_values else None
        if (
            telemetry_values is None
            or telemetry_values.get("complete") is not True
            or REUSED_WITHOUT_ORIGINAL_METRICS in unavailable_values
            or not isinstance(elapsed, int | float)
            or isinstance(elapsed, bool)
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ValueError(
                "benchmark attempt telemetry is unsuitable for a controlled measurement"
            )
        stage = attempt.get("stage")
        if not isinstance(stage, str):
            raise TypeError("benchmark attempt lacks a stage")
        found_stages.add(stage)
        reservation_state = attempt.get("reservation_state")
        if reservation_state == "active":
            if (
                attempt.get("cost_status") != "unknown"
                or attempt.get("actual_cost_micros") is not None
            ):
                raise ValueError("active benchmark reservation lacks unknown-cost provenance")
            retained += variant.reservation_micros
        elif reservation_state not in {"settled", "released"}:
            raise ValueError("benchmark reservation has an unsupported state")
    if found_stages != expected_stages:
        raise ValueError("benchmark accepted stage set is incomplete")
    if run_values.get("reserved_micros") != retained:
        raise ValueError("benchmark run does not retain every unresolved cost exposure")
    function_elapsed = workflow_values.get("functionElapsedSeconds")
    if (
        not isinstance(function_elapsed, int | float)
        or isinstance(function_elapsed, bool)
        or not math.isfinite(function_elapsed)
        or function_elapsed < 0
    ):
        raise ValueError("benchmark lacks complete full-function elapsed time")
    expected_activity_attempts = [1, 2] if require_lost_result else [1]
    if workflow_values.get("activityAttempts") != expected_activity_attempts:
        raise ValueError("benchmark activity attempts differ from the planned recovery proof")
    expected_dispatch_history = (
        [CALLS_PER_CASE, CALLS_PER_CASE] if require_lost_result else [CALLS_PER_CASE]
    )
    if workflow_values.get("observedDispatchCounts") != expected_dispatch_history:
        raise ValueError("benchmark activity retry changed the physical dispatch count")


def benchmark_summary(  # noqa: C901, PLR0912, PLR0915
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Compare the complete fixed matrix without exposing transcript text."""
    by_identity: dict[tuple[str, int | None, str], Mapping[str, object]] = {}
    for case in cases:
        kind = case.get("kind")
        block = case.get("block")
        variant = case.get("variant")
        if (
            kind not in {"preflight", "long"}
            or (block is not None and not isinstance(block, int))
            or variant not in {"A", "B", "C", "D"}
        ):
            raise ValueError("benchmark summary case has an invalid matrix identity")
        identity = (cast("str", kind), block, cast("str", variant))
        if identity in by_identity:
            raise ValueError("benchmark summary contains a duplicate matrix case")
        by_identity[identity] = case
    expected = {(kind, block, variant) for kind, variant, block in BENCHMARK_CASE_ORDER}
    if set(by_identity) != expected:
        raise ValueError("benchmark summary is incomplete")

    output_differences: list[dict[str, object]] = []
    comparison_fields = (
        "language",
        "wordCount",
        "speakerCount",
        "firstWordStartMs",
        "lastWordEndMs",
        "tokenSha256",
        "timingSha256",
        "speakerSha256",
    )
    for (kind, block, variant), case in by_identity.items():
        baseline = by_identity[(kind, block, "A")]
        current_facts = case.get("outputFacts")
        baseline_facts = baseline.get("outputFacts")
        if not isinstance(current_facts, dict) or not isinstance(baseline_facts, dict):
            raise TypeError("benchmark summary lacks private output identity facts")
        current_values = cast("dict[str, object]", current_facts)
        baseline_values = cast("dict[str, object]", baseline_facts)
        output_differences.append(
            {
                "kind": kind,
                "block": block,
                "variant": variant,
                "baselineVariant": "A",
                "same": {
                    field: current_values.get(field) == baseline_values.get(field)
                    for field in comparison_fields
                },
            }
        )

    latency_staircase: list[dict[str, object]] = []
    for block in (1, 2):
        for before, after in (("A", "B"), ("B", "C"), ("C", "D")):
            before_case = by_identity[("long", block, before)]
            after_case = by_identity[("long", block, after)]
            before_wall = before_case.get("wallSeconds")
            after_wall = after_case.get("wallSeconds")
            before_function = before_case.get("functionElapsedSeconds")
            after_function = after_case.get("functionElapsedSeconds")
            if not all(
                isinstance(value, int | float) and not isinstance(value, bool)
                for value in (before_wall, after_wall, before_function, after_function)
            ):
                raise ValueError("benchmark summary lacks finite timing facts")
            before_wall_float = float(cast("int | float", before_wall))
            after_wall_float = float(cast("int | float", after_wall))
            latency_staircase.append(
                {
                    "block": block,
                    "from": before,
                    "to": after,
                    "wallSecondsBefore": before_wall_float,
                    "wallSecondsAfter": after_wall_float,
                    "wallDeltaSeconds": after_wall_float - before_wall_float,
                    "wallChangePercent": (
                        ((after_wall_float / before_wall_float) - 1) * 100
                        if before_wall_float > 0
                        else None
                    ),
                    "functionSecondsBefore": float(cast("int | float", before_function)),
                    "functionSecondsAfter": float(cast("int | float", after_function)),
                }
            )

    actual_totals: dict[str, int] = dict.fromkeys(("A", "B", "C", "D"), 0)
    authoritative = True
    for (_, _, variant), case in by_identity.items():
        facts = case.get("costFacts")
        if not isinstance(facts, list):
            authoritative = False
            continue
        fact_items = cast("list[object]", facts)
        if len(fact_items) != CALLS_PER_CASE:
            authoritative = False
            continue
        for fact in fact_items:
            fact_values = cast("dict[str, object]", fact) if isinstance(fact, dict) else None
            actual = fact_values.get("actualCostMicros") if fact_values is not None else None
            if not isinstance(actual, int) or isinstance(actual, bool):
                authoritative = False
                continue
            actual_totals[variant] += actual
    winner: str | None = None
    if authoritative:
        minimum = min(actual_totals.values())
        winners = [variant for variant, total in actual_totals.items() if total == minimum]
        winner = winners[0] if len(winners) == 1 else None
    return {
        "outputDifferencesVsA": output_differences,
        "latencyStaircase": latency_staircase,
        "actualCostTotalsMicros": actual_totals if authoritative else None,
        "actualCostWinner": winner,
    }


def write_private_json(path: Path, value: object) -> None:
    """Atomically preserve a finite report as mode 0600."""
    _write_private(path, json.loads(canonical_json(value)))
