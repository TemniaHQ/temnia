"""Large editorial context remains exact, bounded and subject to delivered-page admission."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from temnia_pipeline.harness.editorial_context import (
    PAGE_CHARACTERS,
    EditorialContextRecord,
    read_editorial_page,
    validate_editorial_reads,
)
from temnia_pipeline.harness.source_progress import (
    aggregate_checkpoint_inspections,
    checkpoint_from_messages,
    inspection_from_checkpoint,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_source_progress import INDEX_SHA, _compact, _tool_turn


def test_large_record_pages_are_exact_and_admission_survives_audit_rotation() -> None:
    records = tuple({"id": str(i), "prose": "विषय " * 6_000} for i in range(60))
    context = EditorialContextRecord(index_sha256=INDEX_SHA, records=records)
    messages = _compact([ModelRequest(parts=[UserPromptPart("Review all assigned records.")])])
    initial = checkpoint_from_messages(messages)
    assert initial is not None
    chain = [initial]
    cursor = "0:0"
    fragments: dict[int, str] = {}
    while True:
        page = read_editorial_page(context, context_sha256="d" * 64, cursor=cursor)
        assert sum(len(event["jsonFragment"]) for event in page.events) <= PAGE_CHARACTERS
        for event in page.events:
            ordinal = event["recordOrdinal"]
            fragments[ordinal] = fragments.get(ordinal, "") + event["jsonFragment"]
        messages = _tool_turn(
            messages,
            name="read_editorial_context",
            arguments={"cursor": cursor},
            result=page,
            identifier=f"context-{cursor}",
        )
        checkpoint = checkpoint_from_messages(messages)
        assert checkpoint is not None
        chain.append(checkpoint)
        if page.complete:
            break
        assert page.nextCursor is not None
        cursor = page.nextCursor
    assert [json.loads(fragments[i]) for i in range(len(records))] == list(records)
    assert chain[-1].audit_segment > 0
    assert len(chain[-1].calls) <= 96
    trace = aggregate_checkpoint_inspections(list(reversed(chain)))
    validate_editorial_reads(context, "d" * 64, trace)
    with pytest.raises(HarnessValidationError, match="remaining editorial context"):
        validate_editorial_reads(
            context, "d" * 64, trace.model_copy(update={"delivered_context_ids": ()})
        )


def test_unread_or_forged_page_cannot_authorize_judgment() -> None:
    context = EditorialContextRecord(index_sha256=INDEX_SHA, records=({"speech": "exact"},))
    messages = _compact([ModelRequest(parts=[UserPromptPart("Read context.")])])
    page = read_editorial_page(context, context_sha256="d" * 64)
    messages = _tool_turn(
        messages,
        name="read_editorial_context",
        arguments={},
        result=page,
        identifier="context",
    )
    checkpoint = checkpoint_from_messages(messages)
    assert checkpoint is not None
    trace = inspection_from_checkpoint(checkpoint)
    validate_editorial_reads(context, "d" * 64, trace)
    forged = trace.calls[0].model_copy(update={"next_context_cursor": "1:0"})
    with pytest.raises(HarnessValidationError, match="immutable page"):
        validate_editorial_reads(context, "d" * 64, trace.model_copy(update={"calls": (forged,)}))


@pytest.mark.parametrize("cursor", ["wrong", "1:3", "-1:0", "0:-1", "0:999999"])
def test_bad_context_cursor_is_correctable(cursor: str) -> None:
    context = EditorialContextRecord(index_sha256=INDEX_SHA, records=({"id": "a"},))
    with pytest.raises(ValueError, match=r"[Cc]ursor|offset"):
        read_editorial_page(context, context_sha256="d" * 64, cursor=cursor)
