"""Explicit setup for the pinned Chapter-Llama base, tokenizer and ASR adapter."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
from __future__ import annotations

from huggingface_hub import snapshot_download

from temnia_pipeline.chapter_llama.contracts import (
    ADAPTER_PATH,
    ADAPTER_REPO,
    ADAPTER_REVISION,
    BASE_REPO,
    BASE_REVISION,
)
from temnia_pipeline.chapter_llama.inference import ADAPTER_FILES, BASE_FILES, model_root, snapshot


def main() -> None:
    """Download exact allowlisted files; an ungranted HF gate fails explicitly."""
    root = model_root()
    for repo, revision, files in (
        (BASE_REPO, BASE_REVISION, BASE_FILES),
        (ADAPTER_REPO, ADAPTER_REVISION, tuple(f"{ADAPTER_PATH}/{name}" for name in ADAPTER_FILES)),
    ):
        snapshot_download(
            repo, revision=revision, cache_dir=root / "hub", allow_patterns=list(files)
        )
        snapshot(root, repo, revision, files)


if __name__ == "__main__":
    main()
