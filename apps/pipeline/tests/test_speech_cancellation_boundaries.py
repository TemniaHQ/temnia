"""Cancellation fences around the checkpointed provider dispatch boundary."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

from temnia_pipeline.contracts import Scope, TranscribeInput
from temnia_pipeline.harness import ledger
from temnia_pipeline.settings import TranscriptionSettings
from temnia_pipeline.speech import activities as speech_module
from temnia_pipeline.speech.activities import SpeechActivities
from temnia_pipeline.transcription.checkpointed import TranscriptionPlan

SOURCE_ID = UUID("0192e8a0-0000-7000-8000-000000000099")
RUN_ID = UUID("0192e8a0-0000-7000-8000-000000000199")
OPERATION_ID = UUID("0192e8a0-0000-7000-8000-000000000299")
ATTEMPT_ID = UUID("0192e8a0-0000-7000-8000-000000000399")
SCOPE = Scope(
    organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def request() -> TranscribeInput:
    prefix = f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/"
    return TranscribeInput(
        scope=SCOPE,
        sourceId=SOURCE_ID,
        artifactPrefix=prefix,
        audioKey=f"{prefix}audio/audio.m4a",
        durationMs=1000,
    )


def plan() -> TranscriptionPlan:
    return TranscriptionPlan(
        backend="modal-checkpointed",
        app="temnia-speech",
        environment=None,
        protocol="temnia-speech/1",
        build="build",
        budget_micros=6_500_000,
        rate_micros_per_hour=1_250_000,
        stage_timeout_seconds=3600,
        startup_timeout_seconds=120,
        dispatch_limit=5,
        audio_sha256="a" * 64,
        audio_size_bytes=123,
        detector="silero",
        detector_revision="revision",
        detector_sha256="b" * 64,
    )


def attempt(state: str) -> ledger.Attempt:
    return ledger.Attempt(
        id=ATTEMPT_ID,
        operation_id=OPERATION_ID,
        run_id=RUN_ID,
        attempt_number=1,
        owner_token=f"speech-test:{RUN_ID}",
        state=state,
        estimated_cost_micros=1_291_667,
        actual_cost_micros=None,
        cost_status="estimated",
        remote_handle=None,
        dispatched_at=(datetime.now(UTC) if state != "reserved" else None),
        finished_at=None,
    )


def arrange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: object,
    attempts: list[ledger.Attempt],
    mark_dispatched: bool = True,
) -> tuple[SpeechActivities, list[str]]:
    events: list[str] = []
    operation = ledger.Operation(
        id=OPERATION_ID,
        run_id=RUN_ID,
        semantic_key="s" * 64,
        kind="recognize",
        stage="recognize",
        status="pending",
        input_hash="i" * 64,
        config_hash="c" * 64,
        result_artifact_id=None,
    )

    async def progress(*_args: object, **_kwargs: object) -> None:
        return None

    async def acquire(*_args: object, **_kwargs: object) -> ledger.OperationAcquisition:
        return ledger.OperationAcquisition(
            operation=operation,
            created=True,
            accepted=False,
            pending=True,
            uncertain=False,
        )

    async def find(*_args: object, **_kwargs: object) -> None:
        return None

    async def reserve(*_args: object, **_kwargs: object) -> ledger.Attempt:
        return attempts.pop(0)

    async def dispatched(*_args: object, **_kwargs: object) -> bool:
        return mark_dispatched

    async def release(*_args: object, **_kwargs: object) -> bool:
        events.append("released")
        return True

    async def unknown(*_args: object, **kwargs: object) -> ledger.Attempt:
        assert kwargs["outcome_known"] is False
        events.append("unknown")
        return attempt("outcome_unknown")

    async def attached(*_args: object, **_kwargs: object) -> None:
        events.append("attached")

    async def running(*_args: object, **_kwargs: object) -> None:
        events.append("running")

    monkeypatch.setattr(speech_module, "report_speech_progress", progress)
    monkeypatch.setattr(speech_module.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(speech_module.artifacts, "find_artifact", find)
    monkeypatch.setattr(speech_module.ledger, "reserve_attempt", reserve)
    monkeypatch.setattr(speech_module.ledger, "mark_dispatched", dispatched)
    monkeypatch.setattr(speech_module.ledger, "release_undispatched", release)
    monkeypatch.setattr(speech_module.ledger, "fail_attempt", unknown)
    monkeypatch.setattr(speech_module.ledger, "attach_remote_handle", attached)
    monkeypatch.setattr(speech_module.ledger, "mark_running", running)
    settings = SimpleNamespace(
        database_url="unused",
        transcription=TranscriptionSettings(
            provider="modal-checkpointed",
            modal_app="temnia-media",
            modal_environment=None,
            progress_dict="legacy-progress",
            recordings_dir=Path("recordings"),
            recording=None,
        ),
    )
    context = SimpleNamespace(settings=settings, store=None)
    activities = SpeechActivities(cast("Any", context), lambda _settings: cast("Any", client))
    return activities, events


async def test_cancellation_during_build_preflight_releases_reserved_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities, events = arrange(
        monkeypatch,
        client=SimpleNamespace(),
        attempts=[attempt("reserved")],
    )

    async def cancelled(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(speech_module, "assert_frozen_deployment", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await activities._stage(request(), plan(), RUN_ID, plan().recognize, None)  # noqa: SLF001
    assert events == ["released"]


async def test_cancellation_during_spawn_retains_unknown_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        async def spawn(self, *_args: object) -> str:
            raise asyncio.CancelledError

    activities, events = arrange(
        monkeypatch,
        client=Client(),
        attempts=[attempt("reserved"), attempt("dispatching")],
    )

    async def matched(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(speech_module, "assert_frozen_deployment", matched)
    with pytest.raises(asyncio.CancelledError):
        await activities._stage(request(), plan(), RUN_ID, plan().recognize, None)  # noqa: SLF001
    assert events == ["unknown"]


async def test_cancellation_with_known_handle_confirms_provider_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        async def spawn(self, *_args: object) -> str:
            return "known-call"

    activities, events = arrange(
        monkeypatch,
        client=Client(),
        attempts=[attempt("reserved"), attempt("dispatching")],
    )

    async def matched(*_args: object, **_kwargs: object) -> None:
        return None

    async def cancelled_attach(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    async def cancel_and_wait(
        _client: object,
        _lease: object,
        handle: str,
        *,
        return_finished: bool,
    ) -> object:
        assert handle == "known-call"
        assert not return_finished
        events.append("provider-cancelled")
        message = "confirmed"
        raise ApplicationError(message, non_retryable=True, type="SpeechCancelled")

    monkeypatch.setattr(speech_module, "assert_frozen_deployment", matched)
    monkeypatch.setattr(speech_module.ledger, "attach_remote_handle", cancelled_attach)
    monkeypatch.setattr(activities, "_cancel_and_wait", cancel_and_wait)
    with pytest.raises(asyncio.CancelledError):
        await activities._stage(request(), plan(), RUN_ID, plan().recognize, None)  # noqa: SLF001
    assert events == ["provider-cancelled"]
