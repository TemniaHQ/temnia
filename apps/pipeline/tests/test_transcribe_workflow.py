"""TranscribeWorkflow end to end: real Garage, real database, recorded provider.

Everything except the engine is the real thing. The audio object is in the
store, the transcript row and its revision are written by the pipeline role
under RLS, the revision JSON is read back out of the store, and both ledger
rows are checked, because the ledger is the only place the cost of a source can
be reconstructed from.

Needs `pnpm services` and `TEST_DATABASE_URL`, like `test_finalize_ledger.py`.
It never skips silently.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import Scope, TranscribeInput, TranscriptV1
from temnia_pipeline.ingest import Context
from temnia_pipeline.settings import (
    PipelineSettings,
    StorageSettings,
    TranscodeSettings,
    TranscriptionSettings,
)
from temnia_pipeline.transcription.activities import Transcribe
from temnia_pipeline.transcription.factory import make_transcription
from temnia_pipeline.workflows import TranscribeWorkflow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from psycopg import AsyncConnection

    from temnia_pipeline.transcode import Transcoder

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
RECORDING = FIXTURES / "speech-40s.whisperx.json"

SEEDED = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)

# The measured duration of apps/web/e2e/fixtures/speech-40s.mp4.
DURATION_MS = 40_116
# Stands in for the extract. The recorded provider hashes it and never decodes
# it, and the gate's path override is what chooses the recording anyway.
AUDIO = b"speech-40s audio extract stand-in" * 64


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: this test never skips silently")
    return url


def context(database_url: str) -> Context:
    """The worker's own context, with the recording pinned by path.

    The path override is what the gate uses, for the reason the provider's
    docstring gives: the checksum of a re-encoded extract changes with the
    image's ffmpeg, and a recording keyed by it would silently stop matching.
    """
    storage_settings = StorageSettings.from_env()
    store = storage.make_store(storage_settings)
    settings = PipelineSettings(
        database_url=database_url,
        work_root=Path(os.environ.get("PIPELINE_WORK_DIR", "/tmp/temnia-pipeline")),  # noqa: S108
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        transcode=TranscodeSettings(
            backend="local",
            modal_app="temnia-media",
            modal_environment=None,
            progress_dict="temnia-ladder-progress",
        ),
        transcription=TranscriptionSettings(
            provider="recorded",
            modal_app="temnia-media",
            modal_environment=None,
            progress_dict="temnia-transcript-progress",
            recordings_dir=FIXTURES,
            recording=RECORDING,
        ),
    )
    return Context(
        settings=settings,
        storage=storage_settings,
        store=store,
        # Transcription never touches the ladder; a stub is honest about that
        # and keeps the transcode backend out of this test entirely.
        transcoder=cast("Transcoder", object()),
        transcription=make_transcription(settings, store),
    )


@pytest.fixture
async def source() -> AsyncIterator[tuple[str, uuid.UUID, uuid.UUID]]:
    """A ready source with its audio extract in the store; removed afterwards."""
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (SEEDED.organizationId, "transcribe test"),
            )
        ).fetchone()
        assert project is not None
        row = await (
            await conn.execute(
                """
                INSERT INTO source (organization_id, project_id, title, original_filename,
                                    content_type, size_bytes, master_key, status, duration_ms,
                                    audio_channels)
                VALUES (%s, %s, 'speech', 'speech-40s.mp4', 'video/mp4', 795054,
                        'org/x/master', 'ready', %s, 1)
                RETURNING id
                """,
                (SEEDED.organizationId, project["id"], DURATION_MS),
            )
        ).fetchone()
        assert row is not None
    source_id: uuid.UUID = row["id"]
    prefix = f"org/{SEEDED.organizationId}/source/{source_id}/"
    store = storage.make_store(StorageSettings.from_env())
    await storage.upload_bytes(store, prefix + "audio/audio.m4a", AUDIO, "audio/mp4")
    yield prefix, source_id, project["id"]
    await storage.delete_prefix(store, prefix)
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute("DELETE FROM project WHERE id = %s", (project["id"],))
    await db.close_pool()


async def transcript_row(
    conn: AsyncConnection[dict[str, Any]], source_id: uuid.UUID
) -> dict[str, Any]:
    row = await (
        await conn.execute("SELECT * FROM transcript WHERE source_id = %s", (source_id,))
    ).fetchone()
    assert row is not None
    return row


async def run_workflow(ctx: Context, request: TranscribeInput) -> object:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        queue = f"test-{uuid.uuid4()}"
        transcribe = Transcribe(ctx)
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[TranscribeWorkflow],
            activities=transcribe.activities(),
            workflow_runner=SandboxedWorkflowRunner(
                restrictions=SandboxRestrictions.default.with_passthrough_modules(
                    "pydantic", "pydantic_core"
                )
            ),
        ):
            return await env.client.execute_workflow(
                TranscribeWorkflow.run,
                request,
                id=f"transcribe-{request.sourceId}",
                task_queue=queue,
            )


@pytest.mark.timeout(180)
async def test_a_recorded_run_reaches_ready_with_a_revision_and_two_ledger_rows(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    prefix, source_id, _project_id = source
    url = pipeline_url()
    ctx = context(url)
    request = TranscribeInput(
        scope=SEEDED,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=prefix + "audio/audio.m4a",
        durationMs=DURATION_MS,
    )
    result = await run_workflow(ctx, request)
    assert result is not None

    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
        assert row["status"] == "ready"
        assert row["attempts"] == 1
        assert row["current_revision"] == 1
        assert row["language"] == "en"
        assert row["provider"] == "recorded"
        assert row["stage"] is None
        assert row["error_message"] is None
        assert row["ready_at"] is not None
        assert row["speaker_labels"] == {}

        revisions = await (
            await conn.execute(
                "SELECT * FROM transcript_revision WHERE transcript_id = %s ORDER BY revision",
                (row["id"],),
            )
        ).fetchall()
        assert len(revisions) == 1
        revision = revisions[0]
        assert revision["kind"] == "machine"
        assert revision["revision"] == 1
        assert revision["base_revision"] is None
        assert revision["word_count"] == 93
        assert revision["metadata"]["attempt"] == 1
        assert revision["storage_key"] == f"{prefix}transcript/rev-1.json"

        ledger = await (
            await conn.execute(
                "SELECT kind, quantity, detail FROM usage_ledger WHERE source_id = %s"
                " ORDER BY kind",
                (source_id,),
            )
        ).fetchall()
    kinds = {entry["kind"]: entry for entry in ledger}
    assert set(kinds) == {"storage_bytes", "transcription_seconds"}
    # The audio's own duration, not the wall time: that is the cost driver
    # whatever the engine took.
    assert kinds["transcription_seconds"]["quantity"] == round(DURATION_MS / 1000)
    assert kinds["transcription_seconds"]["detail"]["category"] == "transcription"
    assert kinds["transcription_seconds"]["detail"]["provider"] == "recorded"
    assert kinds["transcription_seconds"]["detail"]["attempt"] == 1
    assert kinds["storage_bytes"]["detail"] == {
        "category": "transcript",
        "revision": 1,
        "total": revision["size_bytes"],
    }
    assert kinds["storage_bytes"]["quantity"] == revision["size_bytes"]

    store = storage.make_store(StorageSettings.from_env())
    body = await storage.read_text(store, revision["storage_key"])
    assert body is not None
    transcript = TranscriptV1.model_validate_json(body)
    assert transcript.version == 1
    assert len(transcript.words) == 93
    assert transcript.speakers == ["0", "1"]
    assert transcript.provider.name == "recorded"
    assert transcript.durationMs == DURATION_MS
    # The engine's own response is kept beside the revision, for a fixture and
    # for S12's calibration round.
    raw = await storage.read_text(store, f"{prefix}transcript/raw-1.json")
    assert raw is not None
    assert json.loads(raw)["language"] == "en"
    await db.close_pool()


@pytest.mark.timeout(180)
async def test_a_second_start_for_a_ready_transcript_is_refused_not_re_run(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """One transcript per source. A retry parks the row at pending first."""
    prefix, source_id, _project_id = source
    url = pipeline_url()
    ctx = context(url)
    request = TranscribeInput(
        scope=SEEDED,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=prefix + "audio/audio.m4a",
        durationMs=DURATION_MS,
    )
    await run_workflow(ctx, request)
    with pytest.raises(WorkflowFailureError):
        await run_workflow(ctx, request)

    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
        assert row["status"] == "ready"
        assert row["attempts"] == 1
        revisions = await (
            await conn.execute(
                "SELECT count(*) AS n FROM transcript_revision WHERE transcript_id = %s",
                (row["id"],),
            )
        ).fetchone()
        assert revisions is not None
        assert revisions["n"] == 1
    await db.close_pool()


@pytest.mark.timeout(180)
async def test_a_retry_after_a_reset_writes_the_next_revision(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """What the web's Retry does: park the row at pending, then start the same id."""
    prefix, source_id, _project_id = source
    url = pipeline_url()
    ctx = context(url)
    request = TranscribeInput(
        scope=SEEDED,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=prefix + "audio/audio.m4a",
        durationMs=DURATION_MS,
    )
    await run_workflow(ctx, request)
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            "UPDATE transcript SET status = 'pending' WHERE source_id = %s", (source_id,)
        )
    await run_workflow(ctx, request)

    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
        assert row["attempts"] == 2
        assert row["current_revision"] == 2
        revisions = await (
            await conn.execute(
                "SELECT revision, storage_key, metadata FROM transcript_revision"
                " WHERE transcript_id = %s ORDER BY revision",
                (row["id"],),
            )
        ).fetchall()
        # A re-run is a new revision, never an overwrite of the one a user may
        # already have corrected against.
        assert [r["revision"] for r in revisions] == [1, 2]
        assert [r["metadata"]["attempt"] for r in revisions] == [1, 2]
        assert revisions[1]["storage_key"] == f"{prefix}transcript/rev-2.json"

        ledger = await (
            await conn.execute(
                "SELECT quantity, detail FROM usage_ledger WHERE source_id = %s"
                " AND kind = 'storage_bytes' ORDER BY recorded_at",
                (source_id,),
            )
        ).fetchall()
    # Two objects in storage, so the second entry is the second revision's own
    # size: the delta against what the first entry already counted.
    assert len(ledger) == 2
    assert ledger[1]["detail"]["revision"] == 2
    assert ledger[1]["quantity"] > 0
    await db.close_pool()
