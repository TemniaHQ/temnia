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
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.gateway import GatewayConfig
from temnia_pipeline.harness.models import (
    ModelRuntime,
    clear_model_runtime,
    configure_model_runtime,
    harness_pydantic_ai_plugin,
)
from temnia_pipeline.harness.queues import control_task_queue
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_patch_review import TopicEditorialPatchWorkflow
from temnia_pipeline.harness.topic_review import TopicReviewWorkflow
from temnia_pipeline.harness.topic_selection_workflow import (
    TopicSelectionWorkflow,
    TopicSelectionWorkflowV3,
)
from temnia_pipeline.harness.topic_workflow import TopicRunWorkflow
from temnia_pipeline.harness.workflows import ChapterReviewWorkflow, ChapterRunWorkflow
from temnia_pipeline.ingest import Context, Ingest
from temnia_pipeline.reaper import Reaper, ensure_reaper_schedule
from temnia_pipeline.settings import TemporalSettings
from temnia_pipeline.speech.activities_v2 import SpeechActivitiesV2
from temnia_pipeline.speech.assets import SIZE_BYTES, verify_asset
from temnia_pipeline.speech.client import SpeechModalClient, assert_checkpointed_deployment
from temnia_pipeline.transcription.activities import Transcribe
from temnia_pipeline.workflows import (
    HelloWorkflow,
    IngestWorkflow,
    ReaperWorkflow,
    TranscribeWorkflow,
)

log = logging.getLogger("temnia.worker")

# One ladder at a time per worker: the ladder is CPU-bound and two of them
# only halve each other's speed while doubling the scratch disk in use.
MAX_CONCURRENT_ACTIVITIES = 2
MAX_CONCURRENT_CONTROL_ACTIVITIES = 4


async def assert_modal_deployment(ctx: Context) -> None:
    """When anything runs on Modal, prove the deployment before the queue is served.

    A bad token or a Modal app deployed from another commit must be a failed
    deploy, not a source that sits in the queue: Dokploy keeps the previous
    container when this one exits non-zero. One probe covers both functions,
    because they share an app and a contract version, so a half deployed pair
    cannot get past this either.

    The local ladder and the recorded provider have nothing to probe: ffmpeg is
    in the image and a recording is a file.
    """
    settings = ctx.settings
    users = [
        name
        for name, on_modal in (
            ("TRANSCODE_BACKEND", settings.transcode.backend == "modal"),
            ("TRANSCRIPTION_PROVIDER", settings.transcription.provider == "modal"),
        )
        if on_modal
    ]
    if not users:
        return
    from temnia_pipeline.transcode.modal import (  # noqa: PLC0415
        DeploymentError,
        assert_deployment,
    )
    from temnia_pipeline.transcode.modal_client import RealModalClient  # noqa: PLC0415

    named = " and ".join(f"{name}=modal" for name in users)
    try:
        await assert_deployment(RealModalClient(settings.transcode), settings.transcode)
    except DeploymentError as error:
        log.error("%s: %s", named, error)  # noqa: TRY400
        raise SystemExit(1) from error
    log.info("%s, app %s", named, settings.transcode.modal_app)


async def run_worker(settings: TemporalSettings) -> None:
    """Connect, serve the pipeline queue, and drain on SIGTERM or SIGINT."""
    ctx = Context.from_env()
    harness_settings = HarnessSettings.from_env()
    snapshot = harness_settings.validate_boot()
    await db.assert_reachable(ctx.settings.database_url)
    await assert_modal_deployment(ctx)
    if ctx.settings.transcription.provider == "modal-checkpointed":
        with ctx.settings.transcription.speech_vad_model_path.open("rb") as asset:
            verify_asset(asset.read(SIZE_BYTES + 1))
        await asyncio.wait_for(
            assert_checkpointed_deployment(
                SpeechModalClient(ctx.settings.transcription), ctx.settings.transcription
            ),
            timeout=60,
        )
    if snapshot is not None:
        gateway = (
            GatewayConfig(
                api_key=harness_settings.gateway_api_key, gateway=harness_settings.gateway
            )
            if harness_settings.gateway_api_key is not None
            and harness_settings.backend == "gateway"
            else None
        )
        synthetic = harness_settings.backend == "recorded" and harness_settings.allow_recorded
        configure_model_runtime(
            ModelRuntime(
                database_url=ctx.settings.database_url,
                store=ctx.store,
                cassette_store=CassetteStore(
                    ctx.settings.work_root / "harness-cassettes", allow_synthetic=synthetic
                ),
                gateway=gateway,
                allow_synthetic=synthetic,
            )
        )
    client = await Client.connect(
        settings.address,
        namespace=settings.namespace,
        data_converter=pydantic_data_converter,
        # Worker inherits client plugins. Registering it again on Worker would
        # run its transformation twice (Temporal's worker emits a warning).
        plugins=[harness_pydantic_ai_plugin()],
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    ingest = Ingest(ctx)
    reaper = Reaper(ctx)
    transcribe = Transcribe(ctx)
    speech = SpeechActivitiesV2(ctx)
    harness = HarnessActivities(ctx, harness_settings, snapshot)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[
            HelloWorkflow,
            IngestWorkflow,
            ReaperWorkflow,
            TranscribeWorkflow,
            ChapterRunWorkflow,
            ChapterReviewWorkflow,
            TopicRunWorkflow,
            TopicReviewWorkflow,
            TopicSelectionWorkflow,
            TopicSelectionWorkflowV3,
            TopicEditorialPatchWorkflow,
        ],
        activities=[
            say_hello,
            *ingest.activities(),
            *reaper.activities(),
            *transcribe.activities(),
            *speech.activities(),
            *harness.activities(),
        ],
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
    control_worker = Worker(
        client,
        task_queue=control_task_queue(settings.task_queue),
        activities=harness.control_activities(),
        max_concurrent_activities=MAX_CONCURRENT_CONTROL_ACTIVITIES,
    )
    await ensure_reaper_schedule(client, settings.task_queue)
    log.info(
        "worker up: %s ns=%s queue=%s", settings.address, settings.namespace, settings.task_queue
    )
    try:
        async with worker, control_worker:
            await stop.wait()
    finally:
        clear_model_runtime()
        await db.close_pool()
    log.info("worker drained")


def main() -> None:
    """Configure logging and run the worker until signalled."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_worker(TemporalSettings.from_env()))


if __name__ == "__main__":
    main()
