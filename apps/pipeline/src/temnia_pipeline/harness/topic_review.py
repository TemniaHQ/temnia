"""Finite human topic decisions and revision-bound accepted downloads."""

# Runtime activity inputs and the owner's established media checks are intentional.
# ruff: noqa: EM101, TRY003, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporalio import activity

    from temnia_pipeline import db
    from temnia_pipeline.contracts import (
        ChapterChecks,
        ChapterEditSpec,
        ChapterRenders,
        ChapterReviewAction,
        ChapterReviewInput,
        ChapterReviewOutput,
        HarnessArtifactKind,
        HarnessArtifactRef,
        HarnessEvidence,
        HarnessRunStatus,
        ReviewState,
        Scope,
        TopicCompiledVideo,
        TopicEditSpec,
        TopicExport,
        TopicRenderedVideo,
        TopicRenders,
    )
    from temnia_pipeline.harness import artifacts, runs
    from temnia_pipeline.harness.editorial_policy import is_topic_policy
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.rendering import kept_sections
    from temnia_pipeline.harness.review import ReviewRefused
    from temnia_pipeline.harness.runtime_types import (
        CommitReviewMutationRequest,
        CommitReviewMutationResult,
        ExportRevisionRequest,
        ExportRevisionResult,
        MarkRunFailedRequest,
        PreparedReviewMutation,
        RunRef,
        RunSnapshot,
        StageUpdate,
        WorkflowIdentity,
    )
    from temnia_pipeline.harness.topic_activities import TopicActivities
    from temnia_pipeline.harness.topic_compiler import validate_topic_edit
    from temnia_pipeline.harness.topic_runtime import TopicContext, TopicRenderResult
    from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
    from temnia_pipeline.harness.topic_selection_runtime import SelectionContext
    from temnia_pipeline.harness.validators import rounded_milliseconds
    from temnia_pipeline.media.chapter_checks import technical_checks_pass

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from temnia_pipeline.harness.activities import HarnessActivities

RETRY = RetryPolicy(maximum_attempts=3)
MAX_REVIEW_REASON = 2000


def validate_topic_command(command: ChapterReviewInput) -> None:
    """A topic decision cannot smuggle a chapter timing or budget operation."""
    if command.action not in {
        ChapterReviewAction.accept,
        ChapterReviewAction.reject,
        ChapterReviewAction.cancel,
        ChapterReviewAction.retry,
    } or any(
        value is not None
        for value in (
            command.boundaryId,
            command.otherSectionId,
            command.budgetMicros,
            command.targetRevision,
            command.targetTimeMs,
        )
    ):
        raise ReviewRefused(
            "topic review supports only acceptance, rejection, cancellation and retry"
        )
    if not command.reason.strip() or len(command.reason) > MAX_REVIEW_REASON:
        raise ReviewRefused("topic review requires a reason of at most 2000 characters")
    operational = command.action in {ChapterReviewAction.cancel, ChapterReviewAction.retry}
    if operational != (command.sectionId is None):
        raise ReviewRefused("topic decisions name one candidate; cancellation and retry name none")


def apply_topic_decision(edit: TopicEditSpec, command: ChapterReviewInput) -> TopicEditSpec:
    """Change only the selected kept section's durable human state."""
    validate_topic_command(command)
    if (
        command.action in {ChapterReviewAction.cancel, ChapterReviewAction.retry}
        or command.sourceId != edit.sourceId
    ):
        raise ReviewRefused("the decision does not name this topic portfolio")
    matches = [video for video in edit.videos if video.candidate.id == command.sectionId]
    if len(matches) != 1:
        raise ReviewRefused("the named topic candidate is absent")
    selected = matches[0]
    state = (
        ReviewState.accepted
        if command.action == ChapterReviewAction.accept
        else ReviewState.rejected
    )
    sections = [
        section.model_copy(update={"reviewState": state})
        if section.id == selected.keptSectionId
        else section
        for section in selected.edit.sections
    ]
    if sections == selected.edit.sections:
        raise ReviewRefused("that human decision is already recorded")
    replacement = selected.model_copy(
        update={"edit": selected.edit.model_copy(update={"sections": sections})}
    )
    return edit.model_copy(
        update={"videos": [replacement if video == selected else video for video in edit.videos]}
    )


