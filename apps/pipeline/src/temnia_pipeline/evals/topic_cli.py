"""Pure local topic evaluation commands; no execution or provider reconciliation."""

# ruff: noqa: EM101, TC003, TRY003

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from temnia_pipeline.evals.topic_comparison import TopicComparisonManifest, compare_topics
from temnia_pipeline.evals.topic_report import build_topic_report, readable_topic_report
from temnia_pipeline.evals.topic_retained import retained_author_diagnostic
from temnia_pipeline.evals.topics import (
    TopicEvaluationBundle,
    TopicHumanLabels,
    topic_label_template,
    validate_topic_bundle,
    validate_topic_labels,
)
from temnia_pipeline.harness.artifacts import canonical_json


def add_topic_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # pyright: ignore[reportPrivateUsage]
    """Register local reporting commands, with no inference execution option."""
    topics = commands.add_parser("topics", help="offline standalone-topic quality evaluation")
    subcommands = topics.add_subparsers(dest="topic_command", required=True)
    for name in ("validate", "report"):
        command = subcommands.add_parser(
            name, help=f"{name} a frozen topic bundle and human labels"
        )
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--labels", type=Path)
        if name == "report":
            command.add_argument("--output", type=Path, required=True)
    compare = subcommands.add_parser(
        "compare", help="compare manifest-bound source/configuration pairs"
    )
    compare.add_argument("--manifest", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    template = subcommands.add_parser(
        "labels-template", help="freeze candidate identities with unmeasured human judgments"
    )
    template.add_argument("--bundle", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    retained = subcommands.add_parser(
        "retained-author",
        help="prepare an unqualified external-author diagnostic without inference",
    )
    retained.add_argument("--directory", type=Path, required=True)
    retained.add_argument("--preparation-sha256", required=True)
    retained.add_argument("--response-sha256", required=True)
    retained.add_argument("--output", type=Path, required=True)


def private_json(path: Path, value: object, *, protected: tuple[Path, ...] = ()) -> None:
    """Atomically write a private report without overwriting its inputs."""
    if path.resolve() in {item.resolve() for item in protected}:
        raise ValueError("report output cannot replace an input artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def run_topic_command(args: argparse.Namespace) -> int:
    """Validate, report, compare or prepare a receipt diagnostic using only local files."""
    if args.topic_command == "retained-author":
        if args.output.resolve().is_relative_to(args.directory.resolve()):
            raise ValueError("diagnostic output must be outside retained originals")
        bundle = retained_author_diagnostic(
            directory=args.directory,
            preparation_sha256=args.preparation_sha256,
            response_sha256=args.response_sha256,
        )
        private_json(args.output, bundle.model_dump(mode="json", by_alias=True))
        print(  # noqa: T201
            "Prepared unqualified retained-author diagnostic; "
            "zero inference calls and no workflow continuation."
        )
        return 0
    if args.topic_command == "compare":
        manifest = TopicComparisonManifest.model_validate_json(
            args.manifest.read_bytes(), strict=True
        )
        report = compare_topics(manifest, directory=args.manifest.parent)
        protected = (
            args.manifest,
            *(args.manifest.parent / item.bundle for item in manifest.inputs),
            *(args.manifest.parent / item.labels for item in manifest.inputs if item.labels),
        )
        private_json(
            args.output, report.model_dump(mode="json", by_alias=True), protected=protected
        )
        print(  # noqa: T201
            f"Comparable: {report.comparable}; "
            f"production qualification ready: {report.production_qualification_ready}; "
            "no automatic winner."
        )
        return 0
    bundle = TopicEvaluationBundle.model_validate_json(args.bundle.read_bytes(), strict=True)
    if args.topic_command == "labels-template":
        labels = topic_label_template(bundle)
        private_json(
            args.output,
            labels.model_dump(mode="json", by_alias=True),
            protected=(args.bundle,),
        )
        print("Prepared exact-identity human-label template; no judgments have been made.")  # noqa: T201
        return 0
    labels = (
        TopicHumanLabels.model_validate_json(args.labels.read_bytes(), strict=True)
        if args.labels
        else None
    )
    validate_topic_bundle(bundle)
    if labels is not None:
        validate_topic_labels(bundle, labels)
    if args.topic_command == "validate":
        print("Valid topic evaluation inputs; this is not an editorial quality pass.")  # noqa: T201
        return 0
    report = build_topic_report(bundle, labels)
    private_json(
        args.output,
        report.model_dump(mode="json", by_alias=True),
        protected=(args.bundle, *((args.labels,) if args.labels else ())),
    )
    print(readable_topic_report(report))  # noqa: T201
    return 0
