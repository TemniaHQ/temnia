"""Verify exact offline model files before a metered inference admission."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.speech.contracts import canonical_json
from temnia_pipeline.speech.resources import SpeechModelManifest

MAX_MODEL_FILES = 10_000
MAX_MODEL_BYTES = 20 * 1024**3
MAX_MANIFEST_BYTES = 4 * 1024**2


class ModelFile(BaseModel):
    """One relative regular file in a frozen cache, never a credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def file_manifest(root: Path) -> list[ModelFile]:
    """Hash a bounded tree; reject links and escaping/sensitive paths."""
    base = root.resolve(strict=True)
    result: list[ModelFile] = []
    total = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            message = "frozen model directory must contain regular files, not links"
            raise ValueError(message)
        if not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        if path.name in {"token", "stored_tokens"} or ".locks" in path.parts:
            message = "frozen model directory contains mutable authentication or lock state"
            raise ValueError(message)
        size = path.stat().st_size
        total += size
        if len(result) >= MAX_MODEL_FILES or total > MAX_MODEL_BYTES:
            message = "frozen model directory exceeds its file or byte bound"
            raise ValueError(message)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
        result.append(ModelFile(path=relative, size=size, sha256=digest.hexdigest()))
    if not result or not total:
        message = "frozen model directory is empty"
        raise ValueError(message)
    return result


def manifest_identity(files: list[ModelFile]) -> SpeechModelManifest:
    """Derive the cache address from sorted file bytes and relative names."""
    entries = [item.model_dump() for item in files]
    digest = hashlib.sha256(canonical_json(entries)).hexdigest()
    return SpeechModelManifest(
        sha256=digest,
        file_count=len(files),
        total_bytes=sum(item.size for item in files),
        model_root=f"/models/frozen/{digest}",
    )


def verify_model_files(manifest: SpeechModelManifest, root: Path | None = None) -> None:
    """Refuse missing, extra or changed bytes before using an offline cache."""
    actual = manifest_identity(file_manifest(root or Path(manifest.model_root)))
    if actual != manifest.model_copy(update={"image_assets_sha256": None}):
        message = "offline model files do not match the frozen manifest"
        raise ValueError(message)
