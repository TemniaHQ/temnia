"""`temnia-eval segment`: the report that replaces the byte-identical exit test.

The rows that need a model are marked; everything else runs on the `legacy`
segmenter, which needs none. The end-to-end case goes through the installed
console script, because an argument parser that does not wire up is a failure
mode a function-level test cannot see.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import SUBSTRATE_FIXTURE_DIR, SUBSTRATE_NAMES
from temnia_pipeline.evals.segment_report import (
    Gold,
    build_report,
    format_report,
    parse_spec,
    report_dict,
)

TWO_TOPICS = SUBSTRATE_FIXTURE_DIR / "two-topics.transcript.json"
TWO_TOPICS_GOLD = SUBSTRATE_FIXTURE_DIR / "two-topics.gold.json"


def test_a_spec_carries_its_parameters_with_their_types() -> None:
    assert parse_spec("legacy") == ("legacy", {})
    assert parse_spec("changepoint:target_per_hour=12,sentences_from=legacy") == (
        "changepoint",
        {"target_per_hour": 12, "sentences_from": "legacy"},
    )
    assert parse_spec("sat:threshold=0.4,paragraphs=true") == (
        "sat",
        {"threshold": 0.4, "paragraphs": True},
    )
    with pytest.raises(ValueError, match="is not key=value"):
        parse_spec("sat:paragraphs")


def test_gold_reads_both_shapes_and_derives_the_missing_one() -> None:
    gold = Gold.read(TWO_TOPICS_GOLD)
    assert len(gold.boundaries_ms) == 1
    assert len(gold.chapters) == 2
    assert gold.chapters[0][1] == gold.boundaries_ms[0]
    assert gold.titles[1] == "Compound interest and index funds"


def test_chapters_alone_imply_their_interior_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "gold.json"
    path.write_text(
        json.dumps({"chapters": [{"startMs": 0, "endMs": 1000}, {"startMs": 1000, "endMs": 9000}]})
    )
    gold = Gold.read(path)
    assert gold.boundaries_ms == (1000,)
    assert gold.titles == ("", "")


def test_the_report_scores_the_legacy_row_against_gold() -> None:
    report = build_report(TWO_TOPICS, specs=["legacy"], gold_path=TWO_TOPICS_GOLD)
    (row,) = report.rows
    assert row.against_gold is not None
    assert row.segments is not None
    assert report.words == 996
    # The legacy rule proposes a boundary at every speaker turn, so it finds the
    # one true boundary and 38 others: perfect coverage, hopeless purity. This
    # is the density failure the metrics exist to make visible.
    assert row.against_gold.coverage == 1.0
    assert row.against_gold.purity < 0.05
    assert row.per_hour > 100
    assert "Pk" in format_report(report)


def test_a_source_without_gold_still_reports_density_and_agreement() -> None:
    report = build_report(TWO_TOPICS, specs=["legacy", "legacy"])
    assert report.gold is None
    assert all(row.against_gold is None for row in report.rows)
    assert all(row.per_hour > 0 for row in report.rows)
    # Two of the same segmenter agree perfectly, which is the sanity check on
    # the agreement number itself.
    assert report.agreement == (("legacy", "legacy", 1.0),)
    text = format_report(report)
    assert "no gold" in text
    assert "agreement" in text


def test_the_json_report_carries_the_numbers_and_the_provenance() -> None:
    report = build_report(TWO_TOPICS, specs=["legacy"], gold_path=TWO_TOPICS_GOLD)
    data = report_dict(report)
    assert data["version"] == 1
    rows: list[dict[str, Any]] = data["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert row["segmenter"] == "legacy"
    # Every gap in two-topics is under the legacy 2500 ms threshold by
    # construction, so every break there is a turn and none is a pause.
    assert row["kinds"] == ["turn"]
    assert row["provenance"]["versions"]["legacy_oracle"] == "b642b77"
    assert row["boundaryScores"]["coverage"] == 1.0
    assert len(row["segmentScores"]["perThreshold"]) == 10
    # It round-trips, which is what makes it a report a script can read.
    assert json.loads(json.dumps(data)) == data


@pytest.mark.parametrize("name", SUBSTRATE_NAMES)
def test_every_fixture_runs_through_the_command(name: str) -> None:
    """The smoke test the spec asks for: the CLI, on every source, including empty."""
    shots = SUBSTRATE_FIXTURE_DIR / f"{name}.shots.json"
    gold = SUBSTRATE_FIXTURE_DIR / f"{name}.gold.json"
    command = [
        sys.executable,
        "-m",
        "temnia_pipeline.evals.runner",
        "segment",
        str(SUBSTRATE_FIXTURE_DIR / f"{name}.transcript.json"),
    ]
    if shots.exists():
        command += ["--shots", str(shots)]
    if gold.exists():
        command += ["--gold", str(gold)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.startswith(f"── {name}.transcript.json")
    assert "provenance" in result.stdout


def test_the_json_flag_writes_data_the_shell_can_read() -> None:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "temnia_pipeline.evals.runner",
            "segment",
            str(TWO_TOPICS),
            "--gold",
            str(TWO_TOPICS_GOLD),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["source"] == "two-topics.transcript.json"
    assert data["gold"]["boundariesMs"]


@pytest.mark.models
def test_the_change_point_row_finds_the_one_true_boundary() -> None:
    report = build_report(
        TWO_TOPICS,
        specs=["legacy", "changepoint:sentences_from=legacy"],
        gold_path=TWO_TOPICS_GOLD,
    )
    legacy, changepoint = report.rows
    assert legacy.against_gold is not None
    assert changepoint.against_gold is not None
    assert changepoint.against_gold.wf1 == 1.0
    assert changepoint.against_gold.purity == 1.0
    assert changepoint.per_hour < legacy.per_hour / 10
    assert changepoint.segments is not None
    assert changepoint.segments.tiou_f1 == 1.0
