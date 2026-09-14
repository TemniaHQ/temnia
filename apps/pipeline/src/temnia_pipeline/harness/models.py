"""Replay-safe PydanticAI agents with the durable Temnia accounting boundary."""

# Public refusal messages are intentionally defined at the state transition
# that produces them, and the fixed domain exception names omit Error.
# ruff: noqa: EM101, N818, SLF001, TC001, TC002, TC003, TRY003

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_ai import Agent, ModelResponse, NativeOutput, RunContext
from pydantic_ai.capabilities import ProcessHistory, ResolveModelId
from pydantic_ai.durable_exec import DurableOperationBackend

# The per-call activity deadline (D6) has no public seam: the durable runtime binds one
# `ActivityConfig` per agent at construction, before any payload exists. The bound model
# request operation is the one place that sees both that config and the call's own
# messages, so `PayloadScaledDurability` below subclasses these three.
from pydantic_ai.durable_exec._base import (
    _BoundModelOperations,  # pyright: ignore[reportPrivateUsage]
)
from pydantic_ai.durable_exec._operation import ModelRequestParams
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, TemporalDurability
from pydantic_ai.durable_exec.temporal._operation_backend import TemporalBoundOperation
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
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

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
    TopicCandidateInspectionPage,
    TopicMediaEvidencePage,
    TopicPortfolioReviewV4,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSourceBrowsePage,
    TopicSourceIndex,
    TopicSourceReadPage,
    TopicSourceSearchPage,
)
from temnia_pipeline.harness import artifacts, ledger
from temnia_pipeline.harness.cassettes import (
    MODEL_RESPONSE_ADAPTER,
    CassetteMetadata,
    CassetteMode,
    CassetteModel,
    CassetteStore,
    request_fingerprint,
    request_payload_bytes,
    synthetic_function_model,
)
from temnia_pipeline.harness.editorial_evidence import (
    inspect_topic_candidate,
    read_topic_media_evidence,
)
from temnia_pipeline.harness.gateway import (
    CostObservation,
    GatewayConfig,
    GatewayError,
    build_gateway_model,
    dispatch_payload_bytes,
    observe_gateway_generation,
    observe_generation_cost,
)
from temnia_pipeline.harness.gateway_policy import model_activity_timeout_seconds
from temnia_pipeline.harness.routes import RouteEntry, estimate_cost
from temnia_pipeline.harness.source_index import (
    browse_topic_source,
    load_topic_source_encoder,
    read_topic_source,
    search_topic_source,
)
from temnia_pipeline.harness.source_progress import (
    CHECKPOINT_FORMAT,
    checkpoint_from_messages,
    checkpoint_sha256,
    compact_source_history,
)
from temnia_pipeline.harness.topic_selection_runtime import SourceToolRole

if TYPE_CHECKING:
    from obstore.store import S3Store

MODEL_ALIAS = "temnia:configured"
MODEL_ACTIVITY_NAME = "temnia_harness_model_request_v1"
MODEL_RESPONSE_SCHEMA_VERSION = "pydantic-ai-model-response-v1"
HTTP_CLIENT_ERROR_MIN = 400
HTTP_CLIENT_ERROR_MAX = 500
HTTP_SERVER_ERROR_MIN = 500
# Statuses a gateway returns before it has processed the request: throttling, an early
# timeout, an upstream outage. No generation exists, so nothing was charged, and the same
# request may be sent again (after backoff, then on the next qualified route).
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429})
MAX_SUMMARY_ID_LENGTH = 256
SUMMARY_SCHEMA_VERSION = "hierarchical-summary/1"
TOPIC_ID_NORMALIZATION_VERSION = "initial-topic-identifiers/1"


class ModelPersistenceError(RuntimeError):
    """A response could not cross the durable persistence boundary."""


class KnownProviderRejection(RuntimeError):
    """The provider conclusively rejected the one physical request."""


