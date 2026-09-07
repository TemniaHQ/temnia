"""The substrate parity gate: the port's output is the oracle's output, byte for byte.

Every `<name>.transcript.json` under `tests/fixtures/substrate/` was rendered by
the frozen TypeScript in `tools/legacy-reference/` and the result committed
beside it. This replays each one through the Python port and compares: the two
renderings as bytes, the grid and the display paragraphs as data. `pnpm --filter
@temnia/legacy-reference test` asserts the other half, that the committed files
are what a fresh dump produces, so neither side can drift alone.

The bar is the eval scorers' (legacy DECISIONS #13 and #14): identical, not
close. A rendering is what a model reads, and the ids in it are what a cut is
addressed by.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from pydantic.alias_generators import to_camel

from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.substrate.grid import ShotGrid, build_cut_grid, render_coarse, render_fine
from temnia_pipeline.substrate.paragraphs import build_paragraphs

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "substrate"
NAMES = sorted(
    path.name[: -len(".transcript.json")] for path in FIXTURE_DIR.glob("*.transcript.json")
)


def test_the_fixture_set_is_the_one_the_oracle_dumped() -> None:
    assert NAMES == [
        "empty",
        "signal-boost-snippet",
        "speech-40s",
        "synthetic-edges",
        "two-topics",
    ]


def load(name: str) -> tuple[TranscriptV1, list[int]]:
    """One fixture: the transcript, and the shot times its shot grid decides."""
    transcript = TranscriptV1.model_validate_json(
        (FIXTURE_DIR / f"{name}.transcript.json").read_text()
    )
    shots_file = FIXTURE_DIR / f"{name}.shots.json"
    shots = (
        ShotGrid.model_validate_json(shots_file.read_text()).snap_times_ms()
        if shots_file.exists()
        else []
    )
    return transcript, shots


def wire(value: dict[str, Any]) -> dict[str, Any]:
    """Snake_case field names to the camelCase the committed JSON carries."""
    return {to_camel(key): item for key, item in value.items()}


@pytest.mark.parametrize("name", NAMES)
def test_a_fixture_is_a_contract_transcript(name: str) -> None:
    transcript, _ = load(name)
    starts = [word.startMs for word in transcript.words]
    assert starts == sorted(starts), "words must ride in ascending start order"


@pytest.mark.parametrize("name", NAMES)
def test_the_coarse_rendering_is_byte_identical(name: str) -> None:
    transcript, _ = load(name)
    expected = (FIXTURE_DIR / f"{name}.coarse.txt").read_bytes()
    assert render_coarse(build_cut_grid(transcript.words)).encode() == expected


@pytest.mark.parametrize("name", NAMES)
def test_the_fine_rendering_is_byte_identical(name: str) -> None:
    transcript, shots = load(name)
    expected = (FIXTURE_DIR / f"{name}.fine.txt").read_bytes()
    assert render_fine(build_cut_grid(transcript.words), shots).encode() == expected


@pytest.mark.parametrize("name", NAMES)
def test_the_grid_matches(name: str) -> None:
    transcript, _ = load(name)
    grid = build_cut_grid(transcript.words)
    expected = json.loads((FIXTURE_DIR / f"{name}.grid.json").read_text())
    assert {
        "paragraphs": [wire(asdict(paragraph)) for paragraph in grid.paragraphs],
        "sentences": [wire(asdict(sentence)) for sentence in grid.sentences],
    } == expected


@pytest.mark.parametrize("name", NAMES)
def test_the_display_paragraphs_match(name: str) -> None:
    transcript, _ = load(name)
    expected = json.loads((FIXTURE_DIR / f"{name}.paragraphs.json").read_text())
    got = [
        {
            "endMs": paragraph.end_ms,
            "speaker": paragraph.speaker,
            "startMs": paragraph.start_ms,
            "wordCount": len(paragraph.words),
            "wordOffset": paragraph.word_offset,
        }
        for paragraph in build_paragraphs(transcript.words)
    ]
    assert got == expected
