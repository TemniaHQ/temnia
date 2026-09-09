"""Copy the existing cache read-only into a dedicated bounded benchmark volume.

CPU preparation only; this module has no GPU or source media access. The caller
must supply a fresh benchmark volume name and preserve the returned identity.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import modal

from temnia_pipeline.speech.contracts import canonical_json
from temnia_pipeline.speech.model_files import (
    MAX_MODEL_BYTES,
    MAX_MODEL_FILES,
    file_manifest,
    manifest_identity,
)

VOLUME_NAME = os.environ.get("SPEECH_BENCHMARK_MODEL_VOLUME", "")
if not VOLUME_NAME.startswith("temnia-speech-benchmark-models-"):
    message = "SPEECH_BENCHMARK_MODEL_VOLUME must name a dedicated benchmark volume"
    raise ValueError(message)

app = modal.App("temnia-speech-model-preparation")
source = modal.Volume.from_name("temnia-models")
destination = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install("pydantic==2.13.5")
    .env({"SPEECH_BENCHMARK_MODEL_VOLUME": VOLUME_NAME})
    .add_local_python_source("temnia_pipeline")
)


def _ignore(_directory: str, names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name in {".locks", "token", "stored_tokens", "xet", "__pycache__"}
        or name.endswith(".lock")
    ]


@app.function(  # pyright: ignore[reportUnknownMemberType]
    image=image,
    cpu=(2, 2),
    memory=(2048, 2048),
    timeout=600,
    startup_timeout=120,
    retries=0,
    single_use_containers=True,
    volumes={"/source": source.with_mount_options(read_only=True), "/models": destination},
)
def prepare() -> dict[str, object]:
    """Materialize file bytes, seal their canonical identity, and commit once."""
    target = Path("/models")
    if any(target.iterdir()):
        message = "benchmark destination must be empty; never overwrite an existing snapshot"
        raise ValueError(message)
    scratch = target / "preparing"
    count = 0
    total = 0
    for directory, dirs, names in os.walk("/source"):
        dirs[:] = [name for name in dirs if name not in _ignore(directory, dirs)]
        for name in names:
            if name in _ignore(directory, names):
                continue
            path = Path(directory) / name
            if not path.resolve(strict=True).is_relative_to(Path("/source").resolve(strict=True)):
                message = "model cache link escapes the read-only source volume"
                raise ValueError(message)
            count += 1
            total += path.stat().st_size
            if count > MAX_MODEL_FILES or total > MAX_MODEL_BYTES:
                message = "source model cache exceeds the snapshot bound"
                raise ValueError(message)
    shutil.copytree("/source", scratch, symlinks=False, ignore=_ignore)
    files = file_manifest(scratch)
    identity = manifest_identity(files)
    frozen = Path(identity.model_root)
    frozen.parent.mkdir(parents=True, exist_ok=True)
    scratch.rename(frozen)
    manifests = target / "manifests"
    manifests.mkdir()
    (manifests / f"{identity.sha256}.json").write_bytes(
        canonical_json([item.model_dump() for item in files])
    )
    destination.commit()
    return identity.model_dump(mode="json", by_alias=True)


@app.local_entrypoint()
def main() -> None:
    """Print only the model identity, never source media or credentials."""
    print(json.dumps(prepare.remote(), sort_keys=True))  # noqa: T201
