"""Immutable request-keyed model response record and replay."""

# Domain exception names and refusal-site messages are part of this persistence API.
# ruff: noqa: EM101, EM102, N818, TC002, TC003, TRY003

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from temnia_pipeline.harness.gateway_policy import GatewayTransportPolicy  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Mapping

MODEL_RESPONSE_ADAPTER = TypeAdapter(ModelResponse)
MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])
REQUEST_PARAMETERS_ADAPTER = TypeAdapter(ModelRequestParameters)


class CassetteError(RuntimeError):
    """Base class for deterministic cassette failures."""


class CassetteMiss(CassetteError):
    """Replay has no record for the exact request identity."""


class CassetteConflict(CassetteError):
    """An immutable cassette key already contains different facts."""


class CassetteMode(StrEnum):
    """Whether a wrapper delegates, records, or replays."""

    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class CassetteMetadata(BaseModel):
    """Non-secret version and route facts bound into a cassette request key."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    route_id: Annotated[str, Field(min_length=1)]
    stage: Annotated[str, Field(min_length=1)]
    schema_version: Annotated[str, Field(min_length=1)]
    prompt_version: Annotated[str, Field(min_length=1)]
    program_version: Annotated[str, Field(min_length=1)]
    synthetic: bool = False
    transport: GatewayTransportPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    provider_accounting_name: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    accounting_model: str | None = Field(default=None, exclude_if=lambda value: value is None)


class CassetteEnvelope(BaseModel):
    """One immutable serialized model response and its request identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    metadata: CassetteMetadata
    response: dict[str, Any]


_INCIDENTAL_MESSAGE_FIELDS = {"conversation_id", "run_id", "state", "timestamp"}


def _without_incidental_message_fields(value: object) -> object:
    """Strip PydanticAI envelope metadata without touching semantic nested content."""
    messages = cast("list[dict[str, object]]", value)
    cleaned: list[dict[str, object]] = []
    for message in messages:
        copied = {
            key: item for key, item in message.items() if key not in _INCIDENTAL_MESSAGE_FIELDS
        }
        parts = copied.get("parts")
        if isinstance(parts, list):
            copied["parts"] = [
                {
                    key: item
                    for key, item in cast("dict[str, object]", part).items()
                    if key != "timestamp"
                }
                if isinstance(part, dict)
                else part
                for part in cast("list[object]", parts)
            ]
        cleaned.append(copied)
    return cleaned


def request_payload(
    messages: list[ModelMessage],
    model_settings: ModelSettings | None,
    model_request_parameters: ModelRequestParameters,
    metadata: CassetteMetadata,
) -> bytes:
    """Canonical request bytes with incidental local message timestamps removed."""
    value = {
        "messages": _without_incidental_message_fields(
            MODEL_MESSAGES_ADAPTER.dump_python(messages, mode="json", by_alias=True)
        ),
        "metadata": metadata.model_dump(mode="json"),
        "parameters": REQUEST_PARAMETERS_ADAPTER.dump_python(
            model_request_parameters, mode="json", by_alias=True
        ),
        "settings": dict(model_settings or {}),
    }
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def request_fingerprint(
    messages: list[ModelMessage],
    model_settings: ModelSettings | None,
    model_request_parameters: ModelRequestParameters,
    metadata: CassetteMetadata,
) -> tuple[str, int]:
    """Return the canonical request hash and serialized byte count."""
    payload = request_payload(messages, model_settings, model_request_parameters, metadata)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def request_payload_bytes(
    messages: list[ModelMessage],
    model_settings: ModelSettings | None,
    model_request_parameters: ModelRequestParameters,
    metadata: CassetteMetadata,
) -> int:
    """Return only the canonical serialized size, for callers that never hash it."""
    return len(request_payload(messages, model_settings, model_request_parameters, metadata))


class CassetteStore:
    """Filesystem cassette store using exclusive immutable creation."""

    def __init__(self, root: Path, *, allow_synthetic: bool = False) -> None:
        self.root = root
        self.allow_synthetic = allow_synthetic

    def _path(self, request_hash: str) -> Path:
        return self.root / request_hash[:2] / f"{request_hash}.json"

    def load(self, request_hash: str, expected: CassetteMetadata) -> ModelResponse:
        """Load and validate the exact response; miss/corruption/mismatch are loud."""
        path = self._path(request_hash)
        try:
            envelope = CassetteEnvelope.model_validate_json(path.read_bytes(), strict=True)
        except FileNotFoundError as error:
            raise CassetteMiss(f"no cassette exists for request {request_hash}") from error
        except (ValueError, TypeError) as error:
            raise CassetteError(f"cassette {request_hash} is corrupt") from error
        if envelope.request_hash != request_hash or envelope.metadata != expected:
            raise CassetteConflict("cassette metadata does not match the replay request")
        if envelope.metadata.synthetic and not self.allow_synthetic:
            raise CassetteError("synthetic cassette replay requires explicit enablement")
        return MODEL_RESPONSE_ADAPTER.validate_json(
            json.dumps(envelope.response, allow_nan=False), strict=True
        )

    def record(
        self,
        request_hash: str,
        metadata: CassetteMetadata,
        response: ModelResponse,
    ) -> None:
        """Create one immutable envelope or verify an identical prior recording."""
        if metadata.synthetic and not self.allow_synthetic:
            raise CassetteError("synthetic cassette recording requires explicit enablement")
        envelope = CassetteEnvelope(
            version=1,
            request_hash=request_hash,
            metadata=metadata,
            response=cast(
                "dict[str, Any]",
                MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True),
            ),
        )
        body = json.dumps(
            envelope.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        path = self._path(request_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(body)
        except FileExistsError:
            if path.read_bytes() != body:
                raise CassetteConflict(
                    "cassette request key already has a different response"
                ) from None


class CassetteModel(WrapperModel):
    """Replay or record a full PydanticAI ``ModelResponse`` around any model."""

    def __init__(
        self,
        wrapped: Model,
        *,
        store: CassetteStore,
        mode: CassetteMode,
        metadata: CassetteMetadata,
    ) -> None:
        super().__init__(wrapped)
        self.store = store
        self.mode = mode
        self.metadata = metadata

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Delegate at most once, or replay the exact full response."""
        request_hash, _ = request_fingerprint(
            messages, model_settings, model_request_parameters, self.metadata
        )
        if self.mode == CassetteMode.REPLAY:
            return self.store.load(request_hash, self.metadata)
        response = await super().request(messages, model_settings, model_request_parameters)
        if self.mode == CassetteMode.RECORD:
            self.store.record(request_hash, self.metadata, response)
        return response


def synthetic_function_model(payload: Mapping[str, Any]) -> FunctionModel:
    """Build a deterministic stand-in only from an explicit synthetic fixture payload."""
    if payload.get("synthetic") is not True or "output" not in payload:
        raise CassetteError("synthetic fixtures require synthetic=true and an explicit output")
    output = payload["output"]
    response_id = payload.get("providerResponseId")
    if response_id is not None and not isinstance(response_id, str):
        raise CassetteError("synthetic providerResponseId must be a string")

    async def stand_in(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        output,
                        allow_nan=False,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            ],
            model_name="synthetic:explicit-fixture",
            provider_name="synthetic",
            provider_response_id=response_id,
            metadata={"synthetic": True, "measured": False},
        )

    return FunctionModel(stand_in, model_name="synthetic:explicit-fixture")
