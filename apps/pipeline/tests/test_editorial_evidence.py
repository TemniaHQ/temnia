"""Candidate navigation and measured-media tool projections stay bounded and replayable."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import numpy as np
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from temnia_pipeline.contracts import (
    HarnessEvidence,
    TopicSelectionRecord,
    TopicSentenceSpan,
    TopicSourceIndex,
)
from temnia_pipeline.harness.editorial_evidence import (
    inspect_topic_candidate,
    read_topic_media_evidence,
    validate_reviewer_inspection,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    topic_selection_qualification_case,
)
from temnia_pipeline.harness.source_index import build_topic_source_index
from temnia_pipeline.harness.source_progress import (
    compact_source_messages,
    inspection_from_messages,
)
from temnia_pipeline.harness.topic_selection_runtime import (
    SourceInspectionCall,
    SourceInspectionTrace,
)
from test_topic_source_index import _long_evidence


class FixtureEncoder:
    def encode(
        self, sentences: list[str], *, normalize_embeddings: bool = True, batch_size: int = 32
    ) -> object:
        _ = normalize_embeddings, batch_size
        return np.asarray([[1.0, 0.0] for _ in sentences], dtype=np.float64)


def _candidate_case() -> tuple[HarnessEvidence, TopicSourceIndex, TopicSelectionRecord]:
    evidence = _long_evidence(100)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    _, record, _ = topic_selection_qualification_case()
    span = TopicSentenceSpan(
        firstSentenceId=evidence.sentences[0].id,
        lastSentenceId=evidence.sentences[-1].id,
    )
    candidate = record.draft.proposal.candidates[0].model_copy(
        update={
            "firstSentenceId": span.firstSentenceId,
            "lastSentenceId": span.lastSentenceId,
            "coreSpans": [span],
            "completionSpans": [span],
        }
    )
    proposal = record.draft.proposal.model_copy(update={"candidates": [candidate]})
    draft = record.draft.model_copy(update={"proposal": proposal})
    return evidence, index, record.model_copy(update={"draft": draft, "evidenceSha256": "a" * 64})


def test_candidate_inspection_pages_every_intersecting_leaf() -> None:
    _, index, selection = _candidate_case()
    first = inspect_topic_candidate(
        index,
        selection,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        candidate_id="garden-care",
        limit=2,
    )
    second = inspect_topic_candidate(
        index,
        selection,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        candidate_id="garden-care",
        cursor=first.nextCursor or 0,
        limit=2,
    )

    assert [item.id for item in first.regions] == ["region-0001", "region-0002"]
    assert first.nextCursor == 2
    assert [item.id for item in second.regions] == ["region-0003", "region-0004"]
    assert second.complete
    assert all(item.relation == "inside_candidate" for item in first.regions + second.regions)


def test_media_evidence_pages_measured_events_without_transcript_text() -> None:
    evidence, _, _ = topic_selection_qualification_case()
    first = read_topic_media_evidence(
        evidence,
        evidence_sha256="a" * 64,
        first_sentence_id=evidence.sentences[0].id,
        last_sentence_id=evidence.sentences[-1].id,
        limit=3,
    )
    second = read_topic_media_evidence(
        evidence,
        evidence_sha256="a" * 64,
        first_sentence_id=evidence.sentences[0].id,
        last_sentence_id=evidence.sentences[-1].id,
        cursor=first.nextCursor or 0,
        limit=80,
    )

    events = [*first.events, *second.events]
    assert first.nextCursor == 3
    assert second.complete
    assert {item.kind.value for item in events} >= {"sentence", "boundary"}
    sentence = next(item for item in events if item.kind.value == "sentence")
    assert sentence.wordCount is not None
    assert sentence.alignedWordCount is not None
    assert sentence.interpolatedWordCount is not None
    assert sentence.wordCount == sentence.alignedWordCount + sentence.interpolatedWordCount
    assert "text" not in first.model_dump(mode="json")["events"][0]


def test_reviewer_inspection_requires_complete_candidate_pages_and_replays_media() -> None:
    evidence, selection, _ = topic_selection_qualification_case()
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    candidate_page = inspect_topic_candidate(
        index,
        selection,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        candidate_id="garden-care",
        limit=2,
    )
    media = read_topic_media_evidence(
        evidence,
        evidence_sha256="a" * 64,
        first_sentence_id=evidence.sentences[0].id,
        last_sentence_id=evidence.sentences[0].id,
        limit=80,
    )
    calls = (
        SourceInspectionCall(
            tool_name="inspect_candidate",
            arguments={"candidate_id": "garden-care", "limit": 2},
            node_ids=tuple(item.id for item in candidate_page.regions),
            complete=candidate_page.complete,
            next_cursor=candidate_page.nextCursor,
        ),
        SourceInspectionCall(
            tool_name="read_media_evidence",
            arguments={
                "first_sentence_id": evidence.sentences[0].id,
                "last_sentence_id": evidence.sentences[0].id,
                "limit": 80,
            },
            evidence_ids=tuple(item.id for item in media.events),
            complete=media.complete,
            next_cursor=media.nextCursor,
        ),
    )
    trace = SourceInspectionTrace(
        index_sha256="c" * 64,
        role="source_reviewer",
        stage="verify:selection:source:0",
        calls=calls,
    )

    validate_reviewer_inspection(
        index,
        selection,
        evidence,
        trace,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        evidence_sha256="a" * 64,
    )
    missing = trace.model_copy(update={"calls": calls[1:]})
    with pytest.raises(ValueError, match="did not inspect candidate"):
        validate_reviewer_inspection(
            index,
            selection,
            evidence,
            missing,
            index_sha256="c" * 64,
            selection_sha256="d" * 64,
            evidence_sha256="a" * 64,
        )


def test_reviewer_tool_results_survive_checkpoint_compaction() -> None:
    evidence, selection, _ = topic_selection_qualification_case()
    index = build_topic_source_index(
        evidence,
        evidence_sha256="a" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    candidate_page = inspect_topic_candidate(
        index,
        selection,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        candidate_id="garden-care",
        limit=16,
    )
    media_page = read_topic_media_evidence(
        evidence,
        evidence_sha256="a" * 64,
        first_sentence_id=evidence.sentences[1].id,
        last_sentence_id=evidence.sentences[3].id,
        limit=80,
    )
    stage = "verify:selection:source:0"
    messages = [
        ModelRequest(parts=[UserPromptPart("Inspect every candidate before reviewing.")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "inspect_candidate",
                    {"candidate_id": "garden-care", "limit": 16},
                    tool_call_id="candidate",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("inspect_candidate", candidate_page, tool_call_id="candidate")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "read_media_evidence",
                    {
                        "first_sentence_id": evidence.sentences[1].id,
                        "last_sentence_id": evidence.sentences[3].id,
                        "limit": 80,
                    },
                    tool_call_id="media",
                )
            ],
            finish_reason="tool_call",
        ),
        ModelRequest(
            parts=[ToolReturnPart("read_media_evidence", media_page, tool_call_id="media")]
        ),
    ]
    compacted = compact_source_messages(
        messages,
        index_sha256="c" * 64,
        role="source_reviewer",
        stage=stage,
    )
    trace = inspection_from_messages(
        compacted,
        index_sha256="c" * 64,
        role="source_reviewer",
        stage=stage,
    )

    assert trace.calls[0].node_ids == tuple(item.id for item in candidate_page.regions)
    assert trace.calls[1].evidence_ids == tuple(item.id for item in media_page.events)
    assert all(":" in identifier for identifier in trace.calls[1].evidence_ids)
    validate_reviewer_inspection(
        index,
        selection,
        evidence,
        trace,
        index_sha256="c" * 64,
        selection_sha256="d" * 64,
        evidence_sha256="a" * 64,
    )
