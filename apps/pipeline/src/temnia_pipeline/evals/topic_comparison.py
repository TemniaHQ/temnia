"""Paired full-source comparisons; factor mismatches remain visible."""

# ruff: noqa: C901, EM101, PLR0912, PLR0915, TC003, TRY003

from __future__ import annotations

import random
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import Field

from temnia_pipeline.evals.chapters import EvaluationModel
from temnia_pipeline.evals.topic_report import TopicQualityReport, build_topic_report
from temnia_pipeline.evals.topics import Identifier, TopicEvaluationBundle, TopicHumanLabels, digest


class ComparisonInput(EvaluationModel):
    """Frozen bundle and label identities before comparison."""

    bundle: str
    labels: str | None = None
    expected_bundle_sha256: str
    expected_labels_sha256: str | None = None


class TopicComparisonManifest(EvaluationModel):
    """Predeclared intended factors and source-paired evaluation inputs."""

    format: Literal["temnia-topic-comparison/1"] = "temnia-topic-comparison/1"
    experiment_id: Identifier
    mode: Literal["model_swap", "configuration", "fixed_candidate_reviewer"]
    baseline_configuration_id: Identifier
    allowed_changed_factors: tuple[
        Literal[
            "author_identity",
            "reviewer_identity",
            "program_identity",
            "prompt_identity",
            "schema_identity",
            "media_identity",
            "execution_identity",
        ],
        ...,
    ]
    inputs: tuple[ComparisonInput, ...] = Field(min_length=2)
    primary_metrics: tuple[
        Literal[
            "editorialPrecision",
            "usefulOpportunityRecall",
            "endToEndAcceptedYield",
            "endToEndAcceptedOpportunityRecall",
            "deliveryYield",
            "reviewerFalseAcceptance",
            "invalidAmongApprovals",
            "reviewerFalseRejection",
        ],
        ...,
    ] = ("editorialPrecision", "usefulOpportunityRecall")
    rationale: Identifier


class TopicComparisonReport(EvaluationModel):
    """Observed paired differences, never an automatic model winner."""

    format: Literal["temnia-topic-comparison-report/1"] = "temnia-topic-comparison-report/1"
    experiment_id: str
    mode: str
    comparable: bool
    reasons: tuple[str, ...]
    reports: tuple[TopicQualityReport, ...]
    paired_deltas: dict[str, dict[str, dict[str, float | int | list[float] | None]]]
    production_qualification_ready: bool
    winner: None = None


def _uncertainty(values: list[float]) -> dict[str, float | int | list[float] | None]:
    """Bootstrap episodes, never the correlated candidates within each episode."""
    if len(values) < 2:  # noqa: PLR2004
        return {
            "pairedEpisodes": len(values),
            "meanDelta": mean(values) if values else None,
            "interval95": None,
        }
    generator = random.Random(0)  # noqa: S311
    samples = sorted(mean(generator.choices(values, k=len(values))) for _ in range(2000))
    return {
        "pairedEpisodes": len(values),
        "meanDelta": mean(values),
        "interval95": [samples[49], samples[1949]],
    }


