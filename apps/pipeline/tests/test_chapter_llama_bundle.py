"""Portable terminal generations retain failed output and refuse mixed provenance."""

# Tests intentionally vary immutable wire facts to exercise their validation fences.
# ruff: noqa: ANN401
# pyright: reportPrivateUsage=false
from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest

from temnia_pipeline.chapter_llama.candidate import (
    CANDIDATE_MODEL,
    CandidatePayload,
    candidate_hints,
    input_from_evidence,
)
from temnia_pipeline.chapter_llama.client import ChapterLlamaConfig, DeploymentIdentity
from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    InferenceResult,
    ModelConfig,
    ResourceProfile,
    digest,
    parse_predictions,
)
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.evals.chapters import (
    AttemptFact,
    ChapterLlamaCandidateArtifact,
    EvaluationBundle,
    ImmutableArtifactFact,
    content_sha256,
    validate_bundle,
)
from temnia_pipeline.harness import bundle_export
from temnia_pipeline.harness.artifacts import canonical_json, fingerprint_for
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.scope import resolve_scope
from test_chapter_evaluation import NOW, _bundle

EVIDENCE_ID = UUID(int=1001)
RESPONSE_ID = UUID(int=1002)
ATTEMPT_ID = UUID(int=1003)
OPERATION_ID = UUID(int=1004)


def _candidate_bundle(*, failed: bool = False) -> EvaluationBundle:
    base = _bundle()
    assert base.evidence is not None
    evidence = base.evidence
    resources = ResourceProfile()
    configuration = ChapterLlamaConfig(
        app_name="temnia-chapter-llama",
        environment="staging",
        deployment=DeploymentIdentity(build="b" * 64, config=ModelConfig(), resources=resources),
    )
    request = input_from_evidence(evidence, configuration)
    job = ChapterLlamaJob(
        organization_id=resolve_scope().organizationId,
        source_id=base.source_id,
        operation_id=OPERATION_ID,
        attempt_id=ATTEMPT_ID,
        expected_build=configuration.deployment.build,
        input=request,
    )
    raw = "00:00:00 - Opening\n00:00:05 - Second topic"
    result = InferenceResult(
        input_sha256=request.sha256,
        config=request.config,
        raw_output=raw,
        predictions=parse_predictions(raw, request),
        input_tokens=120,
        output_tokens=18,
        elapsed_seconds=2,
    )
    outcome = ChapterLlamaOutcome(
        job_sha256=job.sha256,
        build=job.expected_build,
        modal_call_id="fc-test-candidate",
        modal_task_id="ta-test-candidate",
        compute_seconds=3,
        status="failed" if failed else "ok",
        result=None if failed else result,
        error_code="InvalidGenerationError" if failed else None,
        error_message="invalid chapter format" if failed else None,
        rejected_output="Paid malformed output: not a timestamp" if failed else None,
        rejected_input_tokens=120 if failed else None,
        rejected_output_tokens=18 if failed else None,
    )
    payload = CandidatePayload(
        evidence_id=EVIDENCE_ID,
        evidence_sha256=content_sha256(evidence),
        input_sha256=request.sha256,
        configuration=configuration,
        job=job,
        outcome=outcome,
    )
    inputs = {
        "evidenceId": str(EVIDENCE_ID),
        "evidenceSha256": payload.evidence_sha256,
        "inputSha256": request.sha256,
    }
    route = configuration.model_dump(mode="json")
    request_hash = digest({"inputs": inputs, "configuration": route})
    prefix = f"org/{job.organization_id}/source/{job.source_id}/harness/"
    candidate = ChapterLlamaCandidateArtifact(
        artifact=ImmutableArtifactFact(
            id=RESPONSE_ID,
            source_id=base.source_id,
            kind="model_response",
            fingerprint=fingerprint_for(
                kind="model_response",
                inputs={"attemptId": str(ATTEMPT_ID), "requestHash": request_hash},
                config={"schema": "chapter-llama-candidate/1", "deployment": route},
            ),
            sha256=digest(payload.model_dump(mode="json")),
            size_bytes=len(canonical_json(payload.model_dump(mode="json"))),
            storage_key=f"{prefix}candidate.json",
        ),
        evidence=ImmutableArtifactFact(
            id=EVIDENCE_ID,
            source_id=base.source_id,
            kind="evidence",
            fingerprint="c" * 64,
            sha256=payload.evidence_sha256,
            size_bytes=len(canonical_json(evidence.model_dump(mode="json", by_alias=True))),
            storage_key=f"{prefix}evidence.json",
        ),
        body=payload,
    )
    attempt = AttemptFact(
        id=ATTEMPT_ID,
        operation_id=OPERATION_ID,
        attempt_number=1,
        state="failed_known" if failed else "succeeded",
        provider="modal",
        family="llama",
        model=CANDIDATE_MODEL,
        synthetic=False,
        estimated_cost_micros=resources.reservation_micros,
        cost_status="unknown",
        reservation_active=True,
        response_present=True,
        remote_handle=outcome.modal_call_id,
        result_artifact_id=None if failed else RESPONSE_ID,
        usage={
            "protocol": job.protocol,
            "candidateArtifactId": str(RESPONSE_ID),
            "checkpointKey": job.checkpoint_key,
            "computeSeconds": 3.0,
            "estimatedComputeMicros": resources.estimated_micros(3),
            "inputTokens": 120,
            "outputTokens": 18,
            "invoiceStatus": "unavailable",
            "resources": resources.model_dump(mode="json"),
        },
        dispatched_at=NOW,
        finished_at=NOW + timedelta(seconds=3),
    )
    return base.model_copy(
        update={
            "status": "failed" if failed else "running",
            "current_revision": 0,
            "edit": None,
            "edit_sha256": None,
            "edit_revision": None,
            "review_events": (),
            "attempts": (attempt,),
            "chapter_llama_candidates": (candidate,),
        }
    )


