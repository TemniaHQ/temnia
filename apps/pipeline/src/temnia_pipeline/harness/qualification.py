"""Finite, private evidence collection for the live gateway transport candidate."""

# The qualification runner intentionally has explicit refusal sites and a verbose report.
# ruff: noqa: C901, EM101, N818, PLR0912, PLR0913, PLR0915, TRY003

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import httpx
import httpx2
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_ai import Agent, ModelResponse, NativeOutput
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.wrapper import WrapperModel

from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.cassettes import (
    CassetteMetadata,
    request_fingerprint,
)
from temnia_pipeline.harness.gateway import (
    CostObservation,
    GatewayChatModel,
    GatewayConfig,
    GatewayError,
    GenerationIdentityError,
    lookup_generation,
    observe_gateway_generation,
    observe_generation_cost,
    validate_gateway_request,
)
from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy  # noqa: TC001
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_V3_SCHEMAS,
    TOPIC_SELECTION_V3_STAGES,
    TOPIC_SELECTION_V4_SCHEMAS,
    TOPIC_SELECTION_V4_STAGES,
    TOPIC_SELECTION_V5_SCHEMAS,
    TOPIC_SELECTION_V5_STAGES,
    topic_selection_qualification_prompts,
    topic_selection_v4_qualification_prompts,
    topic_selection_v5_qualification_prompts,
    topic_source_progress_processor,
    topic_source_qualification_tools,
    validate_topic_selection_qualification_output,
    validate_topic_selection_v4_qualification_output,
    validate_topic_selection_v5_qualification_output,
)
from temnia_pipeline.harness.routes import (
    UNPROVEN_ROUTE_PREFIX,
    ReasoningEffort,
    RouteEligibility,
    RouteEntry,
    RoutePrices,
    ServiceTier,
    estimate_cost,
)

MAX_CANDIDATES = 4
MAX_DISPATCHES = 12
MAX_EXPOSURE_MICROS = 1_000_000
MAX_OUTPUT_TOKENS = 8192
MAX_LOOKUP_WAIT_SECONDS = 30.0
MAX_CANDIDATE_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
SHA256_PATTERN = r"^[a-f0-9]{64}$"
_SENSITIVE_NAMES = frozenset(
    {"api_key", "apikey", "authorization", "credential", "password", "secret", "token"}
)
QualificationSuite = Literal["topic-selection-v3", "topic-selection-v4", "topic-selection-v5"]


def _suite_stages(suite: QualificationSuite) -> tuple[str, ...]:
    if suite == "topic-selection-v5":
        return TOPIC_SELECTION_V5_STAGES
    if suite == "topic-selection-v4":
        return TOPIC_SELECTION_V4_STAGES
    return TOPIC_SELECTION_V3_STAGES


def _schema_version(stage: str, limits: QualificationLimits) -> str:
    schemas = {
        "topic-selection-v3": TOPIC_SELECTION_V3_SCHEMAS,
        "topic-selection-v4": TOPIC_SELECTION_V4_SCHEMAS,
        "topic-selection-v5": TOPIC_SELECTION_V5_SCHEMAS,
    }[limits.suite]
    return schemas[stage]


_RESPONSE_ADAPTER = TypeAdapter(ModelResponse)
HTTP_CLIENT_ERROR_MIN = 400
HTTP_CLIENT_ERROR_MAX = 500
HTTP_REQUEST_TIMEOUT = 408
AUTHORIZATION_FAILURES = frozenset({401, 403})
MAX_FAILURE_DETAIL_CHARS = 512

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

    from temnia_pipeline.harness.gateway_policy import GatewayName


class QualificationRefusal(RuntimeError):
    """The qualification cannot safely dispatch or continue."""


class QualificationHalt(QualificationRefusal):
    """A dispatched request has unresolved outcome, identity, or cost."""


class CandidatePrices(BaseModel):
    """Conservative public price metadata, still subject to live charge lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit: Literal["micros_per_million_tokens"]
    input: Annotated[int, Field(ge=0)]
    output: Annotated[int, Field(ge=0)]
    cache_read: Annotated[int | None, Field(alias="cacheRead", ge=0)]
    cache_write: Annotated[int | None, Field(alias="cacheWrite", ge=0)]
    request_surcharge: Annotated[int, Field(alias="requestSurcharge", ge=0)]

    def route_prices(self) -> RoutePrices:
        """Translate reviewed candidate metadata into the existing route price contract."""
        return RoutePrices(
            input=self.input,
            output=self.output,
            cache_read=self.cache_read,
            cache_write=self.cache_write,
            request_surcharge=self.request_surcharge,
        )


class CandidateRoute(BaseModel):
    """One metadata-only model/provider candidate; it is not a qualified route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
    gateway_model: Annotated[str, Field(alias="gatewayModel", min_length=1, max_length=256)]
    family: Annotated[str, Field(min_length=1, max_length=128)]
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    open_weight: bool = Field(alias="openWeight")
    context_tokens: Annotated[int, Field(alias="contextTokens", gt=0)]
    max_output_tokens: Annotated[int, Field(alias="maxOutputTokens", gt=0)]
    zdr_claim: Literal[True] = Field(alias="zdrClaim")
    prices: CandidatePrices
    reasoning_effort: ReasoningEffort | None = Field(default=None, alias="reasoningEffort")
    service_tier: ServiceTier | None = Field(default=None, alias="serviceTier")
    transport: GatewayTransportPolicy | None = Field(default=None, exclude_if=lambda v: v is None)
    provider_accounting_name: str | None = Field(
        default=None,
        alias="providerAccountingName",
        min_length=1,
        max_length=128,
        exclude_if=lambda v: v is None,
    )
    accounting_model: str | None = Field(
        default=None,
        alias="accountingModel",
        min_length=1,
        max_length=256,
        exclude_if=lambda v: v is None,
    )

    @model_validator(mode="after")
    def _transport_identity(self) -> CandidateRoute:
        if self.transport is not None and self.transport.gateway == "openrouter":
            if not self.provider_accounting_name:
                raise ValueError("OpenRouter candidate requires its accounting provider identity")
        elif self.provider_accounting_name is not None:
            raise ValueError("accounting provider identity requires OpenRouter transport")
        if (
            self.transport is not None
            and self.transport.gateway == "openrouter"
            and self.transport.version == "gateway-transport/2"
        ):
            if self.accounting_model is None:
                raise ValueError("OpenRouter version 2 candidate requires its accounting model")
        elif self.accounting_model is not None:
            raise ValueError("accounting model identity requires OpenRouter transport version 2")
        return self


