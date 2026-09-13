"""Qualification binds all production native shapes and rejects stale or altered evidence."""

# The transport uses invented text and zero external requests, not live qualification.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import TYPE_CHECKING, Any

from qualification_fixtures import (
    API_KEY,
    _candidate_file,
    _candidate_payload,
    _paths,
    _request_transport,
    _three_candidate_lookup_transport,
)
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
    native_schema_sha256,
    topic_selection_qualification_case,
    topic_selection_qualification_inventory,
    topic_selection_qualification_prompts,
)
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    candidate_handoff_rows,
    content_hash,
)
from test_harness_settings import snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from temnia_pipeline.harness.routes import RouteSnapshot


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


async def _qualified(
    tmp_path: Path,
    *,
    max_output_tokens: int = 256,
) -> tuple[RouteSnapshot, dict[str, Any], list[dict[str, Any]]]:
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
    return frozen, report, requests


async def test_qualification_captures_four_real_native_shapes_and_effective_settings(
    tmp_path: Path,
) -> None:
    _, report, requests = await _qualified(tmp_path)
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


async def test_v3_qualification_can_refresh_only_a_changed_stage(tmp_path: Path) -> None:
    request_transport, requests = _request_transport([_outputs_v3()[1]])
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=1)
    paths = _paths(tmp_path)
    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=1),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v3",
            stages=("topic_author",),
            max_exposure_micros=100_000,
            max_dispatches=1,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    assert report["passed"] is True
    assert len(requests) == 1
    assert [(call["stage"], call["promptVersion"]) for call in report["calls"]] == [
        ("topic_author", SELECTION_AUTHOR_PROMPT_V3)
    ]
