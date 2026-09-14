"""Bounded pages of immutable editorial records, including oversized individual records."""

# The errors are corrective messages at the tool boundary.
# ruff: noqa: EM101, TRY003, N815

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from temnia_pipeline.contracts import TopicSelectionDraft, TopicSourceReviewWorkItem
    from temnia_pipeline.harness.topic_selection_runtime import SourceInspectionTrace


PAGE_CHARACTERS = 12_000
MAX_PAGE_RECORDS = 8


class EditorialContextRecord(BaseModel):
    """One source-bound collection, stored outside the model prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["topic-editorial-context/1"] = "topic-editorial-context/1"
    index_sha256: str
    records: tuple[dict[str, Any], ...]


class EditorialContextPage(BaseModel):
    """Exact JSON fragments with stable offsets; prose is never silently truncated."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    indexSha256: str
    contextSha256: str
    events: list[dict[str, Any]]
    nextCursor: str | None
    complete: bool


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_editorial_page(
    context: EditorialContextRecord,
    *,
    context_sha256: str,
    cursor: str = "0:0",
    limit: int = 4,
) -> EditorialContextPage:
    """Page exact records; a large record continues by character offset on the next call."""
    try:
        parts = cursor.split(":")
        if len(parts) != 2:  # noqa: PLR2004
            raise ValueError("invalid cursor")  # noqa: TRY301
        ordinal, offset = (int(value) for value in parts)
    except (ValueError, TypeError) as error:
        raise ValueError("Use the returned editorial context cursor, initially 0:0.") from error
    if not 1 <= limit <= MAX_PAGE_RECORDS or not 0 <= ordinal <= len(context.records):
        raise ValueError("Editorial page limit or cursor is outside the collection.")
    if ordinal == len(context.records) and offset != 0:
        raise ValueError("Completed context cursor must have offset zero.")
    events: list[dict[str, Any]] = []
    remaining = PAGE_CHARACTERS
    while ordinal < len(context.records) and len(events) < limit and remaining:
        text = _json(context.records[ordinal])
        if not 0 <= offset < len(text):
            raise ValueError("Editorial context offset is outside its record.")
        end = min(len(text), offset + remaining)
        events.append(
            {
                "id": f"{ordinal}:{offset}:{end}",
                "recordOrdinal": ordinal,
                "recordCharacters": len(text),
                "startCharacter": offset,
                "endCharacter": end,
                "jsonFragment": text[offset:end],
            }
        )
        remaining -= end - offset
        if end == len(text):
            ordinal += 1
            offset = 0
        else:
            offset = end
    return EditorialContextPage(
        indexSha256=context.index_sha256,
        contextSha256=context_sha256,
        events=events,
        nextCursor=f"{ordinal}:{offset}" if ordinal < len(context.records) else None,
        complete=ordinal == len(context.records),
    )


def validate_editorial_reads(  # noqa: C901
    context: EditorialContextRecord,
    context_sha256: str,
    trace: SourceInspectionTrace,
) -> None:
    """Replay every page and require complete delivered record coverage before judgment."""
    intervals: dict[int, list[tuple[int, int]]] = {}
    for call in trace.calls:
        if call.tool_name != "read_editorial_context":
            continue
        cursor, limit = call.arguments.get("cursor", "0:0"), call.arguments.get("limit", 4)
        if not isinstance(cursor, str) or type(limit) is not int:
            raise HarnessValidationError("Editorial context read has invalid arguments.")
        page = read_editorial_page(
            context, context_sha256=context_sha256, cursor=cursor, limit=limit
        )
        if (
            call.evidence_ids != tuple(str(event["id"]) for event in page.events)
            or call.complete != page.complete
            or call.next_context_cursor != page.nextCursor
        ):
            raise HarnessValidationError("Editorial context read differs from its immutable page.")
        for event in page.events:
            if event["id"] not in trace.delivered_context_ids:
                continue
            intervals.setdefault(int(event["recordOrdinal"]), []).append(
                (int(event["startCharacter"]), int(event["endCharacter"]))
            )
    missing: list[int] = []
    for ordinal, record in enumerate(context.records):
        end = 0
        for start, stop in sorted(intervals.get(ordinal, [])):
            if start > end:
                break
            end = max(end, stop)
        if end != len(_json(record)):
            missing.append(ordinal)
    if missing:
        raise HarnessValidationError(
            "Read the remaining editorial context records before judging: "
            + ", ".join(str(value) for value in missing[:12])
        )


def source_review_context(
    draft: TopicSelectionDraft,
    work_item: TopicSourceReviewWorkItem,
    index_sha256: str,
) -> EditorialContextRecord:
    """Hide author rationale while preserving every assigned structured relationship."""
    candidates = {str(getattr(value, "root", value)) for value in work_item.inspectionCandidateIds}
    opportunities = {
        str(getattr(value, "root", value)) for value in work_item.contextOpportunityIds
    }
    return EditorialContextRecord(
        index_sha256=index_sha256,
        records=(
            *(
                {"kind": "candidate", "value": item.model_dump(mode="json", exclude={"reason"})}
                for item in draft.proposal.candidates
                if item.id in candidates
            ),
            *(
                {
                    "kind": "opportunity",
                    "value": item.model_dump(mode="json", exclude={"dispositionReason"}),
                }
                for item in draft.opportunities
                if item.id in opportunities
            ),
        ),
    )
