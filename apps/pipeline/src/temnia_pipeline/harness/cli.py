"""Offline chapter harness validation/reporting and bounded cost reconciliation."""

# CLI parsing and database row validation are kept explicit so refusal paths are visible.
# ruff: noqa: EM101, EM102, PLR0913, TRY003

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import httpx
from pydantic import ValidationError

from temnia_pipeline import db, storage
from temnia_pipeline.contracts import ChapterEditSpec, HarnessEvidence
from temnia_pipeline.evals.chapter_report import build_report, readable_table
from temnia_pipeline.evals.chapters import (
    EvaluationBundle,
    HumanLabels,
    content_sha256,
    validate_bundle,
)
from temnia_pipeline.harness.artifacts import ArtifactError, canonical_json
from temnia_pipeline.harness.bundle_export import export_evaluation_bundle
from temnia_pipeline.harness.gateway import GatewayConfig, lookup_generation
from temnia_pipeline.harness.ledger import reconcile_cost
from temnia_pipeline.harness.routes import RouteEntry, load_route_snapshot
from temnia_pipeline.harness.validators import validate_edit
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import StorageSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

MAX_RECONCILIATION_ATTEMPTS = 128


class ReconciliationSkippedError(RuntimeError):
    """One row cannot safely be looked up, while other rows may continue."""


def _json(path: Path) -> object:
    try:
        return cast("object", json.loads(path.read_bytes()))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path} is absent or invalid JSON") from error


def _bundle(path: Path) -> EvaluationBundle:
    try:
        return EvaluationBundle.model_validate_json(path.read_bytes(), strict=True)
    except OSError as error:
        raise ValueError(f"{path} is absent or invalid JSON") from error


def _validate_command(path: Path, evidence_path: Path) -> str:
    raw = _json(path)
    mapping = cast("dict[str, object]", raw) if isinstance(raw, dict) else None
    if mapping is not None and mapping.get("format") == "temnia-chapter-evaluation-bundle/1":
        bundle = EvaluationBundle.model_validate_json(path.read_bytes(), strict=True)
        validate_bundle(bundle)
        if bundle.evidence is None:
            raise ValueError("bundle has no evidence body to compare with --evidence")
        evidence = HarnessEvidence.model_validate_json(evidence_path.read_bytes(), strict=True)
        if content_sha256(bundle.evidence) != content_sha256(evidence):
            raise ValueError("--evidence differs from the bundle evidence body")
        return "valid evaluation bundle"
    edit = ChapterEditSpec.model_validate_json(path.read_bytes(), strict=True)
    evidence = HarnessEvidence.model_validate_json(evidence_path.read_bytes(), strict=True)
    evidence_sha = content_sha256(evidence)
    validate_edit(evidence, edit, expected_evidence_sha256=evidence_sha)
    return "valid grounded chapter edit"


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json(value) + b"\n"
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def _reconciliation_rows(database_url: str, run_id: UUID) -> list[dict[str, Any]]:
    async with db.scoped(database_url, resolve_scope()) as conn:
        run = await (
            await conn.execute(
                "SELECT id, source_id FROM harness_run WHERE id = %s AND lane = 'chapters'",
                (run_id,),
            )
        ).fetchone()
        if run is None:
            raise ValueError("run is absent from the seeded scope")
        rows = await (
            await conn.execute(
                """
                SELECT a.id, a.operation_id, a.owner_token, a.state, a.cost_status,
                       a.remote_handle, a.result_artifact_id, a.route, a.source_id,
                       o.status AS operation_status, o.result_artifact_id AS operation_artifact_id
                  FROM harness_attempt a
                  JOIN harness_operation o
                    ON o.organization_id = a.organization_id
                   AND o.source_id = a.source_id
                   AND o.id = a.operation_id
                 WHERE a.run_id = %s AND a.cost_status = 'unknown'
                 ORDER BY a.created_at, a.id
                 LIMIT %s
                """,
                (run_id, MAX_RECONCILIATION_ATTEMPTS + 1),
            )
        ).fetchall()
    if len(rows) > MAX_RECONCILIATION_ATTEMPTS:
        raise ValueError("run exceeds the bounded reconciliation attempt limit")
    return [dict(row) for row in rows]


def _eligible_reconciliation(row: dict[str, Any]) -> tuple[RouteEntry, str]:
    remote_handle = row["remote_handle"]
    if not isinstance(remote_handle, str) or not remote_handle:
        raise ReconciliationSkippedError(
            "unknown-cost attempt has no real provider generation handle"
        )
    state = str(row["state"])
    known_terminal = state in {"succeeded", "failed_known", "cancelled_confirmed"}
    recovered_terminal = state == "outcome_unknown" and (
        row["result_artifact_id"] is not None
        and row["result_artifact_id"] == row["operation_artifact_id"]
        and row["operation_status"] == "succeeded"
    )
    if not known_terminal and not recovered_terminal:
        raise ReconciliationSkippedError(
            "unknown execution has no terminal result artifact identity"
        )
    raw_route = row["route"]
    if not isinstance(raw_route, dict):
        raise TypeError("unknown-cost attempt has no frozen route identity")
    route_raw = cast("dict[str, object]", raw_route)
    route = RouteEntry.model_validate_json(canonical_json(route_raw), strict=True)
    return route, remote_handle


