"""Workflows served on the pipeline task queue."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline.activities import say_hello

    # Temporal resolves the run method's type hints at runtime to deserialise
    # payloads, so these must be real imports, not TYPE_CHECKING ones.
    from temnia_pipeline.contracts import HelloInput, HelloOutput  # noqa: TC001


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
