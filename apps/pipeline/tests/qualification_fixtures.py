"""Shared helpers for the route pre-flight tests (candidate files, paths, mock transport)."""

# pyright: reportUnusedFunction=false, reportPrivateUsage=false
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

import httpx
import httpx2
import numpy as np

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    Scope,
)
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.qualification import (
    CandidateRoute,
    QualificationLimits,
    _provisional_route,
    run_qualification,
)
from temnia_pipeline.harness.qualification_fixture import synthetic_qualification_evidence
from temnia_pipeline.harness.qualification_topic_selection import (
    topic_selection_qualification_case,
    topic_selection_qualification_inventory,
    topic_selection_v4_qualification_inventory,
)
from temnia_pipeline.harness.source_index import build_topic_source_index
from temnia_pipeline.harness.topic_selection import candidate_handoff_rows, content_hash
from test_harness_settings import snapshot

if TYPE_CHECKING:
    from uuid import UUID

    from obstore.store import S3Store

    from temnia_pipeline.harness.artifacts import HarnessArtifact
    from temnia_pipeline.harness.routes import RouteSnapshot

API_KEY = "qualification-test-key-marker"


class _FixtureEncoder:
    def encode(
        self, sentences: list[str], *, normalize_embeddings: bool = True, batch_size: int = 32
    ) -> object:
        _ = normalize_embeddings, batch_size
        return np.ones((len(sentences), 1), dtype=np.float64)


async def _published_source_index_ref(
    database_url: str, *, scope: Scope, source_id: UUID, store: S3Store
) -> HarnessArtifactRef:
    """Publish one valid scoped synthetic index for model-transport integration tests."""
    evidence = synthetic_qualification_evidence().model_copy(update={"sourceId": source_id})
    index = build_topic_source_index(
        evidence,
        evidence_sha256="e" * 64,
        encoder=_FixtureEncoder(),
        embedding_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    )
    accepted = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(kind="checks", fingerprint="f" * 64),
        content=index.model_dump(mode="json"),
        metadata={"format": index.format},
    )
    return HarnessArtifactRef(
        id=accepted.id,
        kind=HarnessArtifactKind.checks,
        fingerprint=accepted.fingerprint,
        sha256=accepted.sha256,
        sizeBytes=accepted.size_bytes,
        storageKey=accepted.storage_key,
    )


async def _published_reviewer_refs(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    run_id: UUID,
    store: S3Store,
) -> tuple[HarnessArtifactRef, HarnessArtifactRef, HarnessArtifactRef]:
    """Publish one coherent evidence/index/selection authority set for source review."""

    def reference(accepted: HarnessArtifact, kind: HarnessArtifactKind) -> HarnessArtifactRef:
        return HarnessArtifactRef(
            id=accepted.id,
            kind=kind,
            fingerprint=accepted.fingerprint,
            sha256=accepted.sha256,
            sizeBytes=accepted.size_bytes,
            storageKey=accepted.storage_key,
        )

    async with db.scoped(database_url, scope) as conn:
        transcript = await (
            await conn.execute(
                "SELECT id, current_revision FROM transcript WHERE source_id = %s",
                (source_id,),
            )
        ).fetchone()
    assert transcript is not None
    evidence, selection, _ = topic_selection_qualification_case(combined_patch=True)
    evidence = evidence.model_copy(
        update={
            "sourceId": source_id,
            "transcriptId": transcript["id"],
            "transcriptRevision": transcript["current_revision"],
        }
    )
    accepted_evidence = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint="d" * 64,
            transcript_id=evidence.transcriptId,
            transcript_revision=evidence.transcriptRevision,
        ),
        content=evidence.model_dump(mode="json"),
        metadata={"format": "harness-evidence/1", "runId": str(run_id)},
    )
    evidence_ref = reference(accepted_evidence, HarnessArtifactKind.evidence)
    index = build_topic_source_index(
        evidence,
        evidence_sha256=evidence_ref.sha256,
        encoder=_FixtureEncoder(),
        embedding_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    )
    accepted_index = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(kind="checks", fingerprint="c" * 64),
        content=index.model_dump(mode="json"),
        metadata={"format": index.format, "runId": str(run_id)},
        dependency_ids=(evidence_ref.id,),
    )
    index_ref = reference(accepted_index, HarnessArtifactKind.checks)
    selection = selection.model_copy(
        update={"runId": run_id, "evidenceSha256": evidence_ref.sha256}
    )
    accepted_selection = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(kind="proposal", fingerprint="b" * 64),
        content=selection.model_dump(mode="json"),
        metadata={"format": selection.format, "runId": str(run_id)},
        dependency_ids=(evidence_ref.id, index_ref.id),
    )
    return (
        evidence_ref,
        index_ref,
        reference(accepted_selection, HarnessArtifactKind.proposal),
    )


