"""`temnia-eval segment`: every segmenter on one source, scored side by side.

This is the exit test for the S2 substrate. The old one was "byte-identical to
the legacy"; the new one is this report, on the recorded sources, with the plan
recording which segmenter S4 starts on and what number would make us switch.

A row is one segmenter. Its hypothesis is its own boundary candidates, which is
why the change-point layer replaces the base's turn and pause candidates rather
than adding to them: a row's boundaries are what it is proposing as cuts.

Every row is scored on the same unit grid, the source's word start times, so
Pk and WindowDiff mean the same thing across rows even though the segmenters
disagree about where sentences are. Without gold the report still carries each
row's density and the pairwise agreement between rows, which is enough to see
that a segmenter proposing three boundaries an hour and one proposing thirty
are different before anyone has judged either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.evals.segmentation import (
    DEFAULT_TOLERANCE_MS,
    BoundaryScores,
    SegmentScores,
    density_per_hour,
    score_boundaries,
    score_segments,
    segments_from_boundaries,
)
from temnia_pipeline.substrate.factory import make_segmenter
from temnia_pipeline.substrate.grid import ShotGrid

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from temnia_pipeline.evals.segmentation import Segment
    from temnia_pipeline.substrate.model import Layers

REPORT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Gold:
    """Reference boundaries for one source, and the chapters they cut."""

    boundaries_ms: tuple[int, ...]
    chapters: tuple[Segment, ...]
    titles: tuple[str, ...]

    @classmethod
    def read(cls, path: Path) -> Gold:
        """`{"boundariesMs": [...], "chapters": [{"startMs", "endMs", "title"?}]}`.

        Both keys are optional: an annotation may know the cut points without
        naming the chapters, and chapters imply their own interior boundaries.
        """
        loaded: dict[str, Any] = json.loads(path.read_text())
        chapters: list[Segment] = []
        titles: list[str] = []
        for chapter in loaded.get("chapters", []):
            chapters.append((int(chapter["startMs"]), int(chapter["endMs"])))
            titles.append(str(chapter.get("title", "")))
        boundaries = [int(ms) for ms in loaded.get("boundariesMs", [])]
        if not boundaries and chapters:
            boundaries = [start for start, _ in chapters[1:]]
        return cls(
            boundaries_ms=tuple(sorted(set(boundaries))),
            chapters=tuple(chapters),
            titles=tuple(titles),
        )


@dataclass(frozen=True, slots=True)
class Row:
    """One segmenter's answer on one source, with its scores."""

    spec: str
    layers: Layers
    boundaries_ms: tuple[int, ...]
    per_hour: float
    against_gold: BoundaryScores | None
    segments: SegmentScores | None


@dataclass(frozen=True, slots=True)
class Report:
    """Every row on one source, plus the agreement between them."""

    source: str
    duration_ms: int
    words: int
    tolerance_ms: int
    gold: Gold | None
    rows: tuple[Row, ...]
    agreement: tuple[tuple[str, str, float], ...]


def parse_spec(spec: str) -> tuple[str, dict[str, object]]:
    """`changepoint:target_per_hour=12,sentences_from=legacy` into a call.

    Values are read as a whole number, then a decimal, then true or false, and
    otherwise as text, so a parameter never silently arrives as the string
    `"12"` where the factory wanted a number.
    """
    name, _, rest = spec.partition(":")
    params: dict[str, object] = {}
    for pair in filter(None, rest.split(",")):
        key, _, raw = pair.partition("=")
        if not raw:
            msg = f"{pair!r} is not key=value"
            raise ValueError(msg)
        params[key.strip()] = _value(raw.strip())
    return name.strip(), params


