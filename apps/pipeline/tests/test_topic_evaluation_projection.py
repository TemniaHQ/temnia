"""Production-shaped export projections preserve controlled-comparison factors.

Database/object reads are mocked; all content and human labels are synthetic fixtures.
"""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001, TC003
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from temnia_pipeline.contracts import Scope
from temnia_pipeline.evals.topic_cli import private_json
from temnia_pipeline.evals.topic_comparison import (
    ComparisonInput,
    TopicComparisonManifest,
    TopicComparisonReport,
    compare_topics,
)
from temnia_pipeline.evals.topics import (
    TopicEvaluationBundle,
    TopicProgramManifest,
    TopicProgramStage,
    digest,
    validate_topic_bundle,
)
from temnia_pipeline.harness import topic_bundle_export as exporter
from temnia_pipeline.harness.routes import RouteEntry, SeatRoutePool
from test_harness_settings import route as fixture_route
from test_harness_settings import snapshot as fixture_snapshot
from test_topic_evaluation import NOW, _bundle, _labels


def _programme() -> TopicProgramManifest:
    return TopicProgramManifest(
        policy="standalone-topics/1",
        implementation_sha256="c" * 64,
        program_version="standalone-topics/1",
        stages={
            name: TopicProgramStage(
                seat=cast("Any", seat),
                prompt_version=f"fixture-{name}/1",
                prompt_template_sha256=digest({"prompt": name}),
                schema_version=f"fixture-{name}-schema/1",
                native_schema_sha256=digest({"schema": name}),
            )
            for name, seat in (
                ("topic_author", "author"),
                ("topic_cold", "reviewer"),
                ("topic_source", "reviewer"),
                ("topic_patch", "author"),
            )
        },
    )


