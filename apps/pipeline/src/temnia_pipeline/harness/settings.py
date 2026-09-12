"""Explicit worker-only configuration for the chapter harness."""

# Boot refusals name the exact invalid variable at the validation site.
# ruff: noqa: EM101, EM102, FBT001, FBT002, PLR0912, TRY003

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from temnia_pipeline.chapter_llama.client import ChapterLlamaConfig
from temnia_pipeline.contracts import Backend, ChapterRunConfig
from temnia_pipeline.harness.routes import (
    ContextWindowExceeded,
    RouteSnapshot,
    estimate_cost,
    load_route_snapshot,
    snapshot_gateway,
)
from temnia_pipeline.harness.topic_selection_runtime import effective_topic_output_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from temnia_pipeline.harness.gateway_policy import GatewayName

HarnessBackend = Literal["gateway", "recorded"]
TopicShotDetector = Literal["pyscenedetect-adaptive", "scdet"]
DEFAULT_MAX_RUN_BUDGET_MICROS = 10_000_000
REQUIRED_ROUTE_SEATS = frozenset({"propose", "summary", "verify"})
MAX_RECORDED_FIXTURE_BYTES = 1024 * 1024


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "1" if default else "0")
    if raw not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return raw == "1"


def _positive(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    """Harness switches and caps read once at worker boot."""

    enabled: bool
    backend: HarnessBackend | None
    route_snapshot_id: str | None
    route_snapshot_path: Path | None
    allow_recorded: bool
    max_run_budget_micros: int
    max_dispatches: int
    max_repairs: int
    max_output_tokens: int
    evidence_window_sentences: int
    max_render_concurrency: int
    gateway_api_key: str | None
    gateway: GatewayName = "vercel"
    recorded_fixture_path: Path | None = None
    chapter_llama_config: ChapterLlamaConfig | None = None
    topic_shot_detector: TopicShotDetector = "pyscenedetect-adaptive"
    topic_selection_enabled: bool = False
    topic_selection_v3_enabled: bool = False
    topic_selection_qualification_path: Path | None = None
    topic_selection_v3_qualification_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HarnessSettings:
        """Parse process configuration without choosing a model vendor fallback."""
        values = os.environ if env is None else env
        raw_backend = values.get("HARNESS_BACKEND") or None
        if raw_backend not in {None, "gateway", "recorded"}:
            raise ValueError("HARNESS_BACKEND must be gateway or recorded")
        raw_gateway = values.get("HARNESS_GATEWAY", "vercel")
        if raw_gateway not in {"vercel", "openrouter"}:
            raise ValueError("HARNESS_GATEWAY must be vercel or openrouter")
        raw_shot_detector = values.get("HARNESS_TOPIC_SHOT_DETECTOR", "pyscenedetect-adaptive")
        if raw_shot_detector not in {"pyscenedetect-adaptive", "scdet"}:
            raise ValueError("HARNESS_TOPIC_SHOT_DETECTOR must be pyscenedetect-adaptive or scdet")
        snapshot_path = values.get("HARNESS_ROUTE_SNAPSHOT_PATH") or None
        return cls(
            topic_selection_enabled=_flag(values, "HARNESS_TOPIC_SELECTION_ENABLED"),
            topic_selection_v3_enabled=_flag(values, "HARNESS_TOPIC_SELECTION_V3_ENABLED"),
            topic_selection_qualification_path=(
                Path(values["HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH"])
                if values.get("HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH")
                else None
            ),
            topic_selection_v3_qualification_path=(
                Path(values["HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH"])
                if values.get("HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH")
                else None
            ),
            topic_shot_detector=cast("TopicShotDetector", raw_shot_detector),
            chapter_llama_config=(
                ChapterLlamaConfig.model_validate_json(values["HARNESS_CHAPTER_LLAMA_CONFIG_JSON"])
                if values.get("HARNESS_CHAPTER_LLAMA_CONFIG_JSON")
                else None
            ),
            enabled=_flag(values, "HARNESS_ENABLED"),
            backend=cast("HarnessBackend | None", raw_backend),
            route_snapshot_id=values.get("HARNESS_ROUTE_SNAPSHOT_ID") or None,
            route_snapshot_path=Path(snapshot_path) if snapshot_path else None,
            allow_recorded=_flag(values, "HARNESS_ALLOW_RECORDED"),
            max_run_budget_micros=_positive(
                values,
                "HARNESS_MAX_RUN_BUDGET_MICROS",
                DEFAULT_MAX_RUN_BUDGET_MICROS,
            ),
            max_dispatches=_positive(values, "HARNESS_MAX_DISPATCHES", 32),
            max_repairs=int(values.get("HARNESS_MAX_REPAIRS", "3")),
            max_output_tokens=_positive(values, "HARNESS_MAX_OUTPUT_TOKENS", 8192),
            evidence_window_sentences=_positive(values, "HARNESS_EVIDENCE_WINDOW_SENTENCES", 80),
            max_render_concurrency=_positive(values, "HARNESS_MAX_RENDER_CONCURRENCY", 2),
            gateway=cast("GatewayName", raw_gateway),
            gateway_api_key=values.get(
                "OPENROUTER_API_KEY" if raw_gateway == "openrouter" else "AI_GATEWAY_API_KEY"
            )
            or None,
            recorded_fixture_path=(
                Path(values["HARNESS_RECORDED_FIXTURE_PATH"])
                if values.get("HARNESS_RECORDED_FIXTURE_PATH")
                else None
            ),
        )

    def allowed_config(self) -> ChapterRunConfig:
        """Return the exact server-visible run configuration this worker accepts."""
        if self.backend is None or self.route_snapshot_id is None:
            raise RuntimeError("enabled harness settings are incomplete")
        return ChapterRunConfig(
            backend=Backend(self.backend),
            evidenceWindowSentences=self.evidence_window_sentences,
            maxDispatches=self.max_dispatches,
            maxOutputTokens=self.max_output_tokens,
            maxRenderConcurrency=self.max_render_concurrency,
            maxRepairs=self.max_repairs,
            routeSnapshotId=self.route_snapshot_id,
        )

    def validate_boot(self) -> RouteSnapshot | None:  # noqa: C901, PLR0915
        """Fail enabled workers loudly and leave disabled legacy workers untouched."""
        if not self.enabled:
            return None
        if self.chapter_llama_config is not None and self.backend != "gateway":
            raise RuntimeError("Chapter-Llama compute requires the explicit gateway backend")
        if self.backend is None:
            raise RuntimeError("HARNESS_BACKEND is required when HARNESS_ENABLED=1")
        if self.route_snapshot_id is None or self.route_snapshot_path is None:
            raise RuntimeError(
                "HARNESS_ROUTE_SNAPSHOT_ID and HARNESS_ROUTE_SNAPSHOT_PATH are required"
            )
        if self.backend == "gateway" and self.gateway_api_key is None:
            key_name = (
                "OPENROUTER_API_KEY" if self.gateway == "openrouter" else "AI_GATEWAY_API_KEY"
            )
            raise RuntimeError(f"{key_name} is required for the selected gateway backend")
        if self.backend == "recorded" and not self.allow_recorded:
            raise RuntimeError("recorded backend requires HARNESS_ALLOW_RECORDED=1")
        if self.backend == "recorded" and self.recorded_fixture_path is None:
            raise RuntimeError("recorded backend requires HARNESS_RECORDED_FIXTURE_PATH")
        if self.backend == "recorded":
            if self.recorded_fixture_path is None:  # narrowed by the refusal above
                raise RuntimeError("recorded backend requires HARNESS_RECORDED_FIXTURE_PATH")
            try:
                fixture_body = self.recorded_fixture_path.read_bytes()
                fixture = cast("object", json.loads(fixture_body))
            except (OSError, ValueError) as error:
                raise RuntimeError("recorded harness fixture is absent or invalid JSON") from error
            if len(fixture_body) > MAX_RECORDED_FIXTURE_BYTES:
                raise RuntimeError("recorded harness fixture exceeds 1 MiB")
            if (
                not isinstance(fixture, dict)
                or cast("dict[object, object]", fixture).get("synthetic") is not True
            ):
                raise RuntimeError("recorded harness fixture must declare synthetic=true")
            outputs = cast("dict[object, object]", fixture).get("outputs")
            required_outputs = ("propose", "summary", "verify")
            if not isinstance(outputs, dict) or not all(
                isinstance(payload := cast("dict[object, object]", outputs).get(stage), dict)
                and cast("dict[object, object]", payload).get("synthetic") is True
                and "output" in payload
                for stage in required_outputs
            ):
                raise RuntimeError(
                    "recorded harness fixture requires explicit synthetic propose, summary, "
                    "and verify outputs"
                )
        config = self.allowed_config()
        if self.max_run_budget_micros <= 0:
            raise RuntimeError("HARNESS_MAX_RUN_BUDGET_MICROS must be positive")
        snapshot = load_route_snapshot(self.route_snapshot_path)
        if snapshot.snapshot_id != config.routeSnapshotId:
            raise RuntimeError("loaded route snapshot hash differs from HARNESS_ROUTE_SNAPSHOT_ID")
        if self.backend == "recorded" and not snapshot.synthetic:
            raise RuntimeError("recorded backend requires a visibly synthetic route snapshot")
        if self.backend == "gateway" and snapshot.synthetic:
            raise RuntimeError("gateway backend requires a production route snapshot")
        if self.backend == "gateway" and snapshot_gateway(snapshot) != self.gateway:
            raise RuntimeError("loaded route transport differs from HARNESS_GATEWAY")
        if (
            self.topic_selection_enabled or self.topic_selection_v3_enabled
        ) and self.backend == "gateway":
            # Qualification imports native schemas; defer until settings/route loading finishes.
            from temnia_pipeline.harness.qualification_topic_selection import (  # noqa: PLC0415
                validate_topic_selection_qualification,
            )

            if self.topic_selection_enabled:
                if self.topic_selection_qualification_path is None:
                    raise RuntimeError(
                        "HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH is required for v2"
                    )
                validate_topic_selection_qualification(
                    snapshot,
                    self.topic_selection_qualification_path,
                    max_output_tokens=self.max_output_tokens,
                )
            if (
                self.topic_selection_v3_enabled
                and self.topic_selection_v3_qualification_path is None
            ):
                raise RuntimeError(
                    "HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH is required for v3"
                )
            if self.topic_selection_v3_enabled:
                qualification = self.topic_selection_v3_qualification_path
                if qualification is None:
                    raise RuntimeError(
                        "HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH is required for v3"
                    )
                validate_topic_selection_qualification(
                    snapshot,
                    qualification,
                    max_output_tokens=self.max_output_tokens,
                    program_version="standalone-topics/3",
                )
        missing_seats = REQUIRED_ROUTE_SEATS - snapshot.seats.keys()
        if missing_seats:
            raise RuntimeError(
                "route snapshot is missing required harness seats: "
                f"{', '.join(sorted(missing_seats))}"
            )
        required_route_ids = {
            route_id for seat in REQUIRED_ROUTE_SEATS for route_id in snapshot.seats[seat].route_ids
        }
        for route in snapshot.routes:
            if route.id not in required_route_ids:
                continue
            try:
                estimate_cost(
                    route,
                    payload_bytes=1,
                    max_output_tokens=(
                        effective_topic_output_tokens(self.max_output_tokens, route)
                        if self.topic_selection_enabled
                        else self.max_output_tokens
                    ),
                )
            except (ContextWindowExceeded, ValueError) as error:
                raise RuntimeError(
                    f"route {route.id!r} cannot honor "
                    f"HARNESS_MAX_OUTPUT_TOKENS={self.max_output_tokens} with protocol headroom"
                ) from error
        return snapshot
