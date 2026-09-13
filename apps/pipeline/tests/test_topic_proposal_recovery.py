"""Known source-invalid proposals recover before critics, with retained paid lineage.

Storage and Temporal dispatch are replaced at transport boundaries. The source
validator, diagnostic loader, paid-response reader and workflow are real code.
"""

# Boundary fixtures intentionally exercise private ownership helpers.
# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai import ModelResponse, TextPart

from harness_fixtures import EVIDENCE_REF
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef, TopicProposal
from temnia_pipeline.harness import artifacts, topic_activities, topic_workflow
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.ledger import OutcomeUnknown
from temnia_pipeline.harness.topic_editorial import TOPIC_PROGRAM, TOPIC_PROMPT, editorial_routes
from temnia_pipeline.harness.topic_runtime import (
    SaveTopicProposal,
    TopicContext,
    TopicProposalResult,
    TopicProposalValidation,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_workflow import EVIDENCE, _Program, _proposal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence


def _invalid(identifier: str = "foreign-sentence") -> TopicProposal:
    proposal = _proposal()
    proposal.candidates[0].firstSentenceId = identifier
    return proposal


class _Recovery(_Program):
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(monkeypatch, mode="unknown")
        self.records: dict[UUID, SimpleNamespace] = {}
        self.bodies: dict[UUID, object] = {}
        self.responses: dict[str, dict[str, object]] = {}
        self.diagnostics: list[tuple[HarnessArtifactRef, TopicProposalValidation]] = []
        self.activities.owner.ctx = SimpleNamespace(  # type: ignore[attr-defined]
            settings=SimpleNamespace(database_url="unused"), store=None
        )
        self.activities.owner._artifact_ref = lambda record: record.reference  # type: ignore[method-assign]
        monkeypatch.setattr(self.activities, "publish", self.publish)
        monkeypatch.setattr(topic_activities.artifacts, "_artifact_for_read", self.record)
        monkeypatch.setattr(topic_activities.artifacts, "read_artifact_json", self.read_body)
        monkeypatch.setattr(topic_activities.db, "scoped", self.scoped)

    def retain(
        self,
        body: object,
        *,
        kind: str,
        metadata: dict[str, object],
        dependencies: Sequence[HarnessArtifactRef],
    ) -> HarnessArtifactRef:
        raw = artifacts.canonical_json(body)
        digest = hashlib.sha256(raw).hexdigest()
        fingerprint = hashlib.sha256(
            artifacts.canonical_json(
                {"sha": digest, "metadata": metadata, "deps": [str(r.id) for r in dependencies]}
            )
        ).hexdigest()
        reference = HarnessArtifactRef(
            id=uuid5(NAMESPACE_URL, fingerprint),
            kind=HarnessArtifactKind(kind),
            sha256=digest,
            fingerprint=fingerprint,
            sizeBytes=len(raw),
            storageKey=f"recovery/{fingerprint}.json",
        )
        self.records[reference.id] = SimpleNamespace(
            kind=kind,
            metadata=metadata,
            dependency_ids=tuple(ref.id for ref in dependencies),
            reference=reference,
        )
        self.bodies[reference.id] = body
        return reference

    async def record(self, *_args: object, **kwargs: object) -> SimpleNamespace:
        return self.records[cast("UUID", kwargs["artifact_id"])]

    async def read_body(self, *_args: object, **kwargs: object) -> object:
        return self.bodies[cast("UUID", kwargs["artifact_id"])]

    @asynccontextmanager
    async def scoped(self, _database: str, _scope: object) -> AsyncGenerator[Any]:
        shell = self

        class Connection:
            async def execute(self, _sql: str, params: tuple[object, ...]) -> Connection:
                self.stage = str(params[2])
                return self

            async def fetchall(self) -> list[dict[str, object]]:
                return [shell.responses[self.stage]] if self.stage in shell.responses else []

        yield Connection()

    async def publish(self, context: TopicContext, **kwargs: object) -> HarnessArtifactRef:
        content = cast("BaseModel", kwargs["content"])
        reference = self.retain(
            content.model_dump(mode="json"),
            kind=str(kwargs["kind"]),
            metadata={
                "runId": str(context.run.run_id),
                "format": kwargs["format_name"],
                **cast("dict[str, object]", kwargs.get("metadata") or {}),
            },
            dependencies=cast("Sequence[HarnessArtifactRef]", kwargs["dependencies"]),
        )
        if isinstance(content, TopicProposal):
            self.proposals[reference.id] = content
            self.saved_proposals.append(reference)
        elif isinstance(content, TopicProposalValidation):
            self.diagnostics.append((reference, content))
        return reference

    def settle(self, request: SaveTopicProposal) -> HarnessArtifactRef:
        body = MODEL_RESPONSE_ADAPTER.dump_python(
            ModelResponse(parts=[TextPart(request.proposal.model_dump_json())]), mode="json"
        )
        refs = tuple(
            reference
            for reference in (
                request.context.evidence,
                request.context.proposal,
                request.context.assessment,
                request.context.navigation,
                request.context.proposal_validation,
            )
            if reference is not None
        )
        response = self.retain(
            body,
            kind="model_response",
            metadata={
                "runId": str(self.request.runId),
                "programVersion": TOPIC_PROGRAM,
                "promptVersion": TOPIC_PROMPT,
                "schemaVersion": TOPIC_PROMPT,
            },
            dependencies=refs,
        )
        self.responses[request.model_stage] = {"id": response.id, "family": request.author_family}
        return response

    async def execute(self, name: str, request: object, **kwargs: object) -> object:
        if name == "save_topic_proposal":
            self.events.append((name, request))
            message = cast("SaveTopicProposal", request)
            self.settle(message)
            return await self.activities.save_proposal(message)
        return await super().execute(name, request, **kwargs)

    def message(self, proposal: TopicProposal) -> SaveTopicProposal:
        author, verifier = editorial_routes(self.routes)
        return SaveTopicProposal(
            context=TopicContext(
                run=topic_workflow.TopicRunWorkflow.ref(self.request), evidence=EVIDENCE_REF
            ),
            proposal=proposal,
            model_stage="proposal:topic:0",
            author_family=author.family,
            verifier_family=verifier.family,
        )


async def test_foreign_initial_ids_are_retained_then_corrected_before_any_critic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Recovery(monkeypatch)
    shell.author.outputs[:] = [_invalid(), _proposal()]
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert output.revision == 1
    assert shell.run.repair_count == 1
    assert len(shell.diagnostics) == len(shell.saved_proposals) == 1
    reference, diagnostic = shell.diagnostics[0]
    assert diagnostic.proposal == _invalid()
    assert diagnostic.error_message == "topic first: unknown sentence foreign-sentence"
    assert diagnostic.model_response.id in shell.records[reference.id].dependency_ids
    assert reference.id in shell.records[shell.saved_proposals[0].id].dependency_ids
    author_events = [i for i, (name, _) in enumerate(shell.events) if name == "save_topic_proposal"]
    first_assessment = next(
        i for i, (name, _) in enumerate(shell.events) if name == "save_topic_assessment"
    )
    assert max(author_events) < first_assessment
    prompt, deps = shell.author.calls[1]
    assert diagnostic.error_message in prompt
    assert "never accepted or sent to critics" in prompt
    assert reference.id in deps.input_artifact_ids
    assert deps.prompt_version == TOPIC_PROMPT
    assert deps.program_version == TOPIC_PROGRAM
    assert len(shell.cold.calls) == 2
    assert len(shell.source.calls) == 1
    assert all("foreign-sentence" not in prompt for prompt, _ in shell.cold.calls)
    assert all(
        deps.route.family == shell.author.calls[0][1].route.family for _, deps in shell.author.calls
    )


@pytest.mark.parametrize("repairs", [0, 2])
async def test_configured_allowance_bounds_initial_corrections_without_critics(
    monkeypatch: pytest.MonkeyPatch, repairs: int
) -> None:
    shell = _Recovery(monkeypatch)
    shell.request = shell.request.model_copy(
        update={"config": shell.request.config.model_copy(update={"maxRepairs": repairs})}
    )
    shell.author.outputs[:] = [_invalid(f"foreign-{index}") for index in range(repairs + 1)]
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert len(shell.author.calls) == repairs + 1
    assert shell.run.repair_count == repairs
    assert len(shell.diagnostics) == repairs + 1
    assert not shell.saved_proposals
    assert not shell.cold.calls
    assert not shell.source.calls
    assert output.revision is None
    assert output.editArtifact is None
    assert output.errorMessage
    assert "Repair allowance exhausted" in output.errorMessage
    assert not any(name == "compile_topic_portfolio" for name, _ in shell.events)


async def test_identical_invalid_selection_stops_without_spending_remaining_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Recovery(monkeypatch)
    cosmetic = _invalid()
    cosmetic.summary = "Different explanation, same foreign selection."
    shell.author.outputs[:] = [_invalid(), cosmetic]
    output = await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert len(shell.author.calls) == 2
    assert shell.run.repair_count == 1
    assert not shell.cold.calls
    assert not shell.source.calls
    assert output.errorMessage
    assert "Repeated invalid source selection" in output.errorMessage


async def test_unknown_paid_outcome_cannot_be_converted_into_source_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Recovery(monkeypatch)

    async def unknown(_prompt: str, **_kwargs: object) -> None:
        message = "provider outcome unresolved"
        raise OutcomeUnknown(message)

    monkeypatch.setattr(shell.author, "run", unknown)
    with pytest.raises(OutcomeUnknown):
        await topic_workflow.TopicRunWorkflow().run(shell.request)
    assert not shell.diagnostics
    assert not shell.saved_proposals
    assert not shell.cold.calls
    assert not shell.source.calls
    assert not any(name == "claim_chapter_repair" for name, _ in shell.events)


@pytest.mark.parametrize(
    "corruption",
    ["run", "evidence", "error", "valid", "response", "dependency", "prompt", "stage", "bytes"],
)
async def test_altered_diagnostic_or_model_identity_cannot_authorize_another_call(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    shell = _Recovery(monkeypatch)
    message = shell.message(_invalid())
    response = shell.settle(message)
    result = await shell.activities.save_proposal(message)
    assert result.validation is not None
    reference, diagnostic = shell.diagnostics[-1]
    body = diagnostic.model_dump(mode="json")
    if corruption == "run":
        body["run_id"] = str(uuid4())
    elif corruption == "evidence":
        body["evidence_sha256"] = "f" * 64
    elif corruption == "error":
        body["error_message"] = "Operator requested a different video."
    elif corruption == "valid":
        body["proposal"] = _proposal().model_dump(mode="json")
    elif corruption == "response":
        shell.responses[message.model_stage]["family"] = message.verifier_family
    elif corruption == "dependency":
        shell.records[reference.id].dependency_ids = (response.id,)
    elif corruption == "prompt":
        shell.records[response.id].metadata["promptVersion"] = "standalone-topic-editor/999"
    elif corruption == "stage":
        body["model_stage"] = "verify:topic:source:0"
    elif corruption == "bytes":
        shell.bodies[reference.id] = {**body, "error_message": "Changed after publication."}
    if corruption in {"run", "evidence", "error", "valid", "stage"}:
        record = shell.records[reference.id]
        reference = shell.retain(
            body,
            kind="checks",
            metadata=record.metadata,
            dependencies=[EVIDENCE_REF, response],
        )
    context = message.context.model_copy(update={"proposal_validation": reference})
    with pytest.raises(HarnessValidationError):
        await shell.activities.prepare(context)
    assert not shell.author.calls
    assert not shell.cold.calls
    assert not shell.source.calls


async def test_unsettled_response_never_produces_a_repair_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _Recovery(monkeypatch)
    with pytest.raises(HarnessValidationError, match="uniquely accepted"):
        await shell.activities.save_proposal(shell.message(_invalid()))
    assert not shell.diagnostics


def test_private_result_and_context_cannot_claim_acceptance_and_refusal_together() -> None:
    with pytest.raises(ValidationError):
        TopicProposalResult(artifact=EVIDENCE_REF, validation=EVIDENCE_REF, validation_error="bad")
    with pytest.raises(ValidationError):
        TopicProposalResult(validation=EVIDENCE_REF)
    with pytest.raises(ValidationError):
        TopicContext.model_validate(
            {
                "run": {
                    "scope_organization_id": uuid4(),
                    "scope_user_id": uuid4(),
                    "source_id": EVIDENCE.sourceId,
                    "run_id": uuid4(),
                },
                "evidence": EVIDENCE_REF,
                "proposal": EVIDENCE_REF,
                "proposal_validation": EVIDENCE_REF,
            }
        )
