"""Qualification binds all production native shapes and rejects stale or altered evidence."""

# The transport uses invented text and zero external requests, not live qualification.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from temnia_pipeline.harness.qualification import (
    CandidateRoute,
    QualificationLimits,
    _provisional_route,
    run_qualification,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_SCHEMAS,
    TOPIC_SELECTION_V3_SCHEMAS,
    TOPIC_SELECTION_V3_STAGES,
    bind_topic_selection_qualification,
    native_schema_sha256,
    topic_selection_qualification_case,
    topic_selection_qualification_inventory,
    topic_selection_qualification_prompts,
    validate_topic_selection_qualification,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_selection import content_hash
from test_harness_gateway_qualification import (
    API_KEY,
    _candidate_file,
    _candidate_payload,
    _paths,
    _request_transport,
    _three_candidate_lookup_transport,
)
from test_harness_settings import env, snapshot, write_snapshot

if TYPE_CHECKING:
    from temnia_pipeline.harness.routes import RouteSnapshot


def _outputs() -> list[dict[str, Any]]:
    _, record, _ = topic_selection_qualification_case()
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
    source = {
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
    patch = {
        "baseSelectionSha256": content_hash(record),
        "evidenceSha256": record.evidenceSha256,
        "rubricSha256": record.rubricSha256,
        "summary": "A scoped title correction tests the native patch schema.",
        "operations": [
            {
                "id": "title-fix",
                "kind": "retitle",
                "affectedCandidateIds": [candidate.id],
                "findingIds": ["synthetic-title"],
                "opportunities": [],
                "replacementCandidates": [
                    {
                        **candidate.model_dump(mode="json"),
                        "title": "Regular watering and garden roots",
                    }
                ],
                "reason": "The narrower title matches the source discussion.",
            }
        ],
    }
    return [record.draft.model_dump(mode="json"), cold, source, patch]


def _outputs_v3() -> list[dict[str, Any]]:
    outputs = _outputs()
    outputs[2]["candidates"] = []
    return [topic_selection_qualification_inventory().model_dump(mode="json"), *outputs]


async def _qualified(
    tmp_path: Path,
    *,
    max_output_tokens: int = 256,
) -> tuple[RouteSnapshot, Path, dict[str, Any], list[dict[str, Any]]]:
    catalogue = _candidate_payload(count=3)
    routes = tuple(
        _provisional_route(CandidateRoute.model_validate(item), date(2026, 9, 11)).model_copy(
            update={"id": f"fixture-qualified-{item['id']}"}
        )
        for item in catalogue["candidates"]
    )
    frozen = snapshot(routes=routes, synthetic=False)
    request_transport, requests = _request_transport(_outputs() * 3)
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=4)
    paths = _paths(tmp_path)
    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=3),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection",
            max_exposure_micros=100_000,
            max_dispatches=12,
            max_output_tokens=max_output_tokens,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    assert report["status"] == "completed"
    assert report["passed"] is True
    manifest = tmp_path / "bound.json"
    bind_topic_selection_qualification(
        frozen, [paths["report_path"]], manifest, max_output_tokens=max_output_tokens
    )
    return frozen, manifest, report, requests


async def test_qualification_captures_four_real_native_shapes_and_effective_settings(
    tmp_path: Path,
) -> None:
    frozen, manifest, report, requests = await _qualified(tmp_path)
    validate_topic_selection_qualification(frozen, manifest, max_output_tokens=256)
    prompts = topic_selection_qualification_prompts()
    assert len(requests) == 12
    for call, request in zip(report["calls"], requests, strict=True):
        expected = native_schema_sha256(prompts[call["stage"]][1])
        native = request["response_format"]["json_schema"]["schema"]
        actual = hashlib.sha256(
            json.dumps(native, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        assert actual == expected == call["nativeSchemaSha256"]
        assert call["schemaVersion"] == TOPIC_SELECTION_SCHEMAS[call["stage"]]
        assert request["max_completion_tokens"] == 256
    with pytest.raises(ValueError, match="output setting"):
        validate_topic_selection_qualification(frozen, manifest, max_output_tokens=512)
    route_path = tmp_path / "routes.json"
    write_snapshot(route_path, frozen)
    configured = {
        **env(route_path, frozen, "gateway"),
        "HARNESS_TOPIC_SELECTION_ENABLED": "1",
        "HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH": str(manifest),
        "HARNESS_MAX_OUTPUT_TOKENS": "256",
    }
    assert HarnessSettings.from_env(configured).validate_boot() == frozen
    del configured["HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH"]
    with pytest.raises(RuntimeError, match="HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH"):
        HarnessSettings.from_env(configured).validate_boot()
    configured["HARNESS_TOPIC_SELECTION_ENABLED"] = "0"
    assert HarnessSettings.from_env(configured).validate_boot() == frozen


async def test_v3_qualification_runs_all_five_exact_contracts(tmp_path: Path) -> None:
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
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    assert report["status"] == "completed"
    assert report["passed"] is True
    assert len(requests) == 15
    assert {call["stage"] for call in report["calls"]} == set(TOPIC_SELECTION_V3_STAGES)
    assert all(
        call["schemaVersion"] == TOPIC_SELECTION_V3_SCHEMAS[call["stage"]]
        for call in report["calls"]
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "suite",
        "prompt",
        "messages",
        "schema",
        "contract",
        "native",
        "provider",
        "reasoning",
        "tokens",
        "receipt",
        "missing_route",
    ],
)
async def test_qualification_refuses_mutated_identity_and_missing_route_coverage(  # noqa: C901
    tmp_path: Path,
    tamper: str,
) -> None:
    frozen, manifest, report, _ = await _qualified(tmp_path)
    call = report["calls"][0]
    if tamper == "suite":
        report["suite"] = "editorial"
    elif tamper == "prompt":
        call["promptVersion"] = "topic-propose/1"
    elif tamper == "messages":
        call["request"]["messages"]["sha256"] = "a" * 64
    elif tamper == "schema":
        call["schemaVersion"] = "topic-proposal/1"
    elif tamper == "contract":
        call["outputContractSha256"] = "a" * 64
    elif tamper == "native":
        call["nativeSchemaSha256"] = "a" * 64
        call["request"]["responseFormat"]["schemaSha256"] = "a" * 64
    elif tamper == "provider":
        call["request"]["providerOptions"]["gateway"]["only"] = ["foreign-provider"]
    elif tamper == "reasoning":
        call["request"]["reasoningEffort"] = "high"
    elif tamper == "tokens":
        call["request"]["maxCompletionTokens"] = 512
    elif tamper == "receipt":
        receipt = Path(call["response"]["path"])
        receipt.write_bytes(receipt.read_bytes() + b" ")
    else:
        report["calls"] = report["calls"][:4]
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report))
    # Rebinding the report digest cannot launder stale request metadata or receipt bytes.
    bound = json.loads(manifest.read_bytes())
    bound["reports"][0]["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(bound))
    with pytest.raises(ValueError, match=r"topic qualification|topic production routes"):
        validate_topic_selection_qualification(frozen, manifest, max_output_tokens=256)
