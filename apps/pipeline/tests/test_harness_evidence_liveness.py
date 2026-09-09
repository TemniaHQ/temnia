"""Temporal liveness and quiet-I/O bounds for chapter evidence construction."""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel, ConfigDict
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef, Scope
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.routes import RouteSnapshot
from temnia_pipeline.harness.runtime_types import (
    BuildEvidenceRequest,
    EvidenceResult,
    RunRef,
    RunSnapshot,
)
from temnia_pipeline.harness.settings import HarnessSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
ARTIFACT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)
RESULT = EvidenceResult(
    artifact=HarnessArtifactRef(
        id=ARTIFACT_ID,
        kind=HarnessArtifactKind.evidence,
        fingerprint="f" * 64,
        sha256="a" * 64,
        sizeBytes=1,
        storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/evidence.json",
    ),
    sentence_count=1,
    word_count=1,
    duration_ms=1,
    lexical_state="present",
)


class EvidenceActivityInput(BaseModel):
    """Typed input for the isolated Temporal activity test."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: BuildEvidenceRequest


@workflow.defn(name="HarnessEvidenceLivenessWorkflow", sandboxed=False)
class EvidenceLivenessWorkflow:
    """Invoke the production activity wrapper with a short test heartbeat timeout."""

    @workflow.run
    async def run(self, value: EvidenceActivityInput) -> EvidenceResult:
        return await workflow.execute_activity(
            "build_chapter_evidence",
            value.request,
            result_type=EvidenceResult,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=3),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=2),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


class QuietEvidenceActivities(HarnessActivities):
    """Replace evidence internals with the quiet I/O interval observed on staging."""

    def __init__(
        self,
        context: object,
        settings: HarnessSettings,
        routes: RouteSnapshot,
    ) -> None:
        super().__init__(cast("Any", context), settings, routes)
        self.cleaned = asyncio.Event()
        self.attempts: list[int] = []
        self.partial = cast("Any", context).settings.work_root / "quiet-source.part"

    async def _build_chapter_evidence_locked(
        self,
        request: BuildEvidenceRequest,
        run: RunSnapshot,
        scope: Scope,
    ) -> EvidenceResult:
        _ = request, run, scope
        self.attempts.append(activity.info().attempt)
        self.partial.write_bytes(b"partial")
        try:
            await asyncio.sleep(6)
            return RESULT
        finally:
            self.partial.unlink(missing_ok=True)
            self.cleaned.set()


def harness_settings(routes: RouteSnapshot) -> HarnessSettings:
    return HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(FIXTURES / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(FIXTURES / "routes.synthetic.json"),
        }
    )


def evidence_request() -> BuildEvidenceRequest:
    return BuildEvidenceRequest(
        run=RunRef(
            scope_organization_id=SCOPE.organizationId,
            scope_user_id=SCOPE.userId,
            source_id=SOURCE_ID,
            run_id=RUN_ID,
        )
    )


async def test_quiet_evidence_io_keeps_one_temporal_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(work_root=tmp_path, database_url="unused"), store=None
    )
    activities = QuietEvidenceActivities(context, harness_settings(routes), routes)

    async def get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(id=RUN_ID)

    monkeypatch.setattr(activities_module.runs, "get_run", get_run)
    queue = f"harness-evidence-liveness-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[EvidenceLivenessWorkflow],
            activities=[activities.build_chapter_evidence],
        ),
    ):
        result = await environment.client.execute_workflow(
            EvidenceLivenessWorkflow.run,
            EvidenceActivityInput(request=evidence_request()),
            id=f"harness-evidence-liveness-{uuid.uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=20),
        )

    assert result == RESULT
    assert activities.attempts == [1]
    assert activities.cleaned.is_set()
    assert not activities.partial.exists()


async def test_cancelled_evidence_drains_partial_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(work_root=tmp_path, database_url="unused"), store=None
    )
    activities = HarnessActivities(cast("Any", context), harness_settings(routes), routes)
    started = asyncio.Event()
    source = SimpleNamespace(
        storage_key="scoped/master.mp4",
        size_bytes=2,
        etag="frozen-etag",
        version_id=None,
    )

    async def get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(id=RUN_ID, source=source)

    async def head(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"size": 2, "e_tag": "frozen-etag"}

    class Download:
        def __init__(self) -> None:
            self.meta = {"size": 2, "e_tag": "frozen-etag"}

        async def stream(self, *, min_chunk_size: int) -> AsyncIterator[bytes]:
            assert min_chunk_size == 8 * 1024 * 1024
            yield b"a"
            started.set()
            await asyncio.Event().wait()
            yield b"b"

    async def download(*_args: object, **_kwargs: object) -> Download:
        return Download()

    def disk_usage(_path: object) -> SimpleNamespace:
        return SimpleNamespace(free=1024 * 1024 * 1024)

    monkeypatch.setattr(activities_module.runs, "get_run", get_run)
    monkeypatch.setattr(activities_module.obs, "head_async", head)
    monkeypatch.setattr(activities_module.obs, "get_async", download)
    monkeypatch.setattr(activities_module.shutil, "disk_usage", disk_usage)
    queue = f"harness-evidence-cancel-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[EvidenceLivenessWorkflow],
            activities=[activities.build_chapter_evidence],
        ),
    ):
        handle = await environment.client.start_workflow(
            EvidenceLivenessWorkflow.run,
            EvidenceActivityInput(request=evidence_request()),
            id=f"harness-evidence-cancel-{uuid.uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=20),
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as raised:
            await handle.result()

    assert isinstance(raised.value.cause, CancelledError)
    directory = tmp_path / "harness" / str(RUN_ID) / "source"
    assert not list(directory.glob("*.part"))
    assert not (directory / "master.mp4").exists()


async def test_source_stream_quiet_timeout_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), harness_settings(routes), routes)
    source = SimpleNamespace(storage_key="scoped/master.mp4", size_bytes=2)
    run = SimpleNamespace(id=RUN_ID, source=source)

    class Download:
        def __init__(self) -> None:
            self.meta = {"size": 2, "e_tag": "frozen-etag"}

        async def stream(self, *, min_chunk_size: int) -> AsyncIterator[bytes]:
            assert min_chunk_size == 8 * 1024 * 1024
            yield b"a"
            await asyncio.Event().wait()
            yield b"b"

    async def download(*_args: object, **_kwargs: object) -> Download:
        return Download()

    monkeypatch.setattr(activities_module.obs, "get_async", download)
    monkeypatch.setattr(activities_module, "SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS", 0.01)

    def disk_usage(_path: object) -> SimpleNamespace:
        return SimpleNamespace(free=1024 * 1024 * 1024)

    monkeypatch.setattr(activities_module.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(
        activities_module.activity,
        "info",
        lambda: SimpleNamespace(activity_id="source-stream-timeout-test"),
    )

    def heartbeat(_details: object) -> None:
        return None

    monkeypatch.setattr(activities_module.activity, "heartbeat", heartbeat)

    with pytest.raises(TimeoutError, match="source download chunk timed out"):
        await activities._source_path(
            cast("Any", run),
            observed={"etag": "frozen-etag", "versionId": None},
        )

    directory = tmp_path / "harness" / str(RUN_ID) / "source"
    assert not list(directory.glob("*.part"))
    assert not (directory / "master.mp4").exists()


async def test_source_download_header_has_a_quiet_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), harness_settings(routes), routes)
    source = SimpleNamespace(storage_key="scoped/master.mp4", size_bytes=2)
    run = SimpleNamespace(id=RUN_ID, source=source)

    async def stalled_download(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()
        return object()

    monkeypatch.setattr(activities_module.obs, "get_async", stalled_download)
    monkeypatch.setattr(activities_module, "SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        activities_module.activity,
        "info",
        lambda: SimpleNamespace(activity_id="source-header-timeout-test"),
    )

    with pytest.raises(TimeoutError, match="source download header timed out"):
        await activities._source_path(
            cast("Any", run),
            observed={"etag": "frozen-etag", "versionId": None},
        )

    directory = tmp_path / "harness" / str(RUN_ID) / "source"
    assert not list(directory.glob("*.part"))


async def test_source_metadata_read_has_a_quiet_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), harness_settings(routes), routes)
    source = SimpleNamespace(storage_key="scoped/master.mp4", size_bytes=2)
    run = SimpleNamespace(id=RUN_ID, source=source)

    async def stalled_head(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()
        return object()

    monkeypatch.setattr(activities_module.obs, "head_async", stalled_head)
    monkeypatch.setattr(activities_module, "SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError, match="source metadata read timed out"):
        await activities._build_chapter_evidence_locked(
            evidence_request(),
            cast("Any", run),
            SCOPE,
        )
