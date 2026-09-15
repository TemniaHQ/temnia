"""Offline chapter harness validation/reporting and bounded cost reconciliation."""

# CLI parsing and database row validation are kept explicit so refusal paths are visible.
# ruff: noqa: EM101, PLR0913, TRY003

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import httpx
from pydantic import ConfigDict, ValidationError
from pydantic_ai.usage import RequestUsage

from temnia_pipeline import db, storage
from temnia_pipeline.harness.artifacts import ArtifactError, canonical_json
from temnia_pipeline.harness.gateway import GatewayConfig, lookup_generation
from temnia_pipeline.harness.ledger import reconcile_cost
from temnia_pipeline.harness.routes import RouteEntry, load_route_snapshot
from temnia_pipeline.scope import resolve_scope
from temnia_pipeline.settings import StorageSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from temnia_pipeline.harness.gateway_policy import GatewayName

MAX_RECONCILIATION_ATTEMPTS = 128


class ReconciliationSkippedError(RuntimeError):
    """One row cannot safely be looked up, while other rows may continue."""


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
    gateway: GatewayName = "vercel",
    lookup: Callable[..., Awaitable[Any]] = lookup_generation,
    settle: Callable[..., Awaitable[Any]] = reconcile_cost,
) -> list[dict[str, object]]:
    """Look up bounded known handles, optionally applying exact idempotent settlements."""
    rows = await _reconciliation_rows(database_url, run_id)
    config = GatewayConfig(api_key=api_key, gateway=gateway)
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

    topic_export = commands.add_parser(
        "export-topic-bundle",
        help="export one scoped standalone-topic artifact closure for offline evaluation",
    )
    topic_export.add_argument("--run-id", required=True, type=UUID)
    topic_export.add_argument("--output", required=True, type=Path)
    topic_export.add_argument("--recording-group")
    topic_export.add_argument(
        "--split", choices=("development", "held_out", "qualification"), default="qualification"
    )

    routes = commands.add_parser("routes", help="route snapshot operations")
    route_commands = routes.add_subparsers(dest="route_command", required=True)
    route_validate = route_commands.add_parser("validate", help="validate a frozen route snapshot")
    route_validate.add_argument("snapshot", type=Path)
    route_validate.add_argument("--allow-synthetic", action="store_true")

    reconcile = commands.add_parser("reconcile-cost", help="look up unknown gateway charges")
    reconcile.add_argument("--run-id", required=True, type=UUID)
    reconcile.add_argument("--apply", action="store_true")

    vendors = commands.add_parser("vendors", help="direct vendor route operations")
    vendor_commands = vendors.add_subparsers(dest="vendor_command", required=True)
    probe = vendor_commands.add_parser(
        "probe",
        help=(
            "send one strict-schema request per route of a direct snapshot with the process "
            "keys; prints the model, usage, settled micros and rate-limit headers"
        ),
    )
    probe.add_argument("snapshot", type=Path)
    probe.add_argument("--route", action="append", default=None, help="only these route ids")
    return parser


USAGE_FIELDS = frozenset(
    {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
)


async def probe_vendors(
    snapshot_path: Path, *, route_ids: list[str] | None
) -> list[dict[str, Any]]:
    """One tiny paid request per route: proves the key, the model id, usage and the headers."""
    from pydantic import BaseModel  # noqa: PLC0415
    from pydantic_ai import Agent, NativeOutput  # noqa: PLC0415

    from temnia_pipeline.harness.vendors import (  # noqa: PLC0415
        VendorKeys,
        build_vendor_model,
        observe_rate_limits,
        settle_usage,
    )

    class Probe(BaseModel):
        model_config = ConfigDict(extra="forbid")
        ok: bool
        vendor: str

    snapshot = load_route_snapshot(snapshot_path)
    if not snapshot.direct:
        raise ValueError("vendor probe needs a direct vendor snapshot")
    keys = VendorKeys.from_env()
    results: list[dict[str, Any]] = []
    for route in snapshot.routes:
        if route_ids and route.id not in route_ids:
            continue
        entry: dict[str, Any] = {"routeId": route.id, "model": route.gateway_model}
        try:
            model = build_vendor_model(route, keys)
            agent: Agent[None, Probe] = Agent(
                model, output_type=NativeOutput(Probe, strict=True), retries=1
            )
            with observe_rate_limits() as readings:
                async with model:
                    result = await agent.run(
                        "Answer with ok=true and vendor set to the company that trained you.",
                        model_settings={"max_tokens": 4096},
                    )
            response = result.all_messages()[-1]
            counted = {
                name: int(value)
                for name, value in dataclasses.asdict(result.usage).items()
                if name in USAGE_FIELDS and isinstance(value, int)
            }
            settled = settle_usage(
                route,
                RequestUsage(
                    input_tokens=counted.get("input_tokens", 0),
                    output_tokens=counted.get("output_tokens", 0),
                    cache_read_tokens=counted.get("cache_read_tokens", 0),
                    cache_write_tokens=counted.get("cache_write_tokens", 0),
                ),
            )
            entry.update(
                {
                    "ok": result.output.ok,
                    "answeredModel": getattr(response, "model_name", None),
                    "usage": counted,
                    "settledMicros": settled.amount_micros if settled is not None else None,
                    "rateLimits": [reading.model_dump(mode="json") for reading in readings],
                }
            )
        except Exception as error:  # noqa: BLE001 - a probe reports, it does not stop
            entry.update({"ok": False, "error": f"{type(error).__name__}: {error}"})
        results.append(entry)
    return results


async def _run(args: argparse.Namespace) -> int:
    if args.command == "export-topic-bundle":
        from temnia_pipeline.harness.topic_bundle_export import export_topic_bundle  # noqa: PLC0415

        database_url = os.environ.get("PIPELINE_DATABASE_URL")
        if not database_url:
            raise ValueError("PIPELINE_DATABASE_URL is required")
        topic_bundle = await export_topic_bundle(
            database_url,
            scope=resolve_scope(),
            store=storage.make_store(StorageSettings.from_env()),
            run_id=args.run_id,
            split=args.split,
            recording_group=args.recording_group,
        )
        _atomic_json(args.output, topic_bundle.model_dump(mode="json", by_alias=True))
        print(  # noqa: T201
            f"Exported topic artifact closure: {len(topic_bundle.artifacts)} artifacts, "
            f"{len(topic_bundle.attempts)} attempts. Human playback remains unmeasured."
        )
        return 0
    if args.command == "routes":
        snapshot = load_route_snapshot(args.snapshot)
        if snapshot.synthetic and not args.allow_synthetic:
            raise ValueError("synthetic route snapshot requires --allow-synthetic")
        print(f"valid route snapshot {snapshot.snapshot_id}")  # noqa: T201
        return 0
    if args.command == "vendors":
        results = await probe_vendors(args.snapshot, route_ids=args.route)
        print(json.dumps({"results": results}, indent=2, default=str))  # noqa: T201
        return 0 if all(item.get("ok") for item in results) else 1
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