def _candidate_payload(*, price: int = 1, count: int = 1) -> dict[str, Any]:
    words = ("one", "two", "three")
    return {
        "version": 1,
        "catalogueObservedAt": datetime(2026, 9, 9, tzinfo=UTC).isoformat(),
        "catalogueSha256": "a" * 64,
        "candidates": [
            {
                "id": f"candidate-{words[index]}",
                "gatewayModel": (
                    f"family/model-{words[index]}"
                    if index == 0
                    else f"family-{words[index]}/model-{words[index]}"
                ),
                "family": "family" if index == 0 else f"family-{words[index]}",
                "provider": f"provider-{words[index]}",
                "openWeight": True,
                "contextTokens": 100_000,
                "maxOutputTokens": 8192,
                "zdrClaim": True,
                "prices": {
                    "unit": "micros_per_million_tokens",
                    "input": price,
                    "output": price,
                    "cacheRead": None,
                    "cacheWrite": None,
                    "requestSurcharge": 0,
                },
            }
            for index in range(count)
        ],
    }


def _candidate_file(tmp_path: Path, *, price: int = 1, count: int = 1) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(_candidate_payload(price=price, count=count)))
    return path


def _request_transport(
    outputs: list[dict[str, Any]] | None = None,
) -> tuple[httpx2.MockTransport, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []
    values = iter(outputs or _outputs_v3())

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        output = next(values)
        generation_id = f"generation-{len(requests)}"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": generation_id,
                "object": "chat.completion",
                "created": 1,
                "model": requests[-1]["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    return httpx2.MockTransport(handler), requests


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "journal_path": tmp_path / "journal.json",
        "receipts_path": tmp_path / "receipts",
        "report_path": tmp_path / "report.json",
    }


def _outputs(*, combined_patch: bool = False) -> list[dict[str, Any]]:
    _, record, _ = topic_selection_qualification_case(combined_patch=combined_patch)
    candidate = record.draft.proposal.candidates[0]
    opportunity = record.draft.opportunities[0]
    criterion = {
        "status": "unknown",
        "reason": "Invented transport fixture has no measured publication quality.",
        "evidenceSpans": [candidate.coreSpans[0].model_dump(mode="json")],
    }
    cold = {
        "candidateId": candidate.id,
        **dict.fromkeys(
            ("intelligibleBeginning", "coherentTopic", "completeDiscussion", "titleFaithful"),
            criterion,
        ),
        "value": {
            "reconstructedPurpose": candidate.purpose,
            "reconstructedTakeaway": "Regular watering supports garden roots.",
            **dict.fromkeys(
                (
                    "viewerReasonToWatch",
                    "deliveredValue",
                    "openingEffectiveness",
                    "focusedDevelopment",
                ),
                criterion,
            ),
        },
    }
    source: dict[str, Any] = {
        "summary": "Invented source judgment checks schema and grounding only.",
        "candidates": [
            {
                "candidateId": candidate.id,
                **dict.fromkeys(
                    ("faithfulMeaning", "completeContext", "distinctPurpose"), criterion
                ),
            }
        ],
        "opportunities": [
            {
                "opportunityId": opportunity.id,
                "candidateIds": [candidate.id],
                "evidenceSpans": criterion["evidenceSpans"],
                "status": "unresolved",
                "reason": criterion["reason"],
            }
        ],
        "selection": [
            {
                "candidateId": candidate.id,
                "disposition": "unresolved",
                "evidenceSpans": criterion["evidenceSpans"],
                "reason": criterion["reason"],
            }
        ],
        "missingOpportunities": [],
        "findings": [
            {
                "id": "unused-fragment",
                "kind": "transcript_uncertainty",
                "severity": "unknown",
                "affectedCandidateIds": [],
                "opportunityIds": [],
                "evidenceSpans": [
                    {
                        "firstSentenceId": "s000004",
                        "lastSentenceId": "s000004",
                    }
                ],
                "reason": (
                    "The unused trailing source fragment is uncertain but does not affect "
                    "a selected candidate or a worthwhile opportunity."
                ),
            }
        ],
    }
    if combined_patch:
        fragment = record.draft.proposal.candidates[1]
        fragment_opportunity = record.draft.opportunities[1]
        source["candidates"].append(
            {
                "candidateId": fragment.id,
                **dict.fromkeys(
                    ("faithfulMeaning", "completeContext", "distinctPurpose"), criterion
                ),
            }
        )
        source["opportunities"].append(
            {
                "opportunityId": fragment_opportunity.id,
                "candidateIds": [fragment.id],
                "evidenceSpans": [fragment.coreSpans[0].model_dump(mode="json")],
                "status": "unresolved",
                "reason": criterion["reason"],
            }
        )
        source["selection"].append(
            {
                "candidateId": fragment.id,
                "disposition": "unresolved",
                "evidenceSpans": [fragment.coreSpans[0].model_dump(mode="json")],
                "reason": criterion["reason"],
            }
        )
    replacement = {
        **candidate.model_dump(mode="json"),
        "title": "Regular watering and garden roots",
    }
    if combined_patch:
        replacement.update(
            lastSentenceId="s000003",
            completionSpans=[{"firstSentenceId": "s000003", "lastSentenceId": "s000003"}],
        )
    patch = {
        "baseSelectionSha256": content_hash(record),
        "evidenceSha256": record.evidenceSha256,
        "rubricSha256": record.rubricSha256,
        "summary": "A scoped title correction tests the native patch schema.",
        "operations": [
            {
                "id": "title-fix",
                "kind": "replace_candidate" if combined_patch else "retitle",
                "affectedCandidateIds": [candidate.id],
                "findingIds": (
                    ["synthetic-ending", "synthetic-title"]
                    if combined_patch
                    else ["synthetic-title"]
                ),
                "opportunities": [],
                "replacementCandidates": [replacement],
                "reason": "The complete extent and narrower title match the source discussion.",
            }
        ],
    }
    return [record.draft.model_dump(mode="json"), cold, source, patch]


def _outputs_v3() -> list[dict[str, Any]]:
    outputs = _outputs(combined_patch=True)
    evidence, record, _ = topic_selection_qualification_case(combined_patch=True)
    outputs[2]["candidates"] = []
    outputs[2]["overlaps"] = []
    outputs[2]["handoffs"] = [
        {
            **row,
            "classification": "clean_handoff",
            "recommendedLeftLastSentenceId": None,
            "recommendedRightFirstSentenceId": None,
            "reason": "The supplied adjacent extents do not reassign topic ownership.",
        }
        for row in candidate_handoff_rows(evidence, record.draft)
    ]
    return [topic_selection_qualification_inventory().model_dump(mode="json"), *outputs]


def _outputs_v4() -> list[dict[str, Any]]:
    """Match the bounded shard ownership ID while retaining later independent fixtures."""
    outputs = _outputs_v3()
    author = outputs[1]
    for item in author["opportunities"]:
        item["id"] = f"section-0001:{item['id']}"
    return [topic_selection_v4_qualification_inventory().model_dump(mode="json"), *outputs[1:]]


def _three_candidate_lookup_transport(
    stages_per_candidate: int = 3,
) -> tuple[httpx.MockTransport, list[str]]:
    generations: list[str] = []
    models = ("family/model-one", "family-two/model-two", "family-three/model-three")
    providers = ("provider-one", "provider-two", "provider-three")

    async def handler(request: httpx.Request) -> httpx.Response:
        generation_id = request.url.params["id"]
        generations.append(generation_id)
        candidate_index = (
            int(generation_id.removeprefix("generation-")) - 1
        ) // stages_per_candidate
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": generation_id,
                    "model": models[candidate_index],
                    "provider_name": providers[candidate_index],
                    "is_byok": False,
                    "total_cost": "0.000001",
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

    return httpx.MockTransport(handler), generations


async def _qualified(
    tmp_path: Path,
    *,
    max_output_tokens: int = 256,
) -> tuple[RouteSnapshot, dict[str, Any], list[dict[str, Any]]]:
    """Run the optional v3 pre-flight against three invented routes with mocked HTTP."""
    catalogue = _candidate_payload(count=3)
    routes = tuple(
        _provisional_route(CandidateRoute.model_validate(item), date(2026, 9, 11)).model_copy(
            update={"id": f"fixture-qualified-{item['id']}"}
        )
        for item in catalogue["candidates"]
    )
    frozen = snapshot(routes=routes, synthetic=False)
    request_transport, requests = _request_transport(_outputs_v3() * 3)
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=5)
    paths = _paths(tmp_path)
    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=3),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v3",
            max_exposure_micros=100_000,
            max_dispatches=15,
            max_output_tokens=max_output_tokens,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    assert report["status"] == "completed"
    assert report["passed"] is True
    return frozen, report, requests
