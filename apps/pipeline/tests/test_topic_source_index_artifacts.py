"""Source-bound index reuse against migrated Postgres and verified object bytes."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from obstore.store import MemoryStore

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef, HarnessEvidence
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.source_index_artifacts import (
    DEFAULT_EMBEDDING_REVISION,
    build_or_reuse_topic_source_index,
    load_reusable_topic_source_index,
    source_index_artifact_identity,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.substrate.backends import LoadedModel, TextEncoder
from temnia_pipeline.substrate.changepoint import DEFAULT_EMBEDDING_MODEL
from test_harness_persistence import SEEDED, make_case, pipeline_url
from test_topic_compiler import _case
from test_topic_source_index import FixtureEncoder

if TYPE_CHECKING:
    from obstore.store import S3Store


def _reference(accepted: artifacts.HarnessArtifact) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=accepted.id,
        kind=HarnessArtifactKind(accepted.kind),
        fingerprint=accepted.fingerprint,
        sha256=accepted.sha256,
        sizeBytes=accepted.size_bytes,
        storageKey=accepted.storage_key,
    )


async def _evidence_artifact(
    url: str, store: S3Store
) -> tuple[uuid.UUID, HarnessArtifactRef, HarnessEvidence]:
    case = await make_case(url)
    async with db.scoped(url, SEEDED) as conn:
        transcript = await (
            await conn.execute(
                """
                INSERT INTO transcript
                    (organization_id, source_id, status, current_revision)
                VALUES (%s, %s, 'ready', 1)
                RETURNING id
                """,
                (SEEDED.organizationId, case.source_id),
            )
        ).fetchone()
        assert transcript is not None
        transcript_id = transcript["id"]
        await conn.execute(
            """
            INSERT INTO transcript_revision
                (organization_id, transcript_id, revision, kind, storage_key,
                 size_bytes, word_count, metadata)
            VALUES (%s, %s, 1, 'machine', %s, 10, 4, %s::jsonb)
            """,
            (
                SEEDED.organizationId,
                transcript_id,
                f"transcripts/{transcript_id}/1.json",
                '{"sha256":"' + "f" * 64 + '"}',
            ),
        )
    evidence = _case().model_copy(
        update={
            "sourceId": case.source_id,
            "transcriptId": transcript_id,
            "transcriptSha256": "f" * 64,
        }
    )
    accepted = await artifacts.publish_json(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint="e" * 64,
            transcript_id=transcript_id,
            transcript_revision=1,
        ),
        content=evidence.model_dump(mode="json"),
        metadata={"format": "harness-evidence/2"},
    )
    return case.source_id, _reference(accepted), evidence


async def test_exact_source_index_is_reused_before_the_encoder_loads() -> None:
    url = pipeline_url()
    store = cast("S3Store", MemoryStore())
    calls: list[tuple[str, str | None]] = []
    try:
        source_id, evidence_ref, evidence = await _evidence_artifact(url, store)

        def load(model: str, revision: str | None) -> LoadedModel[TextEncoder]:
            calls.append((model, revision))
            return LoadedModel(cast("TextEncoder", FixtureEncoder()), revision or "")

        first = await build_or_reuse_topic_source_index(
            url,
            scope=SEEDED,
            source_id=source_id,
            store=store,
            evidence_ref=evidence_ref,
            evidence=evidence,
            encoder_loader=load,
        )
        second = await build_or_reuse_topic_source_index(
            url,
            scope=SEEDED,
            source_id=source_id,
            store=store,
            evidence_ref=evidence_ref,
            evidence=evidence,
            encoder_loader=load,
        )

        assert first.artifact.id == second.artifact.id
        assert first.reused is False
        assert second.reused is True
        assert calls == [(DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_REVISION)]
        assert "runId" not in first.artifact.metadata
        assert "programVersion" not in first.artifact.metadata
        loaded = await load_reusable_topic_source_index(
            url,
            scope=SEEDED,
            source_id=source_id,
            store=store,
            evidence_ref=evidence_ref,
            evidence=evidence,
            index_ref=_reference(first.artifact),
        )
        assert loaded.evidenceSha256 == evidence_ref.sha256

        async with db.scoped(url, SEEDED) as conn:
            count = await (
                await conn.execute(
                    """
                    SELECT count(*)::int AS count
                      FROM harness_artifact
                     WHERE source_id = %s AND kind = 'checks'
                    """,
                    (source_id,),
                )
            ).fetchone()
        assert count == {"count": 1}
    finally:
        await db.close_pool()


async def test_corrupt_reusable_index_fails_closed_without_loading_encoder() -> None:
    url = pipeline_url()
    store = cast("S3Store", MemoryStore())
    calls = 0
    try:
        source_id, evidence_ref, evidence = await _evidence_artifact(url, store)

        def load(_model: str, revision: str | None) -> LoadedModel[TextEncoder]:
            nonlocal calls
            calls += 1
            return LoadedModel(cast("TextEncoder", FixtureEncoder()), revision or "")

        accepted = await build_or_reuse_topic_source_index(
            url,
            scope=SEEDED,
            source_id=source_id,
            store=store,
            evidence_ref=evidence_ref,
            evidence=evidence,
            encoder_loader=load,
        )
        await storage.upload_bytes(
            store, accepted.artifact.storage_key, b"{}", "application/json"
        )
        with pytest.raises(artifacts.ArtifactIntegrityError):
            await build_or_reuse_topic_source_index(
                url,
                scope=SEEDED,
                source_id=source_id,
                store=store,
                evidence_ref=evidence_ref,
                evidence=evidence,
                encoder_loader=load,
            )
        assert calls == 1
    finally:
        await db.close_pool()


def test_reuse_identity_changes_with_any_evidence_identity_axis() -> None:
    evidence = _case()
    reference = HarnessArtifactRef(
        id=uuid.uuid4(),
        kind=HarnessArtifactKind.evidence,
        fingerprint="a" * 64,
        sha256="b" * 64,
        sizeBytes=1,
        storageKey="tests/evidence.json",
    )
    original = source_index_artifact_identity(reference, evidence).fingerprint

    assert (
        source_index_artifact_identity(
            reference.model_copy(update={"fingerprint": "c" * 64}), evidence
        ).fingerprint
        != original
    )
    assert (
        source_index_artifact_identity(
            reference, evidence.model_copy(update={"transcriptRevision": 2})
        ).fingerprint
        != original
    )
    assert (
        source_index_artifact_identity(
            reference, evidence.model_copy(update={"sourceId": uuid.uuid4()})
        ).fingerprint
        != original
    )


async def test_reuse_refuses_a_reference_whose_accepted_kind_is_not_evidence() -> None:
    url = pipeline_url()
    store = cast("S3Store", MemoryStore())
    try:
        case = await make_case(url)
        evidence = _case().model_copy(update={"sourceId": case.source_id})
        accepted = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=case.source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(kind="proposal", fingerprint="a" * 64),
            content=evidence.model_dump(mode="json"),
            metadata={"format": "not-evidence/1"},
        )
        wrong = _reference(accepted).model_copy(update={"kind": HarnessArtifactKind.evidence})
        with pytest.raises(HarnessValidationError, match="evidence reference"):
            await build_or_reuse_topic_source_index(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                evidence_ref=wrong,
                evidence=evidence,
            )
    finally:
        await db.close_pool()
