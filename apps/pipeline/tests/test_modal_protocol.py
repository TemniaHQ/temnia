"""The installed SDK transports the boundary; no account or remote call is used."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from temnia_pipeline.modal_build import BUILD_ENV
from temnia_pipeline.modal_protocol import capture_outcome, read_outcome
from temnia_pipeline.settings import TranscodeSettings, TranscriptionSettings
from temnia_pipeline.transcode import modal_client
from temnia_pipeline.transcription import modal_whisperx
from temnia_pipeline.transcription.runner import classify

SDK = cast("Any", import_module("modal._utils.function_utils"))
SERIALIZATION = cast("Any", import_module("modal._serialization"))
PROTO = cast("Any", import_module("modal_proto.api_pb2"))


async def _round_trip(payload: object, *, failed: bool = False) -> Any:  # noqa: ANN401
    result = PROTO.GenericResult(
        status=(
            PROTO.GenericResult.GENERIC_STATUS_FAILURE
            if failed
            else PROTO.GenericResult.GENERIC_STATUS_SUCCESS
        ),
        data=SERIALIZATION.serialize(payload),
    )
    return await SDK._process_result(result, PROTO.DATA_FORMAT_PICKLE, None)  # noqa: SLF001


@pytest.mark.parametrize(
    "error", [OSError("download size mismatch"), TimeoutError("model timed out")]
)
async def test_sdk_serialized_execution_errors_cannot_become_transport_or_running(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real SDK rethrows these with the original Python type. This is the
    # ambiguity the server-side envelope prevents, not a mocked SDK behavior.
    with pytest.raises(type(error), match=str(error)):
        await _round_trip(error, failed=True)

    @capture_outcome
    async def remote_function() -> dict[str, Any]:
        raise error

    monkeypatch.setenv(BUILD_ENV, "gpu-build-test")
    framed = await remote_function()
    assert framed["build"] == "gpu-build-test"

    async def get_result(timeout: float) -> object:  # noqa: ASYNC109
        assert timeout == 0
        return await _round_trip(framed)

    def call(_handle: str) -> SimpleNamespace:
        return SimpleNamespace(get=SimpleNamespace(aio=get_result))

    monkeypatch.setattr(modal_client, "_call", call)
    monkeypatch.setattr(modal_whisperx, "_call", call)
    ladder = await modal_client.RealModalClient(TranscodeSettings.from_env()).status("fc-test")
    speech = await modal_whisperx.ModalWhisperXProvider(TranscriptionSettings.from_env()).status(
        "fc-test"
    )
    assert type(ladder).__name__ == "Failed"
    assert type(speech).__name__ == "Failed"
    assert getattr(ladder, "message", "") == f"{type(error).__name__}: {error}"
    assert getattr(speech, "message", "") == f"{type(error).__name__}: {error}"


@pytest.mark.parametrize(
    ("error", "expected"), [(OSError("network reset"), "Unreachable"), (TimeoutError(), "Running")]
)
async def test_local_sdk_transport_errors_keep_the_unknown_outcome(
    error: Exception,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_result(timeout: float) -> object:  # noqa: ASYNC109
        assert timeout == 0
        raise error

    def call(_handle: str) -> SimpleNamespace:
        return SimpleNamespace(get=SimpleNamespace(aio=get_result))

    monkeypatch.setattr(modal_client, "_call", call)
    monkeypatch.setattr(modal_whisperx, "_call", call)
    ladder = await modal_client.RealModalClient(TranscodeSettings.from_env()).status("fc-test")
    speech = await modal_whisperx.ModalWhisperXProvider(TranscriptionSettings.from_env()).status(
        "fc-test"
    )
    assert type(ladder).__name__ == expected
    assert type(speech).__name__ == expected


async def test_unframed_v3_results_fail_with_explicit_terminal_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_result(timeout: float) -> object:  # noqa: ASYNC109
        assert timeout == 0
        return {"raw": {}, "rawKey": "old", "language": "en", "gpuSeconds": 1, "gpu": "L4"}

    def call(_handle: str) -> SimpleNamespace:
        return SimpleNamespace(get=SimpleNamespace(aio=get_result))

    monkeypatch.setattr(modal_client, "_call", call)
    monkeypatch.setattr(modal_whisperx, "_call", call)
    for adapter in (
        modal_client.RealModalClient(TranscodeSettings.from_env()),
        modal_whisperx.ModalWhisperXProvider(TranscriptionSettings.from_env()),
    ):
        status = await adapter.status("fc-old")
        message = getattr(status, "message", "")
        assert message.startswith("RemoteProtocolError:")
        assert classify(message).non_retryable


@pytest.mark.parametrize("status", ["ok", "failed"])
@pytest.mark.parametrize("invalid", ["protocol", "status", "wrong_version"])
def test_incoming_outcomes_require_explicit_protocol_and_status(status: str, invalid: str) -> None:
    payload: dict[str, object] = {"protocol": "4", "status": status}
    if status == "ok":
        payload["payload"] = {}
    else:
        payload.update({"error_type": "OSError", "message": "bad download"})
    if invalid == "wrong_version":
        payload["protocol"] = "3"
    else:
        payload.pop(invalid)
    with pytest.raises(ValidationError):
        read_outcome(payload)