def _value(raw: str) -> object:
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def build_report(
    transcript_path: Path,
    *,
    specs: Sequence[str],
    shots_path: Path | None = None,
    gold_path: Path | None = None,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> Report:
    """Run every named segmenter on one transcript and score what comes back."""
    transcript = TranscriptV1.model_validate_json(transcript_path.read_text())
    shots = (
        ShotGrid.model_validate_json(shots_path.read_text()).snap_times_ms()
        if shots_path is not None
        else []
    )
    gold = Gold.read(gold_path) if gold_path is not None else None
    units = [word.startMs for word in transcript.words]
    duration = transcript.durationMs

    rows: list[Row] = []
    for spec in specs:
        name, params = parse_spec(spec)
        if name == "chapter-llama":
            params["duration_ms"] = duration
        layers = make_segmenter(name, **params).segment(transcript.words, shot_times_ms=shots)
        boundaries = layers.boundaries_ms()
        against_gold = (
            score_boundaries(
                gold.boundaries_ms,
                boundaries,
                unit_starts_ms=units,
                duration_ms=duration,
                tolerance_ms=tolerance_ms,
            )
            if gold is not None
            else None
        )
        reference_chapters = (
            gold.chapters
            or segments_from_boundaries(gold.boundaries_ms, start_ms=0, end_ms=duration)
            if gold is not None
            else ()
        )
        segments = (
            score_segments(
                reference_chapters,
                segments_from_boundaries(boundaries, start_ms=0, end_ms=duration),
            )
            if reference_chapters
            else None
        )
        rows.append(
            Row(
                spec=spec,
                layers=layers,
                boundaries_ms=boundaries,
                per_hour=density_per_hour(len(boundaries), duration),
                against_gold=against_gold,
                segments=segments,
            )
        )

    agreement: list[tuple[str, str, float]] = []
    for index, one in enumerate(rows):
        for other in rows[index + 1 :]:
            scored = score_boundaries(
                one.boundaries_ms,
                other.boundaries_ms,
                unit_starts_ms=units,
                duration_ms=duration,
                tolerance_ms=tolerance_ms,
            )
            agreement.append((one.spec, other.spec, scored.wf1))

    return Report(
        source=transcript_path.name,
        duration_ms=duration,
        words=len(transcript.words),
        tolerance_ms=tolerance_ms,
        gold=gold,
        rows=tuple(rows),
        agreement=tuple(agreement),
    )


COLUMNS = (
    ("segmenter", 34),
    ("sent", 6),
    ("para", 6),
    ("cand", 6),
    ("per h", 8),
    ("purity", 8),
    ("cover", 8),
    ("wF1", 8),
    ("Pk", 8),
    ("WD", 8),
    ("GHD", 9),
    ("tIoU-F1", 9),
    ("mIoU", 8),
)


def _cell(value: float | None, width: int, digits: int = 3) -> str:
    return ("" if value is None else f"{value:.{digits}f}").rjust(width)


def format_report(report: Report) -> str:
    """The table a human reads, and what a PR description quotes."""
    minutes, seconds = divmod(report.duration_ms // 1000, 60)
    header = (
        f"── {report.source}  {minutes}:{seconds:02d}  {report.words} words"
        f"  tolerance {report.tolerance_ms / 1000:g}s"
    )
    if report.gold is not None:
        header += (
            f"  gold {len(report.gold.boundaries_ms)} boundary"
            f"{'' if len(report.gold.boundaries_ms) == 1 else 'ies'}"
        )
    else:
        header += "  no gold"
    lines = [header, ""]
    lines.append(
        COLUMNS[0][0].ljust(COLUMNS[0][1])
        + "".join(name.rjust(width) for name, width in COLUMNS[1:])
    )
    for row in report.rows:
        gold = row.against_gold
        segments = row.segments
        lines.append(
            row.spec.ljust(COLUMNS[0][1])
            + str(len(row.layers.sentences)).rjust(COLUMNS[1][1])
            + str(len(row.layers.paragraphs)).rjust(COLUMNS[2][1])
            + str(len(row.boundaries_ms)).rjust(COLUMNS[3][1])
            + _cell(row.per_hour, COLUMNS[4][1], 1)
            + _cell(None if gold is None else gold.purity, COLUMNS[5][1])
            + _cell(None if gold is None else gold.coverage, COLUMNS[6][1])
            + _cell(None if gold is None else gold.wf1, COLUMNS[7][1])
            + _cell(None if gold is None else gold.pk, COLUMNS[8][1])
            + _cell(None if gold is None else gold.windowdiff, COLUMNS[9][1])
            + _cell(None if gold is None else gold.ghd, COLUMNS[10][1], 1)
            + _cell(None if segments is None else segments.tiou_f1, COLUMNS[11][1])
            + _cell(None if segments is None else segments.mean_tiou, COLUMNS[12][1])
        )

    if report.agreement:
        lines.extend(["", "agreement (window-tolerant F1 between rows)"])
        lines.extend(f"  {one} vs {other}: {value:.3f}" for one, other, value in report.agreement)

    lines.extend(["", "provenance"])
    for row in report.rows:
        provenance = row.layers.provenance
        parts = [
            *(f"{key}={value}" for key, value in sorted(provenance.models.items())),
            *(f"{key}={value}" for key, value in sorted(provenance.versions.items())),
            *(f"{key}={value}" for key, value in sorted(provenance.params.items())),
        ]
        lines.append(f"  {row.spec}: {'  '.join(parts)}")
    return "\n".join(lines)


def report_dict(report: Report) -> dict[str, Any]:
    """The same report as data, for `--json`."""
    return {
        "version": REPORT_VERSION,
        "source": report.source,
        "durationMs": report.duration_ms,
        "words": report.words,
        "toleranceMs": report.tolerance_ms,
        "gold": None
        if report.gold is None
        else {
            "boundariesMs": list(report.gold.boundaries_ms),
            "chapters": [{"startMs": start, "endMs": end} for start, end in report.gold.chapters],
        },
        "rows": [
            {
                "segmenter": row.spec,
                "sentences": len(row.layers.sentences),
                "paragraphs": len(row.layers.paragraphs),
                "candidates": len(row.layers.candidates),
                "boundariesMs": list(row.boundaries_ms),
                "boundariesPerHour": row.per_hour,
                "kinds": sorted({candidate.kind for candidate in row.layers.candidates}),
                "provenance": {
                    "segmenter": row.layers.provenance.segmenter,
                    "models": dict(row.layers.provenance.models),
                    "params": dict(row.layers.provenance.params),
                    "versions": dict(row.layers.provenance.versions),
                },
                "boundaryScores": None
                if row.against_gold is None
                else {
                    "units": row.against_gold.units,
                    "reference": row.against_gold.reference,
                    "hypothesis": row.against_gold.hypothesis,
                    "matched": row.against_gold.matched,
                    "purity": row.against_gold.purity,
                    "coverage": row.against_gold.coverage,
                    "wf1": row.against_gold.wf1,
                    "pk": row.against_gold.pk,
                    "windowdiff": row.against_gold.windowdiff,
                    "ghd": row.against_gold.ghd,
                    "window": row.against_gold.window,
                    "referencePerHour": row.against_gold.reference_per_hour,
                    "hypothesisPerHour": row.against_gold.hypothesis_per_hour,
                },
                "segmentScores": None
                if row.segments is None
                else {
                    "reference": row.segments.reference,
                    "hypothesis": row.segments.hypothesis,
                    "tiouF1": row.segments.tiou_f1,
                    "meanTiou": row.segments.mean_tiou,
                    "perThreshold": [
                        {"tiou": threshold, "f1": score}
                        for threshold, score in row.segments.per_threshold
                    ],
                },
            }
            for row in report.rows
        ],
        "agreement": [
            {"a": one, "b": other, "wf1": value} for one, other, value in report.agreement
        ],
    }
