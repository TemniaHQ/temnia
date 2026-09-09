from __future__ import annotations

import hashlib
import json
import stat
import uuid
from typing import TYPE_CHECKING, Literal, cast

import pytest
from pydantic import ValidationError

from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.speech_benchmark import (
    EXPERIMENT_DISPATCH_CAP,
    BenchmarkDeploymentManifest,
    BenchmarkVariant,
    acknowledge_completed_long_a,
    assert_benchmark_environment,
    assert_case_completed,
    begin_cache_only_recovery,
    benchmark_summary,
    build_journal,
    experiment_lease,
    finish_cache_only_recovery,
    finish_case,
    initialize_journal,
    measured_resource_estimate_micros,
    read_journal,
    reserve_case,
    resume_experiment_lease,
    validate_cache_only_recovery_journal,
    validate_completed_long_a_journal,
    validate_completed_long_a_transform,
)

if TYPE_CHECKING:
    from pathlib import Path


def model_manifest(*, image_assets: str | None = "b" * 64) -> SpeechModelManifest:
    return SpeechModelManifest(
        sha256="a" * 64,
        file_count=25,
        total_bytes=6_625_179_753,
        model_root=f"/models/frozen/{'a' * 64}",
        image_assets_sha256=image_assets,
    )


def profile(
    cpu: Literal[4, 8], progress: Literal["coalesced", "synchronous_control"]
) -> SpeechResourceProfile:
    return SpeechResourceProfile(
        cpu_cores=cpu,
        stage_timeout_seconds=900,
        progress_mode=progress,
    )


def deployment_manifest() -> BenchmarkDeploymentManifest:
    return BenchmarkDeploymentManifest(
        source_build_id="c" * 64,
        model_manifest=model_manifest(),
        variants=(
            BenchmarkVariant(
                id="A",
                app="benchmark-a",
                resource_profile=profile(4, "synchronous_control"),
                execution_topology="serial",
                rate_micros_per_hour=1_250_000,
            ),
            BenchmarkVariant(
                id="B",
                app="benchmark-b",
                resource_profile=profile(4, "coalesced"),
                execution_topology="serial",
                rate_micros_per_hour=1_250_000,
            ),
            BenchmarkVariant(
                id="C",
                app="benchmark-c",
                resource_profile=profile(8, "coalesced"),
                execution_topology="serial",
                rate_micros_per_hour=1_450_000,
            ),
            BenchmarkVariant(
                id="D",
                app="benchmark-c",
                resource_profile=profile(8, "coalesced"),
                execution_topology="parallel",
                rate_micros_per_hour=1_450_000,
            ),
        ),
    )


def test_finite_matrix_has_exact_resource_prices_and_total_exposure() -> None:
    manifest = deployment_manifest()
    assert [variant.reservation_micros for variant in manifest.variants] == [
        354_167,
        354_167,
        410_834,
        410_834,
    ]
    assert [variant.case_exposure_micros for variant in manifest.variants] == [
        1_062_501,
        1_062_501,
        1_232_502,
        1_232_502,
    ]
    assert manifest.total_exposure_micros == 13_770_018

    with pytest.raises(ValidationError, match="reviewed benchmark matrix"):
        BenchmarkVariant(
            id="D",
            app="benchmark-c",
            resource_profile=profile(4, "coalesced"),
            execution_topology="parallel",
            rate_micros_per_hour=1_250_000,
        )
    values = manifest.model_dump(mode="json", by_alias=True)
    values["modelManifest"]["imageAssetsSha256"] = None
    with pytest.raises(ValidationError, match="image asset identity"):
        BenchmarkDeploymentManifest.model_validate(values)


