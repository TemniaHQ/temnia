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

import asyncio
import dataclasses
import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
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
from temnia_pipeline.transcription.runner import provider_failure, transcription_failure
from temnia_pipeline.workflows import TranscribeWorkflow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from psycopg import AsyncConnection

    from temnia_pipeline.transcode import Transcoder
    from temnia_pipeline.transcription import TranscribeJob

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


class FlakyProvider:
    """Fails the way a preempted container does, and watches the row while it does.

    The row's state *between* two attempts is a user-facing state with words of
    its own ("being retried"), and it is only observable from inside a later
    attempt, which is what this provider is for.
    """

    def __init__(self, database_url: str, source_id: uuid.UUID) -> None:
        self.database_url = database_url
        self.source_id = source_id
        self.seen: list[tuple[str, str | None]] = []
        self.stages: list[str | None] = []

    @property
    def name(self) -> str:
        return "flaky"

    @property
    def model(self) -> str:
        return "flaky"

    @property
    def version(self) -> str:
        return "1"

    async def start(self, job: TranscribeJob) -> str:
        _ = job
        async with db.scoped(self.database_url, SEEDED) as conn:
            row = await transcript_row(conn, self.source_id)
        self.seen.append((row["status"], row["stage"]))
        msg = "the container was preempted"
        raise ConnectionError(msg)

    async def peek(self) -> str | None:
        async with db.scoped(self.database_url, SEEDED) as conn:
            row = await transcript_row(conn, self.source_id)
        return row["stage"]

    async def status(self, handle: str) -> object:
        raise AssertionError(handle)

    async def progress(self, handle: str) -> object:
        raise AssertionError(handle)


@pytest.mark.timeout(180)
async def test_the_row_says_retrying_between_attempts_and_fails_readably_after_the_last(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    prefix, source_id, _project_id = source
    url = pipeline_url()
    ctx = context(url)
    provider = FlakyProvider(url, source_id)
    ctx.transcription.provider = provider  # pyright: ignore[reportAttributeAccessIssue]
    request = TranscribeInput(
        scope=SEEDED,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=prefix + "audio/audio.m4a",
        durationMs=DURATION_MS,
    )
    with pytest.raises(WorkflowFailureError):
        await run_workflow(ctx, request)

    # Four attempts, the retry policy's maximum, and the row was `processing`
    # at every one of them. The surface must never flash Failed while another
    # attempt is coming; only the workflow, after the last one, writes `failed`.
    assert len(provider.seen) == 4
    assert all(status == "processing" for status, _ in provider.seen)

    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
        assert row["status"] == "failed"
        assert row["stage"] is None
        assert row["percent"] is None
        assert row["current_revision"] is None
        # Words a person reads, not a stack trace, and not the enum.
        assert "preempted" in row["error_message"]
        ledger = await (
            await conn.execute(
                "SELECT count(*) AS n FROM usage_ledger WHERE source_id = %s", (source_id,)
            )
        ).fetchone()
        assert ledger is not None
        # Nothing was produced, so nothing is metered.
        assert ledger["n"] == 0
    await db.close_pool()


@pytest.mark.timeout(60)
async def test_a_retryable_failure_parks_the_stage_at_retrying_and_a_terminal_one_does_not(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """The state the plan calls Retrying, written by the activity that is about to lose.

    The workflow test above proves the row stays `processing` across four
    attempts; this proves the words the surface reads while it does, and that a
    terminal failure is left alone for the workflow to word properly.
    """
    prefix, source_id, _project_id = source
    url = pipeline_url()
    transcribe = Transcribe(context(url))
    request = TranscribeInput(
        scope=SEEDED,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=prefix + "audio/audio.m4a",
        durationMs=DURATION_MS,
    )
    async with db.scoped(url, SEEDED) as conn:
        assert await db.claim_transcription(
            conn, source_id, SEEDED.organizationId, "wf-retrying", "run-retrying"
        )

    # Under an activity context carrying the run that claimed the row: the
    # progress write is fenced on it, like every write a run makes.
    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info, workflow_id="wf-retrying", workflow_run_id="run-retrying"
    )
    await env.run(
        transcribe.mark_retrying, request, provider_failure("the container was preempted")
    )
    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
    assert (row["status"], row["stage"]) == ("processing", "retrying")

    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            "UPDATE transcript SET stage = 'align' WHERE source_id = %s", (source_id,)
        )
    await env.run(
        transcribe.mark_retrying,
        request,
        transcription_failure("this recording is in a language we cannot align yet"),
    )
    async with db.scoped(url, SEEDED) as conn:
        row = await transcript_row(conn, source_id)
    # Untouched: after the last attempt the workflow writes the real message,
    # and a row that said "being retried" when nothing was coming would lie.
    assert row["stage"] == "align"
    await db.close_pool()


# --- The database rules the review found wanting, checked on the migrated schema ---


