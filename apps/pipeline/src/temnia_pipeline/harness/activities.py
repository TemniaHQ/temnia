"""Bound Temporal activities for finite chapter orchestration."""

# DTO annotations are evaluated by Temporal; disabled refusal is a public state.
# ruff: noqa: EM101, N818, TRY003

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import httpx
import obstore as obs
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterCheck,
    ChapterChecks,
    ChapterEditSpec,
    ChapterRender,
    ChapterRenders,
    ChapterReviewInput,
    ChapterReviewOutput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    PositiveRational,
    Scope,
    SignedRationalTime,
    Status,
    TranscriptV1,
)
from temnia_pipeline.harness import artifacts, ledger, receipts, runs
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V8, is_topic_policy
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.harness.gateway import GatewayConfig
from temnia_pipeline.harness.rendering import (
    RenderSection,
    build_render_descriptor,
    caption_fingerprint,
    caption_metadata,
    checks_metadata,
    descriptor_fingerprint,
    descriptor_metadata,
    kept_sections,
    media_fingerprint,
    media_metadata,
    preflight_disk,
    required_disk_bytes,
    run_render_batch,
    write_captions,
)
from temnia_pipeline.harness.review import ReviewRefused
from temnia_pipeline.harness.run_failures import RECONCILED_RUN_MESSAGE
from temnia_pipeline.harness.runtime_types import (
    AcceptInitialRevisionRequest,
    BuildEvidenceRequest,
    ClaimRepairRequest,
    CommitReviewMutationRequest,
    CommitReviewMutationResult,
    EvidenceResult,
    MarkRunFailedRequest,
    ReconcileRunRequest,
    ReconcileRunResult,
    ReconcileSweepResult,
    RenderRevisionRequest,
    RenderRevisionResult,
    ResumeRunAssets,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
)
from temnia_pipeline.harness.shot_evidence import (
    build_source_shot_evidence,
    find_source_shot_evidence,
)
from temnia_pipeline.harness.source_sensors import find_source_timeline, object_identity
from temnia_pipeline.harness.speech_evidence import (
    build_source_speech_coverage,
    find_source_speech_coverage,
)
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence
from temnia_pipeline.harness.topic_decisions import TopicDecisionActivities
from temnia_pipeline.harness.topic_patch_review import TopicEditorialPatchActivities
from temnia_pipeline.harness.topic_render import TopicRenderActivities
from temnia_pipeline.harness.topic_review import TopicReviewActivities
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.validators import rounded_milliseconds
from temnia_pipeline.media.chapter_checks import (
    check_chapter_captions,
    check_chapter_media,
    technical_checks_pass,
)
from temnia_pipeline.media.chapters import (
    RENDERER_VERSION,
    ChapterRenderConfig,
    MediaTimelineFacts,
    inspect_timeline,
    render_chapter,
)
from temnia_pipeline.media.timeline_identity import timeline_from_identity, timeline_identity
from temnia_pipeline.reaper import SERVICE_USER, list_organizations
from temnia_pipeline.render_contracts import RenderJob, RenderProgress, RenderSectionJob
from temnia_pipeline.render_remote import (
    ModalRenderer,
    RealRenderClient,
    RenderFunctionAbsent,
    download_output,
)
from temnia_pipeline.speech.liveness import run_with_activity_heartbeat
from temnia_pipeline.substrate.factory import make_segmenter

log = logging.getLogger("temnia.harness.activities")

CANCELLATION_POLL_SECONDS = 5
SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS = 3 * 60
SHA256_HEX_LENGTH = 64
MAX_SOURCE_SUFFIX_LENGTH = 12
MIN_RUNTIME_DISK_FREE_BYTES = 512 * 1024 * 1024
MAX_REVIEW_REVISIONS = 500
MAX_HIERARCHY_LEVEL = 8
HARNESS_WORKSPACE_TTL_SECONDS = 24 * 60 * 60

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

    from temnia_pipeline.harness.routes import RouteSnapshot
    from temnia_pipeline.harness.runtime_types import WorkflowIdentity
    from temnia_pipeline.harness.settings import HarnessSettings
    from temnia_pipeline.ingest import Context


class HarnessDisabled(RuntimeError):
    """The universally registered activity was invoked on a disabled worker."""


