"""Real SDK streaming qualification binds transport and retains early unknown handles."""

# Invented schema fixtures and in-process transports; no gateway is contacted.
# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import httpx2
import pytest

from qualification_fixtures import API_KEY, _candidate_payload, _paths
from temnia_pipeline.harness.qualification import (
    CandidateCatalogue,
    CandidateRoute,
    QualificationLimits,
    QualificationRefusal,
    _provisional_route,
    qualification_gateway,
    reconcile_journal,
    run_qualification,
)
from test_harness_settings import snapshot
from test_topic_selection_qualification import _outputs

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from temnia_pipeline.harness.routes import RouteSnapshot


def _catalogue(*, count: int = 3) -> dict[str, Any]:
    value = _candidate_payload(count=count)
    for candidate in value["candidates"]:
        candidate["transport"] = {
            "version": "gateway-transport/1",
            "gateway": "openrouter",
            "mode": "streaming",
            "request_timeout_seconds": 300.0,
            "total_timeout_seconds": 540.0,
        }
        candidate["providerAccountingName"] = "Observed " + candidate["provider"]
        candidate["reasoningEffort"] = "high"
    return value


def _limits() -> QualificationLimits:
    return QualificationLimits(
        suite="topic-selection",
        max_exposure_micros=100_000,
        max_dispatches=12,
        max_output_tokens=256,
        lookup_wait_seconds=0,
    )


async def _openrouter(
    directory: Path,
    *,
    failure: str | None = None,
    catalogue_override: dict[str, Any] | None = None,
) -> tuple[RouteSnapshot, Path, dict[str, Any], list[dict[str, Any]]]:
    directory.mkdir(exist_ok=True)
    catalogue = catalogue_override if catalogue_override is not None else _catalogue()
    candidate_path = directory / "candidates.json"
    candidate_path.write_text(json.dumps(catalogue))
    paths = _paths(directory)
    requests: list[dict[str, Any]] = []
    outputs = _outputs() * 3

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "openrouter.ai"
        assert request.headers["authorization"] == "Bearer " + API_KEY
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        identity = f"generation-{index + 1}"

        class Chunks(httpx2.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                # Awaited response hook persists identity before requesting even a role chunk.
                observed = json.loads(paths["journal_path"].read_bytes())["calls"][index]
                assert observed["state"] == "request_sent"
                if failure == "header_absent_timeout":
                    assert "generationId" not in observed
                else:
                    assert observed["generationId"] == identity
                assert "response" not in observed
                yield b": OPENROUTER PROCESSING\n\n"
                chunk: dict[str, Any] = {
                    "id": identity,
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                    ],
                }
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
                observed = json.loads(paths["journal_path"].read_bytes())["calls"][index]
                assert observed["generationId"] == identity
                if failure in {"timeout", "header_absent_timeout"}:
                    message = "invented interrupted stream"
                    raise httpx2.ReadTimeout(message, request=request)
                if failure == "sse_400":
                    yield b'data: {"error":{"code":400,"message":"in-stream failure"}}\n\n'
                    return
                if failure == "id_conflict":
                    chunk["id"] = "different-generation"
                chunk["choices"][0]["delta"] = {"content": json.dumps(outputs[index])}
                chunk["choices"][0]["finish_reason"] = "stop" if failure != "partial_eof" else None
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
                if failure == "partial_eof":
                    return
                chunk["choices"][0]["delta"] = {"content": ""}
                chunk["usage"] = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
                yield b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"

        return httpx2.Response(
            200,
            request=request,
            headers={
                "content-type": "text/event-stream",
                **({"X-Generation-Id": identity} if failure != "header_absent_timeout" else {}),
            },
            stream=Chunks(),
        )

    async def lookup(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "openrouter.ai"
        identity = request.url.params["id"]
        index = (int(identity.removeprefix("generation-")) - 1) // 4
        candidate = catalogue["candidates"][index]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": identity,
                    "model": candidate.get("accountingModel", candidate["gatewayModel"]),
                    "provider_name": candidate["providerAccountingName"],
                    "is_byok": False,
                    "total_cost": "0.000001",
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

    report = await run_qualification(
        candidate_path=candidate_path,
        api_key=API_KEY,
        limits=_limits(),
        journal_path=paths["journal_path"],
        report_path=paths["report_path"],
        receipts_path=paths["receipts_path"],
        request_transport=httpx2.MockTransport(handler),
        lookup_transport=httpx.MockTransport(lookup),
    )
    routes = tuple(
        _provisional_route(CandidateRoute.model_validate(item), date(2026, 9, 11)).model_copy(
            update={"id": "qualified-" + item["id"]}
        )
        for item in catalogue["candidates"]
    )
    return snapshot(routes=routes, synthetic=False), paths["report_path"], report, requests


async def test_streamed_qualification_captures_real_sdk_transport_shape(
    tmp_path: Path,
) -> None:
    _, _, report, requests = await _openrouter(tmp_path)
    assert report["status"] == "completed"
    assert report["passed"] is True
    assert len(requests) == 12
    for call, request in zip(report["calls"], requests, strict=True):
        assert call["generationObservedAt"] <= call["responseSavedAt"]
        assert request["stream"] is True
        assert request["max_tokens"] == 256
        assert "max_completion_tokens" not in request
        assert request["reasoning"] == {"effort": "high"}
        assert call["request"]["headers"] == {
            "x-openrouter-cache": "false",
            "x-openrouter-metadata": "enabled",
        }
        assert call["request"]["httpTimeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 300.0
        )
        assert len(call["costReceipts"]) == 1
        receipt = call["costReceipts"][0]
        raw = Path(receipt["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]
        assert receipt["sha256"] == call["cost"]["components"]["receiptSha256"]
        assert json.loads(raw)["data"]["total_cost"] == "0.000001"
        assert Path(receipt["path"]).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "failure", ["timeout", "header_absent_timeout", "sse_400", "partial_eof", "id_conflict"]
)
async def test_stream_unknown_retains_early_handle_and_never_retries(
    tmp_path: Path, failure: str
) -> None:
    _, _, report, requests = await _openrouter(tmp_path, failure=failure)
    assert len(requests) == report["dispatchCount"] == 1
    assert report["status"] == "halted"
    call = report["calls"][0]
    assert call["state"] == "outcome_unknown"
    assert call["generationId"] == "generation-1"
    assert "response" not in call
    assert "cost" not in call
    assert all(other["state"] == "planned" for other in report["calls"][1:])


async def test_early_unknown_handle_can_be_accounted_without_mutating_original(
    tmp_path: Path,
) -> None:
    _, _, _, _ = await _openrouter(tmp_path, failure="timeout")
    journal = tmp_path / "journal.json"
    original = journal.read_bytes()
    calls: list[str] = []

    async def lookup(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "openrouter.ai"
        calls.append(str(request.url))
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": "generation-1",
                    "model": "family/model-one",
                    "provider_name": "Observed provider-one",
                    "is_byok": False,
                    "total_cost": "0",
                    "tokens_prompt": 10,
                    "tokens_completion": 1,
                }
            },
        )

    result = await reconcile_journal(
        journal_path=journal,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        api_key=API_KEY,
        report_path=tmp_path / "reconciliation.json",
        lookup_transport=httpx.MockTransport(lookup),
    )
    assert result["dispatchCount"] == 0
    assert len(calls) == 1
    assert result["results"][0]["observation"]["actual_cost_micros"] == 0
    assert journal.read_bytes() == original
    assert json.loads(original)["calls"][0]["state"] == "outcome_unknown"


