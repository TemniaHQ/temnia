"""Shared wiring for the pipeline's tests.

Two things live here rather than in a module the tests import, because a test
directory without an `__init__.py` is not a package: pytest can put its own
helpers in scope through fixtures, and a helper module would need a path
setting in both pytest and pyright to be importable by name.

- The substrate fixtures. `substrate_name` parametrizes a test over every
  `<name>.transcript.json` under `tests/fixtures/substrate/`, and
  `load_substrate` hands a test the loader when it wants one named fixture.
  `test_substrate_parity.py` keeps its own copy of the loading on purpose: it
  is the byte-parity gate against the frozen oracle, and nothing it depends on
  may move when the new substrate does.
- The `models` marker. A test that loads Segment-any-Text or a sentence
  embedding model downloads about a gigabyte the first time and takes tens of
  seconds; it runs when `TEMNIA_MODEL_TESTS=1` and skips with a reason
  otherwise. `scripts/local-ci.mjs` sets it, so the gate always runs them.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.substrate.grid import ShotGrid

SUBSTRATE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "substrate"
SUBSTRATE_NAMES = sorted(
    path.name[: -len(".transcript.json")]
    for path in SUBSTRATE_FIXTURE_DIR.glob("*.transcript.json")
)

MODEL_TESTS_ENV = "TEMNIA_MODEL_TESTS"
MODEL_SKIP_REASON = f"set {MODEL_TESTS_ENV}=1 to run the tests that load models"

SubstrateFixture = tuple[TranscriptV1, list[int]]


def _load(name: str) -> SubstrateFixture:
    transcript = TranscriptV1.model_validate_json(
        (SUBSTRATE_FIXTURE_DIR / f"{name}.transcript.json").read_text()
    )
    shots_file = SUBSTRATE_FIXTURE_DIR / f"{name}.shots.json"
    shots = (
        ShotGrid.model_validate_json(shots_file.read_text()).snap_times_ms()
        if shots_file.exists()
        else []
    )
    return transcript, shots


def _load_gold(name: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(
        (SUBSTRATE_FIXTURE_DIR / f"{name}.gold.json").read_text()
    )
    return loaded


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "models: loads a real model; runs only when TEMNIA_MODEL_TESTS=1"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get(MODEL_TESTS_ENV) == "1":
        return
    skip = pytest.mark.skip(reason=MODEL_SKIP_REASON)
    for item in items:
        if item.get_closest_marker("models") is not None:
            item.add_marker(skip)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "substrate_name" in metafunc.fixturenames:
        metafunc.parametrize("substrate_name", SUBSTRATE_NAMES)


@pytest.fixture
def load_substrate() -> Callable[[str], SubstrateFixture]:
    """The transcript and decided shot times of one named fixture."""
    return _load


@pytest.fixture
def load_substrate_gold() -> Callable[[str], dict[str, object]]:
    """The reference boundaries of one named fixture."""
    return _load_gold


@pytest.fixture
def substrate(substrate_name: str) -> SubstrateFixture:
    """The fixture the current parametrization names."""
    return _load(substrate_name)
