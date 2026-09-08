"""Failure-path coverage for the live checkpointed speech driver."""

# ruff: noqa: C901

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast
from uuid import UUID

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/qualify_checkpointed_speech.py"
SPEC = importlib.util.spec_from_file_location("qualify_checkpointed_speech", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - broken test installation
    message = "could not load checkpointed speech qualification driver"
    raise RuntimeError(message)
driver = cast("Any", importlib.util.module_from_spec(SPEC))
SPEC.loader.exec_module(driver)
FAILURE_MESSAGE = "provider result was lost"


class DriverExecutionError(RuntimeError):
    """A deterministic stand-in for a failed external workflow execution."""


async def test_seed_source_verifies_the_exact_uploaded_key_with_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"qualification-source")
    source_id = UUID("0192e8a0-0000-7000-8000-000000000778")
    scope = SimpleNamespace(organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"))
    expected_key = f"org/{scope.organizationId}/source/{source_id}/audio/audio.m4a"
    calls: list[tuple[str, object]] = []

    responses: list[dict[str, object] | None] = [
        None,
        {"id": UUID("0192e8a0-0000-7000-8000-000000000779")},
    ]

    class Result:
        async def fetchone(self) -> dict[str, object] | None:
            return responses.pop(0)

    class Connection:
        async def execute(self, statement: str, parameters: object) -> Result:
            calls.append((statement, parameters))
            return Result()

    class Scoped:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_upload(_store: object, key: str, path: Path) -> int:
        assert key == expected_key
        assert path == source
        return path.stat().st_size

    async def fake_head(_store: object, key: str) -> dict[str, object]:
        assert key == expected_key
        return {"size": source.stat().st_size}

    def fake_scoped(_database_url: str, _scope: object) -> Scoped:
        return Scoped()

    monkeypatch.setattr(driver, "resolve_scope", lambda: scope)
    monkeypatch.setattr(driver.db, "scoped", fake_scoped)
    monkeypatch.setattr(driver.storage, "upload_file", fake_upload)
    monkeypatch.setattr(driver.obs, "head_async", fake_head)
    ctx = SimpleNamespace(settings=SimpleNamespace(database_url="qualification-db"), store=object())

    observed = await driver._seed_source(ctx, source_id, source, 1_000)  # noqa: SLF001

    assert observed == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(calls) == 3


async def test_execute_failure_writes_private_report_and_never_cleans_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"qualification-source")
    output = tmp_path / "report.json"
    source_id = UUID("0192e8a0-0000-7000-8000-000000000777")
    calls: list[str] = []

    class FakeClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> FakeClient:
            calls.append("connect")
            return cls()

        async def execute_workflow(self, *_args: object, **_kwargs: object) -> object:
            calls.append("execute")
            raise DriverExecutionError(FAILURE_MESSAGE)

    class FakeWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeActivities:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.injected = False
            self.observed_attempts: list[int] = []
            self.observed_dispatch_counts: list[int] = []

        def activities(self) -> list[object]:
            return []

    transcription = SimpleNamespace(
        provider="modal-checkpointed",
        speech_vad_model_path=source,
    )
    ctx = SimpleNamespace(
        settings=SimpleNamespace(database_url="qualification-db", transcription=transcription),
        store=object(),
    )

    async def fake_seed(*_args: object, **_kwargs: object) -> str:
        calls.append("seed")
        return hashlib.sha256(source.read_bytes()).hexdigest()

    async def fake_report(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("report")
        assert kwargs["result"] is None
        assert kwargs["workflow_error"] == "DriverExecutionError"
        return {"format": "test-report/1", "workflowError": kwargs["workflow_error"]}

    async def fake_delete(*_args: object, **_kwargs: object) -> int:
        calls.append("delete")
        return 1

    async def fake_close_pool() -> None:
        calls.append("close")

    async def no_op_async(*_args: object, **_kwargs: object) -> None:
        return None

    def no_op_sync(_value: object) -> None:
        return None

    monkeypatch.setattr(driver, "Client", FakeClient)
    monkeypatch.setattr(driver, "Worker", FakeWorker)
    monkeypatch.setattr(driver, "Transcribe", FakeActivities)
    monkeypatch.setattr(driver, "QualificationSpeechActivities", FakeActivities)
    monkeypatch.setattr(driver, "_seed_source", fake_seed)
    monkeypatch.setattr(driver, "_report", fake_report)
    monkeypatch.setattr(driver.storage, "delete_prefix", fake_delete)
    monkeypatch.setattr(driver.db, "close_pool", fake_close_pool)
    monkeypatch.setattr(driver, "assert_isolated_database", no_op_sync)
    monkeypatch.setattr(driver, "assert_qualification_limits", no_op_sync)
    monkeypatch.setattr(driver, "verify_asset", no_op_sync)
    monkeypatch.setattr(driver, "assert_checkpointed_deployment", no_op_async)
    monkeypatch.setattr(
        driver.TemporalSettings,
        "from_env",
        lambda: SimpleNamespace(address="temporal", namespace="test"),
    )
    monkeypatch.setattr(driver.Context, "from_env", lambda: ctx)

    args = argparse.Namespace(
        cleanup_prefix=True,
        duration_ms=1_000,
        expected_sha256=None,
        expected_size=None,
        inject_lost_result=False,
        output=output,
        source=source,
        source_id=source_id,
        task_queue="qualification-test",
        workflow_id="qualification-failure",
    )

    with pytest.raises(DriverExecutionError, match=FAILURE_MESSAGE):
        await driver.run(args)

    assert calls == ["seed", "connect", "execute", "report", "close"]
    assert json.loads(output.read_bytes()) == {
        "format": "test-report/1",
        "workflowError": "DriverExecutionError",
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
