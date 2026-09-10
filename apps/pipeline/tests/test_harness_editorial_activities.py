"""Editorial activities refuse outputs disconnected from their paid receipts."""

# Recorded storage boundaries are substituted; the activity validation runs unchanged.
# ruff: noqa: C901, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic_ai import ModelResponse, TextPart

from temnia_pipeline import db
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.editorial import (
    EDITORIAL_REPAIR_SCHEMA_VERSION,
    EDITORIAL_VERDICT_SCHEMA_VERSION,
    EditorialRepairV1,
    EditorialVerdictV2,
)
from temnia_pipeline.harness.editorial_activities import EditorialActivities
from temnia_pipeline.harness.editorial_runtime import EditorialContextRequest
from temnia_pipeline.harness.routes import select_route, select_verifier_route
from temnia_pipeline.harness.runtime_types import (
    CompiledRevision,
    RunRef,
    StartRunRequest,
    WorkflowIdentity,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_editorial import _case, _edge_verdict, _tail_repair
from test_harness_hierarchy_workflow import _request, _settings, _snapshot

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from temnia_pipeline.harness.activities import HarnessActivities


def _ref(kind: HarnessArtifactKind, value: object) -> HarnessArtifactRef:
    body = artifacts.canonical_json(value)
    digest = hashlib.sha256(body).hexdigest()
    return HarnessArtifactRef(
        id=uuid4(),
        kind=kind,
        fingerprint=digest,
        sha256=digest,
        sizeBytes=len(body),
        storageKey=f"fixture/{digest}.json",
    )


@pytest.mark.parametrize(
    ("repair", "generation"),
    [(False, 1), (False, 2), (False, 3), (True, 1), (True, 2), (True, 3), (True, 4)],
)
@pytest.mark.parametrize(
    "corruption", ["none", "output", "family", "same_family", "dependency", "prompt"]
)
async def test_editorial_response_is_the_exact_independent_accepted_output(
    monkeypatch: pytest.MonkeyPatch, *, repair: bool, corruption: str, generation: int
) -> None:
    evidence, proposal, edit = _case()
    request = _request()
    _, routes = _settings()
    run = _snapshot(
        StartRunRequest(
            request=request,
            workflow=WorkflowIdentity(workflow_id="editorial", workflow_run_id="one"),
        ),
        routes,
    )
    generator = select_route(routes, "propose")
    critic = select_verifier_route(
        routes, "verify", generation_families=frozenset({generator.family})
    )
    route = generator if repair else critic
    verdict = _edge_verdict(evidence, edit)
    candidate = _tail_repair(evidence, proposal)
    output: EditorialRepairV1 | EditorialVerdictV2 = (
        EditorialRepairV1.model_validate_json(
            candidate.model_dump_json(exclude={"sections": {"__all__": {"id"}}})
        )
        if repair
        else verdict
    )
    assessment_body = {
        "verifierFamily": generator.family if corruption == "same_family" else critic.family
    }
    assessment = _ref(HarnessArtifactKind.checks, assessment_body) if repair else None
    context = EditorialContextRequest(
        run=RunRef(
            scope_organization_id=request.scope.organizationId,
            scope_user_id=request.scope.userId,
            source_id=request.sourceId,
            run_id=request.runId,
        ),
        evidence=_ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json")),
        compiled=CompiledRevision(
            proposal_artifact=_ref(HarnessArtifactKind.proposal, proposal.model_dump(mode="json")),
            edit_artifact=_ref(HarnessArtifactKind.edit, edit.model_dump(mode="json")),
            edit=edit,
            revision=1,
        ),
        generation_families=(generator.family,),
        iteration=0,
        assessment=assessment,
    )
    body = MODEL_RESPONSE_ADAPTER.dump_python(
        ModelResponse(parts=[TextPart(output.model_dump_json())]), mode="json", by_alias=True
    )
    if corruption == "output":
        body = MODEL_RESPONSE_ADAPTER.dump_python(
            ModelResponse(parts=[TextPart('{"version":2,"status":"passed","findings":[]}')]),
            mode="json",
            by_alias=True,
        )
    response_ref = _ref(HarnessArtifactKind.model_response, body)
    operation_id, attempt_id = uuid4(), uuid4()
    row: dict[str, Any] = {
        "id": response_ref.id,
        "sha256": response_ref.sha256,
        "fingerprint": response_ref.fingerprint,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "provider": route.provider,
        "model": route.gateway_model,
        "family": route.family,
        "route": route.model_dump(mode="json"),
    }
    if corruption == "family":
        row["family"] = "unrelated-family"
    dependencies = [
        context.evidence.id,
        context.compiled.edit_artifact.id,
        context.compiled.proposal_artifact.id,
    ]
    if assessment is not None:
        dependencies.append(assessment.id)
    if corruption == "dependency":
        dependencies.pop()
    retained = artifacts.HarnessArtifact(
        id=response_ref.id,
        organization_id=request.scope.organizationId,
        source_id=request.sourceId,
        kind="model_response",
        fingerprint=response_ref.fingerprint,
        storage_key=response_ref.storageKey,
        sha256=response_ref.sha256,
        size_bytes=response_ref.sizeBytes,
        transcript_id=None,
        transcript_revision=None,
        dependency_ids=tuple(dependencies),
        metadata={
            "routeId": route.id,
            "route": route.model_dump(mode="json"),
            "attemptId": str(attempt_id),
            "operationId": str(operation_id),
            "runId": str(request.runId),
            "programVersion": "chapter-editorial-workflow/1",
            "promptVersion": "wrong"
            if corruption == "prompt"
            else f"chapter-editorial-{'repair' if repair else 'assess'}-v{generation}",
            "schemaVersion": EDITORIAL_REPAIR_SCHEMA_VERSION
            if repair
            else EDITORIAL_VERDICT_SCHEMA_VERSION,
        },
    )

    async def fetchall() -> list[dict[str, Any]]:
        return [row]

    async def execute(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[SimpleNamespace]:
        yield SimpleNamespace(execute=execute)

    async def find(*_args: object, **_kwargs: object) -> artifacts.HarnessArtifact:
        return retained

    async def read(_context: EditorialContextRequest, ref: HarnessArtifactRef) -> object:
        return assessment_body if ref == assessment else body

    async def families(_context: EditorialContextRequest) -> frozenset[str]:
        return frozenset({critic.family if corruption == "same_family" else generator.family})

    def artifact_ref(_artifact: artifacts.HarnessArtifact) -> HarnessArtifactRef:
        return response_ref

    owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="fixture")),
            _artifact_ref=artifact_ref,
        ),
    )
    activity = EditorialActivities(owner)
    monkeypatch.setattr(db, "scoped", scoped)
    monkeypatch.setattr(artifacts, "find_artifact", find)
    monkeypatch.setattr(activity, "_read", read)
    monkeypatch.setattr(activity, "_generation_families", families)
    if corruption != "none":
        with pytest.raises(HarnessValidationError):
            await activity._model_response(
                context, "editorial:test", output=output, family=route.family, run=run
            )
    else:
        assert await activity._model_response(
            context, "editorial:test", output=output, family=route.family, run=run
        ) == (response_ref.id, response_ref.sha256, generation)