class TransientProviderFailure(RuntimeError):
    """A dispatched request ended without a response, but its charge is settled.

    The gateway receipt for the observed generation reported a known cost, so the
    outcome is not ambiguous: the money is accounted and there is no response to
    recover. Unlike an unknown outcome, a fresh attempt is a new paid call, not a
    duplicate of an unresolved one, so the workflow may retry a bounded number of
    times. An upstream rate limit delivered inside a successful HTTP stream is the
    case this exists for.
    """


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
    source_index: HarnessArtifactRef | None = None
    source_tool_role: SourceToolRole | None = None
    candidate_selection: HarnessArtifactRef | None = None
    media_evidence: HarnessArtifactRef | None = None
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
        if (self.source_index is None) != (self.source_tool_role is None):
            raise ValueError("source tools require both an index identity and an editorial role")
        if self.source_index is not None and self.source_index.id not in self.input_artifact_ids:
            raise ValueError("source index must be one of the model call's immutable inputs")
        reviewer_refs = (self.candidate_selection, self.media_evidence)
        if self.source_tool_role == "source_reviewer":
            if any(item is None for item in reviewer_refs):
                raise ValueError("source reviewer tools require selection and evidence authority")
            if any(
                item is not None and item.id not in self.input_artifact_ids
                for item in reviewer_refs
            ):
                raise ValueError("reviewer tool authority must be an immutable model input")
        elif any(item is not None for item in reviewer_refs):
            raise ValueError("candidate and media tools are restricted to source review")
        return self


class ModelFactory(Protocol):
    """Injected test/backend construction without capturing it in workflow history."""

    def __call__(self, route: RouteEntry) -> Model:
        """Construct a model for one frozen route."""
        ...


FailureHook = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class _GateState:
    slots: asyncio.Semaphore
    pace: asyncio.Lock
    last_dispatch: float | None = None


class RouteGate:
    """One route's admission in this worker: bounded in-flight calls, spaced dispatches.

    Providers throttle per account, not per run, so the bound lives in the process that
    holds the key. Callers wait here before a dispatch is committed, so a queued request
    never counts as sent.
    """

    def __init__(self, *, max_in_flight: int, min_interval_seconds: float) -> None:
        if max_in_flight < 1 or min_interval_seconds < 0:
            raise ValueError("route gate needs a positive slot count and a nonnegative interval")
        self.max_in_flight = max_in_flight
        self.min_interval_seconds = min_interval_seconds
        # asyncio primitives bind to the loop that first waits on them; a runtime can
        # outlive a loop (tests, tools), so the state is kept per running loop.
        self._states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _GateState] = (
            weakref.WeakKeyDictionary()
        )

    def _state(self) -> _GateState:
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _GateState(slots=asyncio.Semaphore(self.max_in_flight), pace=asyncio.Lock())
            self._states[loop] = state
        return state

    async def acquire(self) -> None:
        """Take a slot, then wait out the spacing since the previous dispatch."""
        state = self._state()
        await state.slots.acquire()
        try:
            async with state.pace:
                loop = asyncio.get_running_loop()
                if state.last_dispatch is not None:
                    wait = state.last_dispatch + self.min_interval_seconds - loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                state.last_dispatch = loop.time()
        except BaseException:
            state.slots.release()
            raise

    def release(self) -> None:
        """Free the slot once the provider has answered or refused."""
        self._state().slots.release()


# The runtime's own defaults admit freely; the worker installs the deployment file's
# limits at boot. Tests and tools that build a runtime directly are not paced.
DEFAULT_MAX_IN_FLIGHT_PER_ROUTE = 8
DEFAULT_MIN_DISPATCH_INTERVAL_SECONDS = 0.0


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
    max_in_flight_per_route: int = DEFAULT_MAX_IN_FLIGHT_PER_ROUTE
    min_dispatch_interval_seconds: float = DEFAULT_MIN_DISPATCH_INTERVAL_SECONDS
    route_gates: dict[str, RouteGate] = field(default_factory=dict[str, RouteGate])

    def route_gate(self, route_id: str) -> RouteGate:
        """The gate for one route, created on first use with this worker's limits."""
        gate = self.route_gates.get(route_id)
        if gate is None:
            gate = RouteGate(
                max_in_flight=self.max_in_flight_per_route,
                min_interval_seconds=self.min_dispatch_interval_seconds,
            )
            self.route_gates[route_id] = gate
        return gate


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


