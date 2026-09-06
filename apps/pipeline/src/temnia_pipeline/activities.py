"""Activities for the S0 hello workflow."""

from __future__ import annotations

import socket

from temporalio import activity

from temnia_pipeline.contracts import HelloInput, HelloOutput


@activity.defn
async def say_hello(request: HelloInput) -> HelloOutput:
    """Greet by name and echo the organization id from the input scope.

    The organization id is copied from the scope the web app resolved; an
    activity never invents one. That is the rule this workflow exists to prove.
    """
    return HelloOutput(
        greeting=f"Hello, {request.name}. Temnia's Python worker is alive.",
        organizationId=request.scope.organizationId,
        workerLanguage="python",
        workerHost=socket.gethostname(),
    )
