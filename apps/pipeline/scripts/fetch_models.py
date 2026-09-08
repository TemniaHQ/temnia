# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Fetch immutable substrate snapshots, then prove the libraries load them locally.

Run at image build or setup time. This script imports no pipeline source so
the model layer survives source edits. The duplicated pins/tokenizer/defaults
are compared by tests/test_substrate_models_fetch.py.

snapshot_download supports commit revisions and file filters; both libraries
accept local paths. SaT separately loads its tokenizer, so that gets its own
pin and local path too. The file inventory was verified against the pinned
Hub revisions on 2026-09-08; alternate backend/training weights are excluded.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_MODELS_DIR = "~/.cache/temnia-models"
SAT_MODEL = "sat-3l-sm"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SAT_TOKENIZER = "facebookAI/xlm-roberta-base"
PINNED_REVISIONS = {
    "segment-any-text/sat-3l-sm": "137da054051ad9f1eac42025f758db4ac9f22535",
    "sentence-transformers/all-MiniLM-L6-v2": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    DEFAULT_SAT_TOKENIZER: "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089",
}
MODEL_FILES = {
    "segment-any-text/sat-3l-sm": ("config.json", "model.safetensors"),
    EMBEDDING_MODEL: (
        "config.json",
        "config_sentence_transformers.json",
        "modules.json",
        "sentence_bert_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
        "1_Pooling/config.json",
        "model.safetensors",
    ),
    DEFAULT_SAT_TOKENIZER: (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "sentencepiece.bpe.model",
    ),
}


def configure_model_cache() -> Path:
    """Honor the same effective cache precedence as the runtime loader."""
    root = Path(
        os.environ.get("HF_HOME") or os.environ.get("TEMNIA_MODELS_DIR") or DEFAULT_MODELS_DIR
    )
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(root)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return root


def fetch_snapshots(root: Path) -> dict[str, Path]:
    """Fetch only the files used by the pinned CPU models into one cache."""
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    snapshots: dict[str, Path] = {}
    for repo, revision in PINNED_REVISIONS.items():
        path = Path(
            snapshot_download(
                repo_id=repo,
                revision=revision,
                cache_dir=root / "hub",
                allow_patterns=list(MODEL_FILES[repo]),
            )
        )
        expected = root / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
        if path != expected:
            msg = f"{repo}@{revision} returned {path}, expected the pinned snapshot {expected}"
            raise RuntimeError(msg)
        missing = [name for name in MODEL_FILES[repo] if not (path / name).is_file()]
        if missing:
            msg = f"{repo}@{revision} is incomplete: missing {', '.join(missing)}"
            raise RuntimeError(msg)
        snapshots[repo] = path
    return snapshots


def directory_bytes(root: Path) -> int:
    """Everything under root, following no directory symlinks."""
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    """Fetch exact snapshots, then load weights and tokenizers without network access."""
    root = configure_model_cache()
    snapshots = fetch_snapshots(root)
    # Import skops first, as in substrate.backends.prime_skops.
    import skops.io  # noqa: F401, PLC0415  # pyright: ignore[reportUnusedImport]
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    from wtpsplit import SaT  # noqa: PLC0415

    SaT(
        str(snapshots[f"segment-any-text/{SAT_MODEL}"]),
        tokenizer_name_or_path=str(snapshots[DEFAULT_SAT_TOKENIZER]),
        from_pretrained_kwargs={"local_files_only": True},
    )
    SentenceTransformer(str(snapshots[EMBEDDING_MODEL]), local_files_only=True, device="cpu")
    megabytes = directory_bytes(root) / 1_000_000
    sys.stdout.write(f"{SAT_MODEL} and {EMBEDDING_MODEL} in {root}: {megabytes:.0f} MB\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
