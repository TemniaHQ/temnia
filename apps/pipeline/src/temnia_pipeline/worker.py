"""Worker entrypoint: `python -m temnia_pipeline.worker`."""

from __future__ import annotations

import asyncio
import logging
import signal

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from temnia_pipeline.activities import say_hello
from temnia_pipeline.settings import TemporalSettings
from temnia_pipeline.workflows import HelloWorkflow

log = logging.getLogger("temnia.worker")


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

    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[HelloWorkflow],
        activities=[say_hello],
    )
    log.info(
        "worker up: %s ns=%s queue=%s", settings.address, settings.namespace, settings.task_queue
    )
    async with worker:
        await stop.wait()
    log.info("worker drained")


def main() -> None:
    """Configure logging and run the worker until signalled."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_worker(TemporalSettings.from_env()))


if __name__ == "__main__":
    main()
