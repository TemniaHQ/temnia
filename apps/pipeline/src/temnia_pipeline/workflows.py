"""Workflows served on the pipeline task queue."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.activities import say_hello

    # Temporal resolves the run method's type hints at runtime to deserialise
    # payloads, so these must be real imports, not TYPE_CHECKING ones.
    from temnia_pipeline.contracts import (
        ArtifactRecord,
        HelloInput,
        HelloOutput,
        IngestInput,
        IngestOutput,
        ProbeResult,
    )

# Deterministic failures (bad media, truncated output) are terminal on attempt
# one; everything else (network, disk, a killed worker) retries a few times.
INGEST_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=4,
    non_retryable_error_types=["IngestFailure", "TranscodeFailure", "DeriveFailure"],
)


@workflow.defn(name="HelloWorkflow")
class HelloWorkflow:
    """S0 walking skeleton: Next.js starts it, this worker runs it."""

    @workflow.run
    async def run(self, request: HelloInput) -> HelloOutput:
        """Run the single greeting activity."""
        return await workflow.execute_activity(
            say_hello,
            request,
            start_to_close_timeout=timedelta(seconds=10),
        )


@workflow.defn(name="ReaperWorkflow")
class ReaperWorkflow:
    """One sweep per schedule tick (reaper.py holds the activity)."""

    @workflow.run
    async def run(self) -> int:
        """Run the sweep activity."""
        return await workflow.execute_activity(
            "reap_abandoned_uploads",
            result_type=int,
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn(name="IngestWorkflow")
class IngestWorkflow:
    """Uploaded master -> probed, laddered, derived, finalized source.

    Heartbeat timeouts are how a hard-killed worker is noticed: the ladder
    heartbeats on every progress block, the rest on every step. A failure
    that exhausts retries records itself on the source row before the
    workflow fails, so the row never sits at `processing` with nothing to say.
    """

    @workflow.run
    async def run(self, request: IngestInput) -> IngestOutput:
        """Run the ingest stages in order."""
        started = workflow.now()
        claimed = await workflow.execute_activity(
            "claim_source",
            request,
            result_type=bool,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=INGEST_RETRY,
        )
        if not claimed:
            msg = "the source is not in a claimable state"
            raise ApplicationError(msg, non_retryable=True, type="NotClaimable")
        try:
            probed = await workflow.execute_activity(
                "probe_source",
                request,
                result_type=ProbeResult,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            transcoded = await workflow.execute_activity(
                "transcode_source",
                args=[request, probed],
                result_type=list[ArtifactRecord],
                start_to_close_timeout=timedelta(hours=8),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            derived = await workflow.execute_activity(
                "derive_source",
                args=[request, probed],
                result_type=list[ArtifactRecord],
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
            elapsed = int((workflow.now() - started).total_seconds())
            return await workflow.execute_activity(
                "finalize_source",
                args=[request, probed, [*transcoded, *derived], elapsed],
                result_type=IngestOutput,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=INGEST_RETRY,
            )
        except ActivityError as error:
            cause = error.cause
            message = (
                cause.message
                if isinstance(cause, ApplicationError)
                else "The ingest did not complete. Upload the file again to retry."
            )
            await workflow.execute_activity(
                "fail_source",
                args=[request, message],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            raise
