"""One best-effort transcript liveness write paired with a Temporal heartbeat."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from temporalio import activity

from temnia_pipeline import db

if TYPE_CHECKING:
    from temnia_pipeline.contracts import TranscribeInput, TranscriptStage

log = logging.getLogger(__name__)
MAX_PROGRESS = 100
INVALID_PROGRESS = "speech progress percent must be between zero and 100"


async def report_speech_progress(
    database_url: str,
    request: TranscribeInput,
    stage: TranscriptStage,
    percent: int | None = None,
    *,
    update_visible_stage: bool = True,
) -> None:
    """Heartbeat durably, then refresh the run-fenced transcript row when possible.

    Parallel coverage polling passes ``update_visible_stage=False`` while the
    primary GPU stage is active. It still refreshes liveness without making the
    UI oscillate between two concurrent stages.
    """
    if percent is not None and not 0 <= percent <= MAX_PROGRESS:
        raise ValueError(INVALID_PROGRESS)
    info = activity.info()
    run_id = info.workflow_run_id or "unknown"
    activity.heartbeat({"stage": stage.value, "progress": percent})
    try:
        async with db.scoped(database_url, request.scope) as conn:
            await db.report_transcription_progress(
                conn,
                request.sourceId,
                stage.value if update_visible_stage else None,
                percent,
                run_id,
            )
    except Exception:
        log.warning(
            "checkpointed speech progress write failed",
            extra={"source_id": str(request.sourceId), "stage": stage.value},
            exc_info=True,
        )
