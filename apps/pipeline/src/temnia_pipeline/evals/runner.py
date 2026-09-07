"""The eval runner CLI.

Three commands today:

- ``temnia-eval verify [dir]``: replay the cross-language parity snapshots
  through the ported scorers and fail on any mismatch. The gate runs the same
  check through pytest (``tests/test_parity.py``).
- ``temnia-eval score <snapshot.json ...>``: recompute the scorer cases and
  print the PASS/FAIL threshold report the legacy's ``pnpm eval`` printed.
- ``temnia-eval segment <transcript.json>``: run every named segmenter over
  one source and print the segmentation metrics side by side. This is the S2
  substrate's exit test, in place of the byte-identical-to-the-legacy one.

``segment`` defaults to the ``legacy`` segmenter alone, which needs no model.
Naming ``sat`` or ``changepoint`` is opt-in for the same reason
``TRANSCODE_BACKEND`` defaults to ``local``: a missing argument must never
start a download or a GPU.

Live capability invocation (running lanes to produce fresh scorer inputs)
arrives with the harness; the thresholds and the report format are already the
production ones.
"""

import argparse
import json
import sys
from pathlib import Path

from temnia_pipeline.evals.parity import score_case_report, verify_snapshot
from temnia_pipeline.jsnum import to_fixed
from temnia_pipeline.substrate.factory import SEGMENTER_NAMES

# Thresholds ported from scripts/run-evals.ts — the gate levels are part of
# the eval semantics, not presentation.
THRESHOLDS: dict[str, float] = {
    "chapters": 0.7,
    "extraction": 0.8,
    "moments": 0.8,
    "qa": 0.7,
    "segment-boundaries": 0.8,
    "segments": 0.8,
    "speakers": 0.6,
    "summary": 0.4,
}


def _format_threshold(value: float) -> str:
    return f"{value:g}"


def _print_score(name: str, score: float, threshold: float, issues: list[str]) -> bool:
    ok = score >= threshold
    suffix = f" — {'; '.join(issues)}" if issues else ""
    sys.stdout.write(
        f"   {'PASS' if ok else 'FAIL'} {name}: {to_fixed(score, 2)}"
        f" (>= {_format_threshold(threshold)}){suffix}\n"
    )
    return ok


def _score_command(paths: list[str]) -> int:
    failed = 0
    for raw in paths:
        snapshot = json.loads(Path(raw).read_text())
        sys.stdout.write(f"── {snapshot['name']}\n")
        for case in snapshot["cases"]:
            threshold = THRESHOLDS.get(case["scorer"])
            if threshold is None:
                continue
            report = score_case_report(case)
            if not _print_score(case["scorer"], report.score, threshold, report.issues):
                failed += 1
    verdict = "EVALS PASS" if failed == 0 else f"EVALS: {failed} check(s) under threshold"
    sys.stdout.write(f"\n{verdict}\n")
    return 0 if failed == 0 else 1


def _verify_command(directory: str) -> int:
    parity_dir = Path(directory)
    snapshots = sorted(parity_dir.glob("*.json"))
    if not snapshots:
        sys.stderr.write(f"no parity snapshots in {parity_dir}\n")
        return 1
    problems: list[str] = []
    for snapshot in snapshots:
        problems.extend(verify_snapshot(snapshot))
    if problems:
        for problem in problems:
            sys.stderr.write(f"{problem}\n")
        sys.stderr.write(f"\nPARITY: {len(problems)} mismatch(es)\n")
        return 1
    sys.stdout.write(f"PARITY OK across {len(snapshots)} snapshot(s)\n")
    return 0


def _segment_command(args: argparse.Namespace) -> int:
    # Imported here so `verify` and `score` never load nltk or a model library.
    from temnia_pipeline.evals.segment_report import (  # noqa: PLC0415
        build_report,
        format_report,
        report_dict,
    )

    report = build_report(
        Path(args.transcript),
        specs=args.segmenter,
        shots_path=Path(args.shots) if args.shots else None,
        gold_path=Path(args.gold) if args.gold else None,
        tolerance_ms=round(args.tolerance_seconds * 1000),
    )
    if args.json:
        sys.stdout.write(json.dumps(report_dict(report), indent=2) + "\n")
    else:
        sys.stdout.write(format_report(report) + "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="temnia-eval")
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser(
        "verify", help="replay parity snapshots through the ported scorers"
    )
    verify.add_argument(
        "directory",
        nargs="?",
        default=str(Path(__file__).parents[3] / "tests" / "parity"),
    )

    score = commands.add_parser(
        "score", help="print the threshold report for snapshot scorer cases"
    )
    score.add_argument("paths", nargs="+")

    segment = commands.add_parser("segment", help="score every named segmenter on one transcript")
    segment.add_argument("transcript", help="a TranscriptV1 JSON file")
    segment.add_argument("--shots", help="the shot grid the ingest wrote for it")
    segment.add_argument("--gold", help="reference boundaries and chapters")
    segment.add_argument(
        "--segmenter",
        nargs="+",
        default=["legacy"],
        metavar="NAME[:key=value,...]",
        help=(
            "one or more of "
            + ", ".join(SEGMENTER_NAMES)
            + "; parameters follow a colon, for example"
            " changepoint:target_per_hour=12,sentences_from=legacy"
        ),
    )
    segment.add_argument("--tolerance-seconds", type=float, default=15.0)
    segment.add_argument(
        "--json", action="store_true", help="write the report as data instead of a table"
    )

    args = parser.parse_args()
    if args.command == "verify":
        raise SystemExit(_verify_command(args.directory))
    if args.command == "segment":
        raise SystemExit(_segment_command(args))
    raise SystemExit(_score_command(args.paths))


if __name__ == "__main__":  # pragma: no cover - the console script is the entry point
    main()
