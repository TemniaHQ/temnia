"""Empty initial labels are mechanical; paid bytes and semantic spans stay authoritative."""

# These tests preserve real native parsing and receipt validation at mocked IO seams.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, ModelResponse, NativeOutput, TextPart, ThinkingPart
from pydantic_ai.usage import RequestUsage

from harness_fixtures import EVIDENCE_REF
from temnia_pipeline.contracts import HarnessArtifactRef, TopicProposal
from temnia_pipeline.harness import artifacts, models
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER, CassetteStore
from temnia_pipeline.harness.models import normalize_initial_topic_response
from temnia_pipeline.harness.topic_compiler import validate_topic_proposal
from temnia_pipeline.harness.topic_editorial import TOPIC_PROGRAM, TOPIC_PROMPT
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_model_transport import _summary_deps
from test_topic_proposal_recovery import _Recovery
from test_topic_workflow import EVIDENCE, _proposal

if TYPE_CHECKING:
    from pathlib import Path


def _body() -> dict[str, Any]:
    body = _proposal().model_dump(mode="json")
    for candidate in body["candidates"]:
        candidate["id"] = ""
    return body


def _response(body: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        parts=[ThinkingPart("Unchanged reasoning."), TextPart(json.dumps(body), id="answer")],
        usage=RequestUsage(input_tokens=25_189, output_tokens=4836),
        provider_response_id="committed-generation",
        provider_name="recorded-provider",
        metadata={"cost": "retained"},
    )


def _normalized(response: ModelResponse) -> ModelResponse:
    return normalize_initial_topic_response(
        response, schema_version=TOPIC_PROMPT, stage="proposal:topic:0"
    )


def _parse(response: ModelResponse) -> TopicProposal:
    text = next(part.content for part in response.parts if isinstance(part, TextPart))
    return TopicProposal.model_validate_json(text, strict=True)


def test_nine_empty_ids_preserve_provider_bytes_and_editorial_fields() -> None:
    body = _body()
    template = body["candidates"][0]
    # The retained failure had these nine extents, with an empty ID on every candidate.
    extents = [
        (374, 446),
        (183, 201),
        (488, 506),
        (526, 545),
        (202, 219),
        (335, 348),
        (359, 372),
        (364, 372),
        (465, 490),
    ]
    body["candidates"] = [
        {
            **copy.deepcopy(template),
            "firstSentenceId": f"s{first:06d}",
            "lastSentenceId": f"s{last:06d}",
        }
        for first, last in extents
    ]
    response = _response(body)
    raw = MODEL_RESPONSE_ADAPTER.dump_json(response)
    with pytest.raises(ValidationError):
        _parse(response)
    normalized = _normalized(response)
    proposal = _parse(normalized)
    assert len({candidate.id for candidate in proposal.candidates}) == 9
    assert _normalized(normalized) is normalized
    assert _parse(_normalized(response)) == proposal
    assert normalized.usage is response.usage
    assert normalized.metadata is response.metadata
    assert normalized.provider_response_id == response.provider_response_id
    assert normalized.parts[0] is response.parts[0]
    assert MODEL_RESPONSE_ADAPTER.dump_json(response) == raw
    for original, candidate in zip(body["candidates"], proposal.candidates, strict=True):
        assert candidate.model_dump(exclude={"id"}) == {
            key: value for key, value in original.items() if key != "id"
        }


def test_existing_ids_are_preserved_and_generated_ids_avoid_collisions() -> None:
    body = _body()
    first_generated = _parse(_normalized(_response(body))).candidates[0].id
    body["candidates"][1]["id"] = first_generated
    proposal = _parse(_normalized(_response(body)))
    assert proposal.candidates[1].id == first_generated
    assert proposal.candidates[0].id != first_generated
    valid = _response(proposal.model_dump(mode="json"))
    assert _normalized(valid) is valid


@pytest.mark.parametrize(
    ("schema", "stage"),
    [
        (TOPIC_PROMPT, "proposal:topic:1"),
        (TOPIC_PROMPT, "verify:topic:source:0"),
        ("chapter-proposal/1", "proposal:topic:0"),
    ],
)
def test_other_stages_and_schemas_remain_strict(schema: str, stage: str) -> None:
    response = _response(_body())
    assert (
        normalize_initial_topic_response(response, schema_version=schema, stage=stage) is response
    )


@pytest.mark.parametrize("label", [None, 42, " ", "x" * 257])
def test_unobserved_nonempty_or_wrong_type_labels_are_not_rewritten(label: object) -> None:
    body = _body()
    body["candidates"] = [{**body["candidates"][0], "id": label}]
    response = _response(body)
    assert _normalized(response) is response