def topic_human_states(edit: TopicEditSpec) -> dict[str, ReviewState]:
    """Only each video's keep owns human state; execution drops are not votes."""
    return {
        video.candidate.id: next(
            section.reviewState
            for section in video.edit.sections
            if section.id == video.keptSectionId
        )
        for video in edit.videos
    }


class TopicReviewActivities:
    """Scoped preparation, checked media references and accepted-manifest publication."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner
        self.topics = TopicActivities(owner)

    async def context(self, ref: RunRef) -> TopicContext:
        """Resolve accepted source evidence only from this policy-bound run."""
        self.owner._require_enabled()
        scope = Scope(organizationId=ref.scope_organization_id, userId=ref.scope_user_id)
        run = await runs.get_run(
            self.owner.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            run_id=ref.run_id,
        )
        if not is_topic_policy(run.editorial_policy) or run.evidence_artifact_id is None:
            raise ReviewRefused("topic review requires a standalone run with accepted evidence")
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=scope,
            source_id=ref.source_id,
            artifact_id=run.evidence_artifact_id,
        )
        return TopicContext(run=ref, evidence=self.owner._artifact_ref(record))

    async def reference(self, context: TopicContext, artifact_id: object) -> HarnessArtifactRef:
        """Resolve an immutable ID before comparing its accepted full identity."""
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(context),
            source_id=context.run.source_id,
            artifact_id=UUID(str(artifact_id)),
        )
        return self.owner._artifact_ref(record)

    async def require_dependencies(
        self,
        context: TopicContext,
        reference: HarnessArtifactRef,
        dependencies: Sequence[HarnessArtifactRef],
    ) -> None:
        """A passing body must belong to the exact files recorded as its inputs."""
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(context),
            source_id=context.run.source_id,
            artifact_id=reference.id,
        )
        if self.owner._artifact_ref(record) != reference or not {
            dependency.id for dependency in dependencies
        }.issubset(record.dependency_ids):
            raise ReviewRefused("topic artifact dependencies differ from its exact inputs")

    async def editorial_lineage(
        self, context: TopicContext, edit: HarnessArtifactRef, metadata: dict[str, object]
    ) -> tuple[HarnessArtifactRef, HarnessArtifactRef]:
        """Keep the original proposal and assessment directly reachable after human review."""
        # The selected reader verifies the frozen policy; metadata is only a dispatch hint.
        if metadata.get("selectionArtifactId") is not None:
            selection = await self.reference(context, metadata.get("selectionArtifactId"))
            assessment = await self.reference(context, metadata.get("assessmentArtifactId"))
            rubric = await self.reference(context, metadata.get("rubricArtifactId"))
            if (
                selection.kind != HarnessArtifactKind.proposal
                or assessment.kind != HarnessArtifactKind.checks
                or selection.sha256 != metadata.get("selectionSha256")
            ):
                raise ReviewRefused("selection portfolio differs from its editorial lineage")
            selections = TopicSelectionActivities(self.owner)
            program_version = metadata.get("programVersion", "standalone-topics/3")
            if program_version not in {"standalone-topics/3", "standalone-topics/4"}:
                raise ReviewRefused("selection portfolio has an unknown programme version")
            lineage = SelectionContext.model_validate(
                {
                    "run": context.run,
                    "evidence": context.evidence,
                    "program_version": program_version,
                    "rubric": rubric,
                    "selection": selection,
                    "assessment": assessment,
                }
            )
            await selections.load(lineage)
            await selections.assessment(lineage)
            await self.require_dependencies(
                context, edit, (context.evidence, rubric, selection, assessment)
            )
            return selection, assessment
        raise ReviewRefused("the portfolio has no selection lineage")

    async def portfolio(
        self, context: TopicContext, ref: HarnessArtifactRef
    ) -> tuple[TopicEditSpec, HarnessEvidence, dict[str, object]]:
        """Keep source/evidence and original assessment lineage through human revisions."""
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(context),
            source_id=context.run.source_id,
            artifact_id=ref.id,
        )
        if (
            record.kind != "edit"
            or record.metadata.get("format") != "topic-edit/1"
            or record.metadata.get("runId") != str(context.run.run_id)
        ):
            raise ReviewRefused("the portfolio belongs to another run or format")
        edit = TopicEditSpec.model_validate(await self.topics.read(context, ref))
        _, evidence = await self.topics.load_evidence(context)
        if (
            edit.evidenceArtifactId != context.evidence.id
            or context.evidence.id not in record.dependency_ids
        ):
            raise ReviewRefused("the portfolio does not depend on the run's evidence")
        validate_topic_edit(evidence, edit, expected_evidence_sha256=context.evidence.sha256)
        await self.editorial_lineage(context, ref, dict(record.metadata))
        return edit, evidence, dict(record.metadata)

    async def descriptor(
        self,
        context: TopicContext,
        edit: HarnessArtifactRef,
        reference: HarnessArtifactRef | None = None,
    ) -> tuple[HarnessArtifactRef, TopicRenders]:
        """Read a top-level descriptor bound to this exact portfolio."""
        if reference is None:
            async with db.scoped(
                self.owner.ctx.settings.database_url, self.topics.scope(context)
            ) as conn:
                row = await (
                    await conn.execute(
                        """SELECT id FROM harness_artifact WHERE source_id=%s AND kind='render'
                    AND metadata->>'format'='topic-renders/1' AND metadata->>'runId'=%s
                    AND metadata->>'editSha256'=%s ORDER BY created_at DESC LIMIT 1""",
                        (context.run.source_id, str(context.run.run_id), edit.sha256),
                    )
                ).fetchone()
            if row is None:
                raise ReviewRefused("acceptance requires rendered topic media for this revision")
            reference = await self.reference(context, row["id"])
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(context),
            source_id=context.run.source_id,
            artifact_id=reference.id,
        )
        if reference.kind != HarnessArtifactKind.render or edit.id not in record.dependency_ids:
            raise ReviewRefused("topic descriptor is not a dependency-bound render")
        descriptor = TopicRenders.model_validate(await self.topics.read(context, reference))
        if descriptor.runId != context.run.run_id or descriptor.editSha256 != edit.sha256:
            raise ReviewRefused("topic renders belong to another run or revision")
        if len({item.candidateId for item in descriptor.videos}) != len(descriptor.videos):
            raise ReviewRefused("topic renders contain duplicate candidate identities")
        return reference, descriptor

    async def checked_video(
        self,
        context: TopicContext,
        video: TopicCompiledVideo,
        rendered: TopicRenderedVideo,
        evidence: HarnessEvidence,
    ) -> None:
        """Acceptance uses the exact independent execution and applicable technical checks."""
        if (
            rendered.candidateId != video.candidate.id
            or rendered.execution.kind != HarnessArtifactKind.edit
            or rendered.descriptor.kind != HarnessArtifactKind.render
        ):
            raise ReviewRefused("rendered topic candidate identity differs")
        execution = ChapterEditSpec.model_validate(
            await self.topics.read(context, rendered.execution)
        )
        if execution != video.edit:
            raise ReviewRefused("rendered execution differs from the portfolio's topic")
        descriptor = ChapterRenders.model_validate(
            await self.topics.read(context, rendered.descriptor)
        )
        if (
            descriptor.runId != context.run.run_id
            or descriptor.editSha256 != rendered.execution.sha256
            or len(descriptor.renders) != 1
        ):
            raise ReviewRefused("topic media descriptor differs from its execution")
        media = descriptor.renders[0]
        extent = kept_sections(execution)[0]
        if (
            media.sectionId != video.keptSectionId
            or media.editSha256 != rendered.execution.sha256
            or media.durationMs != rounded_milliseconds(extent.end - extent.start)
            or media.checks is None
        ):
            raise ReviewRefused("acceptance requires checked media for the exact topic extent")
        for ref in (media.media, media.captions):
            if ref is not None and (
                ref.kind != HarnessArtifactKind.render
                or await self.reference(context, ref.id) != ref
            ):
                raise ReviewRefused("topic media reference differs from retained source identity")
        if media.checks.kind != HarnessArtifactKind.checks:
            raise ReviewRefused("topic technical checks have the wrong artifact kind")
        checked_inputs = (
            rendered.execution,
            media.media,
            *((media.captions,) if media.captions is not None else ()),
        )
        await self.require_dependencies(context, media.checks, checked_inputs)
        await self.require_dependencies(
            context, rendered.descriptor, (*checked_inputs, media.checks)
        )
        checks = ChapterChecks.model_validate(await self.topics.read(context, media.checks))
        _, _, timeline = self.owner._evidence_source(evidence)
        if (
            checks.editSha256 != rendered.execution.sha256
            or any(check.sectionId != video.keptSectionId for check in checks.technicalChecks)
            or not technical_checks_pass(
                checks, source=self.owner._timeline_from_identity(timeline)
            )
        ):
            raise ReviewRefused(
                "acceptance requires all applicable technical checks for this topic"
            )

    @activity.defn(name="prepare_topic_review")
    async def prepare(self, command: ChapterReviewInput) -> PreparedReviewMutation:
        """Publish the exact human decision before the shared revision CAS."""
        validate_topic_command(command)
        context = await self.context(topic_run_ref(command))
        async with db.scoped(self.owner.ctx.settings.database_url, command.scope) as conn:
            row = await (
                await conn.execute(
                    """SELECT artifact_id FROM chapter_revision
                       WHERE run_id=%s AND source_id=%s AND revision=%s""",
                    (command.runId, command.sourceId, command.baseRevision),
                )
            ).fetchone()
        if row is None:
            return PreparedReviewMutation(
                request=command,
                candidate=None,
                evidence=context.evidence,
                message="The requested topic revision is unavailable.",
            )
        previous = await self.reference(context, row["artifact_id"])
        edit, evidence, metadata = await self.portfolio(context, previous)
        dependencies = [
            context.evidence.id,
            previous.id,
            UUID(str(metadata.get("selectionArtifactId", metadata.get("proposalArtifactId")))),
            UUID(str(metadata["assessmentArtifactId"])),
        ]
        if metadata.get("rubricArtifactId") is not None:
            dependencies.append(UUID(str(metadata["rubricArtifactId"])))
        try:
            candidate = apply_topic_decision(edit, command)
            validate_topic_edit(
                evidence, candidate, expected_evidence_sha256=context.evidence.sha256
            )
            if command.action == ChapterReviewAction.accept:
                descriptor_ref, descriptor = await self.descriptor(context, previous)
                selected = next(
                    video for video in edit.videos if video.candidate.id == command.sectionId
                )
                renders = [
                    item for item in descriptor.videos if item.candidateId == command.sectionId
                ]
                if len(renders) != 1:
                    raise ReviewRefused("the selected topic has no unique rendered execution")  # noqa: TRY301
                await self.checked_video(context, selected, renders[0], evidence)
                dependencies.append(descriptor_ref.id)
        except ReviewRefused as error:
            return PreparedReviewMutation(
                request=command, candidate=None, evidence=context.evidence, message=str(error)
            )
        fingerprint = artifacts.fingerprint_for(
            kind="edit",
            inputs={
                "baseArtifactId": str(previous.id),
                "baseSha256": previous.sha256,
                "command": command.model_dump(mode="json"),
            },
            config={"topicReview": 1},
        )
        published = await artifacts.publish_json(
            self.owner.ctx.settings.database_url,
            scope=command.scope,
            source_id=command.sourceId,
            store=self.owner.ctx.store,
            identity=artifacts.ArtifactIdentity(kind="edit", fingerprint=fingerprint),
            content=candidate.model_dump(mode="json"),
            metadata={
                **metadata,
                "format": "topic-edit/1",
                "runId": str(command.runId),
                "mutationKey": str(command.mutationKey),
                "revision": command.baseRevision + 1,
            },
            dependency_ids=tuple(dependencies),
        )
        return PreparedReviewMutation(
            request=command,
            candidate=self.owner._artifact_ref(published),
            evidence=context.evidence,
            message="Your topic review was recorded.",
        )

    @activity.defn(name="apply_topic_operational_review")
    async def cancel(self, command: ChapterReviewInput) -> ChapterReviewOutput:
        """Retain the existing cancellation ledger fence under the new lane policy."""
        validate_topic_command(command)
        if command.action != ChapterReviewAction.cancel:
            raise ReviewRefused("only cancellation is an operational topic command")
        run = await runs.get_run(
            self.owner.ctx.settings.database_url,
            scope=command.scope,
            source_id=command.sourceId,
            run_id=command.runId,
        )
        if not is_topic_policy(run.editorial_policy):
            raise ReviewRefused("topic cancellation cannot mutate a chapter run")
        return await self.owner.apply_chapter_review(command)

    @activity.defn(name="export_topic_revision")
    async def export(self, request: ExportRevisionRequest) -> ExportRevisionResult:
        """Publish only accepted independent videos; finish only a fully decided portfolio."""
        context = await self.context(request.run)
        edit, evidence, _ = await self.portfolio(context, request.edit)
        await self.assert_revision(context, request)
        descriptor_ref, descriptor = await self.descriptor(
            context, request.edit, request.descriptor
        )
        if {item.candidateId for item in descriptor.videos} != {
            video.candidate.id for video in edit.videos
        }:
            raise ReviewRefused("export requires a descriptor for this complete portfolio")
        states = topic_human_states(edit)
        selected: list[TopicRenderedVideo] = []
        dependencies = [request.edit, descriptor_ref]
        for video in edit.videos:
            if states[video.candidate.id] != ReviewState.accepted:
                continue
            rendered = next(
                item for item in descriptor.videos if item.candidateId == video.candidate.id
            )
            await self.checked_video(context, video, rendered, evidence)
            selected.append(rendered)
            dependencies.extend((rendered.execution, rendered.descriptor))
        if not selected:
            return ExportRevisionResult(artifact=None, ready=False)
        manifest = TopicExport(
            format="topic-export/1",
            runId=request.run.run_id,
            revision=request.revision,
            editSha256=request.edit.sha256,
            videos=selected,
        )
        reference = await self.topics.publish(
            context,
            kind="export",
            format_name="topic-export/1",
            content=manifest,
            dependencies=dependencies,
            metadata={"editSha256": request.edit.sha256, "revision": request.revision},
        )
        ready = all(
            state in {ReviewState.accepted, ReviewState.rejected} for state in states.values()
        )
        if ready:
            await self.accept_export(context, request, reference)
        return ExportRevisionResult(artifact=reference, ready=ready)

    async def assert_revision(self, context: TopicContext, request: ExportRevisionRequest) -> None:
        """A stale export cannot claim the current run or revive a terminal one."""
        async with db.scoped(
            self.owner.ctx.settings.database_url, self.topics.scope(context)
        ) as conn:
            row = await (
                await conn.execute(
                    """SELECT r.current_revision, r.status, c.artifact_id FROM harness_run r
                JOIN chapter_revision c ON c.run_id=r.id AND c.revision=%s
                WHERE r.id=%s AND r.source_id=%s""",
                    (request.revision, context.run.run_id, context.run.source_id),
                )
            ).fetchone()
        if (
            row is None
            or row["current_revision"] != request.revision
            or row["artifact_id"] != request.edit.id
            or row["status"] in {"cancelled", "failed", "outcome_unknown"}
        ):
            raise ReviewRefused("topic export is stale or the run cannot accept it")

    async def accept_export(
        self, context: TopicContext, request: ExportRevisionRequest, manifest: HarnessArtifactRef
    ) -> None:
        """Keep the topic manifest's ready CAS separate from historical chapter formats."""
        run, _ = await self.topics.load_evidence(context)
        async with db.scoped(
            self.owner.ctx.settings.database_url, self.topics.scope(context)
        ) as conn:
            await runs._lock_ready_source(conn, context.run.source_id)
            row = await (
                await conn.execute(
                    """UPDATE harness_run r
                SET accepted_revision=%s, stage='ready', status='ready',
                    error_message=NULL, updated_at=now()
                WHERE r.id=%s AND r.source_id=%s AND r.current_revision=%s
                AND r.route_snapshot->>'editorialPolicy'=%s
                AND r.status NOT IN ('cancelled','failed','outcome_unknown')
                AND EXISTS (SELECT 1 FROM chapter_revision c
                    JOIN harness_artifact a ON a.id=c.artifact_id
                    WHERE c.run_id=r.id AND c.revision=%s AND a.id=%s AND a.sha256=%s)
                AND EXISTS (SELECT 1 FROM harness_artifact e
                    WHERE e.id=%s AND e.source_id=r.source_id AND e.kind='export'
                    AND e.sha256=%s AND e.metadata->>'format'='topic-export/1'
                    AND e.metadata->>'runId'=%s AND e.metadata->>'editSha256'=%s
                    AND (e.metadata->>'revision')::int=%s)
                RETURNING r.id""",
                    (
                        request.revision,
                        context.run.run_id,
                        context.run.source_id,
                        request.revision,
                        run.editorial_policy,
                        request.revision,
                        request.edit.id,
                        request.edit.sha256,
                        manifest.id,
                        manifest.sha256,
                        str(context.run.run_id),
                        request.edit.sha256,
                        request.revision,
                    ),
                )
            ).fetchone()
            if row is None:
                raise runs.RunStateConflict("topic export lost its revision compare-and-set")

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register only the topic review activity names."""
        return (self.prepare, self.export)

    def control_activities(self) -> Sequence[Callable[..., object]]:
        """Cancellation retains short control-queue capacity."""
        return (self.cancel,)


def topic_run_ref(command: ChapterReviewInput) -> RunRef:
    """Recover the finite review's scoped run identity."""
    return RunRef(
        scope_organization_id=command.scope.organizationId,
        scope_user_id=command.scope.userId,
        source_id=command.sourceId,
        run_id=command.runId,
    )


