"""One create-only execution claim and one immutable terminal outcome per attempt."""

# ruff: noqa: EM101, TRY003
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    canonical,
    validate_result,
)
from temnia_pipeline.speech.checkpoints import (
    AdmissionOutcomeUnknownError,
    CheckpointCollisionError,
)

if TYPE_CHECKING:
    from temnia_pipeline.speech.checkpoints import CheckpointStore


async def prepare_execution(
    store: CheckpointStore,
    job: ChapterLlamaJob,
    *,
    build: str,
    call_id: str,
    task_id: str,
) -> ChapterLlamaOutcome | None:
    """Reuse a proven result; an incomplete admission never authorizes another run."""
    if job.expected_build != build or not call_id or not task_id:
        raise ValueError("Chapter-Llama deployment or execution identity differs from the request")
    existing = await store.read(job.checkpoint_key)
    if existing is not None:
        outcome = ChapterLlamaOutcome.model_validate_json(existing)
        if outcome.job_sha256 != job.sha256 or outcome.build != build:
            raise CheckpointCollisionError("Chapter-Llama checkpoint belongs to another request")
        if outcome.result is not None:
            validate_result(outcome.result, job.input)
        return outcome
    admission = canonical(
        {
            "protocol": job.protocol,
            "job_sha256": job.sha256,
            "build": build,
            "modal_call_id": call_id,
            "modal_task_id": task_id,
        }
    )
    if not await store.create(job.admission_key, admission):
        raise AdmissionOutcomeUnknownError(
            "Chapter-Llama inference admission already exists without a committed outcome"
        )
    return None


async def publish_outcome(
    store: CheckpointStore,
    job: ChapterLlamaJob,
    outcome: ChapterLlamaOutcome,
) -> None:
    """Commit known failures too, preserving paid output and never overwriting bytes."""
    if outcome.job_sha256 != job.sha256 or outcome.build != job.expected_build:
        raise CheckpointCollisionError("Chapter-Llama outcome identity differs from its job")
    if outcome.status == "outcome_unknown":
        raise CheckpointCollisionError("unknown execution cannot become a terminal checkpoint")
    admission_body = await store.read(job.admission_key)
    if admission_body is None:
        raise CheckpointCollisionError("outcome has no inference admission")
    admission = json.loads(admission_body)
    if admission != {
        "protocol": job.protocol,
        "job_sha256": job.sha256,
        "build": job.expected_build,
        "modal_call_id": outcome.modal_call_id,
        "modal_task_id": outcome.modal_task_id,
    }:
        raise CheckpointCollisionError("outcome does not own this inference admission")
    if outcome.result is not None:
        validate_result(outcome.result, job.input)
    body = canonical(outcome.model_dump(mode="json"))
    if (
        not await store.create(job.checkpoint_key, body)
        and await store.read(job.checkpoint_key) != body
    ):
        raise CheckpointCollisionError("Chapter-Llama terminal outcome collision")