class HarnessActivities:
    """Chapter activities sharing the worker's database and object-store context."""

    def __init__(
        self,
        ctx: Context,
        harness_settings: HarnessSettings,
        snapshot: RouteSnapshot | None,
        *,
        execution_open: Callable[[WorkflowIdentity], Awaitable[bool]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.harness_settings = harness_settings
        self.snapshot = snapshot
        # Answers whether a Temporal execution is still running; the worker binds its client.
        # Without it the reaper leaves fenced runs alone rather than guessing.
        self.execution_open = execution_open
        self._render_semaphore = asyncio.Semaphore(harness_settings.max_render_concurrency)
        self._source_expiry_tasks: dict[str, asyncio.Task[None]] = {}
        if isinstance(getattr(ctx.settings, "work_root", None), Path):
            self._cleanup_stale_harness_workspaces(active_run_id=None)
            self._arm_existing_source_cache_expiries()

    def _require_enabled(self) -> RouteSnapshot:
        if not self.harness_settings.enabled or self.snapshot is None:
            raise HarnessDisabled("chapter harness is disabled on this worker")
        return self.snapshot

    @activity.defn(name="start_chapter_run")
    async def start_chapter_run(self, request: StartRunRequest) -> StartRunResult:
        """Create/refetch one immutable run after boot and source validation."""
        snapshot = self._require_enabled()
        return await runs.start_or_refetch_run(
            self.ctx.settings.database_url,
            start=request,
            settings=self.harness_settings,
            route_snapshot=snapshot,
        )

    @activity.defn(name="get_chapter_run")
    async def get_chapter_run(self, request: RunRef) -> RunSnapshot:
        """Return compact current state for cancellation and stale-work checks."""
        self._require_enabled()
        return await runs.get_run(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=request.scope_organization_id,
                userId=request.scope_user_id,
            ),
            source_id=request.source_id,
            run_id=request.run_id,
        )

    @activity.defn(name="get_chapter_resume_assets")
    async def get_chapter_resume_assets(self, request: RunRef) -> ResumeRunAssets:
        """Return compact current artifact refs after a known-state resume claim."""
        self._require_enabled()
        return await runs.get_resume_assets(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=request.scope_organization_id,
                userId=request.scope_user_id,
            ),
            source_id=request.source_id,
            run_id=request.run_id,
        )

    @activity.defn(name="update_chapter_run_stage")
    async def update_chapter_run_stage(self, request: StageUpdate) -> RunSnapshot:
        """Apply a stage/status CAS without reviving a terminal run."""
        self._require_enabled()
        return await runs.update_stage(self.ctx.settings.database_url, request)

    @activity.defn(name="claim_chapter_repair")
    async def claim_chapter_repair(self, request: ClaimRepairRequest) -> RunSnapshot:
        """Claim one finite semantic repair before another model dispatch."""
        self._require_enabled()
        return await runs.claim_repair(
            self.ctx.settings.database_url,
            request=request,
            max_repairs=self.harness_settings.max_repairs,
        )

    @activity.defn(name="mark_chapter_run_failed")
    async def mark_chapter_run_failed(self, request: MarkRunFailedRequest) -> bool:
        """Persist a safe known-failure state for the exact owning execution."""
        self._require_enabled()
        return await runs.mark_run_failed(self.ctx.settings.database_url, request=request)

    def _gateway_config(self) -> GatewayConfig | None:
        api_key = self.harness_settings.gateway_api_key
        if (
            api_key is None
            or self.harness_settings.backend != "gateway"
            or self.harness_settings.gateway == "direct"
        ):
            # The direct vendor path has no receipts to reconcile; the sweeps stay idle.
            return None
        return GatewayConfig(api_key=api_key, gateway=self.harness_settings.gateway)

    @activity.defn(name="reconcile_chapter_run_costs")
    async def reconcile_chapter_run_costs(self, request: ReconcileRunRequest) -> ReconcileRunResult:
        """Settle the ending execution's unknown outcomes from receipts; park the run if clear."""
        self._require_enabled()
        config = self._gateway_config()
        if config is None:
            return ReconcileRunResult()
        from temnia_pipeline.harness.cli import reconcile_run_costs  # noqa: PLC0415

        run = request.run
        scope = Scope(organizationId=run.scope_organization_id, userId=run.scope_user_id)
        url = self.ctx.settings.database_url
        results = await reconcile_run_costs(
            url, run_id=run.run_id, api_key=config.api_key, apply=True, gateway=config.gateway
        )
        async with httpx.AsyncClient() as client:
            report = await receipts.recover_unknown_attempts(
                url, scope=scope, run_id=run.run_id, config=config, client=client
            )
        resolved = await runs.park_reconciled_run(
            url, run=run, workflow=request.workflow, message=RECONCILED_RUN_MESSAGE
        )
        return ReconcileRunResult(
            looked_up=len(results) + report.looked_up,
            settled=sum(1 for item in results if item.get("applied") is True) + report.settled,
            released=report.released,
            pending=report.remaining,
            resolved=resolved,
        )

    @activity.defn(name="reconcile_unknown_runs")
    async def reconcile_unknown_runs(self) -> ReconcileSweepResult:
        """Every reaper tick: settle fenced runs whose execution has ended, from receipts.

        A run whose execution is still running is left to that execution, which waits
        for its own receipts. Historical programs are not touched: their fences are
        part of the audition record and a human settles them with the CLI.
        """
        if not self.harness_settings.enabled or self.snapshot is None:
            return ReconcileSweepResult()
        config = self._gateway_config()
        if config is None or self.execution_open is None:
            return ReconcileSweepResult()
        url = self.ctx.settings.database_url
        counted = 0
        skipped = settled = released = parked = pending = 0
        async with httpx.AsyncClient() as client:
            for organization_id in await list_organizations(url):
                scope = Scope(organizationId=organization_id, userId=SERVICE_USER)
                fenced = await receipts.fenced_runs(
                    url, scope=scope, policy=TOPIC_SELECTION_POLICY_V8
                )
                for run in fenced:
                    counted += 1
                    if run.workflow is not None and await self.execution_open(run.workflow):
                        skipped += 1
                        continue
                    report = await receipts.recover_unknown_attempts(
                        url, scope=scope, run_id=run.run_id, config=config, client=client
                    )
                    ref = RunRef(
                        scope_organization_id=organization_id,
                        scope_user_id=SERVICE_USER,
                        source_id=run.source_id,
                        run_id=run.run_id,
                    )
                    if await runs.park_reconciled_run(
                        url, run=ref, workflow=run.workflow, message=RECONCILED_RUN_MESSAGE
                    ):
                        parked += 1
                    settled += report.settled
                    released += report.released
                    pending += report.remaining
        if counted:
            log.info(
                "reconciled fenced runs: %d seen, %d live, %d settled, %d released, %d parked,"
                " %d pending",
                counted,
                skipped,
                settled,
                released,
                parked,
                pending,
            )
        return ReconcileSweepResult(
            runs=counted,
            skipped_live=skipped,
            settled=settled,
            released=released,
            parked=parked,
            pending=pending,
        )

    @activity.defn(name="apply_chapter_review")
    async def apply_chapter_review(self, request: ChapterReviewInput) -> ChapterReviewOutput:
        """Persist operational review commands; content mutations add their artifact first."""
        self._require_enabled()
        return await runs.apply_operational_review(
            self.ctx.settings.database_url,
            request=request,
            max_run_budget_micros=self.harness_settings.max_run_budget_micros,
        )

    @activity.defn(name="accept_initial_chapter_revision")
    async def accept_initial_chapter_revision(
        self, request: AcceptInitialRevisionRequest
    ) -> RunSnapshot:
        """Apply the compact revision-one CAS after heavy artifact publication."""
        self._require_enabled()
        ref = request.run
        return await runs.accept_initial_revision(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=ref.scope_organization_id,
                userId=ref.scope_user_id,
            ),
            source_id=ref.source_id,
            run_id=ref.run_id,
            request_key=request.request_key,
            edit_artifact_id=request.edit_artifact_id,
            base_revision=request.base_revision,
        )

    @activity.defn(name="commit_chapter_review")
    async def commit_chapter_review(
        self, request: CommitReviewMutationRequest
    ) -> CommitReviewMutationResult:
        """Apply the compact source/run/revision CAS after candidate publication."""
        self._require_enabled()
        return await runs.commit_review_mutation(
            self.ctx.settings.database_url,
            request,
        )

    @activity.defn(name="build_chapter_evidence")
    async def build_chapter_evidence(self, request: BuildEvidenceRequest) -> EvidenceResult:
        """Build evidence while leasing, then retain, one verified source cache."""
        self._require_enabled()
        ref = request.run
        scope = Scope(
            organizationId=ref.scope_organization_id,
            userId=ref.scope_user_id,
        )
        run = await runs.get_run(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
        )
        try:
            async with self._source_cache_lease(run.id):
                return await run_with_activity_heartbeat(
                    lambda: self._build_chapter_evidence_locked(request, run, scope),
                    details={"stage": "build-chapter-evidence"},
                )
        finally:
            self._arm_source_cache_expiry(run.id)

    @activity.defn(name="cleanup_chapter_source_cache")
    async def cleanup_chapter_source_cache(self, request: RunRef) -> bool:
        """Best-effort removal of one scoped run cache when no media activity owns it."""
        self._require_enabled()
        await runs.get_run(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=request.scope_organization_id,
                userId=request.scope_user_id,
            ),
            source_id=request.source_id,
            run_id=request.run_id,
        )
        return self._try_cleanup_source_cache(request.run_id)

    async def _build_chapter_evidence_locked(  # noqa: C901, PLR0912, PLR0915
        self,
        request: BuildEvidenceRequest,
        run: RunSnapshot,
        scope: Scope,
    ) -> EvidenceResult:
        """Build immutable evidence while this worker owns the source cache lease."""
        ref = request.run
        try:
            async with asyncio.timeout(SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS):
                master = await obs.head_async(self.ctx.store, run.source.storage_key)
        except TimeoutError as error:
            raise TimeoutError("source metadata read timed out") from error
        if int(master["size"]) != run.source.size_bytes:  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            raise RuntimeError("source master size changed after run creation")
        observed = self._object_identity(master)
        self._validate_pinned_object(run, observed)
        # Ingest measured this master already: take its hash and timeline from the record
        # and assemble evidence without a download. A source ingested before the sensors
        # existed, or whose sensors are missing, goes through the download path below.
        recorded = await find_source_timeline(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            storage_key=run.source.storage_key,
            size_bytes=run.source.size_bytes,
            observed=observed,
        )
        source_path: Path | None = None
        if recorded is not None:
            source_sha, timeline = recorded
        else:
            source_path, source_sha = await self._source_path(run, observed=observed)
            timeline = await inspect_timeline(source_path, ffprobe=self.ctx.settings.ffprobe)
        stored = await obs.get_async(self.ctx.store, run.transcript.storage_key)
        body = bytes(await stored.bytes_async())
        if len(body) != run.transcript.size_bytes:
            raise RuntimeError("transcript size changed after run creation")
        transcript_sha = hashlib.sha256(body).hexdigest()
        if run.transcript.sha256 is not None and transcript_sha != run.transcript.sha256:
            raise RuntimeError("transcript hash changed after run creation")
        transcript = TranscriptV1.model_validate_json(body)
        if abs(timeline.duration * 1000 - transcript.durationMs) > 1:
            raise RuntimeError("source timeline duration differs from its pinned transcript")
        source_fingerprint = self._source_fingerprint(
            scope, run, timeline, source_sha=source_sha, observed=observed
        )
        source_object = {
            "etag": observed.get("etag"),
            "key": run.source.storage_key,
            "sha256": source_sha,
            "sizeBytes": run.source.size_bytes,
            "versionId": observed.get("versionId"),
        }
        shot_record = None
        if is_topic_policy(run.editorial_policy):
            shot_record = await find_source_shot_evidence(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                source_object=source_object,
                timeline=timeline,
                ffmpeg=self.ctx.settings.ffmpeg,
                detector=run.topic_shot_detector,
            )
            if shot_record is None:
                # No record for this master yet: fetch it once and measure here.
                if source_path is None:
                    source_path, _ = await self._source_path(
                        run, observed=observed, expected_sha256=source_sha
                    )
                shot_record = await build_source_shot_evidence(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    store=self.ctx.store,
                    source_path=source_path,
                    source_object=source_object,
                    timeline=timeline,
                    ffmpeg=self.ctx.settings.ffmpeg,
                    detector=run.topic_shot_detector,
                )
        layers = await asyncio.to_thread(
            make_segmenter(request.segmenter).segment,
            transcript.words,
            **({"shot_times_ms": shot_record.shot_times_ms} if shot_record is not None else {}),
        )
        speech = None
        if is_topic_policy(run.editorial_policy):
            speech = await find_source_speech_coverage(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                source_object=source_object,
                timeline=timeline,
                transcript=transcript,
                detector_path=self.ctx.settings.transcription.speech_vad_model_path,
                ffmpeg=self.ctx.settings.ffmpeg,
            )
            if speech is None:
                if source_path is None:
                    source_path, _ = await self._source_path(
                        run, observed=observed, expected_sha256=source_sha
                    )
                speech = await build_source_speech_coverage(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    store=self.ctx.store,
                    source_path=source_path,
                    source_object=source_object,
                    timeline=timeline,
                    transcript=transcript,
                    detector_path=self.ctx.settings.transcription.speech_vad_model_path,
                    ffmpeg=self.ctx.settings.ffmpeg,
                )
        evidence = build_evidence(
            transcript,
            layers,
            source_id=ref.source_id,
            transcript_id=run.transcript.transcript_id,
            transcript_revision=run.transcript.revision,
            transcript_sha256=transcript_sha,
            source_fingerprint=source_fingerprint,
            frame_rate=self._positive_rational(timeline.frame_rate),
            video_time_base=self._positive_rational(timeline.video_time_base),
            audio_sample_rate=timeline.sample_rate,
            source_start=SignedRationalTime(
                numerator=timeline.source_start.numerator,
                denominator=timeline.source_start.denominator,
            ),
            annotations=run.transcript.annotations,
            machine_revision=run.transcript.machine_revision,
            legacy_speaker_labels=run.transcript.legacy_speaker_labels,
            speech_coverage=speech.coverage if speech is not None else None,
            assess_source_edges=False or is_topic_policy(run.editorial_policy),
            shots=shot_record.shots if shot_record is not None else (),
            config={
                "activity": "build_chapter_evidence",
                "detectedLanguage": transcript.language,
                "segmentationPolicy": "universal-sat-multilingual",
                "segmenter": request.segmenter,
                "sourceObject": source_object,
                "sourceTimeline": timeline_identity(timeline),
                **({"speechEvidence": dict(speech.provenance)} if speech is not None else {}),
                **(
                    {"shotEvidence": dict(shot_record.provenance)}
                    if shot_record is not None
                    else {}
                ),
            },
        )
        if is_topic_policy(run.editorial_policy):
            evidence = augment_topic_evidence(evidence)
        fingerprint = artifacts.fingerprint_for(
            kind="evidence",
            inputs={
                "sourceFingerprint": source_fingerprint,
                "transcriptId": str(run.transcript.transcript_id),
                "transcriptRevision": run.transcript.revision,
                "transcriptSha256": transcript_sha,
            },
            config=evidence.config,
        )
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(
                kind="evidence",
                fingerprint=fingerprint,
                transcript_id=run.transcript.transcript_id,
                transcript_revision=run.transcript.revision,
            ),
            content=evidence.model_dump(mode="json"),
            metadata={
                "format": "harness-evidence/1",
                "sourceFingerprint": source_fingerprint,
                "sourceSha256": source_sha,
                "transcriptSha256": transcript_sha,
            },
            dependency_ids=tuple(
                item.artifact_id for item in (speech, shot_record) if item is not None
            ),
        )
        await runs.attach_evidence(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            artifact_id=accepted.id,
        )
        artifact_ref = self._artifact_ref(accepted)
        return EvidenceResult(
            artifact=artifact_ref,
            sentence_count=len(evidence.sentences),
            word_count=len(evidence.words),
            duration_ms=evidence.durationMs,
            lexical_state="present" if evidence.words else "empty",
        )

    async def _assert_review_media_eligible(
        self,
        command: ChapterReviewInput,
        edit_sha256: str,
        evidence: HarnessEvidence,
        edit: ChapterEditSpec,
    ) -> None:
        target = next(
            (section for section in edit.sections if section.id == command.sectionId),
            None,
        )
        if target is None:
            raise ReviewRefused("section is absent from the current edit")
        if target.kind == "drop":
            return
        async with db.scoped(self.ctx.settings.database_url, command.scope) as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id FROM harness_artifact
                     WHERE source_id = %s AND kind = 'render'
                       AND metadata->>'format' = 'chapter-renders/1'
                       AND metadata->>'runId' = %s
                       AND metadata->>'editSha256' = %s
                     ORDER BY created_at DESC LIMIT 1
                    """,
                    (command.sourceId, str(command.runId), edit_sha256),
                )
            ).fetchone()
        if row is None:
            raise ReviewRefused("accept requires rendered media for the current edit")
        raw = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=command.scope,
            source_id=command.sourceId,
            store=self.ctx.store,
            artifact_id=row["id"],
        )
        descriptor = ChapterRenders.model_validate(raw)
        selected = [
            render for render in descriptor.renders if render.sectionId == command.sectionId
        ]
        if len(selected) != 1 or selected[0].checks is None:
            raise ReviewRefused("accept requires checked media for the named keep section")
        checks_raw = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=command.scope,
            source_id=command.sourceId,
            store=self.ctx.store,
            artifact_id=selected[0].checks.id,
        )
        checks = ChapterChecks.model_validate(checks_raw)
        _, _, timeline_json = self._evidence_source(evidence)
        if not technical_checks_pass(
            checks,
            source=self._timeline_from_identity(timeline_json),
        ):
            raise ReviewRefused("accept requires every applicable technical check to pass")

    @staticmethod
    def _timeline_from_identity(value: dict[str, object]) -> MediaTimelineFacts:
        return timeline_from_identity(value)

    @activity.defn(name="render_chapter_revision")
    async def render_chapter_revision(self, request: RenderRevisionRequest) -> RenderRevisionResult:
        """Lease one run's cache while rendering and clean every revision workspace."""

        async def operation() -> RenderRevisionResult:
            self._require_enabled()
            run = await self._assert_render_active(request.run, request.revision)
            workspace = self._render_workspace(run, request)
            self._cancel_source_cache_expiry(run.id)
            succeeded = False
            try:
                async with self._source_cache_lease(run.id):
                    try:
                        result = await self._render_chapter_revision_locked(request, run)
                    finally:
                        shutil.rmtree(workspace, ignore_errors=True)
                succeeded = True
                return result
            finally:
                if not succeeded:
                    self._arm_source_cache_expiry(run.id)

        return await run_with_activity_heartbeat(
            operation,
            details={"stage": "render-chapter-revision"},
        )

    async def _render_missing_sections_remotely(  # noqa: PLR0913
        self,
        request: RenderRevisionRequest,
        run: RunSnapshot,
        scope: Scope,
        *,
        evidence: HarnessEvidence,
        sections: Sequence[RenderSection],
        timeline: MediaTimelineFacts,
        config: ChapterRenderConfig,
        source_sha: str,
        workspace: Path,
    ) -> None:
        """One Modal call renders every section without a media artifact; outputs land locally.

        The call id rides on the heartbeat, so a retried activity reattaches instead of
        rendering twice. Each output is verified by size in the store and by hash after
        download; the per-section loop then publishes it exactly as a local render.
        """
        ref = request.run
        missing: list[RenderSection] = []
        for section in sections:
            identity = artifacts.ArtifactIdentity(
                kind="render",
                fingerprint=media_fingerprint(
                    source_fingerprint=evidence.sourceFingerprint,
                    section=section,
                    timeline=timeline,
                    config=config,
                ),
            )
            existing = await artifacts.find_artifact(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                identity=identity,
            )
            if existing is None:
                missing.append(section)
        if not missing:
            return
        prefix = (
            f"org/{scope.organizationId}/source/{ref.source_id}/harness/remote-render/"
            f"{ref.run_id}/{request.revision}/"
        )
        job = RenderJob(
            master_key=run.source.storage_key,
            master_sha256=source_sha,
            size_bytes=run.source.size_bytes,
            timeline=timeline_identity(timeline),
            config=asdict(config),
            sections=[
                RenderSectionJob(
                    section_id=section.section_id,
                    start_numerator=section.start.numerator,
                    start_denominator=section.start.denominator,
                    end_numerator=section.end.numerator,
                    end_denominator=section.end.denominator,
                    output_key=f"{prefix}{hashlib.sha256(section.section_id.encode()).hexdigest()[:16]}.mp4",
                )
                for section in missing
            ],
            expected_seconds=float(sum(section.duration for section in missing)),
        )
        resume = None
        for detail in activity.info().heartbeat_details if activity.in_activity() else ():
            if isinstance(detail, dict) and isinstance(detail.get("renderCallId"), str):  # pyright: ignore[reportUnknownMemberType]
                resume = str(detail["renderCallId"])  # pyright: ignore[reportUnknownArgumentType]

        async def on_progress(note: RenderProgress, call_id: str) -> None:
            activity.heartbeat(
                {
                    "stage": "render-remote",
                    "renderCallId": call_id,
                    "sectionId": note.section_id,
                    "progress": note.percent,
                }
            )
            await self._assert_render_active(ref, request.revision)

        renderer = ModalRenderer(
            RealRenderClient(
                self.ctx.settings.transcode, self.harness_settings.render_progress_dict
            ),
            self.ctx.store,
        )
        result = await renderer.run(job, on_progress=on_progress, resume=resume)
        by_section = {output.section_id: output for output in result.outputs}
        for section in missing:
            section_hash = hashlib.sha256(section.section_id.encode()).hexdigest()
            await download_output(
                self.ctx.store,
                by_section[section.section_id],
                workspace / section_hash[:16] / "chapter.mp4",
            )

    async def _render_chapter_revision_locked(  # noqa: C901, PLR0915
        self, request: RenderRevisionRequest, run: RunSnapshot, *, retain_source_cache: bool = False
    ) -> RenderRevisionResult:
        """Render, inspect, and publish every current keep against frozen source bytes."""
        ref = request.run
        scope = Scope(
            organizationId=ref.scope_organization_id,
            userId=ref.scope_user_id,
        )
        if request.edit.kind != HarnessArtifactKind.edit:
            raise RuntimeError("render request must name an edit artifact")
        edit_raw = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=request.edit.id,
        )
        edit = self._validate_render_edit(run, request, edit_raw)
        evidence_raw = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=edit.evidenceArtifactId,
        )
        evidence = HarnessEvidence.model_validate(evidence_raw)
        expected_source_sha, expected_object, expected_timeline = self._evidence_source(evidence)
        sections = kept_sections(edit)
        workspace = self._render_workspace(run, request)
        required = required_disk_bytes(
            source_size_bytes=run.source.size_bytes,
            source_duration=Fraction(run.source.duration_ms, 1000),
            sections=sections,
        )
        preflight_disk(workspace, required)
        head = await obs.head_async(self.ctx.store, run.source.storage_key)
        if int(head["size"]) != run.source.size_bytes:  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            raise RuntimeError("source master size changed before chapter rendering")
        observed = self._object_identity(head)
        self._validate_frozen_object(expected_object, observed)
        source_path, source_sha = await self._source_path(
            run,
            observed=observed,
            expected_sha256=expected_source_sha,
        )
        if source_sha != expected_source_sha:
            raise RuntimeError("source master differs from the evidence source hash")
        timeline = await inspect_timeline(source_path, ffprobe=self.ctx.settings.ffprobe)
        if timeline_identity(timeline) != expected_timeline:
            raise RuntimeError("source master timeline differs from frozen evidence")
        expected_fingerprint = self._source_fingerprint(
            scope,
            run,
            timeline,
            source_sha=source_sha,
            observed=expected_object,
        )
        if expected_fingerprint != evidence.sourceFingerprint:
            raise RuntimeError("source master identity differs from frozen evidence")

        config = ChapterRenderConfig(
            video_codec=self.harness_settings.render_encoder,
            video_preset=(
                "p5" if self.harness_settings.render_encoder == "h264_nvenc" else "medium"
            ),
        )
        if self.harness_settings.render_backend == "modal":
            try:
                await self._render_missing_sections_remotely(
                    request,
                    run,
                    scope,
                    evidence=evidence,
                    sections=sections,
                    timeline=timeline,
                    config=config,
                    source_sha=source_sha,
                    workspace=workspace,
                )
            except RenderFunctionAbsent as absent:
                # The app deploys from main on its own; until it lands, this worker encodes
                # here with the CPU encoder, as a different media artifact.
                log.warning("%s; rendering on this worker with libx264 instead", absent)
                config = ChapterRenderConfig()

        async def one(  # noqa: PLR0915
            section: RenderSection,
        ) -> tuple[ChapterRender, ChapterChecks]:
            async with self._render_semaphore:
                await self._assert_render_active(ref, request.revision)
                section_hash = hashlib.sha256(section.section_id.encode()).hexdigest()
                section_dir = workspace / section_hash[:16]
                section_dir.mkdir(parents=True, exist_ok=True)
                media_path = section_dir / "chapter.mp4"
                captions_path = section_dir / "captions.vtt"

                media_identity = artifacts.ArtifactIdentity(
                    kind="render",
                    fingerprint=media_fingerprint(
                        source_fingerprint=evidence.sourceFingerprint,
                        section=section,
                        timeline=timeline,
                        config=config,
                    ),
                )
                media_operation = await ledger.acquire_operation(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    run_id=ref.run_id,
                    kind=ledger.OperationKind.RENDER,
                    stage=f"render:media:{media_identity.fingerprint}",
                    inputs={
                        "sourceFingerprint": evidence.sourceFingerprint,
                        "sectionStart": str(section.start),
                        "sectionEnd": str(section.end),
                    },
                    config={"rendererVersion": RENDERER_VERSION},
                )
                media = await artifacts.find_artifact(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    identity=media_identity,
                )
                if media is None and media_path.is_file():
                    # Rendered on the card and verified by hash on download.
                    await self._assert_render_active(ref, request.revision)
                    media = await artifacts.publish_file(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        identity=media_identity,
                        path=media_path,
                        content_type="video/mp4",
                        suffix=".mp4",
                        metadata=media_metadata(
                            section=section,
                            timeline=timeline,
                            renderer_version=RENDERER_VERSION,
                            source_fingerprint=evidence.sourceFingerprint,
                        ),
                    )
                elif media is None:
                    last_cancel_check = 0.0

                    async def progress(value: float) -> None:
                        nonlocal last_cancel_check
                        activity.heartbeat(
                            {
                                "stage": "render-chapter",
                                "sectionId": section.section_id,
                                "progress": value,
                            }
                        )
                        now = time.monotonic()
                        if now - last_cancel_check >= CANCELLATION_POLL_SECONDS:
                            await self._assert_render_active(ref, request.revision)
                            if shutil.disk_usage(workspace).free < MIN_RUNTIME_DISK_FREE_BYTES:
                                raise OSError(
                                    "chapter render stopped before exhausting workspace disk"
                                )
                            last_cancel_check = now

                    await render_chapter(
                        self.ctx.settings.ffmpeg,
                        source_path,
                        media_path,
                        start=section.start,
                        end=section.end,
                        timeline=timeline,
                        config=config,
                        on_progress=progress,
                        timeout_seconds=4 * 60 * 60,
                    )
                    await self._assert_render_active(ref, request.revision)
                    media = await artifacts.publish_file(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        identity=media_identity,
                        path=media_path,
                        content_type="video/mp4",
                        suffix=".mp4",
                        metadata=media_metadata(
                            section=section,
                            timeline=timeline,
                            renderer_version=RENDERER_VERSION,
                            source_fingerprint=evidence.sourceFingerprint,
                        ),
                    )
                else:
                    await artifacts.read_artifact_file(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        artifact_id=media.id,
                        destination=media_path,
                    )
                await ledger.complete_operation_from_artifact(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    run_id=ref.run_id,
                    operation_id=media_operation.operation.id,
                    result_artifact_id=media.id,
                    expected_artifact_kind="render",
                    expected_artifact_fingerprint=media_identity.fingerprint,
                )

                caption_identity = artifacts.ArtifactIdentity(
                    kind="render",
                    fingerprint=caption_fingerprint(
                        evidence=evidence,
                        evidence_sha256=edit.evidenceSha256,
                        section=section,
                    ),
                )
                captions = await artifacts.find_artifact(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    identity=caption_identity,
                )
                document = None
                if captions is None:
                    document = write_captions(captions_path, evidence, section)
                    captions = await artifacts.publish_file(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        identity=caption_identity,
                        path=captions_path,
                        content_type="text/vtt",
                        suffix=".vtt",
                        metadata=caption_metadata(
                            section=section,
                            evidence_sha256=edit.evidenceSha256,
                        ),
                        dependency_ids=(edit.evidenceArtifactId,),
                    )
                else:
                    await artifacts.read_artifact_file(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        artifact_id=captions.id,
                        destination=captions_path,
                    )

                checks_identity = artifacts.ArtifactIdentity(
                    kind="checks",
                    fingerprint=artifacts.fingerprint_for(
                        kind="chapter_checks",
                        inputs={
                            "captionArtifactId": str(captions.id),
                            "captionSha256": captions.sha256,
                            "editArtifactId": str(request.edit.id),
                            "editSha256": request.edit.sha256,
                            "mediaArtifactId": str(media.id),
                            "mediaSha256": media.sha256,
                            "sectionId": section.section_id,
                        },
                        config={"checksVersion": 1},
                    ),
                )
                checks_operation = await ledger.acquire_operation(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    run_id=ref.run_id,
                    kind=ledger.OperationKind.CHECK,
                    stage=f"checks:{request.revision}:{section.section_id}",
                    inputs={
                        "editArtifactId": str(request.edit.id),
                        "mediaArtifactId": str(media.id),
                        "captionArtifactId": str(captions.id),
                    },
                    config={"checksVersion": 1},
                )
                checks_artifact = await artifacts.find_artifact(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    identity=checks_identity,
                )
                if checks_artifact is None:
                    checks = await check_chapter_media(
                        media_path,
                        section_id=section.section_id,
                        edit_sha256=request.edit.sha256,
                        source=timeline,
                        start=section.start,
                        end=section.end,
                        ffmpeg=self.ctx.settings.ffmpeg,
                        ffprobe=self.ctx.settings.ffprobe,
                        timeout_seconds=60 * 60,
                    )
                    caption_check = check_chapter_captions(
                        captions_path,
                        section_id=section.section_id,
                        duration=section.duration,
                    )
                    technical = [*checks.technicalChecks, caption_check]
                    if document is not None:
                        technical.extend(
                            ChapterCheck(
                                expected=None,
                                measured=None,
                                message=warning,
                                name="caption_cut_word",
                                sectionId=section.section_id,
                                status=Status.warn,
                            )
                            for warning in document.warnings
                        )
                    checks = checks.model_copy(update={"technicalChecks": technical})
                    checks_artifact = await artifacts.publish_json(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        identity=checks_identity,
                        content=checks.model_dump(mode="json"),
                        metadata=checks_metadata(
                            section_id=section.section_id,
                            edit_sha256=request.edit.sha256,
                        ),
                        dependency_ids=(request.edit.id, media.id, captions.id),
                    )
                else:
                    raw_checks = await artifacts.read_artifact_json(
                        self.ctx.settings.database_url,
                        scope=scope,
                        source_id=ref.source_id,
                        store=self.ctx.store,
                        artifact_id=checks_artifact.id,
                    )
                    checks = ChapterChecks.model_validate(raw_checks)
                await ledger.complete_operation_from_artifact(
                    self.ctx.settings.database_url,
                    scope=scope,
                    source_id=ref.source_id,
                    run_id=ref.run_id,
                    operation_id=checks_operation.operation.id,
                    result_artifact_id=checks_artifact.id,
                    expected_artifact_kind="checks",
                    expected_artifact_fingerprint=checks_identity.fingerprint,
                )
                return (
                    ChapterRender(
                        captions=self._artifact_ref(captions),
                        checks=self._artifact_ref(checks_artifact),
                        durationMs=rounded_milliseconds(section.duration),
                        editSha256=request.edit.sha256,
                        media=self._artifact_ref(media),
                        sectionId=section.section_id,
                    ),
                    checks,
                )

        try:
            completed = await run_render_batch(
                tuple(functools.partial(one, section) for section in sections)
            )
            renders = tuple(item[0] for item in completed)
            check_results = tuple(item[1] for item in completed)
            descriptor = build_render_descriptor(
                run_id=ref.run_id,
                edit_sha256=request.edit.sha256,
                renders=renders,
            )
            descriptor_identity = artifacts.ArtifactIdentity(
                kind="render",
                fingerprint=descriptor_fingerprint(
                    edit_sha256=request.edit.sha256,
                    renders=renders,
                    renderer_version=RENDERER_VERSION,
                ),
            )
            accepted_descriptor = await artifacts.publish_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                identity=descriptor_identity,
                content=descriptor.model_dump(mode="json"),
                metadata=descriptor_metadata(
                    run_id=ref.run_id,
                    edit_sha256=request.edit.sha256,
                    render_count=len(renders),
                ),
                dependency_ids=tuple(
                    dict.fromkeys(
                        [
                            request.edit.id,
                            *(render.media.id for render in renders),
                            *(render.captions.id for render in renders if render.captions),
                            *(render.checks.id for render in renders if render.checks),
                        ]
                    )
                ),
            )
            if not retain_source_cache:
                self._cleanup_source_cache_held(run.id)
            return RenderRevisionResult(
                descriptor=self._artifact_ref(accepted_descriptor),
                has_kept_sections=bool(sections),
                technical_report=tuple(checks.model_dump(mode="json") for checks in check_results),
                technical_passed=all(
                    technical_checks_pass(checks, source=timeline) for checks in check_results
                ),
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    async def _assert_render_active(self, ref: RunRef, revision: int) -> RunSnapshot:
        run = await runs.get_run(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=ref.scope_organization_id,
                userId=ref.scope_user_id,
            ),
            source_id=ref.source_id,
            run_id=ref.run_id,
        )

        if run.status == HarnessRunStatus.cancelled:
            raise asyncio.CancelledError("chapter run was cancelled")
        if run.status in {
            HarnessRunStatus.failed,
            HarnessRunStatus.outcome_unknown,
            HarnessRunStatus.ready,
        }:
            message = f"chapter render refused terminal run status {run.status}"
            raise RuntimeError(message)
        if run.current_revision != revision:
            raise RuntimeError("chapter render revision is stale")
        return run

    @staticmethod
    def _validate_render_edit(
        run: RunSnapshot, request: RenderRevisionRequest, raw: object
    ) -> ChapterEditSpec:
        edit = ChapterEditSpec.model_validate(raw)
        if edit.sourceId != run.source_id:
            raise RuntimeError("chapter edit belongs to a different source")
        if edit.evidenceArtifactId != run.evidence_artifact_id:
            raise RuntimeError("chapter edit names stale evidence")
        actual_sha = hashlib.sha256(artifacts.canonical_json(raw)).hexdigest()
        if actual_sha != request.edit.sha256:
            raise RuntimeError("chapter edit bytes differ from the supplied artifact hash")
        return edit

    @staticmethod
    def _evidence_source(
        evidence: HarnessEvidence,
    ) -> tuple[str, dict[str, str | None], dict[str, object]]:
        raw_object = evidence.config.get("sourceObject")
        raw_timeline = evidence.config.get("sourceTimeline")
        if not isinstance(raw_object, dict) or not isinstance(raw_timeline, dict):
            raise TypeError("evidence does not contain a frozen source identity")
        source_object = cast("dict[str, object]", raw_object)
        sha256 = source_object.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != SHA256_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise RuntimeError("evidence source SHA-256 is invalid")
        expected = {
            "etag": (str(source_object["etag"]) if source_object.get("etag") is not None else None),
            "versionId": (
                str(source_object["versionId"])
                if source_object.get("versionId") is not None
                else None
            ),
        }
        return sha256, expected, cast("dict[str, object]", raw_timeline)

    @staticmethod
    def _validate_frozen_object(
        expected: dict[str, str | None], observed: dict[str, str | None]
    ) -> None:
        for name in ("etag", "versionId"):
            if expected[name] is not None and expected[name] != observed[name]:
                message = f"source master {name} differs from frozen evidence"
                raise RuntimeError(message)

    @staticmethod
    def _object_identity(metadata: object) -> dict[str, str | None]:
        return object_identity(metadata)

    @staticmethod
    def _validate_pinned_object(run: RunSnapshot, observed: dict[str, str | None]) -> None:
        if (
            run.source.etag is not None
            and observed["etag"] is not None
            and run.source.etag != observed["etag"]
        ):
            raise RuntimeError("source master ETag changed after run creation")
        if (
            run.source.version_id is not None
            and observed["versionId"] is not None
            and run.source.version_id != observed["versionId"]
        ):
            raise RuntimeError("source master version changed after run creation")

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _render_workspace(self, run: RunSnapshot, request: RenderRevisionRequest) -> Path:
        return (
            self.ctx.settings.work_root
            / "harness"
            / str(run.id)
            / "render"
            / f"revision-{request.revision}-{request.edit.sha256[:16]}"
        )

    def _source_lease_path(self, run_id: object) -> Path:
        root = self.ctx.settings.work_root / "harness" / ".leases"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{run_id}.lock"

    def _try_source_cache_lease(self, run_id: object) -> BinaryIO | None:
        handle = self._source_lease_path(run_id).open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    @contextlib.asynccontextmanager
    async def _source_cache_lease(self, run_id: object) -> AsyncGenerator[None]:
        handle = self._try_source_cache_lease(run_id)
        while handle is None:
            activity.heartbeat({"stage": "waiting-for-source-cache-lease"})
            await asyncio.sleep(1)
            handle = self._try_source_cache_lease(run_id)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _cancel_source_cache_expiry(self, run_id: object) -> None:
        task = self._source_expiry_tasks.pop(str(run_id), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _cleanup_source_cache_held(self, run_id: object) -> None:
        self._cancel_source_cache_expiry(run_id)
        directory = self.ctx.settings.work_root / "harness" / str(run_id) / "source"
        shutil.rmtree(directory, ignore_errors=True)

    def _try_cleanup_source_cache(self, run_id: object) -> bool:
        handle = self._try_source_cache_lease(run_id)
        if handle is None:
            return False
        try:
            self._cleanup_source_cache_held(run_id)
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _arm_source_cache_expiry(
        self, run_id: object, *, delay_seconds: float = HARNESS_WORKSPACE_TTL_SECONDS
    ) -> None:
        directory = self.ctx.settings.work_root / "harness" / str(run_id) / "source"
        if not directory.is_dir():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cancel_source_cache_expiry(run_id)

        async def expire() -> None:
            await asyncio.sleep(max(0.0, delay_seconds))
            while not self._try_cleanup_source_cache(run_id):  # noqa: ASYNC110
                await asyncio.sleep(CANCELLATION_POLL_SECONDS)

        task = asyncio.create_task(expire(), name=f"source-cache-expiry-{run_id}")
        self._source_expiry_tasks[str(run_id)] = task

        def discard(completed: asyncio.Task[None]) -> None:
            if self._source_expiry_tasks.get(str(run_id)) is completed:
                self._source_expiry_tasks.pop(str(run_id), None)

        task.add_done_callback(discard)

    def _arm_existing_source_cache_expiries(self) -> None:
        root = self.ctx.settings.work_root / "harness"
        if not root.is_dir():
            return
        now = time.time()
        for run_directory in root.iterdir():
            source = run_directory / "source"
            if not source.is_dir():
                continue
            with contextlib.suppress(OSError):
                delay = source.stat().st_mtime + HARNESS_WORKSPACE_TTL_SECONDS - now
                self._arm_source_cache_expiry(
                    run_directory.name,
                    delay_seconds=max(0.0, delay),
                )

    def _cleanup_stale_harness_workspaces(self, active_run_id: object | None) -> None:
        root = self.ctx.settings.work_root / "harness"
        if not root.is_dir():
            return
        cutoff = time.time() - HARNESS_WORKSPACE_TTL_SECONDS
        for directory in root.iterdir():
            if directory.name in {".leases", str(active_run_id)} or not directory.is_dir():
                continue
            handle = None
            with contextlib.suppress(OSError):
                if directory.stat().st_mtime < cutoff:
                    handle = self._try_source_cache_lease(directory.name)
                if handle is not None:
                    shutil.rmtree(directory, ignore_errors=True)
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    def _ensure_source_capacity(self, run: RunSnapshot, directory: Path) -> None:
        required = run.source.size_bytes + MIN_RUNTIME_DISK_FREE_BYTES
        self._cleanup_stale_harness_workspaces(active_run_id=run.id)
        free = shutil.disk_usage(directory).free
        if free < required:
            root = self.ctx.settings.work_root / "harness"
            candidates: list[tuple[float, Path]] = []
            for run_directory in root.iterdir():
                source = run_directory / "source"
                if run_directory.name != str(run.id) and source.is_dir():
                    with contextlib.suppress(OSError):
                        candidates.append((source.stat().st_mtime, source))
            for _, source in sorted(candidates):
                handle = self._try_source_cache_lease(source.parent.name)
                if handle is None:
                    continue
                try:
                    shutil.rmtree(source, ignore_errors=True)
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()
                free = shutil.disk_usage(directory).free
                if free >= required:
                    break
        if free < required:
            message = f"workspace operation needs {required} bytes; {free} bytes are free"
            raise OSError(message)

    async def _source_path(  # noqa: C901, PLR0912, PLR0915
        self,
        run: RunSnapshot,
        *,
        observed: dict[str, str | None],
        expected_sha256: str | None = None,
    ) -> tuple[Path, str]:
        """Stream and hash the real master; reuse only with a stable object identity."""
        directory = self.ctx.settings.work_root / "harness" / str(run.id) / "source"
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(run.source.storage_key).suffix.lower()
        if not suffix or len(suffix) > MAX_SOURCE_SUFFIX_LENGTH:
            suffix = ".bin"
        master = directory / f"master{suffix}"
        sidecar = directory / "master.identity.json"
        stable_identity = observed["etag"] is not None or observed["versionId"] is not None
        if master.is_file() and sidecar.is_file() and stable_identity:
            try:
                cached = cast("object", json.loads(sidecar.read_bytes()))
            except (OSError, ValueError):
                cached = None
            if isinstance(cached, dict):
                cached_values = cast("dict[str, object]", cached)
                matches_identity = (
                    cached_values.get("key") == run.source.storage_key
                    and cached_values.get("sizeBytes") == run.source.size_bytes
                    and cached_values.get("etag") == observed["etag"]
                    and cached_values.get("versionId") == observed["versionId"]
                )
                if matches_identity:
                    sha256, size = await asyncio.to_thread(self._hash_file, master)
                    if size == run.source.size_bytes and sha256 == cached_values.get("sha256"):
                        if expected_sha256 is not None and sha256 != expected_sha256:
                            raise RuntimeError(
                                "cached source bytes differ from the evidence source hash"
                            )
                        return master, sha256

        self._ensure_source_capacity(run, directory)

        token = hashlib.sha256(
            f"{activity.info().activity_id}:{time.monotonic_ns()}".encode()
        ).hexdigest()[:16]
        partial = directory / f"master.{token}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                async with asyncio.timeout(SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS):
                    result = await obs.get_async(self.ctx.store, run.source.storage_key)
            except TimeoutError as error:
                raise TimeoutError("source download header timed out") from error
            if int(result.meta["size"]) != run.source.size_bytes:
                raise RuntimeError("source master changed before its verified download")
            downloaded_identity = self._object_identity(result.meta)
            if stable_identity and downloaded_identity != observed:
                raise RuntimeError("source master changed during its verified download")
            chunks = result.stream(min_chunk_size=8 * 1024 * 1024).__aiter__()
            with partial.open("wb") as handle:
                while True:
                    try:
                        async with asyncio.timeout(SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS):
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as error:
                        raise TimeoutError("source download chunk timed out") from error
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if shutil.disk_usage(directory).free < MIN_RUNTIME_DISK_FREE_BYTES:
                        raise OSError("source download stopped before exhausting workspace disk")
                    activity.heartbeat(
                        {
                            "stage": "download-source",
                            "bytes": size,
                            "totalBytes": run.source.size_bytes,
                        }
                    )
            if size != run.source.size_bytes:
                raise RuntimeError("source master download ended at the wrong size")
            sha256 = digest.hexdigest()
            if expected_sha256 is not None and sha256 != expected_sha256:
                raise RuntimeError("source master bytes differ from the evidence source hash")
            partial.replace(master)
            identity = {
                "etag": observed["etag"],
                "key": run.source.storage_key,
                "sha256": sha256,
                "sizeBytes": size,
                "versionId": observed["versionId"],
            }
            sidecar_partial = directory / f"master.identity.{token}.part"
            sidecar_partial.write_bytes(artifacts.canonical_json(identity))
            sidecar_partial.replace(sidecar)
            return master, sha256
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _source_fingerprint(
        scope: Scope,
        run: RunSnapshot,
        timeline: MediaTimelineFacts,
        *,
        source_sha: str,
        observed: dict[str, str | None],
    ) -> str:
        return artifacts.fingerprint_for(
            kind="source_master",
            inputs={
                "organizationId": str(scope.organizationId),
                "sourceId": str(run.source_id),
                "storageKey": run.source.storage_key,
                "sizeBytes": run.source.size_bytes,
                "sha256": source_sha,
                "etag": observed["etag"],
                "versionId": observed["versionId"],
                "timeline": timeline_identity(timeline),
            },
            config={"version": "source-master/1"},
        )

    @staticmethod
    def _positive_rational(value: Fraction | None) -> PositiveRational | None:
        if value is None:
            return None
        return PositiveRational(numerator=value.numerator, denominator=value.denominator)

    def _recorded_output(self, stage: str) -> dict[str, object] | None:
        if self.harness_settings.backend != "recorded":
            return None
        path = self.harness_settings.recorded_fixture_path
        if path is None:
            raise RuntimeError("recorded backend is missing its explicit fixture")
        body = path.read_bytes()
        if len(body) > 1024 * 1024:
            raise RuntimeError("recorded harness fixture exceeds 1 MiB")
        loaded = cast("object", json.loads(body))
        if (
            not isinstance(loaded, dict)
            or cast("dict[object, object]", loaded).get("synthetic") is not True
        ):
            raise RuntimeError("recorded harness fixture must declare synthetic=true")
        outputs = cast("dict[object, object]", loaded).get("outputs")
        if not isinstance(outputs, dict):
            raise TypeError("recorded harness fixture outputs must be an object")
        payload = cast("dict[object, object]", outputs).get(stage)
        if not isinstance(payload, dict):
            message = f"recorded harness fixture has no {stage} output"
            raise KeyError(message)
        payload = cast("dict[str, object]", payload)
        if payload.get("synthetic") is not True or "output" not in payload:
            raise RuntimeError("recorded output requires synthetic=true and output")
        return payload

    @staticmethod
    def _artifact_ref(accepted: artifacts.HarnessArtifact) -> HarnessArtifactRef:
        return HarnessArtifactRef(
            id=accepted.id,
            kind=HarnessArtifactKind(accepted.kind),
            fingerprint=accepted.fingerprint,
            sha256=accepted.sha256,
            sizeBytes=accepted.size_bytes,
            storageKey=accepted.storage_key,
        )

    def activities(self) -> Sequence[Callable[..., object]]:
        """Return heavy activities for the pipeline task queue."""
        return (
            *TopicRenderActivities(self).activities(),
            *TopicReviewActivities(self).activities(),
            *TopicSelectionActivities(self).activities(),
            *TopicDecisionActivities(self).activities(),
            *TopicEditorialPatchActivities(self).activities(),
            self.build_chapter_evidence,
            self.render_chapter_revision,
            self.cleanup_chapter_source_cache,
            self.reconcile_unknown_runs,
        )

    def control_activities(self) -> Sequence[Callable[..., object]]:
        """Return compact DB-only activities for the control task queue."""
        return (
            *TopicReviewActivities(self).control_activities(),
            self.start_chapter_run,
            self.get_chapter_run,
            self.get_chapter_resume_assets,
            self.update_chapter_run_stage,
            self.claim_chapter_repair,
            self.mark_chapter_run_failed,
            self.reconcile_chapter_run_costs,
            self.apply_chapter_review,
            self.accept_initial_chapter_revision,
            self.commit_chapter_review,
        )
