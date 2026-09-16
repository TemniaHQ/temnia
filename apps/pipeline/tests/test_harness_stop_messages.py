"""Every terminal run message names the cause and what the reader can do about it."""

# The failure helper is module-private on purpose; these are its contract tests.
# pyright: reportPrivateUsage=false
# ruff: noqa: TC002, TC003
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.exceptions import ActivityError, ApplicationError, RetryState

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import gateway, ledger, models
from temnia_pipeline.harness.cassettes import CassetteStore
from temnia_pipeline.harness.gateway import CostObservation, GatewayConfig
from temnia_pipeline.harness.ledger import OUTCOME_UNKNOWN_RUN_MESSAGE
from temnia_pipeline.harness.models import HarnessModelDeps
from temnia_pipeline.harness.routes import (
    ContextWindowExceeded,
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    estimate_cost,
)
from temnia_pipeline.harness.run_failures import known_failure_details


class Answer(BaseModel):
    """The smallest strict native output a harness call can ask for."""

    text: str


def route() -> RouteEntry:
    return RouteEntry(
        id="fixture-route",
        gateway_model="synthetic/model",
        family="synthetic-family",
        provider="synthetic-provider",
        open_weight=True,
        context_tokens=20_000,
        max_output_tokens=8192,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 8),
        ),
        prices=RoutePrices(input=0, output=0),
    )


def activity_error(
    error_type: str, message: str, *, activity_type: str = "run_agent"
) -> ActivityError:
    failure = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="fixture",
        activity_type=activity_type,
        activity_id="1",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    failure.__cause__ = ApplicationError(message, type=error_type, non_retryable=True)
    return failure


def test_route_rejection_names_route_stage_and_status_with_the_next_action() -> None:
    status, message = known_failure_details(
        activity_error(
            "KnownProviderRejection",
            "Route fixture-route rejected the verify:selection:source:1 request (HTTP 404).",
        )
    )
    assert status == "failed"
    assert message == (
        "Route fixture-route rejected the verify:selection:source:1 request (HTTP 404). "
        "Change the route snapshot and start a new run; nothing was retried."
    )


def test_payment_required_names_the_account_not_the_route() -> None:
    status, message = known_failure_details(
        activity_error(
            "KnownProviderRejection",
            "Route fixture-route rejected the proposal:selection:0 request (HTTP 402).",
        )
    )
    assert status == "failed"
    assert message == (
        "Route fixture-route rejected the proposal:selection:0 request (HTTP 402). The vendor "
        "account is out of credit or at its spending limit; add credit or raise the limit, then "
        "retry this run. Settled work and charges are retained and reused; nothing was charged "
        "for the refused request."
    )


def test_an_exhausted_vendor_account_names_the_credit_not_the_snapshot() -> None:
    """The third Karma run: OpenAI's 429 said the balance was empty; the sentence must too."""
    rejection = (
        "Route openai-gpt-5.6-terra-high rejected the verify:selection:source:2:x request "
        "(HTTP 429). openai said: You have no credits remaining. Add credits to continue using "
        "the API at https://platform.openai.com/settings/organization/billing/."
    )
    status, message = known_failure_details(activity_error("KnownProviderRejection", rejection))
    assert status == "failed"
    assert message.startswith(rejection)
    assert "add credit or raise the limit, then retry this run" in message
    assert "Change the route snapshot" not in message


def test_exhausted_seat_names_every_route_and_the_next_action() -> None:
    status, message = known_failure_details(
        activity_error(
            "SeatRoutesExhausted",
            "Every qualified verifier route failed transiently for the verify:selection:cold:x "
            "call (route-a, route-b); last: HTTP 429 before any response.",
        )
    )
    assert status == "failed"
    assert message.startswith("Every qualified verifier route failed transiently")
    assert "route-a, route-b" in message
    assert "Retry this run later or change the route snapshot." in message
    assert message.endswith("Settled work and charges are retained.")


def test_context_window_refusal_keeps_the_exact_sentence_from_the_refusing_site() -> None:
    with pytest.raises(ContextWindowExceeded) as raised:
        estimate_cost(route(), payload_bytes=19_000, max_output_tokens=8192)
    refusal = str(raised.value)
    assert "fixture-route" in refusal
    assert "19000 payload bytes" in refusal
    assert "20000-token context window" in refusal
    status, message = known_failure_details(activity_error("ContextWindowExceeded", refusal))
    assert (status, message) == ("failed", refusal)


