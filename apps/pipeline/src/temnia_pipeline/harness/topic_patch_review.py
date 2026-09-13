"""Finite, replayable human editorial corrections over the existing topic revision CAS."""

# Activity inputs are runtime schemas; guarded access reuses established scoped primitives.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temnia_pipeline import db
    from temnia_pipeline.contracts import (
        ChapterReviewOutput,
        HarnessRunStatus,
        TopicAssessment,
        TopicEditorialPatchInput,
        TopicProposal,
        TopicSelectionAssessment,
        TopicSelectionRecord,
    )
    from temnia_pipeline.harness.queues import control_task_queue
    from temnia_pipeline.harness.run_failures import known_failure_details
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
    from temnia_pipeline.harness.topic_compiler import (
        TOPIC_COMPILER_VERSION_V3,
        compile_topics,
        compile_topics_v2,
        compile_topics_v3,
        topic_boundary_issues,
    )
    from temnia_pipeline.harness.topic_patch import (
        apply_human_topic_patch,
        remap_human_opportunities,
    )
    from temnia_pipeline.harness.topic_review import TopicReviewActivities
    from temnia_pipeline.harness.topic_runtime import TopicRenderResult
    from temnia_pipeline.harness.topic_selection import validate_selection
    from temnia_pipeline.harness.validators import HarnessValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from temnia_pipeline.harness.activities import HarnessActivities

RETRY = RetryPolicy(maximum_attempts=3)


def patch_run_ref(command: TopicEditorialPatchInput) -> RunRef:
    """Use server-derived scope on every artifact/transaction boundary."""
    return RunRef(
        scope_organization_id=command.scope.organizationId,
        scope_user_id=command.scope.userId,
        source_id=command.sourceId,
        run_id=command.runId,
    )


