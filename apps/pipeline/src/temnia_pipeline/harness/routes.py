"""Frozen, vendor-neutral route snapshots and conservative cost bounds."""

# Domain exception names and refusal-site messages are part of this routing API.
# ruff: noqa: C901, EM101, EM102, N818, TC003, TRY003

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from temnia_pipeline.harness.gateway_policy import (  # noqa: TC001
    GatewayName,
    GatewayTransportPolicy,
)

MAX_REQUEST_PAYLOAD_BYTES = 512 * 1024
TOKENS_PER_PRICE_UNIT = 1_000_000
PROTOCOL_OVERHEAD_BYTES = 8192
MIN_PRODUCTION_FAMILIES = 3
UNPROVEN_ROUTE_PREFIX = "qualification-unproven:"

ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
ServiceTier = Literal["auto", "default", "flex", "priority"]


class RouteError(RuntimeError):
    """Base class for frozen-route refusals."""


class ContextWindowExceeded(RouteError):
    """A bounded request cannot fit the qualified route context."""


class NoEligibleRoute(RouteError):
    """The frozen seat pool has no candidate satisfying the family policy."""


class RouteEligibility(BaseModel):
    """Evidence that qualified a route for harness use."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    zero_data_retention: bool
    strict_json_schema: bool
    cache_qualified: bool = False
    probe_artifact_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    probed_at: date


class RoutePrices(BaseModel):
    """Conservative maxima, all in micro-dollars per one million tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit: Literal["micros_per_million_tokens"] = "micros_per_million_tokens"
    input: Annotated[int, Field(ge=0)]
    output: Annotated[int, Field(ge=0)]
    cache_read: Annotated[int | None, Field(ge=0)] = None
    cache_write: Annotated[int | None, Field(ge=0)] = None
    request_surcharge: Annotated[int, Field(ge=0)] = 0


class RouteEntry(BaseModel):
    """One qualified provider endpoint for one exact gateway model alias."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Annotated[str, Field(min_length=1, max_length=128)]
    gateway_model: Annotated[str, Field(min_length=1, max_length=256)]
    family: Annotated[str, Field(min_length=1, max_length=128)]
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    open_weight: bool
    context_tokens: Annotated[int, Field(gt=0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    eligibility: RouteEligibility
    prices: RoutePrices
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None
    cache_enabled: bool = False
    transport: GatewayTransportPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    provider_accounting_name: Annotated[str | None, Field(min_length=1, max_length=128)] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    accounting_model: Annotated[str | None, Field(min_length=1, max_length=256)] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _qualified_options(self) -> Self:
        if self.transport is not None and self.transport.gateway == "openrouter":
            if self.provider_accounting_name is None:
                raise ValueError(
                    "OpenRouter routes require their observed accounting provider name"
                )
        elif self.provider_accounting_name is not None:
            raise ValueError("accounting provider names require an explicit OpenRouter transport")
        canonical_accounting = (
            self.transport is not None
            and self.transport.gateway == "openrouter"
            and self.transport.version == "gateway-transport/2"
        )
        if canonical_accounting != (self.accounting_model is not None):
            raise ValueError(
                "OpenRouter transport version 2 requires a frozen accounting model; "
                "earlier transports preserve request-model accounting"
            )
        if self.id.casefold() == "default":
            raise ValueError("a route may not be named default")
        if not self.eligibility.zero_data_retention or not self.eligibility.strict_json_schema:
            raise ValueError("every harness route must be qualified for ZDR and strict JSON schema")
        if self.cache_enabled and not self.eligibility.cache_qualified:
            raise ValueError("caching may be enabled only by an explicit qualified probe")
        if self.max_output_tokens > self.context_tokens:
            raise ValueError("max output exceeds the route context window")
        return self


class SeatRoutePool(BaseModel):
    """Ordered frozen candidate IDs for one typed harness seat."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    route_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class RouteSnapshot(BaseModel):
    """Immutable route catalogue and ordered pools captured on a harness run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    snapshot_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    signature: str | None = None
    synthetic: bool = False
    routes: Annotated[tuple[RouteEntry, ...], Field(min_length=1)]
    seats: dict[str, SeatRoutePool]

    @model_validator(mode="after")
    def _consistent_catalogue(self) -> Self:
        by_id = {route.id: route for route in self.routes}
        if any(route.id.startswith(UNPROVEN_ROUTE_PREFIX) for route in self.routes):
            raise ValueError("unproven qualification routes cannot enter a route snapshot")
        if len(by_id) != len(self.routes):
            raise ValueError("route IDs must be unique")
        aliases: dict[str, tuple[str, bool]] = {}
        for route in self.routes:
            identity = route.family, route.open_weight
            previous = aliases.setdefault(route.gateway_model, identity)
            if previous != identity:
                raise ValueError("one gateway model alias has inconsistent family declarations")
        for seat, pool in self.seats.items():
            if not seat or len(set(pool.route_ids)) != len(pool.route_ids):
                raise ValueError("seat names and ordered route IDs must be unique")
            missing = set(pool.route_ids) - by_id.keys()
            if missing:
                raise ValueError("seat pool names an absent route")
            candidates = [by_id[route_id] for route_id in pool.route_ids]
            if not self.synthetic:
                if len({route.family for route in candidates}) < MIN_PRODUCTION_FAMILIES:
                    raise ValueError("production seat pools require at least three model families")
                if not any(route.open_weight for route in candidates):
                    raise ValueError("production seat pools require an open-weight candidate")
        if self.snapshot_id != self.computed_id():
            raise ValueError("route snapshot ID does not match its immutable content")
        return self

    def computed_id(self) -> str:
        """Hash full canonical content except the signature and declared ID."""
        content = self.model_dump(mode="json", exclude={"snapshot_id", "signature"})
        body = json.dumps(
            content, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(body).hexdigest()

    def route(self, route_id: str) -> RouteEntry:
        """Resolve an exact route ID from the frozen catalogue."""
        for route in self.routes:
            if route.id == route_id:
                return route
        raise NoEligibleRoute(f"route {route_id!r} is absent from snapshot {self.snapshot_id}")


class CostEstimate(BaseModel):
    """Conservative reservation inputs derived only from a qualified route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    payload_bytes: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    amount_micros: Annotated[int, Field(ge=0)]


