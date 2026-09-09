"""Bake and identify WhisperX's bundled VAD and sentence tokenizer assets."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from temnia_pipeline.speech.contracts import canonical_json

IMAGE_ASSETS_PATH = Path("/opt/temnia-speech-image-assets.json")
NLTK_DATA_DIR = "/opt/temnia-nltk-data"
MAX_IMAGE_MANIFEST_BYTES = 2 * 1024 * 1024


def _inventory() -> dict[str, object]:
    package = importlib.metadata.distribution("whisperx")
    assets = Path(str(package.locate_file("whisperx/assets")))
    roots = {"whisperx": assets, "punkt": Path(NLTK_DATA_DIR)}
    files: list[dict[str, object]] = []
    for role, root in roots.items():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files.append(  # noqa: PERF401
                    {
                        "role": role,
                        "path": path.relative_to(root).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
    if not any(item["path"] == "pytorch_model.bin" for item in files):
        message = "WhisperX bundled VAD weights are missing"
        raise ValueError(message)
    if not any(item["role"] == "punkt" for item in files):
        message = "offline Punkt tokenizer assets are missing"
        raise ValueError(message)
    libraries = {
        name: importlib.metadata.version(name)
        for name in (
            "whisperx",
            "torch",
            "torchaudio",
            "ctranslate2",
            "faster-whisper",
            "pyannote.audio",
            "transformers",
            "nltk",
            "huggingface-hub",
        )
    }
    return {"schema": "speech-image-models/1", "files": files, "libraries": libraries}


def prepare_image_models() -> None:
    """Fetch tokenizer data during the image build, then seal all auxiliary assets."""
    nltk = cast("Any", import_module("nltk"))

    nltk.download("punkt_tab", download_dir=NLTK_DATA_DIR, raise_on_error=True, quiet=True)
    IMAGE_ASSETS_PATH.write_bytes(canonical_json(_inventory()))


def image_assets_sha256(*, verify_files: bool = False) -> str:
    """Return the baked identity and optionally verify all actual loaded assets."""
    body = IMAGE_ASSETS_PATH.read_bytes()
    if verify_files and canonical_json(_inventory()) != body:
        message = "speech image auxiliary assets differ from their baked identity"
        raise ValueError(message)
    json.loads(body)
    return hashlib.sha256(body).hexdigest()


def read_image_model_manifest() -> dict[str, object]:
    """Expose the bounded file/version evidence behind the deployment's digest."""
    with IMAGE_ASSETS_PATH.open("rb") as handle:
        body = handle.read(MAX_IMAGE_MANIFEST_BYTES + 1)
    if len(body) > MAX_IMAGE_MANIFEST_BYTES:
        message = "speech image asset manifest exceeds its bound"
        raise ValueError(message)
    value = json.loads(body)
    if not isinstance(value, dict):
        message = "speech image asset manifest must be an object"
        raise TypeError(message)
    return cast("dict[str, object]", value)
