"""Checkpointed speech run settlement against the migrated ledger schema."""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from temnia_pipeline import db
from temnia_pipeline.contracts import TranscribeInput
from temnia_pipeline.harness import ledger
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.speech.activities import SPEECH_RUN_NAMESPACE, settle_speech_run


def pipeline_url() -> str:
    url = os.environ.get("TEST_PIPELINE_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", ""
    ).replace("//temnia:temnia@", "//temnia_pipeline:temnia_pipeline@")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: speech settlement cannot skip silently")
    return url


async def test_speech_run_settles_only_after_three_accepted_stages() -> None:
    url = pipeline_url()
    scope = resolve_scope()
    source_id = uuid.uuid4()
    temporal_run_id = f"speech-settlement-{uuid.uuid4()}"
    run_id = uuid.uuid5(SPEECH_RUN_NAMESPACE, temporal_run_id)
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    request = TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1000,
    )
    async with db.scoped(url, scope) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (scope.organizationId, temporal_run_id),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status)
            VALUES (%s, %s, %s, 'speech', 'speech.m4a', 'audio/mp4', 1, %s, 'ready')
            """,
            (source_id, scope.organizationId, project["id"], f"{prefix}master/speech.m4a"),
        )
        await conn.execute(
            """
            INSERT INTO harness_run
                (id, organization_id, source_id, request_key, lane, budget_micros,
                 workflow_id, workflow_run_id)
            VALUES (%s, %s, %s, %s, 'transcription', 6500000, %s, %s)
            """,
            (run_id, scope.organizationId, source_id, temporal_run_id, "workflow", temporal_run_id),
        )
        for index, stage in enumerate(("recognize", "align", "diarize"), start=1):
            digest = hashlib.sha256(f"{temporal_run_id}:{stage}".encode()).hexdigest()
            artifact = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes)
                    VALUES (%s, %s, 'speech_checkpoint', %s, %s, %s, 2)
                    RETURNING id
                    """,
                    (scope.organizationId, source_id, digest, f"checkpoint/{stage}", digest),
                )
            ).fetchone()
            assert artifact is not None
            await conn.execute(
                """
                INSERT INTO harness_operation
                    (organization_id, source_id, run_id, semantic_key, kind, stage,
                     input_hash, config_hash, status, result_artifact_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'succeeded', %s)
                """,
                (
                    scope.organizationId,
                    source_id,
                    run_id,
                    hashlib.sha256(f"semantic:{digest}".encode()).hexdigest(),
                    stage,
                    stage,
                    hashlib.sha256(f"input:{index}".encode()).hexdigest(),
                    hashlib.sha256(f"config:{index}".encode()).hexdigest(),
                    artifact["id"],
                ),
            )

    try:
        assert await settle_speech_run(
            url,
            request=request,
            temporal_run_id=temporal_run_id,
            outcome="ready",
        )
        assert await settle_speech_run(
            url,
            request=request,
            temporal_run_id=temporal_run_id,
            outcome="ready",
        )
        async with db.scoped(url, scope) as conn:
            settled = await (
                await conn.execute("SELECT status, stage FROM harness_run WHERE id = %s", (run_id,))
            ).fetchone()
        assert settled == {"status": "ready", "stage": "ready"}
        with pytest.raises(ledger.IdentityConflict, match="different outcome"):
            await settle_speech_run(
                url,
                request=request,
                temporal_run_id=temporal_run_id,
                outcome="failed",
            )
    finally:
        await db.close_pool()


async def test_v2_speech_run_requires_three_gpu_stages_and_cpu_assignment() -> None:
    url = pipeline_url()
    scope = resolve_scope()
    source_id = uuid.uuid4()
    temporal_run_id = f"speech-v2-settlement-{uuid.uuid4()}"
    run_id = uuid.uuid5(SPEECH_RUN_NAMESPACE, temporal_run_id)
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    request = TranscribeInput(
        scope=scope,
        sourceId=source_id,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1000,
    )
    async with db.scoped(url, scope) as conn:
        project = await (
            await conn.execute(
                "INSERT INTO project (organization_id, name) VALUES (%s, %s) RETURNING id",
                (scope.organizationId, temporal_run_id),
            )
        ).fetchone()
        assert project is not None
        await conn.execute(
            """
            INSERT INTO source
                (id, organization_id, project_id, title, original_filename, content_type,
                 size_bytes, master_key, status)
            VALUES (%s, %s, %s, 'speech-v2', 'speech.m4a', 'audio/mp4', 1, %s, 'ready')
            """,
            (source_id, scope.organizationId, project["id"], f"{prefix}master/speech.m4a"),
        )
        await conn.execute(
            """
            INSERT INTO harness_run
                (id, organization_id, source_id, request_key, lane, budget_micros,
                 config, workflow_id, workflow_run_id)
            VALUES (%s, %s, %s, %s, 'transcription', 6500000,
                    '{"protocol":"temnia-speech/2"}'::jsonb, %s, %s)
            """,
            (run_id, scope.organizationId, source_id, temporal_run_id, "workflow", temporal_run_id),
        )
        for index, (kind, stage) in enumerate(
            (
                ("recognize", "recognize"),
                ("align", "align"),
                ("diarize", "speaker_turns"),
                ("diarize", "assign_speakers"),
            ),
            start=1,
        ):
            digest = hashlib.sha256(f"{temporal_run_id}:{stage}".encode()).hexdigest()
            artifact = await (
                await conn.execute(
                    """
                    INSERT INTO harness_artifact
                        (organization_id, source_id, kind, fingerprint, storage_key,
                         sha256, size_bytes)
                    VALUES (%s, %s, 'speech_checkpoint', %s, %s, %s, 2)
                    RETURNING id
                    """,
                    (scope.organizationId, source_id, digest, f"checkpoint/{stage}", digest),
                )
            ).fetchone()
            assert artifact is not None
            await conn.execute(
                """
                INSERT INTO harness_operation
                    (organization_id, source_id, run_id, semantic_key, kind, stage,
                     input_hash, config_hash, status, result_artifact_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'succeeded', %s)
                """,
                (
                    scope.organizationId,
                    source_id,
                    run_id,
                    hashlib.sha256(f"semantic:{digest}".encode()).hexdigest(),
                    kind,
                    stage,
                    hashlib.sha256(f"input:{index}".encode()).hexdigest(),
                    hashlib.sha256(f"config:{index}".encode()).hexdigest(),
                    artifact["id"],
                ),
            )

    try:
        assert await settle_speech_run(
            url,
            request=request,
            temporal_run_id=temporal_run_id,
            outcome="ready",
        )
    finally:
        await db.close_pool()