class TopicEditorialPatchActivities:
    """Prepare complete source-grounded edits before a short shared CAS transaction."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.review = TopicReviewActivities(owner)
        self.topics = self.review.topics

    @activity.defn(name="prepare_topic_editorial_patch")
    async def prepare(self, command: TopicEditorialPatchInput) -> PreparedReviewMutation:  # noqa: PLR0915
        """Retain human origin and invalidate observations affected by the exact change."""
        context = await self.review.context(patch_run_ref(command))
        async with db.scoped(self.review.owner.ctx.settings.database_url, command.scope) as conn:
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
        previous = await self.review.reference(context, row["artifact_id"])
        if (
            previous.sha256 != command.baseEditSha256
            or context.evidence.sha256 != command.evidenceSha256
        ):
            return PreparedReviewMutation(
                request=command,
                candidate=None,
                evidence=context.evidence,
                message="The correction does not match its exact edit and source evidence.",
            )
        edit, evidence, metadata = await self.review.portfolio(context, previous)
        try:
            proposal = TopicProposal(
                version=1,
                summary=edit.summary,
                candidates=[video.candidate for video in edit.videos],
            )
            patched = apply_human_topic_patch(evidence, proposal, command)
            selection = None
            selection_ref = None
            proposal_ref = None
            draft = None
            if metadata.get("selectionArtifactId"):
                selection_ref = await self.review.reference(
                    context, metadata["selectionArtifactId"]
                )
                selection = TopicSelectionRecord.model_validate(
                    await self.topics.read(context, selection_ref)
                )
                complete_patch = apply_human_topic_patch(
                    evidence, selection.draft.proposal, command
                )
                draft = remap_human_opportunities(selection.draft, complete_patch, command)
                validate_selection(evidence, draft)
            else:
                proposal_ref = await self.review.reference(context, metadata["proposalArtifactId"])
                prior_proposal = TopicProposal.model_validate(
                    await self.topics.read(context, proposal_ref)
                )
                complete_patch = apply_human_topic_patch(evidence, prior_proposal, command)
            if edit.compilerVersion == TOPIC_COMPILER_VERSION_V3:
                compiler = compile_topics_v3
            elif metadata.get("selectionArtifactId"):
                compiler = compile_topics_v2
            else:
                compiler = compile_topics
            compiled = compiler(
                evidence,
                patched.proposal,
                evidence_artifact_id=context.evidence.id,
                evidence_sha256=context.evidence.sha256,
            )
        except HarnessValidationError as error:
            return PreparedReviewMutation(
                request=command,
                candidate=None,
                evidence=context.evidence,
                message=str(error)[:2000],
            )
        # Unchanged physical executions and human decisions remain byte-identical.
        old = {video.candidate.id: video for video in edit.videos}
        compiled = compiled.model_copy(
            update={
                "videos": [
                    old[video.candidate.id]
                    if video.candidate.id not in patched.replacement_ids
                    and video.candidate.id in old
                    else video
                    for video in compiled.videos
                ]
            }
        )
        command_ref = await self.topics.publish(
            context,
            kind="checks",
            format_name="topic-editorial-patch/1",
            content=command,
            dependencies=(context.evidence, previous),
            metadata={
                "origin": "human",
                "mutationKey": str(command.mutationKey),
                "baseEditSha256": previous.sha256,
                "candidateLineage": {key: list(value) for key, value in patched.lineage.items()},
            },
        )
        dependencies = [context.evidence, previous, command_ref]
        assessment_ref = await self.review.reference(context, metadata["assessmentArtifactId"])
        dependencies.append(assessment_ref)
        revised_metadata = dict(metadata)
        if selection is not None and selection_ref is not None and draft is not None:
            prior_assessment = TopicSelectionAssessment.model_validate(
                await self.topics.read(context, assessment_ref)
            )
            replacement = TopicSelectionRecord.model_validate(
                {
                    **selection.model_dump(mode="json"),
                    "draft": draft,
                    "origin": "human",
                    "parentSelectionSha256": selection_ref.sha256,
                }
            )
            rubric_ref = await self.review.reference(context, metadata["rubricArtifactId"])
            selected_ref = await self.topics.publish(
                context,
                kind="proposal",
                format_name="topic-selection/2",
                content=replacement,
                dependencies=(*dependencies, selection_ref, rubric_ref),
                metadata={"origin": "human"},
            )
            cold = [
                review
                for review in prior_assessment.coldReviews
                if review.candidateId not in complete_patch.affected_ids
            ]
            assessed = TopicSelectionAssessment.model_validate(
                {
                    "format": "topic-selection-assessment/2",
                    "runId": str(command.runId),
                    "selectionSha256": selected_ref.sha256,
                    "evidenceSha256": context.evidence.sha256,
                    "rubricSha256": selection.rubricSha256,
                    "coldReviews": [item.model_dump(mode="json") for item in cold],
                    "portfolioReview": None,
                    "findings": [],
                    "executionStatus": "needs_review",
                    "reasons": ["Human correction requires new clip and portfolio judgments."],
                    "proposerFamily": "human",
                    "verifierFamily": prior_assessment.verifierFamily if cold else None,
                    "responseArtifacts": [
                        item.model_dump(mode="json") for item in prior_assessment.responseArtifacts
                    ]
                    if cold
                    else [],
                }
            )
            new_assessment = await self.topics.publish(
                context,
                kind="checks",
                format_name="topic-selection-assessment/2",
                content=assessed,
                dependencies=(*dependencies, selected_ref, rubric_ref),
                metadata={"origin": "human", "selectionSha256": selected_ref.sha256},
            )
            revised_metadata.update(
                selectionArtifactId=str(selected_ref.id),
                selectionSha256=selected_ref.sha256,
                assessmentArtifactId=str(new_assessment.id),
            )
            dependencies.extend((selected_ref, new_assessment, rubric_ref))
        else:
            if proposal_ref is None:
                message = "The correction has no validated source proposal."
                raise HarnessValidationError(message)
            prior = TopicAssessment.model_validate(await self.topics.read(context, assessment_ref))
            selected_ref = await self.topics.publish(
                context,
                kind="proposal",
                format_name="topic-proposal/1",
                content=complete_patch.proposal,
                dependencies=(*dependencies, proposal_ref),
                metadata={"origin": "human"},
            )
            old_assessments = {item.candidateId: item for item in prior.candidates}
            candidate_assessments: list[dict[str, object]] = []
            for candidate in complete_patch.proposal.candidates:
                old_assessment = old_assessments.get(candidate.id)
                cold_review = (
                    old_assessment.coldReview
                    if old_assessment and candidate.id not in patched.affected_ids
                    else None
                )
                candidate_assessments.append(
                    {
                        "candidateId": candidate.id,
                        "status": "needs_review",
                        "coldReview": cold_review.model_dump(mode="json") if cold_review else None,
                        "sourceReview": None,
                        "physicalBoundaryIssues": [
                            item.model_dump(mode="json")
                            for item in topic_boundary_issues(evidence, candidate)
                        ],
                        "reasons": ["Human correction requires a new editorial assessment."],
                    }
                )
            assessed = TopicAssessment.model_validate(
                {
                    "format": "topic-assessment/1",
                    "runId": str(command.runId),
                    "proposalSha256": selected_ref.sha256,
                    "evidenceSha256": context.evidence.sha256,
                    "candidates": candidate_assessments,
                    "summary": "Human-corrected portfolio; source review is no longer current.",
                    "proposerFamily": "human",
                    "verifierFamily": prior.verifierFamily,
                }
            )
            new_assessment = await self.topics.publish(
                context,
                kind="checks",
                format_name="topic-assessment/1",
                content=assessed,
                dependencies=(*dependencies, selected_ref),
                metadata={"origin": "human", "proposalSha256": selected_ref.sha256},
            )
            revised_metadata.update(
                proposalArtifactId=str(selected_ref.id),
                proposalSha256=selected_ref.sha256,
                assessmentArtifactId=str(new_assessment.id),
            )
            dependencies.extend((selected_ref, new_assessment))
        revised_metadata.update(
            mutationKey=str(command.mutationKey),
            revision=command.baseRevision + 1,
            humanPatchArtifactId=str(command_ref.id),
            origin="human",
        )
        candidate_ref = await self.topics.publish(
            context,
            kind="edit",
            format_name="topic-edit/1",
            content=compiled,
            dependencies=dependencies,
            metadata=revised_metadata,
        )
        return PreparedReviewMutation(
            request=command,
            candidate=candidate_ref,
            evidence=context.evidence,
            message="Your editorial correction was recorded. Review the revised videos.",
        )

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register the new command without changing old review histories."""
        return (self.prepare,)


@workflow.defn
class TopicEditorialPatchWorkflow:
    """One immutable human patch, followed by revision-bound render and acceptance state."""

    @workflow.run
    async def run(self, command: TopicEditorialPatchInput) -> ChapterReviewOutput:
        """Commit one source-bound correction and deliver current checked previews."""
        try:
            return await self.program(command)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status, message = known_failure_details(error)
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "mark_chapter_run_failed",
                    MarkRunFailedRequest(
                        run=patch_run_ref(command),
                        workflow=WorkflowIdentity(
                            workflow_id=workflow.info().workflow_id,
                            workflow_run_id=workflow.info().run_id,
                        ),
                        error_message=message,
                        status=status,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY,
                    task_queue=control_task_queue(workflow.info().task_queue),
                    result_type=bool,
                )
            raise

    async def program(self, command: TopicEditorialPatchInput) -> ChapterReviewOutput:
        """Reuse revision, render and export boundaries without changing paid operations."""
        queue = control_task_queue(workflow.info().task_queue)
        prepared = await workflow.execute_activity(
            "prepare_topic_editorial_patch",
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
            task_queue=queue,
            result_type=CommitReviewMutationResult,
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
                run=patch_run_ref(command),
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
                    **patch_run_ref(command).model_dump(),
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
