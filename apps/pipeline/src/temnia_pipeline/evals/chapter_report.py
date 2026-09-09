"""Deterministic chapter quality, review, and all-attempt economics reports."""

# Report code intentionally spells out unknown denominators and provenance.
# ruff: noqa: TC003

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Literal

from temnia_pipeline.contracts import Kind, ReviewState, Status
from temnia_pipeline.evals.chapters import (
    SHA256,
    TOKEN_USAGE_FIELDS,
    AttemptFact,
    EvaluationBundle,
    EvaluationModel,
    HumanLabels,
    HumanReviewTasks,
    build_review_tasks,
    required_check_names,
    validate_bundle,
    validate_labels,
)


class BoundaryMetrics(EvaluationModel):
    """Human-grounded boundary outcomes; null means not measured."""

    label_count: int
    accepted_count: int | None
    rejected_count: int | None
    acceptance_rate: float | None
    adjustment_count: int | None
    mean_adjustment_ms: float | None
    max_adjustment_ms: int | None
    correction_active_seconds: int | None
    correction_measurement: Literal["measured", "not_measured"]


class MoneyMetrics(EvaluationModel):
    """Known spend and unresolved exposure across every physical dispatch."""

    physical_attempt_count: int
    live_dispatch_count: int
    synthetic_attempt_count: int
    replayed_attempt_count: int
    known_spent_micros: int
    active_reserved_exposure_micros: int
    missing_cost_count: int
    missing_response_count: int
    latency_count: int
    total_latency_ms: int | None
    cost_provenance_counts: dict[str, int]
    usage_totals: dict[str, int]
    usage_unknown_fields: tuple[str, ...]
    cost_per_accepted_chapter_micros: int | None
    cost_per_source_hour_micros: int | None
    live_cost_comparison_status: Literal["complete", "incomplete", "not_applicable"]
    live_cost_comparison_reasons: tuple[str, ...]
    attempts: tuple[AttemptFact, ...]


class SummaryGroundingMetrics(EvaluationModel):
    """Source-reference outcomes; these do not measure factual or editorial quality."""

    report_count: int
    summary_unit_count: int
    first_pass_reference_valid_report_count: int
    first_pass_reference_valid_unit_count: int
    extractive_fallback_report_count: int
    extractive_fallback_unit_count: int
    rejected_quote_anchor_count: int


class ChapterEvaluationReport(EvaluationModel):
    """Machine-readable report paired with a human-readable CLI table."""

    format: Literal["temnia-chapter-evaluation-report/1"]
    generated_at: datetime
    source_id: str
    source_fingerprint: SHA256 | None
    run_id: str
    split: Literal["tuning", "test", "qualification"]
    status: str
    duration_ms: int
    evidence_sha256: SHA256 | None
    edit_sha256: SHA256 | None
    render_descriptor_sha256: SHA256 | None
    editorial_verification_sha256: SHA256 | None
    current_revision: int
    accepted_revision: int | None
    exact_cover_violations: tuple[str, ...] | None
    keep_duration_ms: int | None
    drop_duration_ms: int | None
    chapter_count: int | None
    chapter_density_per_hour: float | None
    accepted_chapter_count: int | None
    technical_failure_count: int | None
    technical_warning_count: int | None
    technical_statuses: dict[str, int]
    editorial_statuses: dict[str, int]
    editorial_verifier_families: tuple[str, ...]
    editorial_verdict_modality: Literal["code_and_model", "code_only", "not_run"]
    review_event_counts: dict[str, int]
    review_nudge_count: int
    event_elapsed_span_seconds: float | None
    boundary_metrics: BoundaryMetrics
    summary_grounding: SummaryGroundingMetrics | None = None
    money: MoneyMetrics
    provenance: dict[str, object]
    human_review_tasks: HumanReviewTasks | None
    provisional_compiler_weights: bool
    comparison_winner: None = None
    comparison_status: Literal["incomplete"] = "incomplete"
    comparison_reasons: tuple[str, ...]


