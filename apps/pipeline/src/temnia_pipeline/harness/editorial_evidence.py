"""Bounded candidate navigation and measured-media evidence projections."""

# Refusal messages are part of the tool contract and remain beside its invariants.
# ruff: noqa: C901, EM101, EM102, PLR0913, TRY003

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from temnia_pipeline.contracts import (
    HarnessEvidence,
    SpeechCoverageStatus,
    TopicCandidate,
    TopicCandidateInspectionPage,
    TopicCandidateRegionHit,
    TopicMediaEvidenceEvent,
    TopicMediaEvidencePage,
    TopicSelectionRecord,
    TopicSentenceSpan,
    TopicSourceIndex,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.harness.topic_selection_runtime import SourceInspectionTrace

MAX_CANDIDATE_REGION_LIMIT = 16
MAX_MEDIA_EVENT_LIMIT = 80
MAX_EVENT_ID_LENGTH = 256


def _event_id(kind: str, identifier: str) -> str:
    """Keep event identities unique across sensor kinds and inside contract bounds."""
    scoped = f"{kind}:{identifier}"
    if len(scoped) <= MAX_EVENT_ID_LENGTH:
        return scoped
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return f"{kind}:sha256:{digest}"


def _positions(index: TopicSourceIndex) -> dict[str, int]:
    return {sentence.id: offset for offset, sentence in enumerate(index.sentences)}


def _span_positions(
    positions: dict[str, int], first_sentence_id: str, last_sentence_id: str
) -> tuple[int, int]:
    try:
        first = positions[first_sentence_id]
        last = positions[last_sentence_id]
    except KeyError as error:
        raise ValueError("requested evidence span names an unknown sentence") from error
    if first > last:
        raise ValueError("requested evidence span is reversed")
    return first, last


def _relation(
    *, candidate_first: int, candidate_last: int, region_first: int, region_last: int
) -> str:
    if candidate_first == region_first and candidate_last == region_last:
        return "same_extent"
    if candidate_first <= region_first and region_last <= candidate_last:
        return "inside_candidate"
    if region_first <= candidate_first and candidate_last <= region_last:
        return "covers_candidate"
    if region_first < candidate_first <= region_last < candidate_last:
        return "opening_overlap"
    if candidate_first < region_first <= candidate_last < region_last:
        return "closing_overlap"
    raise ValueError("candidate region does not intersect the candidate")


def _candidate(selection: TopicSelectionRecord, candidate_id: str) -> TopicCandidate:
    candidate = next(
        (item for item in selection.draft.proposal.candidates if item.id == candidate_id), None
    )
    if candidate is None:
        raise ValueError("candidate is absent from the accepted selection")
    return candidate


def inspect_topic_candidate(
    index: TopicSourceIndex,
    selection: TopicSelectionRecord,
    *,
    index_sha256: str,
    selection_sha256: str,
    candidate_id: str,
    cursor: int = 0,
    limit: int = 8,
) -> TopicCandidateInspectionPage:
    """Page through every index leaf intersecting one accepted candidate."""
    if type(cursor) is not int or cursor < 0:
        raise ValueError("candidate inspection cursor must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATE_REGION_LIMIT:
        raise ValueError(
            f"candidate inspection limit must be from 1 through {MAX_CANDIDATE_REGION_LIMIT}"
        )
    candidate = _candidate(selection, candidate_id)
    positions = _positions(index)
    candidate_first, candidate_last = _span_positions(
        positions, candidate.firstSentenceId, candidate.lastSentenceId
    )
    hits: list[TopicCandidateRegionHit] = []
    for node in index.nodes:
        if node.kind.value != "region":
            continue
        region_first, region_last = _span_positions(
            positions, node.firstSentenceId, node.lastSentenceId
        )
        if region_last < candidate_first or region_first > candidate_last:
            continue
        selected_first = max(candidate_first, region_first)
        selected_last = min(candidate_last, region_last)
        if node.parentId is None:
            raise ValueError("candidate region has no section parent")
        hits.append(
            TopicCandidateRegionHit.model_validate(
                {
                    "id": node.id,
                    "parentId": node.parentId,
                    "firstSentenceId": node.firstSentenceId,
                    "lastSentenceId": node.lastSentenceId,
                    "selectedFirstSentenceId": index.sentences[selected_first].id,
                    "selectedLastSentenceId": index.sentences[selected_last].id,
                    "startMs": node.startMs,
                    "endMs": node.endMs,
                    "sentenceCount": node.sentenceCount,
                    "keywords": node.keywords,
                    "preview": node.preview,
                    "relation": _relation(
                        candidate_first=candidate_first,
                        candidate_last=candidate_last,
                        region_first=region_first,
                        region_last=region_last,
                    ),
                }
            )
        )
    if cursor > len(hits):
        raise ValueError("candidate inspection cursor is outside the result set")
    end = min(len(hits), cursor + limit)
    next_cursor = end if end < len(hits) else None
    return TopicCandidateInspectionPage(
        candidateId=candidate.id,
        complete=next_cursor is None,
        indexSha256=index_sha256,
        nextCursor=next_cursor,
        regions=hits[cursor:end],
        selectionSha256=selection_sha256,
    )


def _base_event(
    *,
    identifier: str,
    kind: str,
    time_ms: int,
    end_ms: int | None = None,
    sentence_id: str | None = None,
) -> dict[str, Any]:
    return {
        "alignedWordCount": None,
        "boundaryKind": None,
        "clearanceMs": None,
        "endMs": end_ms,
        "id": identifier,
        "interpolatedWordCount": None,
        "kind": kind,
        "minimumConfidence": None,
        "reasons": [],
        "relatedIds": [],
        "requiresReview": None,
        "score": None,
        "sentenceId": sentence_id,
        "timeMs": time_ms,
        "wordCount": None,
    }


def _media_events(
    evidence: HarnessEvidence, *, first: int, last: int
) -> list[TopicMediaEvidenceEvent]:
    first_sentence = evidence.sentences[first]
    last_sentence = evidence.sentences[last]
    start_ms = first_sentence.startMs
    end_ms = last_sentence.endMs
    word_by_id = {word.id: word for word in evidence.words}
    rows: list[dict[str, Any]] = []
    for sentence in evidence.sentences[first : last + 1]:
        words = [word_by_id[item.root] for item in sentence.wordIds]
        confidences = [word.confidence for word in words if word.confidence is not None]
        row = _base_event(
            identifier=_event_id("sentence", sentence.id),
            kind="sentence",
            time_ms=sentence.startMs,
            end_ms=sentence.endMs,
            sentence_id=sentence.id,
        )
        row.update(
            {
                "alignedWordCount": sum(word.timing.value == "aligned" for word in words),
                "interpolatedWordCount": sum(word.timing.value == "interpolated" for word in words),
                "minimumConfidence": min(confidences) if confidences else None,
                "relatedIds": [word.id for word in words],
                "wordCount": len(words),
            }
        )
        rows.append(row)
    for boundary in evidence.boundaries:
        if not start_ms <= boundary.timeMs <= end_ms:
            continue
        row = _base_event(
            identifier=_event_id("boundary", boundary.id),
            kind="boundary",
            time_ms=boundary.timeMs,
            sentence_id=boundary.sentenceId,
        )
        row.update(
            {
                "boundaryKind": boundary.kind.value,
                "clearanceMs": boundary.clearanceMs,
                "reasons": boundary.reasons,
                "requiresReview": boundary.requiresReview,
                "score": boundary.score,
            }
        )
        rows.append(row)
    for pause in evidence.pauses:
        if pause.endMs < start_ms or pause.startMs > end_ms:
            continue
        row = _base_event(
            identifier=_event_id("pause", pause.id),
            kind="pause",
            time_ms=pause.startMs,
            end_ms=pause.endMs,
        )
        row["relatedIds"] = [
            identifier
            for identifier in (pause.leftWordId, pause.rightWordId)
            if identifier is not None
        ]
        rows.append(row)
    for offset, shot in enumerate(evidence.shots):
        if start_ms <= shot.timeMs <= end_ms:
            row = _base_event(
                identifier=_event_id("shot", f"{offset:08d}"),
                kind="shot",
                time_ms=shot.timeMs,
            )
            row["score"] = shot.score
            rows.append(row)
    for offset, interval in enumerate(evidence.speechCoverage.intervals):
        if interval.endMs < start_ms or interval.startMs > end_ms:
            continue
        rows.append(
            _base_event(
                identifier=_event_id("speech", f"{offset:08d}"),
                kind="speech_interval",
                time_ms=interval.startMs,
                end_ms=interval.endMs,
            )
        )
    kind_order = {
        "speech_interval": 0,
        "sentence": 1,
        "boundary": 2,
        "pause": 3,
        "shot": 4,
    }
    rows.sort(key=lambda item: (item["timeMs"], kind_order[str(item["kind"])], item["id"]))
    return [TopicMediaEvidenceEvent.model_validate(item) for item in rows]


def read_topic_media_evidence(
    evidence: HarnessEvidence,
    *,
    evidence_sha256: str,
    first_sentence_id: str,
    last_sentence_id: str,
    cursor: int = 0,
    limit: int = 40,
) -> TopicMediaEvidencePage:
    """Return bounded chronological sensor records for one exact transcript span."""
    if type(cursor) is not int or cursor < 0:
        raise ValueError("media evidence cursor must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_MEDIA_EVENT_LIMIT:
        raise ValueError(f"media evidence limit must be from 1 through {MAX_MEDIA_EVENT_LIMIT}")
    positions = {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}
    first, last = _span_positions(positions, first_sentence_id, last_sentence_id)
    events = _media_events(evidence, first=first, last=last)
    if cursor > len(events):
        raise ValueError("media evidence cursor is outside the result set")
    end = min(len(events), cursor + limit)
    next_cursor = end if end < len(events) else None
    return TopicMediaEvidencePage(
        complete=next_cursor is None,
        detector=evidence.speechCoverage.detector,
        detectorRevision=evidence.speechCoverage.detectorRevision,
        evidenceSha256=evidence_sha256,
        events=events[cursor:end],
        nextCursor=next_cursor,
        sourceId=evidence.sourceId,
        span=TopicSentenceSpan(
            firstSentenceId=first_sentence_id,
            lastSentenceId=last_sentence_id,
        ),
        speechCoverageStatus=SpeechCoverageStatus(evidence.speechCoverage.status.value),
        warnings=evidence.speechCoverage.warnings,
    )


def candidate_region_ids(index: TopicSourceIndex, candidate: TopicCandidate) -> tuple[str, ...]:
    """Exact chronological region identity used by inspection admission."""
    positions = _positions(index)
    first, last = _span_positions(positions, candidate.firstSentenceId, candidate.lastSentenceId)
    return tuple(
        node.id
        for node in index.nodes
        if node.kind.value == "region"
        and positions[node.lastSentenceId] >= first
        and positions[node.firstSentenceId] <= last
    )


def candidate_ids(selection: TopicSelectionRecord) -> tuple[str, ...]:
    """Preserve accepted candidate order for mandatory source-review inspection."""
    return tuple(item.id for item in selection.draft.proposal.candidates)


def event_ids(events: Sequence[TopicMediaEvidenceEvent]) -> tuple[str, ...]:
    """Keep event identity extraction at the generated-contract boundary."""
    return tuple(item.id for item in events)


def validate_reviewer_inspection(
    index: TopicSourceIndex,
    selection: TopicSelectionRecord,
    evidence: HarnessEvidence,
    trace: SourceInspectionTrace,
    *,
    index_sha256: str,
    selection_sha256: str,
    evidence_sha256: str,
    expected_candidate_ids: Sequence[str] | None = None,
) -> None:
    """Replay reviewer-only results and require the whole or explicitly scoped candidate set."""
    calls = [call for call in trace.calls if call.tool_name == "inspect_candidate"]
    offset = 0
    expected = (
        tuple(expected_candidate_ids)
        if expected_candidate_ids is not None
        else candidate_ids(selection)
    )
    if len(expected) != len(set(expected)) or not set(expected) <= set(candidate_ids(selection)):
        raise ValueError("source reviewer candidate inspection scope is invalid")
    for identifier in expected:
        cursor = 0
        while True:
            if offset >= len(calls):
                raise ValueError(f"source reviewer did not inspect candidate {identifier}")
            call = calls[offset]
            candidate_id = call.arguments.get("candidate_id")
            requested_cursor = call.arguments.get("cursor", 0)
            requested_limit = call.arguments.get("limit", 8)
            if (
                candidate_id != identifier
                or type(requested_cursor) is not int
                or requested_cursor != cursor
                or type(requested_limit) is not int
            ):
                raise ValueError("source reviewer did not follow the candidate inspection cursor")
            expected = inspect_topic_candidate(
                index,
                selection,
                index_sha256=index_sha256,
                selection_sha256=selection_sha256,
                candidate_id=identifier,
                cursor=requested_cursor,
                limit=requested_limit,
            )
            if (
                call.node_ids != tuple(item.id for item in expected.regions)
                or call.sentence_ids
                or call.evidence_ids
                or call.next_cursor != expected.nextCursor
                or call.complete != expected.complete
            ):
                raise ValueError("candidate inspection result differs from accepted inputs")
            offset += 1
            if expected.complete:
                break
            if expected.nextCursor is None:  # pragma: no cover - contract invariant
                raise ValueError("candidate inspection lost its continuation cursor")
            cursor = expected.nextCursor
    if offset != len(calls):
        raise ValueError("source reviewer repeated a completed candidate inspection")

    for call in (item for item in trace.calls if item.tool_name == "read_media_evidence"):
        first = call.arguments.get("first_sentence_id")
        last = call.arguments.get("last_sentence_id")
        requested_cursor = call.arguments.get("cursor", 0)
        requested_limit = call.arguments.get("limit", 40)
        if (
            not isinstance(first, str)
            or not isinstance(last, str)
            or type(requested_cursor) is not int
            or type(requested_limit) is not int
        ):
            raise ValueError("media evidence arguments are invalid")
        expected = read_topic_media_evidence(
            evidence,
            evidence_sha256=evidence_sha256,
            first_sentence_id=first,
            last_sentence_id=last,
            cursor=requested_cursor,
            limit=requested_limit,
        )
        if (
            call.node_ids
            or call.sentence_ids
            or call.evidence_ids != event_ids(expected.events)
            or call.next_cursor != expected.nextCursor
            or call.complete != expected.complete
        ):
            raise ValueError("media evidence result differs from accepted evidence")