@pytest.mark.parametrize(
    ("activity_type", "stage"),
    [
        ("agent__topic_opportunity_inventory_v3__model_request", "source opportunity inventory"),
        ("agent__topic_selection_author_v3__model_request", "author"),
        ("run_agent", "model"),
    ],
)
def test_invalid_first_response_names_its_stage_and_the_retained_charge(
    activity_type: str, stage: str
) -> None:
    status, message = known_failure_details(
        activity_error(
            "UnexpectedModelBehavior",
            "Exceeded maximum retries",
            activity_type=activity_type,
        )
    )
    assert status == "failed"
    assert message == (
        f"The {stage} response was incomplete or invalid; "
        "the charge is retained and nothing was retried."
    )


def test_unclassified_activity_failure_names_its_error_type() -> None:
    assert known_failure_details(activity_error("KnownMediaFailure", "bounded media")) == (
        "failed",
        "The run stopped after an activity failure: KnownMediaFailure.",
    )


def test_the_unknown_outcome_sentence_says_what_is_retained_and_what_happens_next() -> None:
    assert OUTCOME_UNKNOWN_RUN_MESSAGE == (
        "A provider call ended without a confirmed outcome. Its reservation is retained while "
        "the gateway receipt is awaited; the run resumes on its own once the charge is settled, "
        "and a finished run becomes retryable."
    )


