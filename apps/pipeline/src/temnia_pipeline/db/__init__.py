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
             WHERE id = %s
               AND deletion_requested_at IS NULL
               AND status IN ('uploaded', 'failed', 'processing', 'ready')
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
    run_id: str,
    artifacts: list[ArtifactRecord],
    duration_ms: int,
    processing_seconds: int,
) -> int:
    """Insert artifact rows, mark the source ready, and write the ledger entries.

    The processing entry carries an idempotency key of the source and the
    Temporal run: a retry of this activity after a commit whose acknowledgement
    was lost inserts nothing, and a re-ingest (a new run) meters again because
    it was paid for again. The storage entry is a delta and needs no key.

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
        INSERT INTO usage_ledger
            (organization_id, kind, quantity, source_id, workflow_id, detail, idempotency_key)
        VALUES (%s, 'processing_seconds', %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        """,
        (
            scope.organizationId,
            round(duration_ms / 1000),
            source_id,
            workflow_id,
            json.dumps({"stage": "ingest", "wallSeconds": processing_seconds}),
            f"processing:{source_id}:{run_id}",
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


class StaleRunError(RuntimeError):
    """The row belongs to a later run of the same workflow; this one may not write it."""


async def claim_transcription(
    conn: AsyncConnection[dict[str, Any]],
    source_id: UUID,
    organization_id: UUID,
    workflow_id: str,
    run_id: str,
) -> int:
    """Insert or claim the source's transcript row; returns the attempt, or 0.

    Claimable means missing, `pending`, or `failed`. A row already `processing`
    or `ready` returns 0, which is what makes a second workflow for the same
    source a no-op rather than a second GPU job; the unique index on
    `source_id` is what the upsert conflicts on.

    Idempotent per run. The claim commits and its acknowledgement can be lost
    (a worker killed in between), and Temporal then runs the activity again in
    the same run. That retry finds its own `run_id` on a `processing` row and
    gets the same attempt back, touching nothing else; without this rule it saw
    `processing`, returned 0, and stranded the row with no run behind it.

    A retry from the web sets a `ready` or `failed` row back to `pending` first,
    so the two rules do not contradict each other: this activity refuses to
    interrupt work, and the user's Retry is the thing that says a finished
    transcript may be replaced.

    The source row is locked before the transcript row. Deletion uses the same
    source-first order, so a claim either commits before deletion starts or
    observes the deletion fence and creates no transcript state.
    """
    source = await (
        await conn.execute(
            """
            SELECT id FROM source
             WHERE id = %s AND organization_id = %s
               AND deletion_requested_at IS NULL
             FOR UPDATE
            """,
            (source_id, organization_id),
        )
    ).fetchone()
    if source is None:
        return 0
    row = await (
        await conn.execute(
            """
            INSERT INTO transcript (organization_id, source_id, status, attempts, workflow_id,
                                    run_id, stage, percent, heartbeat_at)
            VALUES (%(organization_id)s, %(source_id)s, 'processing', 1, %(workflow_id)s,
                    %(run_id)s, 'download', NULL, now())
            ON CONFLICT (source_id) DO UPDATE
               SET status = 'processing',
                   attempts = CASE WHEN transcript.run_id = EXCLUDED.run_id
                                   THEN transcript.attempts ELSE transcript.attempts + 1 END,
                   workflow_id = EXCLUDED.workflow_id, run_id = EXCLUDED.run_id,
                   stage = CASE WHEN transcript.run_id = EXCLUDED.run_id
                                THEN transcript.stage ELSE 'download' END,
                   percent = CASE WHEN transcript.run_id = EXCLUDED.run_id
                                  THEN transcript.percent ELSE NULL END,
                   error_message = NULL, heartbeat_at = now(), updated_at = now()
             WHERE transcript.status IN ('pending', 'failed')
                OR (transcript.status = 'processing' AND transcript.run_id = EXCLUDED.run_id)
         RETURNING attempts
            """,
            {
                "organization_id": organization_id,
                "source_id": source_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
            },
        )
    ).fetchone()
    return int(row["attempts"]) if row else 0


async def report_transcription_progress(
    conn: AsyncConnection[dict[str, Any]],
    source_id: UUID,
    stage: str | None,
    percent: int | None,
    run_id: str,
) -> None:
    """Best-effort progress, and the heartbeat the surface reads to say "stalled".

    Fenced on the run: a late write from a run that lost the row changes nothing.
    A null stage is a liveness-only write for parallel work that must not replace
    the visible primary stage or its last measured percentage.
    """
    if stage is None:
        await conn.execute(
            """
            UPDATE transcript
               SET heartbeat_at = now(), updated_at = now()
             WHERE source_id = %s AND status = 'processing' AND run_id = %s
            """,
            (source_id, run_id),
        )
        return
    await conn.execute(
        """
        UPDATE transcript
           SET stage = %s, percent = %s, heartbeat_at = now(), updated_at = now()
         WHERE source_id = %s AND status = 'processing' AND run_id = %s
        """,
        (stage, percent, source_id, run_id),
    )


async def next_transcript_revision(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, attempt: int, run_id: str
) -> tuple[UUID, int, bool]:
    """The revision this attempt writes: its transcript, its number, and whether it exists.

    Idempotent by attempt, not by call. An activity that uploaded the object
    and then died before committing runs again, finds no row, computes the same
    number, and overwrites the same key; one that committed finds its own row
    and reuses it rather than writing a second revision for one engine run.

    The number is the next after every revision the transcript has, not the
    attempt number, because a user's corrections write revisions too and a
    machine re-run must never land on top of one. The transcript row is locked
    for the rest of the transaction, so a correction saving at the same moment
    waits, sees the machine revision as current, and is refused as stale; or it
    commits first and the machine run computes the number after it. A run that
    no longer owns the row (a later run claimed it) is told so and writes
    nothing.
    """
    transcript = await (
        await conn.execute(
            "SELECT id, run_id FROM transcript WHERE source_id = %s FOR UPDATE", (source_id,)
        )
    ).fetchone()
    if transcript is None:
        msg = f"no transcript row for source {source_id}"
        raise LookupError(msg)
    if transcript["run_id"] != run_id:
        msg = f"transcript for source {source_id} is owned by run {transcript['run_id']!r}"
        raise StaleRunError(msg)
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
    run_id: str,
) -> None:
    """Insert the machine revision and mark the transcript ready at it.

    A plain insert. The number was allocated under the row lock, so a conflict
    here would mean the lock was not held; it must fail rather than overwrite
    a correction's row with machine output, which the earlier upsert did.
    """
    await conn.execute(
        """
        INSERT INTO transcript_revision
            (organization_id, transcript_id, revision, storage_key, size_bytes, kind,
             base_revision, word_count, metadata)
        VALUES (%s, %s, %s, %s, %s, 'machine', NULL, %s, %s::jsonb)
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
         WHERE id = %s AND run_id = %s
        """,
        (revision, language, provider, model, transcript_id, run_id),
    )