def load_route_snapshot(path: Path) -> RouteSnapshot:
    """Load one strict immutable JSON snapshot; absence or corruption is loud."""
    return RouteSnapshot.model_validate_json(path.read_bytes(), strict=True)


def snapshot_gateway(snapshot: RouteSnapshot) -> GatewayName:
    """Resolve the one process gateway required by this immutable worker snapshot."""
    gateways = {
        route.transport.gateway if route.transport is not None else "vercel"
        for route in snapshot.routes
    }
    if len(gateways) != 1:
        raise ValueError("one worker snapshot must use one gateway")
    return "openrouter" if "openrouter" in gateways else "vercel"


def select_route(
    snapshot: RouteSnapshot,
    seat: str,
    *,
    candidate_index: int = 0,
    excluded_families: frozenset[str] = frozenset(),
) -> RouteEntry:
    """Select by frozen order after applying explicit family exclusions."""
    pool = snapshot.seats.get(seat)
    if pool is None:
        raise NoEligibleRoute(f"seat {seat!r} is absent from the route snapshot")
    candidates: list[RouteEntry] = []
    for route_id in pool.route_ids:
        route = snapshot.route(route_id)
        if route.family not in excluded_families:
            candidates.append(route)
    if candidate_index < 0 or candidate_index >= len(candidates):
        raise NoEligibleRoute(f"seat {seat!r} has no eligible candidate at index {candidate_index}")
    return candidates[candidate_index]


def select_verifier_route(
    snapshot: RouteSnapshot,
    seat: str,
    *,
    generation_families: frozenset[str],
    candidate_index: int = 0,
) -> RouteEntry:
    """Choose a verifier whose explicit family differs from every generator."""
    return select_route(
        snapshot,
        seat,
        candidate_index=candidate_index,
        excluded_families=generation_families,
    )


def estimate_cost(
    route: RouteEntry,
    *,
    payload_bytes: int,
    protocol_overhead_bytes: int = PROTOCOL_OVERHEAD_BYTES,
    max_output_tokens: int | None = None,
) -> CostEstimate:
    """Reserve a conservative maximum without a token-count service call."""
    if payload_bytes < 0 or protocol_overhead_bytes < 0:
        raise ValueError("payload and protocol overhead must be nonnegative")
    if payload_bytes > MAX_REQUEST_PAYLOAD_BYTES:
        raise ContextWindowExceeded("serialized prompt and schema exceed 512 KiB")
    if max_output_tokens is not None and (
        max_output_tokens <= 0 or max_output_tokens > route.max_output_tokens
    ):
        raise ValueError("requested output cap must fit the qualified route maximum")
    input_tokens = payload_bytes + protocol_overhead_bytes
    output_tokens = max_output_tokens or route.max_output_tokens
    if input_tokens + output_tokens > route.context_tokens:
        raise ContextWindowExceeded("bounded request exceeds the qualified context window")
    numerator = input_tokens * route.prices.input + output_tokens * route.prices.output
    amount = (
        numerator + TOKENS_PER_PRICE_UNIT - 1
    ) // TOKENS_PER_PRICE_UNIT + route.prices.request_surcharge
    return CostEstimate(
        payload_bytes=payload_bytes,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        amount_micros=amount,
    )
