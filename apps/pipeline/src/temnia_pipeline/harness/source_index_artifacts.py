"""Source-bound publication and verified cross-run reuse for editorial indexes."""

# Refusal messages sit beside the producer-identity checks they describe.
# ruff: noqa: EM101, PLR0913, SLF001, TRY003
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from temnia_pipeline.contracts import HarnessArtifactRef, HarnessEvidence, TopicSourceIndex
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.source_index import (
    EMBEDDING_UNIT_MAX_CHARACTERS,
    REGION_KEYWORDS,
    REGION_MAX_CHARACTERS,
    REGION_MAX_SENTENCES,
    REGION_PREVIEW_CHARACTERS,
    ROOT_NODE_ID,
    SECTION_MAX_REGIONS,
    SOURCE_INDEX_FORMAT,
    build_topic_source_index,
    load_topic_source_encoder,
    validate_topic_source_index,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.substrate.backends import (
    PINNED_REVISIONS,
    LoadedModel,
    TextEncoder,
    library_version,
)
from temnia_pipeline.substrate.changepoint import DEFAULT_EMBEDDING_MODEL

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from obstore.store import S3Store

    from temnia_pipeline.contracts import Scope


SOURCE_INDEX_ARTIFACT_KIND = "checks"
SOURCE_INDEX_BUILDER_VERSION = "topic-source-index-builder/2"
DEFAULT_EMBEDDING_REVISION = PINNED_REVISIONS[DEFAULT_EMBEDDING_MODEL]

type EncoderLoader = Callable[[str, str | None], LoadedModel[TextEncoder]]


@dataclass(frozen=True, slots=True)
class SourceIndexArtifactResult:
    """One verified stable artifact and whether it avoided encoder construction."""

    artifact: artifacts.HarnessArtifact
    reused: bool


def source_index_producer_config() -> dict[str, object]:
    """Return every stable producer choice that can change accepted index bytes."""
    return {
        "builderVersion": SOURCE_INDEX_BUILDER_VERSION,
        "format": SOURCE_INDEX_FORMAT,
        "embeddingModel": DEFAULT_EMBEDDING_MODEL,
        "embeddingRevision": DEFAULT_EMBEDDING_REVISION,
        "embeddingUnitMaxCharacters": EMBEDDING_UNIT_MAX_CHARACTERS,
        "regionMaxSentences": REGION_MAX_SENTENCES,
        "regionMaxCharacters": REGION_MAX_CHARACTERS,
        "sectionMaxRegions": SECTION_MAX_REGIONS,
        "rootNodeId": ROOT_NODE_ID,
        "regionKeywordCount": REGION_KEYWORDS,
        "regionPreviewCharacters": REGION_PREVIEW_CHARACTERS,
        "sentenceReadStrategy": "exact-character-fragments/1",
        "pythonVersion": platform.python_version(),
        "numpyVersion": np.__version__,
        "sentenceTransformersVersion": library_version("sentence-transformers"),
        "torchVersion": library_version("torch"),
    }


def _evidence_identity(
    evidence_ref: HarnessArtifactRef, evidence: HarnessEvidence
) -> dict[str, object]:
    return {
        "artifactId": str(evidence_ref.id),
        "artifactKind": evidence_ref.kind.value,
        "artifactFingerprint": evidence_ref.fingerprint,
        "artifactSha256": evidence_ref.sha256,
        "sourceId": str(evidence.sourceId),
        "transcriptId": str(evidence.transcriptId),
        "transcriptRevision": evidence.transcriptRevision,
    }


def source_index_artifact_identity(
    evidence_ref: HarnessArtifactRef, evidence: HarnessEvidence
) -> artifacts.ArtifactIdentity:
    """Name one policy-neutral index from exact source evidence and producer choices."""
    return artifacts.ArtifactIdentity(
        kind=SOURCE_INDEX_ARTIFACT_KIND,
        fingerprint=artifacts.fingerprint_for(
            kind=SOURCE_INDEX_ARTIFACT_KIND,
            inputs={"evidence": _evidence_identity(evidence_ref, evidence)},
            config=source_index_producer_config(),
        ),
    )


def source_index_artifact_metadata(
    evidence_ref: HarnessArtifactRef, evidence: HarnessEvidence
) -> dict[str, object]:
    """Keep immutable producer and evidence lineage visible without a run consumer."""
    return {
        "format": SOURCE_INDEX_FORMAT,
        "producer": source_index_producer_config(),
        "evidence": _evidence_identity(evidence_ref, evidence),
    }


def _reference_fields(reference: HarnessArtifactRef) -> tuple[object, ...]:
    return (
        reference.id,
        reference.kind.value,
        reference.fingerprint,
        reference.sha256,
        reference.sizeBytes,
        reference.storageKey,
    )


def _artifact_fields(accepted: artifacts.HarnessArtifact) -> tuple[object, ...]:
    return (
        accepted.id,
        accepted.kind,
        accepted.fingerprint,
        accepted.sha256,
        accepted.size_bytes,
        accepted.storage_key,
    )


async def _require_evidence_reference(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    evidence_ref: HarnessArtifactRef,
) -> None:
    accepted = await artifacts._artifact_for_read(
        database_url,
        scope=scope,
        source_id=source_id,
        artifact_id=evidence_ref.id,
    )
    if accepted.kind != "evidence" or _artifact_fields(accepted) != _reference_fields(evidence_ref):
        raise HarnessValidationError("source index evidence reference is not the accepted artifact")


def _validate_record(
    accepted: artifacts.HarnessArtifact,
    *,
    evidence_ref: HarnessArtifactRef,
    evidence: HarnessEvidence,
) -> None:
    expected_identity = source_index_artifact_identity(evidence_ref, evidence)
    if (
        accepted.kind != expected_identity.kind
        or accepted.fingerprint != expected_identity.fingerprint
        or dict(accepted.metadata) != source_index_artifact_metadata(evidence_ref, evidence)
        or accepted.transcript_id is not None
        or accepted.transcript_revision is not None
        or accepted.dependency_ids != (evidence_ref.id,)
    ):
        raise HarnessValidationError(
            "source index artifact differs from its reusable producer or evidence lineage"
        )


def _validate_index(
    index: TopicSourceIndex, *, evidence_ref: HarnessArtifactRef, evidence: HarnessEvidence
) -> None:
    validate_topic_source_index(evidence, index, evidence_sha256=evidence_ref.sha256)
    if (
        index.format != SOURCE_INDEX_FORMAT
        or index.embeddingModel != DEFAULT_EMBEDDING_MODEL
        or index.embeddingRevision != DEFAULT_EMBEDDING_REVISION
    ):
        raise HarnessValidationError("source index uses a different immutable encoder identity")


async def load_reusable_topic_source_index(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    evidence_ref: HarnessArtifactRef,
    evidence: HarnessEvidence,
    index_ref: HarnessArtifactRef,
) -> TopicSourceIndex:
    """Read, hash-check and revalidate one exact reusable source index."""
    await _require_evidence_reference(
        database_url,
        scope=scope,
        source_id=source_id,
        evidence_ref=evidence_ref,
    )
    accepted = await artifacts._artifact_for_read(
        database_url,
        scope=scope,
        source_id=source_id,
        artifact_id=index_ref.id,
    )
    if _artifact_fields(accepted) != _reference_fields(index_ref):
        raise HarnessValidationError("source index reference differs from the accepted artifact")
    _validate_record(accepted, evidence_ref=evidence_ref, evidence=evidence)
    body = await artifacts.read_artifact_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        artifact_id=accepted.id,
    )
    index = TopicSourceIndex.model_validate(body)
    _validate_index(index, evidence_ref=evidence_ref, evidence=evidence)
    return index