@pytest.mark.timeout(60)
async def test_a_claim_is_idempotent_per_run_and_refused_to_another(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """A lost acknowledgement re-claims and gets the same attempt back (S2 review, I04)."""
    _prefix, source_id, _project_id = source
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        first = await db.claim_transcription(
            conn, source_id, SEEDED.organizationId, "transcribe-x", "run-1"
        )
        again = await db.claim_transcription(
            conn, source_id, SEEDED.organizationId, "transcribe-x", "run-1"
        )
        other = await db.claim_transcription(
            conn, source_id, SEEDED.organizationId, "transcribe-x", "run-2"
        )
        row = await transcript_row(conn, source_id)
    assert (first, again, other) == (1, 1, 0)
    assert row["attempts"] == 1
    assert row["run_id"] == "run-1"
    assert row["status"] == "processing"
    await db.close_pool()


@pytest.mark.timeout(60)
async def test_writes_from_a_run_that_lost_the_row_change_nothing(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """Every write of a run is fenced on its id; a later run owns the row."""
    _prefix, source_id, _project_id = source
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        assert (
            await db.claim_transcription(
                conn, source_id, SEEDED.organizationId, "transcribe-x", "run-1"
            )
            == 1
        )
        # The web's Retry, then a second run claims.
        await conn.execute(
            "UPDATE transcript SET status = 'pending' WHERE source_id = %s", (source_id,)
        )
        assert (
            await db.claim_transcription(
                conn, source_id, SEEDED.organizationId, "transcribe-x", "run-2"
            )
            == 2
        )

        await db.report_transcription_progress(conn, source_id, "align", 50, "run-1")
        await db.fail_transcription(conn, source_id, "Whatever: too late", "run-1")
        row = await transcript_row(conn, source_id)
        assert row["status"] == "processing"
        assert row["stage"] == "download"
        assert row["run_id"] == "run-2"
        with pytest.raises(db.StaleRunError):
            await db.next_transcript_revision(conn, source_id, 1, "run-1")

        await db.report_transcription_progress(conn, source_id, "align", 50, "run-2")
        assert (await transcript_row(conn, source_id))["stage"] == "align"
    await db.close_pool()


@pytest.mark.timeout(60)
async def test_the_transcription_entry_is_metered_once_per_attempt(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """The review metered one 60-second run as 120 by finalising twice (I06)."""
    _prefix, source_id, _project_id = source
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        for attempt in (1, 1, 2):
            await db.finalize_transcription(
                conn,
                organization_id=SEEDED.organizationId,
                source_id=source_id,
                workflow_id="transcribe-x",
                duration_ms=60_000,
                revision=1,
                attempt=attempt,
                detail={"category": "transcription", "attempt": attempt},
            )
        rows = await (
            await conn.execute(
                "SELECT quantity, detail FROM usage_ledger"
                " WHERE source_id = %s AND kind = 'transcription_seconds'"
                " ORDER BY recorded_at",
                (source_id,),
            )
        ).fetchall()
    assert [row["quantity"] for row in rows] == [60, 60]
    assert [row["detail"]["attempt"] for row in rows] == [1, 2]
    await db.close_pool()


@pytest.mark.timeout(60)
async def test_a_correction_that_lands_first_pushes_the_machine_revision_after_it(
    source: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """The machine write used to upsert over a correction's row (S2 review, I03)."""
    prefix, source_id, _project_id = source
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        assert (
            await db.claim_transcription(
                conn, source_id, SEEDED.organizationId, "transcribe-x", "run-1"
            )
            == 1
        )
        row = await transcript_row(conn, source_id)
        await conn.execute(
            """
            INSERT INTO transcript_revision
                (organization_id, transcript_id, revision, storage_key, size_bytes, kind,
                 base_revision, word_count, metadata)
            VALUES (%s, %s, 1, %s, 10, 'correction', NULL, 3, '{}'::jsonb)
            """,
            (SEEDED.organizationId, row["id"], f"{prefix}transcript/rev-1-abcdef12.json"),
        )
        transcript_id, revision, already = await db.next_transcript_revision(
            conn, source_id, 1, "run-1"
        )
        assert (transcript_id, revision, already) == (row["id"], 2, False)
        await db.record_transcript_revision(
            conn,
            organization_id=SEEDED.organizationId,
            transcript_id=transcript_id,
            revision=revision,
            attempt=1,
            storage_key=f"{prefix}transcript/rev-2.json",
            size_bytes=20,
            word_count=93,
            language="en",
            provider="recorded",
            model="fixture",
            metadata={},
            run_id="run-1",
        )
        revisions = await (
            await conn.execute(
                "SELECT revision, kind, storage_key FROM transcript_revision"
                " WHERE transcript_id = %s ORDER BY revision",
                (row["id"],),
            )
        ).fetchall()
        row = await transcript_row(conn, source_id)
    assert [(r["revision"], r["kind"]) for r in revisions] == [(1, "correction"), (2, "machine")]
    assert revisions[0]["storage_key"].endswith("rev-1-abcdef12.json")
    assert row["current_revision"] == 2
    assert row["status"] == "ready"
    await db.close_pool()


async def _publish_correction_for_accounting(
    conn: AsyncConnection[dict[str, Any]], source_id: uuid.UUID
) -> None:
    # The web's revision CAS takes the transcript row lock before inserting a
    # revision and reconciling storage. Exercise that transaction on real rows.
    row = await (
        await conn.execute(
            "UPDATE transcript SET current_revision = 2"
            " WHERE source_id = %s AND current_revision = 1 RETURNING id",
            (source_id,),
        )
    ).fetchone()
    assert row is not None
    await conn.execute(
        "INSERT INTO transcript_revision"
        " (organization_id, transcript_id, revision, storage_key, size_bytes, kind,"
        " base_revision, word_count, metadata)"
        " VALUES (%s, %s, 2, 'org/test/correction.json', 110, 'correction', 1, 1, '{}')",
        (SEEDED.organizationId, row["id"]),
    )
    await conn.execute(
        "INSERT INTO usage_ledger (organization_id, kind, quantity, source_id, detail)"
        " SELECT %s, 'storage_bytes',"
        " (SELECT SUM(size_bytes) FROM transcript_revision WHERE transcript_id = %s)"
        ' - COALESCE(SUM(quantity), 0), %s, \'{"category":"transcript"}\'::jsonb'
        " FROM usage_ledger WHERE source_id = %s AND kind = 'storage_bytes'"
        " AND detail->>'category' = 'transcript'",
        (SEEDED.organizationId, row["id"], source_id, source_id),
    )


async def _finalize_for_accounting(
    conn: AsyncConnection[dict[str, Any]], source_id: uuid.UUID
) -> None:
    await db.finalize_transcription(
        conn,
        organization_id=SEEDED.organizationId,
        source_id=source_id,
        workflow_id="accounting-race",
        duration_ms=60_000,
        revision=1,
        attempt=1,
        detail={"category": "transcription", "attempt": 1},
    )


@pytest.mark.timeout(30)
@pytest.mark.parametrize("machine_first", [True, False])
async def test_correction_and_finalizer_serialize_storage_accounting(
    source: tuple[str, uuid.UUID, uuid.UUID], *, machine_first: bool
) -> None:
    """Two real connections must block on the same row in either arrival order."""
    prefix, source_id, _project_id = source
    url = pipeline_url()
    async with db.scoped(url, SEEDED) as conn:
        await db.claim_transcription(
            conn, source_id, SEEDED.organizationId, "accounting-race", "run-accounting"
        )
        row = await transcript_row(conn, source_id)
        await db.record_transcript_revision(
            conn,
            organization_id=SEEDED.organizationId,
            transcript_id=row["id"],
            revision=1,
            attempt=1,
            storage_key=f"{prefix}transcript/rev-1.json",
            size_bytes=100,
            word_count=1,
            language="en",
            provider="recorded",
            model="fixture",
            metadata={},
            run_id="run-accounting",
        )
    first = _finalize_for_accounting if machine_first else _publish_correction_for_accounting
    second = _publish_correction_for_accounting if machine_first else _finalize_for_accounting
    started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def competing_transaction() -> None:
        async with db.scoped(url, SEEDED) as other:
            started.set_result(other.info.backend_pid)
            await second(other, source_id)

    task: asyncio.Task[None] | None = None
    try:
        async with db.scoped(url, SEEDED) as conn:
            await first(conn, source_id)
            task = asyncio.create_task(competing_transaction())
            other_pid = await asyncio.wait_for(started, timeout=5)
            # Inspect PostgreSQL's actual blocker graph; a timing-only sleep
            # could pass even when the second transaction does not take a lock.
            async with asyncio.timeout(5):
                while True:
                    blockers = await (
                        await conn.execute("SELECT pg_blocking_pids(%s) AS pids", (other_pid,))
                    ).fetchone()
                    assert blockers is not None
                    if conn.info.backend_pid in blockers["pids"]:
                        break
                    assert not task.done(), "accounting bypassed the transcript lock"
                    await asyncio.sleep(0.01)
    finally:
        if task is not None:
            await asyncio.wait_for(task, timeout=5)

    async with db.scoped(url, SEEDED) as conn:
        # Lost acknowledgement: rerunning finalization still adds no usage.
        await _finalize_for_accounting(conn, source_id)
        totals = await (
            await conn.execute(
                "SELECT kind, SUM(quantity)::bigint AS total FROM usage_ledger"
                " WHERE source_id = %s GROUP BY kind",
                (source_id,),
            )
        ).fetchall()
    assert {entry["kind"]: entry["total"] for entry in totals} == {
        "storage_bytes": 210,
        "transcription_seconds": 60,
    }
