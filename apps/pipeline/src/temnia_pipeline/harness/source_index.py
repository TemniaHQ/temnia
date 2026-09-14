"""Immutable, bounded source access for long-form editorial agents."""

# Validation messages are the public refusal at this pure boundary.
# ruff: noqa: C901, EM101, EM102, PLR0913, TRY003

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from temnia_pipeline.contracts import (
    HarnessEvidence,
    TopicSourceBrowsePage,
    TopicSourceIndex,
    TopicSourceIndexNode,
    TopicSourceIndexSentence,
    TopicSourceNodeHit,
    TopicSourceReadPage,
    TopicSourceSearchPage,
)
from temnia_pipeline.harness.topic_selection_runtime import (
    SourceInspectionCall,
    SourceInspectionTrace,
    SourceToolRole,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.substrate.backends import (
    LoadedModel,
    TextEncoder,
    encode_sentences,
    load_encoder,
)
from temnia_pipeline.substrate.changepoint import DEFAULT_EMBEDDING_MODEL

if TYPE_CHECKING:
    from collections.abc import Sequence

REGION_MAX_SENTENCES = 32
REGION_MAX_CHARACTERS = 8_000
SECTION_MAX_REGIONS = 8
ROOT_NODE_ID = "episode"
EMBEDDING_UNIT_MAX_CHARACTERS = 800
REGION_KEYWORDS = 10
REGION_PREVIEW_CHARACTERS = 240
MAX_INDEX_SENTENCE_CHARACTERS = 64_000
MAX_QUERY_CHARACTERS = 512
MAX_READ_CHARACTERS = 64_000
MAX_BROWSE_LIMIT = 16
MAX_SEARCH_LIMIT = 12
MAX_READ_SENTENCES = 80
TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "not",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    }
)


def _node_kind(node: TopicSourceIndexNode) -> str:
    """Return the generated string-enum value without depending on its ordinal name."""
    return node.kind.value


def _child_ids(node: TopicSourceIndexNode) -> list[str]:
    """Unwrap code-generated constrained string roots at the contract boundary."""
    return [identifier.root for identifier in node.childIds]


@lru_cache(maxsize=4)
def load_topic_source_encoder(
    model: str = DEFAULT_EMBEDDING_MODEL, revision: str | None = None
) -> LoadedModel[TextEncoder]:
    """Reuse one immutable CPU encoder per worker process and verify requested revisions."""
    loaded = load_encoder(f"{model}@{revision}" if revision is not None else model)
    if revision is not None and loaded.revision != revision:
        raise HarnessValidationError("source-index encoder revision changed")
    return loaded


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in (match.group(0).casefold() for match in TOKEN.finditer(text))
        if len(token) > 1 and token not in STOPWORDS
    ]


def _preview(sentences: Sequence[TopicSourceIndexSentence]) -> str:
    positions = tuple(dict.fromkeys((0, len(sentences) // 2, len(sentences) - 1)))
    return " … ".join(
        sentences[position].text.strip()[:REGION_PREVIEW_CHARACTERS] for position in positions
    )


def _keywords(sentences: Sequence[TopicSourceIndexSentence]) -> list[str]:
    counts = Counter(_tokens(" ".join(sentence.text for sentence in sentences)))
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))][
        :REGION_KEYWORDS
    ]


def _partition(
    sentences: Sequence[TopicSourceIndexSentence],
) -> list[list[TopicSourceIndexSentence]]:
    regions: list[list[TopicSourceIndexSentence]] = []
    current: list[TopicSourceIndexSentence] = []
    characters = 0
    for sentence in sentences:
        size = len(sentence.text) + (1 if current else 0)
        if current and (
            len(current) >= REGION_MAX_SENTENCES or characters + size > REGION_MAX_CHARACTERS
        ):
            regions.append(current)
            current = []
            characters = 0
        current.append(sentence)
        characters += len(sentence.text) + (1 if len(current) > 1 else 0)
    if current:
        regions.append(current)
    return regions