def _money(
    bundle: EvaluationBundle,
    *,
    accepted_chapter_count: int | None,
) -> MoneyMetrics:
    live = [
        attempt for attempt in bundle.attempts if not attempt.synthetic and not attempt.replayed
    ]
    synthetic = [attempt for attempt in bundle.attempts if attempt.synthetic]
    replayed = [attempt for attempt in bundle.attempts if attempt.replayed]
    known = [attempt for attempt in live if attempt.actual_cost_micros is not None]
    unresolved = [attempt for attempt in live if attempt.actual_cost_micros is None]
    known_spent = sum(attempt.actual_cost_micros or 0 for attempt in known)
    exposure = sum(
        attempt.estimated_cost_micros
        for attempt in live
        if attempt.reservation_active and attempt.actual_cost_micros is None
    )
    latencies = [
        int((attempt.finished_at - attempt.dispatched_at).total_seconds() * 1000)
        for attempt in bundle.attempts
        if attempt.finished_at is not None and attempt.dispatched_at is not None
    ]
    provenance = Counter(attempt.cost_status for attempt in bundle.attempts)
    usage_keys = sorted(
        {key for attempt in bundle.attempts for key in attempt.usage} & TOKEN_USAGE_FIELDS
    )
    usage_totals: dict[str, int] = {}
    unknown_usage: list[str] = []
    for key in usage_keys:
        values = [attempt.usage.get(key) for attempt in bundle.attempts]
        numeric = [
            value for value in values if isinstance(value, int) and not isinstance(value, bool)
        ]
        if numeric:
            usage_totals[key] = sum(numeric)
        if any(value is None for value in values):
            unknown_usage.append(key)
    for canonical, aliases in (
        ("input_tokens", ("input_tokens", "inputTokens")),
        ("output_tokens", ("output_tokens", "outputTokens")),
    ):
        if any(
            not any(
                alias in attempt.usage and attempt.usage[alias] is not None for alias in aliases
            )
            for attempt in bundle.attempts
        ):
            unknown_usage.append(canonical)

    reasons: list[str] = []
    if unresolved:
        reasons.append("one_or_more_live_attempt_costs_unknown")
    if any(attempt.state in {"outcome_unknown", "dispatching", "running"} for attempt in live):
        reasons.append("one_or_more_live_attempt_outcomes_unresolved")
    if not live:
        status: Literal["complete", "incomplete", "not_applicable"] = "not_applicable"
        reasons.append("no_live_provider_attempts")
    elif reasons:
        status = "incomplete"
    else:
        status = "complete"
    complete_cost = status == "complete"
    per_chapter = (
        known_spent // accepted_chapter_count
        if complete_cost and accepted_chapter_count is not None and accepted_chapter_count > 0
        else None
    )
    per_hour = (
        (known_spent * 3_600_000 + bundle.duration_ms - 1) // bundle.duration_ms
        if complete_cost
        else None
    )
    return MoneyMetrics(
        physical_attempt_count=len(bundle.attempts),
        live_dispatch_count=sum(attempt.dispatched_at is not None for attempt in live),
        synthetic_attempt_count=len(synthetic),
        replayed_attempt_count=len(replayed),
        known_spent_micros=known_spent,
        active_reserved_exposure_micros=exposure,
        missing_cost_count=len(unresolved),
        missing_response_count=sum(not attempt.response_present for attempt in live),
        latency_count=len(latencies),
        total_latency_ms=sum(latencies) if latencies else None,
        cost_provenance_counts=dict(sorted(provenance.items())),
        usage_totals=usage_totals,
        usage_unknown_fields=tuple(dict.fromkeys(unknown_usage)),
        cost_per_accepted_chapter_micros=per_chapter,
        cost_per_source_hour_micros=per_hour,
        live_cost_comparison_status=status,
        live_cost_comparison_reasons=tuple(reasons),
        attempts=bundle.attempts,
    )