async def _indexed_source(deps: HarnessModelDeps) -> tuple[TopicSourceIndex, str]:
    """Load the exact scoped index made available to this one editorial call."""
    reference = deps.source_index
    if reference is None or deps.source_tool_role is None:
        raise ModelPersistenceError("this model call has no source-index authority")
    runtime = _configured_runtime()
    accepted = await artifacts._artifact_for_read(  # pyright: ignore[reportPrivateUsage]
        runtime.database_url,
        scope=deps.scope,
        source_id=deps.source_id,
        artifact_id=reference.id,
    )
    if (
        accepted.id != reference.id
        or accepted.kind != reference.kind.value
        or accepted.fingerprint != reference.fingerprint
        or accepted.sha256 != reference.sha256
        or accepted.size_bytes != reference.sizeBytes
        or accepted.storage_key != reference.storageKey
        or accepted.metadata.get("format") != "topic-source-index/2"
    ):
        raise ModelPersistenceError("source-index identity differs from the accepted artifact")
    value = await artifacts.read_artifact_json(
        runtime.database_url,
        scope=deps.scope,
        source_id=deps.source_id,
        store=runtime.store,
        artifact_id=reference.id,
    )
    if hashlib.sha256(artifacts.canonical_json(value)).hexdigest() != reference.sha256:
        raise ModelPersistenceError("source-index bytes differ from their accepted hash")
    index = TopicSourceIndex.model_validate(value)
    if index.sourceId != deps.source_id:
        raise ModelPersistenceError("source index belongs to another source")
    return index, reference.sha256


async def _reviewer_inputs(
    deps: HarnessModelDeps,
) -> tuple[TopicSelectionRecord, HarnessEvidence]:
    """Load the exact accepted selection and measured evidence for source-review tools."""
    if deps.source_tool_role != "source_reviewer":
        raise ModelPersistenceError("candidate and media tools are restricted to source review")
    selection_ref = deps.candidate_selection
    evidence_ref = deps.media_evidence
    if selection_ref is None or evidence_ref is None:
        raise ModelPersistenceError("source reviewer tool authority is incomplete")
    runtime = _configured_runtime()

    async def load(reference: HarnessArtifactRef, *, kind: str, format_name: str) -> dict[str, Any]:
        accepted = await artifacts._artifact_for_read(  # pyright: ignore[reportPrivateUsage]
            runtime.database_url,
            scope=deps.scope,
            source_id=deps.source_id,
            artifact_id=reference.id,
        )
        if (
            accepted.id != reference.id
            or accepted.kind != kind
            or accepted.fingerprint != reference.fingerprint
            or accepted.sha256 != reference.sha256
            or accepted.size_bytes != reference.sizeBytes
            or accepted.storage_key != reference.storageKey
            or accepted.metadata.get("format") != format_name
            or accepted.metadata.get("runId") != str(deps.run_id)
        ):
            raise ModelPersistenceError("reviewer tool input differs from its accepted artifact")
        value = await artifacts.read_artifact_json(
            runtime.database_url,
            scope=deps.scope,
            source_id=deps.source_id,
            store=runtime.store,
            artifact_id=reference.id,
        )
        if hashlib.sha256(artifacts.canonical_json(value)).hexdigest() != reference.sha256:
            raise ModelPersistenceError("reviewer tool input bytes differ from their accepted hash")
        return cast("dict[str, Any]", value)

    selection_value, evidence_value = await asyncio.gather(
        load(selection_ref, kind="proposal", format_name="topic-selection/2"),
        load(evidence_ref, kind="evidence", format_name="harness-evidence/1"),
    )
    selection = TopicSelectionRecord.model_validate(selection_value)
    evidence = HarnessEvidence.model_validate(evidence_value)
    if (
        selection.runId != deps.run_id
        or selection.evidenceSha256 != evidence_ref.sha256
        or evidence.sourceId != deps.source_id
    ):
        raise ModelPersistenceError("reviewer tool inputs do not describe the same run and source")
    return selection, evidence


async def browse_source(
    ctx: RunContext[HarnessModelDeps],
    parent_id: str = "episode",
    cursor: int = 0,
    limit: int = 8,
) -> TopicSourceBrowsePage:
    """Browse one episode or section node's children in chronological pages.

    Args:
        ctx: The immutable run and source-index authority.
        parent_id: Episode root or section ID whose direct children should be returned.
        cursor: Zero-based region cursor returned by the prior page.
        limit: Number of regions to return, from 1 through 16.
    """
    index, sha256 = await _indexed_source(ctx.deps)
    return browse_topic_source(
        index, index_sha256=sha256, parent_id=parent_id, cursor=cursor, limit=limit
    )