def _snapshot(
    original: TopicEvaluationBundle, *, author: str = "author-a", repair: bool = False
) -> tuple[exporter._TopicSnapshot, dict[UUID, object]]:
    rows: dict[UUID, dict[str, Any]] = {}
    bodies: dict[UUID, object] = {}
    for ordinal, artifact in enumerate(original.artifacts):
        rows[artifact.id] = {
            "id": artifact.id,
            "source_id": artifact.source_id,
            "sha256": artifact.sha256,
            "fingerprint": artifact.fingerprint,
            "kind": artifact.kind,
            "size_bytes": artifact.size_bytes,
            "storage_key": artifact.storage_key,
            "metadata": dict(artifact.metadata),
            "created_at": NOW.replace(microsecond=ordinal),
        }
        bodies[artifact.id] = artifact.body
    evidence = original.artifacts[0]
    assert original.evidence is not None
    rows[evidence.id]["metadata"].update(
        {
            "sourceSha256": "d" * 64,
            "sourceFingerprint": original.evidence.sourceFingerprint,
            "transcriptSha256": original.evidence.transcriptSha256,
        }
    )
    routes = {
        seat: {
            **fixture_route(route_id=route).model_dump(mode="json"),
            "id": route,
            "gateway_model": f"fixture/{route}",
            "provider": "fixture-provider",
            "family": f"fixture-{seat}",
            "reasoning_effort": "high",
            "max_output_tokens": 8192,
        }
        for seat, route in (("author", author), ("reviewer", "reviewer-fixed"))
    }
    third = fixture_route(route_id="third-qualified-family")
    frozen_routes = fixture_snapshot(
        routes=(
            *tuple(RouteEntry.model_validate_json(json.dumps(value)) for value in routes.values()),
            third,
        ),
        synthetic=False,
    )
    frozen_routes = frozen_routes.model_copy(
        update={
            "seats": {
                **frozen_routes.seats,
                "verify": SeatRoutePool(route_ids=("reviewer-fixed", author, third.id)),
            }
        }
    )
    frozen_routes = frozen_routes.model_copy(update={"snapshot_id": frozen_routes.computed_id()})
    route_snapshot = frozen_routes.model_dump(mode="json")
    program = _programme()
    program_body = program.model_dump(mode="json", by_alias=True)
    attempts: list[dict[str, Any]] = []
    executed = ["topic_author", "topic_cold", "topic_source"] + (["topic_patch"] if repair else [])
    for name in executed:
        stage = program.stages[name]
        route = routes[stage.seat]
        identifier = uuid4()
        rows[identifier] = {
            "id": identifier,
            "source_id": original.source_id,
            "sha256": digest({"response": name, "author": author}),
            "fingerprint": digest({"request": name, "author": author}),
            "kind": "model_response",
            "size_bytes": 10,
            "storage_key": f"source/{original.source_id}/response/{identifier}",
            "metadata": {
                "programVersion": program.program_version,
                "promptVersion": stage.prompt_version,
                "schemaVersion": stage.schema_version,
                "promptTemplateSha256": stage.prompt_template_sha256,
                "nativeSchemaSha256": stage.native_schema_sha256,
            },
            "created_at": NOW,
        }
        attempts.append(
            {
                "id": uuid4(),
                "operation_id": uuid4(),
                "attempt_number": 1,
                "state": "succeeded",
                "provider": "synthetic-recorded",
                "model": route["gateway_model"],
                "family": route["family"],
                "route": route,
                "operation_stage": f"verify:{name}"
                if stage.seat == "reviewer"
                else f"propose:{name}",
                "estimated_cost_micros": 0,
                "actual_cost_micros": 0,
                "cost_status": "reported",
                "reservation_state": "settled",
                "result_artifact_id": identifier,
                "usage": {},
                "dispatched_at": NOW,
                "finished_at": NOW,
            }
        )
    source = {
        "id": original.source_id,
        "duration_ms": original.duration_ms,
        "master_key": f"org/source/{original.source_id}/master.mp4",
        "size_bytes": 4096,
    }
    return exporter._TopicSnapshot(
        run={
            "id": original.run_id,
            "source_id": original.source_id,
            "current_revision": 1,
            "accepted_revision": None,
            "evidence_artifact_id": evidence.id,
            "route_snapshot": {
                "editorialPolicy": "standalone-topics/1",
                "initialBudgetMicros": 1000,
                "pinnedSource": {
                    "storage_key": source["master_key"],
                    "size_bytes": 4096,
                    "duration_ms": original.duration_ms,
                },
                "pinnedTranscript": {"sha256": original.evidence.transcriptSha256},
                "snapshot": route_snapshot,
                "evaluationProgram": program_body,
                "evaluationProgramSha256": digest(program_body),
            },
            "brief": "Generic standalone discussions",
            "status": "needs_review",
            "config": {
                "backend": "gateway",
                "routeSnapshotId": frozen_routes.snapshot_id,
                "evidenceWindowSentences": 80,
                "maxDispatches": 128,
                "maxOutputTokens": 8192,
                "maxRenderConcurrency": 2,
                "maxRepairs": 3,
            },
        },
        source=source,
        observed={"observed_at": NOW},
        revision_rows=[
            {
                "revision": 1,
                "artifact_id": original.revisions[0].artifact_id,
                "base_revision": None,
                "created_at": NOW,
            }
        ],
        events=[],
        attempts=attempts,
        rows=rows,
        dependencies={artifact.id: artifact.dependencies for artifact in original.artifacts},
    ), bodies


async def _export(
    monkeypatch: pytest.MonkeyPatch, snapshot: exporter._TopicSnapshot, bodies: dict[UUID, object]
) -> TopicEvaluationBundle:
    async def read_snapshot(*_args: object, **_kwargs: object) -> exporter._TopicSnapshot:
        return snapshot

    async def read_body(*_args: object, **kwargs: object) -> object:
        return bodies[cast("UUID", kwargs["artifact_id"])]

    monkeypatch.setattr(exporter, "_read_topic_snapshot", read_snapshot)
    monkeypatch.setattr(exporter.artifacts, "read_artifact_json", read_body)
    return await exporter.export_topic_bundle(
        "unused",
        scope=Scope(organizationId=uuid4(), userId=uuid4()),
        store=cast("Any", object()),
        run_id=snapshot.run["id"],
        split="development",
        recording_group="one-synthetic-episode",
    )


