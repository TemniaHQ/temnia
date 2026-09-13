"""Deterministic selection quality and delivery reports with explicit denominators."""

# ruff: noqa: C901, PLR0912, PLR0915

from __future__ import annotations

from collections import Counter
from typing import Literal

from temnia_pipeline.contracts import ChapterChecks, ChapterRenders, TopicRenders
from temnia_pipeline.evals.common import EvaluationModel
from temnia_pipeline.evals.topics import (
    CandidateJudgment,
    TopicEvaluationBundle,
    TopicHumanLabels,
    artifact_model,
    candidate_index,
    digest,
    validate_topic_bundle,
    validate_topic_labels,
)


class Rate(EvaluationModel):
    """A measured fraction with explicit null semantics."""

    numerator: int | None
    denominator: int | None
    value: float | None
    missing_reason: str | None


def rate(numerator: int | None, denominator: int | None, reason: str = "zero_denominator") -> Rate:
    """Unknown measurements do not become zero successes or failures."""
    missing = numerator is None or denominator is None or denominator == 0
    value = numerator / denominator if numerator is not None and denominator else None
    return Rate(
        numerator=numerator,
        denominator=denominator,
        value=value,
        missing_reason=reason if missing else None,
    )


class TopicQualityReport(EvaluationModel):
    """Deterministic quality, delivery, labor and expense observations."""

    format: Literal["temnia-topic-evaluation-report/1"] = "temnia-topic-evaluation-report/1"
    bundle_sha256: str
    labels_sha256: str | None
    source_id: str
    recording_group: str
    split: str
    configuration_id: str
    mode: str
    status: str
    counts: dict[str, int]
    rates: dict[str, Rate]
    reviewer_confusion: dict[str, int]
    stage_losses: dict[str, int]
    stage_statuses: dict[str, str]
    money: dict[str, int | float | None]
    usage: dict[str, int]
    human_effort: dict[str, int | float | None]
    qualification_complete: bool
    limitations: tuple[str, ...]


def editorial_observation_complete(judgment: CandidateJudgment) -> bool:
    """A complete text encounter supports selection judgment, never publication readiness."""
    return (
        (judgment.full_selected_text_reviewed or judgment.full_playback)
        and judgment.source_fully_reviewed
        and not judgment.cold_prior_source_exposure
        and not judgment.cold_prior_variant_exposure
        and judgment.cold_annotator != judgment.source_annotator
        and judgment.editorial_changes != "unknown"
        and all(
            value != "unknown"
            for value in (
                judgment.audience_value,
                judgment.comprehension,
                judgment.completion,
                judgment.fidelity,
                judgment.title_faithful,
            )
        )
    )


def editorially_usable(judgment: CandidateJudgment) -> bool:
    """Use editorial evidence only; a broken render cannot make the discussion weak."""
    return (
        editorial_observation_complete(judgment)
        and judgment.editorial_changes == "none"
        and judgment.audience_value in {"strong", "adequate"}
        and all(
            value == "pass"
            for value in (
                judgment.comprehension,
                judgment.completion,
                judgment.fidelity,
                judgment.title_faithful,
            )
        )
    )


def media_observation_complete(judgment: CandidateJudgment, bundle: TopicEvaluationBundle) -> bool:
    """Full media qualification observes every known source modality on the exact output."""
    required: set[str] = set()
    if bundle.evidence is not None:
        if bundle.evidence.audioSampleRate is not None:
            required.add("audio")
        if bundle.evidence.frameRate is not None or bundle.evidence.videoTimeBase is not None:
            required.add("video")
    return (
        editorial_observation_complete(judgment)
        and bool(required)
        and required <= set(judgment.observed_modalities)
        and judgment.full_playback
        and judgment.edit_sha256 == bundle.final_edit_sha256
        and judgment.render_descriptor_sha256 is not None
        and judgment.render_descriptor_sha256 == bundle.final_renders_sha256
        and judgment.media_quality != "unknown"
        and judgment.decision != "unknown"
    )


def accepted_without_repair(judgment: CandidateJudgment, bundle: TopicEvaluationBundle) -> bool:
    """Publication acceptance requires both editorial usefulness and full media review."""
    return (
        editorially_usable(judgment)
        and media_observation_complete(judgment, bundle)
        and judgment.decision == "accept"
        and judgment.media_quality == "pass"
    )


