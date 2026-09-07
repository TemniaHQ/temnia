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

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

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


async def assert_reachable(database_url: str) -> None:
    """One real connection at worker start, so a wrong host or password fails the boot.

    The pool connects lazily and surfaces a bad URL only as a PoolTimeout
    on the first activity thirty seconds later; the first staging deploy
    sat at "Queued" for that reason.
    """
    conn = await AsyncConnection.connect(database_url, connect_timeout=15)
    await conn.close()


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


async def claim_transcription(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, organization_id: UUID, workflow_id: str
) -> int:
    """Insert or claim the source's transcript row; returns the attempt, or 0.

    Claimable means missing, `pending`, or `failed`. A row already `processing`
    or `ready` returns 0, which is what makes a second workflow for the same
    source a no-op rather than a second GPU job; the unique index on
    `source_id` is what the upsert conflicts on.

    A retry from the web sets a `ready` or `failed` row back to `pending` first,
    so the two rules do not contradict each other: this activity refuses to
    interrupt work, and the user's Retry is the thing that says a finished
    transcript may be replaced.
    """
    row = await (
        await conn.execute(
            """
            INSERT INTO transcript (organization_id, source_id, status, attempts, workflow_id,
                                    stage, percent, heartbeat_at)
            VALUES (%(organization_id)s, %(source_id)s, 'processing', 1, %(workflow_id)s,
                    'download', NULL, now())
            ON CONFLICT (source_id) DO UPDATE
               SET status = 'processing', attempts = transcript.attempts + 1,
                   workflow_id = %(workflow_id)s, stage = 'download', percent = NULL,
                   error_message = NULL, heartbeat_at = now(), updated_at = now()
             WHERE transcript.status IN ('pending', 'failed')
         RETURNING attempts
            """,
            {
                "organization_id": organization_id,
                "source_id": source_id,
                "workflow_id": workflow_id,
            },
        )
    ).fetchone()
    return int(row["attempts"]) if row else 0


async def report_transcription_progress(
    conn: AsyncConnection[dict[str, Any]],
    source_id: UUID,
    stage: str,
    percent: int | None,
) -> None:
    """Best-effort progress, and the heartbeat the surface reads to say "stalled"."""
    await conn.execute(
        """
        UPDATE transcript
           SET stage = %s, percent = %s, heartbeat_at = now(), updated_at = now()
         WHERE source_id = %s AND status = 'processing'
        """,
        (stage, percent, source_id),
    )


async def next_transcript_revision(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, attempt: int
) -> tuple[UUID, int, bool]:
    """The revision this attempt writes: its transcript, its number, and whether it exists.

    Idempotent by attempt, not by call. An activity that uploaded the object
    and then died before committing runs again, finds no row, computes the same
    number, and overwrites the same key; one that committed finds its own row
    and reuses it rather than writing a second revision for one engine run.

    The number is the next after every revision the transcript has, not the
    attempt number, because a user's corrections write revisions too and a
    machine re-run must never land on top of one.
    """
    transcript = await (
        await conn.execute("SELECT id FROM transcript WHERE source_id = %s", (source_id,))
    ).fetchone()
    if transcript is None:
        msg = f"no transcript row for source {source_id}"
        raise LookupError(msg)
    transcript_id: UUID = transcript["id"]
    existing = await (
        await conn.execute(
            """
            SELECT revision FROM transcript_revision
             WHERE transcript_id = %s AND kind = 'machine'
               AND (metadata->>'attempt')::int = %s
            """,
            (transcript_id, attempt),
        )
    ).fetchone()
    if existing is not None:
        return transcript_id, int(existing["revision"]), True
    nxt = await (
        await conn.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 AS revision FROM transcript_revision"
            " WHERE transcript_id = %s",
            (transcript_id,),
        )
    ).fetchone()
    return transcript_id, int(nxt["revision"]) if nxt else 1, False


