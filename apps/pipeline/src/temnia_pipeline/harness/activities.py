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
import shutil
import time
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import obstore as obs
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterCheck,
    ChapterChecks,
    ChapterEditSpec,
    ChapterExport,
    ChapterRender,
    ChapterRenders,
    ChapterReviewAction,
    ChapterReviewInput,
    ChapterReviewOutput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    PositiveRational,
    ReviewState,
    Scope,
    SignedRationalTime,
    Status,
    TranscriptV1,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.compiler import CompilerConfig, compile_chapters
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.harness.models import EditorialVerdictV1, HierarchicalSummaryV1
from temnia_pipeline.harness.prompts import (
    SUMMARIZE_PROMPT_VERSION,
    PromptSentence,
    PromptWindow,
    render_hierarchy_proposal_prompt,
    render_proposal_prompt,
    render_summary_prompt,
    render_summary_reduction_prompt,
    render_verifier_prompt,
)
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
    timeline_identity,
    write_captions,
)
from temnia_pipeline.harness.review import ReviewRefused, apply_review
from temnia_pipeline.harness.review_reuse import reuse_review_render
from temnia_pipeline.harness.routes import (
    ContextWindowExceeded,
    NoEligibleRoute,
    estimate_cost,
    select_route,
    select_verifier_route,
)
from temnia_pipeline.harness.runtime_types import (
    AcceptInitialRevisionRequest,
    BuildEvidenceRequest,
    ClaimRepairRequest,
    CommitReviewMutationRequest,
    CommitReviewMutationResult,
    CompiledRevision,
    CompileProposalRequest,
    CompileProposalResult,
    EvidenceResult,
    ExportRevisionRequest,
    ExportRevisionResult,
    FinalizeVerificationRequest,
    GlobalProposalPlan,
    MarkRunFailedRequest,
    PlanningWindow,
    PreparedReviewMutation,
    PrepareGlobalProposalRequest,
    PreparePlanningRequest,
    PrepareVerificationRequest,
    ProposalPlan,
    RenderRevisionRequest,
    RenderRevisionResult,
    ResumeRunAssets,
    ReuseRevisionRenderOutcome,
    ReuseRevisionRenderRequest,
    RunRef,
    RunSnapshot,
    StageUpdate,
    StartRunRequest,
    StartRunResult,
    ValidatedSummary,
    ValidateSummaryRequest,
    VerificationPlan,
)
from temnia_pipeline.harness.summary_grounding import (
    GROUNDING_FORMAT,
    SummaryGroundingRefusal,
    SummaryGroundingReport,
    allowed_anchors_from_exact_prompt,
    ground_summary,
    grounding_artifact_fingerprint,
    normalized_summary_from_response,
)
from temnia_pipeline.harness.validators import HarnessValidationError, rounded_milliseconds, word_id
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
from temnia_pipeline.speech.liveness import run_with_activity_heartbeat
from temnia_pipeline.substrate.factory import make_segmenter