async def search_source(
    ctx: RunContext[HarnessModelDeps], query: str, cursor: int = 0, limit: int = 6
) -> TopicSourceSearchPage:
    """Search source regions using lexical and pinned semantic retrieval.

    Args:
        ctx: The immutable run and source-index authority.
        query: A concrete topic, claim, person, event, or phrase to retrieve.
        cursor: Zero-based ranked-result cursor returned by the prior page.
        limit: Number of ranked regions to return, from 1 through 12.
    """
    index, sha256 = await _indexed_source(ctx.deps)
    loaded = await asyncio.to_thread(
        load_topic_source_encoder, index.embeddingModel, index.embeddingRevision
    )
    return await asyncio.to_thread(
        search_topic_source,
        index,
        index_sha256=sha256,
        query=query,
        encoder=loaded.value,
        cursor=cursor,
        limit=limit,
    )


async def read_source(
    ctx: RunContext[HarnessModelDeps],
    first_sentence_id: str,
    last_sentence_id: str,
    cursor_sentence_id: str | None = None,
    limit: int = 40,
) -> TopicSourceReadPage:
    """Read exact transcript sentences inside one bounded source range.

    Args:
        ctx: The immutable run and source-index authority.
        first_sentence_id: First allowed sentence in the requested extent.
        last_sentence_id: Last allowed sentence in the requested extent.
        cursor_sentence_id: Continuation sentence returned by the prior page, if any.
        limit: Maximum sentences to return, from 1 through 80.
    """
    index, sha256 = await _indexed_source(ctx.deps)
    return read_topic_source(
        index,
        index_sha256=sha256,
        first_sentence_id=first_sentence_id,
        last_sentence_id=last_sentence_id,
        cursor_sentence_id=cursor_sentence_id,
        limit=limit,
    )


async def inspect_candidate(
    ctx: RunContext[HarnessModelDeps],
    candidate_id: str,
    cursor: int = 0,
    limit: int = 8,
) -> TopicCandidateInspectionPage:
    """Browse the internal index regions intersecting one accepted candidate.

    Args:
        ctx: The immutable run, selection and source-index authority.
        candidate_id: Candidate ID from the accepted selection under review.
        cursor: Zero-based region cursor returned by the prior page.
        limit: Number of intersecting regions to return, from 1 through 16.
    """
    index, index_sha256 = await _indexed_source(ctx.deps)
    selection, evidence = await _reviewer_inputs(ctx.deps)
    evidence_ref = ctx.deps.media_evidence
    if evidence_ref is None:  # pragma: no cover - guarded by dependency validation
        raise ModelPersistenceError("media evidence authority is absent")
    if index.evidenceSha256 != evidence_ref.sha256:
        raise ModelPersistenceError("candidate index and measured evidence identities differ")
    if (
        evidence.transcriptId != index.transcriptId
        or evidence.transcriptRevision != index.transcriptRevision
    ):
        raise ModelPersistenceError("candidate index and measured evidence revisions differ")
    selection_ref = ctx.deps.candidate_selection
    if selection_ref is None:  # pragma: no cover - guarded by dependency validation
        raise ModelPersistenceError("candidate selection authority is absent")
    return inspect_topic_candidate(
        index,
        selection,
        index_sha256=index_sha256,
        selection_sha256=selection_ref.sha256,
        candidate_id=candidate_id,
        cursor=cursor,
        limit=limit,
    )


async def read_media_evidence(
    ctx: RunContext[HarnessModelDeps],
    first_sentence_id: str,
    last_sentence_id: str,
    cursor: int = 0,
    limit: int = 40,
) -> TopicMediaEvidencePage:
    """Read already-measured media sensor events for one exact sentence span.

    Args:
        ctx: The immutable run and evidence authority.
        first_sentence_id: First transcript sentence in the requested span.
        last_sentence_id: Last transcript sentence in the requested span.
        cursor: Zero-based event cursor returned by the prior page.
        limit: Number of chronological events to return, from 1 through 80.
    """
    _, evidence = await _reviewer_inputs(ctx.deps)
    evidence_ref = ctx.deps.media_evidence
    if evidence_ref is None:  # pragma: no cover - guarded by dependency validation
        raise ModelPersistenceError("media evidence authority is absent")
    return read_topic_media_evidence(
        evidence,
        evidence_sha256=evidence_ref.sha256,
        first_sentence_id=first_sentence_id,
        last_sentence_id=last_sentence_id,
        cursor=cursor,
        limit=limit,
    )


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
        transport=deps.route.transport,
        provider_accounting_name=deps.route.provider_accounting_name,
        accounting_model=deps.route.accounting_model,
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


