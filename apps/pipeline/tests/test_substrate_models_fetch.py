"""The image's model list is the code's model list.

`scripts/fetch_models.py` imports nothing from the package, so that the layer
that downloads a gigabyte of weights depends on the lock file alone and editing
Python does not re-download them. That costs three duplicated constants. These
assertions are what stops them drifting: a model renamed in the code and not in
the script would otherwise ship an image that downloads at run time on staging,
which is exactly what baking them in is meant to prevent.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from temnia_pipeline.settings import DEFAULT_MODELS_DIR
from temnia_pipeline.substrate.changepoint import DEFAULT_EMBEDDING_MODEL
from temnia_pipeline.substrate.sat import DEFAULT_SAT_MODEL

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_models.py"


def _script() -> ModuleType:
    """Import the script by path; it is not on the package's import path."""
    spec = importlib.util.spec_from_file_location("fetch_models", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_fetch_script_names_the_models_the_code_loads() -> None:
    script = _script()
    assert script.SAT_MODEL == DEFAULT_SAT_MODEL
    assert script.EMBEDDING_MODEL == DEFAULT_EMBEDDING_MODEL
    assert script.DEFAULT_MODELS_DIR == DEFAULT_MODELS_DIR


def test_the_dockerfile_bakes_the_models_in_before_the_source() -> None:
    """The layering is the point; a COPY of src above it would undo it."""
    dockerfile = (SCRIPT.parents[1] / "Dockerfile").read_text()
    fetch = dockerfile.index("scripts/fetch_models.py")
    assert dockerfile.index("COPY src ./src") > fetch
    assert "TEMNIA_MODELS_DIR" in dockerfile
