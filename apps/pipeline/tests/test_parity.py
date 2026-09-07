"""The scorer gate: every parity snapshot replays through the ported scorers.

The snapshots were dumped from the legacy's TypeScript mock-mode eval flow and
are frozen here; `uv run temnia-eval verify` replays the same files. Scores must
match bit for bit and issue strings byte for byte: "within rounding" is not the
bar, exact equality is what the port achieves by mirroring operation order.
"""

from pathlib import Path

import pytest

from temnia_pipeline.evals.parity import verify_snapshot

PARITY_DIR = Path(__file__).parent / "parity"
SNAPSHOTS = sorted(PARITY_DIR.glob("*.json"))


def test_snapshots_exist() -> None:
    names = [path.name for path in SNAPSHOTS]
    assert "synthetic.json" in names
    assert "signal-boost-snippet.json" in names


@pytest.mark.parametrize("path", SNAPSHOTS, ids=lambda path: path.name)
def test_snapshot_parity(path: Path) -> None:
    problems = verify_snapshot(path)
    assert problems == []
