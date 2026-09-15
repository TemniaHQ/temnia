"""Every editorial role's registered toolset must pass the physical request validator."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.tools import ToolDefinition

from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef, Scope
from temnia_pipeline.harness import models
from temnia_pipeline.harness.models import HarnessModelDeps, ModelPersistenceError
from temnia_pipeline.harness.routes import RouteEligibility, RouteEntry, RoutePrices

if TYPE_CHECKING:
    from temnia_pipeline.harness.topic_selection_runtime import SourceToolRole


def _ref(suffix: str, digest: str) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=UUID(f"0192e8a0-0000-7000-8000-000000000{suffix}"),
        kind=HarnessArtifactKind.checks,
        sha256=digest * 64,
        fingerprint=digest * 64,
        sizeBytes=1,
        storageKey=f"checks/{suffix}",
    )


INDEX = _ref("333", "a")
CONTEXT = _ref("444", "c")


def _route() -> RouteEntry:
    return RouteEntry(
        id="fixture-route",
        gateway_model="synthetic/model",
        family="synthetic-family",
        provider="synthetic-provider",
        open_weight=True,
        context_tokens=1_000_000,
        max_output_tokens=65_536,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="e" * 64,
            probed_at=date(2026, 9, 11),
        ),
        prices=RoutePrices(input=0, output=0),
    )


def _deps(role: SourceToolRole, *, editorial_context: bool = False) -> HarnessModelDeps:
    return HarnessModelDeps(
        scope=Scope(
            organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
            userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
        ),
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage=f"verify:selection:{role}",
        program_version="standalone-topics/7",
        prompt_version="topic-selection-source-shard/2",
        schema_version="topic-selection-portfolio/4",
        route=_route(),
        operation_inputs={},
        operation_config={},
        input_artifact_ids=(INDEX.id, CONTEXT.id),
        source_index=INDEX,
        source_tool_role=role,
        editorial_context=CONTEXT if editorial_context else None,
        candidate_selection=INDEX if role == "source_reviewer" else None,
        media_evidence=INDEX if role == "source_reviewer" else None,
        allowed_sentence_ids=("s000001", "s000002") if role == "cold_reviewer" else None,
        dispatch_limit=None,
    )


def _parameters(names: set[str]) -> ModelRequestParameters:
    return ModelRequestParameters(
        function_tools=[
            ToolDefinition(name=name, parameters_json_schema={"type": "object"})
            for name in sorted(names)
        ],
        output_mode="native",
        output_object=OutputObjectDefinition(
            json_schema={"type": "object", "properties": {}}, strict=True
        ),
    )


def _agent_tool_names(agent: Any) -> set[str]:  # noqa: ANN401
    return set(cast("dict[str, Any]", agent._function_toolset.tools))  # noqa: SLF001


AGENT_ROLES: list[tuple[str, SourceToolRole, bool]] = [
    ("topic_opportunity_inventory_v7", "inventory", False),
    ("topic_selection_author_v7", "author", False),
    ("topic_selection_cold_v7", "cold_reviewer", False),
    ("topic_selection_source_v8", "source_reviewer", True),
    ("topic_selection_source_v7", "source_reviewer", False),
    ("topic_selection_patch_v7", "repair", False),
    ("topic_opportunity_inventory_v3", "inventory", False),
    ("topic_selection_source_v4", "source_reviewer", False),
]


@pytest.mark.parametrize(("agent_name", "role", "editorial_context"), AGENT_ROLES)
def test_registered_agent_toolsets_pass_request_validation(
    agent_name: str,
    role: SourceToolRole,
    editorial_context: bool,  # noqa: FBT001 - parametrized
) -> None:
    agent = getattr(models, agent_name)
    deps = _deps(role, editorial_context=editorial_context)
    names = _agent_tool_names(agent)
    assert names == models.expected_source_tools(deps)
    models._validate_request(deps, _parameters(names))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_cold_reviewer_cannot_carry_navigation_tools() -> None:
    deps = _deps("cold_reviewer")
    with pytest.raises(ModelPersistenceError, match="exact source toolset"):
        models._validate_request(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            deps, _parameters({"browse_source", "search_source", "read_source"})
        )


def test_source_reviewer_without_paged_context_refuses_the_context_tool() -> None:
    deps = _deps("source_reviewer")
    with pytest.raises(ModelPersistenceError, match="exact source toolset"):
        models._validate_request(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            deps, _parameters(_agent_tool_names(models.topic_selection_source_v8))
        )


def test_tools_without_a_role_are_refused() -> None:
    deps = _deps("inventory").model_copy(update={"source_tool_role": None, "source_index": None})
    with pytest.raises(ModelPersistenceError, match="unqualified source tools"):
        models._validate_request(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            deps, _parameters({"read_source"})
        )