async def _persist_source_checkpoint(
    runtime: ModelRuntime, deps: HarnessModelDeps, messages: list[ModelMessage]
) -> artifacts.HarnessArtifact | None:
    """Publish the compact state before any paid continuation can be dispatched."""
    if deps.source_index is None:
        return None
    checkpoint = checkpoint_from_messages(messages)
    if checkpoint is None:
        raise ModelPersistenceError("indexed model request has no compact source checkpoint")
    if (
        deps.source_tool_role is None
        or checkpoint.index_sha256 != deps.source_index.sha256
        or checkpoint.role != deps.source_tool_role
        or checkpoint.stage != deps.stage
    ):
        raise ModelPersistenceError("indexed model request checkpoint identity changed")
    content = checkpoint.model_dump(mode="json")
    sha256 = checkpoint_sha256(checkpoint)
    fingerprint = _source_checkpoint_fingerprint(deps, sha256)
    parent = None
    if checkpoint.parent_checkpoint_sha256 is not None:
        parent = await artifacts.find_artifact(
            runtime.database_url,
            scope=deps.scope,
            source_id=deps.source_id,
            identity=artifacts.ArtifactIdentity(
                kind="checks",
                fingerprint=_source_checkpoint_fingerprint(
                    deps, checkpoint.parent_checkpoint_sha256
                ),
            ),
        )
        if (
            parent is None
            or parent.metadata.get("format") != CHECKPOINT_FORMAT
            or parent.metadata.get("checkpointSha256") != checkpoint.parent_checkpoint_sha256
            or parent.sha256 != checkpoint.parent_checkpoint_sha256
            or parent.metadata.get("indexSha256") != deps.source_index.sha256
            or parent.metadata.get("role") != deps.source_tool_role
            or parent.metadata.get("runId") != str(deps.run_id)
            or parent.metadata.get("stage") != deps.stage
        ):
            raise ModelPersistenceError("indexed model request checkpoint has no durable parent")
    return await artifacts.publish_json(
        runtime.database_url,
        scope=deps.scope,
        source_id=deps.source_id,
        store=runtime.store,
        identity=artifacts.ArtifactIdentity(kind="checks", fingerprint=fingerprint),
        content=content,
        metadata={
            "checkpointSha256": sha256,
            "format": CHECKPOINT_FORMAT,
            "indexSha256": deps.source_index.sha256,
            "parentCheckpointSha256": checkpoint.parent_checkpoint_sha256,
            "requestSequence": checkpoint.request_sequence,
            "role": deps.source_tool_role,
            "runId": str(deps.run_id),
            "stage": deps.stage,
        },
        dependency_ids=(
            (*deps.input_artifact_ids, parent.id) if parent is not None else deps.input_artifact_ids
        ),
    )


def _source_checkpoint_fingerprint(deps: HarnessModelDeps, sha256: str) -> str:
    """Bind checkpoint identity to its exact run, role, stage and source index."""
    if deps.source_index is None or deps.source_tool_role is None:
        raise ModelPersistenceError("source checkpoint identity requires indexed authority")
    return artifacts.fingerprint_for(
        kind="checks",
        inputs={
            "checkpointSha256": sha256,
            "indexSha256": deps.source_index.sha256,
            "role": deps.source_tool_role,
            "runId": str(deps.run_id),
            "stage": deps.stage,
        },
        config={"format": CHECKPOINT_FORMAT, "programVersion": deps.program_version},
    )


