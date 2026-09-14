"""Versioned selection decisions using the existing artifact and paid-operation ledger."""

# Activity DTOs need runtime annotations; evidence failures are explicit refusals.
# ruff: noqa: C901, EM101, PLR0912, TRY003, TC001, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ValidationError
from pydantic_ai import TextPart
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    TopicCompiledVideo,
    TopicEditorialRubric,
    TopicEditSpec,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSourceIndex,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.editorial_evidence import validate_reviewer_inspection
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3
from temnia_pipeline.harness.routes import estimate_cost
from temnia_pipeline.harness.runtime_types import RunSnapshot
from temnia_pipeline.harness.source_index import (
    source_index_map,
    validate_source_inspection,
    validate_source_read_ids,
)
from temnia_pipeline.harness.source_index_artifacts import (
    build_or_reuse_topic_source_index,
    load_reusable_topic_source_index,
)
from temnia_pipeline.harness.source_progress import (
    CHECKPOINT_FORMAT,
    SourceProgressCheckpoint,
    checkpoint_sha256,
    inspection_from_checkpoint,
)
from temnia_pipeline.harness.topic_activities import TopicActivities
from temnia_pipeline.harness.topic_compiler import (
    compile_topics_v3,
    validate_topic_edit,
)
from temnia_pipeline.harness.topic_editorial import editorial_routes
from temnia_pipeline.harness.topic_runtime import TopicCompilation, TopicContext
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    SELECTION_COLD_PROMPT_V3,
    SELECTION_INVENTORY_PROMPT,
    SELECTION_PATCH_PROMPT_V3,
    SELECTION_SOURCE_PROMPT_V3,
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    opportunity_inventory_prompt,
    selection_candidates_for_render,
    selection_cold_key,
    selection_cold_prompt,
    selection_patch_prompt_v3,
    selection_prompt,
    selection_semantic_key,
    selection_source_prompt,
    validate_opportunity_inventory,
    validate_selection,
    validate_selection_against_inventory,
)
from temnia_pipeline.harness.topic_selection_runtime import (
    OpportunityInventorySaveRequest,
    SelectionAssessmentResult,
    SelectionCallPlan,
    SelectionContext,
    SelectionRejection,
    SelectionReviewRequest,
    SelectionSaveRequest,
    SelectionSaveResult,
    SelectionStopRequest,
    SourceCheckpointLoadRequest,
    SourceCheckpointLoadResult,
    SourceIndexBuildResult,
    SourceInspectionTrace,
    TopicSourceIndexUseRecord,
    effective_topic_output_tokens,
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

    @staticmethod
    def _required_sentence_ids(evidence: HarnessEvidence, *values: BaseModel | None) -> set[str]:
        """Expand every typed source span so the deciding call must have read it exactly."""
        positions = {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}
        found: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                mapping = cast("dict[str, object]", value)
                first = mapping.get("firstSentenceId")
                last = mapping.get("lastSentenceId")
                if isinstance(first, str) and isinstance(last, str):
                    try:
                        start = positions[first]
                        end = positions[last]
                    except KeyError:
                        found.update((first, last))
                    else:
                        if start <= end:
                            found.update(
                                sentence.id for sentence in evidence.sentences[start : end + 1]
                            )
                        else:
                            found.update((first, last))
                for key, child in mapping.items():
                    if key in {
                        "firstSentenceId",
                        "lastSentenceId",
                        "recommendedLeftLastSentenceId",
                        "recommendedRightFirstSentenceId",
                    } and isinstance(child, str):
                        found.add(child)
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in cast("list[object]", value):
                    visit(child)

        for value in values:
            if value is not None:
                visit(value.model_dump(mode="json"))
        return found

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
            run.editorial_policy != context.program_version
            or context.program_version != TOPIC_SELECTION_POLICY_V3
            or run.evidence_artifact_id != context.evidence.id
        ):
            raise HarnessValidationError(
                "selection activity requires its exact frozen policy and evidence"
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

    async def inventory(self, context: SelectionContext) -> TopicSelectionDraft | None:
        """Load the exact independent source map before author packaging."""
        if context.inventory is None:
            return None
        if context.rubric is None:
            raise HarnessValidationError("opportunity inventory requires a rubric identity")
        await self.require_record(
            context,
            context.inventory,
            format_name="topic-opportunity-inventory/1",
            dependencies=(context.evidence, context.rubric),
        )
        _, evidence, _, _ = await self.load(context.model_copy(update={"inventory": None}))
        result = TopicSelectionDraft.model_validate(await self.read(context, context.inventory))
        validate_opportunity_inventory(evidence, result)
        return result

    async def source_index(self, context: SelectionContext) -> TopicSourceIndex:
        """Load and prove the exact immutable index derived from accepted evidence."""
        if context.source_index is None:
            raise HarnessValidationError("indexed editorial calls require a source index")
        _, evidence, _, _ = await self.load(context.model_copy(update={"source_index": None}))
        return await load_reusable_topic_source_index(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.common(context)),
            source_id=context.run.source_id,
            store=self.owner.ctx.store,
            evidence_ref=context.evidence,
            evidence=evidence,
            index_ref=context.source_index,
        )

    @activity.defn(name="build_topic_source_index")
    async def build_index(self, context: SelectionContext) -> SourceIndexBuildResult:
        """Reuse or build one source-bound hybrid index before editorial calls."""
        _, evidence, _, _ = await self.load(context)
        pulse = asyncio.create_task(self._index_heartbeat()) if activity.in_activity() else None
        try:
            result = await build_or_reuse_topic_source_index(
                self.owner.ctx.settings.database_url,
                scope=self.topics.scope(self.common(context)),
                source_id=context.run.source_id,
                store=self.owner.ctx.store,
                evidence_ref=context.evidence,
                evidence=evidence,
            )
        finally:
            if pulse is not None:
                pulse.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pulse
        index_ref = self.owner._artifact_ref(result.artifact)
        use_record = TopicSourceIndexUseRecord(
            run_id=context.run.run_id,
            source_id=context.run.source_id,
            evidence=context.evidence,
            source_index=index_ref,
            reused=result.reused,
        )
        use_ref = await self.topics.publish(
            self.common(context),
            kind="checks",
            format_name=use_record.format,
            content=use_record,
            dependencies=(context.evidence, index_ref),
            metadata={"reused": result.reused},
        )
        return SourceIndexBuildResult(
            artifact=index_ref,
            use_record=use_ref,
            reused=result.reused,
        )

    @activity.defn(name="load_topic_source_checkpoint")
    async def load_source_checkpoint(
        self, request: SourceCheckpointLoadRequest
    ) -> SourceCheckpointLoadResult:
        """Recover the latest exact compact request after a known provider failure."""
        context = request.context
        plan = request.plan
        await self.load(context)
        if (
            context.source_index is None
            or plan.source_index != context.source_index
            or plan.source_tool_role is None
            or context.source_index not in plan.input_artifacts
        ):
            raise HarnessValidationError(
                "source checkpoint recovery requires the exact indexed call plan"
            )
        async with db.scoped(
            self.owner.ctx.settings.database_url, self.topics.scope(self.common(context))
        ) as conn:
            row = await (
                await conn.execute(
                    """SELECT id FROM harness_artifact
                        WHERE source_id=%s AND kind='checks'
                          AND metadata->>'format'=%s
                          AND metadata->>'runId'=%s
                          AND metadata->>'stage'=%s
                          AND metadata->>'role'=%s
                          AND metadata->>'indexSha256'=%s
                        ORDER BY (metadata->>'requestSequence')::integer DESC
                        LIMIT 1""",
                    (
                        context.run.source_id,
                        CHECKPOINT_FORMAT,
                        str(context.run.run_id),
                        plan.stage,
                        plan.source_tool_role,
                        context.source_index.sha256,
                    ),
                )
            ).fetchone()
        if row is None:
            return SourceCheckpointLoadResult()
        record = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.topics.scope(self.common(context)),
            source_id=context.run.source_id,
            artifact_id=row["id"],
        )
        reference = self.owner._artifact_ref(record)
        checkpoint = SourceProgressCheckpoint.model_validate(await self.read(context, reference))
        if (
            checkpoint_sha256(checkpoint) != record.sha256
            or checkpoint.index_sha256 != context.source_index.sha256
            or checkpoint.role != plan.source_tool_role
            or checkpoint.stage != plan.stage
            or record.metadata.get("checkpointSha256") != record.sha256
            or record.metadata.get("requestSequence") != checkpoint.request_sequence
            or not {item.id for item in plan.input_artifacts} <= set(record.dependency_ids)
        ):
            raise HarnessValidationError("source checkpoint recovery found invalid state")
        if checkpoint.parent_checkpoint_sha256 is not None:
            parent_matches = 0
            for dependency_id in record.dependency_ids:
                dependency = await artifacts._artifact_for_read(
                    self.owner.ctx.settings.database_url,
                    scope=self.topics.scope(self.common(context)),
                    source_id=context.run.source_id,
                    artifact_id=dependency_id,
                )
                if (
                    dependency.kind == "checks"
                    and dependency.metadata.get("format") == CHECKPOINT_FORMAT
                    and dependency.metadata.get("runId") == str(context.run.run_id)
                    and dependency.metadata.get("stage") == plan.stage
                    and dependency.metadata.get("role") == plan.source_tool_role
                    and dependency.metadata.get("indexSha256") == context.source_index.sha256
                    and dependency.metadata.get("checkpointSha256")
                    == checkpoint.parent_checkpoint_sha256
                    and dependency.sha256 == checkpoint.parent_checkpoint_sha256
                ):
                    parent_matches += 1
            if parent_matches != 1:
                raise HarnessValidationError(
                    "source checkpoint recovery found an invalid parent chain"
                )
        return SourceCheckpointLoadResult(checkpoint=checkpoint.model_dump(mode="json"))

    @staticmethod
    async def _index_heartbeat() -> None:
        """Keep long CPU indexing visible to Temporal without changing its result."""
        while True:
            activity.heartbeat("building-topic-source-index")
            await asyncio.sleep(10)

    async def patch_rejection(self, context: SelectionContext) -> SelectionRejection | None:
        """Load a refused settled patch only for the exact assessed request being corrected."""
        if context.rejection is None:
            return None
        if context.selection is None or context.assessment is None or context.rubric is None:
            raise HarnessValidationError("patch correction requires its exact assessed inputs")
        refusal = SelectionRejection.model_validate(await self.read(context, context.rejection))
        await self.require_record(
            context,
            context.rejection,
            format_name="topic-selection-rejection/2",
            dependencies=(
                context.evidence,
                context.rubric,
                context.selection,
                context.assessment,
                refusal.response,
            ),
        )
        if (
            refusal.patch is None
            or refusal.draft is not None
            or not refusal.diagnostics
            or refusal.stage != f"repair:selection:{context.iteration - 1}"
        ):
            raise HarnessValidationError("patch correction lacks its preceding typed refusal")
        # The immutable request dependencies establish authority. Incorrect hashes inside
        # the rejected model output are admission errors for the next request to correct.
        return refusal

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
            metadata={"programVersion": run.editorial_policy},
        )

    @activity.defn(name="prepare_topic_selection_call")
    async def prepare(self, context: SelectionContext) -> SelectionCallPlan:  # noqa: PLR0915 — four versioned native stages share one receipt path
        """Prepare one native schema call with all evidence and rubric dependencies."""
        run, evidence, rubric, selection = await self.load(context)
        if rubric is None or context.rubric is None:
            raise HarnessValidationError("editorial calls require a frozen rubric")
        author, verifier = editorial_routes(
            run.route_snapshot,
            author_index=context.author_index,
            verifier_index=context.verifier_index,
        )
        dependencies = [context.evidence, context.rubric]
        source_tool_role = None
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
            version = SELECTION_COLD_PROMPT_V3
            synthetic = "topic_selection_cold"
        elif selection is not None and context.assessment is None:
            if context.selection is None:
                raise HarnessValidationError("source review requires its exact selection artifact")
            index = await self.source_index(context)
            if context.source_index is None:
                raise HarnessValidationError("source review requires its exact source index")
            dependencies.append(context.source_index)
            source_tool_role = "source_reviewer"
            prompt = selection_source_prompt(
                evidence,
                selection.draft,
                rubric,
                source_index_map(index, index_sha256=context.source_index.sha256),
            )
            stage = f"verify:selection:source:{context.iteration}"
            version = SELECTION_SOURCE_PROMPT_V3
            synthetic = (
                "topic_selection_source_selected_v3"
                if selection.draft.proposal.candidates
                else "topic_selection_source"
            )
            dependencies.append(context.selection)
        elif selection is not None:
            assessment = await self.assessment(context)
            if assessment is None or context.selection is None or context.assessment is None:
                raise HarnessValidationError("selection repair requires exact assessed state")
            refusal = await self.patch_rejection(context)
            prompt = selection_patch_prompt_v3(
                evidence,
                selection,
                assessment,
                context.selection.sha256,
                rejected_patch=refusal.patch if refusal is not None else None,
                diagnostics=refusal.diagnostics if refusal is not None else (),
            )
            stage = f"repair:selection:{context.iteration}"
            version = SELECTION_PATCH_PROMPT_V3
            synthetic = "topic_selection_patch"
            dependencies.extend((context.selection, context.assessment))
            if context.rejection is not None:
                dependencies.append(context.rejection)
        elif context.inventory is None and not context.inventory_attempted:
            index = await self.source_index(context)
            if context.source_index is None:
                raise HarnessValidationError("inventory requires its exact source index")
            dependencies.append(context.source_index)
            source_tool_role = "inventory"
            prompt = opportunity_inventory_prompt(
                source_index_map(index, index_sha256=context.source_index.sha256), rubric
            )
            stage = "verify:selection:inventory:0"
            version = SELECTION_INVENTORY_PROMPT
            synthetic = "topic_opportunity_inventory"
        else:
            index = await self.source_index(context)
            if context.source_index is None:
                raise HarnessValidationError("authoring requires its exact source index")
            dependencies.append(context.source_index)
            source_tool_role = "author"
            inventory = await self.inventory(context) if context.inventory else None
            if inventory is not None and context.inventory is not None:
                dependencies.append(context.inventory)
            navigation = None
            rejected = None
            diagnostics: tuple[str, ...] = context.inventory_diagnostics
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
                rejected = refusal.draft
                diagnostics = (*diagnostics, *refusal.diagnostics)
                dependencies.append(context.rejection)
            prompt = selection_prompt(
                source_index_map(index, index_sha256=context.source_index.sha256),
                rubric,
                navigation=navigation,
                source_inventory=inventory,
                rejected_output=rejected,
                diagnostics=diagnostics,
            )
            stage = f"proposal:selection:{context.iteration}"
            version = SELECTION_AUTHOR_PROMPT_V3
            synthetic = "topic_selection_author_v3"
        route = verifier if stage.startswith("verify:") else author
        estimate_cost(
            route,
            payload_bytes=len(prompt.encode()),
            max_output_tokens=effective_topic_output_tokens(run.config.maxOutputTokens, route),
        )
        synthetic_payload = self.owner._recorded_output(synthetic)
        if synthetic_payload is not None and version == SELECTION_PATCH_PROMPT_V3:
            # Fixture operations remain authored test data; only run-local immutable hashes vary.
            patch = TopicSelectionPatchV3.model_validate(synthetic_payload["output"])
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
            program_version=context.program_version,
            schema_version={
                SELECTION_AUTHOR_PROMPT_V3: "topic-selection-draft/2",
                SELECTION_INVENTORY_PROMPT: "topic-selection-draft/2",
                SELECTION_SOURCE_PROMPT_V3: "topic-selection-portfolio/4",
            }.get(version, version),
            author=author,
            verifier=verifier,
            input_artifacts=tuple(dependencies),
            source_index=context.source_index if source_tool_role is not None else None,
            source_tool_role=source_tool_role,
            candidate_selection=(
                context.selection if source_tool_role == "source_reviewer" else None
            ),
            media_evidence=(context.evidence if source_tool_role == "source_reviewer" else None),
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
        if not rows:
            raise HarnessValidationError(
                "selection call has no settled response from its assigned family"
            )
        matches: list[HarnessArtifactRef] = []
        for row in rows:
            if row["family"] != route.family:
                continue
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
                or metadata.get("programVersion") != plan.program_version
                or metadata.get("promptVersion") != plan.prompt_version
                or metadata.get("schemaVersion") != plan.schema_version
                or metadata.get("route") != route.model_dump(mode="json")
                or not {ref.id for ref in plan.input_artifacts} <= set(retained.dependency_ids)
                or not isinstance(metadata.get("requestHash"), str)
            ):
                continue
            _, input_hash, config_hash = ledger.operation_identity(
                run_id=run.id,
                kind="model",
                inputs={**selection_call_inputs(plan), "requestHash": metadata["requestHash"]},
                config={
                    **selection_call_config(plan, run.config.maxOutputTokens),
                    "programVersion": plan.program_version,
                    "promptVersion": plan.prompt_version,
                    "schemaVersion": plan.schema_version,
                    "route": route.model_dump(mode="json"),
                },
            )
            if row["input_hash"] != input_hash or row["config_hash"] != config_hash:
                continue
            reference = self.owner._artifact_ref(retained)
            response = MODEL_RESPONSE_ADAPTER.validate_python(await self.read(context, reference))
            text = "".join(part.content for part in response.parts if isinstance(part, TextPart))
            if output is None:
                if response.finish_reason != "tool_call":
                    matches.append(reference)
                continue
            try:
                parsed = type(output).model_validate_json(text)
            except (ValidationError, ValueError):
                continue
            if parsed == output:
                matches.append(reference)
        if len(matches) != 1:
            raise HarnessValidationError(
                "selection call has no unique final response matching its typed output"
            )
        return matches[0]

    async def inspection_ref(
        self,
        context: SelectionContext,
        plan: SelectionCallPlan,
        inspection: SourceInspectionTrace | None,
        response: HarnessArtifactRef,
        required_sentence_ids: set[str],
    ) -> HarnessArtifactRef | None:
        """Retain successful source-tool coverage for one indexed editorial answer."""
        if plan.source_tool_role is None:
            if inspection is not None:
                raise HarnessValidationError("non-indexed model call carried a source inspection")
            return None
        if plan.synthetic_payload is not None:
            return None
        if inspection is None or context.source_index is None:
            raise HarnessValidationError("indexed editorial response has no source inspection")
        if inspection.role != plan.source_tool_role or inspection.stage != plan.stage:
            raise HarnessValidationError("source inspection belongs to another editorial call")
        index = await self.source_index(context)
        if inspection.index_sha256 != context.source_index.sha256:
            raise HarnessValidationError("source inspection names a different index")
        validate_source_inspection(index, inspection)
        validate_source_read_ids(index, inspection, required_sentence_ids)
        if plan.source_tool_role == "source_reviewer":
            _, evidence, _, selection = await self.load(context)
            if selection is None or context.selection is None:
                raise HarnessValidationError("source reviewer inspection has no accepted selection")
            validate_reviewer_inspection(
                index,
                selection,
                evidence,
                inspection,
                index_sha256=context.source_index.sha256,
                selection_sha256=context.selection.sha256,
                evidence_sha256=context.evidence.sha256,
            )
        checkpoint_ref = None
        if inspection.format == "topic-source-inspection/2":
            response_record = await artifacts._artifact_for_read(
                self.owner.ctx.settings.database_url,
                scope=self.topics.scope(self.common(context)),
                source_id=context.run.source_id,
                artifact_id=response.id,
            )
            matches: list[HarnessArtifactRef] = []
            for dependency_id in response_record.dependency_ids:
                candidate = await artifacts._artifact_for_read(
                    self.owner.ctx.settings.database_url,
                    scope=self.topics.scope(self.common(context)),
                    source_id=context.run.source_id,
                    artifact_id=dependency_id,
                )
                if (
                    candidate.kind != "checks"
                    or candidate.metadata.get("format") != CHECKPOINT_FORMAT
                    or candidate.metadata.get("checkpointSha256") != inspection.checkpoint_sha256
                    or candidate.metadata.get("indexSha256") != context.source_index.sha256
                    or candidate.metadata.get("role") != plan.source_tool_role
                    or candidate.metadata.get("stage") != plan.stage
                    or candidate.metadata.get("runId") != str(context.run.run_id)
                    or candidate.metadata.get("requestSequence") != inspection.request_sequence
                ):
                    continue
                reference = self.owner._artifact_ref(candidate)
                checkpoint = SourceProgressCheckpoint.model_validate(
                    await self.read(context, reference)
                )
                if inspection_from_checkpoint(checkpoint) == inspection:
                    matches.append(reference)
            if len(matches) != 1:
                raise HarnessValidationError(
                    "source inspection has no unique durable progress checkpoint"
                )
            checkpoint_ref = matches[0]
        return await self.topics.publish(
            self.common(context),
            kind="checks",
            format_name=inspection.format,
            content=inspection,
            dependencies=tuple(
                item
                for item in (
                    context.evidence,
                    context.source_index,
                    context.selection if plan.source_tool_role == "source_reviewer" else None,
                    response,
                    checkpoint_ref,
                )
                if item is not None
            ),
            metadata={"role": plan.source_tool_role, "stage": plan.stage},
        )

    @activity.defn(name="save_topic_opportunity_inventory_v3")
    async def save_inventory(self, request: OpportunityInventorySaveRequest) -> SelectionSaveResult:
        """Retain the verifier's source map before exposing any author selection."""
        context = request.context
        _, evidence, rubric, selection = await self.load(context)
        if (
            rubric is None
            or context.rubric is None
            or context.inventory is not None
            or selection is not None
        ):
            raise HarnessValidationError("inventory saving requires a fresh v3 source-map state")
        plan = await self.prepare(context)
        if request.schema_error is None and request.inventory is None:
            raise HarnessValidationError("inventory saving requires a typed output or diagnostic")
        response = await self.response_ref(context, plan, request.inventory)
        if request.schema_error is not None or request.inventory is None:
            return SelectionSaveResult(
                diagnostics=(request.schema_error or "Opportunity inventory is unavailable.",)
            )
        try:
            inspection = await self.inspection_ref(
                context,
                plan,
                request.inspection,
                response,
                self._required_sentence_ids(evidence, request.inventory),
            )
            validate_opportunity_inventory(evidence, request.inventory)
        except (HarnessValidationError, ValidationError, ValueError) as error:
            return SelectionSaveResult(diagnostics=(str(error),))
        dependencies = (*plan.input_artifacts, response)
        if inspection is not None:
            dependencies = (*dependencies, inspection)
        reference = await self.topics.publish(
            self.common(context),
            kind="proposal",
            format_name="topic-opportunity-inventory/1",
            content=request.inventory,
            dependencies=dependencies,
            metadata={
                "programVersion": context.program_version,
                "generatorFamily": plan.verifier.family,
            },
        )
        return SelectionSaveResult(selection=reference, draft=request.inventory)

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
        inspection = None
        diagnostics: tuple[str, ...] = ()
        draft = request.draft
        try:
            if request.schema_error is not None:
                raise HarnessValidationError(request.schema_error)  # noqa: TRY301
            inspection = await self.inspection_ref(
                context,
                plan,
                request.inspection,
                response,
                self._required_sentence_ids(evidence, draft),
            )
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
            if previous is None:
                inventory = await self.inventory(context)
                if inventory is not None:
                    validate_selection_against_inventory(inventory, draft)
        except (HarnessValidationError, ValidationError, ValueError) as error:
            diagnostics = (str(error),)
        dependencies = (*plan.input_artifacts, response)
        if inspection is not None:
            dependencies = (*dependencies, inspection)
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
                metadata={"programVersion": context.program_version},
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
            metadata={
                "programVersion": context.program_version,
                "generatorFamily": plan.author.family,
            },
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
        inspection_refs: list[HarnessArtifactRef] = []
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
        admitted_source_review = request.source_review
        # Even a schema-invalid source answer is retained and bound; it is never a pass.
        if request.source_dispatched:
            source_plan = await self.prepare(source_context)
            response_refs.append(
                await self.response_ref(source_context, source_plan, request.source_review)
            )
            try:
                source_inspection = await self.inspection_ref(
                    source_context,
                    source_plan,
                    request.source_inspection,
                    response_refs[-1],
                    self._required_sentence_ids(evidence, record.draft, request.source_review),
                )
            except (HarnessValidationError, ValidationError, ValueError) as error:
                admitted_source_review = None
                reasons.append(f"Source inspection is unavailable: {error}")
            else:
                if source_inspection is not None:
                    inspection_refs.append(source_inspection)
        author, verifier = editorial_routes(
            run.route_snapshot,
            author_index=context.author_index,
            verifier_index=context.verifier_index,
        )
        assessment = assess_selection(
            evidence,
            record,
            context.selection.sha256,
            cold_reviews=admitted_cold,
            source_review=admitted_source_review,
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
            dependencies=(
                context.evidence,
                context.rubric,
                context.selection,
                *response_refs,
                *inspection_refs,
            ),
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
        proposal = selection_candidates_for_render(record, assessment, require_complete_review=True)
        compiler = compile_topics_v3
        videos: list[TopicCompiledVideo] = []
        refusals: list[str] = []
        compiled = compiler(
            evidence,
            proposal.model_copy(update={"candidates": []}),
            evidence_artifact_id=context.evidence.id,
            evidence_sha256=context.evidence.sha256,
        )
        for candidate in proposal.candidates:
            try:
                single = compiler(
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
                "programVersion": context.program_version,
            },
        )
        return TopicCompilation(artifact=reference, edit=edit, refusals=tuple(refusals))

    def activities(self) -> Sequence[Callable[..., object]]:
        """Register versioned selection activities without replacing historical names."""
        return (
            self.rubric,
            self.build_index,
            self.load_source_checkpoint,
            self.prepare,
            self.save_inventory,
            self.save,
            self.save_assessment,
            self.stop,
            self.compile,
        )