def test_completion_outside_extent_is_preserved_for_source_validation() -> None:
    body = _body()
    body["candidates"][0]["completionSpans"] = [
        {"firstSentenceId": "s000003", "lastSentenceId": "s000003"}
    ]
    proposal = _parse(_normalized(_response(body)))
    assert proposal.candidates[0].lastSentenceId == "s000001"
    assert proposal.candidates[0].completionSpans[0].lastSentenceId == "s000003"
    with pytest.raises(HarnessValidationError, match="required evidence lies outside"):
        validate_topic_proposal(EVIDENCE, proposal)


async def test_committed_response_reenters_native_parser_without_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = _response(_body())
    raw = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json")
    original = artifacts.canonical_json(raw)
    acquisitions: list[dict[str, object]] = []

    async def acquire(*_args: object, **kwargs: object) -> SimpleNamespace:
        acquisitions.append(kwargs)
        return SimpleNamespace(accepted=True, operation=SimpleNamespace(result_artifact_id=uuid4()))

    async def read(*_args: object, **_kwargs: object) -> object:
        return raw

    async def forbid(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A committed response must not reserve, dispatch or settle again")

    monkeypatch.setattr(models.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(models.artifacts, "read_artifact_json", read)
    monkeypatch.setattr(models.ledger, "reserve_attempt", forbid)
    monkeypatch.setattr(models.ledger, "complete_attempt", forbid)
    deps = _summary_deps(schema_version=TOPIC_PROMPT).model_copy(
        update={
            "stage": "proposal:topic:0",
            "program_version": TOPIC_PROGRAM,
            "prompt_version": TOPIC_PROMPT,
        }
    )
    models.configure_model_runtime(
        models.ModelRuntime(
            database_url="unused", store=cast("Any", None), cassette_store=CassetteStore(tmp_path)
        )
    )
    try:
        budgeted = models.BudgetedModel(models.LazyConfiguredModel(deps), deps)
        agent = Agent(budgeted, output_type=NativeOutput(TopicProposal, strict=True), retries=0)
        first = await agent.run("Unchanged request")
        second = await agent.run("Unchanged request")
    finally:
        models.clear_model_runtime()
    assert first.output == second.output == _parse(_normalized(response))
    assert acquisitions[0] == acquisitions[1]
    assert artifacts.canonical_json(raw) == original


def _retained_empty_response(
    shell: _Recovery, body: dict[str, Any]
) -> tuple[HarnessArtifactRef, TopicProposal]:
    response = _response(body)
    proposal = _parse(_normalized(response))
    ref = shell.retain(
        MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json"),
        kind="model_response",
        metadata={
            "runId": str(shell.request.runId),
            "programVersion": TOPIC_PROGRAM,
            "promptVersion": TOPIC_PROMPT,
            "schemaVersion": TOPIC_PROMPT,
        },
        dependencies=[EVIDENCE_REF],
    )
    shell.responses["proposal:topic:0"] = {
        "id": ref.id,
        "family": shell.message(proposal).author_family,
    }
    return ref, proposal


@pytest.mark.parametrize("invalid_source", [False, True])
async def test_receipt_matching_and_derived_metadata_keep_raw_lineage(
    monkeypatch: pytest.MonkeyPatch, *, invalid_source: bool
) -> None:
    shell = _Recovery(monkeypatch)
    body = _body()
    if invalid_source:
        body["candidates"][0]["completionSpans"] = [
            {"firstSentenceId": "s000003", "lastSentenceId": "s000003"}
        ]
    ref, proposal = _retained_empty_response(shell, body)
    raw_before = artifacts.canonical_json(shell.bodies[ref.id])
    result = await shell.activities.save_proposal(shell.message(proposal))
    derived = result.validation if invalid_source else result.artifact
    assert derived is not None
    metadata = shell.records[derived.id].metadata
    assert metadata["candidateIdNormalization"] == {
        "version": models.TOPIC_ID_NORMALIZATION_VERSION,
        "rawResponseSha256": ref.sha256,
    }
    assert ref.id in shell.records[derived.id].dependency_ids
    assert artifacts.canonical_json(shell.bodies[ref.id]) == raw_before
    if invalid_source:
        context = shell.message(proposal).context.model_copy(
            update={"proposal_validation": derived}
        )
        assert (
            await shell.activities.proposal_validation(
                context, EVIDENCE, family=shell.message(proposal).author_family
            )
            is not None
        )
    altered = proposal.model_copy(deep=True)
    altered.candidates[0].title = "Changed editorial claim"
    with pytest.raises(HarnessValidationError, match="differs from the original paid response"):
        await shell.activities.save_proposal(shell.message(altered))
    assert hashlib.sha256(raw_before).hexdigest() == ref.sha256