def technically_delivered(bundle: TopicEvaluationBundle, renders: object) -> set[str]:
    """Only render references with retained passing technical checks count as delivered."""
    if not isinstance(renders, TopicRenders):
        return set()
    artifacts = {artifact.sha256: artifact for artifact in bundle.artifacts}
    result: set[str] = set()
    candidates = candidate_index(bundle)
    for video in renders.videos:
        descriptor_artifact = artifacts.get(video.descriptor.sha256)
        descriptor = artifact_model(descriptor_artifact) if descriptor_artifact else None
        if (
            not isinstance(descriptor, ChapterRenders)
            or not descriptor.renders
            or any(render.checks is None for render in descriptor.renders)
        ):
            continue
        checks = [
            artifact_model(artifacts[render.checks.sha256])
            for render in descriptor.renders
            if render.checks is not None
        ]
        if all(
            isinstance(check, ChapterChecks)
            and bool(check.technicalChecks)
            and all(item.status == "pass" for item in check.technicalChecks)
            for check in checks
        ):
            result.update(
                key
                for key in bundle.final_candidate_sha256s
                if candidates[key].id == video.candidateId
            )
    return result


def build_topic_report(
    bundle: TopicEvaluationBundle, labels: TopicHumanLabels | None = None
) -> TopicQualityReport:
    """Validate before counting; never turn absent observations into quality passes."""
    validate_topic_bundle(bundle)
    if labels is not None:
        validate_topic_labels(bundle, labels)
    final = set(bundle.final_candidate_sha256s)
    judgments = (
        [
            judgment
            for judgment in labels.judgments
            if judgment.candidate_sha256 in final
            and judgment.edit_sha256 in {None, bundle.final_edit_sha256}
            and judgment.render_descriptor_sha256 in {None, bundle.final_renders_sha256}
        ]
        if labels
        else []
    )
    evaluated = [judgment for judgment in judgments if editorial_observation_complete(judgment)]
    media_evaluated = [j for j in judgments if media_observation_complete(j, bundle)]
    renders_artifact = next(
        (
            artifact
            for artifact in bundle.artifacts
            if artifact.sha256 == bundle.final_renders_sha256
        ),
        None,
    )
    renders = artifact_model(renders_artifact) if renders_artifact is not None else None
    delivered = technically_delivered(bundle, renders)
    useful = {judgment.candidate_sha256 for judgment in evaluated if editorially_usable(judgment)}
    accepted = {
        judgment.candidate_sha256
        for judgment in media_evaluated
        if accepted_without_repair(judgment, bundle) and judgment.candidate_sha256 in delivered
    }
    independent = (
        [
            opportunity
            for opportunity in labels.opportunities
            if opportunity.origin == "human"
            and opportunity.independently_labeled
            and opportunity.source_fully_reviewed
        ]
        if labels
        else []
    )
    worthwhile = {opportunity.id for opportunity in independent if opportunity.worthwhile is True}
    covered: set[str] = (
        {
            match.opportunity_id
            for match in labels.matches
            if match.opportunity_id in worthwhile
            and match.candidate_sha256 in useful
            and match.independently_usable
            and match.mandatory_content_present
        }
        if labels
        else set()
    )
    accepted_covered: set[str] = (
        {
            match.opportunity_id
            for match in labels.matches
            if match.opportunity_id in worthwhile
            and match.candidate_sha256 in accepted
            and match.independently_usable
            and match.mandatory_content_present
        }
        if labels
        else set()
    )
    opportunities_known = (
        labels is not None
        and labels.opportunities_complete
        and labels.independent_source_review
        and len(independent) == len(labels.opportunities)
        and all(opportunity.worthwhile is not None for opportunity in independent)
    )
    rates = {
        "editorialPrecision": rate(
            len(useful), len(evaluated), "no_editorially_evaluated_candidates"
        ),
        "usefulOpportunityRecall": rate(
            len(covered) if opportunities_known and len(evaluated) == len(final) else None,
            len(worthwhile) if opportunities_known else None,
            "independent_opportunity_labels_incomplete"
            if not opportunities_known
            else "final_outputs_not_all_evaluated"
            if len(evaluated) != len(final)
            else "no_worthwhile_opportunities",
        ),
        "endToEndAcceptedOpportunityRecall": rate(
            len(accepted_covered)
            if opportunities_known and len(media_evaluated) == len(final)
            else None,
            len(worthwhile) if opportunities_known else None,
            "independent_opportunity_labels_incomplete"
            if not opportunities_known
            else "final_media_not_all_evaluated"
            if len(media_evaluated) != len(final)
            else "no_worthwhile_opportunities",
        ),
        "deliveryYield": rate(
            len(delivered)
            if any(
                stage.stage == "rendering" and stage.status != "not_run" for stage in bundle.stages
            )
            else None,
            len(final),
            "no_final_recommendations" if not final else "rendering_not_observed",
        ),
        "endToEndAcceptedYield": rate(
            len(accepted) if labels and len(media_evaluated) == len(final) else None,
            len(final),
            "human_labels_missing"
            if labels is None
            else "final_media_not_all_evaluated"
            if len(media_evaluated) != len(final)
            else "no_final_recommendations",
        ),
        "humanEvaluationCompletion": rate(len(evaluated), len(final), "no_final_recommendations"),
        "fullMediaEvaluationCompletion": rate(
            len(media_evaluated), len(final), "no_final_recommendations"
        ),
        "publicationAcceptance": rate(
            len(accepted), len(media_evaluated), "no_fully_evaluated_final_media"
        ),
        "completionDefects": rate(
            sum(j.completion == "fail" for j in evaluated),
            sum(j.completion != "unknown" for j in evaluated),
            "completion_not_judged",
        ),
        "coldComprehensionDefects": rate(
            sum(j.comprehension == "fail" for j in evaluated),
            sum(j.comprehension != "unknown" for j in evaluated),
            "comprehension_not_judged",
        ),
        "severeFidelityDefects": rate(
            sum(j.severe_fidelity_defect is True for j in evaluated),
            sum(j.severe_fidelity_defect is not None for j in evaluated),
            "fidelity_not_judged",
        ),
        "redundantCore": rate(
            sum(j.redundant_core is True for j in evaluated),
            sum(j.redundant_core is not None for j in evaluated),
            "redundancy_not_judged",
        ),
    }
    confusion: Counter[str] = Counter()
    for case in labels.reviewer_cases if labels else ():
        if case.human_valid is None or case.model_decision == "unknown":
            confusion["unknown"] += 1
        else:
            confusion[
                f"{'valid' if case.human_valid else 'invalid'}_"
                f"{'approved' if case.model_decision == 'pass' else 'rejected'}"
            ] += 1
    for key in (
        "valid_approved",
        "valid_rejected",
        "invalid_approved",
        "invalid_rejected",
        "unknown",
    ):
        confusion.setdefault(key, 0)
    rates.update(
        {
            "reviewerFalseAcceptance": rate(
                confusion["invalid_approved"],
                confusion["invalid_approved"] + confusion["invalid_rejected"],
                "no_human_invalid_controls",
            ),
            "invalidAmongApprovals": rate(
                confusion["invalid_approved"],
                confusion["invalid_approved"] + confusion["valid_approved"],
                "no_adjudicated_approvals",
            ),
            "reviewerFalseRejection": rate(
                confusion["valid_rejected"],
                confusion["valid_approved"] + confusion["valid_rejected"],
                "no_human_valid_controls",
            ),
        }
    )
    repairs = labels.repairs if labels else ()
    rates["repairSuccess"] = rate(
        sum(repair.failure_resolved is True for repair in repairs),
        sum(repair.failure_resolved is not None for repair in repairs),
        "repair_success_not_judged",
    )
    rates["repairRegression"] = rate(
        sum(repair.unaffected_regression is True for repair in repairs),
        sum(repair.unaffected_regression is not None for repair in repairs),
        "repair_regression_not_judged",
    )
    live = [
        attempt
        for attempt in bundle.attempts
        if not attempt.fact.synthetic and not attempt.fact.replayed
    ]
    known = sum(attempt.fact.actual_cost_micros or 0 for attempt in live)
    historical = sum(
        attempt.fact.actual_cost_micros or 0
        for attempt in live
        if attempt.origin == "historical_retained"
    )
    unknown = sum(attempt.fact.actual_cost_micros is None for attempt in live)
    exposure = sum(
        attempt.fact.estimated_cost_micros for attempt in live if attempt.fact.reservation_active
    )
    usage: Counter[str] = Counter()
    for attempt in live:
        for key, value in attempt.fact.usage.items():
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                and "token" in key.casefold()
            ):
                usage[key] += value
    if bundle.retained_expense is not None:
        receipt = bundle.retained_expense
        known += receipt.actual_cost_micros or 0
        historical += receipt.actual_cost_micros or 0
        unknown += receipt.actual_cost_micros is None
        exposure += receipt.estimated_exposure_micros if receipt.actual_cost_micros is None else 0
        usage.update(receipt.usage)
    latencies = [
        (attempt.fact.finished_at - attempt.fact.dispatched_at).total_seconds()
        for attempt in live
        if attempt.fact.dispatched_at is not None and attempt.fact.finished_at is not None
    ]
    human_edits = labels.human_edits if labels else ()
    measured = [edit for edit in human_edits if edit.active_seconds is not None]
    statuses = {stage.stage: stage.status for stage in bundle.stages}
    source_complete = all(
        statuses.get(stage) == "complete"
        for stage in ("discovery", "selection", "compilation", "rendering")
    )
    rates["sourceCompletion"] = rate(
        int(source_complete) if bundle.mode == "workflow" else None, 1, "not_a_workflow_execution"
    )
    limitations = list(bundle.limitations)
    if labels is None:
        limitations.append("human_labels_missing")
    if not opportunities_known:
        limitations.append("independent_opportunity_labels_incomplete")
    if len(evaluated) != len(final):
        limitations.append("final_editorial_candidates_not_all_evaluated")
    if len(media_evaluated) != len(final):
        limitations.append("final_media_not_all_evaluated")
    if any(j.full_selected_text_reviewed and not j.full_playback for j in evaluated):
        limitations.append("selection_assessment_includes_text_only_observations")
    if any(
        j.cold_prior_source_exposure
        or j.cold_prior_variant_exposure
        or j.cold_annotator == j.source_annotator
        for j in judgments
    ):
        limitations.append("cold_viewer_exposure_or_shared_annotator")
    if unknown:
        limitations.append("unsettled_costs")
    if bundle.mode != "workflow":
        limitations.append("retained_author_is_not_a_complete_workflow")
    complete = (
        source_complete
        and opportunities_known
        and len(media_evaluated) == len(final)
        and bundle.mode == "workflow"
        and not any(j.decision == "unknown" for j in judgments)
    )
    return TopicQualityReport(
        bundle_sha256=digest(bundle.model_dump(mode="json", by_alias=True)),
        labels_sha256=digest(labels.model_dump(mode="json", by_alias=True)) if labels else None,
        source_id=str(bundle.source_id),
        recording_group=bundle.recording_group,
        split=bundle.split,
        configuration_id=bundle.configuration.configuration_id,
        mode=bundle.mode,
        status=bundle.status,
        counts={
            "finalRecommendations": len(final),
            "deliveredVideos": len(delivered),
            "evaluatedFinalVideos": len(evaluated),
            "editoriallyEvaluatedCandidates": len(evaluated),
            "fullyMediaEvaluatedVideos": len(media_evaluated),
            "editoriallyUsefulCandidates": len(useful),
            "acceptedWithoutRepair": len(accepted),
            "independentWorthwhileOpportunities": len(worthwhile),
            "representedWorthwhileOpportunities": len(covered),
            "acceptedWorthwhileOpportunities": len(accepted_covered),
            "titleOnlyCorrections": sum(j.editorial_changes == "title_only" for j in evaluated),
            "contentCorrections": sum(
                j.editorial_changes in {"content_boundary", "major"} for j in evaluated
            ),
            "severeFidelityDefects": sum(j.severe_fidelity_defect is True for j in evaluated),
        },
        rates=rates,
        reviewer_confusion=dict(confusion),
        stage_losses=dict(Counter(loss.stage for loss in labels.losses)) if labels else {},
        stage_statuses=statuses,
        money={
            "physicalAttempts": len(bundle.attempts),
            "newLiveDispatches": sum(a.origin == "current_run" for a in live),
            "historicalRetainedAttempts": sum(a.origin == "historical_retained" for a in live)
            + int(bundle.retained_expense is not None),
            "knownTotalMicros": known,
            "historicalRetainedMicros": historical,
            "newKnownMicros": known - historical,
            "unknownCostAttempts": unknown,
            "activeReservedExposureMicros": exposure,
            "knownMicrosPerAcceptedVideo": known / len(accepted)
            if accepted and not unknown
            else None,
            "summedAttemptLatencySeconds": sum(latencies) if latencies else None,
            "measuredLatencyAttempts": len(latencies),
        },
        usage=dict(usage),
        human_effort={
            "measuredEditCount": len(measured),
            "unmeasuredEditCount": len(human_edits) - len(measured),
            "correctionActiveSeconds": sum(edit.active_seconds or 0 for edit in measured)
            if measured
            else None,
            "evaluationActiveSeconds": labels.evaluation_active_seconds if labels else None,
        },
        qualification_complete=complete,
        limitations=tuple(dict.fromkeys(limitations)),
    )


def readable_topic_report(report: TopicQualityReport) -> str:
    """Print compact aggregate observations without private source material."""
    rows = [f"Topic evaluation: {report.configuration_id} / {report.source_id}"]
    for name in (
        "editorialPrecision",
        "usefulOpportunityRecall",
        "deliveryYield",
        "endToEndAcceptedYield",
    ):
        metric = report.rates[name]
        rendered = (
            f"{metric.numerator}/{metric.denominator} ({metric.value:.1%})"
            if metric.value is not None
            else f"not measured ({metric.missing_reason})"
        )
        rows.append(f"{name}: {rendered}")
    rows.append(
        f"Known total cost: {report.money['knownTotalMicros']} micros; "
        f"unknown attempts: {report.money['unknownCostAttempts']}"
    )
    rows.append(f"Qualification complete: {report.qualification_complete}")
    return "\n".join(rows)