class CandidateCatalogue(BaseModel):
    """The reviewed metadata-only input to one finite qualification session."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    catalogue_observed_at: AwareDatetime = Field(alias="catalogueObservedAt")
    catalogue_sha256: Annotated[str, Field(alias="catalogueSha256", pattern=SHA256_PATTERN)]
    candidates: Annotated[tuple[CandidateRoute, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique(self) -> CandidateCatalogue:
        if len({route.id for route in self.candidates}) != len(self.candidates):
            raise ValueError("candidate route IDs must be unique")
        identities = [(route.gateway_model, route.provider) for route in self.candidates]
        if len(set(identities)) != len(identities):
            raise ValueError("candidate model/provider identities must be unique")
        if len({candidate_gateway(route) for route in self.candidates}) != 1:
            raise ValueError("one qualification cohort must use one gateway")
        return self


def candidate_gateway(candidate: CandidateRoute) -> GatewayName:
    """Legacy candidates retain the original Vercel transport identity."""
    return candidate.transport.gateway if candidate.transport is not None else "vercel"


def qualification_gateway(path: Path, *, journal: bool = False) -> GatewayName:
    """Resolve credentials from immutable candidate intent without reading any credential."""
    if journal:
        value = json.loads(_bounded_read(path, MAX_JOURNAL_BYTES))
        _validate_reconciliation_journal(value)
        catalogue = CandidateCatalogue.model_validate_json(canonical_json(value["catalogue"]))
    else:
        catalogue = load_candidates(path)
    return candidate_gateway(catalogue.candidates[0])


class QualificationLimits(BaseModel):
    """Operator limits; historical suites retain their original fixed ceilings."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_exposure_micros: Annotated[int, Field(gt=0)]
    max_dispatches: Annotated[int, Field(gt=0)]
    max_output_tokens: Annotated[int, Field(ge=256)]
    suite: QualificationSuite = "topic-selection-v3"
    stages: tuple[str, ...] | None = None
    request_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 300
    lookup_timeout_seconds: Annotated[float, Field(gt=0, le=10)] = 10
    lookup_wait_seconds: Annotated[float, Field(ge=0, le=MAX_LOOKUP_WAIT_SECONDS)] = 30

    @model_validator(mode="after")
    def _suite_wire(self) -> QualificationLimits:
        if self.stages is not None and (
            not self.stages
            or len(set(self.stages)) != len(self.stages)
            or any(stage not in _suite_stages(self.suite) for stage in self.stages)
        ):
            raise ValueError("qualification stages must be a unique subset of the selected suite")
        return self


def _selected_stages(limits: QualificationLimits) -> tuple[str, ...]:
    return limits.stages or _suite_stages(limits.suite)


@dataclass(frozen=True, slots=True)
class _LoadedCandidateCatalogue:
    """A parsed catalogue bound to the exact bytes read by the runner."""

    catalogue: CandidateCatalogue
    file_sha256: str


def _load_candidate_source(path: Path) -> _LoadedCandidateCatalogue:
    """Load a reviewed metadata file, refusing secret-shaped keys anywhere in it."""
    body = _bounded_read(path, MAX_CANDIDATE_BYTES)
    if len(body) > MAX_CANDIDATE_BYTES:
        raise QualificationRefusal("candidate metadata exceeds 1 MiB")
    raw = cast("object", json.loads(body))
    _reject_sensitive_keys(raw)
    return _LoadedCandidateCatalogue(
        catalogue=CandidateCatalogue.model_validate_json(body, strict=True),
        file_sha256=hashlib.sha256(body).hexdigest(),
    )


def load_candidates(path: Path) -> CandidateCatalogue:
    """Validate a candidate file without producing a separately pairable file hash."""
    return _load_candidate_source(path).catalogue


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit + 1)


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in cast("dict[object, object]", value).items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SENSITIVE_NAMES or normalized.endswith("_secret"):
                raise QualificationRefusal("candidate metadata contains a secret-shaped field")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in cast("list[object]", value):
            _reject_sensitive_keys(child)


def _private_create(path: Path, value: object) -> None:
    body = canonical_json(value) + b"\n"
    _private_create_raw(path, body)