def build_topic_source_index(
    evidence: HarnessEvidence,
    *,
    evidence_sha256: str,
    encoder: TextEncoder,
    embedding_revision: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> TopicSourceIndex:
    """Build one exact episode -> section -> region index with pinned vectors."""
    sentences = [
        TopicSourceIndexSentence(
            id=item.id,
            startMs=item.startMs,
            endMs=item.endMs,
            speakers=item.speakers,
            text=item.text,
        )
        for item in evidence.sentences
    ]
    if not sentences:
        raise HarnessValidationError("a source index requires at least one transcript sentence")
    if any(len(sentence.text) > MAX_INDEX_SENTENCE_CHARACTERS for sentence in sentences):
        raise HarnessValidationError("a transcript sentence exceeds the bounded source-read size")
    groups = _partition(sentences)
    embedding_units: list[str] = []
    region_unit_ranges: list[tuple[int, int]] = []
    for group in groups:
        start = len(embedding_units)
        for sentence in group:
            text = sentence.text or " "
            embedding_units.extend(
                text[offset : offset + EMBEDDING_UNIT_MAX_CHARACTERS]
                for offset in range(0, len(text), EMBEDDING_UNIT_MAX_CHARACTERS)
            )
        region_unit_ranges.append((start, len(embedding_units)))
    unit_matrix = encode_sentences(encoder, embedding_units)
    dimensions = int(unit_matrix.shape[1])
    region_vectors: list[list[float]] = []
    for start, end in region_unit_ranges:
        vector = np.mean(unit_matrix[start:end], axis=0)
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise HarnessValidationError("source-index encoder returned an unusable region vector")
        region_vectors.append((vector / norm).tolist())
    regions = [
        TopicSourceIndexNode.model_validate(
            {
                "id": f"region-{ordinal + 1:04d}",
                "kind": "region",
                "parentId": f"section-{ordinal // SECTION_MAX_REGIONS + 1:04d}",
                "ordinal": ordinal,
                "childIds": [],
                "firstSentenceId": group[0].id,
                "lastSentenceId": group[-1].id,
                "startMs": group[0].startMs,
                "endMs": group[-1].endMs,
                "sentenceCount": len(group),
                "keywords": _keywords(group),
                "preview": _preview(group),
                "embedding": region_vectors[ordinal],
            }
        )
        for ordinal, group in enumerate(groups)
    ]
    sections: list[TopicSourceIndexNode] = []
    for ordinal, start in enumerate(range(0, len(regions), SECTION_MAX_REGIONS)):
        children = regions[start : start + SECTION_MAX_REGIONS]
        first = next(
            offset
            for offset, sentence in enumerate(sentences)
            if sentence.id == children[0].firstSentenceId
        )
        last = next(
            offset
            for offset, sentence in enumerate(sentences)
            if sentence.id == children[-1].lastSentenceId
        )
        section_sentences = sentences[first : last + 1]
        vector = np.average(
            np.asarray([child.embedding for child in children], dtype=np.float64),
            axis=0,
            weights=[child.sentenceCount for child in children],
        )
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise HarnessValidationError("source-index section vector is unusable")
        sections.append(
            TopicSourceIndexNode.model_validate(
                {
                    "id": f"section-{ordinal + 1:04d}",
                    "kind": "section",
                    "parentId": ROOT_NODE_ID,
                    "ordinal": ordinal,
                    "childIds": [child.id for child in children],
                    "firstSentenceId": section_sentences[0].id,
                    "lastSentenceId": section_sentences[-1].id,
                    "startMs": section_sentences[0].startMs,
                    "endMs": section_sentences[-1].endMs,
                    "sentenceCount": len(section_sentences),
                    "keywords": _keywords(section_sentences),
                    "preview": _preview(section_sentences),
                    "embedding": (vector / norm).tolist(),
                }
            )
        )
    root_vector = np.average(
        np.asarray([section.embedding for section in sections], dtype=np.float64),
        axis=0,
        weights=[section.sentenceCount for section in sections],
    )
    root_norm = float(np.linalg.norm(root_vector))
    if not math.isfinite(root_norm) or root_norm <= 0:
        raise HarnessValidationError("source-index episode vector is unusable")
    episode = TopicSourceIndexNode.model_validate(
        {
            "id": ROOT_NODE_ID,
            "kind": "episode",
            "parentId": None,
            "ordinal": 0,
            "childIds": [section.id for section in sections],
            "firstSentenceId": sentences[0].id,
            "lastSentenceId": sentences[-1].id,
            "startMs": sentences[0].startMs,
            "endMs": sentences[-1].endMs,
            "sentenceCount": len(sentences),
            "keywords": _keywords(sentences),
            "preview": _preview(sentences),
            "embedding": (root_vector / root_norm).tolist(),
        }
    )
    index = TopicSourceIndex(
        format="topic-source-index/2",
        evidenceSha256=evidence_sha256,
        sourceId=evidence.sourceId,
        transcriptId=evidence.transcriptId,
        transcriptRevision=evidence.transcriptRevision,
        embeddingModel=embedding_model,
        embeddingRevision=embedding_revision,
        embeddingDimensions=dimensions,
        regionMaxSentences=REGION_MAX_SENTENCES,
        regionMaxCharacters=REGION_MAX_CHARACTERS,
        sectionMaxRegions=SECTION_MAX_REGIONS,
        rootNodeId=ROOT_NODE_ID,
        sentences=sentences,
        nodes=[episode, *sections, *regions],
    )
    validate_topic_source_index(evidence, index, evidence_sha256=evidence_sha256)
    return index


def validate_topic_source_index(  # noqa: PLR0912
    evidence: HarnessEvidence, index: TopicSourceIndex, *, evidence_sha256: str
) -> None:
    """Prove exact sentence coverage and every episode/section/region edge."""
    if (
        index.evidenceSha256 != evidence_sha256
        or index.sourceId != evidence.sourceId
        or index.transcriptId != evidence.transcriptId
        or index.transcriptRevision != evidence.transcriptRevision
    ):
        raise HarnessValidationError("source index differs from its accepted evidence identity")
    expected = [
        (item.id, item.startMs, item.endMs, list(item.speakers), item.text)
        for item in evidence.sentences
    ]
    actual = [
        (item.id, item.startMs, item.endMs, list(item.speakers), item.text)
        for item in index.sentences
    ]
    if actual != expected:
        raise HarnessValidationError("source index sentences do not exactly match the evidence")
    if (
        index.rootNodeId != ROOT_NODE_ID
        or index.regionMaxSentences != REGION_MAX_SENTENCES
        or index.regionMaxCharacters != REGION_MAX_CHARACTERS
        or index.sectionMaxRegions != SECTION_MAX_REGIONS
    ):
        raise HarnessValidationError("source index hierarchy configuration changed")
    node_by_id = {node.id: node for node in index.nodes}
    if len(node_by_id) != len(index.nodes):
        raise HarnessValidationError("source index node IDs are not unique")
    try:
        root = node_by_id[index.rootNodeId]
    except KeyError as error:
        raise HarnessValidationError("source index root node is missing") from error
    sections = [node for node in index.nodes if _node_kind(node) == "section"]
    regions = [node for node in index.nodes if _node_kind(node) == "region"]
    if (
        index.nodes != [root, *sections, *regions]
        or _node_kind(root) != "episode"
        or root.parentId is not None
        or root.ordinal != 0
        or _child_ids(root) != [section.id for section in sections]
        or not sections
        or not regions
    ):
        raise HarnessValidationError("source index node order or root hierarchy is invalid")
    position = {sentence.id: offset for offset, sentence in enumerate(index.sentences)}
    cursor = 0
    for ordinal, region in enumerate(regions):
        try:
            first = position[region.firstSentenceId]
            last = position[region.lastSentenceId]
        except KeyError as error:
            raise HarnessValidationError("source index region names an unknown sentence") from error
        if (
            region.id != f"region-{ordinal + 1:04d}"
            or region.ordinal != ordinal
            or region.parentId != f"section-{ordinal // SECTION_MAX_REGIONS + 1:04d}"
            or region.childIds
            or first != cursor
            or last < first
            or region.sentenceCount != last - first + 1
            or region.startMs != index.sentences[first].startMs
            or region.endMs != index.sentences[last].endMs
            or region.keywords != _keywords(index.sentences[first : last + 1])
            or region.preview != _preview(index.sentences[first : last + 1])
            or len(region.embedding) != index.embeddingDimensions
            or not all(math.isfinite(value) for value in region.embedding)
            or not math.isclose(
                math.sqrt(sum(value * value for value in region.embedding)),
                1.0,
                rel_tol=1e-4,
                abs_tol=1e-4,
            )
        ):
            raise HarnessValidationError("source index regions are not an exact ordered partition")
        cursor = last + 1
    if cursor != len(index.sentences):
        raise HarnessValidationError("source index regions do not cover every sentence")
    for ordinal, section in enumerate(sections):
        children = regions[ordinal * SECTION_MAX_REGIONS : (ordinal + 1) * SECTION_MAX_REGIONS]
        if not children:
            raise HarnessValidationError("source index contains an empty section")
        first = position[children[0].firstSentenceId]
        last = position[children[-1].lastSentenceId]
        section_sentences = index.sentences[first : last + 1]
        expected_vector = np.average(
            np.asarray([child.embedding for child in children], dtype=np.float64),
            axis=0,
            weights=[child.sentenceCount for child in children],
        )
        expected_vector /= np.linalg.norm(expected_vector)
        if (
            section.id != f"section-{ordinal + 1:04d}"
            or section.ordinal != ordinal
            or section.parentId != root.id
            or _child_ids(section) != [child.id for child in children]
            or section.firstSentenceId != children[0].firstSentenceId
            or section.lastSentenceId != children[-1].lastSentenceId
            or section.startMs != children[0].startMs
            or section.endMs != children[-1].endMs
            or section.sentenceCount != last - first + 1
            or section.keywords != _keywords(section_sentences)
            or section.preview != _preview(section_sentences)
            or not np.allclose(section.embedding, expected_vector, rtol=1e-6, atol=1e-6)
        ):
            raise HarnessValidationError("source index sections do not exactly own leaf regions")
    expected_root_vector = np.average(
        np.asarray([section.embedding for section in sections], dtype=np.float64),
        axis=0,
        weights=[section.sentenceCount for section in sections],
    )
    expected_root_vector /= np.linalg.norm(expected_root_vector)
    if (
        root.firstSentenceId != index.sentences[0].id
        or root.lastSentenceId != index.sentences[-1].id
        or root.startMs != index.sentences[0].startMs
        or root.endMs != index.sentences[-1].endMs
        or root.sentenceCount != len(index.sentences)
        or root.keywords != _keywords(index.sentences)
        or root.preview != _preview(index.sentences)
        or not np.allclose(root.embedding, expected_root_vector, rtol=1e-6, atol=1e-6)
    ):
        raise HarnessValidationError("source index root does not own the complete episode")
    for node in index.nodes:
        if (
            len(node.embedding) != index.embeddingDimensions
            or not all(math.isfinite(value) for value in node.embedding)
            or not math.isclose(
                math.sqrt(sum(value * value for value in node.embedding)),
                1.0,
                rel_tol=1e-4,
                abs_tol=1e-4,
            )
        ):
            raise HarnessValidationError("source index node has an unusable vector")


def source_index_map(index: TopicSourceIndex, *, index_sha256: str) -> dict[str, object]:
    """Compact index identity; ordered discovery stays in bounded browse calls."""
    return {
        "indexSha256": index_sha256,
        "rootNodeId": index.rootNodeId,
        "hierarchyDepth": 3,
        "sectionCount": sum(_node_kind(node) == "section" for node in index.nodes),
        "regionCount": sum(_node_kind(node) == "region" for node in index.nodes),
        "sentenceCount": len(index.sentences),
        "durationMs": index.sentences[-1].endMs,
        "browsePageLimit": MAX_BROWSE_LIMIT,
        "searchPageLimit": MAX_SEARCH_LIMIT,
        "readSentenceLimit": MAX_READ_SENTENCES,
        "readCharacterLimit": MAX_READ_CHARACTERS,
    }


def _node_hit(node: TopicSourceIndexNode, *, score: float | None) -> TopicSourceNodeHit:
    if _node_kind(node) == "episode" or node.parentId is None:
        raise ValueError("the episode root is browse authority, not a returned child")
    return TopicSourceNodeHit.model_validate(
        {
            "id": node.id,
            "kind": _node_kind(node),
            "parentId": node.parentId,
            "childCount": len(node.childIds),
            "firstSentenceId": node.firstSentenceId,
            "lastSentenceId": node.lastSentenceId,
            "startMs": node.startMs,
            "endMs": node.endMs,
            "sentenceCount": node.sentenceCount,
            "keywords": node.keywords,
            "preview": node.preview,
            "score": score,
        }
    )


def browse_topic_source(
    index: TopicSourceIndex,
    *,
    index_sha256: str,
    parent_id: str = ROOT_NODE_ID,
    cursor: int = 0,
    limit: int = 8,
) -> TopicSourceBrowsePage:
    """Page through one hierarchy parent's children in source order."""
    if cursor < 0 or not 1 <= limit <= MAX_BROWSE_LIMIT:
        raise ValueError(f"browse cursor must be nonnegative and limit 1..{MAX_BROWSE_LIMIT}")
    node_by_id = {node.id: node for node in index.nodes}
    try:
        parent = node_by_id[parent_id]
    except KeyError as error:
        raise ValueError("browse parent names an unknown source-index node") from error
    if _node_kind(parent) == "region":
        raise ValueError("a leaf region has no browseable children")
    children = [node_by_id[identifier] for identifier in _child_ids(parent)]
    if cursor > len(children):
        raise ValueError("browse cursor is outside the parent's children")
    end = min(len(children), cursor + limit)
    return TopicSourceBrowsePage(
        indexSha256=index_sha256,
        parentId=parent.id,
        nodes=[_node_hit(node, score=None) for node in children[cursor:end]],
        nextCursor=end if end < len(children) else None,
        complete=end == len(children),
    )


def search_topic_source(
    index: TopicSourceIndex,
    *,
    index_sha256: str,
    query: str,
    encoder: TextEncoder,
    cursor: int = 0,
    limit: int = 6,
) -> TopicSourceSearchPage:
    """Rank regions with lexical evidence and pinned semantic similarity."""
    query = query.strip()
    if not query or len(query) > MAX_QUERY_CHARACTERS:
        raise ValueError(f"search query must contain 1..{MAX_QUERY_CHARACTERS} characters")
    if cursor < 0 or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"search cursor must be nonnegative and limit 1..{MAX_SEARCH_LIMIT}")
    regions = [node for node in index.nodes if _node_kind(node) == "region"]
    if cursor > len(regions):
        raise ValueError("search cursor is outside the ranked region set")
    query_vector = encode_sentences(encoder, [query])[0]
    region_matrix = np.asarray([region.embedding for region in regions], dtype=np.float64)
    semantic = region_matrix @ query_vector
    lexical_raw = _bm25_scores(index, regions, set(_tokens(query)))
    lexical = lexical_raw / lexical_raw.max() if lexical_raw.max() > 0 else lexical_raw
    scores = 0.65 * ((semantic + 1.0) / 2.0) + 0.35 * lexical
    ranked = sorted(range(len(regions)), key=lambda item: (-scores[item], item))
    end = min(len(ranked), cursor + limit)
    return TopicSourceSearchPage(
        indexSha256=index_sha256,
        query=query,
        regions=[
            _node_hit(regions[item], score=float(scores[item])) for item in ranked[cursor:end]
        ],
        nextCursor=end if end < len(regions) else None,
        complete=end == len(ranked),
    )