def _compare(
    tmp_path: Path, bundles: list[TopicEvaluationBundle], *, configuration: bool = False
) -> TopicComparisonReport:
    inputs: list[ComparisonInput] = []
    for index, bundle in enumerate(bundles):
        labels = _labels(bundle)
        labels = labels.model_copy(
            update={
                "rubric_sha256": bundle.configuration.rubric_sha256,
                "judgments": tuple(
                    j.model_copy(update={"rubric_sha256": bundle.configuration.rubric_sha256})
                    for j in labels.judgments
                ),
            }
        )
        bundle_body, labels_body = (
            bundle.model_dump(mode="json", by_alias=True),
            labels.model_dump(mode="json", by_alias=True),
        )
        bundle_path, labels_path = f"bundle-{index}.json", f"labels-{index}.json"
        private_json(tmp_path / bundle_path, bundle_body)
        private_json(tmp_path / labels_path, labels_body)
        inputs.append(
            ComparisonInput(
                bundle=bundle_path,
                labels=labels_path,
                expected_bundle_sha256=digest(bundle_body),
                expected_labels_sha256=digest(labels_body),
            )
        )
    return compare_topics(
        TopicComparisonManifest(
            experiment_id="synthetic-production-projection",
            mode="configuration" if configuration else "model_swap",
            baseline_configuration_id=bundles[0].configuration.configuration_id,
            allowed_changed_factors=("author_identity", "execution_identity")
            if configuration
            else ("author_identity",),
            inputs=tuple(inputs),
            rationale="Synthetic exporter regression, not measured model performance.",
        ),
        directory=tmp_path,
    )


@pytest.mark.asyncio
async def test_exported_model_swap_keeps_conditional_repair_out_of_fixed_factors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = _bundle()
    base, challenger = _snapshot(original), _snapshot(original, author="author-b", repair=True)
    bundles = [await _export(monkeypatch, *pair) for pair in (base, challenger)]
    report = _compare(tmp_path, bundles)
    assert report.comparable, report.reasons
    assert all(bundle.configuration.source_sha256 == "d" * 64 for bundle in bundles)
    assert bundles[0].configuration.prompt_identity == bundles[1].configuration.prompt_identity
    assert bundles[0].configuration.schema_identity == bundles[1].configuration.schema_identity
    assert len(bundles[0].configuration.observed_call_identities) == 3
    assert len(bundles[1].configuration.observed_call_identities) == 4
    assert (
        bundles[0].configuration.retained_run_configuration["routeSnapshotId"]
        != bundles[1].configuration.retained_run_configuration["routeSnapshotId"]
    )
    assert not report.production_qualification_ready


@pytest.mark.parametrize("factor", ["reviewer", "settings", "prompt", "schema"])
@pytest.mark.asyncio
async def test_export_still_detects_unintended_fixed_factor_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, factor: str
) -> None:
    original = _bundle()
    base, challenger = _snapshot(original), _snapshot(original, author="author-b")
    snapshot = challenger[0]
    if factor == "reviewer":
        for attempt in snapshot.attempts:
            if attempt["operation_stage"].startswith("verify"):
                attempt["route"]["reasoning_effort"] = "low"
    elif factor == "settings":
        snapshot.run["config"]["maxOutputTokens"] = 4096
    else:
        key = "promptTemplateSha256" if factor == "prompt" else "nativeSchemaSha256"
        programme = snapshot.run["route_snapshot"]["evaluationProgram"]
        programme["stages"]["topic_author"][key] = "e" * 64
        snapshot.run["route_snapshot"]["evaluationProgramSha256"] = digest(programme)
        for attempt in snapshot.attempts:
            if attempt["operation_stage"] == "propose:topic_author":
                snapshot.rows[attempt["result_artifact_id"]]["metadata"][key] = "e" * 64
    report = _compare(tmp_path, [await _export(monkeypatch, *pair) for pair in (base, challenger)])
    assert not report.comparable
    expected = {
        "reviewer": "reviewer_identity",
        "settings": "execution_identity",
        "prompt": "prompt_identity",
        "schema": "schema_identity",
    }[factor]
    assert any(f"changed_fixed_factor:{expected}" in reason for reason in report.reasons)


