"""Timezone and wire-shape parity for generated datetime contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from temnia_pipeline.contracts import ChapterExport


def test_chapter_export_accepts_aware_datetime_and_serializes_rfc3339() -> None:
    exported = ChapterExport(
        chapters=[],
        createdAt=datetime(2026, 9, 8, 18, 30, tzinfo=UTC),
        editSha256="a" * 64,
        manifestKey="org/o/source/s/harness/export.json",
        revision=1,
        runId=UUID("0192e8a0-0000-7000-8000-000000000011"),
        version=1,
    )
    wire = json.loads(exported.model_dump_json(by_alias=True))
    assert wire["createdAt"] == "2026-09-08T18:30:00Z"


def test_chapter_export_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ChapterExport(
            chapters=[],
            createdAt=datetime(2026, 9, 8, 18, 30),  # noqa: DTZ001 - rejection fixture
            editSha256="a" * 64,
            manifestKey="org/o/source/s/harness/export.json",
            revision=1,
            runId=UUID("0192e8a0-0000-7000-8000-000000000011"),
            version=1,
        )