def test_candidate_transport_is_optional_only_for_legacy_and_one_gateway_per_cohort() -> None:
    legacy = _candidate_payload()["candidates"][0]
    encoded = CandidateRoute.model_validate(legacy).model_dump(mode="json", by_alias=True)
    assert "transport" not in encoded
    assert "providerAccountingName" not in encoded
    value = _catalogue()
    del value["candidates"][0]["providerAccountingName"]
    with pytest.raises(ValueError, match="accounting provider"):
        CandidateCatalogue.model_validate_json(json.dumps(value))
    value = _catalogue()
    value["candidates"][0] = legacy
    with pytest.raises(ValueError, match="one gateway"):
        CandidateCatalogue.model_validate_json(json.dumps(value))


async def test_cli_never_uses_vercel_key_for_openrouter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(_catalogue(count=1)))
    assert qualification_gateway(path) == "openrouter"
    cli_path = Path(__file__).resolve().parents[1] / "scripts" / "qualify_harness_gateway.py"
    spec = importlib.util.spec_from_file_location("qualification_cli", cli_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "must-not-be-reused")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    args = module.parser().parse_args(
        [
            "run",
            "--candidates",
            str(path),
            "--journal",
            str(tmp_path / "journal"),
            "--receipts",
            str(tmp_path / "receipts"),
            "--report",
            str(tmp_path / "report"),
            "--max-exposure-micros",
            "100000",
            "--max-dispatches",
            "4",
            "--max-output-tokens",
            "256",
            "--suite",
            "topic-selection",
        ]
    )
    with pytest.raises(QualificationRefusal, match="selected gateway API key"):
        await module._run(args)
    assert not (tmp_path / "journal").exists()
    monkeypatch.setenv("OPENROUTER_API_KEY", "correct-openrouter-key")
    observed: dict[str, Any] = {}

    async def fake_run(**kwargs: object) -> dict[str, Any]:
        observed.update(kwargs)
        return {"status": "completed", "passed": True}

    monkeypatch.setattr(module, "run_qualification", fake_run)
    assert await module._run(args) == 0
    assert observed["api_key"] == "correct-openrouter-key"
    assert observed["gateway"] == "openrouter"
    observed.clear()
    args.gateway = "vercel"
    with pytest.raises(QualificationRefusal, match="immutable input transport"):
        await module._run(args)
    assert observed == {}
