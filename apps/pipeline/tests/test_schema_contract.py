"""The pipeline's DML surface against the migrated catalogue.

Drizzle owns the schema; this test is what fails the gate when a column the
pipeline writes is renamed or retyped on the TypeScript side. It runs against
`TEST_DATABASE_URL` (the gate's disposable database) and never skips silently.
"""

from __future__ import annotations

import os

import psycopg
import pytest

EXPECTED: dict[str, dict[str, str]] = {
    "source": {
        "id": "uuid",
        "organization_id": "uuid",
        "status": "USER-DEFINED",
        "duration_ms": "integer",
        "width": "integer",
        "height": "integer",
        "fps": "numeric",
        "audio_channels": "integer",
        "video_codec": "text",
        "audio_codec": "text",
        "ingest_workflow_id": "text",
        "ingest_stage": "text",
        "ingest_percent": "integer",
        "ingest_heartbeat_at": "timestamp with time zone",
        "error_message": "text",
        "ready_at": "timestamp with time zone",
        "updated_at": "timestamp with time zone",
    },
    "artifact": {
        "organization_id": "uuid",
        "source_id": "uuid",
        "kind": "USER-DEFINED",
        "storage_key": "text",
        "storage_prefix": "text",
        "content_type": "text",
        "size_bytes": "bigint",
        "metadata": "jsonb",
    },
    "usage_ledger": {
        "organization_id": "uuid",
        "kind": "USER-DEFINED",
        "quantity": "bigint",
        "source_id": "uuid",
        "workflow_id": "text",
        "detail": "jsonb",
    },
    "upload": {
        "id": "uuid",
        "source_id": "uuid",
        "storage_key": "text",
        "multipart_upload_id": "text",
        "status": "USER-DEFINED",
        "last_activity_at": "timestamp with time zone",
    },
    "organization": {"id": "uuid"},
}

EXPECTED_ENUMS = {
    "source_status": {"uploading", "uploaded", "processing", "ready", "failed"},
    "artifact_kind": {"master", "hls", "peaks", "thumbnails", "audio", "shots"},
    "upload_status": {"active", "completed", "aborted"},
    "usage_kind": {"storage_bytes", "processing_seconds"},
}


@pytest.fixture(scope="module")
def conn() -> psycopg.Connection[tuple[object, ...]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: the schema contract never skips silently")
    return psycopg.connect(url)


def test_columns_the_pipeline_writes_exist_with_their_types(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    rows = conn.execute(
        """
        SELECT table_name, column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = 'public'
        """
    ).fetchall()
    actual = {(str(t), str(c)): str(d) for t, c, d in rows}
    missing = [
        f"{table}.{column} ({data_type})"
        for table, columns in EXPECTED.items()
        for column, data_type in columns.items()
        if actual.get((table, column)) != data_type
    ]
    assert missing == [], f"schema drift against the pipeline's DML: {missing}"


def test_enum_values_match(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    rows = conn.execute(
        """
        SELECT t.typname, e.enumlabel
          FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        """
    ).fetchall()
    actual: dict[str, set[str]] = {}
    for name, label in rows:
        actual.setdefault(str(name), set()).add(str(label))
    for name, labels in EXPECTED_ENUMS.items():
        assert actual.get(name) == labels, name


def test_pipeline_role_can_enumerate_organizations_and_nothing_else_unscoped() -> None:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    with psycopg.connect(url) as pipeline:
        organizations = pipeline.execute("SELECT count(*) FROM organization").fetchone()
        assert organizations is not None
        assert int(str(organizations[0])) >= 2
        sources = pipeline.execute("SELECT count(*) FROM source").fetchone()
        assert sources is not None
        assert int(str(sources[0])) == 0
