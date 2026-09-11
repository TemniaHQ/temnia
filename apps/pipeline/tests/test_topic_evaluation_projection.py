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
            "id": route,
            "gateway_model": f"fixture/{route}",
            "provider": "synthetic-recorded",
            "family": f"fixture-{seat}",
            "reasoning_effort": "high",
            "max_output_tokens": 8192,
        }
        for seat, route in (("author", author), ("reviewer", "reviewer-fixed"))
    }
    route_snapshot = {
        "routes": list(routes.values()),
        "seats": {"propose": [author], "verify": ["reviewer-fixed"]},
    }
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
                "routeSnapshotId": digest(route_snapshot),
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


def _compare(tmp_path: Path, bundles: list[TopicEvaluationBundle]) -> TopicComparisonReport:
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
            mode="model_swap",
            baseline_configuration_id=bundles[0].configuration.configuration_id,
            allowed_changed_factors=("author_identity",),
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
