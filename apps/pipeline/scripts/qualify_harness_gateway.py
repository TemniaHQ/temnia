"""Collect private, bounded evidence for Temnia's live gateway request shape."""

# This operator CLI keeps every credential in the process environment.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from temnia_pipeline.harness.qualification import (
    QualificationLimits,
    QualificationRefusal,
    qualification_gateway,
    reconcile_journal,
    run_qualification,
)
from temnia_pipeline.harness.qualification_topic_selection import bind_topic_selection_qualification
from temnia_pipeline.harness.routes import load_route_snapshot

if TYPE_CHECKING:
    from collections.abc import Sequence


def parser() -> argparse.ArgumentParser:
    """Build the finite qualification and read-only reconciliation commands."""
    result = argparse.ArgumentParser(prog="qualify-harness-gateway")
    commands = result.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="make a new create-only qualification session")
    run.add_argument("--candidates", required=True, type=Path)
    run.add_argument(
        "--gateway",
        choices=("vercel", "openrouter"),
        help="must match candidate transport; otherwise derived",
    )
    run.add_argument("--journal", required=True, type=Path)
    run.add_argument("--receipts", required=True, type=Path)
    run.add_argument("--report", required=True, type=Path)
    run.add_argument("--max-exposure-micros", required=True, type=int)
    run.add_argument("--max-dispatches", required=True, type=int)
    run.add_argument("--max-output-tokens", required=True, type=int)
    run.add_argument("--proposal-wire", choices=("canonical", "compact"), default="canonical")
    run.add_argument(
        "--suite", choices=("legacy", "editorial", "topic-selection"), default="legacy"
    )
    run.add_argument("--request-timeout-seconds", type=float, default=300)
    run.add_argument("--lookup-timeout-seconds", type=float, default=10)
    run.add_argument("--lookup-wait-seconds", type=float, default=30)

    reconcile = commands.add_parser(
        "reconcile", help="read known generation costs without dispatching a model"
    )
    reconcile.add_argument("--journal", required=True, type=Path)
    reconcile.add_argument(
        "--gateway",
        choices=("vercel", "openrouter"),
        help="must match the original journal transport",
    )
    reconcile.add_argument("--journal-sha256", required=True)
    reconcile.add_argument("--report", required=True, type=Path)
    bind = commands.add_parser(
        "bind-topics", help="bind exact topic request receipts to a snapshot"
    )
    bind.add_argument("--snapshot", required=True, type=Path)
    bind.add_argument("--reports", required=True, nargs="+", type=Path)
    bind.add_argument("--output", required=True, type=Path)
    bind.add_argument("--max-output-tokens", required=True, type=int)
    bind.add_argument(
        "--bind-transport",
        action="store_true",
        help="explicitly bind version 4 route transport, accounting identity and effective outputs",
    )
    bind.add_argument(
        "--per-route-output",
        action="store_true",
        help="explicitly bind version 3 effective outputs from the run and route ceilings",
    )
    return result


async def _run(args: argparse.Namespace) -> int:
    if args.command == "bind-topics":
        bind_topic_selection_qualification(
            load_route_snapshot(args.snapshot),
            args.reports,
            args.output,
            max_output_tokens=args.max_output_tokens,
            per_route_output=args.per_route_output,
            transport_bound=args.bind_transport,
        )
        return 0
    if not os.environ.get("AI_GATEWAY_API_KEY") and not os.environ.get("OPENROUTER_API_KEY"):
        raise QualificationRefusal("a gateway API key is required in the environment")
    selected_gateway = qualification_gateway(
        args.journal if args.command == "reconcile" else args.candidates,
        journal=args.command == "reconcile",
    )
    if args.gateway is not None and args.gateway != selected_gateway:
        raise QualificationRefusal("selected gateway differs from immutable input transport")
    key_name = "OPENROUTER_API_KEY" if selected_gateway == "openrouter" else "AI_GATEWAY_API_KEY"
    api_key = os.environ.get(key_name)
    if not api_key:
        raise QualificationRefusal("the selected gateway API key is required in the environment")
    if args.command == "reconcile":
        await reconcile_journal(
            journal_path=args.journal,
            expected_sha256=args.journal_sha256,
            api_key=api_key,
            report_path=args.report,
            gateway=selected_gateway,
        )
        return 0
    limits = QualificationLimits(
        max_exposure_micros=args.max_exposure_micros,
        max_dispatches=args.max_dispatches,
        max_output_tokens=args.max_output_tokens,
        proposal_wire=args.proposal_wire,
        suite=args.suite,
        request_timeout_seconds=args.request_timeout_seconds,
        lookup_timeout_seconds=args.lookup_timeout_seconds,
        lookup_wait_seconds=args.lookup_wait_seconds,
    )
    report = await run_qualification(
        candidate_path=args.candidates,
        api_key=api_key,
        journal_path=args.journal,
        receipts_path=args.receipts,
        report_path=args.report,
        limits=limits,
        gateway=selected_gateway,
    )
    return 0 if report.get("status") == "completed" and report.get("passed") is True else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run without accepting or printing a credential."""
    command = parser()
    try:
        return asyncio.run(_run(command.parse_args(argv)))
    except (OSError, ValueError, ValidationError, QualificationRefusal) as error:
        command.exit(
            1,
            "qualify-harness-gateway: input or qualification refused "
            f"({type(error).__name__}); inspect the private report when present\n",
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
