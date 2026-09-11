"""Versioned selection decisions using the existing artifact and paid-operation ledger."""

# Activity DTOs need runtime annotations; evidence failures are explicit refusals.
# ruff: noqa: EM101, TRY003, TC001, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError
from pydantic_ai import TextPart
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.chapter_llama.candidate import CandidatePayload, candidate_hints
from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    TopicCompiledVideo,
    TopicEditorialRubric,
    TopicEditSpec,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionPatch,
    TopicSelectionRecord,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.qualification_topic_selection import (
    validate_topic_selection_qualification,
)
from temnia_pipeline.harness.routes import estimate_cost
from temnia_pipeline.harness.runtime_types import RunSnapshot
from temnia_pipeline.harness.topic_activities import TopicActivities
from temnia_pipeline.harness.topic_compiler import compile_topics_v2, validate_topic_edit
from temnia_pipeline.harness.topic_editorial import editorial_routes
from temnia_pipeline.harness.topic_runtime import TopicCompilation, TopicContext
from temnia_pipeline.harness.topic_selection import (
    SELECTION_COLD_PROMPT,
    SELECTION_PATCH_PROMPT,
    SELECTION_POLICY,
    SELECTION_PROMPT,
    SELECTION_SOURCE_PROMPT,
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    selection_candidates_for_render,
    selection_cold_key,
    selection_cold_prompt,
    selection_patch_prompt,
    selection_prompt,
    selection_semantic_key,
    selection_source_prompt,
    validate_selection,
)
from temnia_pipeline.harness.topic_selection_runtime import (
    SelectionAssessmentResult,
    SelectionCallPlan,
    SelectionContext,
    SelectionRejection,
    SelectionReviewRequest,
    SelectionSaveRequest,
    SelectionSaveResult,
    SelectionStopRequest,
    selection_call_config,
    selection_call_inputs,
)
from temnia_pipeline.harness.validators import HarnessValidationError, validate_evidence

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from temnia_pipeline.harness.activities import HarnessActivities


