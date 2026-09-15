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

from temnia_pipeline.harness.gateway_policy import (
    GatewayName,
    GatewayTransportPolicy,
)

AdmissionVersion = Literal["admission/1", "admission/2"]

MAX_REQUEST_PAYLOAD_BYTES = 512 * 1024
TOKENS_PER_PRICE_UNIT = 1_000_000
PROTOCOL_OVERHEAD_BYTES = 8192
# Two families per production pool: the reviewer must come from outside the author's family
# (enforced at selection), and one fallback family per seat is what a vendor outage needs.
# The 2026-09-07 rule of three families and an open-weight candidate per pool was reversed
# for the primary tier on 2026-09-15 (frontier models first; AGENTS.md).
MIN_PRODUCTION_FAMILIES = 2
UNPROVEN_ROUTE_PREFIX = "qualification-unproven:"

VendorName = Literal["anthropic", "openai", "google"]

# Admission arithmetic. `admission/1` counted one serialized byte as one token, which
# admitted Karma's 90 KB prompt as ~100k tokens against ~25k real ones and refused any
# source much longer than it before a model ever saw the request. `admission/2` declares
# a floor of two bytes per token instead: no tokenizer in use here emits more than one
# token per two UTF-8 bytes of prompt for Latin text (~4 bytes/token), Devanagari
# (~2.5 bytes/token at worst) or CJK (~3 bytes/token as UTF-8), so the floor still
# reserves more than any real request spends. It is a declared bound, not a measurement,
# and it changes only the reservation arithmetic: the 512 KiB serialized payload cap is
# unchanged, and beyond that cap a request is still refused outright.
ADMISSION_VERSION_LEGACY: AdmissionVersion = "admission/1"
ADMISSION_VERSION: AdmissionVersion = "admission/2"
BYTES_PER_TOKEN_FLOOR = 2

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


