"""D6: long sources are admitted (admission/2) and given payload-scaled time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic_ai.durable_exec.temporal._operation_backend import TemporalBoundOperation
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import models
from temnia_pipeline.harness.cassettes import request_payload_bytes
from temnia_pipeline.harness.gateway_policy import (
    ACTIVITY_DEADLINE_MARGIN_SECONDS,
    MAX_MODEL_ACTIVITY_SECONDS,
    PAYLOAD_DEADLINE_UNIT_BYTES,
    GatewayTransportPolicy,
    effective_total_timeout_seconds,
    model_activity_timeout_seconds,
    payload_deadline_multiplier,
)
from temnia_pipeline.harness.models import HarnessModelDeps
from temnia_pipeline.harness.routes import (
    ADMISSION_VERSION,
    ADMISSION_VERSION_LEGACY,
    MAX_REQUEST_PAYLOAD_BYTES,
    PROTOCOL_OVERHEAD_BYTES,
    ContextWindowExceeded,
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    estimate_cost,
)
from temnia_pipeline.harness.runs import run_admission_version

# The measured sizes D6 argues from: Karma's full-source prompt, and the 2.5-hour
# World Order source that the one-byte-one-token estimate refused before dispatch.
KARMA_PAYLOAD_BYTES = 90 * 1024
LONG_SOURCE_PAYLOAD_BYTES = 240 * 1024
LONGER_SOURCE_PAYLOAD_BYTES = 400 * 1024
OUTPUT_TOKENS = 32_768
SCOPE = Scope(
    organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
)
TRANSPORT = GatewayTransportPolicy(
    gateway="openrouter",
    mode="streaming",
    request_timeout_seconds=300.0,
    total_timeout_seconds=540.0,
)


def route(
    *, context_tokens: int = 262_144, transport: GatewayTransportPolicy | None = None
) -> RouteEntry:
    return RouteEntry(
        id="long-source-route",
        gateway_model="model/family-a",
        family="family-a",
        provider="provider-one",
        open_weight=True,
        context_tokens=context_tokens,
        max_output_tokens=OUTPUT_TOKENS,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=datetime(2026, 9, 8, tzinfo=UTC).date(),
        ),
        prices=RoutePrices(input=2_000_000, output=8_000_000),
        transport=transport,
        provider_accounting_name="provider-one" if transport is not None else None,
    )


@pytest.mark.parametrize(
    "payload_bytes",
    [KARMA_PAYLOAD_BYTES, LONG_SOURCE_PAYLOAD_BYTES, LONGER_SOURCE_PAYLOAD_BYTES],
)
def test_admission_two_reserves_half_a_token_per_byte(payload_bytes: int) -> None:
    estimate = estimate_cost(
        route(context_tokens=1_048_576),
        payload_bytes=payload_bytes,
        max_output_tokens=OUTPUT_TOKENS,
    )
    assert estimate.payload_bytes == payload_bytes
    assert estimate.input_tokens == payload_bytes // 2 + PROTOCOL_OVERHEAD_BYTES
    assert estimate.output_tokens == OUTPUT_TOKENS


def test_admission_two_rounds_an_odd_payload_up() -> None:
    estimate = estimate_cost(
        route(), payload_bytes=4097, protocol_overhead_bytes=0, max_output_tokens=16
    )
    assert estimate.input_tokens == 2049


def test_a_two_and_a_half_hour_source_now_fits_a_256k_route() -> None:
    candidate = route()
    admitted = estimate_cost(
        candidate, payload_bytes=LONG_SOURCE_PAYLOAD_BYTES, max_output_tokens=OUTPUT_TOKENS
    )
    assert admitted.input_tokens + admitted.output_tokens <= candidate.context_tokens
    # The same request under admission/1 (one byte, one token) did not fit.
    assert (
        candidate.context_tokens
        < LONG_SOURCE_PAYLOAD_BYTES + PROTOCOL_OVERHEAD_BYTES + OUTPUT_TOKENS
    )


def test_the_512_kib_payload_cap_is_unchanged() -> None:
    assert MAX_REQUEST_PAYLOAD_BYTES == 512 * 1024
    at_cap = estimate_cost(
        route(context_tokens=1_048_576),
        payload_bytes=MAX_REQUEST_PAYLOAD_BYTES,
        max_output_tokens=OUTPUT_TOKENS,
    )
    assert at_cap.input_tokens == MAX_REQUEST_PAYLOAD_BYTES // 2 + PROTOCOL_OVERHEAD_BYTES
    with pytest.raises(ContextWindowExceeded, match="512 KiB"):
        estimate_cost(route(context_tokens=1_048_576), payload_bytes=MAX_REQUEST_PAYLOAD_BYTES + 1)


def test_the_context_refusal_names_the_new_estimate() -> None:
    candidate = route(context_tokens=131_072)
    with pytest.raises(ContextWindowExceeded) as raised:
        estimate_cost(
            candidate, payload_bytes=LONGER_SOURCE_PAYLOAD_BYTES, max_output_tokens=OUTPUT_TOKENS
        )
    refusal = str(raised.value)
    assert "long-source-route" in refusal
    assert f"{LONGER_SOURCE_PAYLOAD_BYTES} payload bytes" in refusal
    assert (
        f"about {LONGER_SOURCE_PAYLOAD_BYTES // 2 + PROTOCOL_OVERHEAD_BYTES} estimated" in refusal
    )
    assert f"{OUTPUT_TOKENS} output tokens" in refusal
    assert "131072-token context window" in refusal


@pytest.mark.parametrize(
    ("payload_bytes", "units"),
    [
        (0, 1),
        (1, 1),
        (KARMA_PAYLOAD_BYTES, 1),
        (PAYLOAD_DEADLINE_UNIT_BYTES, 1),
        (PAYLOAD_DEADLINE_UNIT_BYTES + 1, 2),
        (LONG_SOURCE_PAYLOAD_BYTES, 2),
        (LONGER_SOURCE_PAYLOAD_BYTES, 4),
    ],
)
def test_payload_units_start_at_one_and_count_whole_units(payload_bytes: int, units: int) -> None:
    assert payload_deadline_multiplier(payload_bytes) == units


def test_karma_sized_payloads_keep_the_frozen_deadline_and_activity_timeout() -> None:
    assert effective_total_timeout_seconds(TRANSPORT, KARMA_PAYLOAD_BYTES) == 540.0
    assert model_activity_timeout_seconds(TRANSPORT, KARMA_PAYLOAD_BYTES) == 600.0
    assert timedelta(seconds=model_activity_timeout_seconds(TRANSPORT, 0)) == timedelta(minutes=10)


def test_a_two_and_a_half_hour_payload_doubles_the_deadline() -> None:
    assert effective_total_timeout_seconds(TRANSPORT, LONG_SOURCE_PAYLOAD_BYTES) == 1080.0
    assert model_activity_timeout_seconds(TRANSPORT, LONG_SOURCE_PAYLOAD_BYTES) == 1140.0


def test_the_activity_deadline_stops_at_forty_five_minutes() -> None:
    huge = 64 * PAYLOAD_DEADLINE_UNIT_BYTES
    assert effective_total_timeout_seconds(TRANSPORT, huge) == 540.0 * 64
    assert model_activity_timeout_seconds(TRANSPORT, huge) == float(MAX_MODEL_ACTIVITY_SECONDS)
    assert MAX_MODEL_ACTIVITY_SECONDS == 45 * 60


def test_a_route_without_a_frozen_transport_keeps_ten_minutes() -> None:
    assert model_activity_timeout_seconds(None, LONGER_SOURCE_PAYLOAD_BYTES) == 600.0


def test_the_margin_keeps_the_transport_deadline_first() -> None:
    assert ACTIVITY_DEADLINE_MARGIN_SECONDS == 60
    for payload_bytes in (0, KARMA_PAYLOAD_BYTES, LONG_SOURCE_PAYLOAD_BYTES):
        assert (
            model_activity_timeout_seconds(TRANSPORT, payload_bytes)
            == effective_total_timeout_seconds(TRANSPORT, payload_bytes) + 60
        )


def _deps(candidate: RouteEntry) -> HarnessModelDeps:
    return HarnessModelDeps(
        scope=SCOPE,
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage="proposal:selection:0",
        program_version="standalone-topics/3",
        prompt_version="topic-selection-author-v3",
        schema_version="topic-selection-draft/3",
        route=candidate,
        operation_inputs={},
        operation_config={},
        dispatch_limit=8,
    )


class _Params:
    """The workflow-side shape of one model request operation call."""

    def __init__(self, deps: HarnessModelDeps, prompt: str) -> None:
        self.messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(prompt)])]
        self.model_settings = None
        self.model_request_parameters = ModelRequestParameters()
        self.run_context = type("RunContext", (), {"deps": deps})()


async def _scaled_timeouts(
    monkeypatch: pytest.MonkeyPatch, prompts: list[str], candidate: RouteEntry
) -> list[timedelta]:
    captured: list[timedelta] = []

    async def fake_call(
        self: TemporalBoundOperation[Any, Any, Any],
        params: Any,  # noqa: ANN401
        *,
        config: object | None = None,
    ) -> None:
        _ = self, params
        captured.append(cast("dict[str, Any]", config)["start_to_close_timeout"])

    monkeypatch.setattr(TemporalBoundOperation, "__call__", fake_call)
    operation = models.PayloadScaledModelRequest(
        cast("Any", None),
        registration=cast("Any", None),
        config={"start_to_close_timeout": timedelta(minutes=10)},
    )
    for prompt in prompts:
        await cast("Any", operation)(_Params(_deps(candidate), prompt))
    return captured


async def test_the_model_activity_timeout_is_computed_from_that_call_s_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = route(context_tokens=1_048_576, transport=TRANSPORT)
    deps = _deps(candidate)
    prompts = ["one short question", "x" * (3 * PAYLOAD_DEADLINE_UNIT_BYTES)]
    observed = await _scaled_timeouts(monkeypatch, prompts, candidate)
    expected = [
        timedelta(
            seconds=model_activity_timeout_seconds(
                TRANSPORT,
                request_payload_bytes(
                    _Params(deps, prompt).messages,
                    None,
                    ModelRequestParameters(),
                    models._cassette_metadata(deps),  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
                ),
            )
        )
        for prompt in prompts
    ]
    assert observed == expected
    assert observed[0] == timedelta(minutes=10)
    assert observed[1] == timedelta(seconds=540 * 4 + 60)


def test_every_harness_agent_carries_the_payload_scaled_durability() -> None:
    for agent in models.HARNESS_AGENTS:
        capabilities = cast("Any", agent.root_capability).capabilities
        assert any(
            isinstance(capability, models.PayloadScaledDurability) for capability in capabilities
        )


def test_run_admission_version_reads_older_snapshots_as_admission_one() -> None:
    assert run_admission_version({"admission": ADMISSION_VERSION}) == "admission/2"
    assert run_admission_version({}) == ADMISSION_VERSION_LEGACY
    assert run_admission_version({"admission": "admission/1"}) == "admission/1"
