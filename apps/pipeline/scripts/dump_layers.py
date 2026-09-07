"""Dump the new renderer's output for every substrate fixture; `--check` fails on drift.

Only for the `legacy` segmenter. It is the one that needs no model, so its
rendering is deterministic on any machine and is worth committing as bytes: it
is what S4's prompts will quote, and a change to the format should show up as a
diff in a review rather than as a surprise in a prompt. The SaT and
change-point renderings move with their model versions and are asserted by
properties instead (`tests/test_substrate_render.py`).

    uv run --frozen python scripts/dump_layers.py [--check]

Writes `<name>.layers.coarse.txt` and `<name>.layers.fine.txt` beside each
fixture, verbatim and with no trailing newline, so the comparison is against
exactly what the renderer returns.
"""

from __future__ import annotations

import sys
from pathlib import Path

from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.substrate.grid import ShotGrid
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter
from temnia_pipeline.substrate.render import render_coarse, render_fine

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "substrate"
REGENERATE = "uv run --frozen python scripts/dump_layers.py"


def rendered(name: str) -> dict[str, str]:
    """The two renderings for one fixture, keyed by the suffix they are written to."""
    transcript = TranscriptV1.model_validate_json(
        (FIXTURE_DIR / f"{name}.transcript.json").read_text()
    )
    shots_file = FIXTURE_DIR / f"{name}.shots.json"
    shots = (
        ShotGrid.model_validate_json(shots_file.read_text()).snap_times_ms()
        if shots_file.exists()
        else []
    )
    layers = LegacyRulesSegmenter().segment(transcript.words, shot_times_ms=shots)
    return {
        ".layers.coarse.txt": render_coarse(layers),
        ".layers.fine.txt": render_fine(layers, shots),
    }


def names() -> list[str]:
    """Every fixture in the directory; nothing is registered anywhere."""
    return sorted(
        path.name[: -len(".transcript.json")] for path in FIXTURE_DIR.glob("*.transcript.json")
    )


def main() -> int:
    """Write both renderings per fixture, or with `--check` compare them."""
    check = "--check" in sys.argv
    stale: list[str] = []
    for name in names():
        for suffix, text in rendered(name).items():
            target = FIXTURE_DIR / f"{name}{suffix}"
            if check:
                if not target.exists() or target.read_text() != text:
                    stale.append(target.name)
            else:
                target.write_text(text)
    if check:
        if stale:
            sys.stderr.write(f"stale: {', '.join(stale)}; run: {REGENERATE}\n")
            return 1
        sys.stdout.write(f"layer renderings match for {len(names())} fixture(s)\n")
        return 0
    sys.stdout.write(f"dumped {len(names())}: {', '.join(names())}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