def test_journal_persists_fixed_order_and_never_recycles_exposure(tmp_path: Path) -> None:
    manifest = deployment_manifest()
    path = tmp_path / "private" / "experiment.json"
    journal = build_journal(
        experiment_id="bench-20260909",
        organization_id=uuid.uuid4(),
        manifest=manifest,
    )
    assert [(case.kind, case.variant_id, case.block) for case in journal.cases] == [
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
    ]
    lock_root = tmp_path / "locks"
    with experiment_lease(journal.experiment_id, lock_root=lock_root) as lease:
        lease.mark_admitted(journal_path=path, manifest_sha256=manifest.sha256)
        initialize_journal(path, journal, lease=lease)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        with pytest.raises(FileExistsError):
            initialize_journal(path, journal, lease=lease)
        with (
            pytest.raises(RuntimeError, match="already running"),
            experiment_lease(journal.experiment_id, lock_root=lock_root),
        ):
            pass
        with pytest.raises(ValueError, match="in order"):
            reserve_case(path, journal.cases[1].key, lease=lease)

        for case in journal.cases:
            admitted = reserve_case(path, case.key, lease=lease)
            before = admitted.reserved_exposure_micros
            finished = finish_case(
                path,
                case.key,
                error_type="MeasuredFailure" if case.key == "long-a-2" else None,
                lease=lease,
            )
            assert finished.reserved_exposure_micros == before
    final = read_journal(path)
    assert final.reserved_exposure_micros == manifest.total_exposure_micros
    assert final.reserved_dispatches == EXPERIMENT_DISPATCH_CAP
    assert final.cases[-1].status == "failed"
    with (
        pytest.raises(ValueError, match="already admitted"),
        experiment_lease(journal.experiment_id, lock_root=lock_root),
    ):
        pass


def test_failed_first_case_resumes_once_without_recycling_exposure(tmp_path: Path) -> None:
    manifest = deployment_manifest()
    path = tmp_path / "private" / "experiment.json"
    lock_root = tmp_path / "locks"
    organization_id = uuid.uuid4()
    journal = build_journal(
        experiment_id="bench-recovery-20260909",
        organization_id=organization_id,
        manifest=manifest,
    )
    with experiment_lease(journal.experiment_id, lock_root=lock_root) as lease:
        lease.mark_admitted(journal_path=path, manifest_sha256=manifest.sha256)
        initialize_journal(path, journal, lease=lease)
        reserve_case(path, "preflight-a", lease=lease)
        finish_case(path, "preflight-a", error_type="UndefinedColumn", lease=lease)
    original = path.read_bytes()

    with resume_experiment_lease(
        journal.experiment_id,
        journal_path=path,
        manifest_sha256=manifest.sha256,
        lock_root=lock_root,
    ) as lease:
        admitted = read_journal(path)
        validate_cache_only_recovery_journal(
            admitted, organization_id=organization_id, manifest=manifest
        )
        recovering = begin_cache_only_recovery(
            path,
            "preflight-a",
            worker_source_build_id="d" * 64,
            deployment_source_build_id=manifest.source_build_id,
            lease=lease,
        )
        assert recovering.reserved_exposure_micros == 1_062_501
        assert recovering.reserved_dispatches == 3
        assert recovering.initial_worker_source_build_id == manifest.source_build_id
        assert recovering.worker_source_build_id == "d" * 64
        assert recovering.cases[0].error_type == "UndefinedColumn"
        with pytest.raises(ValueError, match="terminal failed"):
            begin_cache_only_recovery(
                path,
                "preflight-a",
                worker_source_build_id="d" * 64,
                deployment_source_build_id=manifest.source_build_id,
                lease=lease,
            )
        recovery_run_id = uuid.uuid4()
        completed = finish_cache_only_recovery(
            path,
            "preflight-a",
            recovery_run_id=recovery_run_id,
            receipt_sha256="e" * 64,
            lease=lease,
        )
        assert completed.cases[0].status == "completed"
        assert completed.cases[0].error_type == "UndefinedColumn"
        assert completed.cases[0].recovery_run_id == recovery_run_id
        next_case = reserve_case(path, "preflight-b", lease=lease)
        assert next_case.reserved_dispatches == 6

    assert original != path.read_bytes()
    with (
        pytest.raises(ValueError, match="admission marker"),
        resume_experiment_lease(
            journal.experiment_id,
            journal_path=tmp_path / "different.json",
            manifest_sha256=manifest.sha256,
            lock_root=lock_root,
        ),
    ):
        pytest.fail("mismatched journal unexpectedly acquired recovery lease")


def test_environment_guard_binds_database_namespace_and_experiment() -> None:
    assert_benchmark_environment(
        "postgresql://host/temnia_speech_benchmark_bench_20260909",
        "temnia-speech-benchmark-bench-20260909",
        "bench-20260909",
    )
    with pytest.raises(ValueError, match="database"):
        assert_benchmark_environment(
            "postgresql://host/temnia",
            "temnia-speech-benchmark-bench-20260909",
            "bench-20260909",
        )
    with pytest.raises(ValueError, match="namespace"):
        assert_benchmark_environment(
            "postgresql://host/temnia_speech_benchmark_bench_20260909",
            "default",
            "bench-20260909",
        )


