"""Use original halted cohorts without clearing another model/provider's uncertainty."""

# These transports exercise real journal transitions with invented text and no network.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import httpx2
import pytest

from temnia_pipeline.harness.qualification import (
    CandidateRoute,
    QualificationLimits,
    _provisional_route,
    run_qualification,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    bind_topic_selection_qualification,
    validate_topic_selection_qualification,
)
from test_harness_gateway_qualification import API_KEY, _candidate_payload, _paths
from test_harness_settings import snapshot
from test_topic_selection_qualification import _outputs, _qualified

if TYPE_CHECKING:
    from temnia_pipeline.harness.routes import RouteSnapshot


async def _halted(
    directory: Path, *, timeout_at: int = 13
) -> tuple[RouteSnapshot, Path, dict[str, Any]]:
    directory.mkdir()
    catalogue = _candidate_payload(count=3)
    fourth = {
        **catalogue["candidates"][0],
        "id": "candidate-four",
        "gatewayModel": "family-four/model-four",
        "family": "family-four",
        "provider": "provider-four",
    }
    catalogue["candidates"].append(fourth)
    candidate_file = directory / "candidates.json"
    candidate_file.write_text(json.dumps(catalogue))
    candidates = catalogue["candidates"]
    calls = 0

    async def request_handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == timeout_at:
            message = "Synthetic response outcome unknown"
            raise httpx2.ReadTimeout(message, request=request)
        wire = json.loads(request.content)
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": f"generation-{calls}",
                "object": "chat.completion",
                "created": 1,
                "model": wire["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_outputs()[(calls - 1) % 4]),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async def lookup_handler(request: httpx.Request) -> httpx.Response:
        generation_id = request.url.params["id"]
        candidate = candidates[(int(generation_id.removeprefix("generation-")) - 1) // 4]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": generation_id,
                    "model": candidate["gatewayModel"],
                    "provider_name": candidate["provider"],
                    "is_byok": False,
                    "total_cost": "0.000001",
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

    paths = _paths(directory)
    report = await run_qualification(
        candidate_path=candidate_file,
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection",
            max_exposure_micros=100_000,
            max_dispatches=16,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=httpx2.MockTransport(request_handler),
        lookup_transport=httpx.MockTransport(lookup_handler),
    )
    assert report["status"] == "halted"
    assert report["dispatchCount"] == timeout_at
    assert report["calls"][timeout_at - 1]["state"] == "outcome_unknown"
    routes = tuple(
        _provisional_route(CandidateRoute.model_validate(candidate), date(2026, 9, 11)).model_copy(
            update={"id": f"fixture-qualified-{candidate['id']}"}
        )
        for candidate in candidates
    )
    return snapshot(routes=routes, synthetic=False), paths["report_path"], report


async def test_halted_original_report_qualifies_settled_unrelated_routes(tmp_path: Path) -> None:
    all_routes, report_path, report = await _halted(tmp_path / "cohort")
    selected = snapshot(routes=all_routes.routes[:3], synthetic=False)
    originals = {
        path: path.read_bytes() for path in report_path.parent.rglob("*") if path.is_file()
    }
    manifest = tmp_path / "bound.json"
    bind_topic_selection_qualification(selected, [report_path], manifest, max_output_tokens=256)
    validate_topic_selection_qualification(selected, manifest, max_output_tokens=256)
    bound = json.loads(manifest.read_bytes())
    assert bound["reports"] == [
        {"path": str(report_path), "sha256": hashlib.sha256(originals[report_path]).hexdigest()}
    ]
    assert "does not resolve unrelated cohort outcomes or expenses" in bound["proofLimit"]
    assert {path: path.read_bytes() for path in originals} == originals
    assert report["reportedCostMicros"] == 12
    assert report["calls"][12]["state"] == "outcome_unknown"
    assert all(call["state"] == "planned" for call in report["calls"][13:])


async def test_halted_unknown_candidate_cannot_enter_selected_snapshot(tmp_path: Path) -> None:
    all_routes, report_path, _ = await _halted(tmp_path / "cohort")
    selected = snapshot(routes=all_routes.routes[1:], synthetic=False)
    output = tmp_path / "refused.json"
    with pytest.raises(ValueError, match="selected model/provider has unsettled calls"):
        bind_topic_selection_qualification(selected, [report_path], output, max_output_tokens=256)
    assert not output.exists()


@pytest.mark.parametrize("unknown_first", [True, False])
async def test_other_report_cannot_override_same_model_provider_unknown(
    tmp_path: Path, *, unknown_first: bool
) -> None:
    complete_dir = tmp_path / "complete"
    complete_dir.mkdir()
    selected, _, _, _ = await _qualified(complete_dir)
    _, halted_path, report = await _halted(tmp_path / "halted", timeout_at=1)
    # Candidate labels differ across sessions; the unresolved identity is model/provider.
    report["catalogue"]["candidates"][0]["id"] = "another-session-label"
    for call in report["calls"][:4]:
        call["candidateId"] = "another-session-label"
    halted_path.write_text(json.dumps(report))
    reports = [halted_path, complete_dir / "report.json"]
    if not unknown_first:
        reports.reverse()
    with pytest.raises(ValueError, match="selected model/provider has unsettled calls"):
        bind_topic_selection_qualification(
            selected, reports, tmp_path / "refused.json", max_output_tokens=256
        )


@pytest.mark.parametrize("state", ["prepared", "running"])
async def test_nonterminal_cohort_still_refuses_binding(tmp_path: Path, state: str) -> None:
    all_routes, report_path, report = await _halted(tmp_path / "cohort")
    selected = snapshot(routes=all_routes.routes[:3], synthetic=False)
    report["status"] = state
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="nonterminal"):
        bind_topic_selection_qualification(
            selected, [report_path], tmp_path / "refused.json", max_output_tokens=256
        )


@pytest.mark.parametrize(
    "state",
    [
        "admitted",
        "request_sent",
        "response_saved",
        "cost_reported",
        "cost_unresolved",
        "identity_failed",
    ],
)
async def test_terminal_report_cannot_hide_active_or_unsettled_selected_call(
    tmp_path: Path, state: str
) -> None:
    selected, _, report, _ = await _qualified(tmp_path)
    report["status"] = "halted"
    report["calls"][0]["state"] = state
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="selected model/provider has unsettled calls"):
        bind_topic_selection_qualification(
            selected, [report_path], tmp_path / "refused.json", max_output_tokens=256
        )


@pytest.mark.parametrize("tamper", ["request", "receipt", "cost"])
async def test_halted_report_keeps_exact_request_receipt_and_cost_checks(
    tmp_path: Path, tamper: str
) -> None:
    all_routes, report_path, report = await _halted(tmp_path / "cohort")
    selected = snapshot(routes=all_routes.routes[:3], synthetic=False)
    call = report["calls"][0]
    if tamper == "request":
        call["promptSha256"] = "a" * 64
    elif tamper == "receipt":
        receipt = Path(call["response"]["path"])
        receipt.write_bytes(receipt.read_bytes() + b" ")
    else:
        call["cost"]["status"] = "unavailable"
        call["cost"]["actual_cost_micros"] = None
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="topic qualification"):
        bind_topic_selection_qualification(
            selected, [report_path], tmp_path / "refused.json", max_output_tokens=256
        )
