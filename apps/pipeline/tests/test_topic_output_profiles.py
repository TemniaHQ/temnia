"""V2 route ceilings propagate without changing older programme identities."""

# The workflow tests double I/O, retaining production preparation and call construction.
# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from temnia_pipeline import db
from temnia_pipeline.harness import ledger, runs
from temnia_pipeline.harness.routes import RouteSnapshot, SeatRoutePool
from temnia_pipeline.harness.runtime_types import StartRunRequest, WorkflowIdentity
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_selection import SELECTION_POLICY
from temnia_pipeline.harness.topic_selection_runtime import (
    SelectionCallPlan,
    effective_topic_output_tokens,
    selection_call_config,
)
from temnia_pipeline.harness.topic_selection_workflow import TopicSelectionWorkflow
from test_harness_hierarchy_workflow import _request, _settings
from test_harness_model_transport import route, snapshot
from test_harness_settings import env, write_snapshot
from test_topic_selection_workflow import AgentDouble, Program, add_patch, draft, portfolio

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace


def profiles(*, author_max: int = 8192, reviewer_max: int = 32768) -> RouteSnapshot:
    author = route("synthetic-author", "author-family", "author-provider").model_copy(
        update={"max_output_tokens": author_max}
    )
    reviewer = route("synthetic-reviewer", "reviewer-family", "reviewer-provider").model_copy(
        update={"max_output_tokens": reviewer_max}
    )
    return snapshot(
        (author, reviewer),
        {
            "propose": SeatRoutePool(route_ids=(author.id, reviewer.id)),
            "summary": SeatRoutePool(route_ids=(author.id, reviewer.id)),
            "verify": SeatRoutePool(route_ids=(reviewer.id, author.id)),
        },
    )


def call_plan(stage: str, routes: RouteSnapshot) -> SelectionCallPlan:
    return SelectionCallPlan(
        prompt="Return a grounded selection judgment.",
        stage=stage,
        prompt_version="fixture-prompt/1",
        schema_version="fixture-schema/1",
        author=routes.routes[0],
        verifier=routes.routes[1],
        input_artifacts=(),
    )


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("proposal:selection:0", 8192),
        ("repair:selection:1", 8192),
        ("verify:selection:cold:fixture", 32768),
        ("verify:selection:source:0", 32768),
    ],
)
def test_effective_profile_and_old_v2_operation_identity(stage: str, expected: int) -> None:
    plan = call_plan(stage, profiles())
    assert selection_call_config(plan, 32768) == {
        "maxOutputTokens": expected,
        "reservedVerifierFamily": "reviewer-family",
    }
    prior_config = {"maxOutputTokens": 8192, "reservedVerifierFamily": "reviewer-family"}
    assert selection_call_config(plan, 8192) == prior_config
    assert ledger.operation_identity(
        run_id=_request().runId, kind="model", inputs={"fixture": "unchanged"}, config=prior_config
    ) == ledger.operation_identity(
        run_id=_request().runId,
        kind="model",
        inputs={"fixture": "unchanged"},
        config=selection_call_config(plan, 8192),
    )


@pytest.mark.parametrize("invalid", [0, -1, True])
def test_effective_profile_requires_a_positive_integer(invalid: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        effective_topic_output_tokens(invalid, profiles().routes[0])


class SettingsAgent:
    def __init__(self, wrapped: AgentDouble) -> None:
        self.wrapped = wrapped
        self.outputs: list[int] = []

    async def run(self, prompt: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        ceiling = kwargs["model_settings"]["max_tokens"]
        assert kwargs["deps"].operation_config["maxOutputTokens"] == ceiling
        self.outputs.append(ceiling)
        return await self.wrapped.run(prompt, **kwargs)


async def test_all_four_workflow_calls_use_their_prepared_route_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = Program(
        monkeypatch,
        initial=draft(selected=False),
        sources=[portfolio(selected=False, missing=True), portfolio(selected=True)],
        patches=[add_patch],
    )
    routes = profiles()
    config = program.request.config.model_copy(
        update={"maxOutputTokens": 32768, "routeSnapshotId": routes.snapshot_id}
    )
    program.request = program.request.model_copy(update={"config": config})
    program.run = program.run.model_copy(update={"config": config, "route_snapshot": routes})
    agents = {
        name: SettingsAgent(getattr(program, name))
        for name in ("author", "cold", "source", "patch")
    }
    for name, agent in agents.items():
        monkeypatch.setattr(TopicSelectionWorkflow, f"{name}_agent", agent)
    result = await TopicSelectionWorkflow().program(program.request)
    assert result.revision == 1
    assert agents["author"].outputs == agents["patch"].outputs == [8192]
    assert agents["cold"].outputs == [32768]
    assert agents["source"].outputs == [32768, 32768]


def test_boot_admits_route_profiles_below_the_configured_ceiling(tmp_path: Path) -> None:
    routes = profiles(author_max=4096, reviewer_max=8192)
    path = tmp_path / "routes.json"
    write_snapshot(path, routes)
    # Every route now boots against its own effective ceiling; there is no per-version flag.
    assert HarnessSettings.from_env(env(path, routes, "recorded")).validate_boot() == routes


def test_route_profile_still_needs_protocol_and_nonempty_request_headroom(tmp_path: Path) -> None:
    routes = profiles(author_max=4096, reviewer_max=8192)
    insufficient = routes.routes[0].model_copy(update={"context_tokens": 4096 + 8192})
    routes = snapshot((insufficient, routes.routes[1]), routes.seats)
    path = tmp_path / "routes.json"
    write_snapshot(path, routes)
    settings = HarnessSettings.from_env(env(path, routes, "recorded"))
    with pytest.raises(RuntimeError, match="protocol headroom"):
        settings.validate_boot()


@pytest.mark.parametrize("policy", ["legacy", "chapter-editorial/1", "standalone-topics/1"])
async def test_non_v2_capacity_refuses_before_creation_or_ownership_query(
    monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    routes = profiles(author_max=4096, reviewer_max=8192)
    settings, _ = _settings()
    settings = replace(settings, route_snapshot_id=routes.snapshot_id)
    request = _request().model_copy(update={"config": settings.allowed_config()})
    start = StartRunRequest(
        request=request,
        workflow=WorkflowIdentity(workflow_id="fixture", workflow_run_id="claiming-execution"),
    ).model_copy(update={"editorial_policy": policy})

    def forbid_database(*_args: object, **_kwargs: object) -> None:
        pytest.fail("an incompatible non-v2 request must not create or claim a database run")

    monkeypatch.setattr(db, "scoped", forbid_database)
    with pytest.raises(ledger.IdentityConflict, match=r"non-v2 route.*global output ceiling"):
        await runs.start_or_refetch_run(
            "unused", start=start, settings=settings, route_snapshot=routes
        )


async def test_v2_profile_reaches_scoped_run_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = profiles(author_max=4096, reviewer_max=8192)
    settings, _ = _settings()
    settings = replace(settings, route_snapshot_id=routes.snapshot_id)
    start = StartRunRequest(
        request=_request().model_copy(update={"config": settings.allowed_config()}),
        editorial_policy=SELECTION_POLICY,
        workflow=WorkflowIdentity(workflow_id="fixture", workflow_run_id="claiming-execution"),
    )

    def reached_database(*_args: object, **_kwargs: object) -> None:
        message = "scoped-admission-reached"
        raise RuntimeError(message)

    monkeypatch.setattr(db, "scoped", reached_database)
    with pytest.raises(RuntimeError, match="scoped-admission-reached"):
        await runs.start_or_refetch_run(
            "unused", start=start, settings=settings, route_snapshot=routes
        )