def _normalize_response(deps: HarnessModelDeps, response: ModelResponse) -> ModelResponse:
    """Reject terminal truncation after settlement, then normalize named cosmetic fields."""
    if (
        deps.route.transport is not None
        and deps.route.transport.mode == "streaming"
        and response.finish_reason
        not in ({"stop", "tool_call"} if deps.source_tool_role is not None else {"stop"})
    ):
        raise UnexpectedModelBehavior("streamed model response did not finish successfully")
    return response


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
    try:
        if runtime.lookup_client is not None:
            return await observe_generation_cost(
                runtime.lookup_client,
                config=runtime.gateway,
                route=deps.route,
                generation_id=response.provider_response_id,
            )
        async with httpx.AsyncClient() as client:
            return await observe_generation_cost(
                client,
                config=runtime.gateway,
                route=deps.route,
                generation_id=response.provider_response_id,
            )
    except (GatewayError, httpx.HTTPError, ValueError) as error:
        return CostObservation(
            status="pending",
            actual_cost_micros=None,
            components={
                "generationId": response.provider_response_id,
                "reason": "lookup_error",
                "errorType": type(error).__name__,
            },
        )


def _owner_token(runtime: ModelRuntime) -> str:
    if activity.in_activity():
        info = activity.info()
        return hashlib.sha256(f"{info.workflow_run_id}:{info.activity_id}".encode()).hexdigest()
    if runtime.allow_outside_activity:
        return "test:outside-activity"
    raise ModelPersistenceError("harness model request must execute inside a Temporal activity")