CANCELLATION_POLL_SECONDS = 5
SOURCE_DOWNLOAD_QUIET_TIMEOUT_SECONDS = 3 * 60
SHA256_HEX_LENGTH = 64
MAX_SOURCE_SUFFIX_LENGTH = 12
MIN_RUNTIME_DISK_FREE_BYTES = 512 * 1024 * 1024
MAX_REVIEW_REVISIONS = 500
MAX_HIERARCHY_LEVEL = 8
HARNESS_WORKSPACE_TTL_SECONDS = 24 * 60 * 60

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence
    from uuid import UUID

    from temnia_pipeline.harness.routes import RouteSnapshot
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
    ) -> None:
        self.ctx = ctx
        self.harness_settings = harness_settings
        self.snapshot = snapshot
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

    async def _build_chapter_evidence_locked(
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
        layers = await asyncio.to_thread(
            make_segmenter(request.segmenter).segment, transcript.words
        )
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
            config={
                "activity": "build_chapter_evidence",
                "detectedLanguage": transcript.language,
                "segmentationPolicy": "universal-sat-multilingual",
                "segmenter": request.segmenter,
                "sourceObject": source_object,
                "sourceTimeline": timeline_identity(timeline),
            },
        )
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

    @activity.defn(name="prepare_chapter_proposal")
    async def prepare_chapter_proposal(self, request: PreparePlanningRequest) -> ProposalPlan:
        """Build one bounded prompt and check its route context before model dispatch."""
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
        if run.evidence_artifact_id != request.evidence.id:
            raise RuntimeError("planning request does not name the run's accepted evidence")
        loaded = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=request.evidence.id,
        )
        evidence = HarnessEvidence.model_validate(loaded)
        if not evidence.sentences:
            raise RuntimeError("transcript evidence is empty; chapter planning needs review")
        language_value = evidence.config.get("detectedLanguage")
        detected_language = (
            language_value.strip()
            if isinstance(language_value, str) and language_value.strip()
            else None
        )
        sentences = tuple(
            PromptSentence(
                id=sentence.id,
                text=sentence.text,
                firstWordId=word_id(sentence.wordIds[0]),
                lastWordId=word_id(sentence.wordIds[-1]),
                speakers=tuple(sentence.speakers),
            )
            for sentence in evidence.sentences
        )
        route = select_route(run.route_snapshot, "propose")
        summary_route = select_route(run.route_snapshot, "summary")
        windows: list[PromptWindow] = []
        first = 0
        while first < len(sentences):
            last = min(
                len(sentences),
                first + self.harness_settings.evidence_window_sentences,
            )
            accepted_window = False
            while last > first:
                candidate = PromptWindow(
                    sourceId=ref.source_id,
                    evidenceSha256=request.evidence.sha256,
                    windowId=f"window-{len(windows):04d}",
                    firstSentenceId=sentences[first].id,
                    lastSentenceId=sentences[last - 1].id,
                    sentences=sentences[first:last],
                )
                try:
                    candidate_prompt = render_summary_prompt(
                        candidate, detected_language=detected_language
                    )
                    estimate_cost(
                        summary_route,
                        payload_bytes=len(candidate_prompt.encode()),
                        max_output_tokens=self.harness_settings.max_output_tokens,
                    )
                    windows.append(candidate)
                    first = last
                    accepted_window = True
                    break
                except ContextWindowExceeded:
                    last -= 1
            if not accepted_window:
                raise RuntimeError(
                    "one complete sentence exceeds the qualified model context window"
                )
        direct = len(windows) == 1
        planning_windows = tuple(
            PlanningWindow(
                id=window.windowId,
                first_sentence_id=window.firstSentenceId,
                last_sentence_id=window.lastSentenceId,
                sentence_count=len(window.sentences),
                prompt=(
                    render_proposal_prompt(
                        window,
                        brief=run.brief,
                        detected_language=detected_language,
                    )
                    if direct
                    else render_summary_prompt(window, detected_language=detected_language)
                ),
            )
            for window in windows
        )
        if direct:
            estimate_cost(
                route,
                payload_bytes=len(planning_windows[0].prompt.encode()),
                max_output_tokens=self.harness_settings.max_output_tokens,
            )
        synthetic_payload = self._recorded_output("propose")
        return ProposalPlan(
            windows=planning_windows,
            route=route,
            summary_route=summary_route,
            synthetic_payload=synthetic_payload,
            synthetic_summary_payload=self._recorded_output("summary"),
        )

    async def _grounding_report(
        self,
        *,
        scope: Scope,
        source_id: UUID,
        reference: HarnessArtifactRef,
    ) -> SummaryGroundingReport:
        if reference.kind != HarnessArtifactKind.checks:
            raise RuntimeError("summary grounding lineage names the wrong artifact kind")
        accepted = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=source_id,
            identity=artifacts.ArtifactIdentity(kind="checks", fingerprint=reference.fingerprint),
        )
        if accepted is None or self._artifact_ref(accepted) != reference:
            raise RuntimeError("summary grounding artifact identity does not match storage")
        if accepted.metadata.get("format") != GROUNDING_FORMAT:
            raise RuntimeError("summary grounding artifact has the wrong format")
        loaded = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=source_id,
            store=self.ctx.store,
            artifact_id=reference.id,
        )
        report = SummaryGroundingReport.model_validate_json(artifacts.canonical_json(loaded))
        if (
            accepted.metadata.get("runId") != str(report.runId)
            or accepted.metadata.get("windowId") != report.windowId
            or accepted.metadata.get("hierarchyLevel") != report.hierarchyLevel
            or accepted.metadata.get("modelStage") != report.modelStage
            or accepted.metadata.get("fallbackUnitCount") != len(report.fallbacks)
            or accepted.metadata.get("fallbackQuoteCount")
            != sum(len(value.rejectedQuoteWordIds) for value in report.fallbacks)
            or grounding_artifact_fingerprint(report) != accepted.fingerprint
        ):
            raise RuntimeError("summary grounding artifact metadata differs from its body")
        expected_dependencies = {
            report.evidence.id,
            report.rawResponse.id,
            *(value.id for value in report.inputArtifacts),
        }
        if set(accepted.dependency_ids) != expected_dependencies or len(
            accepted.dependency_ids
        ) != len(expected_dependencies):
            raise RuntimeError("summary grounding artifact dependency lineage differs")
        return report

    @activity.defn(name="validate_chapter_summary")
    async def validate_chapter_summary(  # noqa: C901, PLR0912, PLR0915
        self, request: ValidateSummaryRequest
    ) -> ValidatedSummary:
        """Ground one retained summary and publish its immutable audit report."""
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
        if run.evidence_artifact_id != request.evidence.id:
            raise RuntimeError("summary validation does not name the run's accepted evidence")
        expected_stage = (
            f"summary:{request.window.id}"
            if request.hierarchy_level == 1
            else f"summary:level:{request.hierarchy_level}:{request.window.id}"
        )
        if request.model_stage != expected_stage:
            raise RuntimeError("summary validation stage does not match its window")
        loaded_evidence = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=request.evidence.id,
        )
        evidence = HarnessEvidence.model_validate(loaded_evidence)
        accepted_evidence = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            identity=artifacts.ArtifactIdentity(
                kind="evidence",
                fingerprint=request.evidence.fingerprint,
                transcript_id=evidence.transcriptId,
                transcript_revision=evidence.transcriptRevision,
            ),
        )
        if accepted_evidence is None or self._artifact_ref(accepted_evidence) != request.evidence:
            raise RuntimeError("summary evidence identity does not match storage")
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT o.id AS operation_id, a.fingerprint
                      FROM harness_operation o
                      JOIN harness_artifact a ON a.id = o.result_artifact_id
                     WHERE o.run_id = %s AND o.source_id = %s
                       AND o.kind = 'model' AND o.stage = %s
                       AND o.status = 'succeeded' AND a.kind = 'model_response'
                    """,
                    (ref.run_id, ref.source_id, request.model_stage),
                )
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("summary stage does not have one accepted model response")
        raw_response = await artifacts.find_artifact(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            identity=artifacts.ArtifactIdentity(
                kind="model_response", fingerprint=str(rows[0]["fingerprint"])
            ),
        )
        if raw_response is None:
            raise RuntimeError("accepted summary response artifact is absent")
        raw_ref = self._artifact_ref(raw_response)
        metadata = raw_response.metadata
        expected_raw_dependencies = {
            request.evidence.id,
            *(value.id for value in request.input_artifacts),
        }
        if (
            metadata.get("format") not in (None, "pydantic-ai-model-response-v1")
            or metadata.get("operationId") != str(rows[0]["operation_id"])
            or metadata.get("runId") != str(ref.run_id)
            or metadata.get("schemaVersion") != "hierarchical-summary/1"
            or metadata.get("promptVersion") != SUMMARIZE_PROMPT_VERSION
            or set(raw_response.dependency_ids) != expected_raw_dependencies
            or len(raw_response.dependency_ids) != len(expected_raw_dependencies)
        ):
            raise RuntimeError("accepted summary response lineage does not match its operation")
        raw_body = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=raw_ref.id,
        )
        try:
            supplied = HierarchicalSummaryV1.model_validate(request.summary)
        except ValueError:
            return ValidatedSummary(refusal="The summary response shape could not be verified.")
        try:
            retained = normalized_summary_from_response(raw_body)
        except SummaryGroundingRefusal as error:
            return ValidatedSummary(refusal=str(error))
        if supplied != retained:
            raise RuntimeError("summary payload differs from its retained model response")

        input_reports = tuple(
            [
                await self._grounding_report(
                    scope=scope,
                    source_id=ref.source_id,
                    reference=reference,
                )
                for reference in request.input_artifacts
            ]
        )
        if request.hierarchy_level == 1:
            if input_reports:
                raise RuntimeError("first-level summary cannot name prior grounding inputs")
        else:
            if not input_reports:
                raise RuntimeError("reduction summary requires prior grounding inputs")
            if any(
                report.runId != ref.run_id
                or report.evidence != request.evidence
                or report.hierarchyLevel != request.hierarchy_level - 1
                for report in input_reports
            ):
                raise RuntimeError("prior grounding lineage does not match the reduction")
        try:
            allowed_model_anchors = allowed_anchors_from_exact_prompt(
                evidence=evidence,
                evidence_sha256=request.evidence.sha256,
                source_id=ref.source_id,
                window_id=request.window.id,
                window_first_sentence_id=request.window.first_sentence_id,
                window_last_sentence_id=request.window.last_sentence_id,
                window_sentence_count=request.window.sentence_count,
                prompt=request.window.prompt,
                hierarchy_level=request.hierarchy_level,
                input_reports=input_reports,
            )
            grounded = ground_summary(
                evidence=evidence,
                window_first_sentence_id=request.window.first_sentence_id,
                window_last_sentence_id=request.window.last_sentence_id,
                window_sentence_count=request.window.sentence_count,
                summary=supplied,
                allowed_model_anchors=allowed_model_anchors,
            )
        except SummaryGroundingRefusal as error:
            return ValidatedSummary(refusal=str(error))

        source_summary_sha256 = hashlib.sha256(
            artifacts.canonical_json(supplied.model_dump(mode="json"))
        ).hexdigest()
        report = SummaryGroundingReport(
            runId=ref.run_id,
            hierarchyLevel=request.hierarchy_level,
            modelStage=request.model_stage,
            windowId=request.window.id,
            firstSentenceId=request.window.first_sentence_id,
            lastSentenceId=request.window.last_sentence_id,
            windowSentenceCount=request.window.sentence_count,
            windowPromptSha256=hashlib.sha256(request.window.prompt.encode()).hexdigest(),
            evidence=request.evidence,
            rawResponse=raw_ref,
            inputArtifacts=request.input_artifacts,
            sourceSummarySha256=source_summary_sha256,
            normalizedSummary=grounded.summary,
            fallbacks=grounded.fallbacks,
        )
        fingerprint = grounding_artifact_fingerprint(report)
        dependencies = (
            request.evidence.id,
            raw_ref.id,
            *(value.id for value in request.input_artifacts),
        )
        if len(set(dependencies)) != len(dependencies):
            raise RuntimeError("summary grounding dependencies must be unique")
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="checks", fingerprint=fingerprint),
            content=report.model_dump(mode="json"),
            metadata={
                "format": GROUNDING_FORMAT,
                "runId": str(ref.run_id),
                "windowId": request.window.id,
                "hierarchyLevel": request.hierarchy_level,
                "modelStage": request.model_stage,
                "fallbackUnitCount": len(grounded.fallbacks),
                "fallbackQuoteCount": sum(
                    len(value.rejectedQuoteWordIds) for value in grounded.fallbacks
                ),
            },
            dependency_ids=dependencies,
        )
        return ValidatedSummary(
            summary=grounded.summary.model_dump(mode="json"),
            artifact=self._artifact_ref(accepted),
        )

    @activity.defn(name="prepare_global_chapter_proposal")
    async def prepare_global_chapter_proposal(  # noqa: C901, PLR0912, PLR0915
        self, request: PrepareGlobalProposalRequest
    ) -> GlobalProposalPlan:
        """Validate summary lineage and build a bounded global proposal request."""
        self._require_enabled()
        if len(request.windows) != len(request.summaries):
            raise RuntimeError("each planning window requires one summary result")
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
        loaded = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=request.evidence.id,
        )
        evidence = HarnessEvidence.model_validate(loaded)
        if request.grounding_artifacts:
            if run.evidence_artifact_id != request.evidence.id:
                raise RuntimeError("grounded global proposal does not name the run evidence")
            accepted_evidence = await artifacts.find_artifact(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                identity=artifacts.ArtifactIdentity(
                    kind="evidence",
                    fingerprint=request.evidence.fingerprint,
                    transcript_id=evidence.transcriptId,
                    transcript_revision=evidence.transcriptRevision,
                ),
            )
            if (
                accepted_evidence is None
                or self._artifact_ref(accepted_evidence) != request.evidence
            ):
                raise RuntimeError("global proposal evidence identity does not match storage")
            if len(request.grounding_artifacts) != len(request.summaries):
                raise RuntimeError("each summary requires one grounding artifact")
            reports = tuple(
                [
                    await self._grounding_report(
                        scope=scope,
                        source_id=ref.source_id,
                        reference=reference,
                    )
                    for reference in request.grounding_artifacts
                ]
            )
            for window, raw_summary, report in zip(
                request.windows, request.summaries, reports, strict=True
            ):
                try:
                    supplied = HierarchicalSummaryV1.model_validate(raw_summary)
                except ValueError as error:
                    raise RuntimeError("grounded summary payload is invalid") from error
                if (
                    report.runId != ref.run_id
                    or report.evidence != request.evidence
                    or report.hierarchyLevel != request.hierarchy_level
                    or report.windowId != window.id
                    or report.firstSentenceId != window.first_sentence_id
                    or report.lastSentenceId != window.last_sentence_id
                    or report.windowSentenceCount != window.sentence_count
                    or report.windowPromptSha256
                    != hashlib.sha256(window.prompt.encode()).hexdigest()
                    or report.normalizedSummary != supplied
                ):
                    raise RuntimeError("grounding report identity differs from its summary window")
        language_value = evidence.config.get("detectedLanguage")
        detected_language = (
            language_value.strip()
            if isinstance(language_value, str) and language_value.strip()
            else None
        )
        sentence_positions = {
            sentence.id: index for index, sentence in enumerate(evidence.sentences)
        }
        word_owners = {
            word_id(value): sentence.id
            for sentence in evidence.sentences
            for value in sentence.wordIds
        }
        summaries: list[dict[str, object]] = []
        expected_window_start = 0
        for window, raw_summary in zip(request.windows, request.summaries, strict=True):
            summary = HierarchicalSummaryV1.model_validate(raw_summary)
            try:
                window_start = sentence_positions[window.first_sentence_id]
                window_end = sentence_positions[window.last_sentence_id]
            except KeyError as error:
                raise RuntimeError("summary window names a foreign sentence") from error
            if window_start != expected_window_start or window_end < window_start:
                raise RuntimeError("summary windows must exactly cover evidence in order")
            expected_unit_start = window_start
            for unit in summary.units:
                try:
                    unit_start = sentence_positions[unit.firstSentenceId]
                    unit_end = sentence_positions[unit.lastSentenceId]
                except KeyError as error:
                    raise RuntimeError("summary unit names a foreign sentence") from error
                if (
                    unit_start != expected_unit_start
                    or unit_end < unit_start
                    or unit_end > window_end
                ):
                    raise RuntimeError("summary units must exactly cover their source window")
                if any(
                    word_owners.get(word) is None
                    or not unit_start <= sentence_positions[word_owners[word]] <= unit_end
                    for word in unit.quoteWordIds
                ):
                    raise RuntimeError("summary quote anchor lies outside its source unit")
                expected_unit_start = unit_end + 1
            if expected_unit_start != window_end + 1:
                raise RuntimeError("summary units leave a gap in their source window")
            expected_window_start = window_end + 1
            summaries.append(summary.model_dump(mode="json"))
        if expected_window_start != len(evidence.sentences):
            raise RuntimeError("summary windows do not cover all evidence sentences")
        route = select_route(run.route_snapshot, "propose")
        prompt: str | None = None
        try:
            candidate_prompt = render_hierarchy_proposal_prompt(
                source_id=ref.source_id,
                evidence_sha256=request.evidence.sha256,
                summaries=summaries,
                brief=run.brief,
                detected_language=detected_language,
            )
            estimate_cost(
                route,
                payload_bytes=len(candidate_prompt.encode()),
                max_output_tokens=self.harness_settings.max_output_tokens,
            )
            prompt = candidate_prompt
        except ContextWindowExceeded:
            pass
        if prompt is not None:
            return GlobalProposalPlan(
                prompt=prompt,
                route=route,
                synthetic_payload=self._recorded_output("propose"),
                hierarchy_level=request.hierarchy_level,
                input_artifacts=request.grounding_artifacts,
            )
        if request.hierarchy_level >= MAX_HIERARCHY_LEVEL:
            return GlobalProposalPlan(
                route=route,
                hierarchy_level=request.hierarchy_level,
                refusal="Eight complete hierarchy levels still exceed the qualified context.",
                input_artifacts=request.grounding_artifacts,
            )
        summary_route = select_route(run.route_snapshot, "summary")
        reductions: list[PlanningWindow] = []
        first_summary = 0
        while first_summary < len(summaries):
            last_summary = min(
                len(summaries),
                first_summary + self.harness_settings.evidence_window_sentences,
            )
            accepted_reduction = False
            while last_summary > first_summary:
                batch = summaries[first_summary:last_summary]
                try:
                    reduction_prompt = render_summary_reduction_prompt(
                        source_id=ref.source_id,
                        evidence_sha256=request.evidence.sha256,
                        summaries=batch,
                        hierarchy_level=request.hierarchy_level + 1,
                        detected_language=detected_language,
                    )
                    estimate_cost(
                        summary_route,
                        payload_bytes=len(reduction_prompt.encode()),
                        max_output_tokens=self.harness_settings.max_output_tokens,
                    )
                except ContextWindowExceeded:
                    last_summary -= 1
                    continue
                first_unit = HierarchicalSummaryV1.model_validate(batch[0]).units[0]
                last_unit = HierarchicalSummaryV1.model_validate(batch[-1]).units[-1]
                start_position = sentence_positions[first_unit.firstSentenceId]
                end_position = sentence_positions[last_unit.lastSentenceId]
                reductions.append(
                    PlanningWindow(
                        id=(f"hierarchy-{request.hierarchy_level + 1}-{len(reductions):04d}"),
                        first_sentence_id=first_unit.firstSentenceId,
                        last_sentence_id=last_unit.lastSentenceId,
                        sentence_count=end_position - start_position + 1,
                        prompt=reduction_prompt,
                    )
                )
                first_summary = last_summary
                accepted_reduction = True
                break
            if not accepted_reduction:
                return GlobalProposalPlan(
                    route=route,
                    hierarchy_level=request.hierarchy_level,
                    refusal=("One complete summary unit exceeds the qualified reduction context."),
                    input_artifacts=request.grounding_artifacts,
                )
        return GlobalProposalPlan(
            route=route,
            reduction_windows=tuple(reductions),
            hierarchy_level=request.hierarchy_level + 1,
            input_artifacts=request.grounding_artifacts,
        )

    @activity.defn(name="prepare_chapter_verification")
    async def prepare_chapter_verification(
        self, request: PrepareVerificationRequest
    ) -> VerificationPlan:
        """Build a bounded text/technical verifier request with family independence."""
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
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            family_rows = await (
                await conn.execute(
                    """
                    SELECT DISTINCT a.family FROM harness_attempt a
                    JOIN harness_operation o ON o.id = a.operation_id
                     WHERE a.run_id = %s AND a.source_id = %s AND a.family IS NOT NULL
                       AND o.stage NOT LIKE 'verify:%%'
                    """,
                    (ref.run_id, ref.source_id),
                )
            ).fetchall()
        generation_families = frozenset(
            [*request.generation_families, *(str(row["family"]) for row in family_rows)]
        )
        try:
            route = select_verifier_route(
                run.route_snapshot,
                "verify",
                generation_families=generation_families,
            )
        except NoEligibleRoute:
            return VerificationPlan(
                refusal="No independent verifier family remains in the frozen route snapshot."
            )
        evidence_raw, proposal_raw = await asyncio.gather(
            artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                artifact_id=request.evidence.id,
            ),
            artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                artifact_id=request.proposal.id,
            ),
        )
        evidence = HarnessEvidence.model_validate(evidence_raw)
        if not evidence.sentences:
            return VerificationPlan(
                refusal="Text evidence is empty; editorial verification stopped."
            )
        sentences = tuple(
            PromptSentence(
                id=sentence.id,
                text=sentence.text,
                firstWordId=word_id(sentence.wordIds[0]),
                lastWordId=word_id(sentence.wordIds[-1]),
                speakers=tuple(sentence.speakers),
            )
            for sentence in evidence.sentences
        )
        window = PromptWindow(
            sourceId=ref.source_id,
            evidenceSha256=request.evidence.sha256,
            windowId="verify-global",
            firstSentenceId=sentences[0].id,
            lastSentenceId=sentences[-1].id,
            sentences=sentences,
        )
        prompt = render_verifier_prompt(
            window,
            proposal=cast("dict[str, object]", proposal_raw),
            technical_report={"sections": list(request.rendered.technical_report)},
        )
        try:
            estimate_cost(
                route,
                payload_bytes=len(prompt.encode()),
                max_output_tokens=self.harness_settings.max_output_tokens,
            )
        except ContextWindowExceeded:
            return VerificationPlan(
                refusal="The complete grounded verifier request exceeds its qualified context."
            )
        return VerificationPlan(
            prompt=prompt,
            route=route,
            synthetic_payload=self._recorded_output("verify"),
            output_cap=self.harness_settings.max_output_tokens,
            dispatch_limit=self.harness_settings.max_dispatches,
        )

    @activity.defn(name="finalize_chapter_verification")
    async def finalize_chapter_verification(
        self, request: FinalizeVerificationRequest
    ) -> HarnessArtifactRef:
        """Publish the verifier verdict with the exact response and render lineage."""
        self._require_enabled()
        ref = request.run
        scope = Scope(
            organizationId=ref.scope_organization_id,
            userId=ref.scope_user_id,
        )
        verdict = EditorialVerdictV1.model_validate(request.verdict)
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            model_rows = await (
                await conn.execute(
                    """
                    SELECT a.id, a.sha256
                      FROM harness_operation o
                      JOIN harness_artifact a ON a.id = o.result_artifact_id
                     WHERE o.run_id = %s AND o.source_id = %s
                       AND o.kind = 'model' AND o.stage = %s
                       AND o.status = 'succeeded' AND a.kind = 'model_response'
                    """,
                    (ref.run_id, ref.source_id, f"verify:revision:{request.revision}"),
                )
            ).fetchall()
        if len(model_rows) != 1:
            raise RuntimeError("verifier stage does not have one accepted model response")
        model_row = model_rows[0]
        fingerprint = artifacts.fingerprint_for(
            kind="checks",
            inputs={
                "descriptorArtifactId": str(request.rendered.descriptor.id),
                "descriptorSha256": request.rendered.descriptor.sha256,
                "editArtifactId": str(request.edit.id),
                "editSha256": request.edit.sha256,
                "modelResponseArtifactId": str(model_row["id"]),
                "modelResponseSha256": str(model_row["sha256"]),
            },
            config={"format": "chapter-verification/1"},
        )
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="checks", fingerprint=fingerprint),
            content={
                "format": "chapter-verification/1",
                "verdict": verdict.model_dump(mode="json"),
            },
            metadata={
                "editSha256": request.edit.sha256,
                "format": "chapter-verification/1",
                "revision": request.revision,
                "runId": str(ref.run_id),
                "verifierFamily": request.verifier_family,
            },
            dependency_ids=(
                request.edit.id,
                request.rendered.descriptor.id,
                model_row["id"],
            ),
        )
        return self._artifact_ref(accepted)

    @activity.defn(name="export_chapter_revision")
    async def export_chapter_revision(self, request: ExportRevisionRequest) -> ExportRevisionResult:
        """Publish a manifest only after every keep/drop has explicit human acceptance."""
        self._require_enabled()
        ref = request.run
        scope = Scope(
            organizationId=ref.scope_organization_id,
            userId=ref.scope_user_id,
        )
        edit_raw, descriptor_raw = await asyncio.gather(
            artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                artifact_id=request.edit.id,
            ),
            artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                store=self.ctx.store,
                artifact_id=request.descriptor.id,
            ),
        )
        edit = ChapterEditSpec.model_validate(edit_raw)
        descriptor = ChapterRenders.model_validate(descriptor_raw)
        if (
            request.edit.kind != HarnessArtifactKind.edit
            or request.descriptor.kind != HarnessArtifactKind.render
        ):
            raise RuntimeError("export requires edit and render descriptor artifacts")
        accepted_edit, accepted_descriptor = await asyncio.gather(
            artifacts.find_artifact(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                identity=artifacts.ArtifactIdentity(
                    kind=str(request.edit.kind), fingerprint=request.edit.fingerprint
                ),
            ),
            artifacts.find_artifact(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=ref.source_id,
                identity=artifacts.ArtifactIdentity(
                    kind=str(request.descriptor.kind),
                    fingerprint=request.descriptor.fingerprint,
                ),
            ),
        )
        if (
            accepted_edit is None
            or accepted_descriptor is None
            or self._artifact_ref(accepted_edit) != request.edit
            or self._artifact_ref(accepted_descriptor) != request.descriptor
        ):
            raise RuntimeError("export artifact references differ from accepted identities")
        if edit.sourceId != ref.source_id or descriptor.runId != ref.run_id:
            raise RuntimeError("export content belongs to a different source or run")
        if descriptor.editSha256 != accepted_edit.sha256:
            raise RuntimeError("export descriptor names a different edit hash")
        expected_keep_ids = {section.section_id for section in kept_sections(edit)}
        if {render.sectionId for render in descriptor.renders} != expected_keep_ids:
            raise RuntimeError("export descriptor does not exactly cover the kept sections")
        if any(section.reviewState != ReviewState.accepted for section in edit.sections):
            return ExportRevisionResult(artifact=None, ready=False)
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            revision_row = await (
                await conn.execute(
                    """
                    SELECT created_at FROM chapter_revision
                     WHERE run_id = %s AND source_id = %s AND revision = %s
                       AND artifact_id = %s
                    """,
                    (ref.run_id, ref.source_id, request.revision, request.edit.id),
                )
            ).fetchone()
        if revision_row is None:
            raise RuntimeError("export edit is not the named immutable chapter revision")
        manifest = ChapterExport(
            chapters=descriptor.renders,
            createdAt=revision_row["created_at"],
            editSha256=accepted_edit.sha256,
            manifestKey=accepted_descriptor.storage_key,
            revision=request.revision,
            runId=ref.run_id,
            version=1,
        )
        fingerprint = artifacts.fingerprint_for(
            kind="export",
            inputs={
                "descriptorArtifactId": str(accepted_descriptor.id),
                "descriptorSha256": accepted_descriptor.sha256,
                "editArtifactId": str(accepted_edit.id),
                "editSha256": request.edit.sha256,
                "revision": request.revision,
                "runId": str(ref.run_id),
            },
            config={"format": "chapter-export/1"},
        )
        operation = await ledger.acquire_operation(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            kind=ledger.OperationKind.EXPORT,
            stage=f"export:revision:{request.revision}",
            inputs={
                "descriptorArtifactId": str(accepted_descriptor.id),
                "editArtifactId": str(accepted_edit.id),
            },
            config={"format": "chapter-export/1"},
        )
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="export", fingerprint=fingerprint),
            content=manifest.model_dump(mode="json"),
            metadata={
                "editSha256": request.edit.sha256,
                "format": "chapter-export/1",
                "revision": request.revision,
                "runId": str(ref.run_id),
            },
            dependency_ids=(accepted_edit.id, accepted_descriptor.id),
        )
        await ledger.complete_operation_from_artifact(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            operation_id=operation.operation.id,
            result_artifact_id=accepted.id,
            expected_artifact_kind="export",
            expected_artifact_fingerprint=fingerprint,
        )
        await runs.accept_export(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            revision=request.revision,
            edit_artifact_id=accepted_edit.id,
            edit_sha256=request.edit.sha256,
            export_artifact_id=accepted.id,
        )
        return ExportRevisionResult(artifact=self._artifact_ref(accepted), ready=True)

    @activity.defn(name="compile_chapter_proposal")
    async def compile_chapter_proposal(
        self, request: CompileProposalRequest
    ) -> CompileProposalResult:
        """Publish grounded proposal/edit artifacts without moving the revision pointer."""
        self._require_enabled()
        ref = request.run
        scope = Scope(
            organizationId=ref.scope_organization_id,
            userId=ref.scope_user_id,
        )
        loaded = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            artifact_id=request.evidence.id,
        )
        evidence = HarnessEvidence.model_validate(loaded)
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            model_rows = await (
                await conn.execute(
                    """
                    SELECT a.id, a.sha256
                      FROM harness_operation o
                      JOIN harness_artifact a ON a.id = o.result_artifact_id
                     WHERE o.run_id = %s AND o.source_id = %s
                       AND o.kind = 'model' AND o.stage = %s
                       AND o.status = 'succeeded' AND a.kind = 'model_response'
                    """,
                    (ref.run_id, ref.source_id, request.model_stage),
                )
            ).fetchall()
        if len(model_rows) != 1:
            raise RuntimeError("proposal stage does not have one accepted model response")
        model_artifact_id = model_rows[0]["id"]
        proposal_fingerprint = artifacts.fingerprint_for(
            kind="proposal",
            inputs={
                "evidenceArtifactId": str(request.evidence.id),
                "evidenceSha256": request.evidence.sha256,
                "modelResponseArtifactId": str(model_artifact_id),
                "modelResponseSha256": str(model_rows[0]["sha256"]),
                "runId": str(ref.run_id),
            },
            config={"schema": "chapter-proposal/1"},
        )
        proposal = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="proposal", fingerprint=proposal_fingerprint),
            content=request.proposal.model_dump(mode="json"),
            metadata={
                "format": "chapter-proposal/1",
                "generatorFamily": request.generator_family,
                "runId": str(ref.run_id),
            },
            dependency_ids=(request.evidence.id, model_artifact_id),
        )
        try:
            edit = compile_chapters(
                evidence,
                request.proposal,
                evidence_artifact_id=request.evidence.id,
                evidence_sha256=request.evidence.sha256,
                config=CompilerConfig(),
            )
        except HarnessValidationError as error:
            return CompileProposalResult(refusal=str(error)[:2000])
        edit_fingerprint = artifacts.fingerprint_for(
            kind="edit",
            inputs={
                "evidenceArtifactId": str(request.evidence.id),
                "proposalArtifactId": str(proposal.id),
                "runId": str(ref.run_id),
            },
            config={"compiler": edit.compilerVersion},
        )
        compile_operation = await ledger.acquire_operation(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            kind=ledger.OperationKind.COMPILE,
            stage="compile:revision-1",
            inputs={
                "evidenceArtifactId": str(request.evidence.id),
                "proposalArtifactId": str(proposal.id),
            },
            config={"compiler": edit.compilerVersion},
        )
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="edit", fingerprint=edit_fingerprint),
            content=edit.model_dump(mode="json"),
            metadata={
                "format": "chapter-edit/1",
                "proposalArtifactId": str(proposal.id),
                "revision": 1,
                "runId": str(ref.run_id),
            },
            dependency_ids=(request.evidence.id, proposal.id, model_artifact_id),
        )
        await ledger.complete_operation_from_artifact(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
            operation_id=compile_operation.operation.id,
            result_artifact_id=accepted.id,
            expected_artifact_kind="edit",
            expected_artifact_fingerprint=edit_fingerprint,
        )
        return CompileProposalResult(
            compiled=CompiledRevision(
                proposal_artifact=self._artifact_ref(proposal),
                edit_artifact=self._artifact_ref(accepted),
                edit=edit,
                revision=1,
            )
        )

    @activity.defn(name="prepare_chapter_review")
    async def prepare_chapter_review(  # noqa: C901
        self, command: ChapterReviewInput
    ) -> PreparedReviewMutation:
        """Apply a content command in memory and publish its immutable candidate edit."""
        self._require_enabled()
        if command.action in {
            ChapterReviewAction.cancel,
            ChapterReviewAction.raise_budget,
            ChapterReviewAction.retry,
        }:
            raise TypeError("operational review commands do not prepare an edit")
        scope = command.scope
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT r.revision, r.artifact_id, a.sha256
                      FROM chapter_revision r
                      JOIN harness_artifact a ON a.id = r.artifact_id
                     WHERE r.run_id = %s AND r.source_id = %s
                       AND r.revision <= %s
                     ORDER BY r.revision
                    """,
                    (command.runId, command.sourceId, command.baseRevision),
                )
            ).fetchall()
            run = await (
                await conn.execute(
                    """
                    SELECT evidence_artifact_id FROM harness_run
                     WHERE id = %s AND source_id = %s
                    """,
                    (command.runId, command.sourceId),
                )
            ).fetchone()
        if run is None or run["evidence_artifact_id"] is None:
            raise RuntimeError("review run has no accepted evidence")
        async with db.scoped(self.ctx.settings.database_url, scope) as conn:
            evidence_row = await (
                await conn.execute(
                    "SELECT * FROM harness_artifact WHERE id = %s AND source_id = %s",
                    (run["evidence_artifact_id"], command.sourceId),
                )
            ).fetchone()
        if evidence_row is None:
            raise RuntimeError("review run evidence artifact is absent")
        evidence_ref = HarnessArtifactRef(
            id=evidence_row["id"],
            kind=HarnessArtifactKind(str(evidence_row["kind"])),
            fingerprint=str(evidence_row["fingerprint"]),
            sha256=str(evidence_row["sha256"]),
            sizeBytes=int(evidence_row["size_bytes"]),
            storageKey=str(evidence_row["storage_key"]),
        )
        if not rows or int(rows[-1]["revision"]) != command.baseRevision:
            return PreparedReviewMutation(
                request=command,
                candidate=None,
                evidence=evidence_ref,
                message="The requested base revision is unavailable.",
            )
        if len(rows) > MAX_REVIEW_REVISIONS:
            raise RuntimeError("chapter review history exceeds the bounded activity limit")
        evidence_raw = await artifacts.read_artifact_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=command.sourceId,
            store=self.ctx.store,
            artifact_id=run["evidence_artifact_id"],
        )
        evidence = HarnessEvidence.model_validate(evidence_raw)
        edits: dict[int, ChapterEditSpec] = {}
        for row in rows:
            raw = await artifacts.read_artifact_json(
                self.ctx.settings.database_url,
                scope=scope,
                source_id=command.sourceId,
                store=self.ctx.store,
                artifact_id=row["artifact_id"],
            )
            edits[int(row["revision"])] = ChapterEditSpec.model_validate(raw)
        current = edits[command.baseRevision]
        try:
            if command.action == ChapterReviewAction.accept:
                await self._assert_review_media_eligible(
                    command, str(rows[-1]["sha256"]), evidence, current
                )
            candidate = apply_review(evidence, current, command, edits)
        except ReviewRefused as error:
            return PreparedReviewMutation(
                request=command,
                candidate=None,
                evidence=evidence_ref,
                message=str(error),
            )
        if command.action == ChapterReviewAction.undo:
            candidate = candidate.model_copy(
                update={
                    "sections": [
                        section.model_copy(update={"reviewState": ReviewState.proposed})
                        for section in candidate.sections
                    ]
                }
            )
        next_revision = command.baseRevision + 1
        fingerprint = artifacts.fingerprint_for(
            kind="edit",
            inputs={
                "baseArtifactId": str(rows[-1]["artifact_id"]),
                "baseSha256": str(rows[-1]["sha256"]),
                "command": command.model_dump(mode="json"),
                "runId": str(command.runId),
            },
            config={"reviewMutation": 1},
        )
        dependencies = [run["evidence_artifact_id"], rows[-1]["artifact_id"]]
        if command.targetRevision is not None:
            dependencies.append(rows[command.targetRevision - 1]["artifact_id"])
        accepted = await artifacts.publish_json(
            self.ctx.settings.database_url,
            scope=scope,
            source_id=command.sourceId,
            store=self.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="edit", fingerprint=fingerprint),
            content=candidate.model_dump(mode="json"),
            metadata={
                "format": "chapter-edit/1",
                "mutationKey": str(command.mutationKey),
                "revision": next_revision,
                "runId": str(command.runId),
            },
            dependency_ids=tuple(dict.fromkeys(dependencies)),
        )
        return PreparedReviewMutation(
            request=command,
            candidate=self._artifact_ref(accepted),
            evidence=evidence_ref,
            message="Chapter review applied and the new revision is rendering.",
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
        def fraction(name: str, *, optional: bool = False) -> Fraction | None:
            raw = value.get(name)
            if raw is None and optional:
                return None
            if not isinstance(raw, dict):
                raise TypeError("frozen timeline rational is invalid")
            parts = cast("dict[str, object]", raw)
            return Fraction(int(str(parts["numerator"])), int(str(parts["denominator"])))

        return MediaTimelineFacts(
            duration=cast("Fraction", fraction("duration")),
            container_start=cast("Fraction", fraction("containerStart")),
            source_start=cast("Fraction", fraction("sourceStart")),
            has_video=bool(value.get("hasVideo")),
            has_audio=bool(value.get("hasAudio")),
            video_stream_index=cast("int | None", value.get("videoStreamIndex")),
            audio_stream_index=cast("int | None", value.get("audioStreamIndex")),
            video_start=fraction("videoStart", optional=True),
            audio_start=fraction("audioStart", optional=True),
            video_duration=fraction("videoDuration", optional=True),
            audio_duration=fraction("audioDuration", optional=True),
            frame_rate=fraction("frameRate", optional=True),
            video_time_base=fraction("videoTimeBase", optional=True),
            audio_time_base=fraction("audioTimeBase", optional=True),
            sample_rate=cast("int | None", value.get("sampleRate")),
            width=cast("int | None", value.get("width")),
            height=cast("int | None", value.get("height")),
            rotation=cast("int | None", value.get("rotation")),
            audio_channels=cast("int | None", value.get("audioChannels")),
            audio_layout=cast("str | None", value.get("audioLayout")),
            variable_frame_rate=bool(value.get("variableFrameRate")),
            video_codec=cast("str | None", value.get("videoCodec")),
            audio_codec=cast("str | None", value.get("audioCodec")),
            sample_aspect_ratio=fraction("sampleAspectRatio", optional=True),
        )

    @activity.defn(name="render_chapter_revision")
    async def render_chapter_revision(self, request: RenderRevisionRequest) -> RenderRevisionResult:
        """Lease one run's cache while rendering and clean every revision workspace."""
        self._require_enabled()
        run = await self._assert_render_active(request.run, request.revision)
        workspace = self._render_workspace(run, request)
        self._cancel_source_cache_expiry(run.id)
        succeeded = False
        try:
            async with self._source_cache_lease(run.id):
                result = await self._render_chapter_revision_locked(request, run)
                succeeded = True
                return result
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
            if not succeeded:
                self._arm_source_cache_expiry(run.id)

    async def _render_chapter_revision_locked(  # noqa: C901, PLR0915
        self, request: RenderRevisionRequest, run: RunSnapshot
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

        config = ChapterRenderConfig()

        async def one(section: RenderSection) -> tuple[ChapterRender, ChapterChecks]:
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
                if media is None:
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

    @activity.defn(name="reuse_chapter_revision_render")
    async def reuse_chapter_revision_render(
        self, request: ReuseRevisionRenderRequest
    ) -> ReuseRevisionRenderOutcome:
        """Rebind checked predecessor bytes after a review-metadata-only mutation."""
        self._require_enabled()
        ref = request.run
        return await reuse_review_render(
            self.ctx.settings.database_url,
            scope=Scope(
                organizationId=ref.scope_organization_id,
                userId=ref.scope_user_id,
            ),
            source_id=ref.source_id,
            store=self.ctx.store,
            request=request,
        )

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
        if not isinstance(metadata, dict):
            raise TypeError("object storage returned invalid source metadata")
        values = cast("dict[object, object]", metadata)

        def text_value(*keys: str) -> str | None:
            for key in keys:
                value = values.get(key)
                if value is not None:
                    return str(value)
            return None

        return {
            "etag": text_value("e_tag", "etag"),
            "versionId": text_value("version", "version_id", "versionId"),
        }

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
            self.build_chapter_evidence,
            self.prepare_chapter_proposal,
            self.validate_chapter_summary,
            self.prepare_global_chapter_proposal,
            self.prepare_chapter_verification,
            self.compile_chapter_proposal,
            self.render_chapter_revision,
            self.cleanup_chapter_source_cache,
            self.reuse_chapter_revision_render,
            self.prepare_chapter_review,
            self.finalize_chapter_verification,
            self.export_chapter_revision,
        )

    def control_activities(self) -> Sequence[Callable[..., object]]:
        """Return compact DB-only activities for the control task queue."""
        return (
            self.start_chapter_run,
            self.get_chapter_run,
            self.get_chapter_resume_assets,
            self.update_chapter_run_stage,
            self.claim_chapter_repair,
            self.mark_chapter_run_failed,
            self.apply_chapter_review,
            self.accept_initial_chapter_revision,
            self.commit_chapter_review,
        )
