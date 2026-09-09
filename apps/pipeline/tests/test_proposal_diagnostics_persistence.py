"""Proposal diagnostics use real scoped ledger and artifact boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from obstore.store import MemoryStore
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.proposal_diagnostics import ProposalDiagnosticReport
from temnia_pipeline.harness.routes import (
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
)
from temnia_pipeline.harness.runtime_types import ProposalDiagnosticRequest, RunRef
from temnia_pipeline.harness.settings import HarnessSettings

if TYPE_CHECKING:
    from obstore.store import S3Store

SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)
STAGE = "proposal:v2:global"
PROMPT_VERSION = "chapter-propose-v3"
SCHEMA_VERSION = "chapter-proposal-compact/1"
MAX_OUTPUT_TOKENS = 8192


def _pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required for the real persistence boundary")
    return url


def _route(route_id: str = "proposal-route") -> RouteEntry:
    return RouteEntry(
        id=route_id,
        gateway_model=f"fixture/{route_id}",
        family="fixture-family",
        provider="fixture-provider",
        open_weight=True,
        context_tokens=100_000,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 9),
        ),
        prices=RoutePrices(input=0, output=0),
    )


def _snapshot(route: RouteEntry) -> RouteSnapshot:
    candidate = RouteSnapshot.model_construct(
        snapshot_id="b" * 64,
        routes=(route,),
        seats={
            seat: SeatRoutePool(route_ids=(route.id,)) for seat in ("propose", "summary", "verify")
        },
        synthetic=True,
        version=1,
    )
    return RouteSnapshot.model_validate(
        candidate.model_dump(mode="python") | {"snapshot_id": candidate.computed_id()}
    )


def _evidence(source_id: uuid.UUID) -> HarnessEvidence:
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {},
            "durationMs": 1000,
            "frameRate": None,
            "modelVersions": {},
            "pauses": [],
            "sentences": [
                {
                    "endMs": 900,
                    "id": "sentence-0",
                    "speakers": ["speaker-a"],
                    "startMs": 0,
                    "text": "Synthetic evidence sentence.",
                    "wordIds": ["word-0"],
                }
            ],
            "shots": [],
            "sourceFingerprint": "c" * 64,
            "sourceId": str(source_id),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": None,
                "detectorHash": None,
                "detectorRevision": None,
                "intervals": [],
                "status": "unknown",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(uuid.uuid4()),
            "transcriptRevision": 1,
            "transcriptSha256": "d" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": [
                {
                    "confidence": 1.0,
                    "endMs": 900,
                    "id": "word-0",
                    "lineageIds": [],
                    "speaker": "speaker-a",
                    "startMs": 0,
                    "text": "Synthetic",
                    "timing": "aligned",
                    "wordIndex": 0,
                }
            ],
        }
    )


async def _case(url: str, snapshot: RouteSnapshot) -> tuple[uuid.UUID, uuid.UUID]:
    async with db.scoped(url, SCOPE) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SCOPE.organizationId, f"proposal-diagnostic-{uuid.uuid4()}"),
            )
        ).fetchone()
        assert project is not None
        source = await (
            await conn.execute(
                """
                INSERT INTO source
                    (organization_id, project_id, title, original_filename, content_type,
                     size_bytes, master_key, status)
                VALUES (%s, %s, 'proposal diagnostic', 'source.mp4', 'video/mp4',
                        1, %s, 'ready')
                RETURNING id
                """,
                (
                    SCOPE.organizationId,
                    project["id"],
                    f"proposal-diagnostic/{uuid.uuid4()}/master.mp4",
                ),
            )
        ).fetchone()
        assert source is not None
        run = await (
            await conn.execute(
                """
                INSERT INTO harness_run
                    (organization_id, source_id, request_key, lane, budget_micros,
                     config, route_snapshot, status, stage, workflow_id, workflow_run_id)
                VALUES (%s, %s, %s, 'chapters', 1000000,
                        %s::jsonb, %s::jsonb, 'running', 'planning',
                        'proposal-diagnostic-workflow', 'proposal-diagnostic-run')
                RETURNING id
                """,
                (
                    SCOPE.organizationId,
                    source["id"],
                    str(uuid.uuid4()),
                    json.dumps(
                        {
                            "backend": "gateway",
                            "evidenceWindowSentences": 80,
                            "maxDispatches": 8,
                            "maxOutputTokens": MAX_OUTPUT_TOKENS,
                            "maxRenderConcurrency": 1,
                            "maxRepairs": 1,
                            "routeSnapshotId": snapshot.snapshot_id,
                        }
                    ),
                    snapshot.model_dump_json(by_alias=True),
                ),
            )
        ).fetchone()
        assert run is not None
    return source["id"], run["id"]


async def _accepted_response(  # noqa: PLR0913
    url: str,
    *,
    store: MemoryStore,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    evidence: HarnessArtifactRef,
    route: RouteEntry,
    suffix: str = "one",
) -> HarnessArtifactRef:
    operation_inputs = {"evidenceArtifactId": str(evidence.id), "suffix": suffix}
    operation_config = {"maxOutputTokens": MAX_OUTPUT_TOKENS, "suffix": suffix}
    request_hash = hashlib.sha256(f"request-{suffix}".encode()).hexdigest()
    operation = await ledger.acquire_operation(
        url,
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        kind=ledger.OperationKind.MODEL,
        stage=STAGE,
        inputs={**operation_inputs, "requestHash": request_hash},
        config={
            **operation_config,
            "programVersion": "chapter-workflow/2",
            "promptVersion": PROMPT_VERSION,
            "route": route.model_dump(mode="json"),
            "schemaVersion": SCHEMA_VERSION,
        },
    )
    attempt = await ledger.reserve_attempt(
        url,
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        operation_id=operation.operation.id,
        owner_token=f"owner-{suffix}",
        provider=route.provider,
        model=route.gateway_model,
        family=route.family,
        route=route.model_dump(mode="json"),
        request_hash=request_hash,
        estimated_cost_micros=0,
        dispatch_limit=8,
    )
    assert await ledger.mark_dispatched(
        url,
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        operation_id=operation.operation.id,
        attempt_id=attempt.id,
        owner_token=f"owner-{suffix}",
        dispatch_limit=8,
    )
    response = ModelResponse(
        parts=[TextPart('{"sections":[{"firstSentenceId":"sentence-0"')],
        usage=RequestUsage(input_tokens=100, output_tokens=MAX_OUTPUT_TOKENS),
        finish_reason="length",
        model_name=route.gateway_model,
        provider_name=route.provider,
        provider_response_id=f"generation-{suffix}",
    )
    response_body = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True)
    fingerprint = artifacts.fingerprint_for(
        kind="model_response",
        inputs={"attemptId": str(attempt.id), "requestHash": request_hash},
        config={
            "operationId": str(operation.operation.id),
            "route": route.model_dump(mode="json"),
            "runId": str(run_id),
        },
    )
    accepted = await artifacts.publish_json(
        url,
        scope=SCOPE,
        source_id=source_id,
        store=cast("S3Store", store),
        identity=artifacts.ArtifactIdentity(kind="model_response", fingerprint=fingerprint),
        content=response_body,
        metadata={
            "attemptId": str(attempt.id),
            "cassetteMode": "off",
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "operationId": str(operation.operation.id),
            "programVersion": "chapter-workflow/2",
            "promptVersion": PROMPT_VERSION,
            "requestHash": request_hash,
            "route": route.model_dump(mode="json"),
            "routeId": route.id,
            "runId": str(run_id),
            "schemaVersion": SCHEMA_VERSION,
            "synthetic": True,
        },
        dependency_ids=(evidence.id,),
    )
    await ledger.complete_attempt(
        url,
        scope=SCOPE,
        source_id=source_id,
        run_id=run_id,
        operation_id=operation.operation.id,
        attempt_id=attempt.id,
        owner_token=f"owner-{suffix}",
        result_artifact_id=accepted.id,
        usage={"input_tokens": 100, "output_tokens": MAX_OUTPUT_TOKENS},
        actual_cost_micros=0,
    )
    return HarnessArtifactRef(
        id=accepted.id,
        kind=HarnessArtifactKind.model_response,
        fingerprint=accepted.fingerprint,
        sha256=accepted.sha256,
        sizeBytes=accepted.size_bytes,
        storageKey=accepted.storage_key,
    )


async def test_diagnostic_publishes_exact_lineage_once_and_refuses_ambiguity() -> None:
    url = _pipeline_url()
    store = MemoryStore()
    route = _route()
    snapshot = _snapshot(route)
    source_id, run_id = await _case(url, snapshot)
    evidence = _evidence(source_id)
    try:
        async with db.scoped(url, SCOPE) as conn:
            await conn.execute(
                """
                INSERT INTO transcript
                    (id, organization_id, source_id, current_revision, status)
                VALUES (%s, %s, %s, 1, 'ready')
                """,
                (evidence.transcriptId, SCOPE.organizationId, source_id),
            )
            await conn.execute(
                """
                INSERT INTO transcript_revision
                    (organization_id, transcript_id, revision, kind, storage_key,
                     size_bytes, word_count)
                VALUES (%s, %s, 1, 'machine', %s, 1, 1)
                """,
                (
                    SCOPE.organizationId,
                    evidence.transcriptId,
                    f"proposal-diagnostic/{source_id}/transcript.json",
                ),
            )
        evidence_artifact = await artifacts.publish_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            identity=artifacts.ArtifactIdentity(
                kind="evidence",
                fingerprint=artifacts.fingerprint_for(
                    kind="evidence",
                    inputs={"sourceId": str(source_id)},
                    config={"fixture": "proposal-diagnostic"},
                ),
                transcript_id=evidence.transcriptId,
                transcript_revision=1,
            ),
            content=evidence.model_dump(mode="json"),
            metadata={"format": "harness-evidence/1"},
        )
        evidence_ref = HarnessArtifactRef(
            id=evidence_artifact.id,
            kind=HarnessArtifactKind.evidence,
            fingerprint=evidence_artifact.fingerprint,
            sha256=evidence_artifact.sha256,
            sizeBytes=evidence_artifact.size_bytes,
            storageKey=evidence_artifact.storage_key,
        )
        async with db.scoped(url, SCOPE) as conn:
            await conn.execute(
                """
                UPDATE harness_run
                   SET evidence_artifact_id = %s, route_snapshot = %s::jsonb
                 WHERE id = %s
                """,
                (
                    evidence_ref.id,
                    json.dumps(
                        {
                            "initialBudgetMicros": 1_000_000,
                            "pinnedSource": {
                                "storage_key": "fixture/master.mp4",
                                "size_bytes": 1,
                                "duration_ms": 1000,
                            },
                            "pinnedTranscript": {
                                "transcript_id": str(evidence.transcriptId),
                                "revision": 1,
                                "storage_key": f"proposal-diagnostic/{source_id}/transcript.json",
                                "size_bytes": 1,
                                "sha256": evidence.transcriptSha256,
                                "machine_revision": 1,
                                "legacy_speaker_labels": {},
                            },
                            "snapshot": snapshot.model_dump(mode="json"),
                        }
                    ),
                    run_id,
                ),
            )
        response_ref = await _accepted_response(
            url,
            store=store,
            source_id=source_id,
            run_id=run_id,
            evidence=evidence_ref,
            route=route,
        )
        settings = HarnessSettings(
            enabled=True,
            backend="gateway",
            route_snapshot_id=snapshot.snapshot_id,
            route_snapshot_path=None,
            allow_recorded=False,
            max_run_budget_micros=1_000_000,
            max_dispatches=8,
            max_repairs=1,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            evidence_window_sentences=80,
            max_render_concurrency=1,
            gateway_api_key="fixture",
        )
        instance = HarnessActivities(
            cast(
                "Any",
                SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store),
            ),
            settings,
            snapshot,
        )
        request = ProposalDiagnosticRequest(
            run=RunRef(
                scope_organization_id=SCOPE.organizationId,
                scope_user_id=SCOPE.userId,
                source_id=source_id,
                run_id=run_id,
            ),
            evidence=evidence_ref,
            model_stage=STAGE,
            route=route,
            program_version="chapter-workflow/2",
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            operation_inputs={
                "evidenceArtifactId": str(evidence_ref.id),
                "suffix": "one",
            },
            operation_config={"maxOutputTokens": MAX_OUTPUT_TOKENS, "suffix": "one"},
        )

        first = await instance.diagnose_chapter_proposal(request)
        second = await instance.diagnose_chapter_proposal(request)
        assert first == second
        assert first.code == "output_limit"
        assert first.response == response_ref
        report_body = await artifacts.read_artifact_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            artifact_id=first.artifact.id,
        )
        report = ProposalDiagnosticReport.model_validate_json(
            artifacts.canonical_json(report_body),
            strict=True,
        )
        assert report.response == response_ref
        assert report.finishReason == "length"
        assert report.usage.outputTokens == MAX_OUTPUT_TOKENS
        async with db.scoped(url, SCOPE) as conn:
            facts = await (
                await conn.execute(
                    """
                    SELECT r.dispatch_count, r.spent_micros, r.reserved_micros,
                           (SELECT count(*)::int FROM harness_attempt WHERE run_id = %s)
                               AS attempt_count,
                           (SELECT count(*)::int FROM harness_artifact
                             WHERE source_id = %s AND kind = 'checks'
                               AND metadata->>'format' = 'chapter-proposal-diagnostic/1')
                               AS diagnostic_count
                      FROM harness_run r WHERE r.id = %s
                    """,
                    (run_id, source_id, run_id),
                )
            ).fetchone()
            dependencies = await (
                await conn.execute(
                    """
                    SELECT input_artifact_id FROM harness_artifact_dependency
                     WHERE artifact_id = %s
                    """,
                    (first.artifact.id,),
                )
            ).fetchall()
        assert facts == {
            "dispatch_count": 1,
            "spent_micros": 0,
            "reserved_micros": 0,
            "attempt_count": 1,
            "diagnostic_count": 1,
        }
        assert {row["input_artifact_id"] for row in dependencies} == {
            evidence_ref.id,
            response_ref.id,
        }

        grounding_artifact = await artifacts.publish_json(
            url,
            scope=SCOPE,
            source_id=source_id,
            store=cast("S3Store", store),
            identity=artifacts.ArtifactIdentity(
                kind="checks",
                fingerprint=artifacts.fingerprint_for(
                    kind="checks",
                    inputs={"sourceId": str(source_id), "runId": str(run_id)},
                    config={"format": "chapter-summary-grounding/1"},
                ),
            ),
            content={"format": "chapter-summary-grounding/1"},
            metadata={
                "format": "chapter-summary-grounding/1",
                "runId": str(run_id),
            },
            dependency_ids=(evidence_ref.id,),
        )
        grounding_ref = HarnessArtifactRef(
            id=grounding_artifact.id,
            kind=HarnessArtifactKind.checks,
            fingerprint=grounding_artifact.fingerprint,
            sha256=grounding_artifact.sha256,
            sizeBytes=grounding_artifact.size_bytes,
            storageKey=grounding_artifact.storage_key,
        )
        changed_inputs = request.model_copy(
            update={
                "input_artifacts": (grounding_ref,),
                "operation_inputs": {
                    **request.operation_inputs,
                    "groundingArtifacts": [
                        {"id": str(grounding_ref.id), "sha256": grounding_ref.sha256}
                    ],
                },
            }
        )
        with pytest.raises(RuntimeError, match="dependencies differ"):
            await instance.diagnose_chapter_proposal(changed_inputs)

        mismatched = request.model_copy(update={"max_output_tokens": 4096})
        with pytest.raises(RuntimeError, match="route or output limit"):
            await instance.diagnose_chapter_proposal(mismatched)

        await _accepted_response(
            url,
            store=store,
            source_id=source_id,
            run_id=run_id,
            evidence=evidence_ref,
            route=route,
            suffix="duplicate",
        )
        with pytest.raises(RuntimeError, match="one accepted model response"):
            await instance.diagnose_chapter_proposal(request)
    finally:
        await db.close_pool()