@workflow.defn
class TopicReviewWorkflow:
    """One mutation UUID records one human decision, then rebinds checked outputs."""

    @workflow.run
    async def run(self, command: ChapterReviewInput) -> ChapterReviewOutput:
        """Preserve ownership and cancellation fences around the finite review."""
        try:
            return await self.program(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "mark_chapter_run_failed",
                    MarkRunFailedRequest(
                        run=topic_run_ref(command),
                        workflow=WorkflowIdentity(
                            workflow_id=workflow.info().workflow_id,
                            workflow_run_id=workflow.info().run_id,
                        ),
                        error_message=(
                            "Topic review could not finish. "
                            "Its recorded decision and artifacts remain available."
                        ),
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    task_queue=control_task_queue(workflow.info().task_queue),
                    result_type=bool,
                )
            raise

    async def program(self, command: ChapterReviewInput) -> ChapterReviewOutput:
        """Use the shared immutable-revision CAS, with independent topic artifacts."""
        validate_topic_command(command)
        queue = control_task_queue(workflow.info().task_queue)
        ref = topic_run_ref(command)
        if command.action in {ChapterReviewAction.cancel, ChapterReviewAction.retry}:
            before = await workflow.execute_activity(
                "get_chapter_run",
                ref,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
                result_type=RunSnapshot,
                task_queue=queue,
            )
            result = await workflow.execute_activity(
                "apply_topic_operational_review",
                command,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
                result_type=ChapterReviewOutput,
                task_queue=queue,
            )
            if (
                command.action == ChapterReviewAction.cancel
                and result.state == "applied"
                and before.status in {HarnessRunStatus.pending, HarnessRunStatus.running}
            ):
                with contextlib.suppress(Exception):
                    await workflow.get_external_workflow_handle(
                        before.workflow_id, run_id=before.workflow_run_id
                    ).cancel()
            return result
        prepared = await workflow.execute_activity(
            "prepare_topic_review",
            command,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
            result_type=PreparedReviewMutation,
        )
        committed = await workflow.execute_activity(
            "commit_chapter_review",
            CommitReviewMutationRequest(
                prepared=prepared,
                workflow=WorkflowIdentity(
                    workflow_id=workflow.info().workflow_id, workflow_run_id=workflow.info().run_id
                ),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=CommitReviewMutationResult,
            task_queue=queue,
        )
        if committed.render is None:
            return committed.output
        rendered = await workflow.execute_activity(
            "render_topic_revision",
            committed.render,
            start_to_close_timeout=timedelta(hours=12),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
            result_type=TopicRenderResult,
        )
        exported = await workflow.execute_activity(
            "export_topic_revision",
            ExportRevisionRequest(
                run=ref,
                revision=committed.render.revision,
                edit=committed.render.edit,
                descriptor=rendered.descriptor,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
            result_type=ExportRevisionResult,
        )
        if not exported.ready:
            await workflow.execute_activity(
                "update_chapter_run_stage",
                StageUpdate(
                    scope_organization_id=ref.scope_organization_id,
                    scope_user_id=ref.scope_user_id,
                    source_id=ref.source_id,
                    run_id=ref.run_id,
                    expected_stage="render",
                    expected_revision=committed.render.revision,
                    next_stage="needs_review",
                    status=HarnessRunStatus.needs_review,
                    error_message=None
                    if rendered.technical_passed
                    else "Some candidate videos need technical review.",
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY,
                task_queue=queue,
                result_type=RunSnapshot,
            )
        return committed.output
