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
    TopicSourceIndexRegion,
    TopicSourceIndexSentence,
    TopicSourceReadPage,
    TopicSourceRegionHit,
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
    """Build one exact source index with deterministic regions and pinned vectors."""
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
        TopicSourceIndexRegion(
            id=f"r{ordinal + 1}",
            ordinal=ordinal,
            firstSentenceId=group[0].id,
            lastSentenceId=group[-1].id,
            startMs=group[0].startMs,
            endMs=group[-1].endMs,
            sentenceCount=len(group),
            keywords=_keywords(group),
            preview=_preview(group),
            embedding=region_vectors[ordinal],
        )
        for ordinal, group in enumerate(groups)
    ]
    index = TopicSourceIndex(
        format="topic-source-index/1",
        evidenceSha256=evidence_sha256,
        sourceId=evidence.sourceId,
        transcriptId=evidence.transcriptId,
        transcriptRevision=evidence.transcriptRevision,
        embeddingModel=embedding_model,
        embeddingRevision=embedding_revision,
        embeddingDimensions=dimensions,
        regionMaxSentences=REGION_MAX_SENTENCES,
        regionMaxCharacters=REGION_MAX_CHARACTERS,
        sentences=sentences,
        regions=regions,
    )
    validate_topic_source_index(evidence, index, evidence_sha256=evidence_sha256)
    return index


def validate_topic_source_index(
    evidence: HarnessEvidence, index: TopicSourceIndex, *, evidence_sha256: str
) -> None:
    """Prove exact sentence coverage, ordered regions, and usable vector dimensions."""
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
    position = {sentence.id: offset for offset, sentence in enumerate(index.sentences)}
    cursor = 0
    for ordinal, region in enumerate(index.regions):
        try:
            first = position[region.firstSentenceId]
            last = position[region.lastSentenceId]
        except KeyError as error:
            raise HarnessValidationError("source index region names an unknown sentence") from error
        if (
            region.ordinal != ordinal
            or first != cursor
            or last < first
            or region.sentenceCount != last - first + 1
            or region.startMs != index.sentences[first].startMs
            or region.endMs != index.sentences[last].endMs
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


def source_index_map(index: TopicSourceIndex, *, index_sha256: str) -> dict[str, object]:
    """Compact index identity; ordered discovery stays in bounded browse calls."""
    return {
        "indexSha256": index_sha256,
        "regionCount": len(index.regions),
        "sentenceCount": len(index.sentences),
        "durationMs": index.sentences[-1].endMs,
        "browsePageLimit": MAX_BROWSE_LIMIT,
        "searchPageLimit": MAX_SEARCH_LIMIT,
        "readSentenceLimit": MAX_READ_SENTENCES,
        "readCharacterLimit": MAX_READ_CHARACTERS,
    }


def _region_hit(region: TopicSourceIndexRegion, *, score: float | None) -> TopicSourceRegionHit:
    return TopicSourceRegionHit(
        id=region.id,
        firstSentenceId=region.firstSentenceId,
        lastSentenceId=region.lastSentenceId,
        startMs=region.startMs,
        endMs=region.endMs,
        sentenceCount=region.sentenceCount,
        keywords=region.keywords,
        preview=region.preview,
        score=score,
    )


def browse_topic_source(
    index: TopicSourceIndex, *, index_sha256: str, cursor: int = 0, limit: int = 8
) -> TopicSourceBrowsePage:
    """Page through every region in source order with an explicit completion cursor."""
    if cursor < 0 or not 1 <= limit <= MAX_BROWSE_LIMIT:
        raise ValueError(f"browse cursor must be nonnegative and limit 1..{MAX_BROWSE_LIMIT}")
    end = min(len(index.regions), cursor + limit)
    return TopicSourceBrowsePage(
        indexSha256=index_sha256,
        regions=[_region_hit(region, score=None) for region in index.regions[cursor:end]],
        nextCursor=end if end < len(index.regions) else None,
        complete=end == len(index.regions),
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
    query_vector = encode_sentences(encoder, [query])[0]
    region_matrix = np.asarray([region.embedding for region in index.regions], dtype=np.float64)
    semantic = region_matrix @ query_vector
    lexical_raw = _bm25_scores(index, set(_tokens(query)))
    lexical = lexical_raw / lexical_raw.max() if lexical_raw.max() > 0 else lexical_raw
    scores = 0.65 * ((semantic + 1.0) / 2.0) + 0.35 * lexical
    ranked = sorted(range(len(index.regions)), key=lambda item: (-scores[item], item))
    end = min(len(ranked), cursor + limit)
    return TopicSourceSearchPage(
        indexSha256=index_sha256,
        query=query,
        regions=[
            _region_hit(index.regions[item], score=float(scores[item]))
            for item in ranked[cursor:end]
        ],
        nextCursor=end if end < len(ranked) else None,
        complete=end == len(ranked),
    )


def _region_text(index: TopicSourceIndex, region: TopicSourceIndexRegion) -> str:
    positions = {sentence.id: offset for offset, sentence in enumerate(index.sentences)}
    return " ".join(
        item.text
        for item in index.sentences[
            positions[region.firstSentenceId] : positions[region.lastSentenceId] + 1
        ]
    )


def _bm25_scores(index: TopicSourceIndex, query_terms: set[str]) -> np.ndarray:
    """Okapi BM25 over the immutable region corpus."""
    documents = [Counter(_tokens(_region_text(index, region))) for region in index.regions]
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
                regions = cast("list[object]", payload.get("regions", []))
                sentences = cast("list[object]", payload.get("sentences", []))
                calls.append(
                    SourceInspectionCall.model_validate(
                        {
                            "tool_name": request.tool_name,
                            "arguments": request.args_as_dict(raise_if_invalid=True),
                            "region_ids": [
                                str(cast("dict[str, Any]", item)["id"])
                                for item in regions
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


def validate_source_inspection(index: TopicSourceIndex, trace: SourceInspectionTrace) -> None:
    """Require complete map traversal and at least one exact grounded read."""
    browse_calls = [call for call in trace.calls if call.tool_name == "browse_source"]
    cursor = 0
    for call in browse_calls:
        requested_cursor = call.arguments.get("cursor", 0)
        requested_limit = call.arguments.get("limit", 8)
        if (
            type(requested_cursor) is not int
            or type(requested_limit) is not int
            or requested_cursor != cursor
            or not 1 <= requested_limit <= MAX_BROWSE_LIMIT
        ):
            raise HarnessValidationError(
                "source inspection did not follow the chronological browse cursor"
            )
        end = min(len(index.regions), cursor + requested_limit)
        if call.region_ids != tuple(region.id for region in index.regions[cursor:end]):
            raise HarnessValidationError("source inspection browse results differ from the index")
        expected_next = end if end < len(index.regions) else None
        if call.next_cursor != expected_next or call.complete != (expected_next is None):
            raise HarnessValidationError("source inspection browse pagination is inconsistent")
        cursor = end
    if cursor != len(index.regions) or not browse_calls or not browse_calls[-1].complete:
        raise HarnessValidationError(
            "source inspection did not browse every indexed region through the final page"
        )
    known_sentences = {sentence.id for sentence in index.sentences}
    read_ids = {
        identifier
        for call in trace.calls
        if call.tool_name == "read_source"
        for identifier in call.sentence_ids
    }
    if not read_ids or not read_ids <= known_sentences:
        raise HarnessValidationError("source inspection did not retain any exact indexed speech")
    if not any(call.tool_name == "search_source" for call in trace.calls):
        raise HarnessValidationError("source inspection did not use hybrid retrieval")


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
