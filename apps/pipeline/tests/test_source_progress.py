"""Durable compact state for indexed editorial model/tool continuations."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json

import pytest
from pydantic_ai import Agent, TextPart
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from temnia_pipeline.harness.source_index import (
    browse_topic_source,
    build_topic_source_index,
    read_topic_source,
    search_topic_source,
    source_inspection_trace,
    validate_source_inspection,
    validate_source_read_ids,
)
from temnia_pipeline.harness.source_progress import (
    MAX_CHECKPOINT_BYTES,
    SourceProgressLimitExceeded,
    checkpoint_from_messages,
    checkpoint_sha256,
    compact_source_messages,
    messages_from_checkpoint,
)
from test_topic_source_index import FixtureEncoder, _long_evidence

INDEX_SHA = "c" * 64
STAGE = "proposal:selection:0"


def _compact(messages: list[ModelMessage]) -> list[ModelMessage]:
    return compact_source_messages(messages, index_sha256=INDEX_SHA, role="author", stage=STAGE)


def _tool_turn(
    messages: list[ModelMessage],
    *,
    name: str,
    arguments: dict[str, object],
    result: object,
    identifier: str,
) -> list[ModelMessage]:
    messages.extend(
        [
            ModelResponse(
                parts=[ToolCallPart(name, arguments, tool_call_id=identifier)],
                finish_reason="tool_call",
            ),
            ModelRequest(parts=[ToolReturnPart(name, result, tool_call_id=identifier)]),
        ]
    )
    return _compact(messages)


def test_continuations_rebuild_from_checkpoint_and_recent_exact_excerpts() -> None:
    evidence = _long_evidence(400)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    messages = _compact(
        [ModelRequest(parts=[UserPromptPart("Inspect the indexed source before answering.")])]
    )
    initial = checkpoint_from_messages(messages)
    assert initial is not None
    assert initial.request_sequence == 0
    assert initial.parent_checkpoint_sha256 is None

    root = browse_topic_source(index, index_sha256=INDEX_SHA, limit=8)
    messages = _tool_turn(
        messages,
        name="browse_source",
        arguments={"parent_id": "episode", "cursor": 0, "limit": 8},
        result=root,
        identifier="root",
    )
    after_root = checkpoint_from_messages(messages)
    assert after_root is not None
    assert after_root.parent_checkpoint_sha256 == checkpoint_sha256(initial)
    for section in root.nodes:
        page = browse_topic_source(index, index_sha256=INDEX_SHA, parent_id=section.id, limit=8)
        messages = _tool_turn(
            messages,
            name="browse_source",
            arguments={"parent_id": section.id, "cursor": 0, "limit": 8},
            result=page,
            identifier=f"browse-{section.id}",
        )
    search = search_topic_source(
        index,
        index_sha256=INDEX_SHA,
        query="rareterm irrigation",
        encoder=FixtureEncoder(),
        limit=6,
    )
    messages = _tool_turn(
        messages,
        name="search_source",
        arguments={"query": "rareterm irrigation", "cursor": 0, "limit": 6},
        result=search,
        identifier="search",
    )
    cursor = index.sentences[0].id
    page_number = 0
    while cursor is not None:
        read = read_topic_source(
            index,
            index_sha256=INDEX_SHA,
            first_sentence_id=index.sentences[0].id,
            last_sentence_id=index.sentences[-1].id,
            cursor_sentence_id=cursor,
            limit=80,
        )
        messages = _tool_turn(
            messages,
            name="read_source",
            arguments={
                "first_sentence_id": index.sentences[0].id,
                "last_sentence_id": index.sentences[-1].id,
                "cursor_sentence_id": cursor,
                "limit": 80,
            },
            result=read,
            identifier=f"read-{page_number}",
        )
        cursor = read.nextSentenceId
        page_number += 1

    checkpoint = checkpoint_from_messages(messages)
    assert checkpoint is not None
    assert checkpoint.request_sequence == 2 + len(root.nodes) + page_number
    assert len(checkpoint.retained_sentences) == 320
    assert checkpoint.evicted_sentence_count == 80
    assert len(json.dumps(checkpoint.model_dump(mode="json"))) < MAX_CHECKPOINT_BYTES
    prompt = json.dumps(list(messages), default=str)
    assert index.sentences[0].id not in {s.id for s in checkpoint.retained_sentences}
    assert evidence.sentences[-1].text in prompt

    trace = source_inspection_trace(messages, index_sha256=INDEX_SHA, role="author", stage=STAGE)
    validate_source_inspection(index, trace)
    validate_source_read_ids(index, trace, {index.sentences[-1].id})
    validate_source_read_ids(index, trace, {index.sentences[0].id})
    assert len(trace.observed_sentence_ids) == 400


def test_checkpoint_recovers_repeats_before_refusing_sustained_no_progress() -> None:
    evidence = _long_evidence(8)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    search = search_topic_source(
        index,
        index_sha256=INDEX_SHA,
        query="garden",
        encoder=FixtureEncoder(),
        limit=3,
    )
    messages = _compact([ModelRequest(parts=[UserPromptPart("Inspect the source.")])])
    for index_number in range(6):
        messages = _tool_turn(
            messages,
            name="search_source",
            arguments={"query": "garden", "cursor": 0, "limit": 3},
            result=search,
            identifier=f"search-{index_number}",
        )
    messages.extend(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_source",
                        {"query": "garden", "cursor": 0, "limit": 3},
                        tool_call_id="search-2",
                    )
                ],
                finish_reason="tool_call",
            ),
            ModelRequest(parts=[ToolReturnPart("search_source", search, tool_call_id="search-2")]),
        ]
    )
    with pytest.raises(SourceProgressLimitExceeded, match="no new progress"):
        _compact(messages)


async def test_pydantic_agent_sends_only_rebuilt_checkpoint_requests() -> None:
    evidence = _long_evidence(8)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    observed: list[list[ModelMessage]] = []

    async def browse_source(parent_id: str = "episode", cursor: int = 0, limit: int = 8) -> object:
        return browse_topic_source(
            index,
            index_sha256=INDEX_SHA,
            parent_id=parent_id,
            cursor=cursor,
            limit=limit,
        )

    async def search_source(query: str, cursor: int = 0, limit: int = 6) -> object:
        return search_topic_source(
            index,
            index_sha256=INDEX_SHA,
            query=query,
            encoder=FixtureEncoder(),
            cursor=cursor,
            limit=limit,
        )

    async def read_source(
        first_sentence_id: str,
        last_sentence_id: str,
        cursor_sentence_id: str | None = None,
        limit: int = 40,
    ) -> object:
        return read_topic_source(
            index,
            index_sha256=INDEX_SHA,
            first_sentence_id=first_sentence_id,
            last_sentence_id=last_sentence_id,
            cursor_sentence_id=cursor_sentence_id,
            limit=limit,
        )

    async def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        observed.append(messages)
        step = len(observed)
        if step == 1:
            part = ToolCallPart(
                "browse_source",
                {"parent_id": "episode", "cursor": 0, "limit": 8},
                tool_call_id="root",
            )
        elif step == 2:
            part = ToolCallPart(
                "browse_source",
                {"parent_id": "section-0001", "cursor": 0, "limit": 8},
                tool_call_id="section",
            )
        elif step == 3:
            part = ToolCallPart(
                "search_source",
                {"query": "rareterm irrigation", "cursor": 0, "limit": 6},
                tool_call_id="search",
            )
        elif step == 4:
            part = ToolCallPart(
                "read_source",
                {
                    "first_sentence_id": index.sentences[0].id,
                    "last_sentence_id": index.sentences[-1].id,
                    "limit": 40,
                },
                tool_call_id="read",
            )
        else:
            return ModelResponse(parts=[TextPart("complete")], finish_reason="stop")
        return ModelResponse(parts=[part], finish_reason="tool_call")

    def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
        return _compact(messages)

    agent = Agent(
        FunctionModel(respond),
        tools=[browse_source, search_source, read_source],
        capabilities=[ProcessHistory(processor)],
    )
    result = await agent.run("Inspect the source.")

    assert result.output == "complete"
    assert len(observed) == 5
    assert all(len(request) == 1 for request in observed)
    checkpoints = [checkpoint_from_messages(request) for request in observed]
    assert all(checkpoint is not None for checkpoint in checkpoints)
    assert [checkpoint.request_sequence for checkpoint in checkpoints if checkpoint] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert evidence.sentences[-1].text in str(observed[-1])
    assert len(result.all_messages()) == 2

    final_checkpoint = checkpoints[-1]
    assert final_checkpoint is not None
    resumed = await agent.run(
        None,
        message_history=messages_from_checkpoint(final_checkpoint, prompt="Inspect the source."),
    )
    assert resumed.output == "complete"
    resumed_checkpoint = checkpoint_from_messages(observed[-1])
    assert resumed_checkpoint == final_checkpoint


def test_cold_inspection_pages_speech_without_episode_browse_or_search() -> None:
    evidence = _long_evidence(400)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("Review selected speech.")])]
    for offset in range(0, 400, 80):
        arguments = {
            "first_sentence_id": index.sentences[0].id,
            "last_sentence_id": index.sentences[-1].id,
            "cursor_sentence_id": index.sentences[offset].id,
            "limit": 80,
        }
        page = read_topic_source(
            index,
            index_sha256=INDEX_SHA,
            first_sentence_id=index.sentences[0].id,
            last_sentence_id=index.sentences[-1].id,
            cursor_sentence_id=index.sentences[offset].id,
            limit=80,
        )
        messages.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart("read_source", arguments, tool_call_id=str(offset))]
                ),
                ModelRequest(parts=[ToolReturnPart("read_source", page, tool_call_id=str(offset))]),
            ]
        )
        messages = compact_source_messages(
            messages,
            index_sha256=INDEX_SHA,
            role="cold_reviewer",
            stage="verify:cold",
        )
    trace = source_inspection_trace(
        messages,
        index_sha256=INDEX_SHA,
        role="cold_reviewer",
        stage="verify:cold",
    )
    validate_source_inspection(index, trace)
    validate_source_read_ids(index, trace, {sentence.id for sentence in index.sentences})
    assert len(trace.retained_sentence_ids) == 320
    assert len(trace.observed_sentence_ids) == 400