async def _dispatch_refused_with(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    retry_after: float | None = None,
) -> tuple[BaseException, list[dict[str, object]]]:
    """Drive one budgeted call into an HTTP status before any response."""
    failures: list[dict[str, object]] = []

    async def acquire(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            accepted=False, operation=SimpleNamespace(id=uuid4(), result_artifact_id=None)
        )

    async def no_recovery(*_args: object, **_kwargs: object) -> None:
        return None

    async def reserve(*_args: object, **kwargs: object) -> ledger.Attempt:
        return ledger.Attempt(
            id=uuid4(),
            operation_id=uuid4(),
            run_id=None,
            attempt_number=1,
            owner_token=str(kwargs["owner_token"]),
            state=ledger.AttemptState.RESERVED,
            estimated_cost_micros=0,
            actual_cost_micros=None,
            cost_status="unknown",
            remote_handle=None,
            dispatched_at=None,
            finished_at=None,
        )

    async def dispatched(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fail_attempt(*_args: object, **kwargs: object) -> None:
        failures.append(dict(kwargs))

    monkeypatch.setattr(models.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(models.ledger, "find_recoverable_attempt", no_recovery)
    monkeypatch.setattr(models.ledger, "reserve_attempt", reserve)
    monkeypatch.setattr(models.ledger, "mark_dispatched", dispatched)
    monkeypatch.setattr(models.ledger, "fail_attempt", fail_attempt)
    monkeypatch.setattr(models.artifacts, "find_artifact", no_recovery)

    def rejecting(route: RouteEntry) -> FunctionModel:  # noqa: ARG001
        def refuse(
            _messages: list[ModelMessage], _info: AgentInfo
        ) -> ModelResponse:  # pragma: no cover - the transport raises first
            error = ModelHTTPError(status_code=status_code, model_name="synthetic/model")
            if retry_after is not None:
                error.retry_after_seconds = retry_after  # type: ignore[attr-defined]
            raise error

        return FunctionModel(refuse, model_name="synthetic/model")

    deps = HarnessModelDeps(
        scope=Scope(
            organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
            userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
        ),
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage="verify:selection:source:1",
        program_version="standalone-topics/3",
        prompt_version="standalone-topic-selection-source-v3",
        schema_version="topic-selection-portfolio/4",
        route=route(),
        operation_inputs={},
        operation_config={},
        dispatch_limit=8,
    )
    models.configure_model_runtime(
        models.ModelRuntime(
            database_url="unused",
            store=cast("Any", None),
            cassette_store=CassetteStore(tmp_path),
            model_factory=rejecting,
            allow_outside_activity=True,
        )
    )
    try:
        budgeted = models.BudgetedModel(models.LazyConfiguredModel(deps), deps)
        agent = Agent(budgeted, output_type=NativeOutput(Answer, strict=True), retries=0)
        with pytest.raises(Exception) as raised:  # noqa: PT011 - the class is the assertion
            await agent.run("Ask")
    finally:
        models.clear_model_runtime()
    return raised.value, failures


async def test_conclusive_http_rejection_raises_a_route_stage_and_status_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal an operator reads is written where the route and stage are known."""
    raised, failures = await _dispatch_refused_with(404, monkeypatch, tmp_path)
    assert isinstance(raised, models.KnownProviderRejection)
    assert str(raised) == (
        "Route fixture-route rejected the verify:selection:source:1 request (HTTP 404)."
    )
    assert failures[0]["outcome_known"] is True
    assert failures[0]["actual_cost_micros"] == 0


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 502, 503, 529])
async def test_throttling_and_outages_before_any_response_are_transient_and_free(
    status_code: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No generation exists, so the reservation is released and a retry is allowed."""
    raised, failures = await _dispatch_refused_with(
        status_code, monkeypatch, tmp_path, retry_after=17
    )
    assert isinstance(raised, models.TransientProviderFailure)
    assert str(raised) == (
        "Route fixture-route answered the verify:selection:source:1 request with "
        f"HTTP {status_code} before any response; nothing was charged and a fresh attempt "
        "is allowed. The provider asked for a pause of 17 s."
    )
    assert failures[0]["outcome_known"] is True
    assert failures[0]["actual_cost_micros"] == 0
    assert failures[0]["error_code"] == f"http-{status_code}"


def test_exhausted_transient_failure_names_the_settled_cause_and_the_next_action() -> None:
    status, message = known_failure_details(
        activity_error(
            "TransientProviderFailure",
            "Route fixture-route: the verify:selection:source:0 request ended without a "
            "response; its charge of 0 micros is settled and a fresh attempt is allowed.",
        )
    )
    assert status == "failed"
    assert message == (
        "Route fixture-route: the verify:selection:source:0 request ended without a "
        "response; its charge of 0 micros is settled and a fresh attempt is allowed. "
        "Three attempts ended the same way; nothing more is retried. "
        "Change the route snapshot or start a new run later."
    )


async def test_lost_stream_with_a_settled_receipt_is_a_known_failure_not_an_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An upstream error inside a 200 stream settles against the receipt and may be retried."""
    failures: list[dict[str, object]] = []
    lookups: list[str] = []

    async def acquire(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            accepted=False, operation=SimpleNamespace(id=uuid4(), result_artifact_id=None)
        )

    async def no_recovery(*_args: object, **_kwargs: object) -> None:
        return None

    async def reserve(*_args: object, **kwargs: object) -> ledger.Attempt:
        return ledger.Attempt(
            id=uuid4(),
            operation_id=uuid4(),
            run_id=None,
            attempt_number=1,
            owner_token=str(kwargs["owner_token"]),
            state=ledger.AttemptState.RESERVED,
            estimated_cost_micros=0,
            actual_cost_micros=None,
            cost_status="unknown",
            remote_handle=None,
            dispatched_at=None,
            finished_at=None,
        )

    async def dispatched(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fail_attempt(*_args: object, **kwargs: object) -> None:
        failures.append(dict(kwargs))

    async def attach(*_args: object, **_kwargs: object) -> None:
        return None

    async def settled_lookup(*_args: object, **kwargs: object) -> CostObservation:
        lookups.append(str(kwargs["generation_id"]))
        return CostObservation(
            status="reported", actual_cost_micros=0, components={"generationId": "gen-1"}
        )

    monkeypatch.setattr(models.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(models.ledger, "find_recoverable_attempt", no_recovery)
    monkeypatch.setattr(models.ledger, "reserve_attempt", reserve)
    monkeypatch.setattr(models.ledger, "mark_dispatched", dispatched)
    monkeypatch.setattr(models.ledger, "fail_attempt", fail_attempt)
    monkeypatch.setattr(models.ledger, "attach_remote_handle", attach)
    monkeypatch.setattr(models.artifacts, "find_artifact", no_recovery)
    monkeypatch.setattr(models, "observe_generation_cost", settled_lookup)

    def lost_stream(route: RouteEntry) -> FunctionModel:  # noqa: ARG001
        async def fail_after_open(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            await gateway.notify_observed_generation("gen-1")
            raise ModelAPIError(model_name="synthetic/model", message="upstream rate limit")

        return FunctionModel(fail_after_open, model_name="synthetic/model")

    deps = HarnessModelDeps(
        scope=Scope(
            organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
            userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
        ),
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage="verify:selection:source:0",
        program_version="standalone-topics/3",
        prompt_version="standalone-topic-selection-source-v3",
        schema_version="topic-selection-portfolio/4",
        route=route(),
        operation_inputs={},
        operation_config={},
        dispatch_limit=8,
    )
    models.configure_model_runtime(
        models.ModelRuntime(
            database_url="unused",
            store=cast("Any", None),
            cassette_store=CassetteStore(tmp_path),
            gateway=GatewayConfig(api_key="test-key"),
            model_factory=lost_stream,
            allow_outside_activity=True,
        )
    )
    try:
        budgeted = models.BudgetedModel(models.LazyConfiguredModel(deps), deps)
        agent = Agent(budgeted, output_type=NativeOutput(Answer, strict=True), retries=0)
        with pytest.raises(models.TransientProviderFailure) as raised:
            await agent.run("Ask")
    finally:
        models.clear_model_runtime()
    assert lookups == ["gen-1"]
    assert str(raised.value) == (
        "Route fixture-route: the verify:selection:source:0 request ended without a "
        "response; its charge of 0 micros is settled and a fresh attempt is allowed."
    )
    assert failures[0]["outcome_known"] is True
    assert failures[0]["actual_cost_micros"] == 0
    assert failures[0]["error_code"] == "provider-stream-failure"


async def test_lost_stream_with_a_pending_receipt_stays_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a settled charge the fence is unchanged: unknown, never retried."""
    failures: list[dict[str, object]] = []

    async def acquire(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            accepted=False, operation=SimpleNamespace(id=uuid4(), result_artifact_id=None)
        )

    async def no_recovery(*_args: object, **_kwargs: object) -> None:
        return None

    async def reserve(*_args: object, **kwargs: object) -> ledger.Attempt:
        return ledger.Attempt(
            id=uuid4(),
            operation_id=uuid4(),
            run_id=None,
            attempt_number=1,
            owner_token=str(kwargs["owner_token"]),
            state=ledger.AttemptState.RESERVED,
            estimated_cost_micros=0,
            actual_cost_micros=None,
            cost_status="unknown",
            remote_handle=None,
            dispatched_at=None,
            finished_at=None,
        )

    async def dispatched(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fail_attempt(*_args: object, **kwargs: object) -> None:
        failures.append(dict(kwargs))

    async def attach(*_args: object, **_kwargs: object) -> None:
        return None

    async def pending_lookup(*_args: object, **_kwargs: object) -> CostObservation:
        return CostObservation(status="pending", actual_cost_micros=None, components={})

    monkeypatch.setattr(models.ledger, "acquire_operation", acquire)
    monkeypatch.setattr(models.ledger, "find_recoverable_attempt", no_recovery)
    monkeypatch.setattr(models.ledger, "reserve_attempt", reserve)
    monkeypatch.setattr(models.ledger, "mark_dispatched", dispatched)
    monkeypatch.setattr(models.ledger, "fail_attempt", fail_attempt)
    monkeypatch.setattr(models.ledger, "attach_remote_handle", attach)
    monkeypatch.setattr(models.artifacts, "find_artifact", no_recovery)
    monkeypatch.setattr(models, "observe_generation_cost", pending_lookup)

    def lost_stream(route: RouteEntry) -> FunctionModel:  # noqa: ARG001
        async def fail_after_open(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            await gateway.notify_observed_generation("gen-2")
            raise ModelAPIError(model_name="synthetic/model", message="stream ended")

        return FunctionModel(fail_after_open, model_name="synthetic/model")

    deps = HarnessModelDeps(
        scope=Scope(
            organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
            userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
        ),
        source_id=UUID("0192e8a0-0000-7000-8000-000000000111"),
        run_id=UUID("0192e8a0-0000-7000-8000-000000000222"),
        stage="verify:selection:source:0",
        program_version="standalone-topics/3",
        prompt_version="standalone-topic-selection-source-v3",
        schema_version="topic-selection-portfolio/4",
        route=route(),
        operation_inputs={},
        operation_config={},
        dispatch_limit=8,
    )
    models.configure_model_runtime(
        models.ModelRuntime(
            database_url="unused",
            store=cast("Any", None),
            cassette_store=CassetteStore(tmp_path),
            gateway=GatewayConfig(api_key="test-key"),
            model_factory=lost_stream,
            allow_outside_activity=True,
        )
    )
    try:
        budgeted = models.BudgetedModel(models.LazyConfiguredModel(deps), deps)
        agent = Agent(budgeted, output_type=NativeOutput(Answer, strict=True), retries=0)
        with pytest.raises(ledger.OutcomeUnknown):
            await agent.run("Ask")
    finally:
        models.clear_model_runtime()
    assert failures[0]["outcome_known"] is False
    assert failures[0]["error_code"] == "transport-ambiguous"