def _region_text(index: TopicSourceIndex, region: TopicSourceIndexNode) -> str:
    positions = {sentence.id: offset for offset, sentence in enumerate(index.sentences)}
    return " ".join(
        item.text
        for item in index.sentences[
            positions[region.firstSentenceId] : positions[region.lastSentenceId] + 1
        ]
    )


def _bm25_scores(
    index: TopicSourceIndex,
    regions: Sequence[TopicSourceIndexNode],
    query_terms: set[str],
) -> np.ndarray:
    """Okapi BM25 over the immutable region corpus."""
    documents = [Counter(_tokens(_region_text(index, region))) for region in regions]
    lengths = [sum(document.values()) for document in documents]
    average = sum(lengths) / len(lengths) if lengths else 1.0
    scores = np.zeros(len(documents), dtype=np.float64)
    for term in query_terms:
        document_frequency = sum(term in document for document in documents)
        inverse = math.log(
            1.0 + (len(documents) - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        for offset, document in enumerate(documents):
            frequency = document[term]
            if frequency:
                denominator = frequency + 1.2 * (0.25 + 0.75 * lengths[offset] / average)
                scores[offset] += inverse * frequency * 2.2 / denominator
    return scores


def read_topic_source(
    index: TopicSourceIndex,
    *,
    index_sha256: str,
    first_sentence_id: str,
    last_sentence_id: str,
    cursor_sentence_id: str | None = None,
    limit: int = 40,
) -> TopicSourceReadPage:
    """Return an exact bounded sentence range with an explicit continuation ID."""
    if not 1 <= limit <= MAX_READ_SENTENCES:
        raise ValueError(f"read limit must be 1..{MAX_READ_SENTENCES}")
    positions = {sentence.id: offset for offset, sentence in enumerate(index.sentences)}
    try:
        first = positions[first_sentence_id]
        last = positions[last_sentence_id]
        cursor = positions[cursor_sentence_id] if cursor_sentence_id is not None else first
    except KeyError as error:
        raise ValueError("read range names an unknown sentence") from error
    if last < first or cursor < first or cursor > last:
        raise ValueError("read range or cursor is reversed")
    end = cursor
    characters = 0
    while end <= last and end < cursor + limit:
        next_size = len(index.sentences[end].text)
        if end > cursor and characters + next_size > MAX_READ_CHARACTERS:
            break
        characters += next_size
        end += 1
    return TopicSourceReadPage(
        indexSha256=index_sha256,
        sentences=index.sentences[cursor:end],
        nextSentenceId=index.sentences[end].id if end <= last else None,
        complete=end > last,
    )


def source_inspection_trace(
    messages: Sequence[ModelMessage],
    *,
    index_sha256: str,
    role: SourceToolRole,
    stage: str,
) -> SourceInspectionTrace:
    """Reduce successful tool messages to replay-safe access facts without source prose."""
    pending: dict[str, ToolCallPart] = {}
    calls: list[SourceInspectionCall] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in {
                    "browse_source",
                    "search_source",
                    "read_source",
                }:
                    pending[part.tool_call_id] = part
        else:
            for part in message.parts:
                if not isinstance(part, ToolReturnPart) or part.outcome != "success":
                    continue
                request = pending.get(part.tool_call_id)
                if request is None:
                    continue
                content = part.content
                if isinstance(content, BaseModel):
                    payload = content.model_dump(mode="json")
                elif isinstance(content, dict):
                    payload = cast("dict[str, Any]", content)
                else:
                    continue
                if payload.get("indexSha256") != index_sha256:
                    continue
                nodes = cast("list[object]", payload.get("nodes", payload.get("regions", [])))
                sentences = cast("list[object]", payload.get("sentences", []))
                calls.append(
                    SourceInspectionCall.model_validate(
                        {
                            "tool_name": request.tool_name,
                            "arguments": request.args_as_dict(raise_if_invalid=True),
                            "node_ids": [
                                str(cast("dict[str, Any]", item)["id"])
                                for item in nodes
                                if isinstance(item, dict)
                                and isinstance(cast("dict[str, Any]", item).get("id"), str)
                            ],
                            "sentence_ids": [
                                str(cast("dict[str, Any]", item)["id"])
                                for item in sentences
                                if isinstance(item, dict)
                                and isinstance(cast("dict[str, Any]", item).get("id"), str)
                            ],
                            "complete": payload.get("complete") is True,
                            "next_cursor": payload.get("nextCursor"),
                            "next_sentence_id": payload.get("nextSentenceId"),
                        }
                    )
                )
    return SourceInspectionTrace(
        index_sha256=index_sha256, role=role, stage=stage, calls=tuple(calls)
    )


def validate_source_inspection(  # noqa: PLR0912, PLR0915
    index: TopicSourceIndex, trace: SourceInspectionTrace
) -> None:
    """Require deterministic hierarchy traversal, hybrid lookup and exact reads."""
    browse_calls = [call for call in trace.calls if call.tool_name == "browse_source"]
    node_by_id = {node.id: node for node in index.nodes}
    root = node_by_id[index.rootNodeId]
    parents = [root, *(node_by_id[identifier] for identifier in _child_ids(root))]
    parent_offset = 0
    cursor = 0
    for call in browse_calls:
        if parent_offset >= len(parents):
            raise HarnessValidationError("source inspection repeated completed hierarchy browse")
        parent = parents[parent_offset]
        requested_parent = call.arguments.get("parent_id", index.rootNodeId)
        requested_cursor = call.arguments.get("cursor", 0)
        requested_limit = call.arguments.get("limit", 8)
        if (
            requested_parent != parent.id
            or type(requested_cursor) is not int
            or type(requested_limit) is not int
            or requested_cursor != cursor
            or not 1 <= requested_limit <= MAX_BROWSE_LIMIT
        ):
            raise HarnessValidationError(
                "source inspection did not follow the chronological browse cursor"
            )
        parent_child_ids = _child_ids(parent)
        end = min(len(parent_child_ids), cursor + requested_limit)
        if call.node_ids != tuple(parent_child_ids[cursor:end]):
            raise HarnessValidationError("source inspection browse results differ from the index")
        expected_next = end if end < len(parent_child_ids) else None
        if call.next_cursor != expected_next or call.complete != (expected_next is None):
            raise HarnessValidationError("source inspection browse pagination is inconsistent")
        if expected_next is None:
            parent_offset += 1
            cursor = 0
        else:
            cursor = end
    if parent_offset != len(parents) or not browse_calls or not browse_calls[-1].complete:
        raise HarnessValidationError(
            "source inspection did not browse every hierarchy node through the final page"
        )
    known_sentences = {sentence.id for sentence in index.sentences}
    known_regions = {node.id for node in index.nodes if _node_kind(node) == "region"}
    read_ids = {
        identifier
        for call in trace.calls
        if call.tool_name == "read_source"
        for identifier in call.sentence_ids
    }
    if not read_ids or not read_ids <= known_sentences:
        raise HarnessValidationError("source inspection did not retain any exact indexed speech")
    searches = [call for call in trace.calls if call.tool_name == "search_source"]
    if not searches:
        raise HarnessValidationError("source inspection did not use hybrid retrieval")
    for call in searches:
        query = call.arguments.get("query")
        requested_cursor = call.arguments.get("cursor", 0)
        requested_limit = call.arguments.get("limit", 6)
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query.strip()) > MAX_QUERY_CHARACTERS
            or type(requested_cursor) is not int
            or requested_cursor < 0
            or requested_cursor > len(known_regions)
            or type(requested_limit) is not int
            or not 1 <= requested_limit <= MAX_SEARCH_LIMIT
            or len(call.node_ids) != len(set(call.node_ids))
            or not set(call.node_ids) <= known_regions
        ):
            raise HarnessValidationError("source inspection search result is invalid")
        end = min(len(known_regions), requested_cursor + requested_limit)
        expected_next = end if end < len(known_regions) else None
        if (
            len(call.node_ids) != end - requested_cursor
            or call.next_cursor != expected_next
            or call.complete != (expected_next is None)
        ):
            raise HarnessValidationError("source inspection search pagination is inconsistent")
    for call in (item for item in trace.calls if item.tool_name == "read_source"):
        first = call.arguments.get("first_sentence_id")
        last = call.arguments.get("last_sentence_id")
        continuation = call.arguments.get("cursor_sentence_id")
        requested_limit = call.arguments.get("limit", 40)
        if (
            not isinstance(first, str)
            or not isinstance(last, str)
            or (continuation is not None and not isinstance(continuation, str))
            or type(requested_limit) is not int
        ):
            raise HarnessValidationError("source inspection read arguments are invalid")
        try:
            expected = read_topic_source(
                index,
                index_sha256=trace.index_sha256,
                first_sentence_id=first,
                last_sentence_id=last,
                cursor_sentence_id=continuation,
                limit=requested_limit,
            )
        except ValueError as error:
            raise HarnessValidationError("source inspection read range is invalid") from error
        if (
            call.sentence_ids != tuple(sentence.id for sentence in expected.sentences)
            or call.next_sentence_id != expected.nextSentenceId
            or call.complete != expected.complete
        ):
            raise HarnessValidationError("source inspection read result differs from the index")


def validate_source_read_ids(
    index: TopicSourceIndex,
    trace: SourceInspectionTrace,
    required_sentence_ids: set[str],
) -> None:
    """Require exact reads for every source sentence used by a final editorial answer."""
    known = {sentence.id for sentence in index.sentences}
    if not required_sentence_ids <= known:
        raise HarnessValidationError("editorial answer cites a sentence outside the source index")
    read = {
        identifier
        for call in trace.calls
        if call.tool_name == "read_source"
        for identifier in call.sentence_ids
    }
    missing = required_sentence_ids - read
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        raise HarnessValidationError(
            f"source inspection did not read cited boundary sentences: {sample}"
        )