def _boundary_metrics(bundle: EvaluationBundle, labels: HumanLabels | None) -> BoundaryMetrics:
    if labels is None:
        return BoundaryMetrics(
            label_count=0,
            accepted_count=None,
            rejected_count=None,
            acceptance_rate=None,
            adjustment_count=None,
            mean_adjustment_ms=None,
            max_adjustment_ms=None,
            correction_active_seconds=None,
            correction_measurement="not_measured",
        )
    validate_labels(bundle, labels)
    if bundle.edit is None:
        message = "validated labels require an edit"
        raise AssertionError(message)
    boundary_times = {boundary.id: boundary.timeMs for boundary in bundle.edit.boundaries}
    adjustments = [
        abs(label.preferred_time_ms - boundary_times[label.boundary_id])
        for label in labels.labels
        if label.preferred_time_ms is not None
    ]
    correction_values = [
        label.correction_active_seconds
        for label in labels.labels
        if label.correction_active_seconds is not None
    ]
    accepted = sum(label.accepted for label in labels.labels)
    return BoundaryMetrics(
        label_count=len(labels.labels),
        accepted_count=accepted,
        rejected_count=len(labels.labels) - accepted,
        acceptance_rate=accepted / len(labels.labels),
        adjustment_count=len(adjustments),
        mean_adjustment_ms=sum(adjustments) / len(adjustments) if adjustments else None,
        max_adjustment_ms=max(adjustments) if adjustments else None,
        correction_active_seconds=(
            sum(correction_values) if len(correction_values) == len(labels.labels) else None
        ),
        correction_measurement=(
            "measured" if len(correction_values) == len(labels.labels) else "not_measured"
        ),
    )


def _accepted_chapter_count(bundle: EvaluationBundle) -> int | None:
    """Return a denominator only for a fully accepted and technically checked revision."""
    if (
        bundle.edit is None
        or bundle.accepted_revision != bundle.current_revision
        or bundle.renders is None
    ):
        return None
    if any(section.reviewState != ReviewState.accepted for section in bundle.edit.sections):
        return None
    kept = {section.id for section in bundle.edit.sections if section.kind == Kind.keep}
    checks = {artifact.section_id: artifact for artifact in bundle.checks}
    if set(checks) != kept:
        return None
    required_names = required_check_names(bundle)
    for artifact in checks.values():
        statuses = {check.name: check.status for check in artifact.checks.technicalChecks}
        if any(name not in statuses for name in required_names):
            return None
        if any(statuses[name] == Status.fail for name in required_names):
            return None
    return len(kept)


def _summary_grounding(bundle: EvaluationBundle) -> SummaryGroundingMetrics | None:
    reports = bundle.summary_grounding
    if not reports:
        return None
    unit_count = sum(len(item.body.normalizedSummary.units) for item in reports)
    fallback_count = sum(len(item.body.fallbacks) for item in reports)
    fallback_reports = sum(bool(item.body.fallbacks) for item in reports)
    return SummaryGroundingMetrics(
        report_count=len(reports),
        summary_unit_count=unit_count,
        first_pass_reference_valid_report_count=len(reports) - fallback_reports,
        first_pass_reference_valid_unit_count=unit_count - fallback_count,
        extractive_fallback_report_count=fallback_reports,
        extractive_fallback_unit_count=fallback_count,
        rejected_quote_anchor_count=sum(
            len(fallback.rejectedQuoteWordIds)
            for item in reports
            for fallback in item.body.fallbacks
        ),
    )