def test_measured_cost_is_a_precise_resource_floor_estimate() -> None:
    four_cpu = profile(4, "coalesced")
    assert four_cpu.minimum_rate_micros_per_hour == 1_115_712
    assert measured_resource_estimate_micros(four_cpu, 1.000_001) == 310
    with pytest.raises(ValueError, match="finite and nonnegative"):
        measured_resource_estimate_micros(four_cpu, float("nan"))


def test_case_completion_requires_exact_three_calls_and_retains_unknown_cost() -> None:
    variant = deployment_manifest().variants[3]
    attempts: list[dict[str, object]] = [
        {
            "id": str(uuid.uuid4()),
            "stage": stage,
            "state": "succeeded",
            "dispatched_at": "2026-09-09T00:00:00Z",
            "estimated_cost_micros": variant.reservation_micros,
            "reservation_state": "active",
            "actual_cost_micros": None,
            "cost_status": "unknown",
            "usage": {
                "telemetry": {
                    "complete": True,
                    "elapsedSeconds": 10.5,
                    "metricsUnavailable": list[str](),
                }
            },
        }
        for stage in ("recognize", "align", "speaker_turns")
    ]
    report: dict[str, object] = {
        "run": {
            "status": "ready",
            "budget_micros": variant.case_exposure_micros,
            "dispatch_count": 3,
            "reserved_micros": variant.case_exposure_micros,
        },
        "attempts": attempts,
        "transcript": {"status": "ready"},
        "workflow": {
            "activityAttempts": [1, 2],
            "observedDispatchCounts": [3, 3],
            "transcriptSha256": "d" * 64,
            "functionElapsedSeconds": 31.5,
        },
    }
    assert_case_completed(report, variant=variant, require_lost_result=True)
    attempts[2]["stage"] = "diarize"
    with pytest.raises(ValueError, match="stage set"):
        assert_case_completed(report, variant=variant, require_lost_result=True)


def test_case_completion_refuses_reused_checkpoint_without_original_metrics() -> None:
    variant = deployment_manifest().variants[3]
    attempts: list[dict[str, object]] = [
        {
            "id": str(uuid.uuid4()),
            "stage": stage,
            "state": "succeeded",
            "dispatched_at": "2026-09-09T00:00:00Z",
            "estimated_cost_micros": variant.reservation_micros,
            "reservation_state": "active",
            "actual_cost_micros": None,
            "cost_status": "unknown",
            "usage": {
                "telemetry": {
                    "complete": stage != "recognize",
                    "elapsedSeconds": 0.1 if stage == "recognize" else 10.0,
                    "metricsUnavailable": (
                        ["checkpoint_reused_without_original_container_metrics"]
                        if stage == "recognize"
                        else list[str]()
                    ),
                }
            },
        }
        for stage in ("recognize", "align", "speaker_turns")
    ]
    report: dict[str, object] = {
        "run": {
            "status": "ready",
            "budget_micros": variant.case_exposure_micros,
            "dispatch_count": 3,
            "reserved_micros": variant.case_exposure_micros,
        },
        "attempts": attempts,
        "transcript": {"status": "ready"},
        "workflow": {
            "activityAttempts": [1],
            "observedDispatchCounts": [3],
            "transcriptSha256": "d" * 64,
            "functionElapsedSeconds": None,
        },
    }

    with pytest.raises(ValueError, match="unsuitable for a controlled measurement"):
        assert_case_completed(report, variant=variant, require_lost_result=False)


def test_manifest_wire_is_finite_and_does_not_contain_credentials() -> None:
    body = deployment_manifest().model_dump_json(by_alias=True)
    parsed = json.loads(body)
    assert parsed["format"] == "temnia-speech-benchmark/1"
    assert "token" not in body.lower()
    assert "secret" not in body.lower()


