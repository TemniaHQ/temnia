"""Explicit worker-only configuration for the topic harness: one file per deployment, or env."""

# Boot refusals name the exact invalid variable at the validation site.
# ruff: noqa: EM101, EM102, FBT001, FBT002, PLR0912, TRY003

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from temnia_pipeline.contracts import Backend, ChapterRunConfig, HarnessConfig
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
# Every recorded output the standalone-topic program can ask for, so a fixture that is
# missing one fails at settings load rather than mid-run.
RECORDED_TOPIC_OUTPUTS = (
    "topic_opportunity_inventory",
    "topic_selection_author_v3",
    "topic_selection_cold",
    "topic_selection_source",
    "topic_selection_source_selected_v3",
    "topic_selection_patch",
)
MAX_RECORDED_FIXTURE_BYTES = 1024 * 1024
# The deployed images bake this to their committed configuration file; an empty value
# (the gate, local development, the experiment operator) means the environment is the
# configuration, as before.
CONFIG_PATH_VARIABLE = "HARNESS_CONFIG_PATH"
SECRET_VARIABLES = frozenset({"OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY"})
log = logging.getLogger("temnia.harness.settings")


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
    topic_shot_detector: TopicShotDetector = "scdet"
    config_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HarnessSettings:
        """Parse process configuration without choosing a model vendor fallback."""
        values = os.environ if env is None else env
        config_path = values.get(CONFIG_PATH_VARIABLE) or None
        if config_path:
            return cls.from_file(Path(config_path), values)
        raw_backend = values.get("HARNESS_BACKEND") or None
        if raw_backend not in {None, "gateway", "recorded"}:
            raise ValueError("HARNESS_BACKEND must be gateway or recorded")
        raw_gateway = values.get("HARNESS_GATEWAY", "vercel")
        if raw_gateway not in {"vercel", "openrouter"}:
            raise ValueError("HARNESS_GATEWAY must be vercel or openrouter")
        raw_shot_detector = values.get("HARNESS_TOPIC_SHOT_DETECTOR", "scdet")
        if raw_shot_detector not in {"pyscenedetect-adaptive", "scdet"}:
            raise ValueError("HARNESS_TOPIC_SHOT_DETECTOR must be pyscenedetect-adaptive or scdet")
        snapshot_path = values.get("HARNESS_ROUTE_SNAPSHOT_PATH") or None
        return cls(
            topic_shot_detector=cast("TopicShotDetector", raw_shot_detector),
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

    @classmethod
    def from_file(cls, path: Path, env: Mapping[str, str]) -> HarnessSettings:
        """Load the committed deployment file; the environment then supplies only secrets."""
        config = HarnessConfig.model_validate_json(path.read_bytes())
        ignored = sorted(
            name for name in env if name.startswith("HARNESS_") and name != CONFIG_PATH_VARIABLE
        )
        if ignored:
            log.warning(
                "%s=%s is the harness configuration; ignoring environment entries %s",
                CONFIG_PATH_VARIABLE,
                path,
                ", ".join(ignored),
            )
        base = path.resolve().parent

        def resolve(value: str) -> Path:
            candidate = Path(value)
            return candidate if candidate.is_absolute() else base / candidate

        gateway = cast("GatewayName", str(config.gateway))
        return cls(
            enabled=config.enabled,
            backend=cast("HarnessBackend", str(config.backend)),
            route_snapshot_id=config.routeSnapshot.id,
            route_snapshot_path=resolve(config.routeSnapshot.path),
            allow_recorded=config.allowRecorded,
            max_run_budget_micros=config.limits.maxRunBudgetMicros,
            max_dispatches=config.limits.maxDispatches,
            max_repairs=config.limits.maxRepairs,
            max_output_tokens=config.limits.maxOutputTokens,
            evidence_window_sentences=config.limits.evidenceWindowSentences,
            max_render_concurrency=config.limits.maxRenderConcurrency,
            gateway=gateway,
            gateway_api_key=env.get(
                "OPENROUTER_API_KEY" if gateway == "openrouter" else "AI_GATEWAY_API_KEY"
            )
            or None,
            recorded_fixture_path=(
                resolve(config.recordedFixturePath) if config.recordedFixturePath else None
            ),
            topic_shot_detector=cast("TopicShotDetector", str(config.topicShotDetector)),
            config_path=path,
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
            required_outputs = RECORDED_TOPIC_OUTPUTS
            if not isinstance(outputs, dict) or not all(
                isinstance(payload := cast("dict[object, object]", outputs).get(stage), dict)
                and cast("dict[object, object]", payload).get("synthetic") is True
                and "output" in payload
                for stage in required_outputs
            ):
                raise RuntimeError(
                    "recorded harness fixture requires an explicit synthetic output for "
                    "every standalone-topic stage"
                )
        config = self.allowed_config()
        if self.max_run_budget_micros <= 0:
            raise RuntimeError("HARNESS_MAX_RUN_BUDGET_MICROS must be positive")
        snapshot = load_route_snapshot(self.route_snapshot_path)
        if snapshot.snapshot_id != config.routeSnapshotId:
            raise RuntimeError("loaded route snapshot hash differs from the configured snapshot ID")
        if self.backend == "recorded" and not snapshot.synthetic:
            raise RuntimeError("recorded backend requires a visibly synthetic route snapshot")
        if self.backend == "gateway" and snapshot.synthetic:
            raise RuntimeError("gateway backend requires a production route snapshot")
        if self.backend == "gateway" and snapshot_gateway(snapshot) != self.gateway:
            raise RuntimeError("loaded route transport differs from HARNESS_GATEWAY")
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
                    max_output_tokens=effective_topic_output_tokens(self.max_output_tokens, route),
                )
            except (ContextWindowExceeded, ValueError) as error:
                raise RuntimeError(
                    f"route {route.id!r} cannot honor "
                    f"HARNESS_MAX_OUTPUT_TOKENS={self.max_output_tokens} with protocol headroom"
                ) from error
        return snapshot
