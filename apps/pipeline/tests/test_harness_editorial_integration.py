"""Real ledger, object-store and Temporal boundaries for the new editorial policy."""

# Tests share recorded source builders; only the external provider and media bytes are fixtures.
# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from obstore.store import MemoryStore
from pydantic import BaseModel
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, PydanticAIWorkflow
from pydantic_ai.models.function import FunctionModel
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterProposal,
    ChapterRender,
    ChapterRenders,
    HarnessArtifactRef,
    SpeechCoverage,
    Status1,
)
from temnia_pipeline.harness import artifacts, runs
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.compiler import compile_chapters
from temnia_pipeline.harness.editorial import (
    EDITORIAL_ASSESSMENT_PROMPT_VERSION,
    EDITORIAL_REPAIR_PROMPT_VERSION,
    EDITORIAL_VERDICT_SCHEMA_VERSION,
    EditorialRepairV1,
    EditorialVerdictV2,
    render_editorial_assessment_prompt,
)
from temnia_pipeline.harness.editorial_activities import EditorialActivities
from temnia_pipeline.harness.editorial_runtime import (
    CompileEditorialRepairRequest,
    EditorialContextRequest,
    FinalizeEditorialRequest,
)
from temnia_pipeline.harness.editorial_workflow import editorial_model_deps
from temnia_pipeline.harness.models import (
    HarnessModelDeps,
    ModelRuntime,
    chapter_editorial_assess_v1,
    chapter_editorial_repair_v1,
    clear_model_runtime,
    configure_model_runtime,
)
from temnia_pipeline.harness.rendering import kept_sections
from temnia_pipeline.harness.routes import RouteEntry, select_route, select_verifier_route
from temnia_pipeline.harness.runtime_types import (
    ClaimRepairRequest,
    CompiledRevision,
    CompileProposalRequest,
    FinalizeVerificationRequest,
    PrepareVerificationRequest,
    RenderRevisionResult,
    RunRef,
    StartRunRequest,
    VerificationPlan,
)
from temnia_pipeline.harness.validators import HarnessValidationError, rounded_milliseconds
from test_harness_editorial import _case, _edge_verdict, _tail_repair
from test_harness_hierarchy_workflow import _settings
from test_harness_runs import SEEDED, pipeline_url, ready_source, start_request

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from obstore.store import S3Store
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    from temnia_pipeline.ingest import Context


class ReceiptWorkflowInput(BaseModel):
    """Exact post-render production call inputs, including immutable artifacts."""

    deps: HarnessModelDeps
    prompt: str
    finalize: FinalizeVerificationRequest


@workflow.defn
class EditorialReceiptWorkflow(PydanticAIWorkflow):
    """Execute the real guarded model activity followed by production finalization."""

    __pydantic_ai_agents__ = (chapter_editorial_assess_v1,)

    @workflow.run
    async def run(self, request: ReceiptWorkflowInput) -> HarnessArtifactRef:
        result = await chapter_editorial_assess_v1.run(
            request.prompt, deps=request.deps, model_settings={"max_tokens": 8192}
        )
        return await workflow.execute_activity(
            "finalize_chapter_verification",
            request.finalize.model_copy(update={"verdict": result.output.model_dump(mode="json")}),
            start_to_close_timeout=timedelta(seconds=30),
            result_type=HarnessArtifactRef,
        )


@dataclass
class EditorialIntegration:
    """Production persistence resources with a recorded external model only."""

    owner: HarnessActivities
    request: ReceiptWorkflowInput
    calls: list[str]
    start: StartRunRequest
    context: EditorialContextRequest
    repair: EditorialRepairV1