def _validate_request(deps: HarnessModelDeps, parameters: ModelRequestParameters) -> None:
    source_tools = {"browse_source", "search_source", "read_source"}
    reviewer_tools = {*source_tools, "inspect_candidate", "read_media_evidence"}
    allowed = reviewer_tools if deps.source_tool_role == "source_reviewer" else source_tools
    names = {tool.name for tool in parameters.function_tools}
    if parameters.native_tools or parameters.output_tools:
        raise ModelPersistenceError("native and output tools are disabled on the harness path")
    if names and (deps.source_tool_role is None or names != allowed):
        raise ModelPersistenceError("model request contains unqualified source tools")
    if deps.source_tool_role is not None and names != allowed:
        raise ModelPersistenceError("indexed editorial calls require the exact source toolset")
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

    async def _settle_failed_dispatch(
        self,
        *,
        runtime: ModelRuntime,
        operation_id: UUID,
        attempt: ledger.Attempt,
        owner_token: str,
        generation_ids: list[str],
    ) -> str | None:
        """Turn a lost stream into a known failure when the gateway receipt settles.

        Returns the failure sentence when the observed generation's charge is
        reported, after recording the attempt as conclusively failed with that
        charge. Returns None, without writing anything, when there is no observed
        generation, the lookup is pending, or the lookup itself fails; the caller
        then keeps the unknown-outcome fence exactly as before.
        """
        if not generation_ids or runtime.gateway is None or self.deps.synthetic_payload:
            return None
        generation_id = generation_ids[-1]
        try:
            if runtime.lookup_client is not None:
                observation = await observe_generation_cost(
                    runtime.lookup_client,
                    config=runtime.gateway,
                    route=self.deps.route,
                    generation_id=generation_id,
                )
            else:
                async with httpx.AsyncClient() as client:
                    observation = await observe_generation_cost(
                        client,
                        config=runtime.gateway,
                        route=self.deps.route,
                        generation_id=generation_id,
                    )
        except (GatewayError, httpx.HTTPError, ValueError, TimeoutError):
            return None
        if observation.status != "reported" or observation.actual_cost_micros is None:
            return None
        sentence = (
            f"Route {self.deps.route.id}: the {self.deps.stage} request ended without a "
            f"response; its charge of {observation.actual_cost_micros} micros is settled and "
            "a fresh attempt is allowed."
        )
        await ledger.fail_attempt(
            runtime.database_url,
            scope=self.deps.scope,
            source_id=self.deps.source_id,
            run_id=self.deps.run_id,
            operation_id=operation_id,
            attempt_id=attempt.id,
            owner_token=owner_token,
            outcome_known=True,
            actual_cost_micros=observation.actual_cost_micros,
            usage=dict(observation.components),
            error_code="provider-stream-failure",
            error_message=sentence,
        )
        return sentence

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
        _validate_request(self.deps, model_request_parameters)
        _validate_route_settings(self.deps, model_settings)
        source_checkpoint = await _persist_source_checkpoint(runtime, self.deps, messages)
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
            return _normalize_response(self.deps, _response_from_artifact(stored))
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
            return _normalize_response(self.deps, response)
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
        # Wait for a slot on this route before the dispatch is committed: a request queued
        # behind the provider's rate limit is not a request the provider has seen.
        gate = runtime.route_gate(self.deps.route.id)
        await gate.acquire()
        gate_released = False

        def release_gate() -> None:
            nonlocal gate_released
            if not gate_released:
                gate_released = True
                gate.release()

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
            release_gate()
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
        except BaseException:
            release_gate()
            raise
        if not dispatched:
            release_gate()
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

        observed_generations: list[str] = []

        async def remember_generation(identity: str) -> None:
            observed_generations.append(identity)
            save = asyncio.create_task(
                ledger.attach_remote_handle(
                    runtime.database_url,
                    scope=self.deps.scope,
                    source_id=self.deps.source_id,
                    operation_id=acquired.operation.id,
                    attempt_id=attempt.id,
                    owner_token=owner_token,
                    remote_handle=identity,
                )
            )
            try:
                await asyncio.shield(save)
            except asyncio.CancelledError:
                await save
                raise

        try:
            with (
                observe_gateway_generation(remember_generation),
                dispatch_payload_bytes(payload_bytes),
            ):
                response = await super().request(messages, model_settings, model_request_parameters)
        except ModelHTTPError as error:
            status = error.status_code
            transient = status in TRANSIENT_HTTP_STATUSES or status >= HTTP_SERVER_ERROR_MIN
            conclusive = HTTP_CLIENT_ERROR_MIN <= status < HTTP_CLIENT_ERROR_MAX and not transient
            await ledger.fail_attempt(
                runtime.database_url,
                scope=self.deps.scope,
                source_id=self.deps.source_id,
                run_id=self.deps.run_id,
                operation_id=acquired.operation.id,
                attempt_id=attempt.id,
                owner_token=owner_token,
                outcome_known=conclusive or transient,
                # An HTTP status before any response means no generation exists: the cost
                # is a known zero and the reservation is released rather than left pending.
                actual_cost_micros=0 if conclusive or transient else None,
                usage={},
                error_code=f"http-{status}",
                error_message="provider request ended with an HTTP error",
            )
            if transient:
                retry_after = getattr(error, "retry_after_seconds", None)
                advice = (
                    f" The provider asked for a pause of {int(retry_after)} s."
                    if isinstance(retry_after, (int, float))
                    else ""
                )
                refusal = (
                    f"Route {self.deps.route.id} answered the {self.deps.stage} request with "
                    f"HTTP {status} before any response; nothing was charged and a fresh "
                    f"attempt is allowed.{advice}"
                )
                raise TransientProviderFailure(refusal) from error
            if conclusive:
                # The stop reason names the route, stage and code an operator has to act on.
                rejection = (
                    f"Route {self.deps.route.id} rejected the {self.deps.stage} request "
                    f"(HTTP {status})."
                )
                raise KnownProviderRejection(rejection) from error
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
            settled = await self._settle_failed_dispatch(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                generation_ids=observed_generations,
            )
            if settled is not None:
                raise TransientProviderFailure(settled) from error
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
        finally:
            release_gate()
        try:
            accepted = await self._accept_response(
                runtime=runtime,
                operation_id=acquired.operation.id,
                attempt=attempt,
                owner_token=owner_token,
                request_hash=request_hash,
                response_fingerprint=response_fingerprint,
                response=response,
                max_output_tokens=requested_max,
                source_checkpoint=source_checkpoint,
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
        return _normalize_response(self.deps, accepted)

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
        max_output_tokens: int | None,
        source_checkpoint: artifacts.HarnessArtifact | None,
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
                "maxOutputTokens": max_output_tokens,
                "sourceCheckpointId": (
                    str(source_checkpoint.id) if source_checkpoint is not None else None
                ),
                "sourceCheckpointSha256": (
                    source_checkpoint.sha256 if source_checkpoint is not None else None
                ),
            },
            dependency_ids=(
                (*self.deps.input_artifact_ids, source_checkpoint.id)
                if source_checkpoint is not None
                else self.deps.input_artifact_ids
            ),
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


