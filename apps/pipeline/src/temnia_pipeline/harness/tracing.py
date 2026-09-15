"""Logfire tracing for the worker: one trace per run, one span per activity and model call.

Nothing is exported unless `LOGFIRE_TOKEN` is set on the service. With it, the Temporal client
plugin puts every workflow and activity on a trace, and PydanticAI's instrumentation adds a
span per model request with its tokens; the harness's own settlement is on the ledger row.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from temporalio.plugin import SimplePlugin

TOKEN_VARIABLE = "LOGFIRE_TOKEN"  # noqa: S105 - the variable name, not a secret
SERVICE_NAME = "temnia-pipeline"
log = logging.getLogger("temnia.harness.tracing")


def tracing_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether a Logfire token is present in the process environment."""
    values = os.environ if env is None else env
    return bool(values.get(TOKEN_VARIABLE))


def configure_tracing(env: Mapping[str, str] | None = None) -> Any:  # noqa: ANN401
    """Configure Logfire once for this process; returns the instance, or None without a token.

    Prompts and completions are not put on spans (`include_content=False`): the response
    artifact already retains them under the run's scope, and a trace is read by whoever holds
    the Logfire project, not by the organisation that owns the source.
    """
    if not tracing_enabled(env):
        return None
    import logfire  # noqa: PLC0415

    instance = logfire.configure(
        service_name=SERVICE_NAME,
        send_to_logfire="if-token-present",
        console=False,
    )
    instance.instrument_pydantic_ai(include_content=False)
    log.info("tracing to Logfire as %s", SERVICE_NAME)
    return instance


def tracing_plugins(env: Mapping[str, str] | None = None) -> list[SimplePlugin]:
    """The Temporal client plugins that put workflows and activities on the trace."""
    instance = configure_tracing(env)
    if instance is None:
        return []
    from pydantic_ai.durable_exec.temporal import LogfirePlugin  # noqa: PLC0415

    return [LogfirePlugin(lambda: instance, metrics=False)]