class VendorAccountTerms(BaseModel):
    """The vendor account's data terms, recorded once per route rather than probed per endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    zero_data_retention: bool
    recorded_at: date
    reference: Annotated[str, Field(min_length=1, max_length=512)]


class RouteEntry(BaseModel):
    """One provider endpoint for one exact model id: a direct vendor, or a legacy gateway alias."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Annotated[str, Field(min_length=1, max_length=128)]
    # For a vendor route this is the vendor's own model id; the field keeps its historical
    # name until the deletion step renames the snapshot schema.
    gateway_model: Annotated[str, Field(min_length=1, max_length=256)]
    family: Annotated[str, Field(min_length=1, max_length=128)]
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    open_weight: bool
    context_tokens: Annotated[int, Field(gt=0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    eligibility: RouteEligibility | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prices: RoutePrices
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None
    cache_enabled: bool = False
    vendor: VendorName | None = Field(default=None, exclude_if=lambda value: value is None)
    account: VendorAccountTerms | None = Field(default=None, exclude_if=lambda value: value is None)
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
        if self.vendor is not None:
            return self._vendor_options()
        if self.account is not None:
            raise ValueError("account terms belong to a direct vendor route")
        if self.eligibility is None:
            raise ValueError("a gateway route requires its qualification evidence")
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

    def _vendor_options(self) -> Self:
        """A direct vendor route: the vendor's SDK is the transport and the account is the terms."""
        if self.provider != self.vendor:
            raise ValueError("a direct vendor route's provider is the vendor itself")
        if (
            self.transport is not None
            or self.provider_accounting_name is not None
            or self.accounting_model is not None
        ):
            raise ValueError("a direct vendor route carries no gateway transport or accounting")
        if self.eligibility is not None:
            raise ValueError("a direct vendor route records account terms, not a gateway probe")
        if self.account is None:
            raise ValueError("a direct vendor route requires its account terms")
        # The account's retention terms are recorded, not enforced here: a vendor grants zero
        # data retention per account on request, and the worker's boot log names every route
        # still waiting for it. Sources are the operator's own recordings until Temnia is live.
        if self.id.casefold() == "default":
            raise ValueError("a route may not be named default")
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
        if len({route.vendor is not None for route in self.routes}) > 1:
            raise ValueError("one snapshot uses either direct vendor routes or gateway routes")
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
            if (
                not self.synthetic
                and len({route.family for route in candidates}) < MIN_PRODUCTION_FAMILIES
            ):
                raise ValueError("production seat pools require at least two model families")
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

    @property
    def direct(self) -> bool:
        """Whether every route calls its vendor directly (no gateway on the path)."""
        return all(route.vendor is not None for route in self.routes)

    def vendors(self) -> frozenset[VendorName]:
        """The vendors this snapshot's routes call; empty for a gateway snapshot."""
        return frozenset(route.vendor for route in self.routes if route.vendor is not None)


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


ProviderPath = GatewayName | Literal["direct"]


def snapshot_gateway(snapshot: RouteSnapshot) -> ProviderPath:
    """Resolve the one provider path required by this immutable worker snapshot."""
    if snapshot.direct:
        return "direct"
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
    excluded_vendors: frozenset[str] = frozenset(),
) -> RouteEntry:
    """Select by frozen order after applying explicit family and vendor exclusions.

    A vendor is excluded when the worker holds no key for it; the refusal names the variable
    an operator has to set, so a missing secret is a sentence on the run, not a boot failure
    of the worker that also serves ingest and transcription.
    """
    pool = snapshot.seats.get(seat)
    if pool is None:
        raise NoEligibleRoute(f"seat {seat!r} is absent from the route snapshot")
    candidates: list[RouteEntry] = []
    keyless: list[RouteEntry] = []
    for route_id in pool.route_ids:
        route = snapshot.route(route_id)
        if route.family in excluded_families:
            continue
        if route.vendor is not None and route.vendor in excluded_vendors:
            keyless.append(route)
            continue
        candidates.append(route)
    if candidate_index < 0 or candidate_index >= len(candidates):
        if keyless and candidate_index >= len(candidates):
            missing = ", ".join(sorted({vendor_key_variable(r.vendor) for r in keyless}))
            raise NoEligibleRoute(
                f"seat {seat!r} has no eligible candidate at index {candidate_index}; "
                f"{len(keyless)} route(s) are skipped because {missing} is not set on the "
                "pipeline service"
            )
        raise NoEligibleRoute(f"seat {seat!r} has no eligible candidate at index {candidate_index}")
    return candidates[candidate_index]


VENDOR_KEY_VARIABLES: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


def vendor_key_variable(vendor: str | None) -> str:
    """The environment variable that carries one vendor's API key."""
    if vendor is None or vendor not in VENDOR_KEY_VARIABLES:
        raise ValueError(f"unknown vendor {vendor!r}")
    return VENDOR_KEY_VARIABLES[vendor]


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
    expected_output_tokens: int | None = None,
) -> CostEstimate:
    """Reserve a bound without a token-count service call.

    Input tokens follow `ADMISSION_VERSION`: the serialized payload divided by the
    declared bytes-per-token floor, rounded up, plus the protocol overhead allowance.
    The context check always uses the full output cap; the amount uses
    `expected_output_tokens` when given (a vendor route settles from usage, so it
    reserves the typical answer rather than the whole allowance) and the cap otherwise.
    """
    if payload_bytes < 0 or protocol_overhead_bytes < 0:
        raise ValueError("payload and protocol overhead must be nonnegative")
    if payload_bytes > MAX_REQUEST_PAYLOAD_BYTES:
        raise ContextWindowExceeded("serialized prompt and schema exceed 512 KiB")
    if max_output_tokens is not None and (
        max_output_tokens <= 0 or max_output_tokens > route.max_output_tokens
    ):
        raise ValueError("requested output cap must fit the qualified route maximum")
    input_tokens = (
        payload_bytes + BYTES_PER_TOKEN_FLOOR - 1
    ) // BYTES_PER_TOKEN_FLOOR + protocol_overhead_bytes
    output_tokens = max_output_tokens or route.max_output_tokens
    if input_tokens + output_tokens > route.context_tokens:
        # The refusal names what an operator must change: the source, the cap or the route.
        raise ContextWindowExceeded(
            f"Route {route.id} cannot accept this request: {payload_bytes} payload bytes "
            f"(about {input_tokens} estimated input tokens) plus {output_tokens} output tokens "
            f"exceed its {route.context_tokens}-token context window."
        )
    reserved_output = output_tokens
    if expected_output_tokens is not None:
        if expected_output_tokens <= 0 or expected_output_tokens > output_tokens:
            raise ValueError("expected output must be positive and within the output cap")
        reserved_output = expected_output_tokens
    numerator = input_tokens * route.prices.input + reserved_output * route.prices.output
    amount = (
        numerator + TOKENS_PER_PRICE_UNIT - 1
    ) // TOKENS_PER_PRICE_UNIT + route.prices.request_surcharge
    return CostEstimate(
        payload_bytes=payload_bytes,
        input_tokens=input_tokens,
        output_tokens=reserved_output,
        amount_micros=amount,
    )
