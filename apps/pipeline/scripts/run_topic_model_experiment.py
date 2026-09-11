"""Prepare, launch, or inspect controlled v2 workflows using the existing paid runtime."""

# The operator CLI prints machine-readable private state, never credentials or source speech.
# ruff: noqa: T201
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from temnia_pipeline import db
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_experiment import (
    ExperimentSpec,
    TemporalDriver,
    prepare_experiment,
    read_prepared,
    read_status,
    run_execution,
    worker_environment,
    write_prepared,
)
from temnia_pipeline.settings import PipelineSettings, TemporalSettings


def parser() -> argparse.ArgumentParser:
    """Keep report/status commands separate from the only workflow-start command."""
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Freeze qualified arms and ready source pins")
    prepare.add_argument("--spec", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    for name in ("run", "status"):
        command = commands.add_parser(name)
        command.add_argument("--prepared", required=True, type=Path)
        command.add_argument("--arm", required=name == "run")
        command.add_argument("--case", required=name == "run")
    return cli


async def execute(args: argparse.Namespace) -> None:
    """Use existing database/scope and Temporal configuration without infrastructure mutations."""
    database_url = PipelineSettings.from_env().database_url
    if args.command == "prepare":
        # Check before even read-only preparation; O_EXCL still fences concurrent writers.
        if args.output.exists():
            message = "prepared output already exists; reuse it instead of replacing run identities"
            raise FileExistsError(message)
        spec = ExperimentSpec.model_validate_json(args.spec.read_bytes())
        prepared = await prepare_experiment(spec, database_url=database_url, environment=os.environ)
        write_prepared(args.output, prepared)
        print(
            json.dumps(
                {
                    "prepared": str(args.output.resolve()),
                    "sha256": prepared.sha256,
                    "state": "prepared",
                    "workerEnvironments": {
                        arm.spec.id: worker_environment(prepared.spec, arm) for arm in prepared.arms
                    },
                    "next": (
                        "Start the unchanged worker on each declared isolated queue with these "
                        "non-secret overrides and its existing database/storage credentials. "
                        "Verify its image and boot health before running the matching case."
                    ),
                },
                indent=2,
            )
        )
        return
    prepared = read_prepared(args.prepared)
    if args.command == "run":
        temporal = TemporalSettings.from_env()
        driver = await TemporalDriver.connect(temporal)
        result = await run_execution(
            prepared,
            prepared.execution(args.arm, args.case),
            database_url=database_url,
            settings=HarnessSettings.from_env(),
            temporal=temporal,
            driver=driver,
        )
        print(result.model_dump_json(by_alias=True, indent=2))
        return
    driver = await TemporalDriver.connect(
        TemporalSettings(
            prepared.spec.temporal_address,
            prepared.spec.temporal_namespace,
            prepared.arms[0].pipeline_queue,
        )
    )
    selected = [
        execution
        for execution in prepared.executions
        if (args.arm is None or execution.arm_id == args.arm)
        and (args.case is None or execution.case_id == args.case)
    ]
    if not selected:
        message = "unknown experiment arm/source case"
        raise ValueError(message)
    results = [
        await read_status(prepared, execution, database_url=database_url, driver=driver)
        for execution in selected
    ]
    print(
        json.dumps([result.model_dump(mode="json", by_alias=True) for result in results], indent=2)
    )


async def run() -> None:
    """Close existing scoped database pools after any outcome."""
    try:
        await execute(parser().parse_args())
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
