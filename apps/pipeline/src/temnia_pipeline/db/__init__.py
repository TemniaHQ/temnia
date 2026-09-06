"""DML against the migrated product database, as the pipeline role, under RLS.

Drizzle owns every table (packages/db). This module writes the handful of
columns the ingest workflow owns and nothing else; `tests/test_schema_contract.py`
asserts those columns exist with the expected types on the migrated database,
so a schema change on the TypeScript side fails the gate here.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from psycopg import AsyncConnection

    from temnia_pipeline.contracts import ArtifactRecord, ProbeResult, Scope

_pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] | None = None


async def get_pool(database_url: str) -> AsyncConnectionPool[AsyncConnection[dict[str, Any]]]:
    """One pool per process; opened lazily on first use."""
    global _pool  # noqa: PLW0603
    if _pool is None:
        _pool = AsyncConnectionPool(
            database_url,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    """Close the pool at worker shutdown."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def scoped(
    database_url: str, scope: Scope
) -> AsyncGenerator[AsyncConnection[dict[str, Any]]]:
    """A transaction scoped to `scope`: the GUCs the RLS policies read.

    set_config(..., true) is SET LOCAL with bound parameters; it dies with the
    transaction, so a pooled connection never carries a scope to the next user.
    """
    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.organization_id', %s, true),"
            " set_config('app.user_id', %s, true)",
            (str(scope.organizationId), str(scope.userId)),
        )
        yield conn


async def claim_source(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, workflow_id: str
) -> bool:
    """Move an `uploaded` (or a retried `failed`, or a re-ingested `ready`) source to `processing`.

    Returns False when the row is still `uploading`, which makes a workflow
    started too early a no-op. Prior artifact rows are cleared: a re-run
    overwrites the same keys, and finalize meters only the net-new bytes.
    """
    row = await (
        await conn.execute(
            """
            UPDATE source
               SET status = 'processing', ingest_workflow_id = %s, ingest_stage = 'probe',
                   ingest_percent = NULL, ingest_heartbeat_at = now(), error_message = NULL,
                   updated_at = now()
             WHERE id = %s AND status IN ('uploaded', 'failed', 'processing', 'ready')
         RETURNING id
            """,
            (workflow_id, source_id),
        )
    ).fetchone()
    if row is None:
        return False
    await conn.execute("DELETE FROM artifact WHERE source_id = %s", (source_id,))
    return True


async def report_progress(
    conn: AsyncConnection[dict[str, Any]],
    source_id: UUID,
    stage: str,
    percent: int | None,
) -> None:
    """Best-effort progress: stage, percent, and the heartbeat the reaper reads."""
    await conn.execute(
        """
        UPDATE source
           SET ingest_stage = %s, ingest_percent = %s, ingest_heartbeat_at = now(),
               updated_at = now()
         WHERE id = %s AND status = 'processing'
        """,
        (stage, percent, source_id),
    )


async def record_probe(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, probe: ProbeResult
) -> None:
    """Write what probing learned onto the source row."""
    await conn.execute(
        """
        UPDATE source
           SET duration_ms = %s, width = %s, height = %s, fps = %s, audio_channels = %s,
               video_codec = %s, audio_codec = %s, updated_at = now()
         WHERE id = %s
        """,
        (
            probe.durationMs,
            probe.width,
            probe.height,
            probe.fps,
            probe.audioChannels,
            probe.videoCodec,
            probe.audioCodec,
            source_id,
        ),
    )


async def finalize_source(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    scope: Scope,
    source_id: UUID,
    workflow_id: str,
    artifacts: list[ArtifactRecord],
    duration_ms: int,
    processing_seconds: int,
) -> int:
    """Insert artifact rows, mark the source ready, and write the ledger entries.

    The storage figure is read back from the rows just written (SUM), so the
    per-artifact attribution and the ledger cannot drift. It is metered as a
    delta against earlier *artifact* entries for the source (a re-ingest
    overwrites in place); the master's own entry, written at upload
    completion, is a different category and is never netted against.
    Processing is metered in media seconds (the cost driver); wall-clock
    seconds ride along in detail. Returns the summed storage bytes.
    """
    for record in artifacts:
        await conn.execute(
            """
            INSERT INTO artifact (organization_id, source_id, kind, storage_key, storage_prefix,
                                  content_type, size_bytes, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_id, kind) DO UPDATE
               SET storage_key = EXCLUDED.storage_key, storage_prefix = EXCLUDED.storage_prefix,
                   content_type = EXCLUDED.content_type, size_bytes = EXCLUDED.size_bytes,
                   metadata = EXCLUDED.metadata
            """,
            (
                scope.organizationId,
                source_id,
                record.kind.value,
                record.storageKey,
                record.storagePrefix,
                record.contentType,
                record.sizeBytes,
                json.dumps(record.metadata),
            ),
        )
    total = await (
        await conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0)::bigint AS total FROM artifact"
            " WHERE source_id = %s",
            (source_id,),
        )
    ).fetchone()
    storage_bytes = int(total["total"]) if total else 0
    previous = await (
        await conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)::bigint AS total FROM usage_ledger
             WHERE source_id = %s AND kind = 'storage_bytes'
               AND detail->>'category' = 'artifacts'
            """,
            (source_id,),
        )
    ).fetchone()
    delta = storage_bytes - (int(previous["total"]) if previous else 0)
    await conn.execute(
        """
        UPDATE source
           SET status = 'ready', ready_at = %s, ingest_stage = NULL, ingest_percent = NULL,
               ingest_heartbeat_at = now(), error_message = NULL, updated_at = now()
         WHERE id = %s
        """,
        (datetime.now(tz=UTC), source_id),
    )
    if delta != 0:
        await conn.execute(
            """
            INSERT INTO usage_ledger
                (organization_id, kind, quantity, source_id, workflow_id, detail)
            VALUES (%s, 'storage_bytes', %s, %s, %s, %s::jsonb)
            """,
            (
                scope.organizationId,
                delta,
                source_id,
                workflow_id,
                json.dumps({"category": "artifacts", "total": storage_bytes}),
            ),
        )
    await conn.execute(
        """
        INSERT INTO usage_ledger (organization_id, kind, quantity, source_id, workflow_id, detail)
        VALUES (%s, 'processing_seconds', %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organizationId,
            round(duration_ms / 1000),
            source_id,
            workflow_id,
            json.dumps({"stage": "ingest", "wallSeconds": processing_seconds}),
        ),
    )
    return storage_bytes


async def fail_source(conn: AsyncConnection[dict[str, Any]], source_id: UUID, message: str) -> None:
    """Terminal failure: the row says why, in words safe to render."""
    await conn.execute(
        """
        UPDATE source
           SET status = 'failed', error_message = %s, ingest_stage = NULL, ingest_percent = NULL,
               updated_at = now()
         WHERE id = %s
        """,
        (message[:2000], source_id),
    )