async def reconcile_run_costs(
    database_url: str,
    *,
    run_id: UUID,
    api_key: str,
    apply: bool,
    lookup: Callable[..., Awaitable[Any]] = lookup_generation,
    settle: Callable[..., Awaitable[Any]] = reconcile_cost,
) -> list[dict[str, object]]:
    """Look up bounded known handles, optionally applying exact idempotent settlements."""
    rows = await _reconciliation_rows(database_url, run_id)
    config = GatewayConfig(api_key=api_key)
    results: list[dict[str, object]] = []
    async with httpx.AsyncClient() as client:
        for row in rows:
            try:
                route, handle = _eligible_reconciliation(row)
            except ReconciliationSkippedError as error:
                results.append(
                    {
                        "attemptId": str(row["id"]),
                        "generationId": (
                            str(row["remote_handle"]) if row["remote_handle"] is not None else None
                        ),
                        "status": "skipped",
                        "actualCostMicros": None,
                        "applied": False,
                        "components": {"reason": str(error)},
                    }
                )
                continue
            observation = await lookup(
                client,
                config=config,
                route=route,
                generation_id=handle,
            )
            applied = False
            if (
                observation.status == "reported"
                and observation.actual_cost_micros is not None
                and apply
            ):
                await settle(
                    database_url,
                    scope=resolve_scope(),
                    source_id=row["source_id"],
                    run_id=run_id,
                    operation_id=row["operation_id"],
                    attempt_id=row["id"],
                    owner_token=row["owner_token"],
                    observed_cost_micros=observation.actual_cost_micros,
                    usage=observation.components,
                )
                applied = True
            results.append(
                {
                    "attemptId": str(row["id"]),
                    "generationId": handle,
                    "status": observation.status,
                    "actualCostMicros": observation.actual_cost_micros,
                    "applied": applied,
                    "components": observation.components,
                }
            )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="temnia-harness")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a local bundle or edit")
    validate.add_argument("input", type=Path)
    validate.add_argument("--evidence", required=True, type=Path)

    report = commands.add_parser("report", help="write a deterministic chapter report")
    report.add_argument("--bundle", required=True, type=Path)
    report.add_argument("--labels", type=Path)
    report.add_argument("--output", required=True, type=Path)

    export = commands.add_parser(
        "export-bundle", help="export one scoped run for offline evaluation"
    )
    export.add_argument("--run-id", required=True, type=UUID)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument(
        "--split",
        choices=("tuning", "test", "qualification"),
        default="qualification",
    )

    routes = commands.add_parser("routes", help="route snapshot operations")
    route_commands = routes.add_subparsers(dest="route_command", required=True)
    route_validate = route_commands.add_parser("validate", help="validate a frozen route snapshot")
    route_validate.add_argument("snapshot", type=Path)
    route_validate.add_argument("--allow-synthetic", action="store_true")

    reconcile = commands.add_parser("reconcile-cost", help="look up unknown gateway charges")
    reconcile.add_argument("--run-id", required=True, type=UUID)
    reconcile.add_argument("--apply", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "validate":
        print(_validate_command(args.input, args.evidence))  # noqa: T201
        return 0
    if args.command == "report":
        bundle = _bundle(args.bundle)
        labels = (
            HumanLabels.model_validate_json(args.labels.read_bytes(), strict=True)
            if args.labels
            else None
        )
        report = build_report(bundle, labels)
        _atomic_json(args.output, report.model_dump(mode="json", by_alias=True))
        print(readable_table(report))  # noqa: T201
        return 0
    if args.command == "export-bundle":
        database_url = os.environ.get("PIPELINE_DATABASE_URL")
        if not database_url:
            raise ValueError("PIPELINE_DATABASE_URL is required")
        bundle = await export_evaluation_bundle(
            database_url,
            scope=resolve_scope(),
            store=storage.make_store(StorageSettings.from_env()),
            run_id=args.run_id,
            split=args.split,
        )
        _atomic_json(args.output, bundle.model_dump(mode="json", by_alias=True))
        print(  # noqa: T201
            canonical_json(
                {
                    "attempts": len(bundle.attempts),
                    "bundle": str(args.output),
                    "currentRevision": bundle.current_revision,
                    "runId": str(bundle.run_id),
                    "sourceId": str(bundle.source_id),
                }
            ).decode()
        )
        return 0
    if args.command == "routes":
        snapshot = load_route_snapshot(args.snapshot)
        if snapshot.synthetic and not args.allow_synthetic:
            raise ValueError("synthetic route snapshot requires --allow-synthetic")
        print(f"valid route snapshot {snapshot.snapshot_id}")  # noqa: T201
        return 0
    if args.command == "reconcile-cost":
        database_url = os.environ.get("PIPELINE_DATABASE_URL")
        api_key = os.environ.get("AI_GATEWAY_API_KEY")
        if not database_url:
            raise ValueError("PIPELINE_DATABASE_URL is required")
        if not api_key:
            raise ValueError("AI_GATEWAY_API_KEY is required")
        results = await reconcile_run_costs(
            database_url,
            run_id=args.run_id,
            api_key=api_key,
            apply=args.apply,
        )
        print(canonical_json({"applied": args.apply, "results": results}).decode())  # noqa: T201
        return 0
    raise AssertionError("unreachable command")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the harness CLI with stable nonzero validation failures."""
    parser = _parser()
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except (ArtifactError, OSError, ValueError, ValidationError) as error:
        parser.exit(1, f"temnia-harness: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
