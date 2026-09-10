"""Outcome tests for the automatic editorial program with recorded judgments."""

# pyright: reportPrivateUsage=false
# Reuse the corpus builders; model/Temporal I/O is the only substituted boundary.
# ruff: noqa: PLR0915, C901, ANN401, TRY003, EM101
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.harness import editorial_workflow as program
from temnia_pipeline.harness.compiler import compile_chapters
from temnia_pipeline.harness.editorial import (
    EditorialRepairV1,
    EditorialVerdictV2,
    editorial_candidate_fingerprint,
    ground_editorial_verdict,
    validate_editorial_repair,
)
from temnia_pipeline.harness.editorial_runtime import EditorialAssessment, EditorialRepairResult
from temnia_pipeline.harness.models import CompactChapterProposal, canonical_chapter_proposal
from temnia_pipeline.harness.routes import select_route, select_verifier_route
from temnia_pipeline.harness.runtime_types import (
    CompiledRevision,
    CompileProposalResult,
    RunRef,
    StartRunRequest,
    VerificationPlan,
    WorkflowIdentity,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_editorial import _case, _edge_verdict, _tail_repair
from test_harness_hierarchy_workflow import _request, _settings, _snapshot


def _ref(kind: HarnessArtifactKind, value: object) -> HarnessArtifactRef:
    body = json.dumps(value, sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    return HarnessArtifactRef(
        id=uuid4(),
        kind=kind,
        fingerprint=digest,
        sha256=digest,
        sizeBytes=len(body),
        storageKey=f"fixture/{digest}.json",
    )


@pytest.mark.parametrize("outcome", ["repair", "conflict", "unchanged", "unknown", "exhausted"])
@pytest.mark.parametrize(
    ("prepared_generation", "repair_generation"), [(None, None), (3, 3), (3, 4)]
)
@pytest.mark.parametrize("preserve_existing_boundaries", [False, True])
async def test_editorial_program_reaches_review_without_operator_cut_instructions(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    prepared_generation: int | None,
    repair_generation: int | None,
    *,
    preserve_existing_boundaries: bool,
) -> None:
    evidence, proposal, edit = _case()
    evidence_ref = _ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json"))
    # Compile to the exact artifact identity used by this run.
    edit = compile_chapters(
        evidence,
        proposal,
        evidence_artifact_id=evidence_ref.id,
        evidence_sha256=evidence_ref.sha256,
    )
    initial = CompiledRevision(
        proposal_artifact=_ref(HarnessArtifactKind.proposal, proposal.model_dump(mode="json")),
        edit_artifact=_ref(HarnessArtifactKind.edit, edit.model_dump(mode="json")),
        edit=edit,
        revision=1,
    )
    _, routes = _settings()
    request = _request().model_copy(
        update={"brief": "Create logical chapters with complete thoughts."}
    )
    request = request.model_copy(
        update={
            "config": request.config.model_copy(
                update={"maxRepairs": 0 if outcome == "exhausted" else 1}
            )
        }
    )
    identity = WorkflowIdentity(workflow_id="editorial-test", workflow_run_id="execution-1")
    run = _snapshot(StartRunRequest(request=request, workflow=identity), routes).model_copy(
        update={"editorial_policy": "chapter-editorial/1", "config": request.config}
    )
    ref = RunRef(
        scope_organization_id=request.scope.organizationId,
        scope_user_id=request.scope.userId,
        source_id=request.sourceId,
        run_id=request.runId,
    )
    generator = select_route(routes, "propose")
    critic = select_verifier_route(
        routes, "verify", generation_families=frozenset({generator.family})
    )
    assert critic.family != generator.family
    judgment = _edge_verdict(
        evidence, edit, code="brief_conflict" if outcome == "conflict" else None
    )
    calls: list[tuple[str, Any]] = []
    events: list[str] = []
    current_proposal = proposal

    class RecordedCritic:
        async def run(self, _prompt: str, **kwargs: Any) -> SimpleNamespace:
            calls.append(("critic", kwargs["deps"]))
            if outcome == "unknown":
                raise TimeoutError("retained uncertain provider outcome")
            return SimpleNamespace(
                output=(
                    judgment
                    if len(calls) == 1
                    else EditorialVerdictV2(status="passed", findings=())
                )
            )

    class RecordedRepair:
        async def run(self, _prompt: str, **kwargs: Any) -> SimpleNamespace:
            calls.append(("repair", kwargs["deps"]))
            replacement = proposal if outcome == "unchanged" else _tail_repair(evidence, proposal)
            value = replacement.model_dump(mode="json")
            for section in value["sections"]:
                section.pop("id")
            return SimpleNamespace(output=EditorialRepairV1.model_validate_json(json.dumps(value)))

    async def activity(name: str, argument: Any, **_kwargs: Any) -> object:
        nonlocal current_proposal
        events.append(name)
        if name == "prepare_chapter_editorial":
            generation = (
                repair_generation if argument.assessment is not None else prepared_generation
            )
            return VerificationPlan(
                prompt="Recorded generic editorial context",
                route=(generator if argument.assessment is not None else critic),
                editorial_prompt_version=(
                    f"chapter-editorial-{'repair' if argument.assessment is not None else 'assess'}"
                    f"-v{generation}"
                    if prepared_generation is not None
                    else None
                ),
            )
        if name == "finalize_chapter_editorial":
            ground_editorial_verdict(
                evidence=evidence, edit=argument.context.compiled.edit, verdict=argument.verdict
            )
            return EditorialAssessment(
                artifact=_ref(HarnessArtifactKind.checks, argument.verdict.model_dump(mode="json")),
                verdict=argument.verdict,
                candidate_sha256=editorial_candidate_fingerprint(current_proposal),
            )
        if name == "claim_chapter_repair":
            assert argument.expected_repair_count == 0
            return run.model_copy(update={"repair_count": 1})
        assert name == "compile_chapter_editorial_repair"
        assert argument.preserve_existing_boundaries == preserve_existing_boundaries
        assert ("preserve_existing_boundaries" in argument.model_dump(mode="json")) == (
            preserve_existing_boundaries
        )
        replacement = canonical_chapter_proposal(
            CompactChapterProposal.model_validate_json(
                argument.repair.model_dump_json(exclude={"boundaryChoices"})
            )
        )
        try:
            validated = validate_editorial_repair(
                evidence=evidence,
                original_proposal=proposal,
                original_edit=edit,
                verdict=judgment,
                replacement_proposal=replacement,
                seen_candidate_hashes=argument.seen_candidate_hashes,
                allow_source_edge_drops=True,
            )
        except HarnessValidationError as error:
            return EditorialRepairResult(result=CompileProposalResult(refusal=str(error)))
        current_proposal = validated.proposal
        corrected = compile_chapters(
            evidence,
            current_proposal,
            evidence_artifact_id=evidence_ref.id,
            evidence_sha256=evidence_ref.sha256,
        )
        return EditorialRepairResult(
            result=CompileProposalResult(
                compiled=CompiledRevision(
                    proposal_artifact=_ref(
                        HarnessArtifactKind.proposal, current_proposal.model_dump(mode="json")
                    ),
                    edit_artifact=_ref(HarnessArtifactKind.edit, corrected.model_dump(mode="json")),
                    edit=corrected,
                    revision=1,
                )
            ),
            candidate_sha256=validated.candidateSha256,
        )

    monkeypatch.setattr(program, "chapter_editorial_assess_v1", RecordedCritic())
    monkeypatch.setattr(program, "chapter_editorial_repair_v1", RecordedRepair())
    monkeypatch.setattr(program.workflow, "execute_activity", activity)

    def patched(patch_id: str) -> bool:
        assert patch_id == "chapter-editorial-retained-boundaries-v1"
        return preserve_existing_boundaries

    monkeypatch.setattr(program.workflow, "patched", patched)
    monkeypatch.setattr(
        program.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=identity.workflow_id,
            run_id=identity.workflow_run_id,
        ),
    )
    if outcome == "unknown":
        with pytest.raises(TimeoutError, match="uncertain"):
            await program.assess_and_repair(
                request,
                run=run,
                ref=ref,
                evidence=evidence_ref,
                compiled=initial,
                generation_families={generator.family},
                control_queue="control",
            )
        assert len(calls) == 1
        assert "claim_chapter_repair" not in events
        return
    final_run, final, message = await program.assess_and_repair(
        request,
        run=run,
        ref=ref,
        evidence=evidence_ref,
        compiled=initial,
        generation_families={generator.family},
        control_queue="control",
    )
    if outcome == "repair":
        assert message is None
        assert [name for name, _ in calls] == ["critic", "repair", "critic"]
        assert final.edit.sections[-1].kind.value == "drop"
        assert final.edit.sections[0] == initial.edit.sections[0]
        assert final_run.repair_count == 1
        assert calls[1][1].operation_inputs["assessmentSha256"]
        assert calls[2][1].operation_inputs["editSha256"] == final.edit_artifact.sha256
        assert calls[0][1].route.family != calls[1][1].route.family
    else:
        assert message
        assert final == initial
        assert len(calls) == (2 if outcome == "unchanged" else 1)
    assert "render_chapter_revision" not in events
    for name, deps in calls:
        assert deps.prompt_version == (
            f"chapter-editorial-{'repair' if name == 'repair' else 'assess'}"
            f"-v{(repair_generation if name == 'repair' else prepared_generation) or 2}"
        )
    assert "accept_initial_chapter_revision" not in events