class PayloadScaledModelRequest(TemporalBoundOperation[ModelRequestParams, Any, ModelResponse]):
    """One model activity bounded by the deadline its own payload size earns.

    The workflow side sees exactly the messages the activity will send, so it recomputes
    the canonical serialized size and derives `start_to_close_timeout` from it. The value
    is a pure function of the request, so replay reproduces it.
    """

    async def __call__(
        self, params: ModelRequestParams, *, config: object | None = None
    ) -> ModelResponse:
        """Schedule the model activity with this call's own start-to-close timeout."""
        deps = params.run_context.deps
        base = cast("dict[str, Any]", config if config is not None else self._config)
        if not isinstance(deps, HarnessModelDeps):
            return await super().__call__(params, config=cast("Any", base))
        payload_bytes = request_payload_bytes(
            params.messages,
            params.model_settings,
            params.model_request_parameters,
            _cassette_metadata(deps),
        )
        scaled = {
            **base,
            "start_to_close_timeout": timedelta(
                seconds=model_activity_timeout_seconds(deps.route.transport, payload_bytes)
            ),
        }
        return await super().__call__(params, config=cast("Any", scaled))


class PayloadScaledDurability(TemporalDurability[HarnessModelDeps]):
    """Temporal durability whose model activity deadline is computed per request."""

    def _bind_model_operations(
        self, backend: DurableOperationBackend[Any], *, model_id: str | None, model_name: str
    ) -> _BoundModelOperations:
        bound = super()._bind_model_operations(backend, model_id=model_id, model_name=model_name)
        request = bound.request
        if not isinstance(request, TemporalBoundOperation):  # pragma: no cover - engine invariant
            return bound
        scaled = PayloadScaledModelRequest(
            request.operation,
            registration=request.registration,
            config=self._model_activity_config,
        )
        return bound._replace(request=scaled)


def _agent(
    name: str,
    output_type: type[Any],
    *,
    indexed_source: bool = False,
    reviewer_evidence: bool = False,
) -> Agent[HarnessModelDeps, Any]:
    if reviewer_evidence and not indexed_source:
        raise ValueError("reviewer evidence tools require indexed source tools")
    tools: list[Any] = [browse_source, search_source, read_source] if indexed_source else []
    if reviewer_evidence:
        tools.extend((inspect_candidate, read_media_evidence))
    return Agent(
        model=MODEL_ALIAS,
        defer_model_check=True,
        output_type=NativeOutput(output_type, strict=True),
        deps_type=HarnessModelDeps,
        name=name,
        retries=0,
        tools=tools,
        capabilities=[
            *([ProcessHistory(compact_source_history)] if indexed_source else []),
            ResolveModelId(resolve_configured_model),
            PayloadScaledDurability(
                model_activity_config={
                    # The unscaled default: one payload unit keeps exactly ten minutes.
                    "start_to_close_timeout": timedelta(minutes=10),
                    "heartbeat_timeout": timedelta(seconds=30),
                    "retry_policy": RetryPolicy(maximum_attempts=1),
                },
                activity_config={
                    "start_to_close_timeout": timedelta(minutes=2),
                    "retry_policy": RetryPolicy(maximum_attempts=1),
                },
            ),
        ],
    )


topic_opportunity_inventory_v3 = _agent(
    "topic_opportunity_inventory_v3", TopicSelectionDraft, indexed_source=True
)
topic_selection_author_v3 = _agent(
    "topic_selection_author_v3", TopicSelectionDraft, indexed_source=True
)
topic_selection_cold_v3 = _agent("topic_selection_cold_v3", TopicSelectionColdReview)
topic_selection_source_v4 = _agent(
    "topic_selection_source_v4",
    TopicPortfolioReviewV4,
    indexed_source=True,
    reviewer_evidence=True,
)
topic_selection_patch_v3 = _agent("topic_selection_patch_v3", TopicSelectionPatchV3)
# The pinned plugin appends every workflow's agents without deduplicating them.
# Keep registrations disjoint; chapter review reuses the chapter worker activities.
TOPIC_SELECTION_AGENTS: tuple[Agent[HarnessModelDeps, Any], ...] = (
    topic_opportunity_inventory_v3,
    topic_selection_author_v3,
    topic_selection_cold_v3,
    topic_selection_source_v4,
    topic_selection_patch_v3,
)
HARNESS_AGENTS = TOPIC_SELECTION_AGENTS


def harness_pydantic_ai_plugin() -> PydanticAIPlugin:
    """Return the worker/client plugin for disjoint per-workflow agent registrations."""
    return PydanticAIPlugin()
