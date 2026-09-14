"""Qualification binds all production native shapes and rejects stale or altered evidence."""

# The transport uses invented text and zero external requests, not live qualification.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import httpx2

from qualification_fixtures import (
    API_KEY,
    _candidate_file,
    _outputs_v3,
    _outputs_v4,
    _outputs_v5,
    _outputs_v6,
    _paths,
    _qualified,
    _request_transport,
    _three_candidate_lookup_transport,
)
from temnia_pipeline.harness.qualification import (
    QualificationLimits,
    qualification_prompts,
    run_qualification,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_V3_SCHEMAS,
    TOPIC_SELECTION_V3_STAGES,
    TOPIC_SELECTION_V4_SCHEMAS,
    TOPIC_SELECTION_V4_STAGES,
    TOPIC_SELECTION_V5_SCHEMAS,
    TOPIC_SELECTION_V5_STAGES,
    TOPIC_SELECTION_V6_SCHEMAS,
    TOPIC_SELECTION_V6_STAGES,
)
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    SELECTION_AUTHOR_SHARD_PROMPT,
    SELECTION_INVENTORY_SHARD_PROMPT,
    SELECTION_SOURCE_SHARD_PROMPT,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_v4_qualification_binds_the_section_shard_request() -> None:
    prompts = qualification_prompts("topic-selection-v4")

    assert tuple(prompts) == TOPIC_SELECTION_V4_STAGES
    assert set(TOPIC_SELECTION_V4_SCHEMAS) == set(TOPIC_SELECTION_V4_STAGES)
    prompt, output_type, version = prompts["topic_inventory_shard"]
    assert version == SELECTION_INVENTORY_SHARD_PROMPT
    assert output_type.__name__ == "TopicSelectionDraft"
    assert '"sectionId":"section-0001"' in prompt
    assert "Browse targetSection.sectionId" in prompt
    QualificationLimits(
        suite="topic-selection-v4",
        stages=("topic_inventory_shard",),
        max_exposure_micros=100_000,
        max_dispatches=1,
        max_output_tokens=256,
    )


async def test_v4_qualification_runs_the_exact_five_stage_suite(tmp_path: Path) -> None:
    request_transport, requests = _request_transport(_outputs_v4())
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=5)
    paths = _paths(tmp_path)

    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=1),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v4",
            max_exposure_micros=100_000,
            max_dispatches=5,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["passed"] is True
    assert len(requests) == 5
    assert [call["stage"] for call in report["calls"]] == list(TOPIC_SELECTION_V4_STAGES)


def test_v5_qualification_binds_the_bounded_author_request() -> None:
    prompts = qualification_prompts("topic-selection-v5")

    assert tuple(prompts) == TOPIC_SELECTION_V5_STAGES
    assert set(TOPIC_SELECTION_V5_SCHEMAS) == set(TOPIC_SELECTION_V5_STAGES)
    prompt, output_type, version = prompts["topic_author"]
    assert version == SELECTION_AUTHOR_SHARD_PROMPT
    assert output_type.__name__ == "TopicSelectionDraft"
    assert '"workItemId":"section-0001:author-0001"' in prompt
    assert "Package only the opportunities in targetWorkItem" in prompt


async def test_v5_qualification_runs_the_exact_five_stage_suite(tmp_path: Path) -> None:
    request_transport, requests = _request_transport(_outputs_v5())
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=5)
    paths = _paths(tmp_path)

    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=1),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v5",
            max_exposure_micros=100_000,
            max_dispatches=5,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["passed"] is True
    assert len(requests) == 5
    assert [call["stage"] for call in report["calls"]] == list(TOPIC_SELECTION_V5_STAGES)


def test_v6_qualification_binds_the_bounded_source_review_request() -> None:
    prompts = qualification_prompts("topic-selection-v6")

    assert tuple(prompts) == TOPIC_SELECTION_V6_STAGES
    assert set(TOPIC_SELECTION_V6_SCHEMAS) == set(TOPIC_SELECTION_V6_STAGES)
    prompt, output_type, version = prompts["topic_source"]
    assert version == SELECTION_SOURCE_SHARD_PROMPT
    assert output_type.__name__ == "TopicPortfolioReviewV4"
    assert '"workItemId":"section-0001:source-local-0001"' in prompt
    assert "review exactly one bounded source assignment" in prompt


async def test_v6_qualification_runs_the_exact_five_stage_suite(tmp_path: Path) -> None:
    request_transport, requests = _request_transport(_outputs_v6())
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=5)
    paths = _paths(tmp_path)

    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=1),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v6",
            max_exposure_micros=100_000,
            max_dispatches=5,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )

    assert report["passed"] is True
    assert len(requests) == 5
    assert [call["stage"] for call in report["calls"]] == list(TOPIC_SELECTION_V6_STAGES)


async def test_v3_qualification_runs_all_five_exact_contracts(tmp_path: Path) -> None:
    _, report, requests = await _qualified(tmp_path)
    assert len(requests) == 15
    assert {call["stage"] for call in report["calls"]} == set(TOPIC_SELECTION_V3_STAGES)
    assert all(
        call["schemaVersion"] == TOPIC_SELECTION_V3_SCHEMAS[call["stage"]]
        for call in report["calls"]
    )
    for call, request in zip(report["calls"], requests, strict=True):
        tool_names = {tool["function"]["name"] for tool in request.get("tools", [])}
        if call["stage"] in {"topic_inventory", "topic_author"}:
            assert tool_names == {"browse_source", "search_source", "read_source"}
        elif call["stage"] == "topic_source":
            assert tool_names == {
                "browse_source",
                "search_source",
                "read_source",
                "inspect_candidate",
                "read_media_evidence",
            }
        else:
            assert not tool_names


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


async def test_v3_qualification_accounts_for_tool_and_final_model_rounds(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    async def request_handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        generation = f"generation-{len(requests)}"
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "browse-call",
                        "type": "function",
                        "function": {
                            "name": "browse_source",
                            "arguments": '{"cursor":0,"limit":8}',
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": json.dumps(_outputs_v3()[1])}
            finish_reason = "stop"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": generation,
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async def lookup_handler(request: httpx.Request) -> httpx.Response:
        generation = request.url.params["id"]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": generation,
                    "model": "family/model-one",
                    "provider_name": "provider-one",
                    "is_byok": False,
                    "total_cost": "0.000001",
                    "tokens_prompt": 10,
                    "tokens_completion": 10,
                }
            },
        )

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
            max_dispatches=2,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=httpx2.MockTransport(request_handler),
        lookup_transport=httpx.MockTransport(lookup_handler),
    )

    assert report["passed"] is True
    assert report["dispatchCount"] == len(requests) == 2
    call = report["calls"][0]
    assert [round_["finishReason"] for round_ in call["rounds"]] == ["tool_call", "stop"]
    assert not any(message["role"] in {"assistant", "tool"} for message in requests[1]["messages"])
    checkpoint_prompt = requests[1]["messages"][-1]
    assert checkpoint_prompt["role"] == "user"
    assert "Temnia indexed-source progress checkpoint" in checkpoint_prompt["content"]
    assert '"format":"topic-agent-checkpoint/1"' in checkpoint_prompt["content"]
    assert '"tool_name":"browse_source"' in checkpoint_prompt["content"]
