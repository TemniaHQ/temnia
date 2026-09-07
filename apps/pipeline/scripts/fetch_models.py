# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Download the substrate's models into the model cache.

    uv run --frozen python scripts/fetch_models.py

The pipeline `Dockerfile` runs this at build time so the deployed worker never
downloads during a request, and a developer or the local gate runs it to fill
`TEMNIA_MODELS_DIR` once per machine.

**Why this file imports nothing from `temnia_pipeline`.** The image copies it in
*before* the source, so the layer that downloads about a gigabyte of weights
depends on the lock file alone and editing Python does not re-download them.
That costs three duplicated constants, and
`tests/test_substrate_models_fetch.py` asserts each of them equals the
package's own, so the duplication cannot drift silently.

Loading the models rather than fetching the repositories is deliberate:
`sat-3l-sm` also publishes ONNX weights and an 18 MB trainer state that a
snapshot download would pull and the worker would never read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Duplicated from temnia_pipeline.settings.DEFAULT_MODELS_DIR,
# substrate.sat.DEFAULT_SAT_MODEL and
# substrate.changepoint.DEFAULT_EMBEDDING_MODEL. See the module docstring.
DEFAULT_MODELS_DIR = "~/.cache/temnia-models"
SAT_MODEL = "sat-3l-sm"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def directory_bytes(root: Path) -> int:
    """Everything under `root`, following no symlinks."""
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    """Load both models once, which downloads exactly the files they read."""
    root = Path(os.environ.get("TEMNIA_MODELS_DIR") or DEFAULT_MODELS_DIR).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(root))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    # skops before transformers: see backends.prime_skops for the whole story.
    # The imports are here rather than at the top so the environment above is
    # set before huggingface_hub reads it, which it does once at import.
    import skops.io  # noqa: F401, PLC0415  # pyright: ignore[reportUnusedImport]
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    from wtpsplit import SaT  # noqa: PLC0415

    SaT(SAT_MODEL)
    SentenceTransformer(EMBEDDING_MODEL)
    megabytes = directory_bytes(root) / 1_000_000
    sys.stdout.write(f"{SAT_MODEL} and {EMBEDDING_MODEL} in {root}: {megabytes:.0f} MB\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