async def record_transcript_revision(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    organization_id: UUID,
    transcript_id: UUID,
    revision: int,
    attempt: int,
    storage_key: str,
    size_bytes: int,
    word_count: int,
    language: str,
    provider: str,
    model: str,
    metadata: dict[str, Any],
) -> None:
    """Insert the machine revision and mark the transcript ready at it."""
    await conn.execute(
        """
        INSERT INTO transcript_revision
            (organization_id, transcript_id, revision, storage_key, size_bytes, kind,
             base_revision, word_count, metadata)
        VALUES (%s, %s, %s, %s, %s, 'machine', NULL, %s, %s::jsonb)
        ON CONFLICT (transcript_id, revision) DO UPDATE
           SET storage_key = EXCLUDED.storage_key, size_bytes = EXCLUDED.size_bytes,
               word_count = EXCLUDED.word_count, metadata = EXCLUDED.metadata
        """,
        (
            organization_id,
            transcript_id,
            revision,
            storage_key,
            size_bytes,
            word_count,
            json.dumps({**metadata, "attempt": attempt}),
        ),
    )
    await conn.execute(
        """
        UPDATE transcript
           SET current_revision = %s, language = %s, provider = %s, model = %s,
               status = 'ready', ready_at = now(), stage = NULL, percent = NULL,
               error_message = NULL, heartbeat_at = now(), updated_at = now()
         WHERE id = %s
        """,
        (revision, language, provider, model, transcript_id),
    )


async def finalize_transcription(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    organization_id: UUID,
    source_id: UUID,
    workflow_id: str,
    duration_ms: int,
    revision: int,
    detail: dict[str, Any],
) -> int:
    """Meter the run and the bytes it left behind; returns the storage delta.

    Two entries. `transcription_seconds` is the audio's own duration, because
    that is the cost driver whatever the engine took in wall time; the GPU
    seconds and the GPU ride along in detail, so dollars per source-hour can be
    computed from the ledger alone.

    Storage is a delta against earlier `transcript` entries for this source, the
    same pattern the artifact ledger uses. Revisions are new objects, so the
    delta is usually just this revision's size, but a source whose revisions
    were replaced or removed still meters correctly.
    """
    await conn.execute(
        """
        INSERT INTO usage_ledger (organization_id, kind, quantity, source_id, workflow_id, detail)
        VALUES (%s, 'transcription_seconds', %s, %s, %s, %s::jsonb)
        """,
        (
            organization_id,
            round(duration_ms / 1000),
            source_id,
            workflow_id,
            json.dumps(detail),
        ),
    )
    stored = await (
        await conn.execute(
            """
            SELECT COALESCE(SUM(r.size_bytes), 0)::bigint AS total
              FROM transcript_revision r
              JOIN transcript t ON t.id = r.transcript_id
             WHERE t.source_id = %s
            """,
            (source_id,),
        )
    ).fetchone()
    total = int(stored["total"]) if stored else 0
    previous = await (
        await conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)::bigint AS total FROM usage_ledger
             WHERE source_id = %s AND kind = 'storage_bytes'
               AND detail->>'category' = 'transcript'
            """,
            (source_id,),
        )
    ).fetchone()
    delta = total - (int(previous["total"]) if previous else 0)
    if delta != 0:
        await conn.execute(
            """
            INSERT INTO usage_ledger
                (organization_id, kind, quantity, source_id, workflow_id, detail)
            VALUES (%s, 'storage_bytes', %s, %s, %s, %s::jsonb)
            """,
            (
                organization_id,
                delta,
                source_id,
                workflow_id,
                json.dumps({"category": "transcript", "revision": revision, "total": total}),
            ),
        )
    return delta


async def fail_transcription(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, message: str
) -> None:
    """Terminal failure: the row says why, in words safe to render."""
    await conn.execute(
        """
        UPDATE transcript
           SET status = 'failed', error_message = %s, stage = NULL, percent = NULL,
               updated_at = now()
         WHERE source_id = %s
        """,
        (message[:2000], source_id),
    )