def build_report(
    bundle: EvaluationBundle,
    labels: HumanLabels | None = None,
) -> ChapterEvaluationReport:
    """Validate all inputs before computing any metric or economic comparison."""
    validate_bundle(bundle)
    if labels is not None:
        validate_labels(bundle, labels)
    edit = bundle.edit
    if edit is None:
        keep_duration = None
        chapter_count = None
    else:
        durations = [
            edit.boundaries[index + 1].timeMs - edit.boundaries[index].timeMs
            for index in range(len(edit.sections))
        ]
        keep_duration = sum(
            duration
            for duration, section in zip(durations, edit.sections, strict=True)
            if section.kind == Kind.keep
        )
        chapter_count = sum(section.kind == Kind.keep for section in edit.sections)
    accepted_count = _accepted_chapter_count(bundle)
    technical = [check for artifact in bundle.checks for check in artifact.checks.technicalChecks]
    technical_status = Counter(check.status.value for check in technical)
    verification = bundle.editorial_verification
    if verification is not None:
        editorial_status = Counter((verification.body.verdict.status,))
        verifier_families = (verification.verifier_family,)
        modality: Literal["code_and_model", "code_only", "not_run"] = "code_and_model"
    else:
        editorial_status = Counter(
            artifact.checks.editorialStatus.value for artifact in bundle.checks
        )
        verifier_families = tuple(
            sorted(
                {
                    artifact.checks.verifierFamily
                    for artifact in bundle.checks
                    if artifact.checks.verifierFamily is not None
                }
            )
        )
        modality = "code_only" if bundle.checks else "not_run"
    event_counts = Counter(event.action for event in bundle.review_events)
    event_span = None
    if len(bundle.review_events) >= 2:  # noqa: PLR2004
        times = [event.created_at for event in bundle.review_events]
        event_span = (max(times) - min(times)).total_seconds()
    boundary_metrics = _boundary_metrics(bundle, labels)
    money = _money(bundle, accepted_chapter_count=accepted_count)
    comparison_reasons = list(money.live_cost_comparison_reasons)
    if bundle.evidence is None:
        comparison_reasons.append("evidence_not_available")
    if edit is None:
        comparison_reasons.append("chapter_edit_not_available")
    if labels is None:
        comparison_reasons.append("human_labels_not_supplied")
    if accepted_count is None or accepted_count == 0:
        comparison_reasons.append("accepted_chapter_denominator_unavailable")
    comparison_reasons.append("single_candidate_report_has_no_winner")
    return ChapterEvaluationReport(
        format="temnia-chapter-evaluation-report/1",
        generated_at=bundle.observed_at,
        source_id=str(bundle.source_id),
        source_fingerprint=bundle.source_fingerprint,
        run_id=str(bundle.run_id),
        split=bundle.split,
        status=bundle.status,
        duration_ms=bundle.duration_ms,
        evidence_sha256=bundle.evidence_sha256,
        edit_sha256=bundle.edit_sha256,
        render_descriptor_sha256=bundle.render_descriptor_sha256,
        editorial_verification_sha256=(
            verification.artifact.sha256 if verification is not None else None
        ),
        current_revision=bundle.current_revision,
        accepted_revision=bundle.accepted_revision,
        exact_cover_violations=() if edit is not None else None,
        keep_duration_ms=keep_duration,
        drop_duration_ms=(
            bundle.duration_ms - keep_duration if keep_duration is not None else None
        ),
        chapter_count=chapter_count,
        chapter_density_per_hour=(
            chapter_count * 3_600_000 / bundle.duration_ms if chapter_count is not None else None
        ),
        accepted_chapter_count=accepted_count,
        technical_failure_count=(technical_status[Status.fail.value] if edit is not None else None),
        technical_warning_count=(technical_status[Status.warn.value] if edit is not None else None),
        technical_statuses=dict(sorted(technical_status.items())),
        editorial_statuses=dict(sorted(editorial_status.items())),
        editorial_verifier_families=verifier_families,
        editorial_verdict_modality=modality,
        review_event_counts=dict(sorted(event_counts.items())),
        review_nudge_count=event_counts["nudge"],
        event_elapsed_span_seconds=event_span,
        boundary_metrics=boundary_metrics,
        summary_grounding=_summary_grounding(bundle),
        money=money,
        provenance=bundle.provenance.model_dump(mode="json", by_alias=True),
        human_review_tasks=build_review_tasks(bundle) if edit is not None else None,
        provisional_compiler_weights=(
            bundle.provenance.compiler_calibration_artifact_sha256 is None
        ),
        comparison_reasons=tuple(dict.fromkeys(comparison_reasons)),
    )


def readable_table(report: ChapterEvaluationReport) -> str:
    """Render a compact deterministic table without hiding unknown values."""
    source = report.source_fingerprint[:12] if report.source_fingerprint else "unmeasured"
    grounding = report.summary_grounding
    rows: tuple[tuple[str, str], ...] = (
        ("source", source),
        ("revision", f"{report.current_revision} (accepted {report.accepted_revision or 'none'})"),
        ("chapters", str(report.chapter_count)),
        ("keep / drop ms", f"{report.keep_duration_ms} / {report.drop_duration_ms}"),
        (
            "technical fail / warn",
            f"{report.technical_failure_count} / {report.technical_warning_count}",
        ),
        ("human labels", str(report.boundary_metrics.label_count)),
        (
            "summary refs first pass / fallback",
            (
                f"{grounding.first_pass_reference_valid_unit_count} / "
                f"{grounding.extractive_fallback_unit_count}"
                if grounding is not None
                else "not measured"
            ),
        ),
        ("live attempts", str(report.money.live_dispatch_count)),
        ("known spend micros", str(report.money.known_spent_micros)),
        ("active exposure micros", str(report.money.active_reserved_exposure_micros)),
        ("comparison", report.comparison_status),
    )
    width = max(len(label) for label, _value in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)
