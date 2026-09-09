"""Fingerprint deploy inputs locally and seal that identity into the Modal image."""

from __future__ import annotations

import hashlib
from pathlib import Path

BUILD_ENV = "TEMNIA_MODAL_BUILD"


def source_build_id(pipeline_root: Path | None = None) -> str:
    """Hash all packaged Python and the pipeline dependency/build declarations.

    This is an input fingerprint, not a claim that mutable upstream wheels,
    container tags or model caches are content-pinned. Deployment puts it in
    the image environment so a container never recomputes it from partial files.
    """
    root = pipeline_root or Path(__file__).resolve().parents[2]
    files = [
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "Dockerfile",
            "Dockerfile.speech",
            "THIRD_PARTY_NOTICES.md",
            "LICENSES/NLTK-3.10.3.txt",
        )
        if (root / name).is_file()
    ]
    files.extend(sorted((root / "src" / "temnia_pipeline").rglob("*.py")))
    digest = hashlib.sha256()
    for path in files:
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()
