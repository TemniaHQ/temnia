"""Replay-safe PydanticAI agents with the durable Temnia accounting boundary."""

# Public refusal messages are intentionally defined at the state transition
# that produces them, and the fixed domain exception names omit Error.
# ruff: noqa: EM101, N815, N818, TC002, TC003, TRY003

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_ai import Agent, ModelResponse, NativeOutput, RunContext
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, TemporalDurability
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import (
    Model,
    ModelRequestContext,
    ModelRequestParameters,
    ModelResolutionContext,
    StreamedResponse,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from temporalio import activity
from temporalio.common import RetryPolicy

from temnia_pipeline.contracts import ChapterProposal, Scope
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.cassettes import (
    MODEL_RESPONSE_ADAPTER,
    CassetteMetadata,
    CassetteMode,
    CassetteModel,
    CassetteStore,
    request_fingerprint,
    synthetic_function_model,
)
from temnia_pipeline.harness.gateway import (
    CostObservation,
    GatewayConfig,
    GatewayError,
    build_gateway_model,
    lookup_generation,
)
from temnia_pipeline.harness.routes import RouteEntry, estimate_cost

if TYPE_CHECKING:
    from obstore.store import S3Store

MODEL_ALIAS = "temnia:configured"
MODEL_ACTIVITY_NAME = "temnia_harness_model_request_v1"
MODEL_RESPONSE_SCHEMA_VERSION = "pydantic-ai-model-response-v1"
HTTP_CLIENT_ERROR_MIN = 400
HTTP_CLIENT_ERROR_MAX = 500
HTTP_REQUEST_TIMEOUT = 408


class ModelPersistenceError(RuntimeError):
    """A response could not cross the durable persistence boundary."""


class KnownProviderRejection(RuntimeError):
    """The provider conclusively rejected the one physical request."""


class SummaryUnit(BaseModel):
    """One ordered summary unit grounded in original source sentence IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Annotated[str, Field(min_length=1, max_length=256)]
    firstSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    lastSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    quoteWordIds: list[Annotated[str, Field(min_length=1, max_length=256)]]
    text: Annotated[str, Field(min_length=1, max_length=20_000)]


class HierarchicalSummaryV1(BaseModel):
    """A bounded hierarchy whose endpoints remain in original evidence IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    units: Annotated[list[SummaryUnit], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _unique_lineage(self) -> HierarchicalSummaryV1:
        if len({unit.id for unit in self.units}) != len(self.units):
            raise ValueError("summary unit IDs must be unique")
        return self


class EditorialVerdictV1(BaseModel):
    """Text/evidence verdict that cannot claim audiovisual inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    status: Literal["passed", "needs_review", "failed"]
    reasons: Annotated[list[str], Field(max_length=100)]
    inspectedModalities: Literal["text_evidence_and_technical_report"]


class HarnessModelDeps(BaseModel):
    """Frozen serializable call facts safe to place in Temporal history."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: Scope
    source_id: UUID
    run_id: UUID
    stage: Annotated[str, Field(min_length=1, max_length=128)]
    program_version: Annotated[str, Field(min_length=1, max_length=128)]
    prompt_version: Annotated[str, Field(min_length=1, max_length=128)]
    schema_version: Annotated[str, Field(min_length=1, max_length=128)]
    route: RouteEntry
    operation_inputs: dict[str, Any]
    operation_config: dict[str, Any]
    input_artifact_ids: tuple[UUID, ...] = ()
    dispatch_limit: Annotated[int, Field(gt=0, le=128)]
    cassette_mode: CassetteMode = CassetteMode.OFF
    synthetic_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _synthetic_route(self) -> HarnessModelDeps:
        if self.synthetic_payload is not None:
            if self.synthetic_payload.get("synthetic") is not True:
                raise ValueError("synthetic payload requires an explicit synthetic=true marker")
            if not self.route.id.startswith("synthetic-"):
                raise ValueError("synthetic payload requires an obvious synthetic route ID")
        return self


class ModelFactory(Protocol):
    """Injected test/backend construction without capturing it in workflow history."""

    def __call__(self, route: RouteEntry) -> Model:
        """Construct a model for one frozen route."""
        ...


FailureHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ModelRuntime:
    """Process-only activity resources; never serialized in harness dependencies."""

    database_url: str
    store: S3Store
    cassette_store: CassetteStore
    gateway: GatewayConfig | None = None
    model_factory: ModelFactory | None = None
    lookup_client: httpx.AsyncClient | None = None
    failure_hook: FailureHook | None = None
    allow_outside_activity: bool = False
    allow_synthetic: bool = False


_runtime: ModelRuntime | None = None


def configure_model_runtime(runtime: ModelRuntime) -> None:
    """Install process-only activity resources during worker boot."""
    global _runtime  # noqa: PLW0603
    _runtime = runtime


def clear_model_runtime() -> None:
    """Remove process-only resources after a test worker closes."""
    global _runtime  # noqa: PLW0603
    _runtime = None


def _configured_runtime() -> ModelRuntime:
    if _runtime is None:
        raise ModelPersistenceError("harness model runtime was not configured at worker boot")
    return _runtime


async def _unreachable_model(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:  # pragma: no cover - only a lazy profile carrier
    _ = messages, info
    raise ModelPersistenceError("lazy harness model reached its placeholder backend")


class LazyConfiguredModel(WrapperModel):
    """Carry only a frozen route until request-time process configuration is available."""

    def __init__(self, deps: HarnessModelDeps) -> None:
        super().__init__(FunctionModel(_unreachable_model, model_name=f"temnia:{deps.route.id}"))
        self.deps = deps
        self._prepared: Model | None = None

    def build(self, runtime: ModelRuntime) -> Model:
        """Construct the real or explicitly injected backend inside the activity."""
        if self._prepared is not None:
            return self._prepared
        if self.deps.synthetic_payload is not None:
            if not runtime.allow_synthetic or not runtime.cassette_store.allow_synthetic:
                raise ModelPersistenceError(
                    "synthetic model execution requires explicit worker enablement"
                )
            model = synthetic_function_model(self.deps.synthetic_payload)
        elif runtime.model_factory is not None:
            model = runtime.model_factory(self.deps.route)
        elif runtime.gateway is None:
            raise ModelPersistenceError(
                "gateway configuration is required for a live harness route"
            )
        else:
            model = build_gateway_model(self.deps.route, runtime.gateway)
        self._prepared = model
        return model

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Construct and close the SDK-backed model within the one request activity."""
        model = self.build(_configured_runtime())
        async with model:
            return await model.request(messages, model_settings, model_request_parameters)


def _cassette_metadata(deps: HarnessModelDeps) -> CassetteMetadata:
    return CassetteMetadata(
        route_id=deps.route.id,
        stage=deps.stage,
        schema_version=deps.schema_version,
        prompt_version=deps.prompt_version,
        program_version=deps.program_version,
        synthetic=deps.synthetic_payload is not None,
    )


def _usage(response: ModelResponse, observation: CostObservation | None) -> dict[str, Any]:
    value = TypeAdapter(type(response.usage)).dump_python(response.usage, mode="json")
    usage = cast("dict[str, Any]", value)
    if observation is not None:
        usage["gateway"] = observation.model_dump(mode="json")
    return usage


def _response_from_artifact(value: object) -> ModelResponse:
    if not isinstance(value, dict):
        raise ModelPersistenceError("accepted model response artifact is not a JSON object")
    return MODEL_RESPONSE_ADAPTER.validate_python(value)


async def _heartbeat(attempt_id: UUID) -> None:
    while True:
        await asyncio.sleep(10)
        activity.heartbeat({"attemptId": str(attempt_id)})


async def _observe_cost(
    runtime: ModelRuntime, deps: HarnessModelDeps, response: ModelResponse
) -> CostObservation | None:
    if deps.synthetic_payload is not None or deps.cassette_mode == CassetteMode.REPLAY:
        return CostObservation(status="reported", actual_cost_micros=0, components={})
    if response.provider_response_id is None or runtime.gateway is None:
        return None
    if runtime.lookup_client is not None:
        return await lookup_generation(
            runtime.lookup_client,
            config=runtime.gateway,
            route=deps.route,
            generation_id=response.provider_response_id,
        )
    async with httpx.AsyncClient() as client:
        return await lookup_generation(
            client,
            config=runtime.gateway,
            route=deps.route,
            generation_id=response.provider_response_id,
        )


def _owner_token(runtime: ModelRuntime) -> str:
    if activity.in_activity():
        info = activity.info()
        return hashlib.sha256(f"{info.workflow_run_id}:{info.activity_id}".encode()).hexdigest()
    if runtime.allow_outside_activity:
        return "test:outside-activity"
    raise ModelPersistenceError("harness model request must execute inside a Temporal activity")


def _validate_request(parameters: ModelRequestParameters) -> None:
    if parameters.function_tools or parameters.native_tools or parameters.output_tools:
        raise ModelPersistenceError("tools are disabled on the initial harness path")
    if parameters.allow_image_output:
        raise ModelPersistenceError("image output is disabled on the initial harness path")
    if parameters.output_mode != "native" or parameters.output_object is None:
        raise ModelPersistenceError("harness model calls require native JSON schema output")
    if parameters.output_object.strict is not True:
        raise ModelPersistenceError("harness model calls require a strict output schema")


def _validate_route_settings(deps: HarnessModelDeps, settings: ModelSettings | None) -> None:
    values = dict(settings or {})
    forbidden = {"extra_body", "openai_store", "parallel_tool_calls"} & values.keys()
    if forbidden:
        raise ModelPersistenceError("model settings cannot override gateway privacy policy")
    requested_max = values.get("max_tokens")
    if requested_max is not None and (
        not isinstance(requested_max, int) or requested_max > deps.route.max_output_tokens
    ):
        raise ModelPersistenceError("model max_tokens exceeds the qualified route")
    if deps.route.cache_enabled:
        raise ModelPersistenceError(
            "cache transport remains disabled until its request shape is probed"
        )


class BudgetedModel(WrapperModel):
    """The only path across reserve, dispatch, response publication, and settlement."""

    def __init__(self, lazy: LazyConfiguredModel, deps: HarnessModelDeps) -> None:
        runtime = _runtime
        cassette_store = (
            runtime.cassette_store
            if runtime is not None
            else CassetteStore(Path(".temnia-unconfigured-cassettes"))
        )
        wrapped: Model = lazy
        if deps.cassette_mode == CassetteMode.REPLAY:
            wrapped = CassetteModel(
                lazy,
                store=cassette_store,
                mode=deps.cassette_mode,
                metadata=_cassette_metadata(deps),
            )
        super().__init__(wrapped)
        self.lazy = lazy
        self.deps = deps

    async def _record_unknown(  # noqa: PLR0913
        self,
        *,
        runtime: ModelRuntime,
        operation_id: UUID,
        attempt: ledger.Attempt,
        owner_token: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """Persist the post-dispatch fence even while the caller is cancelled."""
        cleanup = asyncio.create_task(
            ledger.fail_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=operation_id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                outcome_known=False,
                actual_cost_micros=None,
                usage={},
                error_code=error_code,
                error_message=error_message,
            )
        )
        try:
            try:
                await asyncio.shield(cleanup)
            except ledger.LostOwnership:
                return
        except asyncio.CancelledError:
            try:
                await cleanup
            except ledger.LostOwnership:
                return

    async def request(  # noqa: C901, PLR0912, PLR0915
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Make at most one physical request after the committed dispatch CAS."""
        runtime = _configured_runtime()
        _validate_request(model_request_parameters)
        _validate_route_settings(self.deps, model_settings)
        request_hash, payload_bytes = request_fingerprint(
            messages,
            model_settings,
            model_request_parameters,
            _cassette_metadata(self.deps),
        )
        requested_max = (model_settings or {}).get("max_tokens")
        estimated = estimate_cost(
            self.deps.route,
            payload_bytes=payload_bytes,
            max_output_tokens=requested_max,
        )
        if (
            self.deps.synthetic_payload is not None
            or self.deps.cassette_mode == CassetteMode.REPLAY
        ):
            estimated_micros = 0
        else:
            estimated_micros = estimated.amount_micros
        acquired = await ledger.acquire_operation(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            run_id=self.deps.run_id,
            kind=ledger.OperationKind.MODEL,
            stage=self.deps.stage,
            inputs={**self.deps.operation_inputs, "requestHash": request_hash},
            config={
                **self.deps.operation_config,
                "programVersion": self.deps.program_version,
                "promptVersion": self.deps.prompt_version,
                "route": self.deps.route.model_dump(mode="json"),
                "schemaVersion": self.deps.schema_version,
            },
        )
        if acquired.accepted:
            artifact_id = acquired.operation.result_artifact_id
            if artifact_id is None:
                raise ModelPersistenceError("successful operation has no response artifact")
            stored = await artifacts.read_artifact_json(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                store=runtime.store,
                artifact_id=artifact_id,
            )
            return _response_from_artifact(stored)
        owner_token = _owner_token(runtime)
        recovery_attempt = await ledger.find_recoverable_attempt(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            run_id=self.deps.run_id,
            operation_id=acquired.operation.id,
            provider=self.deps.route.provider,
            model=self.deps.route.gateway_model,
            family=self.deps.route.family,
            route=self.deps.route.model_dump(mode="json"),
            request_hash=request_hash,
            estimated_cost_micros=estimated_micros,
        )
        attempt = recovery_attempt
        if attempt is None:
            attempt = await ledger.reserve_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                owner_token=owner_token,
                provider=self.deps.route.provider,
                model=self.deps.route.gateway_model,
                family=self.deps.route.family,
                route=self.deps.route.model_dump(mode="json"),
                request_hash=request_hash,
                estimated_cost_micros=estimated_micros,
                dispatch_limit=self.deps.dispatch_limit,
            )
        response_fingerprint = artifacts.fingerprint_for(
            kind="model_response",
            inputs={"attemptId": str(attempt.id), "requestHash": request_hash},
            config={
                "operationId": str(acquired.operation.id),
                "route": self.deps.route.model_dump(mode="json"),
                "runId": str(self.deps.run_id),
            },
        )
        persisted = await artifacts.find_artifact(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            identity=artifacts.ArtifactIdentity(
                kind="model_response", fingerprint=response_fingerprint
            ),
        )
        if persisted is not None:
            stored = await artifacts.read_artifact_json(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                store=runtime.store,
                artifact_id=persisted.id,
            )
            response = _response_from_artifact(stored)
            try:
                observation = await _observe_cost(runtime, self.deps, response)
            except (GatewayError, httpx.HTTPError, ValueError):
                observation = None
            await ledger.complete_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=attempt.owner_token,
                result_artifact_id=persisted.id,
                usage=_usage(response, observation),
                actual_cost_micros=(
                    observation.actual_cost_micros if observation is not None else None
                ),
            )
            if self.deps.cassette_mode == CassetteMode.RECORD:
                runtime.cassette_store.record(request_hash, _cassette_metadata(self.deps), response)
            return response
        if recovery_attempt is not None:
            raise ledger.OutcomeUnknown(
                "a prior dispatched attempt has no durable response and cannot be repeated"
            )
        try:
            if self.deps.cassette_mode != CassetteMode.REPLAY:
                self.lazy.build(runtime)
        except Exception:
            await ledger.release_undispatched(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner_token,
            )
            raise
        try:
            dispatched = await ledger.mark_dispatched(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                dispatch_limit=self.deps.dispatch_limit,
            )
        except (
            ledger.BudgetExceeded,
            ledger.DispatchLimitExceeded,
            ledger.IdentityConflict,
            ledger.OutcomeUnknown,
        ):
            if attempt.state == ledger.AttemptState.RESERVED:
                await ledger.release_undispatched(
                    runtime.database_url,
                    scope=self.deps.scope,
                    source_id=self.deps.source_id,
                    run_id=self.deps.run_id,
                    operation_id=acquired.operation.id,
                    attempt_id=attempt.id,
                    owner_token=owner_token,
                )
            raise
        if not dispatched:
            await ledger.fail_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                outcome_known=False,
                actual_cost_micros=None,
                usage={},
                error_code="activity-retry-after-dispatch",
                error_message="activity execution resumed after the dispatch commit",
            )
            raise ledger.OutcomeUnknown("prior activity execution may have reached the provider")
        pulse = asyncio.create_task(_heartbeat(attempt.id)) if activity.in_activity() else None
        if pulse is not None:
            current = asyncio.current_task()
            if current is not None:
                current.add_done_callback(lambda _: pulse.cancel())
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except ModelHTTPError as error:
            conclusive = (
                HTTP_CLIENT_ERROR_MIN <= error.status_code < HTTP_CLIENT_ERROR_MAX
                and error.status_code != HTTP_REQUEST_TIMEOUT
            )
            await ledger.fail_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                outcome_known=conclusive,
                actual_cost_micros=None,
                usage={},
                error_code=f"http-{error.status_code}",
                error_message="provider request ended with an HTTP error",
            )
            if conclusive:
                raise KnownProviderRejection("provider rejected the model request") from error
            raise ledger.OutcomeUnknown("provider outcome is unknown") from error
        except asyncio.CancelledError:
            await self._record_unknown(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                error_code="transport-cancelled",
                error_message="provider transport was cancelled without a conclusive outcome",
            )
            raise
        except (ModelAPIError, httpx.TimeoutException) as error:
            await self._record_unknown(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                error_code="transport-ambiguous",
                error_message="provider transport ended without a conclusive outcome",
            )
            raise ledger.OutcomeUnknown("provider outcome is unknown") from error
        except Exception as error:
            await self._record_unknown(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                error_code="unexpected-after-dispatch",
                error_message="unexpected failure after the dispatch commit",
            )
            raise ledger.OutcomeUnknown("provider outcome is unknown") from error
        try:
            return await self._accept_response(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                request_hash=request_hash,
                response_fingerprint=response_fingerprint,
                response=response,
            )
        except asyncio.CancelledError:
            await self._record_unknown(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                error_code="cancelled-during-response-acceptance",
                error_message="response acceptance was interrupted after provider dispatch",
            )
            raise
        except Exception as error:
            await self._record_unknown(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                error_code="response-persistence-ambiguous",
                error_message="response acceptance did not reach a durable terminal state",
            )
            raise ledger.OutcomeUnknown("response persistence outcome is unknown") from error

    async def _accept_response(  # noqa: PLR0913
        self,
        *,
        runtime: ModelRuntime,
        operation_id: UUID,
        attempt: ledger.Attempt,
        owner_token: str,
        request_hash: str,
        response_fingerprint: str,
        response: ModelResponse,
    ) -> ModelResponse:
        """Persist the paid response before optional recording and settlement."""
        if response.provider_response_id is not None:
            await ledger.attach_remote_handle(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                operation_id=operation_id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                remote_handle=response.provider_response_id,
            )
        if runtime.failure_hook is not None:
            await runtime.failure_hook("after_response_before_publication")
        response_body = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True)
        published = await artifacts.publish_json(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            store=runtime.store,
            identity=artifacts.ArtifactIdentity(
                kind="model_response", fingerprint=response_fingerprint
            ),
            content=response_body,
            metadata={
                "attemptId": str(attempt.id),
                "cassetteMode": str(self.deps.cassette_mode),
                "operationId": str(operation_id),
                "programVersion": self.deps.program_version,
                "promptVersion": self.deps.prompt_version,
                "requestHash": request_hash,
                "route": self.deps.route.model_dump(mode="json"),
                "routeId": self.deps.route.id,
                "runId": str(self.deps.run_id),
                "schemaVersion": self.deps.schema_version,
                "synthetic": self.deps.synthetic_payload is not None,
            },
            dependency_ids=self.deps.input_artifact_ids,
        )
        if self.deps.cassette_mode == CassetteMode.RECORD:
            runtime.cassette_store.record(request_hash, _cassette_metadata(self.deps), response)
        if runtime.failure_hook is not None:
            await runtime.failure_hook("after_publication_before_settlement")
        try:
            observation = await _observe_cost(runtime, self.deps, response)
        except (GatewayError, httpx.HTTPError, ValueError):
            observation = None
        await ledger.complete_attempt(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            run_id=self.deps.run_id,
            operation_id=operation_id,
            attempt_id=attempt.id,
            owner_token=owner_token,
            result_artifact_id=published.id,
            usage=_usage(response, observation),
            actual_cost_micros=(
                observation.actual_cost_micros if observation is not None else None
            ),
        )
        return response

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        """Forbid token-count calls; the byte estimator is the budget boundary."""
        _ = messages, model_settings, model_request_parameters
        raise ModelPersistenceError("model token counting is disabled")

    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        """Forbid provider compaction; the workflow owns finite summary stages."""
        _ = request_context, instructions
        raise ModelPersistenceError("provider message compaction is disabled")

    @contextlib.asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Forbid streaming until its full lifecycle is durably wrapped."""
        _ = messages, model_settings, model_request_parameters, run_context
        raise ModelPersistenceError("streaming is disabled")
        yield  # pragma: no cover


def resolve_configured_model(
    context: ModelResolutionContext[HarnessModelDeps], model_id: str
) -> Model | None:
    """Pure replay-safe resolver: no environment, database, SDK, or I/O access."""
    if model_id != MODEL_ALIAS:
        return None
    lazy = LazyConfiguredModel(context.deps)
    return BudgetedModel(lazy, context.deps)


def _agent(name: str, output_type: type[Any]) -> Agent[HarnessModelDeps, Any]:
    return Agent(
        model=MODEL_ALIAS,
        defer_model_check=True,
        output_type=NativeOutput(output_type, strict=True),
        deps_type=HarnessModelDeps,
        name=name,
        retries=0,
        capabilities=[
            ResolveModelId(resolve_configured_model),
            TemporalDurability(
                model_activity_config={
                    "start_to_close_timeout": timedelta(minutes=10),
                    "heartbeat_timeout": timedelta(seconds=30),
                    "retry_policy": RetryPolicy(maximum_attempts=1),
                }
            ),
        ],
    )


chapter_propose_v1 = _agent("chapter_propose_v1", ChapterProposal)
chapter_verify_v1 = _agent("chapter_verify_v1", EditorialVerdictV1)
chapter_summarize_v1 = _agent("chapter_summarize_v1", HierarchicalSummaryV1)
HARNESS_AGENTS: tuple[Agent[HarnessModelDeps, Any], ...] = (
    chapter_propose_v1,
    chapter_verify_v1,
    chapter_summarize_v1,
)


def harness_pydantic_ai_plugin() -> PydanticAIPlugin:
    """Return the worker/client plugin paired with ``HARNESS_AGENTS`` workflow registration."""
    return PydanticAIPlugin()