def _private_create_raw(path: Path, body: bytes) -> None:
    """Retain received accounting bytes without changing their monetary representation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        _fsync_directory(path.parent)


def _private_replace(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json(value) + b"\n"
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _safe_failure_text(value: object, *, forbidden: bytes, limit: int) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = " ".join(str(value).split())[:limit]
    if not text or forbidden in text.encode():
        return "[redacted]" if text else None
    return text


def _sanitized_http_failure(error: ModelHTTPError, *, forbidden: bytes) -> dict[str, Any]:
    body = error.body
    container: dict[object, object] = {}
    if isinstance(body, dict):
        raw = cast("dict[object, object]", body)
        nested = raw.get("error")
        container = cast("dict[object, object]", nested) if isinstance(nested, dict) else raw
    details = {
        key: value
        for key, value in (
            (
                "type",
                _safe_failure_text(container.get("type"), forbidden=forbidden, limit=128),
            ),
            (
                "code",
                _safe_failure_text(container.get("code"), forbidden=forbidden, limit=128),
            ),
            (
                "message",
                _safe_failure_text(
                    container.get("message"),
                    forbidden=forbidden,
                    limit=MAX_FAILURE_DETAIL_CHARS,
                ),
            ),
        )
        if value is not None
    }
    return {
        "format": "temnia-gateway-http-failure/1",
        "statusCode": error.status_code,
        "error": details,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _QualificationJournal:
    """Create-only session intent with atomic private state transitions."""

    def __init__(self, path: Path, receipts: Path, value: dict[str, Any], forbidden: bytes) -> None:
        self.path = path
        self.receipts = receipts
        self.value = value
        self.forbidden = forbidden

    @classmethod
    def create(
        cls,
        *,
        path: Path,
        receipts: Path,
        catalogue: CandidateCatalogue,
        catalogue_file_sha256: str,
        limits: QualificationLimits,
        api_key: str,
    ) -> _QualificationJournal:
        calls = [
            {
                "candidateId": candidate.id,
                "stage": stage,
                "state": "planned",
            }
            for candidate in catalogue.candidates
            for stage in _selected_stages(limits)
        ]
        prompts = qualification_prompts(limits.suite)
        for call in calls:
            stage = call["stage"]
            call.update(
                {
                    "suite": limits.suite,
                    "promptVersion": prompts[stage][2],
                    "schemaVersion": _schema_version(stage, limits),
                }
            )
        serialized_limits = limits.model_dump(mode="json")
        if limits.stages is None:
            serialized_limits.pop("stages")
        value: dict[str, Any] = {
            "format": "temnia-gateway-qualification/1",
            "createdAt": _now(),
            "status": "prepared",
            "catalogueFileSha256": catalogue_file_sha256,
            "catalogue": catalogue.model_dump(mode="json", by_alias=True),
            "limits": serialized_limits,
            "dispatchCount": 0,
            "admittedExposureMicros": 0,
            "reportedCostMicros": 0,
            "calls": calls,
            "proofLimits": [
                "small synthetic prompts do not qualify maximum context capacity",
                "transmitted request controls do not prove provider policy beyond this request",
                "transport success does not establish editorial quality or Temporal recovery",
                "candidate metadata is not a production route snapshot",
            ],
        }
        value["suite"] = limits.suite
        forbidden = api_key.encode()
        if forbidden in canonical_json(value):
            raise QualificationRefusal("qualification input contains the gateway credential")
        _private_create(path, value)
        receipts.mkdir(parents=True, exist_ok=True)
        receipts.chmod(0o700)
        return cls(path, receipts, value, forbidden)

    def _call(self, candidate_id: str, stage: str) -> dict[str, Any]:
        for call in cast("list[dict[str, Any]]", self.value["calls"]):
            if call["candidateId"] == candidate_id and call["stage"] == stage:
                return call
        raise QualificationRefusal("qualification call is absent from the frozen plan")

    def save(self) -> None:
        if self.forbidden in canonical_json(self.value):
            raise QualificationRefusal("qualification evidence contains the gateway credential")
        _private_replace(self.path, self.value)

    @staticmethod
    def _archive_round(call: dict[str, Any]) -> None:
        """Retain each paid request inside one logical qualification stage."""
        round_number = call.get("roundNumber")
        if type(round_number) is not int:
            return
        rounds = cast("list[dict[str, Any]]", call.setdefault("rounds", []))
        logical = {"candidateId", "stage", "suite", "promptVersion", "schemaVersion", "rounds"}
        snapshot = {key: value for key, value in call.items() if key not in logical}
        for offset, item in enumerate(rounds):
            if item.get("roundNumber") == round_number:
                rounds[offset] = snapshot
                return
        rounds.append(snapshot)

    @staticmethod
    def _clear_round(call: dict[str, Any]) -> None:
        logical = {"candidateId", "stage", "suite", "promptVersion", "schemaVersion", "rounds"}
        for key in tuple(call):
            if key not in logical:
                del call[key]

    def admit(
        self,
        candidate_id: str,
        stage: str,
        *,
        request_hash: str,
        payload_bytes: int,
        estimated_cost_micros: int,
        prompt_version: str | None = None,
        schema_version: str | None = None,
    ) -> None:
        call = self._call(candidate_id, stage)
        if call["state"] == "cost_reported" and call.get("finishReason") == "tool_call":
            self._archive_round(call)
            self._clear_round(call)
        elif call["state"] != "planned":
            raise QualificationRefusal("qualification call was already admitted")
        limits = cast("dict[str, Any]", self.value["limits"])
        dispatches = int(self.value["dispatchCount"])
        exposure = int(self.value["admittedExposureMicros"])
        if dispatches >= int(limits["max_dispatches"]):
            raise QualificationRefusal("qualification dispatch limit reached")
        if exposure + estimated_cost_micros > int(limits["max_exposure_micros"]):
            raise QualificationRefusal("qualification exposure limit cannot admit the next call")
        call.update(
            {
                "state": "admitted",
                "roundNumber": len(cast("list[dict[str, Any]]", call.get("rounds", []))) + 1,
                "admittedAt": _now(),
                "requestHash": request_hash,
                "payloadBytes": payload_bytes,
                "estimatedCostMicros": estimated_cost_micros,
            }
        )
        if prompt_version is not None and schema_version is not None:
            call.update(
                {
                    "suite": limits["suite"],
                    "promptVersion": prompt_version,
                    "schemaVersion": schema_version,
                }
            )
        self.value["dispatchCount"] = dispatches + 1
        self.value["admittedExposureMicros"] = exposure + estimated_cost_micros
        self.value["status"] = "running"
        self.save()

    def call_identity(self, candidate_id: str, stage: str) -> dict[str, Any]:
        """Return the admitted call for exact native-schema evidence before dispatch."""
        return self._call(candidate_id, stage)

    def request_sent(self, candidate_id: str, stage: str, sanitized: dict[str, Any]) -> None:
        call = self._call(candidate_id, stage)
        if call["state"] != "admitted":
            raise QualificationRefusal("HTTP request was reached without a durable admission")
        call.update({"state": "request_sent", "requestSentAt": _now(), "request": sanitized})
        self.save()

    def response_saved(
        self, candidate_id: str, stage: str, response: ModelResponse
    ) -> tuple[str, int, Path]:
        call = self._call(candidate_id, stage)
        if call["state"] != "request_sent":
            raise QualificationRefusal("model response arrived without a sent request")
        if call.get("generationId") not in {None, response.provider_response_id}:
            self.failure(
                candidate_id, stage, state="identity_failed", code="generation-id-conflict"
            )
            raise QualificationHalt("response generation differs from the observed stream")
        body = _RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True)
        raw = canonical_json(body) + b"\n"
        if self.forbidden in raw:
            self.failure(
                candidate_id,
                stage,
                state="outcome_unknown",
                code="credential-echo-refused",
            )
            raise QualificationRefusal("gateway response contained a credential marker")
        round_number = int(call.get("roundNumber", 1))
        path = self.receipts / f"{candidate_id}.{stage}.round-{round_number}.model-response.json"
        try:
            _private_create(path, body)
        except Exception:
            self.failure(
                candidate_id,
                stage,
                state="outcome_unknown",
                code="response-persistence-failed",
            )
            raise
        digest = hashlib.sha256(raw).hexdigest()
        response_ref = {"path": str(path), "sha256": digest, "sizeBytes": len(raw)}
        response_ref.update(
            {"promptVersion": call["promptVersion"], "schemaVersion": call["schemaVersion"]}
        )
        call.update(
            {
                "state": "response_saved",
                "responseSavedAt": _now(),
                "response": response_ref,
                "generationId": response.provider_response_id,
                "responseModel": response.model_name,
                "responseProvider": response.provider_name,
                "finishReason": response.finish_reason,
            }
        )
        self.save()
        return digest, len(raw), path

    def generation_observed(self, candidate_id: str, stage: str, generation_id: str) -> None:
        """Persist an early handle without claiming a complete model response."""
        call = self._call(candidate_id, stage)
        if call["state"] != "request_sent" or not generation_id:
            raise QualificationHalt("generation identity arrived outside an active request")
        previous = call.get("generationId")
        if previous not in {None, generation_id}:
            self.failure(
                candidate_id, stage, state="identity_failed", code="generation-id-conflict"
            )
            raise QualificationHalt("stream generation identity changed")
        if previous is None:
            call.update({"generationId": generation_id, "generationObservedAt": _now()})
            self.save()

    def http_failure_saved(self, candidate_id: str, stage: str, error: ModelHTTPError) -> None:
        call = self._call(candidate_id, stage)
        if call["state"] != "request_sent":
            raise QualificationRefusal("HTTP failure arrived without a sent request")
        detail = _sanitized_http_failure(error, forbidden=self.forbidden)
        raw = canonical_json(detail) + b"\n"
        round_number = int(call.get("roundNumber", 1))
        path = self.receipts / f"{candidate_id}.{stage}.round-{round_number}.http-failure.json"
        _private_create(path, detail)
        call["httpFailure"] = {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sizeBytes": len(raw),
        }
        self.save()

    def cost_receipt_saved(self, candidate_id: str, stage: str, response: httpx.Response) -> None:
        """Accounting GETs are finite JSON responses, separate from model streaming."""
        call = self._call(candidate_id, stage)
        identity = response.request.url.params.get("id")
        if call.get("generationId") != identity or call["state"] != "response_saved":
            raise QualificationHalt("cost receipt differs from the completed request identity")
        raw = response.content
        if self.forbidden in raw:
            raise QualificationHalt("accounting receipt contains a credential marker")
        receipts = cast("list[dict[str, Any]]", call.setdefault("costReceipts", []))
        round_number = int(call.get("roundNumber", 1))
        path = self.receipts / (
            f"{candidate_id}.{stage}.round-{round_number}.cost-{len(receipts) + 1}.json"
        )
        _private_create_raw(path, raw)
        receipts.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "sizeBytes": len(raw),
                "generationId": identity,
                "statusCode": response.status_code,
                "url": str(response.request.url.copy_with(query=None)),
            }
        )
        self.save()

    def cost_observed(self, candidate_id: str, stage: str, observation: CostObservation) -> bool:
        call = self._call(candidate_id, stage)
        call["cost"] = observation.model_dump(mode="json")
        if observation.status == "reported" and observation.actual_cost_micros is not None:
            estimated = int(call["estimatedCostMicros"])
            estimate_exceeded = observation.actual_cost_micros > estimated
            if estimate_exceeded:
                self.value["admittedExposureMicros"] = int(self.value["admittedExposureMicros"]) + (
                    observation.actual_cost_micros - estimated
                )
                call["estimateExceeded"] = True
                self.value["status"] = "halted"
            self.value["reportedCostMicros"] = int(self.value["reportedCostMicros"]) + int(
                observation.actual_cost_micros
            )
            call["state"] = "cost_reported"
        else:
            estimate_exceeded = False
            call["state"] = "cost_unresolved"
            self.value["status"] = "halted"
        self.save()
        return estimate_exceeded

    def validation(self, candidate_id: str, stage: str, *, passed: bool, code: str) -> None:
        call = self._call(candidate_id, stage)
        call["validation"] = {"passed": passed, "code": code}
        call["state"] = "passed" if passed else "failed"
        self._archive_round(call)
        self.save()

    def failure(self, candidate_id: str, stage: str, *, state: str, code: str) -> None:
        call = self._call(candidate_id, stage)
        call.update({"state": state, "errorCode": code, "finishedAt": _now()})
        self._archive_round(call)
        if state in {"outcome_unknown", "cost_unresolved", "identity_failed"}:
            self.value["status"] = "halted"
        self.save()

    def finish(self) -> None:
        calls = cast("list[dict[str, Any]]", self.value["calls"])
        unsafe = {"admitted", "request_sent", "response_saved", "cost_reported"}
        if any(call["state"] in unsafe for call in calls):
            self.value["status"] = "halted"
            self.value.setdefault("haltCode", "incomplete-admitted-call")
        elif self.value["status"] != "halted":
            self.value["status"] = "completed"
            self.value["passed"] = all(call["state"] == "passed" for call in calls)
        self.value["finishedAt"] = _now()
        self.save()


def _provisional_route(candidate: CandidateRoute, observed: date) -> RouteEntry:
    candidate_sha256 = hashlib.sha256(
        canonical_json(candidate.model_dump(mode="json", by_alias=True))
    ).hexdigest()
    return RouteEntry(
        id=f"{UNPROVEN_ROUTE_PREFIX}{candidate.id}",
        gateway_model=candidate.gateway_model,
        family=candidate.family,
        provider=candidate.provider,
        open_weight=candidate.open_weight,
        context_tokens=candidate.context_tokens,
        max_output_tokens=candidate.max_output_tokens,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            cache_qualified=False,
            probe_artifact_sha256=candidate_sha256,
            probed_at=observed,
        ),
        prices=candidate.prices.route_prices(),
        reasoning_effort=candidate.reasoning_effort,
        service_tier=candidate.service_tier,
        transport=candidate.transport,
        provider_accounting_name=candidate.provider_accounting_name,
        accounting_model=candidate.accounting_model,
        cache_enabled=False,
    )


def qualification_prompts(
    suite: QualificationSuite = "topic-selection-v3",
) -> dict[str, tuple[str, type[Any], str]]:
    """Render the exact production prompts and output types for the topic seats."""
    if suite == "topic-selection-v4":
        return dict(topic_selection_v4_qualification_prompts())
    if suite == "topic-selection-v5":
        return dict(topic_selection_v5_qualification_prompts())
    return dict(topic_selection_qualification_prompts())


def _validate_grounding(stage: str, output: object, suite: QualificationSuite) -> None:
    if suite == "topic-selection-v4":
        validate_topic_selection_v4_qualification_output(stage, output)
    elif suite == "topic-selection-v5":
        validate_topic_selection_v5_qualification_output(stage, output)
    else:
        validate_topic_selection_qualification_output(stage, output)


def _sanitized_request(request: httpx2.Request, route: RouteEntry) -> dict[str, Any]:
    if route.transport is not None:
        body = validate_gateway_request(request, route)
    elif (
        request.method != "POST"
        or str(request.url) != "https://ai-gateway.vercel.sh/v1/chat/completions"
    ):
        raise QualificationRefusal("gateway SDK used an unexpected request endpoint")
    else:
        raw = cast("object", json.loads(request.content))
        if not isinstance(raw, dict):
            raise QualificationRefusal("gateway SDK request is not a JSON object")
        body = cast("dict[str, Any]", raw)
    expected_options = {"gateway": {"only": [route.provider], "zeroDataRetention": True}}
    if body.get("model") != route.gateway_model:
        raise QualificationRefusal("outgoing gateway model differs from the candidate")
    if route.transport is None and (
        body.get("store") is not False or body.get("providerOptions") != expected_options
    ):
        raise QualificationRefusal(
            "outgoing gateway privacy controls differ from the qualification"
        )
    response_format = body.get("response_format")
    if not isinstance(response_format, dict):
        raise QualificationRefusal("outgoing request has no native response schema")
    response_format_values = cast("dict[str, Any]", response_format)
    schema = response_format_values.get("json_schema")
    if not isinstance(schema, dict) or cast("dict[str, Any]", schema).get("strict") is not True:
        raise QualificationRefusal("outgoing response schema is not strict")
    messages_value = body.get("messages")
    if not isinstance(messages_value, list):
        raise QualificationRefusal("outgoing request has no messages")
    messages = cast("list[object]", messages_value)
    result = {
        "method": request.method,
        "url": str(request.url),
        "model": body["model"],
        "store": body["store"],
        "maxCompletionTokens": body.get("max_completion_tokens"),
        "reasoningEffort": body.get("reasoning_effort"),
        "serviceTier": body.get("service_tier"),
        "providerOptions": body.get("providerOptions"),
        "responseFormat": {
            "type": response_format_values.get("type"),
            "name": cast("dict[str, Any]", schema).get("name"),
            "strict": True,
            "schemaSha256": _sha(cast("dict[str, Any]", schema).get("schema")),
        },
        "messages": {
            "count": len(messages),
            "sha256": _sha(messages),
            "sizeBytes": len(canonical_json(messages)),
        },
    }
    if route.transport is not None:
        result.update(
            {
                "transport": route.transport.model_dump(mode="json"),
                "providerAccountingName": route.provider_accounting_name,
                "stream": body.get("stream"),
                "httpTimeout": request.extensions.get("timeout"),
                "maxTokens": body.get("max_tokens"),
                "provider": body.get("provider"),
                "reasoning": body.get("reasoning"),
                "headers": {
                    name: request.headers[name]
                    for name in ("x-openrouter-cache", "x-openrouter-metadata")
                    if name in request.headers
                },
            }
        )
    if route.accounting_model is not None:
        result["accountingModel"] = route.accounting_model
    tools = body.get("tools")
    if isinstance(tools, list):
        result["functionTools"] = [
            {
                "name": cast("dict[str, Any]", cast("dict[str, Any]", item)["function"])["name"],
                "schemaSha256": _sha(
                    cast("dict[str, Any]", cast("dict[str, Any]", item)["function"])["parameters"]
                ),
            }
            for item in cast("list[object]", tools)
            if isinstance(item, dict)
            and isinstance(cast("dict[str, Any]", item).get("function"), dict)
        ]
    return result


def _request_capture(
    journal: _QualificationJournal, candidate_id: str, stage: str, route: RouteEntry
) -> Callable[[httpx2.Request], Awaitable[None]]:
    async def capture(request: httpx2.Request) -> None:
        journal.request_sent(candidate_id, stage, _sanitized_request(request, route))

    return capture


class _QualificationModel(WrapperModel):
    def __init__(
        self,
        model: GatewayChatModel,
        *,
        candidate_id: str,
        stage: str,
        route: RouteEntry,
        journal: _QualificationJournal,
        lookup_client: httpx.AsyncClient,
        gateway_config: GatewayConfig,
        limits: QualificationLimits,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        super().__init__(model)
        self.candidate_id = candidate_id
        self.stage = stage
        self.route = route
        self.journal = journal
        self.lookup_client = lookup_client
        self.gateway_config = gateway_config
        self.limits = limits
        self.sleep = sleep

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        prompts = qualification_prompts(self.limits.suite)
        schema_version = _schema_version(self.stage, self.limits)
        metadata = CassetteMetadata(
            route_id=self.route.id,
            stage=f"qualification:{self.stage}",
            schema_version=schema_version,
            prompt_version=prompts[self.stage][2],
            program_version="gateway-qualification/1",
            synthetic=False,
            transport=self.route.transport,
            provider_accounting_name=self.route.provider_accounting_name,
            accounting_model=self.route.accounting_model,
        )
        request_hash, payload_bytes = request_fingerprint(
            messages, model_settings, model_request_parameters, metadata
        )
        requested_max = cast("int", (model_settings or {}).get("max_tokens"))
        estimate = estimate_cost(
            self.route, payload_bytes=payload_bytes, max_output_tokens=requested_max
        )
        self.journal.admit(
            self.candidate_id,
            self.stage,
            request_hash=request_hash,
            payload_bytes=payload_bytes,
            estimated_cost_micros=estimate.amount_micros,
            prompt_version=metadata.prompt_version,
            schema_version=metadata.schema_version,
        )
        if self.limits.suite.startswith("topic-selection"):
            prompt, output_type, _ = prompts[self.stage]
            output_object = self.customize_request_parameters(
                model_request_parameters
            ).output_object
            if output_object is None:
                raise QualificationRefusal("topic qualification lacks its native output schema")
            call = self.journal.call_identity(self.candidate_id, self.stage)
            call.update(
                {
                    "outputContractSha256": _sha(output_type.model_json_schema()),
                    "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "nativeSchemaSha256": _sha(output_object.json_schema),
                }
            )
            self.journal.save()

        async def observed_generation(generation_id: str) -> None:
            self.journal.generation_observed(self.candidate_id, self.stage, generation_id)

        try:
            with observe_gateway_generation(observed_generation):
                response = await super().request(messages, model_settings, model_request_parameters)
        except ModelHTTPError as error:
            conclusive = (
                HTTP_CLIENT_ERROR_MIN <= error.status_code < HTTP_CLIENT_ERROR_MAX
                and error.status_code != HTTP_REQUEST_TIMEOUT
                and not self.journal.call_identity(self.candidate_id, self.stage).get(
                    "generationId"
                )
            )
            try:
                self.journal.http_failure_saved(self.candidate_id, self.stage, error)
            except Exception as persistence_error:
                self.journal.failure(
                    self.candidate_id,
                    self.stage,
                    state="outcome_unknown",
                    code="http-failure-persistence-failed",
                )
                raise QualificationHalt(
                    "gateway failure detail could not be durably retained"
                ) from persistence_error
            self.journal.failure(
                self.candidate_id,
                self.stage,
                state="known_failure" if conclusive else "outcome_unknown",
                code=f"http-{error.status_code}",
            )
            if error.status_code in AUTHORIZATION_FAILURES:
                raise QualificationHalt(
                    "gateway authorization or ZDR entitlement rejected the request"
                ) from error
            if conclusive:
                raise
            raise QualificationHalt("gateway request outcome is unknown") from error
        except (ModelAPIError, httpx.TimeoutException, httpx2.TimeoutException) as error:
            self.journal.failure(
                self.candidate_id, self.stage, state="outcome_unknown", code="transport-ambiguous"
            )
            raise QualificationHalt("gateway request outcome is unknown") from error
        except asyncio.CancelledError:
            self.journal.failure(
                self.candidate_id, self.stage, state="outcome_unknown", code="transport-cancelled"
            )
            raise
        except Exception as error:
            self.journal.failure(
                self.candidate_id,
                self.stage,
                state="outcome_unknown",
                code="unexpected-after-admission",
            )
            raise QualificationHalt("gateway request outcome is unknown") from error
        try:
            self.journal.response_saved(self.candidate_id, self.stage, response)
        except Exception as error:
            raise QualificationHalt("gateway response could not be durably retained") from error
        if not response.provider_response_id:
            self.journal.failure(
                self.candidate_id, self.stage, state="outcome_unknown", code="missing-generation-id"
            )
            raise QualificationHalt("gateway response has no reconcilable generation ID")

        async def capture_cost(receipt: httpx.Response) -> None:
            if (
                receipt.request.method != "GET"
                or str(receipt.request.url.copy_with(query=None))
                != self.gateway_config.base_url + "/generation"
            ):
                raise QualificationHalt("unexpected accounting request endpoint")
            await receipt.aread()
            self.journal.cost_receipt_saved(self.candidate_id, self.stage, receipt)

        try:
            if self.route.transport is not None:
                self.lookup_client.event_hooks["response"].append(capture_cost)
            try:
                observation = await _poll_cost(
                    self.lookup_client,
                    config=self.gateway_config,
                    route=self.route,
                    generation_id=response.provider_response_id,
                    wait_seconds=self.limits.lookup_wait_seconds,
                    sleep=self.sleep,
                )
            finally:
                if self.route.transport is not None:
                    self.lookup_client.event_hooks["response"].remove(capture_cost)
        except GenerationIdentityError:
            self.journal.failure(
                self.candidate_id,
                self.stage,
                state="identity_failed",
                code="generation-identity-mismatch",
            )
            raise
        except (GatewayError, httpx.HTTPError, ValueError, OSError, QualificationRefusal) as error:
            self.journal.failure(
                self.candidate_id,
                self.stage,
                state="cost_unresolved",
                code="generation-lookup-failed",
            )
            raise QualificationHalt("gateway generation cost remains unresolved") from error
        estimate_exceeded = self.journal.cost_observed(self.candidate_id, self.stage, observation)
        if observation.status != "reported":
            raise QualificationHalt("gateway generation cost remains unresolved")
        if int(self.journal.value["reportedCostMicros"]) > self.limits.max_exposure_micros:
            raise QualificationHalt("reported gateway charges exceed the qualification cap")
        if estimate_exceeded:
            raise QualificationHalt("reported charge exceeds the candidate price reservation")
        if (
            self.route.transport is not None
            and self.route.transport.mode == "streaming"
            and response.finish_reason
            not in (
                {"stop", "tool_call"} if topic_source_qualification_tools(self.stage) else {"stop"}
            )
        ):
            raise UnexpectedModelBehavior("streamed qualification did not finish successfully")
        return response


async def _poll_cost(
    client: httpx.AsyncClient,
    *,
    config: GatewayConfig,
    route: RouteEntry,
    generation_id: str,
    wait_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> CostObservation:
    return await observe_generation_cost(
        client,
        config=config,
        route=route,
        generation_id=generation_id,
        wait_seconds=wait_seconds,
        sleep=sleep,
    )


async def run_qualification(
    *,
    candidate_path: Path,
    api_key: str,
    journal_path: Path,
    receipts_path: Path,
    report_path: Path,
    limits: QualificationLimits,
    request_transport: httpx2.AsyncBaseTransport | None = None,
    lookup_transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    gateway: GatewayName | None = None,
) -> dict[str, Any]:
    """Run the selected finite strict-schema suite sequentially with durable spend evidence."""
    if not api_key:
        raise QualificationRefusal("the selected gateway credential is required")
    resolved_journal = journal_path.resolve()
    resolved_report = report_path.resolve()
    resolved_receipts = receipts_path.resolve()
    resolved_paths = (resolved_journal, resolved_report, resolved_receipts)
    if len(set(resolved_paths)) != len(resolved_paths):
        raise QualificationRefusal("journal, report, and receipt paths must be distinct")
    if resolved_journal.is_relative_to(resolved_receipts) or resolved_report.is_relative_to(
        resolved_receipts
    ):
        raise QualificationRefusal("journal and report must be outside the receipt directory")
    if report_path.exists():
        raise QualificationRefusal("qualification report already exists")
    if receipts_path.exists() and any(receipts_path.iterdir()):
        raise QualificationRefusal("qualification receipt directory is not empty")
    candidates = _load_candidate_source(candidate_path)
    selected_gateway = candidate_gateway(candidates.catalogue.candidates[0])
    if gateway is not None and gateway != selected_gateway:
        raise QualificationRefusal("selected gateway differs from candidate transport")
    if (
        not limits.suite.startswith("topic-selection")
        and len(candidates.catalogue.candidates) > MAX_CANDIDATES
    ):
        raise QualificationRefusal("historical qualification exceeds its candidate ceiling")
    journal = _QualificationJournal.create(
        path=journal_path,
        receipts=receipts_path,
        catalogue=candidates.catalogue,
        catalogue_file_sha256=candidates.file_sha256,
        limits=limits,
        api_key=api_key,
    )
    gateway_config = GatewayConfig(
        api_key=api_key,
        gateway=selected_gateway,
        request_timeout_seconds=limits.request_timeout_seconds,
        lookup_timeout_seconds=limits.lookup_timeout_seconds,
    )
    prompts = qualification_prompts(limits.suite)
    catalogue = candidates.catalogue
    try:
        async with (
            httpx.AsyncClient(transport=lookup_transport) as lookup_client,
        ):
            for candidate in catalogue.candidates:
                route = _provisional_route(candidate, catalogue.catalogue_observed_at.date())
                candidate_failed = False
                for stage in _selected_stages(limits):
                    if candidate_failed:
                        journal.failure(
                            candidate.id, stage, state="skipped", code="candidate-failed"
                        )
                        continue
                    prompt, output_type, _prompt_version = prompts[stage]

                    async with httpx2.AsyncClient(
                        transport=request_transport,
                        event_hooks={
                            "request": [_request_capture(journal, candidate.id, stage, route)]
                        },
                    ) as request_client:
                        model = _QualificationModel(
                            GatewayChatModel(route, gateway_config, http_client=request_client),
                            candidate_id=candidate.id,
                            stage=stage,
                            route=route,
                            journal=journal,
                            lookup_client=lookup_client,
                            gateway_config=gateway_config,
                            limits=limits,
                            sleep=sleep,
                        )
                        progress_processor = topic_source_progress_processor(stage)
                        agent = Agent(
                            model,
                            output_type=NativeOutput(output_type, strict=True),
                            retries=0,
                            tools=topic_source_qualification_tools(stage),
                            model_settings={"max_tokens": limits.max_output_tokens},
                            capabilities=(
                                [ProcessHistory(progress_processor)]
                                if progress_processor is not None
                                else []
                            ),
                        )
                        try:
                            result = await agent.run(prompt)
                        except UnexpectedModelBehavior:
                            journal.validation(
                                candidate.id,
                                stage,
                                passed=False,
                                code="strict-output-invalid",
                            )
                            candidate_failed = False
                        except ModelHTTPError:
                            candidate_failed = False
                        else:
                            try:
                                _validate_grounding(stage, result.output, limits.suite)
                            except ValueError:
                                journal.validation(
                                    candidate.id,
                                    stage,
                                    passed=False,
                                    code="grounding-invalid",
                                )
                                candidate_failed = False
                            else:
                                journal.validation(
                                    candidate.id,
                                    stage,
                                    passed=True,
                                    code="strict-output-and-grounding-valid",
                                )
    except (QualificationHalt, GenerationIdentityError) as error:
        journal.value["status"] = "halted"
        journal.value["haltCode"] = (
            "generation-identity-mismatch"
            if isinstance(error, GenerationIdentityError)
            else "qualification-halt"
        )
        journal.save()
    except BaseException:
        journal.value["status"] = "halted"
        journal.value.setdefault("haltCode", "unexpected-driver-failure")
        journal.save()
        raise
    finally:
        journal.finish()
        _private_replace(report_path, journal.value)
    return journal.value


async def reconcile_journal(
    *,
    journal_path: Path,
    expected_sha256: str,
    api_key: str,
    report_path: Path,
    lookup_transport: httpx.AsyncBaseTransport | None = None,
    gateway: GatewayName | None = None,
) -> dict[str, Any]:
    """Read generation state for durable handles without making a model request."""
    body = _bounded_read(journal_path, MAX_JOURNAL_BYTES)
    if len(body) > MAX_JOURNAL_BYTES:
        raise QualificationRefusal("qualification journal exceeds 4 MiB")
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise QualificationRefusal("qualification journal SHA-256 differs from the acknowledgment")
    raw = cast("object", json.loads(body))
    if not isinstance(raw, dict):
        raise QualificationRefusal("qualification journal is not a JSON object")
    value = cast("dict[str, Any]", raw)
    _validate_reconciliation_journal(value)
    catalogue = CandidateCatalogue.model_validate_json(
        canonical_json(value["catalogue"]), strict=True
    )
    candidates = {row.id: row for row in catalogue.candidates}
    selected_gateway = candidate_gateway(catalogue.candidates[0])
    if gateway is not None and gateway != selected_gateway:
        raise QualificationRefusal("selected gateway differs from journal transport")
    results: list[dict[str, Any]] = []
    config = GatewayConfig(api_key=api_key, gateway=selected_gateway)
    async with httpx.AsyncClient(transport=lookup_transport) as client:
        for call in cast("list[dict[str, Any]]", value["calls"]):
            generation_id = call.get("generationId")
            if call.get("state") not in {
                "response_saved",
                "cost_reported",
                "cost_unresolved",
                "outcome_unknown",
                "request_sent",
            } or not isinstance(generation_id, str):
                continue
            if call.get("state") == "cost_reported":
                results.append(
                    {
                        "candidateId": call["candidateId"],
                        "stage": call["stage"],
                        "generationId": generation_id,
                        "observation": call.get("cost"),
                        "validationStatus": "unknown",
                    }
                )
                continue
            candidate = candidates[str(call["candidateId"])]
            route = _provisional_route(candidate, catalogue.catalogue_observed_at.date())
            observation = await lookup_generation(
                client, config=config, route=route, generation_id=generation_id
            )
            results.append(
                {
                    "candidateId": call["candidateId"],
                    "stage": call["stage"],
                    "generationId": generation_id,
                    "observation": observation.model_dump(mode="json"),
                }
            )
    report = {
        "format": "temnia-gateway-reconciliation/1",
        "observedAt": _now(),
        "journalSha256": expected_sha256,
        "dispatchCount": 0,
        "results": results,
    }
    if report_path.exists():
        raise QualificationRefusal("reconciliation report already exists")
    _private_create(report_path, report)
    return report


def _validate_reconciliation_journal(value: dict[str, Any]) -> None:
    if value.get("format") != "temnia-gateway-qualification/1":
        raise QualificationRefusal("qualification journal format is invalid")
    try:
        catalogue = CandidateCatalogue.model_validate_json(
            canonical_json(value["catalogue"]), strict=True
        )
        limits = QualificationLimits.model_validate_json(
            canonical_json(value["limits"]), strict=True
        )
    except (KeyError, ValueError) as error:
        raise QualificationRefusal("qualification journal metadata is invalid") from error
    if value.get("suite") != limits.suite:
        raise QualificationRefusal("qualification journal suite is inconsistent")
    expected_prompt_versions = {
        stage: prompt[2] for stage, prompt in qualification_prompts(limits.suite).items()
    }
    calls_value = value.get("calls")
    if not isinstance(calls_value, list):
        raise QualificationRefusal("qualification journal call list is invalid")
    calls = cast("list[object]", calls_value)
    expected = {
        (candidate.id, stage)
        for candidate in catalogue.candidates
        for stage in _selected_stages(limits)
    }
    observed: set[tuple[str, str]] = set()
    dispatched = 0
    allowed_states = {
        "planned",
        "admitted",
        "request_sent",
        "response_saved",
        "cost_reported",
        "cost_unresolved",
        "identity_failed",
        "known_failure",
        "outcome_unknown",
        "passed",
        "failed",
        "skipped",
    }
    for item in calls:
        if not isinstance(item, dict):
            raise QualificationRefusal("qualification journal call is invalid")
        call = cast("dict[str, object]", item)
        identity = (str(call.get("candidateId")), str(call.get("stage")))
        state = str(call.get("state"))
        if identity not in expected or identity in observed or state not in allowed_states:
            raise QualificationRefusal("qualification journal call identity or state is invalid")
        if (
            call.get("suite") != limits.suite
            or call.get("promptVersion") != expected_prompt_versions[identity[1]]
            or call.get("schemaVersion") != _schema_version(identity[1], limits)
        ):
            raise QualificationRefusal("qualification journal call metadata is invalid")
        observed.add(identity)
        if state not in {"planned", "skipped"}:
            dispatched += 1
        generation_id = call.get("generationId")
        if generation_id is not None and (not isinstance(generation_id, str) or not generation_id):
            raise QualificationRefusal("qualification journal generation identity is invalid")
    if observed != expected:
        raise QualificationRefusal("qualification journal call plan is incomplete")
    if value.get("dispatchCount") != dispatched or dispatched > limits.max_dispatches:
        raise QualificationRefusal("qualification journal dispatch count is invalid")
