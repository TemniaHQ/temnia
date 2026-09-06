"""Worker entrypoint: `python -m temnia_pipeline.worker`."""

from __future__ import annotations

import asyncio
import logging
import signal

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from temnia_pipeline import db
from temnia_pipeline.activities import say_hello
from temnia_pipeline.ingest import Context, Ingest
from temnia_pipeline.reaper import Reaper, ensure_reaper_schedule
from temnia_pipeline.settings import TemporalSettings
from temnia_pipeline.workflows import HelloWorkflow, IngestWorkflow, ReaperWorkflow

log = logging.getLogger("temnia.worker")

# One ladder at a time per worker: the ladder is CPU-bound and two of them
# only halve each other's speed while doubling the scratch disk in use.
MAX_CONCURRENT_ACTIVITIES = 2


async def assert_transcode_backend(ctx: Context) -> None:
    """On the Modal backend, prove the deployment before the queue is served.

    A bad token or a Modal app deployed from another commit must be a failed
    deploy, not a source that sits in the queue: Dokploy keeps the previous
    container when this one exits non-zero. The local backend has nothing to
    probe; ffmpeg is in the image.
    """
    if ctx.settings.transcode.backend != "modal":
        return
    from temnia_pipeline.transcode.modal import (  # noqa: PLC0415
        DeploymentError,
        assert_deployment,
    )
    from temnia_pipeline.transcode.modal_client import RealModalClient  # noqa: PLC0415

    try:
        await assert_deployment(RealModalClient(ctx.settings.transcode), ctx.settings.transcode)
    except DeploymentError as error:
        log.error("TRANSCODE_BACKEND=modal: %s", error)  # noqa: TRY400
        raise SystemExit(1) from error
    log.info("transcode backend: modal, app %s", ctx.settings.transcode.modal_app)


async def run_worker(settings: TemporalSettings) -> None:
    """Connect, serve the pipeline queue, and drain on SIGTERM or SIGINT."""
    client = await Client.connect(
        settings.address,
        namespace=settings.namespace,
        data_converter=pydantic_data_converter,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    ctx = Context.from_env()
    await db.assert_reachable(ctx.settings.database_url)
    await assert_transcode_backend(ctx)
    ingest = Ingest(ctx)
    reaper = Reaper(ctx)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[HelloWorkflow, IngestWorkflow, ReaperWorkflow],
        activities=[say_hello, *ingest.activities(), *reaper.activities()],
        max_concurrent_activities=MAX_CONCURRENT_ACTIVITIES,
        # The contract models are pydantic; passing pydantic through the sandbox
        # is the documented setup for the pydantic data converter and stops the
        # "imported after initial workflow load" warnings on every worker start.
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "pydantic", "pydantic_core"
            )
        ),
    )
    await ensure_reaper_schedule(client, settings.task_queue)
    log.info(
        "worker up: %s ns=%s queue=%s", settings.address, settings.namespace, settings.task_queue
    )
    try:
        async with worker:
            await stop.wait()
    finally:
        await db.close_pool()
    log.info("worker drained")


def main() -> None:
    """Configure logging and run the worker until signalled."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_worker(TemporalSettings.from_env()))


if __name__ == "__main__":
    main()
