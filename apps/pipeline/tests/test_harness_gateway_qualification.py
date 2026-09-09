"""Offline regressions for the finite live-gateway qualification runner."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import httpx2
import pytest

from temnia_pipeline.harness.qualification import (
    QualificationLimits,
    QualificationRefusal,
    reconcile_journal,
    run_qualification,
)

API_KEY = "qualification-test-key-marker"


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


def _outputs() -> list[dict[str, Any]]:
    return [
        {
            "version": 1,
            "units": [
                {
                    "id": "unit-1",
                    "firstSentenceId": "sentence-1",
                    "lastSentenceId": "sentence-2",
                    "quoteWordIds": ["word-1", "word-9"],
                    "text": "The pump includes a battery backup.",
                }
            ],
        },
        {
            "version": 1,
            "summary": "The pump and battery backup form one chapter.",
            "sections": [
                {
                    "id": "chapter-1",
                    "kind": "keep",
                    "title": "Pump and backup",
                    "reason": "Both sentences explain one product.",
                    "firstSentenceId": "sentence-1",
                    "lastSentenceId": "sentence-2",
                    "quoteWordIds": ["word-1", "word-9"],
                }
            ],
        },
        {
            "version": 1,
            "status": "passed",
            "reasons": [],
            "inspectedModalities": "text_evidence_and_technical_report",
        },
    ]


def _compact_outputs(candidate_count: int) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for _ in range(candidate_count):
        candidate_outputs = _outputs()
        del candidate_outputs[1]["sections"][0]["id"]
        outputs.extend(candidate_outputs)
    return outputs


def _request_transport(
    outputs: list[dict[str, Any]] | None = None,
) -> tuple[httpx2.MockTransport, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []
    values = iter(outputs or _outputs())

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


def _lookup_transport(
    *,
    cost: str = "0.000001",
    provider: str = "provider-one",
    status: int = 200,
) -> tuple[httpx.MockTransport, list[str]]:
    generations: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        generation_id = request.url.params["id"]
        generations.append(generation_id)
        if status != 200:
            return httpx.Response(status, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": generation_id,
                    "model": "family/model-one",
                    "provider_name": provider,
                    "is_byok": False,
                    "total_cost": cost,
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

    return httpx.MockTransport(handler), generations


def _three_candidate_lookup_transport() -> tuple[httpx.MockTransport, list[str]]:
    generations: list[str] = []
    models = ("family/model-one", "family-two/model-two", "family-three/model-three")
    providers = ("provider-one", "provider-two", "provider-three")

    async def handler(request: httpx.Request) -> httpx.Response:
        generation_id = request.url.params["id"]
        generations.append(generation_id)
        candidate_index = (int(generation_id.removeprefix("generation-")) - 1) // 3
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


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "journal_path": tmp_path / "journal.json",
        "receipts_path": tmp_path / "receipts",
        "report_path": tmp_path / "report.json",
    }


def _limits(
    *,
    exposure: int = 100_000,
    proposal_wire: Literal["canonical", "compact"] = "canonical",
) -> QualificationLimits:
    return QualificationLimits(
        max_exposure_micros=exposure,
        max_dispatches=12,
        max_output_tokens=256,
        proposal_wire=proposal_wire,
        lookup_wait_seconds=0,
    )


async def _run(
    tmp_path: Path,
    *,
    request_transport: httpx2.AsyncBaseTransport,
    lookup_transport: httpx.AsyncBaseTransport,
    price: int = 1,
    exposure: int = 100_000,
) -> dict[str, Any]:
    paths = _paths(tmp_path)
    return await run_qualification(
        candidate_path=_candidate_file(tmp_path, price=price),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=_limits(exposure=exposure),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )


@pytest.mark.asyncio
async def test_success_records_three_private_hash_verifiable_receipts(tmp_path: Path) -> None:
    request_transport, requests = _request_transport()
    lookup_transport, generations = _lookup_transport()

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["status"] == "completed"
    assert report["passed"] is True
    assert report["dispatchCount"] == 3
    assert generations == ["generation-1", "generation-2", "generation-3"]
    assert len(requests) == 3
    assert all(request["store"] is False for request in requests)
    assert all(
        request["providerOptions"]
        == {"gateway": {"only": ["provider-one"], "zeroDataRetention": True}}
        for request in requests
    )
    journal_bytes = (tmp_path / "journal.json").read_bytes()
    report_bytes = (tmp_path / "report.json").read_bytes()
    assert API_KEY.encode() not in journal_bytes + report_bytes
    for call in report["calls"]:
        receipt = Path(call["response"]["path"])
        body = receipt.read_bytes()
        assert receipt.stat().st_mode & 0o777 == 0o600
        assert call["response"]["sizeBytes"] == len(body)
        assert call["response"]["sha256"] == hashlib.sha256(body).hexdigest()
        assert API_KEY.encode() not in body


@pytest.mark.asyncio
async def test_compact_wire_qualifies_three_families_and_freezes_call_identity(
    tmp_path: Path,
) -> None:
    request_transport, requests = _request_transport(_compact_outputs(3))
    lookup_transport, generations = _three_candidate_lookup_transport()
    paths = _paths(tmp_path)

    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=3),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=_limits(proposal_wire="compact"),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["status"] == "completed"
    assert report["passed"] is True
    assert report["dispatchCount"] == 9
    assert report["proposalWire"] == "compact"
    assert report["limits"]["proposal_wire"] == "compact"
    assert generations == [f"generation-{index}" for index in range(1, 10)]
    assert len(requests) == 9
    for call in report["calls"]:
        stage = call["stage"]
        assert call["proposalWire"] == "compact"
        assert call["promptVersion"]
        assert call["schemaVersion"] == (
            "chapter-proposal-compact/1" if stage == "proposal" else f"qualification-{stage}/1"
        )
        assert call["response"]["promptVersion"] == call["promptVersion"]
        assert call["response"]["schemaVersion"] == call["schemaVersion"]
    proposal_requests = requests[1::3]
    assert all(
        "id"
        not in request["response_format"]["json_schema"]["schema"]["$defs"][
            "CompactChapterProposalSection"
        ]["properties"]
        for request in proposal_requests
    )


@pytest.mark.asyncio
async def test_canonical_wire_keeps_historical_journal_shape(tmp_path: Path) -> None:
    request_transport, _ = _request_transport()
    lookup_transport, _ = _lookup_transport()

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert "proposalWire" not in report
    assert "proposal_wire" not in report["limits"]
    assert all("proposalWire" not in call for call in report["calls"])
    assert all("promptVersion" not in call for call in report["calls"])
    assert all("schemaVersion" not in call for call in report["calls"])


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["wire", "prompt"])
async def test_compact_journal_identity_tamper_refuses_before_lookup(
    tmp_path: Path,
    tamper: str,
) -> None:
    request_transport, _ = _request_transport(_compact_outputs(1))
    lookup_transport, _ = _lookup_transport()
    paths = _paths(tmp_path)
    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=_limits(proposal_wire="compact"),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    if tamper == "wire":
        report["proposalWire"] = "canonical"
        expected = "proposal wire is inconsistent"
    else:
        report["calls"][1]["promptVersion"] = "wrong-prompt"
        expected = "compact call metadata is invalid"
    journal = tmp_path / "tampered-journal.json"
    body = json.dumps(report, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    journal.write_bytes(body)
    lookups = 0

    async def lookup(_request: httpx.Request) -> httpx.Response:
        nonlocal lookups
        lookups += 1
        return httpx.Response(500)

    with pytest.raises(QualificationRefusal, match=expected):
        await reconcile_journal(
            journal_path=journal,
            expected_sha256=hashlib.sha256(body).hexdigest(),
            api_key=API_KEY,
            report_path=tmp_path / "tampered-reconciliation.json",
            lookup_transport=httpx.MockTransport(lookup),
        )
    assert lookups == 0


@pytest.mark.asyncio
async def test_authorization_rejection_is_known_and_halts_without_retry(tmp_path: Path) -> None:
    requests = 0

    async def reject(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(
            403,
            request=request,
            json={
                "error": {
                    "message": f"not entitled {API_KEY}",
                    "type": "permission_denied",
                    "code": "zdr_not_available",
                    "api_key": API_KEY,
                },
                "api_key": API_KEY,
            },
        )

    report = await _run(
        tmp_path,
        request_transport=httpx2.MockTransport(reject),
        lookup_transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )

    assert requests == 1
    assert report["status"] == "halted"
    assert report["calls"][0]["state"] == "known_failure"
    assert report["admittedExposureMicros"] > 0
    failure_ref = report["calls"][0]["httpFailure"]
    failure_body = Path(failure_ref["path"]).read_bytes()
    assert failure_ref["sha256"] == hashlib.sha256(failure_body).hexdigest()
    assert failure_ref["sizeBytes"] == len(failure_body)
    assert API_KEY.encode() not in failure_body
    assert json.loads(failure_body) == {
        "format": "temnia-gateway-http-failure/1",
        "statusCode": 403,
        "error": {
            "type": "permission_denied",
            "code": "zdr_not_available",
            "message": "[redacted]",
        },
    }


@pytest.mark.asyncio
async def test_pending_cost_halts_without_redispatch(tmp_path: Path) -> None:
    request_transport, requests = _request_transport()
    lookup_transport, generations = _lookup_transport(status=404)

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert len(requests) == 1
    assert generations == ["generation-1"]
    assert report["status"] == "halted"
    assert report["calls"][0]["state"] == "cost_unresolved"


@pytest.mark.asyncio
async def test_wrong_cost_identity_halts_before_validation(tmp_path: Path) -> None:
    request_transport, requests = _request_transport()
    lookup_transport, _ = _lookup_transport(provider="other-provider")

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert len(requests) == 1
    assert report["calls"][0]["state"] == "identity_failed"
    assert report["status"] == "halted"


@pytest.mark.asyncio
async def test_strict_output_failure_keeps_response_and_cost_before_skipping(
    tmp_path: Path,
) -> None:
    request_transport, requests = _request_transport([{"version": 1, "unexpected": True}])
    lookup_transport, _ = _lookup_transport()

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert len(requests) == 1
    first, second, third = report["calls"]
    assert first["state"] == "failed"
    assert first["validation"] == {"passed": False, "code": "strict-output-invalid"}
    assert first["cost"]["status"] == "reported"
    assert Path(first["response"]["path"]).exists()
    assert second["state"] == third["state"] == "skipped"


@pytest.mark.asyncio
async def test_actual_cost_above_reservation_halts_and_counts_excess(tmp_path: Path) -> None:
    request_transport, requests = _request_transport()
    lookup_transport, _ = _lookup_transport(cost="0.000100")

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert len(requests) == 1
    assert report["status"] == "halted"
    assert report["reportedCostMicros"] == 100
    assert report["admittedExposureMicros"] == 100
    assert report["calls"][0]["estimateExceeded"] is True


@pytest.mark.asyncio
async def test_credential_echo_never_enters_a_receipt(tmp_path: Path) -> None:
    output = _outputs()[0]
    output["units"][0]["text"] = API_KEY
    request_transport, _ = _request_transport([output])
    lookup_transport, _ = _lookup_transport()

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["status"] == "halted"
    assert report["calls"][0]["state"] == "outcome_unknown"
    assert report["calls"][0]["errorCode"] == "credential-echo-refused"
    assert list((tmp_path / "receipts").iterdir()) == []
    assert API_KEY.encode() not in (tmp_path / "journal.json").read_bytes()


@pytest.mark.asyncio
async def test_grounding_rejects_quote_anchor_outside_its_section(tmp_path: Path) -> None:
    proposal = _outputs()[1]
    proposal["sections"] = [
        {
            "id": "chapter-1",
            "kind": "keep",
            "title": "Pump",
            "reason": "First sentence.",
            "firstSentenceId": "sentence-1",
            "lastSentenceId": "sentence-1",
            "quoteWordIds": ["word-9"],
        },
        {
            "id": "chapter-2",
            "kind": "keep",
            "title": "Backup",
            "reason": "Second sentence.",
            "firstSentenceId": "sentence-2",
            "lastSentenceId": "sentence-2",
            "quoteWordIds": ["word-9"],
        },
    ]
    request_transport, requests = _request_transport([_outputs()[0], proposal])
    lookup_transport, _ = _lookup_transport()

    report = await _run(
        tmp_path,
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert len(requests) == 2
    assert report["calls"][0]["state"] == "passed"
    assert report["calls"][1]["validation"] == {
        "passed": False,
        "code": "grounding-invalid",
    }


@pytest.mark.asyncio
async def test_existing_journal_refuses_before_transport(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["journal_path"].write_text("occupied")
    request_transport, requests = _request_transport()
    lookup_transport, _ = _lookup_transport()

    with pytest.raises(FileExistsError):
        await run_qualification(
            candidate_path=_candidate_file(tmp_path),
            api_key=API_KEY,
            journal_path=paths["journal_path"],
            receipts_path=paths["receipts_path"],
            report_path=paths["report_path"],
            limits=_limits(),
            request_transport=request_transport,
            lookup_transport=lookup_transport,
        )
    assert requests == []


@pytest.mark.asyncio
async def test_reconcile_validates_journal_shape_and_never_dispatches(tmp_path: Path) -> None:
    journal = tmp_path / "journal.json"
    body = b'{"format":"temnia-gateway-qualification/1","calls":[]}\n'
    journal.write_bytes(body)
    lookups = 0

    async def lookup(_request: httpx.Request) -> httpx.Response:
        nonlocal lookups
        lookups += 1
        return httpx.Response(500)

    with pytest.raises(QualificationRefusal, match="metadata is invalid"):
        await reconcile_journal(
            journal_path=journal,
            expected_sha256=hashlib.sha256(body).hexdigest(),
            api_key=API_KEY,
            report_path=tmp_path / "reconcile.json",
            lookup_transport=httpx.MockTransport(lookup),
        )
    assert lookups == 0
