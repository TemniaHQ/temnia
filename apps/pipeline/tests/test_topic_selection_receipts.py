"""Paid responses bind to v2 program, rubric, exact prompt, native schema and route."""

# Only the persistence transport is doubled; operation identity/admission remain real.
# pyright: reportPrivateUsage=false
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic_ai import ModelResponse, TextPart

from harness_fixtures import EVIDENCE_REF
from temnia_pipeline import db
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.topic_selection_runtime import (
    SelectionContext,
    SelectionSaveRequest,
    selection_call_inputs,
)
from temnia_pipeline.harness.topic_selection_workflow import TopicSelectionWorkflow
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_output_profiles import profiles
from test_topic_selection_workflow import Program, cold, draft

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from temnia_pipeline.harness.activities import HarnessActivities


@pytest.mark.parametrize("output_ceiling", [4096, 8192])
@pytest.mark.parametrize(
    "corruption", ["none", "prompt", "rubric", "schema", "route", "output", "setting"]
)
async def test_response_requires_complete_original_call_identity(  # noqa: C901 — independent receipt corruptions
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
    output_ceiling: int,
) -> None:
    program = Program(monkeypatch, initial=draft(selected=True), sources=[], patches=[])
    routes = profiles(author_max=output_ceiling, reviewer_max=output_ceiling)
    config = program.run.config.model_copy(update={"routeSnapshotId": routes.snapshot_id})
    program.request = program.request.model_copy(update={"config": config})
    program.run = program.run.model_copy(update={"route_snapshot": routes, "config": config})
    context = SelectionContext(
        run=TopicSelectionWorkflow.ref(program.request), evidence=EVIDENCE_REF
    )
    rubric = await program.activities.rubric(context)
    context = context.model_copy(update={"rubric": rubric})
    accepted = await program.activities.save(
        SelectionSaveRequest(context=context, draft=draft(selected=True))
    )
    context = context.model_copy(
        update={"selection": accepted.selection, "candidate_id": "discussion"}
    )
    plan = await program.activities.prepare(context)
    output = cold()
    response = ModelResponse(parts=[TextPart(output.model_dump_json())])
    body = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json")
    request_hash = "1" * 64
    _, input_hash, config_hash = ledger.operation_identity(
        run_id=program.run.id,
        kind="model",
        inputs={**selection_call_inputs(plan), "requestHash": request_hash},
        config={
            "maxOutputTokens": output_ceiling - 1 if corruption == "setting" else output_ceiling,
            "reservedVerifierFamily": plan.verifier.family,
            "programVersion": "standalone-topics/3",
            "promptVersion": plan.prompt_version,
            "schemaVersion": plan.schema_version,
            "route": plan.verifier.model_dump(mode="json"),
        },
    )
    reference = HarnessArtifactRef(
        id=uuid5(NAMESPACE_URL, "selection-receipt"),
        kind=HarnessArtifactKind.model_response,
        sha256="2" * 64,
        fingerprint="3" * 64,
        sizeBytes=1,
        storageKey="tests/response.json",
    )
    metadata: dict[str, Any] = {
        "programVersion": "standalone-topics/3",
        "promptVersion": plan.prompt_version,
        "schemaVersion": plan.schema_version,
        "route": plan.verifier.model_dump(mode="json"),
        "runId": str(program.run.id),
        "requestHash": request_hash,
    }
    retained = SimpleNamespace(
        kind="model_response",
        metadata=metadata,
        dependency_ids=[ref.id for ref in plan.input_artifacts],
    )
    program.activities.owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="mock")),
            _artifact_ref=lambda _record: reference,  # pyright: ignore[reportUnknownLambdaType]
        ),
    )

    async def read(_context: SelectionContext, ref: HarnessArtifactRef) -> object:
        assert ref == reference
        return body

    async def record(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return retained

    async def fetchall() -> list[dict[str, object]]:
        return [
            {
                "id": reference.id,
                "family": plan.verifier.family,
                "input_hash": input_hash,
                "config_hash": config_hash,
            }
        ]

    async def execute(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[SimpleNamespace]:
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(program.activities, "read", read)
    monkeypatch.setattr(artifacts, "_artifact_for_read", record)
    monkeypatch.setattr(db, "scoped", scoped)
    if corruption == "prompt":
        plan = plan.model_copy(update={"prompt": plan.prompt + "Different audience assumptions."})
    elif corruption == "rubric":
        replacement = rubric.model_copy(update={"id": uuid5(NAMESPACE_URL, "different-rubric")})
        plan = plan.model_copy(update={"input_artifacts": (context.evidence, replacement)})
    elif corruption == "schema":
        metadata["schemaVersion"] = "standalone-topic-cold-review/1"
    elif corruption == "route":
        metadata["route"] = {**metadata["route"], "provider": "another-provider"}
    elif corruption == "output":
        output = output.model_copy(deep=True)
        output.value.reconstructedTakeaway = "A claim the paid response did not make."
    if corruption == "none":
        assert (
            await TopicSelectionActivities.response_ref(program.activities, context, plan, output)
            == reference
        )
    else:
        with pytest.raises(HarnessValidationError):
            await TopicSelectionActivities.response_ref(program.activities, context, plan, output)
