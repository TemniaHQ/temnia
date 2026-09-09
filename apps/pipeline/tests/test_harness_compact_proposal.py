"""Bounded wire and prompt behavior for compact chapter proposals."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelResponse, TextPart

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import models as models_module
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.models import (
    COMPACT_PROPOSAL_SCHEMA_VERSION,
    BudgetedModel,
    CompactChapterProposal,
    HarnessModelDeps,
    LazyConfiguredModel,
    ModelRuntime,
    canonical_chapter_proposal,
    clear_model_runtime,
    compact_synthetic_proposal,
    configure_model_runtime,
)
from temnia_pipeline.harness.prompts import (
    COMPACT_PROPOSE_PROMPT_VERSION,
    render_compact_proposal_prompt,
    render_proposal_repair_prompt,
)
from temnia_pipeline.harness.routes import (
    MAX_REQUEST_PAYLOAD_BYTES,
    ContextWindowExceeded,
    RouteSnapshot,
    select_route,
)

FIXTURE = Path(__file__).parent / "fixtures/harness/chapter.synthetic.json"


def _wire() -> dict[str, object]:
    return {
        "version": 1,
        "summary": "Keep both source ranges.",
        "sections": [
            {
                "firstSentenceId": "sentence-1",
                "lastSentenceId": "sentence-2",
                "kind": "keep",
                "title": "Opening",
                "reason": "Introduces the subject.",
                "quoteWordIds": ["word-1", "word-8"],
            },
            {
                "firstSentenceId": "sentence-3",
                "lastSentenceId": "sentence-4",
                "kind": "drop",
                "title": "Aside",
                "reason": "A deliberate omission.",
                "quoteWordIds": [],
            },
        ],
    }


def _validate_json(value: dict[str, object]) -> CompactChapterProposal:
    return CompactChapterProposal.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False), strict=True
    )


def test_compact_wire_converts_without_changing_semantic_fields() -> None:
    compact = _validate_json(_wire())
    first = canonical_chapter_proposal(compact)
    second = canonical_chapter_proposal(compact)

    assert first == second
    assert first.summary == compact.summary
    assert len({section.id for section in first.sections}) == 2
    assert all(section.id.startswith("section-") for section in first.sections)
    for compact_section, canonical_section in zip(compact.sections, first.sections, strict=True):
        assert canonical_section.model_dump(mode="json", exclude={"id"}) == (
            compact_section.model_dump(mode="json")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "x" * 1025),
        ("title", "x" * 161),
        ("reason", "x" * 321),
        ("quoteWordIds", ["word-1", "word-2", "word-3"]),
    ],
)
def test_compact_wire_refuses_overlong_fields(field: str, value: object) -> None:
    wire = _wire()
    if field == "summary":
        wire[field] = value
    else:
        sections = cast("list[object]", wire["sections"])
        assert isinstance(sections, list)
        section = cast("dict[str, object]", sections[0])
        assert isinstance(section, dict)
        section[field] = value
    with pytest.raises(ValidationError):
        _validate_json(wire)


def test_compact_wire_has_no_model_authored_section_id_or_count_quota() -> None:
    with_id = _wire()
    sections = cast("list[object]", with_id["sections"])
    assert isinstance(sections, list)
    section = cast("dict[str, object]", sections[0])
    assert isinstance(section, dict)
    section["id"] = "model-label"
    with pytest.raises(ValidationError):
        _validate_json(with_id)

    many = _wire()
    source_section = cast("dict[str, object]", sections[1])
    many["sections"] = [copy.deepcopy(source_section) for _ in range(1001)]
    assert len(_validate_json(many).sections) == 1001


def test_recorded_fixture_adaptation_is_explicit_non_mutating_and_strict() -> None:
    payload = {"synthetic": True, "output": {**_wire()}}
    output = cast("dict[str, object]", payload["output"])
    assert isinstance(output, dict)
    output_sections = cast("list[object]", output["sections"])
    assert isinstance(output_sections, list)
    assert isinstance(output_sections[0], dict)
    first_output_section = cast("dict[str, object]", output_sections[0])
    first_output_section["id"] = "old-label"
    original = copy.deepcopy(payload)

    adapted = compact_synthetic_proposal(payload)

    assert payload == original
    assert adapted is not None
    adapted_output = cast("dict[str, object]", adapted["output"])
    assert isinstance(adapted_output, dict)
    adapted_sections = cast("list[object]", adapted_output["sections"])
    assert isinstance(adapted_sections, list)
    assert isinstance(adapted_sections[0], dict)
    assert "id" not in adapted_sections[0]
    _validate_json(adapted_output)


def test_production_recorded_fixture_adapts_to_native_compact_wire() -> None:
    recorded = cast("dict[str, Any]", json.loads(FIXTURE.read_bytes()))
    outputs = cast("dict[str, dict[str, Any]]", recorded["outputs"])
    proposal = copy.deepcopy(outputs["propose"])

    adapted = compact_synthetic_proposal(proposal)

    assert adapted is not None
    compact = _validate_json(cast("dict[str, object]", adapted["output"]))
    assert len(compact.sections) == 4
    assert all(len(section.quoteWordIds) <= 2 for section in compact.sections)


def test_compact_and_repair_appendices_preserve_original_prompt_bytes() -> None:
    original = 'Original fixed instruction.\n\nEVIDENCE_JSON\n{"source":"unchanged"}'
    compact = render_compact_proposal_prompt(original)
    assert compact.startswith(original)
    assert COMPACT_PROPOSE_PROMPT_VERSION in compact
    assert "supersedes only" in compact

    repaired = render_proposal_repair_prompt(
        compact,
        feedback={
            "code": "invalid_schema",
            "compilerCode": None,
            "issues": [{"path": "sections.19.reason", "code": "missing"}],
            "message": "The response has a missing bounded field.",
        },
        diagnostic_artifact=(uuid.UUID(int=1), "a" * 64),
        response_artifact=(uuid.UUID(int=2), "b" * 64),
    )
    assert repaired.startswith(compact)
    assert "sections.19.reason" in repaired
    assert str(uuid.UUID(int=2)) in repaired
    assert len(repaired.encode()) <= MAX_REQUEST_PAYLOAD_BYTES


def test_appendices_enforce_the_request_byte_ceiling() -> None:
    with pytest.raises(ContextWindowExceeded):
        render_compact_proposal_prompt("x" * MAX_REQUEST_PAYLOAD_BYTES)


async def test_budgeted_model_publishes_compact_output_limit_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURE.parent / "routes.synthetic.json").read_bytes(), strict=True
    )
    route = select_route(routes, "propose")
    scope = Scope(organizationId=uuid.UUID(int=3), userId=uuid.UUID(int=4))
    response = ModelResponse(parts=[TextPart(content='{"version":1}')])
    published_id = uuid.UUID(int=5)
    metadata_values: list[dict[str, object]] = []

    async def publish_json(*_args: object, **kwargs: object) -> object:
        metadata_values.append(cast("dict[str, object]", kwargs["metadata"]))
        return SimpleNamespace(id=published_id)

    async def complete_attempt(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(models_module.artifacts, "publish_json", publish_json)
    monkeypatch.setattr(models_module.ledger, "complete_attempt", complete_attempt)

    def deps(schema_version: str) -> HarnessModelDeps:
        return HarnessModelDeps(
            scope=scope,
            source_id=uuid.UUID(int=6),
            run_id=uuid.UUID(int=7),
            stage="proposal:v2:global",
            program_version="chapter-workflow/1",
            prompt_version=COMPACT_PROPOSE_PROMPT_VERSION,
            schema_version=schema_version,
            route=route,
            operation_inputs={"evidenceArtifactId": str(uuid.UUID(int=8))},
            operation_config={"maxOutputTokens": 8192},
            dispatch_limit=32,
            synthetic_payload={"synthetic": True, "output": _wire()},
        )

    configure_model_runtime(
        ModelRuntime(
            database_url="unused",
            store=cast("Any", None),
            cassette_store=CassetteStore(tmp_path),
            allow_outside_activity=True,
            allow_synthetic=True,
        )
    )
    try:
        for schema_version in (COMPACT_PROPOSAL_SCHEMA_VERSION, "chapter-proposal/1"):
            model = BudgetedModel(LazyConfiguredModel(deps(schema_version)), deps(schema_version))
            await model._accept_response(  # noqa: SLF001
                runtime=models_module._configured_runtime(),  # noqa: SLF001
                operation_id=uuid.UUID(int=9),
                attempt=cast("Any", SimpleNamespace(id=uuid.UUID(int=10))),
                owner_token="test-owner",  # noqa: S106
                request_hash="a" * 64,
                response_fingerprint="b" * 64,
                response=response,
                max_output_tokens=8192,
            )
    finally:
        clear_model_runtime()

    assert metadata_values[0]["maxOutputTokens"] == 8192
    assert "maxOutputTokens" not in metadata_values[1]