@pytest.mark.parametrize(
    "conflict",
    ["source_hash", "fingerprint", "source_id", "runtime_id", "native_hash", "manifest_hash"],
)
@pytest.mark.asyncio
async def test_export_refuses_conflicting_source_or_runtime_identities(
    monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    original = _bundle()
    snapshot, bodies = _snapshot(original)
    if conflict == "source_hash":
        snapshot.run["route_snapshot"]["pinnedSource"]["sha256"] = "e" * 64
    elif conflict == "fingerprint":
        snapshot.rows[original.artifacts[0].id]["metadata"]["sourceFingerprint"] = "e" * 64
    elif conflict == "source_id":
        snapshot.source["id"] = uuid4()
    elif conflict in {"runtime_id", "native_hash"}:
        key = "promptVersion" if conflict == "runtime_id" else "nativeSchemaSha256"
        snapshot.rows[snapshot.attempts[0]["result_artifact_id"]]["metadata"][key] = "e" * 64
    else:
        snapshot.run["route_snapshot"]["evaluationProgramSha256"] = "e" * 64
    with pytest.raises(ValueError, match=r"(source|evidence|programme|prompt|schema)"):
        await _export(monkeypatch, snapshot, bodies)


@pytest.mark.asyncio
async def test_historical_missing_roster_is_not_inferred_from_observed_response_subset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = _bundle()
    pairs = [_snapshot(original), _snapshot(original, author="author-b", repair=True)]
    for snapshot, _ in pairs:
        del snapshot.run["route_snapshot"]["evaluationProgram"]
        del snapshot.run["route_snapshot"]["evaluationProgramSha256"]
        del snapshot.rows[original.artifacts[0].id]["metadata"]["sourceSha256"]
    bundles = [await _export(monkeypatch, *pair) for pair in pairs]
    assert all(bundle.configuration.source_sha256 is None for bundle in bundles)
    assert all(not bundle.configuration.prompt_identity for bundle in bundles)
    assert all(bundle.configuration.observed_call_identities for bundle in bundles)
    report = _compare(tmp_path, bundles)
    assert not report.comparable
    assert any("unobserved_factor:prompt_identity" in reason for reason in report.reasons)


def test_v2_programme_cannot_omit_optional_repair_from_frozen_roster() -> None:
    body = deepcopy(_programme().model_dump(mode="json", by_alias=True))
    body["policy"] = "standalone-topics/2"
    del body["stages"]["topic_patch"]
    with pytest.raises(ValueError, match="four-stage"):
        TopicProgramManifest.model_validate(body)


@pytest.mark.asyncio
async def test_archived_null_source_projection_remains_readable_and_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = _bundle()
    bundles: list[TopicEvaluationBundle] = []
    for author in ("author-a", "author-b"):
        bundle = await _export(monkeypatch, *_snapshot(original, author=author))
        body = bundle.model_dump(mode="json", by_alias=True)
        config = body["configuration"]
        config["sourceSha256"] = None
        del config["executionIdentity"]["effectiveOutputTokens"]
        for field in (
            "intendedProgram",
            "intendedProgramSha256",
            "observedCallIdentities",
            "retainedRunConfiguration",
        ):
            del config[field]
        config["programIdentity"] = {
            "policy": "standalone-topics/1",
            "programs": ["standalone-topics/1"],
        }
        config["promptIdentity"] = {"versions": ["standalone-topic-editor/1"]}
        config["schemaIdentity"] = {"versions": ["standalone-topic-editor/1"]}
        archived = TopicEvaluationBundle.model_validate_json(json.dumps(body))
        validate_topic_bundle(archived)
        assert archived.configuration.source_sha256 is None
        bundles.append(archived)
    report = _compare(tmp_path, bundles)
    assert not report.comparable
    assert any("unmatched_identity:source_sha256" in reason for reason in report.reasons)
    assert any("unobserved_intended_program" in reason for reason in report.reasons)


@pytest.mark.parametrize(
    "field", ["program_identity", "prompt_identity", "schema_identity", "observed_call_identities"]
)
@pytest.mark.asyncio
async def test_report_roundtrip_refuses_explicit_programme_identity_contradictions(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    bundle = await _export(monkeypatch, *_snapshot(_bundle()))
    if field == "observed_call_identities":
        observed = deepcopy(bundle.configuration.observed_call_identities)
        cast("dict[str, Any]", observed[0]["identities"])["nativeSchemaSha256"] = "e" * 64
        change = observed
    else:
        change = {"incorrect": "known contradictory factor"}
    updated = bundle.model_copy(
        update={"configuration": bundle.configuration.model_copy(update={field: change})}
    )
    roundtrip = TopicEvaluationBundle.model_validate_json(updated.model_dump_json(by_alias=True))
    with pytest.raises(ValueError, match=r"(programme|manifest|schema)"):
        validate_topic_bundle(roundtrip)


def _v2_output_profile(
    original: TopicEvaluationBundle, *, author: str, author_output: int
) -> tuple[exporter._TopicSnapshot, dict[UUID, object]]:
    value, bodies = _snapshot(original, author=author)
    wrapper = value.run["route_snapshot"]
    wrapper["editorialPolicy"] = "standalone-topics/2"
    program = wrapper["evaluationProgram"]
    program["policy"] = program["programVersion"] = "standalone-topics/2"
    wrapper["evaluationProgramSha256"] = digest(program)
    for row in value.attempts:
        value.rows[row["result_artifact_id"]]["metadata"]["programVersion"] = "standalone-topics/2"
    routes = wrapper["snapshot"]["routes"]
    for route in routes:
        route["context_tokens"] = 131072
        route["max_output_tokens"] = author_output if route["id"] == author else 32768
    for row in value.attempts:
        row["route"].update(next(route for route in routes if route["id"] == row["route"]["id"]))
    frozen = wrapper["snapshot"]
    frozen["snapshot_id"] = digest(
        {key: item for key, item in frozen.items() if key not in {"snapshot_id", "signature"}}
    )
    value.run["config"]["routeSnapshotId"] = frozen["snapshot_id"]
    value.run["config"]["maxOutputTokens"] = 32768
    return value, bodies


@pytest.mark.parametrize("author_output", [8192, 32768])
async def test_export_to_compare_keeps_output_profile_as_fixed_execution_factor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, author_output: int
) -> None:
    original = _bundle()
    pairs = [
        _v2_output_profile(original, author="author-a", author_output=32768),
        _v2_output_profile(original, author="author-b", author_output=author_output),
    ]
    bundles = [await _export(monkeypatch, *pair) for pair in pairs]
    expected = {"author": author_output, "reviewer": 32768}
    assert bundles[1].configuration.execution_identity["effectiveOutputTokens"] == expected
    comparison = _compare(tmp_path, bundles)
    assert comparison.comparable is (author_output == 32768)
    if author_output == 8192:
        assert any(
            "changed_fixed_factor:execution_identity" in reason for reason in comparison.reasons
        )
        declared = tmp_path / "configuration"
        declared.mkdir()
        result = _compare(declared, bundles, configuration=True)
        assert result.comparable, result.reasons


def _explicit_transport(value: exporter._TopicSnapshot, *, author_timeout: float = 300.0) -> None:
    frozen = value.run["route_snapshot"]["snapshot"]
    for route in frozen["routes"]:
        route["transport"] = {
            "version": "gateway-transport/1",
            "gateway": "openrouter",
            "mode": "streaming",
            "request_timeout_seconds": author_timeout
            if route["id"].startswith("author-")
            else 300.0,
            "total_timeout_seconds": 540.0,
        }
        route["provider_accounting_name"] = "Fixture Provider"
    for attempt in value.attempts:
        attempt["route"].update(
            next(route for route in frozen["routes"] if route["id"] == attempt["route"]["id"])
        )
    frozen["snapshot_id"] = digest(
        {key: item for key, item in frozen.items() if key not in {"snapshot_id", "signature"}}
    )
    value.run["config"]["routeSnapshotId"] = frozen["snapshot_id"]


@pytest.mark.parametrize("author_timeout", [240.0, 300.0])
async def test_transport_change_is_an_execution_factor_through_real_export_and_compare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, author_timeout: float
) -> None:
    pairs = [_snapshot(_bundle(), author=name) for name in ("author-a", "author-b")]
    _explicit_transport(pairs[0][0])
    _explicit_transport(pairs[1][0], author_timeout=author_timeout)
    bundles = [await _export(monkeypatch, *pair) for pair in pairs]
    report = _compare(tmp_path, bundles)
    assert report.comparable is (author_timeout == 300.0), report.reasons
    if author_timeout != 300.0:
        assert any("changed_fixed_factor:execution_identity" in reason for reason in report.reasons)
        declared = tmp_path / "configuration"
        declared.mkdir()
        assert _compare(declared, bundles, configuration=True).comparable