class TopicSelectionActivities:
    """Prepare, admit and persist source-linked editorial selection states."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner
        self.topics = TopicActivities(owner)

    @staticmethod
    def common(context: SelectionContext) -> TopicContext:
        """Use the shared scoped artifact service without v1 editorial admission."""
        return TopicContext(run=context.run, evidence=context.evidence)

    async def read(self, context: SelectionContext, ref: HarnessArtifactRef) -> object:
        """Read the exact immutable bytes in the run's source scope."""
        return await self.topics.read(self.common(context), ref)

    async def require_record(
        self,
        context: SelectionContext,
        ref: HarnessArtifactRef,
        *,
        format_name: str,
        dependencies: Sequence[HarnessArtifactRef],
    ) -> None:
        """A valid body also needs exact run, format and dependency provenance."""
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.common(context)),
            source_id=context.run.source_id,
            artifact_id=ref.id,
        )
        if (
            self.owner._artifact_ref(record) != ref
            or record.metadata.get("format") != format_name
            or record.metadata.get("runId") != str(context.run.run_id)
            or not {item.id for item in dependencies} <= set(record.dependency_ids)
        ):
            raise HarnessValidationError("selection artifact lacks its exact run dependencies")

    async def load(
        self, context: SelectionContext
    ) -> tuple[
        RunSnapshot, HarnessEvidence, TopicEditorialRubric | None, TopicSelectionRecord | None
    ]:
        """Refuse cross-generation reuse before accessing editorial inputs."""
        self.owner._require_enabled()
        run = await runs.get_run(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.common(context)),
            source_id=context.run.source_id,
            run_id=context.run.run_id,
        )
        if (
            run.editorial_policy != SELECTION_POLICY
            or run.evidence_artifact_id != context.evidence.id
        ):
            raise HarnessValidationError(
                "selection activity requires its frozen v2 policy and evidence"
            )
        evidence = HarnessEvidence.model_validate(await self.read(context, context.evidence))
        if evidence.sourceId != run.source_id:
            raise HarnessValidationError("selection evidence belongs to another source")
        validate_evidence(evidence)
        rubric = None
        if context.rubric is not None:
            await self.require_record(
                context,
                context.rubric,
                format_name="topic-editorial-rubric/1",
                dependencies=(context.evidence,),
            )
            rubric = TopicEditorialRubric.model_validate(await self.read(context, context.rubric))
            if rubric != make_rubric(run.brief) or content_hash(rubric) != context.rubric.sha256:
                raise HarnessValidationError(
                    "selection rubric differs from the frozen original brief"
                )
        selection = None
        if context.selection is not None:
            if rubric is None or context.rubric is None:
                raise HarnessValidationError("selection requires an accepted rubric")
            await self.require_record(
                context,
                context.selection,
                format_name="topic-selection/2",
                dependencies=(context.evidence, context.rubric),
            )
            selection = TopicSelectionRecord.model_validate(
                await self.read(context, context.selection)
            )
            if (
                selection.runId != run.id
                or selection.evidenceSha256 != context.evidence.sha256
                or selection.rubric != rubric
                or selection.rubricSha256 != context.rubric.sha256
            ):
                raise HarnessValidationError("selection record differs from its evidence or rubric")
            validate_selection(evidence, selection.draft)
        return run, evidence, rubric, selection

    async def assessment(self, context: SelectionContext) -> TopicSelectionAssessment | None:
        """Only the assessment of the exact current selection can authorize a patch."""
        if context.assessment is None:
            return None
        if context.selection is None or context.rubric is None:
            raise HarnessValidationError("assessment requires selection and rubric identities")
        await self.require_record(
            context,
            context.assessment,
            format_name="topic-selection-assessment/2",
            dependencies=(context.evidence, context.rubric, context.selection),
        )
        result = TopicSelectionAssessment.model_validate(
            await self.read(context, context.assessment)
        )
        if (
            result.runId != context.run.run_id
            or result.selectionSha256 != context.selection.sha256
            or result.evidenceSha256 != context.evidence.sha256
            or result.rubricSha256 != context.rubric.sha256
        ):
            raise HarnessValidationError("assessment differs from its exact selection inputs")
        return result

    @activity.defn(name="prepare_topic_selection_rubric")
    async def rubric(self, context: SelectionContext) -> HarnessArtifactRef:
        """Freeze deterministic audience defaults before any paid editorial operation."""
        run, _, _, _ = await self.load(context)
        return await self.topics.publish(
            self.common(context),
            kind="checks",
            format_name="topic-editorial-rubric/1",
            content=make_rubric(run.brief),
            dependencies=(context.evidence,),
            metadata={"programVersion": SELECTION_POLICY},
        )

    @activity.defn(name="prepare_topic_selection_call")
    async def prepare(self, context: SelectionContext) -> SelectionCallPlan:  # noqa: C901, PLR0912, PLR0915 — four versioned native stages share one receipt path
        """Prepare one native schema call with all evidence and rubric dependencies."""
        run, evidence, rubric, selection = await self.load(context)
        if rubric is None or context.rubric is None:
            raise HarnessValidationError("editorial calls require a frozen rubric")
        if str(run.config.backend) == "gateway":
            qualification = self.owner.harness_settings.topic_selection_qualification_path
            if qualification is None:
                raise HarnessValidationError("topic selection requires exact route qualification")
            validate_topic_selection_qualification(
                run.route_snapshot,
                qualification,
                max_output_tokens=run.config.maxOutputTokens,
            )
        author, verifier = editorial_routes(run.route_snapshot)
        dependencies = [context.evidence, context.rubric]
        if context.candidate_id is not None:
            candidate = (
                next(
                    (
                        item
                        for item in selection.draft.proposal.candidates
                        if item.id == context.candidate_id
                    ),
                    None,
                )
                if selection
                else None
            )
            if candidate is None:
                raise HarnessValidationError("cold review candidate is absent from the selection")
            prompt = selection_cold_prompt(evidence, candidate, rubric)
            stage = f"verify:selection:cold:{selection_cold_key(candidate, context.rubric.sha256)}"
            version = SELECTION_COLD_PROMPT
            synthetic = "topic_selection_cold"
        elif selection is not None and context.assessment is None:
            if context.selection is None:
                raise HarnessValidationError("source review requires its exact selection artifact")
            prompt = selection_source_prompt(evidence, selection.draft, rubric)
            stage = f"verify:selection:source:{context.iteration}"
            version = SELECTION_SOURCE_PROMPT
            synthetic = (
                "topic_selection_source_selected"
                if selection.draft.proposal.candidates
                else "topic_selection_source"
            )
            dependencies.append(context.selection)
        elif selection is not None:
            assessment = await self.assessment(context)
            if assessment is None or context.selection is None or context.assessment is None:
                raise HarnessValidationError("selection repair requires exact assessed state")
            prompt = selection_patch_prompt(
                evidence, selection, assessment, context.selection.sha256
            )
            stage = f"repair:selection:{context.iteration}"
            version = SELECTION_PATCH_PROMPT
            synthetic = "topic_selection_patch"
            dependencies.extend((context.selection, context.assessment))
        else:
            navigation = None
            if context.navigation is not None:
                payload = CandidatePayload.model_validate(
                    await self.read(context, context.navigation)
                )
                if payload.configuration != run.chapter_llama_config or verifier.family == "llama":
                    raise HarnessValidationError(
                        "navigation changed its frozen identity or reserved family"
                    )
                navigation = candidate_hints(payload, evidence, context.evidence)
                dependencies.append(context.navigation)
            rejected = None
            diagnostics: tuple[str, ...] = ()
            if context.rejection is not None:
                await self.require_record(
                    context,
                    context.rejection,
                    format_name="topic-selection-rejection/2",
                    dependencies=(context.evidence, context.rubric),
                )
                refusal = SelectionRejection.model_validate(
                    await self.read(context, context.rejection)
                )
                rejected, diagnostics = refusal.draft, refusal.diagnostics
                dependencies.append(context.rejection)
            prompt = selection_prompt(
                evidence,
                rubric,
                navigation=navigation,
                rejected_output=rejected,
                diagnostics=diagnostics,
            )
            stage = f"proposal:selection:{context.iteration}"
            version = SELECTION_PROMPT
            synthetic = "topic_selection_author"
        route = verifier if stage.startswith("verify:") else author
        estimate_cost(
            route, payload_bytes=len(prompt.encode()), max_output_tokens=run.config.maxOutputTokens
        )
        synthetic_payload = self.owner._recorded_output(synthetic)
        if synthetic_payload is not None and version == SELECTION_PATCH_PROMPT:
            # Fixture operations remain authored test data; only run-local immutable hashes vary.
            patch = TopicSelectionPatch.model_validate(synthetic_payload["output"])
            if context.selection is None:
                raise HarnessValidationError("recorded patch requires its exact selection")
            synthetic_payload = {
                **synthetic_payload,
                "output": {
                    **patch.model_dump(mode="json"),
                    "baseSelectionSha256": context.selection.sha256,
                    "evidenceSha256": context.evidence.sha256,
                    "rubricSha256": context.rubric.sha256,
                },
            }
        return SelectionCallPlan(
            prompt=prompt,
            stage=stage,
            prompt_version=version,
            schema_version={
                SELECTION_PROMPT: "topic-selection-draft/2",
                SELECTION_SOURCE_PROMPT: "topic-selection-portfolio/2",
            }.get(version, version),
            author=author,
            verifier=verifier,
            input_artifacts=tuple(dependencies),
            synthetic_payload=synthetic_payload,
        )

    async def response_ref(
        self,
        context: SelectionContext,
        plan: SelectionCallPlan,
        output: BaseModel | None,
    ) -> HarnessArtifactRef:
        """Admit only the original settled response for this complete prepared request."""
        run, _, _, _ = await self.load(context)
        route = plan.verifier if plan.stage.startswith("verify:") else plan.author
        async with db.scoped(
            self.owner.ctx.settings.database_url, self.topics.scope(self.common(context))
        ) as conn:
            rows = await (
                await conn.execute(
                    """SELECT a.id, t.family, o.input_hash, o.config_hash FROM harness_operation o
                JOIN harness_artifact a ON a.id=o.result_artifact_id
                JOIN harness_attempt t ON t.operation_id=o.id
                  AND t.result_artifact_id=a.id AND t.state='succeeded'
                WHERE o.run_id=%s AND o.source_id=%s AND o.stage=%s
                  AND o.kind='model' AND o.status='succeeded'""",
                    (run.id, run.source_id, plan.stage),
                )
            ).fetchall()
        if len(rows) != 1 or rows[0]["family"] != route.family:
            raise HarnessValidationError(
                "selection call has no unique settled response from its assigned family"
            )
        row = rows[0]
        retained = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.common(context)),
            source_id=run.source_id,
            artifact_id=row["id"],
        )
        metadata = retained.metadata
        if (
            retained.kind != "model_response"
            or metadata.get("runId") != str(run.id)
            or metadata.get("programVersion") != SELECTION_POLICY
            or metadata.get("promptVersion") != plan.prompt_version
            or metadata.get("schemaVersion") != plan.schema_version
            or metadata.get("route") != route.model_dump(mode="json")
            or not {ref.id for ref in plan.input_artifacts} <= set(retained.dependency_ids)
            or not isinstance(metadata.get("requestHash"), str)
        ):
            raise HarnessValidationError("selection response changed its exact request identity")
        _, input_hash, config_hash = ledger.operation_identity(
            run_id=run.id,
            kind="model",
            inputs={**selection_call_inputs(plan), "requestHash": metadata["requestHash"]},
            config={
                **selection_call_config(plan, run.config.maxOutputTokens),
                "programVersion": SELECTION_POLICY,
                "promptVersion": plan.prompt_version,
                "schemaVersion": plan.schema_version,
                "route": route.model_dump(mode="json"),
            },
        )
        if row["input_hash"] != input_hash or row["config_hash"] != config_hash:
            raise HarnessValidationError(
                "selection response belongs to different prompt or settings"
            )
        reference = self.owner._artifact_ref(retained)
        response = MODEL_RESPONSE_ADAPTER.validate_python(await self.read(context, reference))
        text = "".join(part.content for part in response.parts if isinstance(part, TextPart))
        if output is not None and type(output).model_validate_json(text) != output:
            raise HarnessValidationError("selection output differs from its original paid response")
        return reference

    @activity.defn(name="save_topic_selection")
    async def save(self, request: SelectionSaveRequest) -> SelectionSaveResult:
        """Apply a known response atomically, retaining refused patches beside prior work."""
        context = request.context
        _, evidence, rubric, previous = await self.load(context)
        if rubric is None or context.rubric is None:
            raise HarnessValidationError("selection saving requires a rubric")
        plan = await self.prepare(context)
        output = request.patch if previous is not None else request.draft
        if request.schema_error is None and output is None:
            raise HarnessValidationError(
                "selection saving requires a typed output or schema diagnostic"
            )
        response = await self.response_ref(context, plan, output)
        dependencies = (*plan.input_artifacts, response)
        diagnostics: tuple[str, ...] = ()
        draft = request.draft
        try:
            if request.schema_error is not None:
                raise HarnessValidationError(request.schema_error)  # noqa: TRY301
            if previous is not None:
                assessment = await self.assessment(context)
                if request.patch is None or assessment is None or context.selection is None:
                    raise HarnessValidationError("patch requires its exact assessed base")  # noqa: TRY301
                draft = apply_selection_patch(
                    evidence, previous, context.selection.sha256, assessment, request.patch
                )
            if draft is None:
                raise HarnessValidationError("selection response contains no draft")  # noqa: TRY301
            validate_selection(evidence, draft)
        except (HarnessValidationError, ValidationError, ValueError) as error:
            diagnostics = (str(error),)
        if diagnostics:
            rejection = SelectionRejection(
                response=response,
                stage=plan.stage,
                draft=request.draft,
                patch=request.patch,
                diagnostics=diagnostics,
            )
            ref = await self.topics.publish(
                self.common(context),
                kind="checks",
                format_name=rejection.format,
                content=rejection,
                dependencies=dependencies,
                metadata={"programVersion": SELECTION_POLICY},
            )
            return SelectionSaveResult(
                selection=context.selection, rejection=ref, diagnostics=diagnostics
            )
        if draft is None:
            raise HarnessValidationError("selection admission did not produce a draft")
        record = TopicSelectionRecord.model_validate(
            {
                "draft": draft.model_dump(mode="json"),
                "evidenceSha256": context.evidence.sha256,
                "format": "topic-selection/2",
                "origin": "model",
                "parentSelectionSha256": context.selection.sha256 if context.selection else None,
                "rubric": rubric.model_dump(mode="json"),
                "rubricSha256": context.rubric.sha256,
                "runId": str(context.run.run_id),
            }
        )
        ref = await self.topics.publish(
            self.common(context),
            kind="proposal",
            format_name=record.format,
            content=record,
            dependencies=dependencies,
            metadata={"programVersion": SELECTION_POLICY, "generatorFamily": plan.author.family},
        )
        return SelectionSaveResult(
            selection=ref, semantic_key=selection_semantic_key(draft), draft=draft
        )

    @activity.defn(name="save_topic_selection_assessment")
    async def save_assessment(self, request: SelectionReviewRequest) -> SelectionAssessmentResult:
        """Ground each observation and preserve unavailable observations as unknown."""
        context = request.context
        run, evidence, _, record = await self.load(context)
        if record is None or context.selection is None or context.rubric is None:
            raise HarnessValidationError("selection assessment requires its exact selection")
        if (
            not len(request.cold_reviews)
            == len(request.cold_stages)
            == len(request.cold_candidate_ids)
        ):
            raise HarnessValidationError("cold observations and stages differ")
        response_refs: list[HarnessArtifactRef] = []
        admitted_cold: list[TopicSelectionColdReview] = []
        reasons = list(request.reasons)
        for cold, candidate_id, stage in zip(
            request.cold_reviews, request.cold_candidate_ids, request.cold_stages, strict=True
        ):
            cold_context = context.model_copy(
                update={"candidate_id": candidate_id, "assessment": None}
            )
            plan = await self.prepare(cold_context)
            if stage != plan.stage:
                raise HarnessValidationError("cached cold review differs from this clip or rubric")
            response_refs.append(await self.response_ref(cold_context, plan, cold))
            if cold.candidateId == candidate_id:
                admitted_cold.append(cold)
            else:
                reasons.append(
                    f"Cold review of {candidate_id} named a different candidate and is unavailable."
                )
        for candidate_id in request.unavailable_cold_ids:
            cold_context = context.model_copy(
                update={"candidate_id": candidate_id, "assessment": None}
            )
            plan = await self.prepare(cold_context)
            response_refs.append(await self.response_ref(cold_context, plan, None))
        source_context = context.model_copy(update={"candidate_id": None, "assessment": None})
        # Even a schema-invalid source answer is retained and bound; it is never a pass.
        if request.source_dispatched:
            source_plan = await self.prepare(source_context)
            response_refs.append(
                await self.response_ref(source_context, source_plan, request.source_review)
            )
        author, verifier = editorial_routes(run.route_snapshot)
        assessment = assess_selection(
            evidence,
            record,
            context.selection.sha256,
            cold_reviews=admitted_cold,
            source_review=request.source_review,
            author_family=author.family,
            verifier_family=verifier.family,
            response_artifacts=tuple(response_refs),
            reasons=reasons,
        )
        if request.execution_limited:
            assessment = TopicSelectionAssessment.model_validate(
                {
                    **assessment.model_dump(mode="json"),
                    "executionStatus": "execution_limited",
                }
            )
        reference = await self.topics.publish(
            self.common(context),
            kind="checks",
            format_name=assessment.format,
            content=assessment,
            dependencies=(context.evidence, context.rubric, context.selection, *response_refs),
            metadata={"selectionSha256": context.selection.sha256},
        )
        return SelectionAssessmentResult(
            artifact=reference,
            assessment=assessment,
            actionable=any(str(item.severity) == "required" for item in assessment.findings),
        )

    @activity.defn(name="stop_topic_selection")
    async def stop(self, request: SelectionStopRequest) -> SelectionAssessmentResult:
        """Record exhaustion or refused repair without mislabeling opportunity dispositions."""
        context = request.context
        await self.load(context)
        assessment = await self.assessment(context)
        if (
            assessment is None
            or context.assessment is None
            or context.selection is None
            or context.rubric is None
        ):
            raise HarnessValidationError("selection stop requires the last valid assessment")
        updated = TopicSelectionAssessment.model_validate(
            {
                **assessment.model_dump(mode="json"),
                "reasons": list(dict.fromkeys((*assessment.reasons, *request.reasons))),
                "executionStatus": "execution_limited"
                if request.execution_limited
                else assessment.executionStatus,
            }
        )
        reference = await self.topics.publish(
            self.common(context),
            kind="checks",
            format_name=updated.format,
            content=updated,
            dependencies=(
                context.evidence,
                context.rubric,
                context.selection,
                context.assessment,
                *updated.responseArtifacts,
            ),
            metadata={"selectionSha256": context.selection.sha256},
        )
        return SelectionAssessmentResult(artifact=reference, assessment=updated, actionable=False)

    @activity.defn(name="compile_topic_selection")
    async def compile(self, context: SelectionContext) -> TopicCompilation:
        """Render selected/unresolved treatments while retaining every editorial disposition."""
        _, evidence, _, record = await self.load(context)
        assessment = await self.assessment(context)
        if (
            record is None
            or assessment is None
            or context.selection is None
            or context.rubric is None
            or context.assessment is None
        ):
            raise HarnessValidationError("selection compilation requires exact assessed state")
        proposal = selection_candidates_for_render(record, assessment)
        videos: list[TopicCompiledVideo] = []
        refusals: list[str] = []
        compiled = compile_topics_v2(
            evidence,
            proposal.model_copy(update={"candidates": []}),
            evidence_artifact_id=context.evidence.id,
            evidence_sha256=context.evidence.sha256,
        )
        for candidate in proposal.candidates:
            try:
                single = compile_topics_v2(
                    evidence,
                    proposal.model_copy(update={"candidates": [candidate]}),
                    evidence_artifact_id=context.evidence.id,
                    evidence_sha256=context.evidence.sha256,
                )
            except HarnessValidationError as error:
                refusals.append(f"{candidate.id}: {error}")
            else:
                videos.extend(single.videos)
        edit = TopicEditSpec.model_validate(
            {
                **compiled.model_dump(mode="json"),
                "videos": [item.model_dump(mode="json") for item in videos],
            }
        )
        validate_topic_edit(evidence, edit, expected_evidence_sha256=context.evidence.sha256)
        reference = await self.topics.publish(
            self.common(context),
            kind="edit",
            format_name="topic-edit/1",
            content=edit,
            dependencies=(context.evidence, context.rubric, context.selection, context.assessment),
            metadata={
                "selectionArtifactId": str(context.selection.id),
                "selectionSha256": context.selection.sha256,
                "assessmentArtifactId": str(context.assessment.id),
                "rubricArtifactId": str(context.rubric.id),
                "programVersion": SELECTION_POLICY,
            },
        )
        return TopicCompilation(artifact=reference, edit=edit, refusals=tuple(refusals))

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register the v2 activities without replacing historical names."""
        return (self.rubric, self.prepare, self.save, self.save_assessment, self.stop, self.compile)