def test_summary_compares_private_identities_and_withholds_unknown_cost_winner() -> None:
    cases: list[dict[str, object]] = []
    for index, (kind, variant, block) in enumerate(
        (
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
    ):
        cases.append(
            {
                "kind": kind,
                "variant": variant,
                "block": block,
                "wallSeconds": 100 - index,
                "functionElapsedSeconds": 90 - index,
                "outputFacts": {
                    "language": "hi",
                    "wordCount": 10,
                    "speakerCount": 2,
                    "firstWordStartMs": 0,
                    "lastWordEndMs": 1000,
                    "tokenSha256": "a" * 64,
                    "timingSha256": "b" * 64,
                    "speakerSha256": "c" * 64,
                },
                "costFacts": [
                    {"actualCostMicros": None},
                    {"actualCostMicros": None},
                    {"actualCostMicros": None},
                ],
            }
        )

    summary = benchmark_summary(cases)

    assert summary["actualCostWinner"] is None
    assert summary["actualCostTotalsMicros"] is None
    assert len(cast("list[object]", summary["latencyStaircase"])) == 6
    differences = cast("list[dict[str, object]]", summary["outputDifferencesVsA"])
    assert all(all(cast("dict[str, bool]", item["same"]).values()) for item in differences)


def test_completed_long_a_acknowledgment_preserves_admission_and_only_advances_case(
    tmp_path: Path,
) -> None:
    manifest = deployment_manifest()
    organization_id = uuid.uuid4()
    journal = build_journal(
        experiment_id="bench-completed-20260909",
        organization_id=organization_id,
        manifest=manifest,
    )
    cases = tuple(
        case.model_copy(
            update={
                "status": "completed" if index < 4 else "failed",
                "error_type": (
                    "UndefinedColumn" if index == 0 else "ValueError" if index == 4 else None
                ),
            }
        )
        if index <= 4
        else case
        for index, case in enumerate(journal.cases)
    )
    failed = journal.model_copy(
        update={
            "reserved_exposure_micros": 5_652_507,
            "reserved_dispatches": 15,
            "worker_source_build_id": "d" * 64,
            "cases": cases,
        }
    )
    validate_completed_long_a_journal(
        failed,
        organization_id=organization_id,
        manifest=manifest,
        previous_worker_source_build_id="d" * 64,
    )
    path = tmp_path / "experiment.json"
    lock_root = tmp_path / "locks"
    with experiment_lease(failed.experiment_id, lock_root=lock_root) as lease:
        lease.mark_admitted(journal_path=path, manifest_sha256=manifest.sha256)
        initialize_journal(path, failed, lease=lease)
    failed_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with resume_experiment_lease(
        failed.experiment_id,
        journal_path=path,
        manifest_sha256=manifest.sha256,
        lock_root=lock_root,
    ) as lease:
        completed = acknowledge_completed_long_a(
            path,
            expected_journal_sha256=failed_sha,
            previous_worker_source_build_id="d" * 64,
            worker_source_build_id="e" * 64,
            lease=lease,
        )
    assert completed.reserved_exposure_micros == 5_652_507
    assert completed.reserved_dispatches == 15
    assert completed.cases[4].status == "completed"
    assert completed.cases[4].error_type == "ValueError"
    assert all(case.status == "planned" for case in completed.cases[5:])
    validate_completed_long_a_transform(
        original=failed,
        current=completed,
        worker_source_build_id="e" * 64,
    )
    with pytest.raises(ValueError, match="unreviewed mutation"):
        validate_completed_long_a_transform(
            original=failed,
            current=completed.model_copy(update={"reserved_dispatches": 16}),
            worker_source_build_id="e" * 64,
        )


def test_summary_withholds_only_contaminated_wall_comparison() -> None:
    cases: list[dict[str, object]] = []
    for index, (kind, variant, block) in enumerate(
        (
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
    ):
        cases.append(
            {
                "kind": kind,
                "variant": variant,
                "block": block,
                "wallSeconds": None if (kind, variant, block) == ("long", "A", 1) else 100 - index,
                "wallTimingStatus": (
                    "activity_retry_contaminated"
                    if (kind, variant, block) == ("long", "A", 1)
                    else "usable"
                ),
                "functionElapsedSeconds": 90 - index,
                "outputFacts": {"language": "en", "wordCount": 1, "speakerCount": 1},
                "costFacts": [{"actualCostMicros": None}] * 3,
            }
        )
    summary = benchmark_summary(cases)
    staircase = cast("list[dict[str, object]]", summary["latencyStaircase"])
    assert staircase[0]["wallTimingStatus"] == "unavailable"
    assert staircase[0]["wallSecondsBefore"] is None
    assert staircase[0]["functionSecondsBefore"] == 86.0
    assert all(row["wallTimingStatus"] == "usable" for row in staircase[1:])
