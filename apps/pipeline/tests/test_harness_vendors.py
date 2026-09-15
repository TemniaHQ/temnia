"""Direct vendor routes: schema, keys, settings, usage settlement and header pacing."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy
from temnia_pipeline.harness.models import RouteGate
from temnia_pipeline.harness.routes import (
    NoEligibleRoute,
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    RouteSnapshot,
    SeatRoutePool,
    VendorAccountTerms,
    estimate_cost,
    select_route,
    snapshot_gateway,
)
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.topic_editorial import inventory_route
from temnia_pipeline.harness.topic_windows import REPAIR_INSTRUCTIONS, REPAIR_PROMPT_VERSION
from temnia_pipeline.harness.vendors import (
    LOW_WATER_TOKENS,
    RateLimitReading,
    VendorKeys,
    VendorPolicyError,
    _mark_vendor_request,  # pyright: ignore[reportPrivateUsage]
    build_vendor_model,
    duration_seconds,
    is_spend_cap,
    mark_request_sent,
    read_rate_limits,
    retry_after_seconds,
    rfc3339_seconds_from,
    settle_usage,
    track_dispatch,
    vendor_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

TERMS = VendorAccountTerms(zero_data_retention=True, recorded_at=date(2026, 9, 15), reference="t")
KEYS = VendorKeys(anthropic="sk-a", openai="sk-o", google="sk-g")


def vendor_route(
    identifier: str,
    vendor: str,
    *,
    family: str | None = None,
    prices: RoutePrices | None = None,
    **overrides: object,
) -> RouteEntry:
    values: dict[str, object] = {
        "id": identifier,
        "gateway_model": f"{vendor}-model",
        "family": family or vendor,
        "provider": vendor,
        "open_weight": False,
        "context_tokens": 1_000_000,
        "max_output_tokens": 128_000,
        "prices": prices
        or RoutePrices(
            input=1_000_000, output=2_000_000, cache_read=100_000, cache_write=1_250_000
        ),
        "reasoning_effort": "high",
        "cache_enabled": vendor == "anthropic",
        "vendor": vendor,
        "account": TERMS,
    }
    values.update(overrides)
    return RouteEntry.model_validate(values)


def direct_snapshot(
    routes: tuple[RouteEntry, ...], seats: dict[str, SeatRoutePool]
) -> RouteSnapshot:
    draft = RouteSnapshot.model_construct(
        version=1, snapshot_id="0" * 64, signature=None, synthetic=False, routes=routes, seats=seats
    )
    return RouteSnapshot(
        version=1,
        snapshot_id=draft.computed_id(),
        signature=None,
        synthetic=False,
        routes=routes,
        seats=seats,
    )


# ---------------------------------------------------------------------------------- routes


def test_vendor_route_records_account_terms_and_no_gateway_facts() -> None:
    route = vendor_route("opus", "anthropic")
    assert route.eligibility is None
    dumped = route.model_dump(mode="json")
    assert dumped["vendor"] == "anthropic"
    assert "transport" not in dumped
    assert "eligibility" not in dumped
    assert RouteEntry.model_validate_json(route.model_dump_json(), strict=True) == route


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"account": None}, "account terms"),
        ({"provider": "somebody-else"}, "provider is the vendor"),
        (
            {
                "eligibility": RouteEligibility(
                    zero_data_retention=True,
                    strict_json_schema=True,
                    probe_artifact_sha256="a" * 64,
                    probed_at=date(2026, 9, 1),
                )
            },
            "account terms, not a gateway probe",
        ),
        (
            {
                "transport": GatewayTransportPolicy(
                    gateway="vercel",
                    mode="non_streaming",
                    request_timeout_seconds=1,
                    total_timeout_seconds=2,
                )
            },
            "no gateway transport",
        ),
    ],
)
def test_vendor_route_refuses_gateway_shapes(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        vendor_route("opus", "anthropic", **overrides)


def test_gateway_route_still_requires_its_probe() -> None:
    with pytest.raises(ValidationError, match="qualification evidence"):
        RouteEntry(
            id="legacy",
            gateway_model="x/y",
            family="x",
            provider="p",
            open_weight=True,
            context_tokens=10,
            max_output_tokens=5,
            prices=RoutePrices(input=1, output=1),
        )


def test_snapshot_needs_two_families_per_pool_and_one_kind_of_route() -> None:
    opus = vendor_route("opus", "anthropic")
    sol = vendor_route("sol", "openai")
    terra = vendor_route("terra", "openai")
    flash = vendor_route("flash", "google")
    seats = {
        "propose": SeatRoutePool(route_ids=(opus.id, sol.id)),
        "verify": SeatRoutePool(route_ids=(terra.id, flash.id)),
    }
    snapshot = direct_snapshot((opus, sol, terra, flash), seats)
    assert snapshot.direct
    assert snapshot.vendors() == {"anthropic", "openai", "google"}
    assert snapshot_gateway(snapshot) == "direct"
    with pytest.raises(ValidationError, match="two model families"):
        direct_snapshot(
            (opus, sol, terra),
            {
                "propose": SeatRoutePool(route_ids=(opus.id, sol.id)),
                "verify": SeatRoutePool(route_ids=(terra.id, sol.id)),
            },
        )
    legacy = RouteEntry(
        id="legacy",
        gateway_model="x/y",
        family="legacy",
        provider="p",
        open_weight=True,
        context_tokens=1000,
        max_output_tokens=500,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 1),
        ),
        prices=RoutePrices(input=1, output=1),
    )
    with pytest.raises(ValidationError, match="either direct vendor routes or gateway routes"):
        direct_snapshot(
            (opus, legacy),
            {"propose": SeatRoutePool(route_ids=(opus.id, legacy.id))},
        )


def test_keyless_vendor_is_skipped_and_the_stop_names_the_variable() -> None:
    terra = vendor_route("terra", "openai")
    flash = vendor_route("flash", "google")
    snapshot = direct_snapshot(
        (terra, flash),
        {
            "propose": SeatRoutePool(route_ids=(terra.id, flash.id)),
            "verify": SeatRoutePool(route_ids=(terra.id, flash.id)),
        },
    )
    assert select_route(snapshot, "verify", excluded_vendors=frozenset({"openai"})) is flash
    with pytest.raises(NoEligibleRoute, match="OPENAI_API_KEY"):
        select_route(snapshot, "verify", excluded_vendors=frozenset({"openai", "google"}))
    with pytest.raises(NoEligibleRoute, match="GEMINI_API_KEY"):
        select_route(snapshot, "verify", candidate_index=1, excluded_vendors=frozenset({"google"}))


def test_inventory_seat_is_optional_and_falls_back_to_the_reviewer() -> None:
    opus = vendor_route("opus", "anthropic")
    sonnet = vendor_route("sonnet", "anthropic")
    terra = vendor_route("terra", "openai")
    flash = vendor_route("flash", "google")
    with_seat = direct_snapshot(
        (opus, sonnet, terra, flash),
        {
            "propose": SeatRoutePool(route_ids=(opus.id, terra.id)),
            "verify": SeatRoutePool(route_ids=(terra.id, flash.id)),
            "inventory": SeatRoutePool(route_ids=(sonnet.id, flash.id)),
        },
    )
    assert inventory_route(with_seat, verifier=terra) is sonnet
    assert inventory_route(with_seat, verifier=terra, inventory_index=1) is flash
    without = direct_snapshot(
        (opus, terra, flash),
        {
            "propose": SeatRoutePool(route_ids=(opus.id, terra.id)),
            "verify": SeatRoutePool(route_ids=(terra.id, flash.id)),
        },
    )
    assert inventory_route(without, verifier=terra) is terra


def test_estimate_reserves_the_expected_output_but_checks_the_cap() -> None:
    route = vendor_route("opus", "anthropic", prices=RoutePrices(input=1_000_000, output=2_000_000))
    full = estimate_cost(route, payload_bytes=2000, max_output_tokens=1000)
    typical = estimate_cost(
        route, payload_bytes=2000, max_output_tokens=1000, expected_output_tokens=500
    )
    assert typical.output_tokens == 500
    assert typical.amount_micros == full.amount_micros - 1000
    with pytest.raises(ValueError, match="within the output cap"):
        estimate_cost(route, payload_bytes=1, max_output_tokens=10, expected_output_tokens=11)


# ------------------------------------------------------------------------------------ keys


def test_keys_read_the_three_variables_and_the_google_alias() -> None:
    keys = VendorKeys.from_env({"ANTHROPIC_API_KEY": "a", "GOOGLE_API_KEY": "g"})
    assert keys == VendorKeys(anthropic="a", openai=None, google="g")
    assert keys.missing(frozenset({"anthropic", "openai", "google"})) == {"openai"}
    assert VendorKeys.from_env({"GEMINI_API_KEY": "x", "GOOGLE_API_KEY": "y"}).google == "x"


def test_direct_settings_boot_without_keys_and_name_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    opus = vendor_route("opus", "anthropic")
    terra = vendor_route("terra", "openai")
    snapshot = direct_snapshot(
        (opus, terra),
        {
            "propose": SeatRoutePool(route_ids=(opus.id, terra.id)),
            "verify": SeatRoutePool(route_ids=(terra.id, opus.id)),
        },
    )
    path = tmp_path / "routes.json"
    path.write_text(snapshot.model_dump_json())
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ENABLED": "1",
            "HARNESS_BACKEND": "gateway",
            "HARNESS_GATEWAY": "direct",
            "HARNESS_ROUTE_SNAPSHOT_ID": snapshot.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(path),
            "HARNESS_MAX_OUTPUT_TOKENS": "4096",
            "OPENAI_API_KEY": "sk-o",
        }
    )
    assert settings.gateway == "direct"
    assert settings.gateway_api_key is None
    with caplog.at_level(logging.WARNING, logger="temnia.harness.settings"):
        loaded = settings.validate_boot()
    assert loaded == snapshot
    assert "ANTHROPIC_API_KEY is not set" in caplog.text
    assert "OPENAI_API_KEY" not in caplog.text.replace("OPENAI_API_KEY is not set", "")


# --------------------------------------------------------------------------------- headers


def test_reset_formats_parse_and_junk_is_none() -> None:
    assert duration_seconds("6m0s") == 360.0
    assert duration_seconds("1s") == 1.0
    assert duration_seconds("250ms") == 0.25
    assert duration_seconds("1h2m") == 3720.0
    assert duration_seconds("soon") is None
    assert duration_seconds("") is None
    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    assert rfc3339_seconds_from("2026-09-15T12:00:30Z", now) == 30.0
    assert rfc3339_seconds_from("2026-09-15T11:59:00Z", now) == 0.0
    assert rfc3339_seconds_from("yesterday", now) is None


def test_anthropic_and_openai_headers_become_one_reading() -> None:
    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    anthropic = read_rate_limits(
        "anthropic",
        {
            "Anthropic-RateLimit-Input-Tokens-Remaining": "40000",
            "Anthropic-RateLimit-Input-Tokens-Reset": "2026-09-15T12:00:20Z",
            "anthropic-ratelimit-requests-remaining": "999",
            "anthropic-ratelimit-requests-reset": "2026-09-15T12:00:01Z",
            "content-type": "application/json",
        },
        now=now,
    )
    assert anthropic.remaining_tokens == 40_000
    assert anthropic.tokens_reset_seconds == 20.0
    assert anthropic.remaining_requests == 999
    assert "content-type" not in anthropic.headers
    assert anthropic.pause_seconds() == 20.0
    openai = read_rate_limits(
        "openai",
        {
            "x-ratelimit-remaining-tokens": "250000",
            "x-ratelimit-reset-tokens": "6m0s",
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "2s",
        },
    )
    assert openai.remaining_tokens == 250_000
    assert openai.pause_seconds() == 2.0
    google = read_rate_limits("google", {"retry-after": "7"})
    assert google.remaining_tokens is None
    assert google.pause_seconds() == 7.0
    assert RateLimitReading(vendor="openai", remaining_tokens=LOW_WATER_TOKENS).pause_seconds() == 0


async def test_gate_paces_from_a_reading() -> None:
    gate = RouteGate(max_in_flight=2, min_interval_seconds=0)
    open_reading = RateLimitReading(vendor="anthropic", remaining_tokens=1_000_000)
    assert gate.pace(open_reading) == 0.0
    assert gate.cooldown_remaining() == 0.0
    low = RateLimitReading(vendor="anthropic", remaining_tokens=1000, tokens_reset_seconds=0.05)
    assert gate.pace(low) == 0.05
    loop = asyncio.get_running_loop()
    started = loop.time()
    await gate.acquire()
    gate.release()
    assert loop.time() - started >= 0.04


def test_spend_cap_and_retry_after_are_read_from_the_exception() -> None:
    capped = ModelHTTPError(
        status_code=429,
        model_name="claude-opus-5",
        body={
            "error": {
                "type": "rate_limit_error",
                "details": {"error_code": "enforced_spend_limit_reached"},
            }
        },
        headers={},
    )
    assert is_spend_cap(capped)
    assert retry_after_seconds(capped) is None
    throttled = ModelHTTPError(
        status_code=429, model_name="gpt-5.6-terra", body=None, headers={"retry-after": "12"}
    )
    assert not is_spend_cap(throttled)
    assert retry_after_seconds(throttled) == 12.0


# ------------------------------------------------------------------------------------ usage


def test_usage_settles_uncached_cached_and_output_at_their_prices() -> None:
    route = vendor_route("opus", "anthropic")
    settled = settle_usage(
        route,
        RequestUsage(
            input_tokens=1000, cache_read_tokens=400, cache_write_tokens=100, output_tokens=50
        ),
    )
    assert settled is not None
    # 500 uncached at $1, 400 reads at $0.10, 100 writes at $1.25, 50 out at $2 (per MTok).
    assert settled.amount_micros == 500 + 40 + 125 + 100
    assert settled.components["uncachedInputTokens"] == 500
    assert settled.components["settlement"] == "usage"


def test_usage_without_cache_prices_charges_cache_tokens_as_input() -> None:
    route = vendor_route("terra", "openai", prices=RoutePrices(input=2_000_000, output=12_000_000))
    settled = settle_usage(route, RequestUsage(input_tokens=1000, cache_read_tokens=1000))
    assert settled is not None
    assert settled.amount_micros == 2000


def test_empty_usage_is_no_settlement() -> None:
    assert settle_usage(vendor_route("opus", "anthropic"), RequestUsage()) is None


# --------------------------------------------------------------------------------- adapter


def test_settings_carry_allowance_thinking_and_cache_and_refuse_overrides() -> None:
    opus = vendor_route("opus", "anthropic")
    settings = cast("dict[str, Any]", vendor_settings(opus, {"max_tokens": 4096}))
    assert settings == {"max_tokens": 4096, "thinking": "high", "anthropic_cache": True}
    terra = vendor_route("terra", "openai", service_tier="priority")
    settings = cast("dict[str, Any]", vendor_settings(terra, None))
    assert settings == {
        "max_tokens": 128_000,
        "thinking": "high",
        "service_tier": "priority",
        "openai_store": False,
    }
    with pytest.raises(VendorPolicyError, match="override"):
        vendor_settings(opus, cast("Any", {"extra_body": {"x": 1}}))
    with pytest.raises(VendorPolicyError, match="exceeds"):
        vendor_settings(opus, {"max_tokens": 1_000_000})


@pytest.mark.parametrize(
    ("vendor", "adapter"),
    [("anthropic", AnthropicModel), ("openai", OpenAIResponsesModel), ("google", GoogleModel)],
)
async def test_each_vendor_builds_its_adapter_with_sdk_retries_off(
    vendor: str, adapter: type[Any]
) -> None:
    route = vendor_route(vendor, vendor)
    model = build_vendor_model(route, KEYS)
    async with model:
        assert isinstance(model.wrapped, adapter)
        assert model.model_name == f"{vendor}-model"
        client = cast("Any", model.wrapped).client
        if vendor != "google":
            assert client.max_retries == 0


def test_missing_key_is_a_sentence_naming_the_variable() -> None:
    with pytest.raises(VendorPolicyError, match="GEMINI_API_KEY is not set"):
        build_vendor_model(vendor_route("flash", "google"), VendorKeys(anthropic="a"))


async def test_the_vendor_client_is_rebuilt_for_every_request_context() -> None:
    """The budgeted model enters the context once per round; a closed client is never reused."""
    model = build_vendor_model(vendor_route("opus", "anthropic"), KEYS)
    async with model:
        first = model._owned_http_client  # noqa: SLF001
        assert first is not None
        assert not first.is_closed
        assert _mark_vendor_request in first.event_hooks["request"]
    assert first.is_closed
    async with model:
        second = model._owned_http_client  # noqa: SLF001
        assert second is not None
        assert second is not first
        assert not second.is_closed
        assert cast("Any", model.wrapped).client._client is second  # noqa: SLF001
    assert second.is_closed


async def test_the_request_hook_marks_the_dispatch_as_sent() -> None:
    with track_dispatch() as trace:
        assert trace.sent is False
        await _mark_vendor_request(cast("Any", object()))
        assert trace.sent is True
    mark_request_sent()  # outside a tracked dispatch: nothing to record, nothing raised


def test_repair_instructions_state_every_model_facing_admission_rule() -> None:
    text = " ".join(REPAIR_INSTRUCTIONS.split())
    # Each phrase is one rule the validator refuses on; the first two frontier runs lost every
    # repair patch to rules the instruction had not stated.
    for rule in (
        "`<workItemId>:operation:`",
        "`<workItemId>:candidate:`",
        "carrying that same existing candidate ID",
        "outside the supplied windows is rejected",
        "One to sixteen operations",
        "cannot return an empty patch",
        "requires an unsupported_title finding",
        "requires a missed_opportunity finding",
        "must not reuse an existing ID",
        "each opportunity at most once per patch",
    ):
        assert rule in text, rule
    assert REPAIR_PROMPT_VERSION == "topic-repair-window/4"