@pytest.mark.parametrize("failed", [False, True])
def test_terminal_generation_is_portable_and_only_success_supplies_hints(*, failed: bool) -> None:
    bundle = _candidate_bundle(failed=failed)
    validate_bundle(bundle)
    portable = EvaluationBundle.model_validate_json(bundle.model_dump_json(by_alias=True))
    validate_bundle(portable)
    candidate = portable.chapter_llama_candidates[0]
    fact = candidate.evidence
    reference = HarnessArtifactRef(
        id=fact.id,
        kind=HarnessArtifactKind.evidence,
        fingerprint=fact.fingerprint,
        sha256=fact.sha256,
        storageKey=cast("str", fact.storage_key),
        sizeBytes=cast("int", fact.size_bytes),
    )
    assert portable.evidence is not None
    if failed:
        assert candidate.body.outcome.rejected_output == "Paid malformed output: not a timestamp"
        assert portable.attempts[0].result_artifact_id is None
        with pytest.raises(ValueError, match="cannot supply topic hints"):
            candidate_hints(candidate.body, portable.evidence, reference)
    else:
        assert '"firstSentenceId": "s2"' in candidate_hints(
            candidate.body, portable.evidence, reference
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "other-llama-model"),
        ("remote_handle", "fc-another-call"),
        ("response_present", False),
        ("estimated_cost_micros", 1),
        ("state", "outcome_unknown"),
    ],
)
def test_candidate_refuses_contradictory_attempt_facts(field: str, value: object) -> None:
    bundle = _candidate_bundle()
    changed = bundle.attempts[0].model_copy(update={field: value})
    with pytest.raises(HarnessValidationError):
        validate_bundle(bundle.model_copy(update={"attempts": (changed,)}))


@pytest.mark.parametrize(
    "field",
    [
        "candidateArtifactId",
        "checkpointKey",
        "inputTokens",
        "outputTokens",
        "computeSeconds",
        "resources",
    ],
)
def test_candidate_refuses_contradictory_usage(field: str) -> None:
    bundle = _candidate_bundle(failed=True)
    attempt = bundle.attempts[0]
    changed = attempt.model_copy(update={"usage": {**attempt.usage, field: "wrong"}})
    with pytest.raises(HarnessValidationError):
        validate_bundle(bundle.model_copy(update={"attempts": (changed,)}))


def test_failed_output_cannot_be_omitted_or_changed_even_when_rehashed() -> None:
    bundle = _candidate_bundle(failed=True)
    with pytest.raises(HarnessValidationError, match="closure"):
        validate_bundle(bundle.model_copy(update={"chapter_llama_candidates": ()}))
    candidate = bundle.chapter_llama_candidates[0]
    changed_payload = candidate.body.model_copy(
        update={"outcome": candidate.body.outcome.model_copy(update={"modal_task_id": ""})}
    )
    changed = candidate.model_copy(
        update={
            "body": changed_payload,
            "artifact": candidate.artifact.model_copy(
                update={
                    "sha256": digest(changed_payload.model_dump(mode="json")),
                    "size_bytes": len(canonical_json(changed_payload.model_dump(mode="json"))),
                }
            ),
        }
    )
    with pytest.raises(HarnessValidationError, match="grounded"):
        validate_bundle(bundle.model_copy(update={"chapter_llama_candidates": (changed,)}))