def compare_topics(manifest: TopicComparisonManifest, *, directory: Path) -> TopicComparisonReport:
    """Validate identity and episode pairing before computing descriptive differences."""
    bundles: list[TopicEvaluationBundle] = []
    reports: list[TopicQualityReport] = []
    reasons: list[str] = []
    seen: set[tuple[str, str]] = set()
    splits: dict[str, str] = {}
    for item in manifest.inputs:
        bundle = TopicEvaluationBundle.model_validate_json(
            (directory / item.bundle).read_bytes(), strict=True
        )
        labels = (
            TopicHumanLabels.model_validate_json(
                (directory / item.labels).read_bytes(), strict=True
            )
            if item.labels
            else None
        )
        if digest(bundle.model_dump(mode="json", by_alias=True)) != item.expected_bundle_sha256:
            raise ValueError("comparison bundle differs from frozen manifest")
        if (
            digest(labels.model_dump(mode="json", by_alias=True)) if labels else None
        ) != item.expected_labels_sha256:
            raise ValueError("comparison labels differ from frozen manifest")
        key = bundle.recording_group, bundle.configuration.configuration_id
        if key in seen:
            raise ValueError(
                "duplicate source/configuration pair; aggregate repeated trials explicitly"
            )
        seen.add(key)
        if bundle.recording_group in splits and splits[bundle.recording_group] != bundle.split:
            raise ValueError("one recording crosses development and held-out splits")
        splits[bundle.recording_group] = bundle.split
        bundles.append(bundle)
        reports.append(build_topic_report(bundle, labels))
    by_key = {
        (bundle.recording_group, bundle.configuration.configuration_id): (bundle, report)
        for bundle, report in zip(bundles, reports, strict=True)
    }
    configs = {bundle.configuration.configuration_id for bundle in bundles}
    if manifest.baseline_configuration_id not in configs:
        raise ValueError("comparison baseline configuration is absent")
    if manifest.mode == "model_swap" and set(manifest.allowed_changed_factors) != {
        "author_identity"
    }:
        raise ValueError("model-swap comparison changes author identity only")
    if manifest.mode == "fixed_candidate_reviewer" and set(manifest.allowed_changed_factors) != {
        "reviewer_identity"
    }:
        raise ValueError("reviewer calibration changes reviewer identity only")
    factors = {
        "author_identity",
        "reviewer_identity",
        "program_identity",
        "prompt_identity",
        "schema_identity",
        "media_identity",
        "execution_identity",
    }
    deltas: dict[str, dict[str, dict[str, float | int | list[float] | None]]] = {}
    for configuration_id in sorted(configs - {manifest.baseline_configuration_id}):
        values: dict[str, list[float]] = {name: [] for name in manifest.primary_metrics}
        for group in sorted(splits):
            baseline_pair = by_key.get((group, manifest.baseline_configuration_id))
            candidate_pair = by_key.get((group, configuration_id))
            if baseline_pair is None or candidate_pair is None:
                reasons.append(f"unpaired_source:{group}:{configuration_id}")
                continue
            baseline, baseline_report = baseline_pair
            candidate, candidate_report = candidate_pair
            if (
                baseline.source_id,
                baseline.evidence_sha256,
                baseline.source_fingerprint,
                baseline.duration_ms,
            ) != (
                candidate.source_id,
                candidate.evidence_sha256,
                candidate.source_fingerprint,
                candidate.duration_ms,
            ):
                reasons.append(f"different_source_evidence:{group}:{configuration_id}")
            for field in ("source_sha256", "transcript_sha256", "rubric_sha256"):
                left, right = (
                    getattr(baseline.configuration, field),
                    getattr(candidate.configuration, field),
                )
                if left is None or left != right:
                    reasons.append(f"unmatched_identity:{field}:{group}:{configuration_id}")
            for field in factors:
                left, right = (
                    getattr(baseline.configuration, field),
                    getattr(candidate.configuration, field),
                )
                if not left or not right:
                    reasons.append(f"unobserved_factor:{field}:{group}:{configuration_id}")
                elif field not in manifest.allowed_changed_factors and left != right:
                    reasons.append(f"changed_fixed_factor:{field}:{group}:{configuration_id}")
            if (
                manifest.mode == "fixed_candidate_reviewer"
                and baseline.final_candidate_sha256s != candidate.final_candidate_sha256s
            ):
                reasons.append(f"reviewer_inputs_differ:{group}:{configuration_id}")
            for name, samples in values.items():
                left, right = baseline_report.rates[name].value, candidate_report.rates[name].value
                if left is not None and right is not None:
                    samples.append(right - left)
                else:
                    reasons.append(f"unmeasured_metric:{name}:{group}:{configuration_id}")
        deltas[configuration_id] = {name: _uncertainty(rows) for name, rows in values.items()}
    qualification = not reasons and all(
        report.qualification_complete
        and report.split == "held_out"
        and report.counts["severeFidelityDefects"] == 0
        for report in reports
    )
    return TopicComparisonReport(
        experiment_id=manifest.experiment_id,
        mode=manifest.mode,
        comparable=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        reports=tuple(reports),
        paired_deltas=deltas,
        production_qualification_ready=qualification,
    )
