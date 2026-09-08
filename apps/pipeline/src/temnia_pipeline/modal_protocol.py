"""Versioned results separate remote execution failures from SDK transport failures."""

from __future__ import annotations

import os
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from temnia_pipeline.modal_build import BUILD_ENV

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CONTRACT_VERSION = "4"


class RemoteSuccess(BaseModel):
    """A completed function returns its payload inside the versioned boundary."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["4"]
    build: str | None = None
    status: Literal["ok"]
    payload: dict[str, Any]


class RemoteFailure(BaseModel):
    """An execution error is data, never an exception mistaken for a lost connection."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["4"]
    build: str | None = None
    status: Literal["failed"]
    error_type: str
    message: str

    @property
    def description(self) -> str:
        """The typed description the worker's retry classifier consumes."""
        return f"{self.error_type}: {self.message}"


def capture_outcome[**P](
    function: Callable[P, Awaitable[dict[str, Any]]],
) -> Callable[P, Awaitable[dict[str, Any]]]:
    """Frame every application exception, including OSError and builtin TimeoutError.

    Cancellation and container termination still belong to Modal. This wraps
    the whole function, including configuration, validation and scratch setup.
    """

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            payload = await function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001
            return RemoteFailure(
                protocol=CONTRACT_VERSION,
                status="failed",
                build=os.environ.get(BUILD_ENV),
                error_type=type(error).__name__,
                message=str(error),
            ).model_dump(mode="json")
        return RemoteSuccess(
            protocol=CONTRACT_VERSION, status="ok", build=os.environ.get(BUILD_ENV), payload=payload
        ).model_dump(mode="json")

    return wrapped


def read_outcome(payload: object) -> RemoteSuccess | RemoteFailure:
    """Refuse unframed old results and malformed outcomes instead of guessing."""
    if isinstance(payload, dict) and cast("dict[str, object]", payload).get("status") == "failed":
        return RemoteFailure.model_validate(payload)
    return RemoteSuccess.model_validate(payload)
