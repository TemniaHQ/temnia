"""Shared helpers for the route pre-flight tests (candidate files, paths, mock transport)."""

# pyright: reportUnusedFunction=false
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import Any

import httpx
import httpx2

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


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "journal_path": tmp_path / "journal.json",
        "receipts_path": tmp_path / "receipts",
        "report_path": tmp_path / "report.json",
    }


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
