"""Transport identity preserves old receipts and fences newly configured requests."""

# Token-parameter spellings are public protocol fields, not passwords.
# ruff: noqa: S106

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from temnia_pipeline.evals.topics import (
    TopicConfiguration,
    known_transport,
    transport_projection,
)
from temnia_pipeline.harness.cassettes import CassetteMetadata, request_fingerprint
from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy
from temnia_pipeline.harness.routes import RouteEntry, load_route_snapshot, snapshot_gateway
from temnia_pipeline.harness.settings import HarnessSettings
from test_harness_settings import env, route, snapshot, write_snapshot

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from temnia_pipeline.harness.routes import RouteSnapshot


def transport(**changes: object) -> GatewayTransportPolicy:
    return GatewayTransportPolicy.model_validate(
        {
            "gateway": "openrouter",
            "mode": "streaming",
            "request_timeout_seconds": 300.0,
            "total_timeout_seconds": 540.0,
            **changes,
        }
    )


def router_route(name: str) -> RouteEntry:
    return RouteEntry.model_validate(
        {
            **route(route_id=name).model_dump(),
            "transport": transport().model_dump(mode="json"),
            "provider_accounting_name": "Fixture Provider",
        }
    )


def router_snapshot() -> RouteSnapshot:
    return snapshot(routes=tuple(router_route(name) for name in ("a", "b", "c")), synthetic=False)


def test_original_snapshot_retains_its_canonical_identity() -> None:
    path = Path(__file__).parent / "fixtures/harness/routes.synthetic.json"
    value = load_route_snapshot(path)
    assert value.computed_id() == value.snapshot_id
    assert all("transport" not in item for item in value.model_dump(mode="json")["routes"])
    assert all(
        "provider_accounting_name" not in item for item in value.model_dump(mode="json")["routes"]
    )
    assert snapshot_gateway(value) == "vercel"


def test_legacy_cassette_hash_matches_the_merged_implementation() -> None:
    metadata = CassetteMetadata(
        route_id="route-a",
        stage="proposal",
        schema_version="schema/1",
        prompt_version="prompt/1",
        program_version="program/1",
    )
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("transport identity fixture")])
    ]
    parameters = ModelRequestParameters()
    original = request_fingerprint(messages, {"max_tokens": 8192}, parameters, metadata)
    # Captured from the actual a9cf688 cassettes.py under the frozen installed SDK.
    assert original == ("14bcd86743dc312e32c844f0be2feb0380321f68dc7d6d38b80431a834d85ff8", 649)
    identities: set[tuple[str, int]] = set()
    for policy, provider in (
        (transport(), "Fixture Provider"),
        (transport(mode="non_streaming"), "Fixture Provider"),
        (transport(request_timeout_seconds=240.0), "Fixture Provider"),
        (transport(total_timeout_seconds=480.0), "Fixture Provider"),
        (transport(), "Another Provider"),
        (transport(gateway="vercel"), None),
    ):
        updated = metadata.model_copy(
            update={"transport": policy, "provider_accounting_name": provider}
        )
        identity = request_fingerprint(messages, {"max_tokens": 8192}, parameters, updated)
        assert identity != original
        identities.add(identity)
    assert len(identities) == 6


def test_openrouter_requires_explicit_accounting_identity() -> None:
    value = router_route("a").model_dump()
    value.pop("provider_accounting_name")
    with pytest.raises(ValueError, match="accounting provider"):
        RouteEntry.model_validate(value)


def test_one_worker_cannot_mix_gateway_credentials() -> None:
    mixed = snapshot(routes=(router_route("a"), route(route_id="b")))
    with pytest.raises(ValueError, match="one gateway"):
        snapshot_gateway(mixed)


def test_worker_requires_the_matching_gateway_key_and_snapshot(tmp_path: Path) -> None:
    value = router_snapshot()
    path = tmp_path / "routes.json"
    write_snapshot(path, value)
    values = {**env(path, value, "gateway"), "HARNESS_GATEWAY": "openrouter"}
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        HarnessSettings.from_env(values).validate_boot()
    values["OPENROUTER_API_KEY"] = "test-openrouter-key"
    configured = HarnessSettings.from_env(values)
    assert configured.gateway_api_key == "test-openrouter-key"
    assert configured.validate_boot() == value
    values["HARNESS_GATEWAY"] = "vercel"
    with pytest.raises(RuntimeError, match="transport differs"):
        HarnessSettings.from_env(values).validate_boot()


@pytest.mark.parametrize("gateway", ["", "auto", "https://example.test", "open-router"])
def test_worker_refuses_unrecognized_gateway_names(gateway: str) -> None:
    with pytest.raises(ValueError, match="HARNESS_GATEWAY"):
        HarnessSettings.from_env({"HARNESS_GATEWAY": gateway})


def test_frozen_transport_projects_intended_roles_before_model_calls() -> None:
    value = router_snapshot()
    projected = transport_projection(value.model_dump(mode="json"))
    assert projected == {
        "author": transport().model_dump(mode="json"),
        "reviewer": transport().model_dump(mode="json"),
    }
    configuration = TopicConfiguration(
        configuration_id=value.snapshot_id,
        policy="standalone-topics/3",
        route_snapshot=value.model_dump(mode="json"),
        execution_identity={"transport": projected},
    )
    assert known_transport(configuration)
    assert not known_transport(configuration.model_copy(update={"execution_identity": {}}))
    legacy = snapshot(routes=tuple(route(route_id=name) for name in ("a", "b", "c")))
    assert transport_projection(legacy.model_dump(mode="json")) == {
        "author": None,
        "reviewer": None,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"request_timeout_seconds": True},
        {"total_timeout_seconds": 600.0},
        {"request_timeout_seconds": 300.0, "total_timeout_seconds": 200.0},
    ],
)
def test_explicit_deadlines_fit_the_existing_model_activity(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        transport(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "gateway-transport/2"},
        {"output_token_parameter": "max_completion_tokens"},
    ],
)
def test_new_token_encoding_cannot_reinterpret_an_earlier_transport(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="version 2 requires"):
        transport(**changes)


def test_canonical_accounting_is_required_only_on_the_explicit_new_contract() -> None:
    earlier = router_route("a")
    assert "accounting_model" not in earlier.model_dump(mode="json")
    assert "output_token_parameter" not in transport().model_dump(mode="json")
    policy = transport(
        version="gateway-transport/2", output_token_parameter="max_completion_tokens"
    )
    body = {**earlier.model_dump(), "transport": policy}
    with pytest.raises(ValueError, match="frozen accounting model"):
        RouteEntry.model_validate(body)
    body["accounting_model"] = "fixture/model-dated"
    current = RouteEntry.model_validate(body)
    assert current.accounting_model == "fixture/model-dated"
    body["transport"] = transport()
    with pytest.raises(ValueError, match="earlier transports preserve"):
        RouteEntry.model_validate(body)


def test_canonical_accounting_identity_changes_paid_response_reuse() -> None:
    base = CassetteMetadata(
        route_id="route-a",
        stage="proposal",
        schema_version="schema/1",
        prompt_version="prompt/1",
        program_version="program/1",
        transport=transport(version="gateway-transport/2", output_token_parameter="max_tokens"),
        provider_accounting_name="Fixture Provider",
        accounting_model="fixture/model-20260901",
    )
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("identity fixture")])]
    parameters = ModelRequestParameters()
    first = request_fingerprint(messages, {"max_tokens": 8192}, parameters, base)
    second = request_fingerprint(
        messages,
        {"max_tokens": 8192},
        parameters,
        base.model_copy(update={"accounting_model": "fixture/model-20260902"}),
    )
    assert first != second
