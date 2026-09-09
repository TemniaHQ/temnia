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
    reconcile_journal,
    run_qualification,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def parser() -> argparse.ArgumentParser:
    """Build the finite qualification and read-only reconciliation commands."""
    result = argparse.ArgumentParser(prog="qualify-harness-gateway")
    commands = result.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="make a new create-only qualification session")
    run.add_argument("--candidates", required=True, type=Path)
    run.add_argument("--journal", required=True, type=Path)
    run.add_argument("--receipts", required=True, type=Path)
    run.add_argument("--report", required=True, type=Path)
    run.add_argument("--max-exposure-micros", required=True, type=int)
    run.add_argument("--max-dispatches", required=True, type=int)
    run.add_argument("--max-output-tokens", required=True, type=int)
    run.add_argument("--request-timeout-seconds", type=float, default=300)
    run.add_argument("--lookup-timeout-seconds", type=float, default=10)
    run.add_argument("--lookup-wait-seconds", type=float, default=30)

    reconcile = commands.add_parser(
        "reconcile", help="read known generation costs without dispatching a model"
    )
    reconcile.add_argument("--journal", required=True, type=Path)
    reconcile.add_argument("--journal-sha256", required=True)
    reconcile.add_argument("--report", required=True, type=Path)
    return result


async def _run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        raise QualificationRefusal("AI_GATEWAY_API_KEY is required in the environment")
    if args.command == "reconcile":
        await reconcile_journal(
            journal_path=args.journal,
            expected_sha256=args.journal_sha256,
            api_key=api_key,
            report_path=args.report,
        )
        return 0
    limits = QualificationLimits(
        max_exposure_micros=args.max_exposure_micros,
        max_dispatches=args.max_dispatches,
        max_output_tokens=args.max_output_tokens,
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
