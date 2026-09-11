"""Whole-activity Temporal liveness for chapter rendering."""

# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
# ruff: noqa: SLF001

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from fractions import Fraction
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

from temnia_pipeline.contracts import (
    ChapterCheck,
    ChapterChecks,
    ChapterEditSpec,
    EditorialStatus,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
    Status,
)
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.artifacts import HarnessArtifact
from temnia_pipeline.harness.rendering import CaptionDocument, RenderSection, timeline_identity
from temnia_pipeline.harness.routes import RouteSnapshot
from temnia_pipeline.harness.runtime_types import (
    RenderRevisionRequest,
    RenderRevisionResult,
    RunRef,
    RunSnapshot,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.media.chapters import MediaTimelineFacts
from temnia_pipeline.speech import liveness

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
EDIT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")
DESCRIPTOR_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000444")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)
TEST_TIMELINE = MediaTimelineFacts(
    duration=Fraction(1),
    container_start=Fraction(0),
    source_start=Fraction(0),
    has_video=True,
    has_audio=True,
    video_stream_index=0,
    audio_stream_index=1,
    video_start=Fraction(0),
    audio_start=Fraction(0),
    video_duration=Fraction(1),
    audio_duration=Fraction(1),
    frame_rate=Fraction(25),
    video_time_base=Fraction(1, 12800),
    audio_time_base=Fraction(1, 48000),
    sample_rate=48000,
    width=320,
    height=240,
    rotation=0,
    audio_channels=2,
    audio_layout="stereo",
    variable_frame_rate=False,
    video_codec="h264",
    audio_codec="aac",
    sample_aspect_ratio=Fraction(1),
)


class RenderActivityInput(BaseModel):
    """Typed input for the isolated Temporal activity test."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: RenderRevisionRequest


@workflow.defn(name="HarnessRenderLivenessWorkflow", sandboxed=False)
class RenderLivenessWorkflow:
    """Invoke the production render entry with a short test heartbeat timeout."""

    @workflow.run
    async def run(self, value: RenderActivityInput) -> RenderRevisionResult:
        return await workflow.execute_activity(
            "render_chapter_revision",
            value.request,
            result_type=RenderRevisionResult,
            start_to_close_timeout=timedelta(seconds=40),
            heartbeat_timeout=timedelta(seconds=3),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=2),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


RESULT = RenderRevisionResult(
    descriptor=HarnessArtifactRef(
        id=DESCRIPTOR_ID,
        kind=HarnessArtifactKind.render,
        fingerprint="f" * 64,
        sha256="a" * 64,
        sizeBytes=1,
        storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/render.json",
    ),
    technical_report=(),
    technical_passed=True,
)


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


def render_request() -> RenderRevisionRequest:
    return RenderRevisionRequest(
        run=RunRef(
            scope_organization_id=SCOPE.organizationId,
            scope_user_id=SCOPE.userId,
            source_id=SOURCE_ID,
            run_id=RUN_ID,
        ),
        edit=HarnessArtifactRef(
            id=EDIT_ID,
            kind=HarnessArtifactKind.edit,
            fingerprint="b" * 64,
            sha256="c" * 64,
            sizeBytes=1,
            storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/edit.json",
        ),
        revision=1,
    )


class QuietRenderActivities(HarnessActivities):
    """Expose render ownership events while retaining the production entry and lease."""

    def __init__(self, context: object, settings: HarnessSettings, routes: RouteSnapshot) -> None:
        super().__init__(cast("Any", context), settings, routes)
        self.run = cast("RunSnapshot", SimpleNamespace(id=RUN_ID))
        self.attempts: list[int] = []
        self.phases: list[str] = []
        self.operation_started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.operation_cleaned = asyncio.Event()
        self.lease_entered = asyncio.Event()
        self.lease_released = asyncio.Event()
        self.expiry_armed: list[object] = []
        self.cancel_cleanup_seconds = 0.0
        self.cleanup_release: asyncio.Event | None = None
        self.block = False

    async def _assert_render_active(self, ref: RunRef, revision: int) -> RunSnapshot:
        _ = ref, revision
        self.phases.append("scoped-lookup")
        await asyncio.sleep(2)
        return self.run

    @asynccontextmanager
    async def _source_cache_lease(self, run_id: object) -> AsyncGenerator[None]:
        async with super()._source_cache_lease(run_id):
            self.lease_entered.set()
            try:
                yield
            finally:
                self.lease_released.set()

    async def _render_chapter_revision_locked(
        self,
        request: RenderRevisionRequest,
        run: RunSnapshot,
        *,
        retain_source_cache: bool = False,
    ) -> RenderRevisionResult:
        _ = run, retain_source_cache
        with contextlib.suppress(RuntimeError):
            self.attempts.append(activity.info().attempt)
        workspace = self._render_workspace(self.run, request)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "owned.partial").write_bytes(b"partial")
        self.operation_started.set()
        try:
            self.phases.append("quiet-decode")
            await asyncio.sleep(2)
            self.phases.append("quiet-publish")
            if self.block:
                await asyncio.Future()
            await asyncio.sleep(2)
            return RESULT
        finally:
            self.cleanup_started.set()
            if self.cleanup_release is not None:
                await self.cleanup_release.wait()
            if self.cancel_cleanup_seconds:
                await asyncio.sleep(self.cancel_cleanup_seconds)
            self.operation_cleaned.set()

    def _arm_source_cache_expiry(self, run_id: object, *, delay_seconds: float = 0) -> None:
        _ = delay_seconds
        self.expiry_armed.append(run_id)


class ProductionPathRenderActivities(HarnessActivities):
    """Use the real render/check/publication implementation with quiet I/O seams."""

    def __init__(self, context: object, settings: HarnessSettings, routes: RouteSnapshot) -> None:
        super().__init__(cast("Any", context), settings, routes)
        self.timeline = TEST_TIMELINE
        self.source_sha = "d" * 64
        self.expected_source_fingerprint = "e" * 64
        self.run = cast(
            "RunSnapshot",
            SimpleNamespace(
                id=RUN_ID,
                source_id=SOURCE_ID,
                evidence_artifact_id=uuid.UUID(int=9),
                source=SimpleNamespace(
                    size_bytes=1024,
                    duration_ms=1000,
                    storage_key="source/master.mp4",
                ),
            ),
        )
        self.phases: list[str] = []

    async def _assert_render_active(self, ref: RunRef, revision: int) -> RunSnapshot:
        _ = ref, revision
        return self.run

    @staticmethod
    def _validate_render_edit(
        run: RunSnapshot, request: RenderRevisionRequest, raw: object
    ) -> ChapterEditSpec:
        _ = request, raw
        return cast(
            "ChapterEditSpec",
            SimpleNamespace(
                evidenceArtifactId=run.evidence_artifact_id,
                evidenceSha256="1" * 64,
            ),
        )

    @staticmethod
    def _evidence_source(
        evidence: HarnessEvidence,
    ) -> tuple[str, dict[str, str | None], dict[str, object]]:
        _ = evidence
        return (
            "d" * 64,
            {"etag": "source-etag", "versionId": None},
            timeline_identity(TEST_TIMELINE),
        )

    async def _source_path(
        self,
        run: RunSnapshot,
        *,
        observed: dict[str, str | None],
        expected_sha256: str | None = None,
    ) -> tuple[Path, str]:
        _ = run, observed, expected_sha256
        self.phases.append("source-download-and-hash")
        await asyncio.sleep(0.04)
        path = self.ctx.settings.work_root / "source.mp4"
        path.write_bytes(b"source")
        return path, self.source_sha

    @staticmethod
    def _source_fingerprint(
        scope: Scope,
        run: RunSnapshot,
        timeline: MediaTimelineFacts,
        *,
        source_sha: str,
        observed: dict[str, str | None],
    ) -> str:
        _ = scope, run, timeline, source_sha, observed
        return "e" * 64


def quiet_activities(tmp_path: Path) -> QuietRenderActivities:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(work_root=tmp_path, database_url="unused"), store=None
    )
    return QuietRenderActivities(context, harness_settings(routes), routes)


def accepted_artifact(kind: str, fingerprint: str, index: int) -> HarnessArtifact:
    return HarnessArtifact(
        id=uuid.UUID(int=100 + index),
        organization_id=SCOPE.organizationId,
        source_id=SOURCE_ID,
        kind=kind,
        fingerprint=fingerprint,
        storage_key=f"artifact/{index}",
        sha256=hashlib.sha256(str(index).encode()).hexdigest(),
        size_bytes=1,
        metadata={},
        transcript_id=None,
        transcript_revision=None,
        dependency_ids=(),
    )


@pytest.mark.parametrize("reuse_media", [False, True])
async def test_real_render_check_publish_path_remains_under_heartbeat(  # noqa: C901, PLR0915
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reuse_media: bool,  # noqa: FBT001
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(
            work_root=tmp_path,
            database_url="unused",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        ),
        store=None,
    )
    activities = ProductionPathRenderActivities(context, harness_settings(routes), routes)
    request = render_request()
    section = RenderSection("section-1", Fraction(0), Fraction(1))
    evidence = cast(
        "HarnessEvidence",
        SimpleNamespace(sourceFingerprint=activities.expected_source_fingerprint),
    )
    published = 0
    find_count = 0
    heartbeat_phases: list[str] = []

    async def read_json(*args: object, **kwargs: object) -> dict[str, object]:
        _ = args, kwargs
        return {}

    async def head(*args: object, **kwargs: object) -> dict[str, object]:
        _ = args, kwargs
        activities.phases.append("source-head")
        await asyncio.sleep(0.04)
        return {"size": 1024, "e_tag": "source-etag"}

    async def inspect(*args: object, **kwargs: object) -> MediaTimelineFacts:
        _ = args, kwargs
        activities.phases.append("source-probe")
        await asyncio.sleep(0.04)
        return activities.timeline

    async def acquire(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        return SimpleNamespace(operation=SimpleNamespace(id=uuid.uuid4()))

    async def find(*args: object, **kwargs: object) -> HarnessArtifact | None:
        nonlocal find_count
        _ = args, kwargs
        find_count += 1
        if reuse_media and find_count == 1:
            return accepted_artifact("render", "3" * 64, 20)
        return None

    async def read_file(*args: object, **kwargs: object) -> int:
        _ = args
        activities.phases.append("reuse-download-and-hash")
        await asyncio.sleep(0.04)
        cast("Path", kwargs["destination"]).write_bytes(b"media")
        return len(b"media")

    async def render(*args: object, **kwargs: object) -> None:
        _ = args
        assert not reuse_media
        activities.phases.append("encode")
        await asyncio.sleep(0.04)
        cast("Path", kwargs.get("path", args[2])).write_bytes(b"media")

    async def publish_file(*args: object, **kwargs: object) -> HarnessArtifact:
        nonlocal published
        _ = args
        published += 1
        activities.phases.append("publish-file")
        await asyncio.sleep(0.04)
        identity = cast("Any", kwargs["identity"])
        return accepted_artifact(identity.kind, identity.fingerprint, published)

    async def inspect_media(*args: object, **kwargs: object) -> ChapterChecks:
        _ = args, kwargs
        activities.phases.append("strict-decode")
        await asyncio.sleep(0.04)
        return ChapterChecks(
            editorialReasons=[],
            editorialStatus=EditorialStatus.not_run,
            editSha256=request.edit.sha256,
            technicalChecks=[],
            verifierFamily=None,
            version=1,
        )

    async def publish_json(*args: object, **kwargs: object) -> HarnessArtifact:
        nonlocal published
        _ = args
        published += 1
        activities.phases.append("publish-json")
        await asyncio.sleep(0.04)
        identity = cast("Any", kwargs["identity"])
        return accepted_artifact(identity.kind, identity.fingerprint, published)

    async def complete(*args: object, **kwargs: object) -> None:
        _ = args, kwargs

    def write_captions(path: Path, *_args: object) -> CaptionDocument:
        path.write_text("WEBVTT\n", encoding="utf-8")
        return CaptionDocument(body="WEBVTT\n", cue_count=0, warnings=())

    monkeypatch.setattr(liveness, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def heartbeat(_details: object) -> None:
        heartbeat_phases.append(activities.phases[-1] if activities.phases else "entry")

    monkeypatch.setattr(liveness.activity, "heartbeat", heartbeat)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", read_json)
    monkeypatch.setattr(
        activities_module,
        "HarnessEvidence",
        SimpleNamespace(model_validate=lambda _raw: evidence),
    )
    monkeypatch.setattr(activities_module, "kept_sections", lambda _edit: (section,))
    monkeypatch.setattr(activities_module, "required_disk_bytes", lambda **_kwargs: 1)
    monkeypatch.setattr(activities_module, "preflight_disk", lambda *_args: 1)
    monkeypatch.setattr(activities_module.obs, "head_async", head)
    monkeypatch.setattr(activities_module, "inspect_timeline", inspect)
    monkeypatch.setattr(activities_module.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(activities_module.artifacts, "find_artifact", find)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_file", read_file)
    monkeypatch.setattr(activities_module, "render_chapter", render)
    monkeypatch.setattr(activities_module.artifacts, "publish_file", publish_file)
    monkeypatch.setattr(activities_module, "caption_fingerprint", lambda **_kwargs: "2" * 64)
    monkeypatch.setattr(activities_module, "write_captions", write_captions)
    monkeypatch.setattr(activities_module, "check_chapter_media", inspect_media)
    monkeypatch.setattr(
        activities_module,
        "check_chapter_captions",
        lambda *_args, **_kwargs: ChapterCheck(
            expected=None,
            measured=None,
            message="captions valid",
            name="captions",
            sectionId=section.section_id,
            status=Status.pass_,
        ),
    )
    monkeypatch.setattr(activities_module.artifacts, "publish_json", publish_json)
    monkeypatch.setattr(activities_module.ledger, "complete_operation_from_artifact", complete)
    monkeypatch.setattr(activities_module, "technical_checks_pass", lambda *_args, **_kwargs: True)

    result = await activities.render_chapter_revision(request)

    assert result.technical_passed
    assert published == (3 if reuse_media else 4)
    expected_phases = [
        "source-head",
        "source-download-and-hash",
        "source-probe",
        *(["reuse-download-and-hash"] if reuse_media else ["encode", "publish-file"]),
        "publish-file",
        "strict-decode",
        "publish-json",
        "publish-json",
    ]
    assert activities.phases == expected_phases
    assert "strict-decode" in heartbeat_phases
    assert heartbeat_phases.count("publish-json") >= 2
    assert not activities._render_workspace(activities.run, request).exists()


async def test_quiet_render_phases_keep_one_temporal_attempt(tmp_path: Path) -> None:
    activities = quiet_activities(tmp_path)
    request = render_request()
    queue = f"harness-render-liveness-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[RenderLivenessWorkflow],
            activities=[activities.render_chapter_revision],
        ),
    ):
        result = await environment.client.execute_workflow(
            RenderLivenessWorkflow.run,
            RenderActivityInput(request=request),
            id=f"harness-render-liveness-{uuid.uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )

    assert result == RESULT
    assert activities.attempts == [1]
    assert activities.phases == ["scoped-lookup", "quiet-decode", "quiet-publish"]
    assert activities.operation_cleaned.is_set()
    assert activities.lease_released.is_set()
    assert not activities._render_workspace(activities.run, request).exists()
    assert activities.expiry_armed == []


async def test_temporal_cancellation_keeps_heartbeats_alive_through_render_cleanup(
    tmp_path: Path,
) -> None:
    activities = quiet_activities(tmp_path)
    activities.block = True
    activities.cancel_cleanup_seconds = 4
    request = render_request()
    queue = f"harness-render-cancel-{uuid.uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[RenderLivenessWorkflow],
            activities=[activities.render_chapter_revision],
        ),
    ):
        handle = await environment.client.start_workflow(
            RenderLivenessWorkflow.run,
            RenderActivityInput(request=request),
            id=f"harness-render-cancel-{uuid.uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        await asyncio.wait_for(activities.operation_started.wait(), timeout=10)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as raised:
            await handle.result()

    assert isinstance(raised.value.cause, CancelledError)
    assert activities.attempts == [1]
    assert activities.operation_cleaned.is_set()
    assert activities.lease_released.is_set()
    assert not activities._render_workspace(activities.run, request).exists()
    assert activities.expiry_armed == [RUN_ID]


async def test_heartbeat_failure_drains_render_before_releasing_lease_and_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = quiet_activities(tmp_path)
    activities.block = True
    activities.cleanup_release = asyncio.Event()
    request = render_request()

    async def fail_after_render_starts(
        stop: asyncio.Event,
        details: object,
        *,
        interval_seconds: float,
    ) -> None:
        _ = stop, details, interval_seconds
        await activities.operation_started.wait()
        message = "heartbeat transport failed"
        raise RuntimeError(message)

    monkeypatch.setattr(liveness, "_heartbeat_until_stopped", fail_after_render_starts)
    supervised = asyncio.create_task(activities.render_chapter_revision(request))
    await asyncio.wait_for(activities.cleanup_started.wait(), timeout=5)
    assert activities._render_workspace(activities.run, request).exists()
    assert activities._try_source_cache_lease(RUN_ID) is None
    assert not activities.lease_released.is_set()
    activities.cleanup_release.set()
    with pytest.raises(RuntimeError, match="heartbeat transport failed"):
        await supervised

    assert activities.operation_cleaned.is_set()
    assert activities.lease_released.is_set()
    assert not activities._render_workspace(activities.run, request).exists()
    assert activities.expiry_armed == [RUN_ID]
    handle = activities._try_source_cache_lease(RUN_ID)
    assert handle is not None
    handle.close()


async def test_cancelled_lease_waiter_never_deletes_active_owner_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = quiet_activities(tmp_path)
    request = render_request()
    workspace = activities._render_workspace(activities.run, request)
    workspace.mkdir(parents=True)
    sentinel = workspace / "owner.partial"
    sentinel.write_bytes(b"active owner")
    owner = activities._try_source_cache_lease(RUN_ID)
    assert owner is not None
    waiting = asyncio.Event()

    def heartbeat(details: object) -> None:
        if details == {"stage": "waiting-for-source-cache-lease"}:
            waiting.set()

    monkeypatch.setattr(liveness.activity, "heartbeat", heartbeat)
    try:
        waiter = asyncio.create_task(activities.render_chapter_revision(request))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert sentinel.read_bytes() == b"active owner"
        assert activities._try_source_cache_lease(RUN_ID) is None
        assert activities.expiry_armed == [RUN_ID]
    finally:
        fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
        owner.close()
