"""Independent topic videos reuse the established media renderer and artifact checks."""

# Activity schemas remain runtime imports; guarded reuse of the owner's render primitives.
# ruff: noqa: EM101, TRY003, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING

from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import Scope, TopicEditSpec, TopicRenderedVideo, TopicRenders
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.editorial_policy import TOPIC_POLICY
from temnia_pipeline.harness.runtime_types import RenderRevisionRequest
from temnia_pipeline.harness.topic_activities import TopicActivities
from temnia_pipeline.harness.topic_compiler import validate_topic_edit
from temnia_pipeline.harness.topic_runtime import TopicContext, TopicRenderResult
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.speech.liveness import run_with_activity_heartbeat

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from temnia_pipeline.harness.activities import HarnessActivities


class TopicRenderActivities:
    """Render independent executions against one leased verified source cache."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner
        self.topics = TopicActivities(owner)

    @activity.defn(name="render_topic_revision")
    async def render(self, request: RenderRevisionRequest) -> TopicRenderResult:
        """Validate the portfolio and render each independent source interval."""

        async def operation() -> TopicRenderResult:
            self.owner._require_enabled()
            run = await self.owner._assert_render_active(request.run, request.revision)
            if run.editorial_policy != TOPIC_POLICY:
                raise HarnessValidationError("topic renderer requires a standalone topic run")
            scope = Scope(
                organizationId=request.run.scope_organization_id, userId=request.run.scope_user_id
            )
            async with db.scoped(self.owner.ctx.settings.database_url, scope) as conn:
                row = await (
                    await conn.execute(
                        """SELECT artifact_id FROM chapter_revision
                       WHERE run_id=%s AND source_id=%s AND revision=%s""",
                        (run.id, run.source_id, request.revision),
                    )
                ).fetchone()
            if row is None or row["artifact_id"] != request.edit.id:
                raise HarnessValidationError("topic render is not the current immutable revision")
            raw = await artifacts.read_artifact_json(
                self.owner.ctx.settings.database_url,
                scope=scope,
                source_id=run.source_id,
                store=self.owner.ctx.store,
                artifact_id=request.edit.id,
            )
            portfolio = TopicEditSpec.model_validate(raw)
            evidence_record = await artifacts._artifact_for_read(
                self.owner.ctx.settings.database_url,
                scope=scope,
                source_id=run.source_id,
                artifact_id=portfolio.evidenceArtifactId,
            )
            context = TopicContext(
                run=request.run, evidence=self.owner._artifact_ref(evidence_record)
            )
            await self.topics.read(context, request.edit)
            _, evidence, _ = await self.topics.load(context)
            validate_topic_edit(
                evidence, portfolio, expected_evidence_sha256=context.evidence.sha256
            )
            videos: list[TopicRenderedVideo] = []
            technical_passed = bool(portfolio.videos)
            dependencies = [request.edit]
            self.owner._cancel_source_cache_expiry(run.id)
            try:
                async with self.owner._source_cache_lease(run.id):
                    for video in portfolio.videos:
                        execution = await self.topics.publish(
                            context,
                            kind="edit",
                            format_name="chapter-edit/1",
                            content=video.edit,
                            dependencies=(request.edit, context.evidence),
                            metadata={
                                "topicId": video.candidate.id,
                                "portfolioSha256": request.edit.sha256,
                            },
                        )
                        rendered = await self.owner._render_chapter_revision_locked(
                            RenderRevisionRequest(
                                run=request.run, edit=execution, revision=request.revision
                            ),
                            run,
                            retain_source_cache=True,
                        )
                        videos.append(
                            TopicRenderedVideo(
                                candidateId=video.candidate.id,
                                execution=execution,
                                descriptor=rendered.descriptor,
                            )
                        )
                        technical_passed = technical_passed and rendered.technical_passed
                        dependencies.extend((execution, rendered.descriptor))
                    descriptor = TopicRenders(
                        format="topic-renders/1",
                        runId=run.id,
                        editSha256=request.edit.sha256,
                        videos=videos,
                    )
                    reference = await self.topics.publish(
                        context,
                        kind="render",
                        format_name="topic-renders/1",
                        content=descriptor,
                        dependencies=dependencies,
                        metadata={
                            "editSha256": request.edit.sha256,
                            "revision": request.revision,
                            "renderCount": len(videos),
                            "technicalPassed": technical_passed,
                        },
                    )
                    self.owner._cleanup_source_cache_held(run.id)
            finally:
                self.owner._arm_source_cache_expiry(run.id)
            return TopicRenderResult(
                descriptor=reference, technical_passed=technical_passed, count=len(videos)
            )

        return await run_with_activity_heartbeat(
            operation, details={"stage": "render-topic-videos"}
        )

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register the topic portfolio renderer."""
        return (self.render,)
