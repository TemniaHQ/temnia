"""The Modal render function must start in an image without the worker's dependencies.

Run `f7396b5e`'s successor failed on the card with `No module named 'psycopg'`: the render
function imported a harness module that imports the database driver. These tests keep the
render function's imports on a short list and prove that list loads with the worker-only
packages made unimportable.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from temnia_pipeline import modal_app

WORKER_ONLY = ("psycopg", "psycopg_pool", "temporalio", "av", "torch")


def _function_source(name: str) -> str:
    """The body of one Modal function; the SDK wraps it, so read the module text."""
    text = Path(modal_app.__file__).read_text()
    start = text.index(f"async def {name}(")
    end = text.find("\n@app.function", start)
    return text[start : end if end != -1 else len(text)]


def test_render_sections_imports_only_the_probed_modules() -> None:
    source = _function_source("render_sections")
    imported = set(re.findall(r"from (temnia_pipeline\.[a-z_.]+) import", source))
    assert imported, "render_sections imports its helpers at call time"
    assert imported <= set(modal_app.RENDER_MODULES)


def test_the_probed_modules_load_without_worker_dependencies() -> None:
    script = "import sys\n"
    script += "".join(f"sys.modules[{name!r}] = None\n" for name in WORKER_ONLY)
    script += "".join(f"import {name}\n" for name in modal_app.RENDER_MODULES)
    completed = subprocess.run(  # noqa: S603 - our own interpreter on a generated script
        [sys.executable, "-c", script], capture_output=True, text=True, check=False, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