async def build_or_reuse_topic_source_index(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    evidence_ref: HarnessArtifactRef,
    evidence: HarnessEvidence,
    encoder_loader: EncoderLoader = load_topic_source_encoder,
) -> SourceIndexArtifactResult:
    """Return an exact existing index before loading the encoder, or publish one."""
    await _require_evidence_reference(
        database_url,
        scope=scope,
        source_id=source_id,
        evidence_ref=evidence_ref,
    )
    identity = source_index_artifact_identity(evidence_ref, evidence)
    existing = await artifacts.find_artifact(
        database_url,
        scope=scope,
        source_id=source_id,
        identity=identity,
    )
    if existing is not None:
        reference = HarnessArtifactRef.model_validate(
            {
                "id": existing.id,
                "kind": existing.kind,
                "fingerprint": existing.fingerprint,
                "sha256": existing.sha256,
                "sizeBytes": existing.size_bytes,
                "storageKey": existing.storage_key,
            }
        )
        await load_reusable_topic_source_index(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            evidence_ref=evidence_ref,
            evidence=evidence,
            index_ref=reference,
        )
        return SourceIndexArtifactResult(artifact=existing, reused=True)

    loaded = await asyncio.to_thread(
        encoder_loader, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_REVISION
    )
    if loaded.revision != DEFAULT_EMBEDDING_REVISION:
        raise HarnessValidationError("source-index encoder revision changed before construction")
    index = await asyncio.to_thread(
        build_topic_source_index,
        evidence,
        evidence_sha256=evidence_ref.sha256,
        encoder=loaded.value,
        embedding_revision=loaded.revision,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
    )
    _validate_index(index, evidence_ref=evidence_ref, evidence=evidence)
    accepted = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=identity,
        content=index.model_dump(mode="json"),
        metadata=source_index_artifact_metadata(evidence_ref, evidence),
        dependency_ids=(evidence_ref.id,),
    )
    _validate_record(accepted, evidence_ref=evidence_ref, evidence=evidence)
    return SourceIndexArtifactResult(artifact=accepted, reused=False)
