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

# Duplicated from substrate.backends.PINNED_REVISIONS, for the same reason. A
# model name resolves to whatever the hub's `main` points at on the day of the
# build; the build fails when that is not the pinned commit, so two clean
# images never carry different weights under one name (S2 review, I15).
PINNED_REVISIONS = {
    "segment-any-text/sat-3l-sm": "137da054051ad9f1eac42025f758db4ac9f22535",
    "sentence-transformers/all-MiniLM-L6-v2": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
}


def resolved_revision(root: Path, repo: str) -> str | None:
    """The commit the hub cache under `root` resolved `repo` to, if it is there."""
    ref = root / "hub" / f"models--{repo.replace('/', '--')}" / "refs" / "main"
    try:
        return ref.read_text().strip() or None
    except OSError:
        return None


def assert_pinned(root: Path, pins: dict[str, str]) -> None:
    """Fail unless every pinned model resolved to its pinned commit."""
    for repo, pinned in pins.items():
        resolved = resolved_revision(root, repo)
        if resolved != pinned:
            msg = (
                f"{repo} resolved to {resolved or 'nothing'} but the pin is {pinned}. The hub's "
                "main moved (or the cache is stale). Re-run the segmentation eval on the "
                "fixtures, then bump PINNED_REVISIONS here and in substrate/backends.py together."
            )
            raise SystemExit(msg)


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
    assert_pinned(root, PINNED_REVISIONS)
    megabytes = directory_bytes(root) / 1_000_000
    sys.stdout.write(f"{SAT_MODEL} and {EMBEDDING_MODEL} in {root}: {megabytes:.0f} MB\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
