"""finalize_source against the migrated database, as the pipeline role, under RLS.

The ledger rule: the artifact storage entry is a delta against earlier
artifact entries only, never against the master's entry. The 30-minute scale
run on 2026-09-06 under-reported by the master's size before this test existed.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from temnia_pipeline import db
from temnia_pipeline.contracts import ArtifactKind, ArtifactRecord, Scope

if TYPE_CHECKING:
    from psycopg import AsyncConnection

SEEDED = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: this test never skips silently")
    return url


async def ledger_sum(conn: AsyncConnection[dict[str, Any]], source_id: uuid.UUID) -> int:
    row = await (
        await conn.execute(
            "SELECT COALESCE(SUM(quantity), 0)::bigint AS total FROM usage_ledger"
            " WHERE source_id = %s AND kind = 'storage_bytes'",
            (source_id,),
        )
    ).fetchone()
    return int(row["total"]) if row else 0


async def processing_rows(conn: AsyncConnection[dict[str, Any]], source_id: uuid.UUID) -> int:
    row = await (
        await conn.execute(
            "SELECT count(*) AS n FROM usage_ledger"
            " WHERE source_id = %s AND kind = 'processing_seconds'",
            (source_id,),
        )
    ).fetchone()
    return int(row["n"]) if row else 0


def artifact(kind: ArtifactKind, prefix: str, size: int) -> ArtifactRecord:
    return ArtifactRecord(
        kind=kind,
        storageKey=f"{prefix}{kind.value}",
        storagePrefix=None,
        contentType="application/octet-stream",
        sizeBytes=size,
        metadata={},
    )


async def test_artifact_delta_never_nets_against_the_master() -> None:
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SEEDED.organizationId, "ledger test"),
            )
        ).fetchone()
        assert project is not None
        source = await (
            await conn.execute(
                """
                INSERT INTO source (organization_id, project_id, title, original_filename,
                                    content_type, size_bytes, master_key, status)
                VALUES (%s, %s, 'ledger', 'ledger.mp4', 'video/mp4', 1000, 'org/x/master',
                        'uploaded')
                RETURNING id
                """,
                (SEEDED.organizationId, project["id"]),
            )
        ).fetchone()
        assert source is not None
        source_id = source["id"]
        prefix = f"org/{SEEDED.organizationId}/source/{source_id}/"
        # The master's entry, as the complete route writes it.
        await conn.execute(
            """
            INSERT INTO usage_ledger (organization_id, kind, quantity, source_id, detail)
            VALUES (%s, 'storage_bytes', 1000, %s, '{"category": "master"}'::jsonb)
            """,
            (SEEDED.organizationId, source_id),
        )
        assert await db.claim_source(conn, source_id, "wf-1")

        total = await db.finalize_source(
            conn,
            scope=SEEDED,
            source_id=source_id,
            workflow_id="wf-1",
            run_id="run-1",
            artifacts=[
                artifact(ArtifactKind.hls, prefix, 600),
                artifact(ArtifactKind.peaks, prefix, 50),
            ],
            duration_ms=120_000,
            processing_seconds=7,
        )
        assert total == 650
        assert await ledger_sum(conn, source_id) == 1000 + 650

        # The same run finalising again (a lost acknowledgement) meters nothing
        # more: neither storage (a delta) nor processing (keyed by the run).
        total = await db.finalize_source(
            conn,
            scope=SEEDED,
            source_id=source_id,
            workflow_id="wf-1",
            run_id="run-1",
            artifacts=[
                artifact(ArtifactKind.hls, prefix, 600),
                artifact(ArtifactKind.peaks, prefix, 50),
            ],
            duration_ms=120_000,
            processing_seconds=7,
        )
        assert total == 650
        assert await ledger_sum(conn, source_id) == 1000 + 650
        assert await processing_rows(conn, source_id) == 1

        # A re-ingest overwrites in place: only net-new artifact bytes are metered.
        assert await db.claim_source(conn, source_id, "wf-2")
        total = await db.finalize_source(
            conn,
            scope=SEEDED,
            source_id=source_id,
            workflow_id="wf-2",
            run_id="run-2",
            artifacts=[
                artifact(ArtifactKind.hls, prefix, 700),
                artifact(ArtifactKind.peaks, prefix, 50),
            ],
            duration_ms=120_000,
            processing_seconds=7,
        )
        assert total == 750
        assert await ledger_sum(conn, source_id) == 1000 + 750
        # A new run was paid for again, so it is metered again.
        assert await processing_rows(conn, source_id) == 2

        status = await (
            await conn.execute("SELECT status FROM source WHERE id = %s", (source_id,))
        ).fetchone()
        assert status is not None
        assert status["status"] == "ready"
        await conn.execute("DELETE FROM project WHERE id = %s", (project["id"],))
    await db.close_pool()