@pytest.fixture
async def editorial_integration(  # noqa: PLR0915
    tmp_path: Path, request: pytest.FixtureRequest
) -> AsyncGenerator[EditorialIntegration]:
    url = pipeline_url()
    source_id = await ready_source(url)
    harness_settings, routes = _settings()
    harness_settings = replace(harness_settings, max_repairs=1)
    start = start_request(source_id, routes)
    start = start.model_copy(
        update={
            "editorial_policy": "chapter-editorial/1",
            "request": start.request.model_copy(
                update={"config": harness_settings.allowed_config()}
            ),
        }
    )
    started = await runs.start_or_refetch_run(
        url, start=start, settings=harness_settings, route_snapshot=routes
    )
    store = cast("S3Store", MemoryStore())
    owner = HarnessActivities(
        cast("Context", SimpleNamespace(settings=SimpleNamespace(database_url=url), store=store)),
        harness_settings,
        routes,
    )

    async def publish(
        kind: str,
        body: object,
        dependencies: tuple[HarnessArtifactRef, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> HarnessArtifactRef:
        artifact = await artifacts.publish_json(
            url,
            scope=SEEDED,
            source_id=source_id,
            store=store,
            identity=artifacts.ArtifactIdentity(
                kind=kind,
                fingerprint=artifacts.fingerprint_for(kind=kind, inputs={"body": body}, config={}),
                transcript_id=started.run.transcript.transcript_id if kind == "evidence" else None,
                transcript_revision=started.run.transcript.revision if kind == "evidence" else None,
            ),
            content=body,
            metadata={"runId": str(start.request.runId), **(metadata or {})},
            dependency_ids=tuple(item.id for item in dependencies),
        )
        return owner._artifact_ref(artifact)

    evidence, proposal, _ = _case()
    retained_generation = cast("int | None", getattr(request, "param", None))
    if retained_generation is not None:
        evidence = evidence.model_copy(
            update={
                "speechCoverage": SpeechCoverage(
                    detector="recorded-clear-detector",
                    detectorHash="d" * 64,
                    detectorRevision="fixture/1",
                    status=Status1.needs_review,
                    intervals=[],
                    uncoveredSpeechMs=1000,
                    uncoveredTailMs=0,
                    warnings=["Global status is not local timing evidence."],
                )
            }
        )
    evidence = evidence.model_copy(
        update={
            "sourceId": source_id,
            "transcriptId": started.run.transcript.transcript_id,
            "transcriptSha256": started.run.transcript.sha256,
        }
    )
    evidence_ref = await publish("evidence", evidence.model_dump(mode="json"))
    proposal_ref = await publish("proposal", proposal.model_dump(mode="json"), (evidence_ref,))
    await runs.attach_evidence(
        url,
        scope=SEEDED,
        source_id=source_id,
        run_id=start.request.runId,
        artifact_id=evidence_ref.id,
    )
    edit = compile_chapters(
        evidence,
        proposal,
        evidence_artifact_id=evidence_ref.id,
        evidence_sha256=evidence_ref.sha256,
    )
    edit_ref = await publish(
        "edit",
        edit.model_dump(mode="json"),
        (evidence_ref, proposal_ref),
        {"proposalArtifactId": str(proposal_ref.id)},
    )
    renders: list[ChapterRender] = []
    reports: list[dict[str, object]] = []
    for section in kept_sections(edit):
        report: dict[str, object] = {
            "version": 1,
            "editorialStatus": "not_run",
            "editorialReasons": [],
            "editSha256": edit_ref.sha256,
            "verifierFamily": None,
            "technicalChecks": [
                {
                    "sectionId": section.section_id,
                    "name": "recorded-fixture",
                    "status": "pass",
                    "expected": None,
                    "measured": None,
                    "message": "fixture",
                }
            ],
        }
        checks = await publish("checks", report, (edit_ref,))
        media = await publish("render", {"fixtureMedia": section.section_id}, (edit_ref,))
        renders.append(
            ChapterRender(
                captions=None,
                checks=checks,
                durationMs=rounded_milliseconds(section.duration),
                editSha256=edit_ref.sha256,
                media=media,
                sectionId=section.section_id,
            )
        )
        reports.append(report)
    descriptor = ChapterRenders(
        format="chapter-renders/1",
        editSha256=edit_ref.sha256,
        runId=start.request.runId,
        renders=renders,
    )
    descriptor_ref = await publish(
        "render",
        descriptor.model_dump(mode="json"),
        (edit_ref, *(render.checks for render in renders if render.checks)),
    )
    generator = select_route(routes, "propose")
    critic = select_verifier_route(
        routes, "verify", generation_families=frozenset({generator.family})
    )
    verdict = _edge_verdict(
        evidence, edit, code="timing_uncertainty" if retained_generation is not None else None
    )
    repair = EditorialRepairV1.model_validate_json(
        _tail_repair(evidence, proposal).model_dump_json(exclude={"sections": {"__all__": {"id"}}})
    )
    ref = RunRef(
        scope_organization_id=SEEDED.organizationId,
        scope_user_id=SEEDED.userId,
        source_id=source_id,
        run_id=start.request.runId,
    )
    finalize = FinalizeVerificationRequest(
        run=ref,
        revision=1,
        edit=edit_ref,
        rendered=RenderRevisionResult(
            descriptor=descriptor_ref, technical_report=tuple(reports), technical_passed=True
        ),
        verdict=verdict.model_dump(mode="json"),
        verifier_family=critic.family,
    )
    deps = HarnessModelDeps(
        scope=SEEDED,
        source_id=source_id,
        run_id=start.request.runId,
        stage="verify:revision:1",
        program_version="chapter-workflow/1",
        prompt_version=(
            f"chapter-editorial-assess-v{retained_generation}"
            if retained_generation is not None
            else EDITORIAL_ASSESSMENT_PROMPT_VERSION
        ),
        schema_version=EDITORIAL_VERDICT_SCHEMA_VERSION,
        route=critic,
        operation_inputs={
            "editArtifactId": str(edit_ref.id),
            "editSha256": edit_ref.sha256,
            "descriptorArtifactId": str(descriptor_ref.id),
            "descriptorSha256": descriptor_ref.sha256,
        },
        operation_config={"revision": 1, "maxOutputTokens": 8192},
        input_artifact_ids=(evidence_ref.id, edit_ref.id, descriptor_ref.id),
        dispatch_limit=32,
    )
    calls: list[str] = []

    async def response(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append("critic")
        return ModelResponse(
            parts=[TextPart(verdict.model_dump_json())], model_name=critic.gateway_model
        )

    async def repair_response(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append("repair")
        return ModelResponse(
            parts=[TextPart(repair.model_dump_json())], model_name=generator.gateway_model
        )

    def model_factory(route: RouteEntry) -> FunctionModel:
        assert route in (critic, generator)
        return FunctionModel(
            response if route == critic else repair_response, model_name=route.gateway_model
        )

    configure_model_runtime(
        ModelRuntime(
            database_url=url,
            store=store,
            cassette_store=CassetteStore(tmp_path),
            model_factory=model_factory,
            allow_outside_activity=True,
        )
    )
    try:
        yield EditorialIntegration(
            owner,
            ReceiptWorkflowInput(
                deps=deps,
                finalize=finalize,
                prompt=render_editorial_assessment_prompt(
                    evidence=evidence,
                    edit=edit,
                    brief=start.request.brief,
                    technical_report={"sections": reports},
                ),
            ),
            calls,
            start,
            EditorialContextRequest(
                run=ref,
                evidence=evidence_ref,
                compiled=CompiledRevision(
                    proposal_artifact=proposal_ref, edit_artifact=edit_ref, edit=edit, revision=1
                ),
                generation_families=(generator.family,),
                iteration=0,
            ),
            repair,
        )
    finally:
        clear_model_runtime()
        await db.close_pool()


@pytest.mark.parametrize(
    "corruption", ["verdict", "family", "edit_hash", "descriptor_hash", "technical_report"]
)
async def test_postrender_receipt_rejects_tampering_before_publication(
    editorial_integration: EditorialIntegration, corruption: str
) -> None:
    case = editorial_integration
    await chapter_editorial_assess_v1.run(
        case.request.prompt, deps=case.request.deps, model_settings={"max_tokens": 8192}
    )
    request = case.request.finalize
    if corruption == "verdict":
        request = request.model_copy(
            update={
                "verdict": EditorialVerdictV2(status="passed", findings=()).model_dump(mode="json")
            }
        )
    elif corruption == "family":
        request = request.model_copy(update={"verifier_family": "fabricated-family"})
    elif corruption == "edit_hash":
        request = request.model_copy(
            update={"edit": request.edit.model_copy(update={"sha256": "f" * 64})}
        )
    elif corruption == "descriptor_hash":
        request = request.model_copy(
            update={
                "rendered": request.rendered.model_copy(
                    update={
                        "descriptor": request.rendered.descriptor.model_copy(
                            update={"sha256": "f" * 64}
                        )
                    }
                )
            }
        )
    else:
        request = request.model_copy(
            update={"rendered": request.rendered.model_copy(update={"technical_report": ()})}
        )
    with pytest.raises(HarnessValidationError):
        await case.owner.finalize_chapter_verification(request)
    async with db.scoped(pipeline_url(), SEEDED) as conn:
        rows = await (
            await conn.execute(
                "SELECT id FROM harness_artifact WHERE source_id=%s "
                "AND metadata->>'format'='chapter-verification/1'",
                (request.run.source_id,),
            )
        ).fetchall()
    assert rows == []
    assert case.calls == ["critic"]


async def test_postrender_temporal_retains_real_response_and_reuses_it_without_redispatch(
    editorial_integration: EditorialIntegration,
) -> None:
    case = editorial_integration
    queue = f"editorial-receipt-{uuid4()}"
    async with (
        await WorkflowEnvironment.start_time_skipping(plugins=[PydanticAIPlugin()]) as environment,
        Worker(
            environment.client,
            task_queue=queue,
            workflows=[EditorialReceiptWorkflow],
            activities=[case.owner.finalize_chapter_verification],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        first = await environment.client.execute_workflow(
            EditorialReceiptWorkflow.run,
            case.request,
            id=f"editorial-first-{uuid4()}",
            task_queue=queue,
        )
        second = await environment.client.execute_workflow(
            EditorialReceiptWorkflow.run,
            case.request,
            id=f"editorial-second-{uuid4()}",
            task_queue=queue,
        )
    assert first == second
    assert case.calls == ["critic"]
    body = await artifacts.read_artifact_json(
        pipeline_url(),
        scope=SEEDED,
        source_id=case.request.finalize.run.source_id,
        store=case.owner.ctx.store,
        artifact_id=first.id,
    )
    assert isinstance(body, dict)
    assert body["editorial"] == case.request.finalize.verdict
    assert body["verdict"]["status"] == "needs_review"


@pytest.mark.parametrize("editorial_integration", [1, 2, 3, 99], indirect=True)
async def test_postrender_grounding_uses_the_accepted_prompt_generation(
    editorial_integration: EditorialIntegration,
) -> None:
    case = editorial_integration
    await chapter_editorial_assess_v1.run(
        case.request.prompt, deps=case.request.deps, model_settings={"max_tokens": 8192}
    )
    version = case.request.deps.prompt_version
    if version in {"chapter-editorial-assess-v1", "chapter-editorial-assess-v2"}:
        first = await case.owner.finalize_chapter_verification(case.request.finalize)
        second = await case.owner.finalize_chapter_verification(case.request.finalize)
        assert second == first
        body = await artifacts.read_artifact_json(
            pipeline_url(),
            scope=SEEDED,
            source_id=case.context.run.source_id,
            store=case.owner.ctx.store,
            artifact_id=first.id,
        )
        assert isinstance(body, dict)
        assert body["editorial"] == case.request.finalize.verdict
    else:
        message = "local cut evidence" if version.endswith("-v3") else "unsupported prompt version"
        with pytest.raises(HarnessValidationError, match=message):
            await case.owner.finalize_chapter_verification(case.request.finalize)
        async with db.scoped(pipeline_url(), SEEDED) as conn:
            rows = await (
                await conn.execute(
                    "SELECT id FROM harness_artifact WHERE source_id=%s "
                    "AND metadata->>'format'='chapter-verification/1'",
                    (case.context.run.source_id,),
                )
            ).fetchall()
        assert rows == []
    assert case.calls == ["critic"]


@pytest.mark.parametrize("editorial_integration", [1, 2, 3, 99], indirect=True)
async def test_preassessment_grounding_uses_the_accepted_prompt_generation(
    editorial_integration: EditorialIntegration,
) -> None:
    case = editorial_integration
    editor = EditorialActivities(case.owner)
    plan = VerificationPlan(
        route=case.request.deps.route,
        prompt=case.request.prompt,
        editorial_prompt_version=case.request.deps.prompt_version,
    )
    # A foreign version is rejected at prepared dispatch as well as finalization;
    # record that response through the existing metering seam to exercise the latter.
    deps = editorial_model_deps(
        case.start.request,
        case.context,
        plan.model_copy(update={"editorial_prompt_version": "chapter-editorial-assess-v2"}),
        stage="verify:editorial:0",
    )
    deps = deps.model_copy(update={"prompt_version": case.request.deps.prompt_version})
    result = await chapter_editorial_assess_v1.run(
        case.request.prompt, deps=deps, model_settings={"max_tokens": 8192}
    )
    request = FinalizeEditorialRequest(
        context=case.context,
        verdict=result.output,
        model_stage="verify:editorial:0",
        verifier_family=case.request.deps.route.family,
    )
    version = deps.prompt_version
    if version in {"chapter-editorial-assess-v1", "chapter-editorial-assess-v2"}:
        first = await editor.finalize(request)
        second = await editor.finalize(request)
        assert second == first
        assert first.verdict.model_dump(mode="json") == case.request.finalize.verdict
    else:
        message = "local cut evidence" if version.endswith("-v3") else "unsupported prompt version"
        with pytest.raises(HarnessValidationError, match=message):
            await editor.finalize(request)
    assert case.calls == ["critic"]


@pytest.mark.parametrize("preserve_existing_boundaries", [False, True])
async def test_grounded_repair_publishes_only_the_scoped_candidate_with_real_receipts(
    editorial_integration: EditorialIntegration,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preserve_existing_boundaries: bool,
) -> None:
    case = editorial_integration
    activities = EditorialActivities(case.owner)
    critic = case.request.deps.route

    def recorded_output(_stage: str) -> None:
        return None

    monkeypatch.setattr(case.owner, "_recorded_output", recorded_output)
    plan = await activities.prepare(case.context)
    assert plan.editorial_prompt_version == EDITORIAL_ASSESSMENT_PROMPT_VERSION
    assert plan.route == critic
    assessment_stage = "verify:editorial:0"
    assessment = await chapter_editorial_assess_v1.run(
        plan.prompt or "",
        deps=editorial_model_deps(case.start.request, case.context, plan, stage=assessment_stage),
        model_settings={"max_tokens": 8192},
    )
    finalized = await activities.finalize(
        FinalizeEditorialRequest(
            context=case.context,
            verdict=assessment.output,
            model_stage=assessment_stage,
            verifier_family=critic.family,
        )
    )
    context = case.context.model_copy(update={"assessment": finalized.artifact})
    await runs.claim_repair(
        pipeline_url(),
        request=ClaimRepairRequest(
            run=context.run, workflow=case.start.workflow, expected_repair_count=0
        ),
        max_repairs=1,
    )
    assert case.owner.snapshot is not None
    generator = select_route(case.owner.snapshot, "propose")
    repair_plan = await activities.prepare(context)
    assert repair_plan.editorial_prompt_version == EDITORIAL_REPAIR_PROMPT_VERSION
    assert repair_plan.route == generator
    repair_stage = "editorial:repair:1"
    repair = await chapter_editorial_repair_v1.run(
        repair_plan.prompt or "",
        deps=editorial_model_deps(
            case.start.request, context, repair_plan, stage=repair_stage, repair=True
        ),
        model_settings={"max_tokens": 8192},
    )
    request = CompileEditorialRepairRequest(
        context=context,
        repair=repair.output,
        model_stage=repair_stage,
        generator_family=generator.family,
        seen_candidate_hashes=(finalized.candidate_sha256,),
        preserve_existing_boundaries=preserve_existing_boundaries,
    )
    result = await activities.compile_repair(request)
    repeated = await activities.compile_repair(request)
    assert result == repeated
    assert result.result.compiled is not None
    compiled = result.result.compiled
    assert compiled.edit.compilerVersion == (
        "chapter-compiler/3" if preserve_existing_boundaries else "chapter-compiler/2"
    )
    assert compiled.edit.sections[0] == context.compiled.edit.sections[0]
    assert compiled.edit.boundaries[1].time == context.compiled.edit.boundaries[1].time
    assert compiled.edit.sections[-1].kind == "drop"
    assert result.candidate_sha256 != finalized.candidate_sha256
    retained = await artifacts._artifact_for_read(
        pipeline_url(),
        scope=SEEDED,
        source_id=context.run.source_id,
        artifact_id=compiled.edit_artifact.id,
    )
    assert context.assessment is not None
    assert {context.assessment.id, context.compiled.edit_artifact.id} <= set(
        retained.dependency_ids
    )
    if preserve_existing_boundaries:
        assert context.compiled.proposal_artifact.id in retained.dependency_ids
        replacement_body = await artifacts.read_artifact_json(
            pipeline_url(),
            scope=SEEDED,
            source_id=context.run.source_id,
            store=case.owner.ctx.store,
            artifact_id=compiled.proposal_artifact.id,
        )
        # This pair is valid and belongs to this run, but the retained repair
        # response was conditioned on the original pair, not its own output.
        with pytest.raises(
            HarnessValidationError, match="prior pair differs from the accepted repair response"
        ):
            await case.owner.compile_chapter_proposal(
                CompileProposalRequest(
                    run=context.run,
                    evidence=context.evidence,
                    proposal=ChapterProposal.model_validate(replacement_body),
                    generator_family=generator.family,
                    model_stage=repair_stage,
                    prior_proposal_artifact=compiled.proposal_artifact,
                    prior_edit_artifact=compiled.edit_artifact,
                )
            )
    assert case.calls == ["critic", "repair"]


async def test_postrender_preparation_freezes_the_current_editorial_prompt_generation(
    editorial_integration: EditorialIntegration,
) -> None:
    case = editorial_integration
    plan = await case.owner.prepare_chapter_verification(
        PrepareVerificationRequest(
            run=case.context.run,
            evidence=case.context.evidence,
            proposal=case.context.compiled.proposal_artifact,
            edit=case.context.compiled.edit_artifact,
            rendered=case.request.finalize.rendered,
            generation_families=case.context.generation_families,
        )
    )
    assert plan.prompt is not None
    assert plan.editorial_v2
    assert plan.editorial_prompt_version == EDITORIAL_ASSESSMENT_PROMPT_VERSION
    assert case.calls == []