async def finalize_transcription(  # noqa: PLR0913
    conn: AsyncConnection[dict[str, Any]],
    *,
    organization_id: UUID,
    source_id: UUID,
    workflow_id: str,
    duration_ms: int,
    revision: int,
    attempt: int,
    detail: dict[str, Any],
) -> int:
    """Meter the run and the bytes it left behind; returns the storage delta.

    Two entries. `transcription_seconds` is the audio's own duration, because
    that is the cost driver whatever the engine took in wall time; the GPU
    seconds and the GPU ride along in detail, so dollars per source-hour can be
    computed from the ledger alone. It is keyed by source and attempt, so a
    retry of this activity after a lost acknowledgement inserts nothing and a
    second engine run (a new attempt) meters again; the review's reproduction
    of one 60-second run metered as 120 cannot recur.

    Storage is a delta against earlier `transcript` entries for this source, the
    same pattern the artifact ledger uses. Revisions are new objects, so the
    delta is usually just this revision's size, but a source whose revisions
    were replaced or removed still meters correctly.
    """
    # Corrections take this same row lock with their revision compare-and-swap.
    # Acquire it before touching the ledger: both totals must describe the same
    # committed revision set, and every writer must take locks in the same order.
    await conn.execute("SELECT id FROM transcript WHERE source_id = %s FOR UPDATE", (source_id,))
    await conn.execute(
        """
        INSERT INTO usage_ledger
            (organization_id, kind, quantity, source_id, workflow_id, detail, idempotency_key)
        VALUES (%s, 'transcription_seconds', %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        """,
        (
            organization_id,
            round(duration_ms / 1000),
            source_id,
            workflow_id,
            json.dumps(detail),
            f"transcription:{source_id}:{attempt}",
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
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, message: str, run_id: str
) -> None:
    """Terminal failure: the row says why, in words safe to render. Fenced on the run."""
    await conn.execute(
        """
        UPDATE transcript
           SET status = 'failed', error_message = %s, stage = NULL, percent = NULL,
               updated_at = now()
         WHERE source_id = %s AND run_id = %s
        """,
        (message[:2000], source_id, run_id),
    )


async def mark_transcript_unavailable(
    conn: AsyncConnection[dict[str, Any]], source_id: UUID, organization_id: UUID, message: str
) -> None:
    """A transcript that never got a run: no audio, or a dispatch that failed.

    Written by the ingest, which owns no transcript run, so it is not fenced on
    one; instead it refuses to touch a row that is `processing` or `ready`,
    which belongs to a run or to a finished transcript. The message carries a
    type name in front (`NoAudioError:`, `DispatchError:`) that the surface
    classifies, the same rule the runner's failures follow. Without this row the
    tab said "Queued" for ever.
    """
    await conn.execute(
        """
        INSERT INTO transcript (organization_id, source_id, status, attempts, error_message)
        VALUES (%s, %s, 'failed', 0, %s)
        ON CONFLICT (source_id) DO UPDATE
           SET status = 'failed', error_message = EXCLUDED.error_message, stage = NULL,
               percent = NULL, updated_at = now()
         WHERE transcript.status IN ('pending', 'failed')
        """,
        (organization_id, source_id, message[:2000]),
    )
