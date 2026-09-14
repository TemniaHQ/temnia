"""Long-source indexing, bounded retrieval, and retained inspection coverage."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json

import numpy as np
import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from temnia_pipeline.contracts import (
    HarnessEvidence,
    HarnessEvidenceSentence,
    TopicSourceIndex,
    WordId,
)
from temnia_pipeline.harness.source_index import (
    browse_topic_source,
    build_topic_source_index,
    read_topic_source,
    search_topic_source,
    source_index_map,
    source_inspection_trace,
    validate_source_inspection,
    validate_source_read_ids,
    validate_topic_source_index,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_compiler import _case


class FixtureEncoder:
    """Small normalized encoder double; lexical retrieval supplies the discriminating score."""

    def encode(
        self, sentences: list[str], *, normalize_embeddings: bool = True, batch_size: int = 32
    ) -> object:
        _ = normalize_embeddings, batch_size
        return np.asarray([[1.0, 0.0] for _ in sentences], dtype=np.float64)


def _long_evidence(sentence_count: int = 2_400) -> HarnessEvidence:
    base = _case()
    sentences = [
        HarnessEvidenceSentence(
            id=f"long-sentence-{index:04d}",
            startMs=index * 6_000,
            endMs=(index + 1) * 6_000,
            speakers=["speaker-1"],
            text=(
                "A rareterm irrigation finding changes the recommendation."
                if index == 100
                else f"Source discussion sentence {index}."
            ),
            wordIds=[WordId(root=f"long-word-{index:04d}")],
        )
        for index in range(sentence_count)
    ]
    return base.model_copy(update={"sentences": sentences, "durationMs": sentence_count * 6_000})


def _node_ids(index: TopicSourceIndex, kind: str) -> list[str]:
    return [node.id for node in index.nodes if node.kind.value == kind]


def test_four_hour_source_is_indexed_without_entering_the_model_prompt() -> None:
    evidence = _long_evidence()
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    overview = source_index_map(index, index_sha256="c" * 64)

    assert evidence.durationMs == 4 * 60 * 60 * 1_000
    assert _node_ids(index, "episode") == ["episode"]
    assert len(_node_ids(index, "section")) == 10
    assert len(_node_ids(index, "region")) == 75
    assert len(index.sentences) == 2_400
    assert "Source discussion sentence" not in json.dumps(overview)
    assert len(json.dumps(overview)) < 500

    root = browse_topic_source(index, index_sha256="c" * 64, limit=16)
    assert [node.id for node in root.nodes] == _node_ids(index, "section")
    seen: list[str] = []
    for section in root.nodes:
        page = browse_topic_source(index, index_sha256="c" * 64, parent_id=section.id, limit=16)
        assert page.complete
        seen.extend(node.id for node in page.nodes)
    assert seen == _node_ids(index, "region")


def test_hybrid_search_and_exact_read_are_bounded_and_paginated() -> None:
    evidence = _long_evidence(160)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    results = search_topic_source(
        index,
        index_sha256="c" * 64,
        query="rareterm irrigation",
        encoder=FixtureEncoder(),
        limit=3,
    )
    assert results.regions[0].id == "region-0004"
    assert all(node.kind.value == "region" for node in results.regions)

    page = read_topic_source(
        index,
        index_sha256="c" * 64,
        first_sentence_id="long-sentence-0096",
        last_sentence_id="long-sentence-0110",
        limit=5,
    )
    assert [sentence.id for sentence in page.sentences] == [
        f"long-sentence-{index:04d}" for index in range(96, 101)
    ]
    assert page.nextSentenceId == "long-sentence-0101"
    assert not page.complete


def test_inspection_trace_proves_complete_map_browse_and_exact_read() -> None:
    evidence = _long_evidence(8)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    browse_root = browse_topic_source(index, index_sha256="c" * 64, cursor=0, limit=8)
    browse_section = browse_topic_source(
        index,
        index_sha256="c" * 64,
        parent_id=browse_root.nodes[0].id,
        cursor=0,
        limit=8,
    )
    search = search_topic_source(
        index,
        index_sha256="c" * 64,
        query="garden",
        encoder=FixtureEncoder(),
        limit=3,
    )
    read = read_topic_source(
        index,
        index_sha256="c" * 64,
        first_sentence_id=index.sentences[0].id,
        last_sentence_id=index.sentences[-1].id,
        limit=8,
    )
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "episode", "cursor": 0, "limit": 8},
                    tool_call_id="browse-root",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("browse_source", browse_root, tool_call_id="browse-root")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "section-0001", "cursor": 0, "limit": 8},
                    tool_call_id="browse-section",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("browse_source", browse_section, tool_call_id="browse-section")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_source",
                    {"query": "garden", "cursor": 0, "limit": 3},
                    tool_call_id="search",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(parts=[ToolReturnPart("search_source", search, tool_call_id="search")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "read_source",
                    {
                        "first_sentence_id": index.sentences[0].id,
                        "last_sentence_id": index.sentences[-1].id,
                        "limit": 8,
                    },
                    tool_call_id="read",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(parts=[ToolReturnPart("read_source", read, tool_call_id="read")]),
    ]
    trace = source_inspection_trace(
        messages,
        index_sha256="c" * 64,
        role="source_reviewer",
        stage="verify:selection:source:0",
    )
    validate_source_inspection(index, trace)
    validate_source_read_ids(index, trace, {index.sentences[0].id, index.sentences[-1].id})
    assert [call.tool_name for call in trace.calls] == [
        "browse_source",
        "browse_source",
        "search_source",
        "read_source",
    ]
    assert trace.calls[3].sentence_ids == tuple(sentence.id for sentence in index.sentences)


def test_inspection_trace_cannot_skip_to_the_final_browse_page() -> None:
    evidence = _long_evidence(40)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    final_page = browse_topic_source(index, index_sha256="c" * 64, cursor=1, limit=16)
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "episode", "cursor": 1, "limit": 16},
                    tool_call_id="b",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(parts=[ToolReturnPart("browse_source", final_page, tool_call_id="b")]),
    ]
    trace = source_inspection_trace(
        messages,
        index_sha256="c" * 64,
        role="inventory",
        stage="verify:selection:inventory:0",
    )

    with pytest.raises(HarnessValidationError, match="chronological browse cursor"):
        validate_source_inspection(index, trace)


def test_index_validator_rejects_broken_hierarchy_derivations() -> None:
    evidence = _long_evidence(300)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )

    broken_parent = index.model_copy(deep=True)
    broken_parent.nodes[-1].parentId = "section-0001"
    with pytest.raises(HarnessValidationError, match="ordered partition"):
        validate_topic_source_index(evidence, broken_parent, evidence_sha256="a" * 64)

    broken_summary = index.model_copy(deep=True)
    broken_summary.nodes[1].preview = "A summary that was not derived from exact source speech."
    with pytest.raises(HarnessValidationError, match="exactly own leaf regions"):
        validate_topic_source_index(evidence, broken_summary, evidence_sha256="a" * 64)

    broken_vector = index.model_copy(deep=True)
    broken_vector.nodes[1].embedding = [0.0, 1.0]
    with pytest.raises(HarnessValidationError, match="exactly own leaf regions"):
        validate_topic_source_index(evidence, broken_vector, evidence_sha256="a" * 64)


def test_inspection_trace_must_browse_each_section_in_root_order() -> None:
    evidence = _long_evidence(300)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    root = browse_topic_source(index, index_sha256="c" * 64, limit=8)
    section_one = browse_topic_source(
        index, index_sha256="c" * 64, parent_id="section-0001", limit=8
    )

    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "episode", "cursor": 0, "limit": 8},
                    tool_call_id="root",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(parts=[ToolReturnPart("browse_source", root, tool_call_id="root")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "section-0001", "cursor": 0, "limit": 8},
                    tool_call_id="section-one",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("browse_source", section_one, tool_call_id="section-one")]
        ),
    ]
    trace = source_inspection_trace(
        messages,
        index_sha256="c" * 64,
        role="inventory",
        stage="verify:selection:inventory:0",
    )
    with pytest.raises(HarnessValidationError, match="every hierarchy node"):
        validate_source_inspection(index, trace)

    section_two = browse_topic_source(
        index, index_sha256="c" * 64, parent_id="section-0002", limit=8
    )
    messages[2:] = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "browse_source",
                    {"parent_id": "section-0002", "cursor": 0, "limit": 8},
                    tool_call_id="section-two",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("browse_source", section_two, tool_call_id="section-two")]
        ),
    ]
    trace = source_inspection_trace(
        messages,
        index_sha256="c" * 64,
        role="inventory",
        stage="verify:selection:inventory:0",
    )
    with pytest.raises(HarnessValidationError, match="chronological browse cursor"):
        validate_source_inspection(index, trace)


def test_browse_refuses_leaf_nodes() -> None:
    evidence = _long_evidence(8)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )

    with pytest.raises(ValueError, match="leaf region"):
        browse_topic_source(index, index_sha256="c" * 64, parent_id="region-0001", limit=8)