def _export_snapshot(bundle: EvaluationBundle) -> Any:
    candidate = bundle.chapter_llama_candidates[0]
    payload = candidate.body
    attempt = bundle.attempts[0]
    rows: dict[UUID, dict[str, Any]] = {}
    for fact in (candidate.artifact, candidate.evidence):
        rows[fact.id] = {
            **fact.model_dump(),
            "metadata": {},
        }
    rows[RESPONSE_ID]["metadata"] = {
        "schemaVersion": "chapter-llama-candidate/1",
        "promptVersion": payload.configuration.deployment.config.prompt_version,
        "operationId": str(OPERATION_ID),
        "runId": str(bundle.run_id),
    }
    inputs = {
        "evidenceId": str(EVIDENCE_ID),
        "evidenceSha256": payload.evidence_sha256,
        "inputSha256": payload.input_sha256,
    }
    route = payload.configuration.model_dump(mode="json")
    attempt_row = {
        **attempt.model_dump(),
        "route": route,
        "request_hash": digest({"inputs": inputs, "configuration": route}),
        "reservation_state": "active",
    }
    return vars(bundle_export)["_Snapshot"](
        run={
            "id": bundle.run_id,
            "source_id": bundle.source_id,
            "evidence_artifact_id": EVIDENCE_ID,
            "status": bundle.status,
            "current_revision": 0,
            "accepted_revision": None,
            "config": {"routeSnapshotId": "candidate-fixture"},
        },
        source={"id": bundle.source_id, "duration_ms": bundle.duration_ms},
        observed_at=NOW,
        revision=None,
        descriptor_id=None,
        verification_id=None,
        grounding_ids=(),
        diagnostic_ids=(),
        rows=rows,
        dependencies={RESPONSE_ID: (EVIDENCE_ID,)},
        attempts=(attempt_row,),
        events=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_exporter_preserves_success_and_failed_raw_output(
    monkeypatch: pytest.MonkeyPatch, *, failed: bool
) -> None:
    fixture = _candidate_bundle(failed=failed)
    snapshot = _export_snapshot(fixture)
    assert fixture.evidence is not None
    bodies = {
        EVIDENCE_ID: fixture.evidence.model_dump(mode="json"),
        RESPONSE_ID: fixture.chapter_llama_candidates[0].body.model_dump(mode="json"),
    }

    async def read_snapshot(*_args: object, **_kwargs: object) -> object:
        return snapshot

    async def read_artifact(*_args: object, **kwargs: object) -> object:
        return bodies[cast("UUID", kwargs["artifact_id"])]

    monkeypatch.setattr(bundle_export, "_read_snapshot", read_snapshot)
    monkeypatch.setattr(bundle_export.artifacts, "read_artifact_json", read_artifact)
    exported = await bundle_export.export_evaluation_bundle(
        "postgresql://unused",
        scope=resolve_scope(),
        store=cast("Any", object()),
        run_id=fixture.run_id,
    )
    assert exported.chapter_llama_candidates == fixture.chapter_llama_candidates
    assert exported.attempts[0].response_present
    assert exported.attempts[0].result_artifact_id == (None if failed else RESPONSE_ID)
    assert exported.attempts[0].actual_cost_micros is None
    assert exported.attempts[0].reservation_active
    snapshot.rows[RESPONSE_ID]["metadata"]["runId"] = str(UUID(int=999))
    with pytest.raises(ValueError, match="source scope"):
        await bundle_export.export_evaluation_bundle(
            "postgresql://unused",
            scope=resolve_scope(),
            store=cast("Any", object()),
            run_id=fixture.run_id,
        )


@pytest.mark.parametrize("failed", [False, True])
def test_response_discovery_requires_exact_attempt_identity(*, failed: bool) -> None:
    snapshot = _export_snapshot(_candidate_bundle(failed=failed))
    attempt = snapshot.attempts[0]
    discover = vars(bundle_export)["_chapter_llama_response_id"]
    assert discover(attempt) == RESPONSE_ID
    with pytest.raises(ValueError, match="terminal attempt"):
        discover({**attempt, "model": "foreign-model"})
    with pytest.raises(ValueError, match="attempt result"):
        discover({**attempt, "result_artifact_id": RESPONSE_ID if failed else None})


def test_legacy_bundle_keeps_empty_candidate_default() -> None:
    payload = _bundle().model_dump(mode="json", by_alias=True)
    payload.pop("chapterLlamaCandidates")
    restored = EvaluationBundle.model_validate_json(canonical_json(payload))
    validate_bundle(restored)
    assert restored.chapter_llama_candidates == ()