async def test_missing_historical_transport_stays_unknown_in_new_transport_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pairs = [_snapshot(_bundle(), author=name) for name in ("author-a", "author-b")]
    _explicit_transport(pairs[1][0])
    bundles = [await _export(monkeypatch, *pair) for pair in pairs]
    report = _compare(tmp_path, bundles, configuration=True)
    assert not report.comparable
    assert any("unobserved_transport:" in reason for reason in report.reasons)
    assert bundles[0].configuration.execution_identity["transport"] == {
        "author": None,
        "reviewer": None,
    }


async def test_bundle_refuses_forged_transport_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, bodies = _snapshot(_bundle())
    _explicit_transport(snapshot)
    bundle = await _export(monkeypatch, snapshot, bodies)
    execution = deepcopy(bundle.configuration.execution_identity)
    cast("dict[str, Any]", execution["transport"])["author"]["request_timeout_seconds"] = 240.0
    changed = bundle.model_copy(
        update={
            "configuration": bundle.configuration.model_copy(
                update={"execution_identity": execution}
            )
        }
    )
    with pytest.raises(ValueError, match="transport projection contradicts"):
        validate_topic_bundle(changed)


async def test_provider_required_output_spelling_stays_with_role_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pairs = [_snapshot(_bundle(), author=name) for name in ("author-a", "author-b")]
    for value, _ in pairs:
        _explicit_transport(value)
        frozen = value.run["route_snapshot"]["snapshot"]
        for route in frozen["routes"]:
            route["transport"]["version"] = "gateway-transport/2"
            route["transport"]["output_token_parameter"] = (
                "max_completion_tokens" if route["id"] == "author-a" else "max_tokens"
            )
            route["accounting_model"] = route["gateway_model"] + "-dated"
        for attempt in value.attempts:
            attempt["route"].update(
                next(route for route in frozen["routes"] if route["id"] == attempt["route"]["id"])
            )
        frozen["snapshot_id"] = digest(
            {key: item for key, item in frozen.items() if key not in {"snapshot_id", "signature"}}
        )
        value.run["config"]["routeSnapshotId"] = frozen["snapshot_id"]
    bundles = [await _export(monkeypatch, *pair) for pair in pairs]
    assert (
        bundles[0].configuration.execution_identity == bundles[1].configuration.execution_identity
    )
    assert bundles[0].configuration.author_identity != bundles[1].configuration.author_identity
    report = _compare(tmp_path, bundles)
    assert report.comparable, report.reasons


