"""The reaper: abandoned uploads are aborted at the store and failed in the row.

Runs on a Temporal schedule every fifteen minutes. An upload is abandoned when
nothing has signed a part for the resume window (24 h; listing parts is never
a liveness signal). The abort is storage-first, so a crash between the two
steps leaves a row that is reaped again, never stranded parts under a row
that says otherwise. Stalled ingests need no reaper: Temporal's heartbeat
timeouts retry or fail the workflow, and the workflow writes the failure to
the row.

The sweep crosses organizations on purpose, and does it through RLS, not
around it: the pipeline role has one declared policy that lets it enumerate
`organization` ids (packages/db, `organization_enumerable_by_pipeline`), and
every row it then touches is read and written inside a transaction scoped to
that organization. The workflow itself lives in workflows.py: this module
imports the database and storage clients, which the workflow sandbox refuses.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
)

from temnia_pipeline import db
from temnia_pipeline.contracts import Scope
from temnia_pipeline.storage import abort_multipart

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg import AsyncConnection

    from temnia_pipeline.ingest import Context

log = logging.getLogger("temnia.reaper")

SCHEDULE_ID = "temnia-reaper"
INTERVAL = timedelta(minutes=15)
UPLOAD_IDLE_TTL = timedelta(hours=24)
BATCH = 50
SERVICE_USER = UUID(int=0)


async def list_organizations(database_url: str) -> list[UUID]:
    """Every organization id, through the pipeline role's enumeration policy."""
    pool = await db.get_pool(database_url)
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT id FROM organization ORDER BY id")).fetchall()
    return [row["id"] for row in rows]


async def reap_uploads(conn: AsyncConnection[dict[str, Any]], ctx: Context) -> int:
    """Abort idle uploads for the scoped organization; returns how many."""
    rows = await (
        await conn.execute(
            """
            SELECT u.id, u.source_id, u.storage_key, u.multipart_upload_id
              FROM upload u
             WHERE u.status = 'active' AND u.last_activity_at < now() - %s::interval
             ORDER BY u.last_activity_at
             LIMIT %s
            """,
            (UPLOAD_IDLE_TTL, BATCH),
        )
    ).fetchall()
    count = 0
    for row in rows:
        await abort_multipart(ctx.storage, row["storage_key"], row["multipart_upload_id"])
        await conn.execute(
            "UPDATE upload SET status = 'aborted' WHERE id = %s AND status = 'active'",
            (row["id"],),
        )
        await conn.execute(
            """
            UPDATE source SET status = 'failed', updated_at = now(),
                   error_message = 'Upload never finished. Upload the file again to replace it.'
             WHERE id = %s AND status = 'uploading'
            """,
            (row["source_id"],),
        )
        count += 1
    return count


class Reaper:
    """Activities for the reaper workflow."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    @activity.defn(name="reap_abandoned_uploads")
    async def reap_abandoned_uploads(self) -> int:
        """Sweep every organization, each inside its own scope."""
        total = 0
        for organization_id in await list_organizations(self.ctx.settings.database_url):
            scope = Scope(organizationId=organization_id, userId=SERVICE_USER)
            async with db.scoped(self.ctx.settings.database_url, scope) as conn:
                total += await reap_uploads(conn, self.ctx)
        if total:
            log.info("reaped %d abandoned uploads", total)
        return total

    def activities(self) -> list[Callable[..., Any]]:
        """Everything the worker registers."""
        return [self.reap_abandoned_uploads]


async def ensure_reaper_schedule(client: Client, task_queue: str) -> None:
    """Create the schedule once; idempotent across worker restarts."""
    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "ReaperWorkflow",
                    id="reaper",
                    task_queue=task_queue,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=INTERVAL)]),
            ),
        )
        log.info("reaper schedule created")
    except ScheduleAlreadyRunningError:
        pass
