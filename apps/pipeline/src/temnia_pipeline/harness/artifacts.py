"""Immutable, content-addressed harness artifact publication and verified reads."""

# IdentityConflict and SourceDeleting are fixed public API names. Refusal
# messages stay beside the exact invariant each branch checks.
# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import obstore as obs

from temnia_pipeline import db, storage
from temnia_pipeline.harness.ledger import IdentityConflict, SourceDeleting

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from obstore.store import S3Store
    from psycopg import AsyncConnection

    from temnia_pipeline.contracts import Scope

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
SHA256_HEX_LENGTH = 64
CONTENT_SUFFIXES = {
    "application/json": ".json",
    "application/octet-stream": ".bin",
    "application/x-subrip": ".srt",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "text/plain": ".txt",
    "text/vtt": ".vtt",
    "video/mp4": ".mp4",
}
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")


class ArtifactError(RuntimeError):
    """Base class for expected artifact publication failures."""


class ArtifactIntegrityError(ArtifactError):
    """Stored bytes no longer match the accepted artifact row."""


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Inputs that select one immutable artifact version."""

    kind: str
    fingerprint: str
    transcript_id: UUID | None = None
    transcript_revision: int | None = None


@dataclass(frozen=True, slots=True)
class HarnessArtifact:
    """An accepted immutable artifact and its verified storage identity."""

    id: UUID
    organization_id: UUID
    source_id: UUID
    kind: str
    fingerprint: str
    storage_key: str
    sha256: str
    size_bytes: int
    metadata: Mapping[str, Any]
    transcript_id: UUID | None
    transcript_revision: int | None
    dependency_ids: tuple[UUID, ...]


def canonical_json(value: object) -> bytes:
    """Encode sorted UTF-8 JSON while rejecting NaN, infinity, and unknown values."""
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("artifact JSON must be finite canonical JSON") from error


def fingerprint_for(*, inputs: object, config: object, kind: str) -> str:
    """Hash canonical producer inputs separately from the artifact's content hash."""
    identity = {"config": config, "inputs": inputs, "kind": kind}
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def _validate_hash(value: str, name: str) -> None:
    if len(value) != SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _identity_metadata(
    identity: ArtifactIdentity, metadata: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Remove the one legacy run-consumer fact from evidence producer identity."""
    if identity.kind != "evidence" or "runId" not in metadata:
        return metadata
    stable = dict(metadata)
    stable.pop("runId")
    return stable


def _artifact(row: Mapping[str, Any], dependencies: Sequence[UUID]) -> HarnessArtifact:
    return HarnessArtifact(
        id=row["id"],
        organization_id=row["organization_id"],
        source_id=row["source_id"],
        kind=str(row["kind"]),
        fingerprint=str(row["fingerprint"]),
        storage_key=str(row["storage_key"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        metadata=cast("Mapping[str, Any]", row["metadata"]),
        transcript_id=row["transcript_id"],
        transcript_revision=row["transcript_revision"],
        dependency_ids=tuple(dependencies),
    )


def _validate_identity(identity: ArtifactIdentity) -> None:
    _validate_hash(identity.fingerprint, "fingerprint")
    pair = identity.transcript_id is not None, identity.transcript_revision is not None
    if pair[0] != pair[1]:
        raise ValueError("transcript id and revision must be provided together")
    if identity.transcript_revision is not None and identity.transcript_revision <= 0:
        raise ValueError("transcript revision must be positive")
    if identity.kind == "evidence" and not all(pair):
        raise ValueError("evidence artifacts require a transcript id and revision")
    if identity.kind != "evidence" and any(pair):
        raise ValueError("only evidence artifacts may name a transcript revision")


async def _validate_source(database_url: str, scope: Scope, source_id: UUID) -> None:
    """Fence deletion and verify source identity before any object-store operation."""
    async with db.scoped(database_url, scope) as conn:
        row = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (source_id,),
            )
        ).fetchone()
        if row is None:
            raise IdentityConflict("source is absent from the active organization scope")
        if row["deletion_requested_at"] is not None:
            raise SourceDeleting("source is fenced for deletion")


async def _dependencies(
    conn: AsyncConnection[dict[str, Any]], artifact_id: UUID
) -> tuple[UUID, ...]:
    rows = await (
        await conn.execute(
            """
            SELECT input_artifact_id FROM harness_artifact_dependency
             WHERE artifact_id = %s ORDER BY input_artifact_id
            """,
            (artifact_id,),
        )
    ).fetchall()
    return tuple(row["input_artifact_id"] for row in rows)


def _storage_key(  # noqa: PLR0913
    *,
    scope: Scope,
    source_id: UUID,
    identity: ArtifactIdentity,
    sha256: str,
    content_type: str,
    suffix: str | None = None,
) -> str:
    media_type = content_type.partition(";")[0].strip().lower()
    suffix = suffix or CONTENT_SUFFIXES.get(media_type, ".bin")
    return (
        f"org/{scope.organizationId}/source/{source_id}/harness/"
        f"{identity.kind}/{identity.fingerprint}/{sha256}{suffix}"
    )


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def publish_json(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    identity: ArtifactIdentity,
    content: object,
    metadata: Mapping[str, Any],
    dependency_ids: Sequence[UUID] = (),
) -> HarnessArtifact:
    """Canonicalize and publish one bounded JSON artifact."""
    body = canonical_json(content)
    if len(body) > MAX_JSON_BYTES:
        raise ValueError(f"JSON artifact exceeds {MAX_JSON_BYTES} bytes")
    return await publish_bytes(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=identity,
        body=body,
        content_type="application/json",
        metadata=metadata,
        dependency_ids=dependency_ids,
    )


async def publish_bytes(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    identity: ArtifactIdentity,
    body: bytes,
    content_type: str,
    metadata: Mapping[str, Any],
    dependency_ids: Sequence[UUID] = (),
) -> HarnessArtifact:
    """Publish bytes before atomically accepting their immutable identity and lineage."""
    _validate_identity(identity)
    if not content_type:
        raise ValueError("content type must not be empty")
    canonical_metadata = json.loads(canonical_json(dict(metadata)))
    dependencies = tuple(sorted(set(dependency_ids), key=str))
    if len(dependencies) != len(dependency_ids):
        raise ValueError("artifact dependencies must be unique")
    await _validate_source(database_url, scope, source_id)
    sha256 = hashlib.sha256(body).hexdigest()
    key = _storage_key(
        scope=scope,
        source_id=source_id,
        identity=identity,
        sha256=sha256,
        content_type=content_type,
    )
    await storage.upload_bytes(store, key, body, content_type)
    return await _accept_artifact(
        database_url,
        scope=scope,
        source_id=source_id,
        identity=identity,
        key=key,
        sha256=sha256,
        size_bytes=len(body),
        metadata=canonical_metadata,
        dependencies=dependencies,
    )


async def publish_file(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    identity: ArtifactIdentity,
    path: Path,
    content_type: str,
    suffix: str,
    metadata: Mapping[str, Any],
    dependency_ids: Sequence[UUID] = (),
) -> HarnessArtifact:
    """Hash and publish a large artifact without loading it into memory."""
    _validate_identity(identity)
    media_type = content_type.partition(";")[0].strip().lower()
    if not _SAFE_SUFFIX.fullmatch(suffix) or CONTENT_SUFFIXES.get(media_type) != suffix:
        raise ValueError("artifact suffix must match its supported content type")
    if storage.content_type_for(path) != media_type:
        raise ValueError("artifact path extension must match its content type")
    canonical_metadata = json.loads(canonical_json(dict(metadata)))
    dependencies = tuple(sorted(set(dependency_ids), key=str))
    if len(dependencies) != len(dependency_ids):
        raise ValueError("artifact dependencies must be unique")
    await _validate_source(database_url, scope, source_id)
    before = path.stat()
    sha256, size_bytes = await asyncio.to_thread(_hash_file, path)
    key = _storage_key(
        scope=scope,
        source_id=source_id,
        identity=identity,
        sha256=sha256,
        content_type=content_type,
        suffix=suffix,
    )
    uploaded_size = await storage.upload_file(store, key, path)
    after = path.stat()
    if (
        uploaded_size != size_bytes
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ArtifactIntegrityError("artifact file changed while it was being published")
    return await _accept_artifact(
        database_url,
        scope=scope,
        source_id=source_id,
        identity=identity,
        key=key,
        sha256=sha256,
        size_bytes=size_bytes,
        metadata=canonical_metadata,
        dependencies=dependencies,
    )


async def _accept_artifact(  # noqa: C901, PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    identity: ArtifactIdentity,
    key: str,
    sha256: str,
    size_bytes: int,
    metadata: Mapping[str, Any],
    dependencies: tuple[UUID, ...],
) -> HarnessArtifact:
    """Accept one already-persisted object under the source deletion lock."""
    async with db.scoped(database_url, scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (source_id,),
            )
        ).fetchone()
        if source is None:
            raise IdentityConflict("source disappeared before artifact acceptance")
        if source["deletion_requested_at"] is not None:
            raise SourceDeleting("source was fenced for deletion during artifact upload")
        if identity.transcript_id is not None:
            transcript = await (
                await conn.execute(
                    """
                    SELECT 1
                      FROM transcript t
                      JOIN transcript_revision r
                        ON r.transcript_id = t.id AND r.revision = %s
                     WHERE t.id = %s AND t.source_id = %s
                    """,
                    (identity.transcript_revision, identity.transcript_id, source_id),
                )
            ).fetchone()
            if transcript is None:
                raise IdentityConflict("transcript revision is absent from the source scope")
        if dependencies:
            rows = await (
                await conn.execute(
                    """
                    SELECT id FROM harness_artifact
                     WHERE source_id = %s AND id = ANY(%s)
                    """,
                    (source_id, list(dependencies)),
                )
            ).fetchall()
            found = {row["id"] for row in rows}
            if found != set(dependencies):
                raise IdentityConflict("an artifact dependency is absent from the source scope")
        row = await (
            await conn.execute(
                """
                INSERT INTO harness_artifact
                    (organization_id, source_id, kind, fingerprint, storage_key, sha256,
                     size_bytes, metadata, transcript_id, transcript_revision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (organization_id, source_id, kind, fingerprint) DO NOTHING
                RETURNING *
                """,
                (
                    scope.organizationId,
                    source_id,
                    identity.kind,
                    identity.fingerprint,
                    key,
                    sha256,
                    size_bytes,
                    json.dumps(dict(metadata), allow_nan=False),
                    identity.transcript_id,
                    identity.transcript_revision,
                ),
            )
        ).fetchone()
        created = row is not None
        if row is None:
            row = await (
                await conn.execute(
                    """
                    SELECT * FROM harness_artifact
                     WHERE organization_id = %s AND source_id = %s
                       AND kind = %s AND fingerprint = %s
                    """,
                    (scope.organizationId, source_id, identity.kind, identity.fingerprint),
                )
            ).fetchone()
        if row is None:
            raise IdentityConflict("artifact identity disappeared while accepting it")
        if created:
            for dependency_id in dependencies:
                await conn.execute(
                    """
                    INSERT INTO harness_artifact_dependency
                        (organization_id, source_id, artifact_id, input_artifact_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (scope.organizationId, source_id, row["id"], dependency_id),
                )
            await conn.execute(
                """
                INSERT INTO usage_ledger
                    (organization_id, kind, quantity, source_id, detail, idempotency_key)
                VALUES (%s, 'storage_bytes', %s, %s, %s::jsonb, %s)
                ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                """,
                (
                    scope.organizationId,
                    size_bytes,
                    source_id,
                    json.dumps(
                        {"artifactId": str(row["id"]), "category": "harness"},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    f"harness-storage:{row['id']}",
                ),
            )
        accepted_dependencies = await _dependencies(conn, row["id"])
        expected = (
            sha256,
            key,
            size_bytes,
            _identity_metadata(identity, metadata),
            identity.transcript_id,
            identity.transcript_revision,
            dependencies,
        )
        actual = (
            str(row["sha256"]),
            str(row["storage_key"]),
            int(row["size_bytes"]),
            _identity_metadata(identity, cast("Mapping[str, Any]", row["metadata"])),
            row["transcript_id"],
            row["transcript_revision"],
            accepted_dependencies,
        )
        if actual != expected:
            raise IdentityConflict("artifact identity already names different content or lineage")
        return _artifact(row, accepted_dependencies)


async def accept_existing_json(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    identity: ArtifactIdentity,
    key: str,
    sha256: str,
    size_bytes: int,
    metadata: Mapping[str, Any],
    dependency_ids: Sequence[UUID] = (),
    max_bytes: int = MAX_JSON_BYTES,
) -> HarnessArtifact:
    """Verify and accept immutable JSON already persisted by a scoped remote worker.

    The caller validates producer-specific semantics. This boundary verifies
    source scope, exact object bytes, canonical JSON, finite metadata and exact
    dependency lineage before the shared artifact row and storage meter exist.
    """
    _validate_identity(identity)
    _validate_hash(sha256, "sha256")
    if size_bytes < 0 or size_bytes > max_bytes:
        raise ArtifactIntegrityError("remote JSON artifact size is outside the accepted bound")
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    if not key.startswith(prefix):
        raise ArtifactIntegrityError("remote artifact key is outside the active source prefix")
    canonical_metadata = json.loads(canonical_json(dict(metadata)))
    dependencies = tuple(sorted(set(dependency_ids), key=str))
    if len(dependencies) != len(dependency_ids):
        raise ValueError("artifact dependencies must be unique")
    await _validate_source(database_url, scope, source_id)
    try:
        result = await obs.get_async(store, key)
    except FileNotFoundError as error:
        raise ArtifactIntegrityError("remote artifact is missing from storage") from error
    if int(result.meta["size"]) != size_bytes:
        raise ArtifactIntegrityError("remote artifact size differs from its returned reference")
    body = bytes(await result.bytes_async())
    if len(body) != size_bytes or hashlib.sha256(body).hexdigest() != sha256:
        raise ArtifactIntegrityError("remote artifact bytes differ from its returned reference")
    loaded: object = json.loads(body)
    if canonical_json(loaded) != body:
        raise ArtifactIntegrityError("remote JSON artifact is not in canonical form")
    return await _accept_artifact(
        database_url,
        scope=scope,
        source_id=source_id,
        identity=identity,
        key=key,
        sha256=sha256,
        size_bytes=size_bytes,
        metadata=canonical_metadata,
        dependencies=dependencies,
    )


async def find_artifact(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    identity: ArtifactIdentity,
) -> HarnessArtifact | None:
    """Find one scoped immutable identity while honoring the source deletion fence."""
    _validate_identity(identity)
    async with db.scoped(database_url, scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (source_id,),
            )
        ).fetchone()
        if source is None:
            raise IdentityConflict("source is absent from the active organization scope")
        if source["deletion_requested_at"] is not None:
            raise SourceDeleting("source is fenced for deletion")
        row = await (
            await conn.execute(
                """
                SELECT * FROM harness_artifact
                 WHERE organization_id = %s AND source_id = %s
                   AND kind = %s AND fingerprint = %s
                """,
                (
                    scope.organizationId,
                    source_id,
                    identity.kind,
                    identity.fingerprint,
                ),
            )
        ).fetchone()
        if row is None:
            return None
        if (
            row["transcript_id"] != identity.transcript_id
            or row["transcript_revision"] != identity.transcript_revision
        ):
            raise IdentityConflict("artifact identity maps to a different transcript revision")
        return _artifact(row, await _dependencies(conn, row["id"]))


async def _artifact_for_read(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    artifact_id: UUID,
) -> HarnessArtifact:
    async with db.scoped(database_url, scope) as conn:
        source = await (
            await conn.execute(
                "SELECT id, deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (source_id,),
            )
        ).fetchone()
        if source is None:
            raise IdentityConflict("source is absent from the active organization scope")
        if source["deletion_requested_at"] is not None:
            raise SourceDeleting("source is fenced for deletion")
        row = await (
            await conn.execute(
                "SELECT * FROM harness_artifact WHERE id = %s AND source_id = %s",
                (artifact_id, source_id),
            )
        ).fetchone()
        if row is None:
            raise IdentityConflict("artifact is absent from the active source scope")
        return _artifact(row, await _dependencies(conn, artifact_id))


async def read_artifact_bytes(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    artifact_id: UUID,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> bytes:
    """Read bounded bytes and verify both accepted size and SHA-256."""
    artifact = await _artifact_for_read(
        database_url, scope=scope, source_id=source_id, artifact_id=artifact_id
    )
    if artifact.size_bytes > max_bytes:
        raise ValueError(f"accepted artifact exceeds {max_bytes} bytes")
    result = await obs.get_async(store, artifact.storage_key)
    stored_size = int(result.meta["size"])
    if stored_size != artifact.size_bytes:
        raise ArtifactIntegrityError("stored artifact size differs from accepted metadata")
    body = bytes(await result.bytes_async())
    if len(body) != artifact.size_bytes or hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise ArtifactIntegrityError("stored artifact bytes differ from accepted metadata")
    return body


async def read_artifact_json(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    artifact_id: UUID,
    max_bytes: int = MAX_JSON_BYTES,
) -> object:
    """Read and verify one bounded canonical JSON artifact."""
    body = await read_artifact_bytes(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        artifact_id=artifact_id,
        max_bytes=max_bytes,
    )
    loaded: object = json.loads(body)
    if canonical_json(loaded) != body:
        raise ArtifactIntegrityError("stored JSON is not in canonical form")
    return loaded


async def read_artifact_file(  # noqa: PLR0913
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    artifact_id: UUID,
    destination: Path,
) -> int:
    """Stream verified artifact bytes to a file, then atomically expose the result."""
    artifact = await _artifact_for_read(
        database_url, scope=scope, source_id=source_id, artifact_id=artifact_id
    )
    result = await obs.get_async(store, artifact.storage_key)
    if int(result.meta["size"]) != artifact.size_bytes:
        raise ArtifactIntegrityError("stored artifact size differs from accepted metadata")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary_name = handle.name
            async for chunk in result.stream(min_chunk_size=8 * 1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
            raise ArtifactIntegrityError("stored artifact bytes differ from accepted metadata")
        Path(temporary_name).replace(destination)
        temporary_name = None
        return size
    finally:
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary_name).unlink()