@pytest.mark.parametrize("tamper", ["projection", "raw_config", "execution_config", "route_cap"])
async def test_effective_output_projection_cannot_contradict_frozen_inputs(
    monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    bundle = await _export(
        monkeypatch, *_v2_output_profile(_bundle(), author="author-a", author_output=8192)
    )
    body = bundle.model_dump(mode="json", by_alias=True)
    config = body["configuration"]
    if tamper == "projection":
        config["executionIdentity"]["effectiveOutputTokens"]["author"] = 32768
    elif tamper == "raw_config":
        config["retainedRunConfiguration"]["maxOutputTokens"] = 4096
    elif tamper == "execution_config":
        config["executionIdentity"]["config"]["maxOutputTokens"] = 8192
    else:
        frozen = config["routeSnapshot"]
        frozen["routes"][0]["max_output_tokens"] = 32768
        frozen["snapshot_id"] = digest(
            {key: item for key, item in frozen.items() if key not in {"snapshot_id", "signature"}}
        )
        config["retainedRunConfiguration"]["routeSnapshotId"] = frozen["snapshot_id"]
        config["routeSnapshotSha256"] = digest(frozen)
    roundtrip = TopicEvaluationBundle.model_validate_json(json.dumps(body))
    with pytest.raises(
        ValueError, match=r"(output|execution) .*configuration|execution configuration"
    ):
        validate_topic_bundle(roundtrip)


async def test_archived_absent_output_projections_remain_unknown_for_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundles: list[TopicEvaluationBundle] = []
    original = _bundle()
    for author in ("author-a", "author-b"):
        bundle = await _export(monkeypatch, *_snapshot(original, author=author))
        body = bundle.model_dump(mode="json", by_alias=True)
        del body["configuration"]["executionIdentity"]["effectiveOutputTokens"]
        archived = TopicEvaluationBundle.model_validate_json(json.dumps(body))
        validate_topic_bundle(archived)
        bundles.append(archived)
    result = _compare(tmp_path, bundles)
    assert not result.comparable
    assert any("unobserved_effective_outputs" in reason for reason in result.reasons)


async def test_export_does_not_invent_missing_historical_output_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, bodies = _snapshot(_bundle())
    del value.run["config"]["maxOutputTokens"]
    bundle = await _export(monkeypatch, value, bodies)
    assert bundle.configuration.execution_identity["effectiveOutputTokens"] == {
        "author": None,
        "reviewer": None,
    }
    validate_topic_bundle(bundle)
