"""Strict offline inputs for chapter evaluation and human boundary review."""

# Public bundle refusals name the violated identity at the validation site.
# ruff: noqa: C901, EM101, PLR0912, TC001, TC003, TRY003

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    HarnessEvidence,
)
from temnia_pipeline.harness.artifacts import canonical_json, fingerprint_for
from temnia_pipeline.harness.models import EditorialVerdictV1
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_summary_prompt,
    render_summary_reduction_prompt,
)
from temnia_pipeline.harness.proposal_diagnostics import (
    SUPPORTED_PROPOSAL_SCHEMAS,
    ProposalDiagnosticReport,
    diagnostic_artifact_fingerprint,
)
from temnia_pipeline.harness.summary_grounding import (
    SummaryGroundingReportType,
    SummaryGroundingReportV2,
    grounding_artifact_fingerprint,
)
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    rational,
    rounded_milliseconds,
    validate_edit,
    validate_evidence,
)

SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_STORAGE_KEY_LENGTH = 2048
type JSONValue = str | int | float | bool | list[JSONValue] | dict[str, JSONValue] | None
TOKEN_USAGE_FIELDS = frozenset(
    {
        "cache_audio_read_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cachedInputTokens",
        "input_audio_tokens",
        "input_tokens",
        "inputTokens",
        "output_audio_tokens",
        "output_tokens",
        "outputTokens",
        "reasoning_tokens",
        "reasoningTokens",
    }
)


def _validate_finite_json(value: JSONValue, *, path: str = "usage") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        message = f"{path} contains a nonfinite number"
        raise ValueError(message)
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_json(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_json(item, path=f"{path}.{key}")


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class EvaluationModel(BaseModel):
    """Strict immutable camel-case wire model."""

    model_config = ConfigDict(
        alias_generator=_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class Provenance(EvaluationModel):
    """Frozen versions needed to reproduce and compare one result."""

    source_version: Annotated[str, Field(min_length=1)]
    model_versions: dict[str, str]
    compiler_version: Annotated[str, Field(min_length=1)]
    prompt_versions: dict[str, str]
    route_snapshot_id: Annotated[str, Field(min_length=1)]
    compiler_calibration_artifact_sha256: SHA256 | None = None


class CheckArtifact(EvaluationModel):
    """One verified section check body and its immutable body hash."""

    artifact_id: UUID
    section_id: Annotated[str, Field(min_length=1, max_length=256)]
    sha256: SHA256
    checks: ChapterChecks


class ImmutableArtifactFact(EvaluationModel):
    """Scoped immutable identity for one verification artifact or dependency."""

    id: UUID
    source_id: UUID
    kind: Literal["evidence", "edit", "render", "model_response", "checks"]
    fingerprint: SHA256
    sha256: SHA256
    size_bytes: Annotated[int | None, Field(ge=0)] = None
    storage_key: Annotated[str | None, Field(min_length=1)] = None


class EditorialVerificationBody(EvaluationModel):
    """Exact accepted JSON body of a chapter-verification/1 artifact."""

    format: Literal["chapter-verification/1"]
    verdict: EditorialVerdictV1


class EditorialVerificationArtifact(EvaluationModel):
    """Verifier body, metadata identity, and its three immutable dependencies."""

    artifact: ImmutableArtifactFact
    body: EditorialVerificationBody
    run_id: UUID
    revision: Annotated[int, Field(gt=0)]
    verifier_family: Annotated[str, Field(min_length=1, max_length=128)]
    edit: ImmutableArtifactFact
    descriptor: ImmutableArtifactFact
    model_response: ImmutableArtifactFact


class SummaryGroundingArtifact(EvaluationModel):
    """One portable grounding report with its exact persisted dependency closure."""

    artifact: ImmutableArtifactFact
    body: SummaryGroundingReportType
    dependencies: tuple[ImmutableArtifactFact, ...]


class ProposalDiagnosticArtifact(EvaluationModel):
    """One content-free proposal refusal with its immutable dependency closure."""

    artifact: ImmutableArtifactFact
    body: ProposalDiagnosticReport
    dependencies: tuple[ImmutableArtifactFact, ...]


class ReviewEvent(EvaluationModel):
    """A persisted review event; elapsed timestamps are not editing duration."""

    action: Annotated[str, Field(min_length=1, max_length=64)]
    state: Literal["applied", "conflict", "refused"]
    base_revision: Annotated[int, Field(ge=0)]
    resulting_revision: Annotated[int | None, Field(gt=0)] = None
    created_at: datetime


class AttemptFact(EvaluationModel):
    """Every physical attempt, including failures and unresolved exposure."""

    id: UUID
    operation_id: UUID
    attempt_number: Annotated[int, Field(gt=0)]
    state: Literal[
        "reserved",
        "dispatching",
        "running",
        "succeeded",
        "failed_known",
        "outcome_unknown",
        "cancel_requested",
        "cancelled_confirmed",
    ]
    provider: Annotated[str, Field(min_length=1)]
    model: str | None = None
    family: str | None = None
    route_id: str | None = None
    synthetic: bool
    replayed: bool = False
    estimated_cost_micros: Annotated[int, Field(ge=0)]
    actual_cost_micros: Annotated[int | None, Field(ge=0)] = None
    cost_status: Literal["estimated", "reported", "reconciled", "unknown"]
    reservation_active: bool = False
    response_present: bool
    remote_handle: str | None = None
    result_artifact_id: UUID | None = None
    usage: dict[str, JSONValue]
    dispatched_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("usage")
    @classmethod
    def _finite_usage(cls, value: dict[str, JSONValue]) -> dict[str, JSONValue]:
        _validate_finite_json(value)
        for key in TOKEN_USAGE_FIELDS & value.keys():
            item = value[key]
            if item is not None and (
                not isinstance(item, int) or isinstance(item, bool) or item < 0
            ):
                message = f"usage.{key} must be a nonnegative integer or null"
                raise ValueError(message)
        for snake, camel in (
            ("input_tokens", "inputTokens"),
            ("output_tokens", "outputTokens"),
            ("reasoning_tokens", "reasoningTokens"),
        ):
            if snake in value and camel in value:
                message = f"usage cannot contain both {snake} and {camel}"
                raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _cost_truth(self) -> Self:
        synthetic_providers = {"recorded", "synthetic-recorded"}
        if self.synthetic != (self.provider in synthetic_providers):
            raise ValueError(
                "synthetic attempt flag must match a recorded or synthetic-recorded provider"
            )
        if self.synthetic and self.replayed:
            raise ValueError("an attempt cannot be both synthetic and cassette-replayed")
        if self.cost_status in {"reported", "reconciled"} and self.actual_cost_micros is None:
            raise ValueError("reported or reconciled attempt requires actual cost")
        if self.actual_cost_micros is not None and self.cost_status not in {
            "reported",
            "reconciled",
        }:
            raise ValueError("actual cost requires reported or reconciled provenance")
        if self.synthetic and self.actual_cost_micros not in {None, 0}:
            raise ValueError("synthetic attempts cannot carry a live provider charge")
        if self.finished_at is not None and self.dispatched_at is None:
            raise ValueError("finished attempt requires a dispatch timestamp")
        if (
            self.finished_at is not None
            and self.dispatched_at is not None
            and self.finished_at < self.dispatched_at
        ):
            raise ValueError("attempt finish precedes dispatch")
        return self


class EvaluationBundle(EvaluationModel):
    """Accepted local material for one source-scoped run and revision."""

    format: Literal["temnia-chapter-evaluation-bundle/1"]
    source_id: UUID
    source_fingerprint: SHA256 | None = None
    duration_ms: Annotated[int, Field(gt=0)]
    source_has_video: bool
    source_has_audio: bool
    run_id: UUID
    observed_at: datetime
    status: Annotated[str, Field(min_length=1)]
    current_revision: Annotated[int, Field(ge=0)]
    accepted_revision: Annotated[int | None, Field(gt=0)] = None
    evidence_sha256: SHA256 | None = None
    evidence: HarnessEvidence | None = None
    edit_sha256: SHA256 | None = None
    edit_revision: Annotated[int | None, Field(gt=0)] = None
    edit: ChapterEditSpec | None = None
    render_descriptor_sha256: SHA256 | None = None
    renders: ChapterRenders | None = None
    checks: tuple[CheckArtifact, ...] = ()
    editorial_verification: EditorialVerificationArtifact | None = None
    summary_grounding: tuple[SummaryGroundingArtifact, ...] = ()
    proposal_diagnostics: tuple[ProposalDiagnosticArtifact, ...] = ()
    review_events: tuple[ReviewEvent, ...] = ()
    attempts: tuple[AttemptFact, ...] = ()
    provenance: Provenance
    split: Literal["tuning", "test", "qualification"]

    @model_validator(mode="after")
    def _has_media(self) -> Self:
        if not self.source_has_video and not self.source_has_audio:
            raise ValueError("evaluation source must have selected audio or video")
        return self


class PreferredWindow(EvaluationModel):
    """Human-selected local source range, never uploaded by this module."""

    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("preferred window must have positive duration")
        return self


class HumanBoundaryLabel(EvaluationModel):
    """Explicit human judgment for one grounded boundary candidate."""

    annotator: Annotated[str, Field(min_length=1, max_length=256)]
    source_fingerprint: SHA256
    evidence_sha256: SHA256
    boundary_id: Annotated[str, Field(min_length=1, max_length=256)]
    candidate_id: Annotated[str | None, Field(min_length=1, max_length=256)]
    accepted: bool
    preferred_time_ms: Annotated[int | None, Field(ge=0)] = None
    preferred_window: PreferredWindow | None = None
    correction_active_seconds: Annotated[int | None, Field(ge=0)] = None
    correction_measurement_method: str | None = None
    reviewed_at: datetime
    split: Literal["tuning", "test"]

    @model_validator(mode="after")
    def _measured_correction(self) -> Self:
        pair = self.correction_active_seconds, self.correction_measurement_method
        if (pair[0] is None) != (pair[1] is None):
            raise ValueError("correction duration and measurement method must be provided together")
        if (
            self.correction_measurement_method is not None
            and not self.correction_measurement_method
        ):
            raise ValueError("correction measurement method must be nonempty")
        return self


class HumanLabels(EvaluationModel):
    """Optional boundary labels with an explicit held-out split."""

    format: Literal["temnia-chapter-human-labels/1"]
    labels: Annotated[tuple[HumanBoundaryLabel, ...], Field(min_length=1)]


class ReviewAlternative(EvaluationModel):
    """One nearby grounded cut option for the existing review UI."""

    candidate_id: str
    time_ms: int
    requires_review: bool


class BoundaryReviewTask(EvaluationModel):
    """Source-local, text-free task for a blinded human judgment."""

    boundary_id: str
    candidate_id: str | None
    source_identity: SHA256
    window_start_ms: int
    window_end_ms: int
    proposed_time_ms: int
    alternatives: tuple[ReviewAlternative, ...]
    uncertainty: tuple[str, ...]
    blinded_candidate_label: str


class HumanReviewTasks(EvaluationModel):
    """Portable review metadata; media stays in the existing scoped UI."""

    format: Literal["temnia-chapter-review-tasks/1"]
    source_id: UUID
    evidence_sha256: SHA256
    tasks: tuple[BoundaryReviewTask, ...]


def content_sha256(value: BaseModel) -> str:
    """Hash one contract body with the artifact store's strict canonical encoding."""
    body = value.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(canonical_json(body)).hexdigest()


def required_check_names(bundle: EvaluationBundle) -> frozenset[str]:
    """Derive the exact technical acceptance contract from frozen source presence."""
    names = {"full_decode", "duration", "video_presence", "audio_presence", "caption_bounds"}
    if bundle.source_has_video:
        names.update(
            {
                "video_codec_h264",
                "video_width",
                "video_height",
                "rotation",
                "sample_aspect_ratio",
                "video_start_offset",
                "video_end",
            }
        )
    if bundle.source_has_audio:
        names.update(
            {
                "audio_codec_aac",
                "audio_layout",
                "audio_channels",
                "audio_start_offset",
                "audio_end",
            }
        )
    return frozenset(names)


def _summary_ref_matches_fact(ref: object, fact: ImmutableArtifactFact) -> bool:
    """Compare the immutable fields shared by an artifact ref and portable fact."""
    return bool(
        getattr(ref, "id", None) == fact.id
        and getattr(getattr(ref, "kind", None), "value", None) == fact.kind
        and getattr(ref, "fingerprint", None) == fact.fingerprint
        and getattr(ref, "sha256", None) == fact.sha256
        and getattr(ref, "sizeBytes", None) == fact.size_bytes
        and getattr(ref, "storageKey", None) == fact.storage_key
    )


def _source_scoped_storage_key(value: str | None, source_id: UUID) -> bool:
    if value is None or len(value) > MAX_STORAGE_KEY_LENGTH or value.startswith("/"):
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return any(
        parts[index : index + 2] == ["source", str(source_id)] for index in range(len(parts) - 1)
    )


def _validate_summary_grounding(bundle: EvaluationBundle) -> None:  # noqa: PLR0915
    reports = bundle.summary_grounding
    if not reports:
        return
    if bundle.evidence is None or bundle.evidence_sha256 is None:
        raise HarnessValidationError("summary grounding requires accepted evidence")
    validate_evidence(bundle.evidence)
    if content_sha256(bundle.evidence) != bundle.evidence_sha256:
        raise HarnessValidationError("evidence body hash differs from evidenceSha256")
    if (
        bundle.evidence.sourceId != bundle.source_id
        or bundle.evidence.sourceFingerprint != bundle.source_fingerprint
        or bundle.evidence.durationMs != bundle.duration_ms
    ):
        raise HarnessValidationError("summary grounding evidence belongs to a different source")
    artifact_ids = [item.artifact.id for item in reports]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise HarnessValidationError("summary grounding contains duplicate artifacts")
    reports_by_id = {item.artifact.id: item for item in reports}
    attempts_by_id = {attempt.id: attempt for attempt in bundle.attempts}
    if len(attempts_by_id) != len(bundle.attempts):
        raise HarnessValidationError("summary grounding has ambiguous attempt identities")
    response_ids = {
        attempt.result_artifact_id
        for attempt in attempts_by_id.values()
        if attempt.state == "succeeded"
        and attempt.response_present
        and attempt.result_artifact_id is not None
    }
    evidence_ids: set[UUID] = set()
    identities: set[tuple[int, str, str]] = set()
    sentence_positions = {
        sentence.id: index for index, sentence in enumerate(bundle.evidence.sentences)
    }
    word_owners = {
        word.root: sentence.id
        for sentence in bundle.evidence.sentences
        for word in sentence.wordIds
    }
    for item in reports:
        body = item.body
        if item.artifact.source_id != bundle.source_id or item.artifact.kind != "checks":
            raise HarnessValidationError("summary grounding artifact crosses source scope")
        if content_sha256(body) != item.artifact.sha256:
            raise HarnessValidationError("summary grounding body hash is invalid")
        if grounding_artifact_fingerprint(body) != item.artifact.fingerprint:
            raise HarnessValidationError("summary grounding fingerprint is invalid")
        if body.runId != bundle.run_id:
            raise HarnessValidationError("summary grounding belongs to a different run")
        expected_stage = (
            f"summary:{body.windowId}"
            if body.hierarchyLevel == 1
            else f"summary:level:{body.hierarchyLevel}:{body.windowId}"
        )
        if body.modelStage != expected_stage:
            raise HarnessValidationError("summary grounding stage identity is invalid")
        identity = (body.hierarchyLevel, body.modelStage, body.windowId)
        if identity in identities:
            raise HarnessValidationError("summary grounding contains a duplicate stage window")
        identities.add(identity)
        refs = (body.evidence, body.rawResponse, *body.inputArtifacts)
        ref_ids = [ref.id for ref in refs]
        if len(ref_ids) != len(set(ref_ids)):
            raise HarnessValidationError("summary grounding names duplicate dependencies")
        facts = item.dependencies
        fact_ids = [fact.id for fact in facts]
        if len(fact_ids) != len(set(fact_ids)) or set(fact_ids) != set(ref_ids):
            raise HarnessValidationError("summary grounding dependency closure is invalid")
        facts_by_id = {fact.id: fact for fact in facts}
        for ref in refs:
            fact = facts_by_id.get(ref.id)
            if (
                fact is None
                or fact.source_id != bundle.source_id
                or not _summary_ref_matches_fact(ref, fact)
            ):
                raise HarnessValidationError("summary grounding dependency identity is invalid")
        evidence_fact = facts_by_id[body.evidence.id]
        if evidence_fact.kind != "evidence" or body.evidence.sha256 != bundle.evidence_sha256:
            raise HarnessValidationError("summary grounding names different evidence")
        evidence_ids.add(body.evidence.id)
        if (
            facts_by_id[body.rawResponse.id].kind != "model_response"
            or body.rawResponse.id not in response_ids
        ):
            raise HarnessValidationError(
                "summary grounding raw response is not an accepted run attempt"
            )
        if any(
            facts_by_id[ref.id].kind != "checks"
            or ref.id == item.artifact.id
            or ref.id not in reports_by_id
            or not _summary_ref_matches_fact(ref, reports_by_id[ref.id].artifact)
            or reports_by_id[ref.id].body.hierarchyLevel != body.hierarchyLevel - 1
            or reports_by_id[ref.id].body.evidence != body.evidence
            for ref in body.inputArtifacts
        ):
            raise HarnessValidationError("summary grounding input lineage is incomplete")
        if (body.hierarchyLevel == 1) != (not body.inputArtifacts):
            raise HarnessValidationError("summary grounding hierarchy lineage is invalid")
        try:
            window_start = sentence_positions[body.firstSentenceId]
            window_end = sentence_positions[body.lastSentenceId]
        except KeyError as error:
            raise HarnessValidationError(
                "summary grounding window names a foreign sentence"
            ) from error
        if window_end < window_start or window_end - window_start + 1 != body.windowSentenceCount:
            raise HarnessValidationError("summary grounding window range is invalid")
        contained_inputs: list[SummaryGroundingReportType] = []
        if body.inputArtifacts:
            for ref in body.inputArtifacts:
                prior = reports_by_id[ref.id].body
                try:
                    prior_start = sentence_positions[prior.firstSentenceId]
                    prior_end = sentence_positions[prior.lastSentenceId]
                except KeyError as error:
                    raise HarnessValidationError(
                        "summary grounding input window names a foreign sentence"
                    ) from error
                if window_start <= prior_start and prior_end <= window_end:
                    contained_inputs.append(prior)
            contained_inputs.sort(key=lambda report: sentence_positions[report.firstSentenceId])
            expected_input_start = window_start
            for prior in contained_inputs:
                prior_start = sentence_positions[prior.firstSentenceId]
                prior_end = sentence_positions[prior.lastSentenceId]
                if (
                    prior_start != expected_input_start
                    or prior_end < prior_start
                    or prior_end - prior_start + 1 != prior.windowSentenceCount
                    or prior_end > window_end
                ):
                    raise HarnessValidationError(
                        "summary grounding input windows do not cover their parent"
                    )
                expected_input_start = prior_end + 1
            if expected_input_start != window_end + 1:
                raise HarnessValidationError("summary grounding input windows leave a parent gap")
        language_value = bundle.evidence.config.get("detectedLanguage")
        detected_language = (
            language_value.strip()
            if isinstance(language_value, str) and language_value.strip()
            else None
        )
        if body.hierarchyLevel == 1:
            source_sentences = bundle.evidence.sentences[window_start : window_end + 1]
            expected_prompt = render_summary_prompt(
                PromptWindow(
                    sourceId=bundle.source_id,
                    evidenceSha256=body.evidence.sha256,
                    windowId=body.windowId,
                    firstSentenceId=body.firstSentenceId,
                    lastSentenceId=body.lastSentenceId,
                    sentences=tuple(
                        PromptSentence(
                            id=sentence.id,
                            text=sentence.text,
                            firstWordId=sentence.wordIds[0].root,
                            lastWordId=sentence.wordIds[-1].root,
                            speakers=tuple(sentence.speakers),
                        )
                        for sentence in source_sentences
                    ),
                ),
                detected_language=detected_language,
            )
        else:
            expected_prompt = render_summary_reduction_prompt(
                source_id=bundle.source_id,
                evidence_sha256=body.evidence.sha256,
                summaries=[
                    report.normalizedSummary.model_dump(mode="json") for report in contained_inputs
                ],
                hierarchy_level=body.hierarchyLevel,
                detected_language=detected_language,
            )
        if hashlib.sha256(expected_prompt.encode()).hexdigest() != body.windowPromptSha256:
            raise HarnessValidationError("summary grounding prompt hash is invalid")
        units = {unit.id: unit for unit in body.normalizedSummary.units}
        if len(units) != len(body.normalizedSummary.units):
            raise HarnessValidationError("summary grounding contains duplicate unit identities")
        fallback_ids = [fallback.unitId for fallback in body.fallbacks]
        if len(fallback_ids) != len(set(fallback_ids)):
            raise HarnessValidationError("summary grounding contains duplicate fallbacks")
        fallbacks_by_id = {fallback.unitId: fallback for fallback in body.fallbacks}
        expected_start = window_start
        for unit in body.normalizedSummary.units:
            try:
                unit_start = sentence_positions[unit.firstSentenceId]
                unit_end = sentence_positions[unit.lastSentenceId]
            except KeyError as error:
                raise HarnessValidationError(
                    "summary grounding unit names a foreign sentence"
                ) from error
            if unit_start != expected_start or unit_end < unit_start or unit_end > window_end:
                raise HarnessValidationError(
                    "summary grounding units do not exactly cover their window"
                )
            if any(
                (owner := word_owners.get(quote)) is None
                or not unit_start <= sentence_positions[owner] <= unit_end
                for quote in unit.quoteWordIds
            ):
                raise HarnessValidationError("summary grounding quote lies outside its source unit")
            fallback = fallbacks_by_id.get(unit.id)
            if fallback is not None:
                source_sentences = bundle.evidence.sentences[unit_start : unit_end + 1]
                expected_text = " ".join(sentence.text for sentence in source_sentences)
                expected_quotes = tuple(
                    dict.fromkeys(
                        (
                            source_sentences[0].wordIds[0].root,
                            source_sentences[-1].wordIds[-1].root,
                        )
                    )
                )
                if (
                    not fallback.rejectedQuoteWordIds
                    or fallback.firstSentenceId != unit.firstSentenceId
                    or fallback.lastSentenceId != unit.lastSentenceId
                    or fallback.replacementQuoteWordIds != expected_quotes
                    or tuple(unit.quoteWordIds) != expected_quotes
                    or unit.text != expected_text
                ):
                    raise HarnessValidationError("summary grounding fallback content is invalid")
            expected_start = unit_end + 1
        if expected_start != window_end + 1:
            raise HarnessValidationError(
                "summary grounding units leave a gap in their source window"
            )
        if isinstance(body, SummaryGroundingReportV2):
            fallback = body.coverageFallback
            diagnostic = body.coverageDiagnostic
            source_sentences = bundle.evidence.sentences[window_start : window_end + 1]
            expected_text = " ".join(sentence.text for sentence in source_sentences)
            expected_quotes = tuple(
                dict.fromkeys(
                    (
                        source_sentences[0].wordIds[0].root,
                        source_sentences[-1].wordIds[-1].root,
                    )
                )
            )
            if (
                body.fallbacks
                or len(body.normalizedSummary.units) != 1
                or fallback.unitId != body.normalizedSummary.units[0].id
                or fallback.firstSentenceId != body.firstSentenceId
                or fallback.lastSentenceId != body.lastSentenceId
                or fallback.replacementQuoteWordIds != expected_quotes
                or body.normalizedSummary.units[0].firstSentenceId != body.firstSentenceId
                or body.normalizedSummary.units[0].lastSentenceId != body.lastSentenceId
                or tuple(body.normalizedSummary.units[0].quoteWordIds) != expected_quotes
                or body.normalizedSummary.units[0].text != expected_text
            ):
                raise HarnessValidationError(
                    "summary grounding coverage fallback content is invalid"
                )
            if (
                diagnostic.coveredSentenceCount + diagnostic.gapSentenceCount
                != body.windowSentenceCount
                or diagnostic.coveredSentenceCount > body.windowSentenceCount
                or diagnostic.overlapSentenceCount > diagnostic.coveredSentenceCount
                or (
                    diagnostic.gapSentenceCount == 0
                    and diagnostic.overlapSentenceCount == 0
                    and diagnostic.orderingViolationCount == 0
                )
            ):
                raise HarnessValidationError("summary grounding coverage diagnostic is invalid")
        for fallback in body.fallbacks:
            unit = units.get(fallback.unitId)
            if (
                unit is None
                or fallback.firstSentenceId != unit.firstSentenceId
                or fallback.lastSentenceId != unit.lastSentenceId
                or tuple(unit.quoteWordIds) != fallback.replacementQuoteWordIds
            ):
                raise HarnessValidationError("summary grounding fallback identity is invalid")
    if len(evidence_ids) != 1:
        raise HarnessValidationError("summary grounding mixes evidence artifacts")


def _validate_proposal_diagnostics(bundle: EvaluationBundle) -> None:  # noqa: PLR0915
    diagnostics = bundle.proposal_diagnostics
    if not diagnostics:
        return
    if bundle.evidence is None or bundle.evidence_sha256 is None:
        raise HarnessValidationError("proposal diagnostics require accepted evidence")
    attempts_by_id = {attempt.id: attempt for attempt in bundle.attempts}
    if len(attempts_by_id) != len(bundle.attempts):
        raise HarnessValidationError("proposal diagnostics have ambiguous attempt identities")
    artifact_ids = [item.artifact.id for item in diagnostics]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise HarnessValidationError("proposal diagnostics contain duplicate artifacts")
    diagnostics_by_id = {item.artifact.id: item for item in diagnostics}
    grounding_by_id = {item.artifact.id: item for item in bundle.summary_grounding}
    accepted_response_ids = {
        attempt.result_artifact_id
        for attempt in attempts_by_id.values()
        if attempt.state == "succeeded"
        and attempt.response_present
        and attempt.result_artifact_id is not None
    }
    identities: set[tuple[str, UUID]] = set()
    for item in diagnostics:
        body = item.body
        if item.artifact.source_id != bundle.source_id or item.artifact.kind != "checks":
            raise HarnessValidationError("proposal diagnostic artifact crosses source scope")
        body_bytes = canonical_json(body.model_dump(mode="json", by_alias=True))
        if item.artifact.size_bytes != len(body_bytes):
            raise HarnessValidationError("proposal diagnostic artifact size is invalid")
        if not _source_scoped_storage_key(item.artifact.storage_key, bundle.source_id):
            raise HarnessValidationError("proposal diagnostic artifact storage path is invalid")
        if content_sha256(body) != item.artifact.sha256:
            raise HarnessValidationError("proposal diagnostic body hash is invalid")
        if diagnostic_artifact_fingerprint(body) != item.artifact.fingerprint:
            raise HarnessValidationError("proposal diagnostic fingerprint is invalid")
        if body.runId != bundle.run_id:
            raise HarnessValidationError("proposal diagnostic belongs to a different run")
        if body.schemaVersion not in SUPPORTED_PROPOSAL_SCHEMAS:
            raise HarnessValidationError("proposal diagnostic schema is unsupported")
        identity = (body.modelStage, body.response.id)
        if identity in identities:
            raise HarnessValidationError("proposal diagnostics contain a duplicate response stage")
        identities.add(identity)
        refs = (body.evidence, body.response, *body.inputArtifacts)
        if any(not _source_scoped_storage_key(ref.storageKey, bundle.source_id) for ref in refs):
            raise HarnessValidationError("proposal diagnostic dependency storage path is invalid")
        ref_ids = [ref.id for ref in refs]
        facts = item.dependencies
        fact_ids = [fact.id for fact in facts]
        if (
            len(ref_ids) != len(set(ref_ids))
            or len(fact_ids) != len(set(fact_ids))
            or set(fact_ids) != set(ref_ids)
        ):
            raise HarnessValidationError("proposal diagnostic dependency closure is invalid")
        facts_by_id = {fact.id: fact for fact in facts}
        for ref in refs:
            fact = facts_by_id.get(ref.id)
            if (
                fact is None
                or fact.source_id != bundle.source_id
                or not _summary_ref_matches_fact(ref, fact)
            ):
                raise HarnessValidationError("proposal diagnostic dependency identity is invalid")
        evidence_fact = facts_by_id[body.evidence.id]
        if evidence_fact.kind != "evidence" or body.evidence.sha256 != bundle.evidence_sha256:
            raise HarnessValidationError("proposal diagnostic names different evidence")
        response_fact = facts_by_id[body.response.id]
        attempt = attempts_by_id.get(body.attemptId)
        if (
            response_fact.kind != "model_response"
            or attempt is None
            or attempt.state != "succeeded"
            or not attempt.response_present
            or attempt.operation_id != body.operationId
            or attempt.result_artifact_id != body.response.id
            or attempt.route_id != body.routeId
            or attempt.remote_handle != body.providerResponseId
        ):
            raise HarnessValidationError(
                "proposal diagnostic response is not an accepted run attempt"
            )
        for ref in body.inputArtifacts:
            fact = facts_by_id[ref.id]
            if fact.kind == "model_response":
                if ref.id not in accepted_response_ids:
                    raise HarnessValidationError(
                        "proposal diagnostic input response is not an accepted run attempt"
                    )
                continue
            if fact.kind != "checks" or ref.id == item.artifact.id:
                raise HarnessValidationError("proposal diagnostic input lineage is invalid")
            prior = grounding_by_id.get(ref.id) or diagnostics_by_id.get(ref.id)
            if prior is None or not _summary_ref_matches_fact(ref, prior.artifact):
                raise HarnessValidationError("proposal diagnostic input lineage is incomplete")


def validate_bundle(bundle: EvaluationBundle) -> None:  # noqa: PLR0915
    """Refuse corrupt, mixed-source, stale-revision, or ungrounded inputs."""
    for attempt in bundle.attempts:
        if attempt.synthetic != (attempt.provider in {"recorded", "synthetic-recorded"}):
            raise HarnessValidationError(
                "synthetic attempt flag must match a recorded or synthetic-recorded provider"
            )
        if attempt.synthetic and attempt.replayed:
            raise HarnessValidationError(
                "an attempt cannot be both synthetic and cassette-replayed"
            )
    if (bundle.evidence is None) != (bundle.evidence_sha256 is None):
        raise HarnessValidationError("evidence body and hash must be provided together")
    edit_fields = (
        bundle.edit is not None,
        bundle.edit_sha256 is not None,
        bundle.edit_revision is not None,
    )
    if len(set(edit_fields)) != 1:
        raise HarnessValidationError("edit body, hash, and revision must be provided together")
    if bundle.evidence is None:
        if bundle.source_fingerprint is not None:
            raise HarnessValidationError("a source fingerprint cannot be observed before evidence")
        if bundle.edit is not None or bundle.current_revision != 0:
            raise HarnessValidationError("an edit or revision requires grounded evidence")
        if bundle.accepted_revision is not None:
            raise HarnessValidationError("an incomplete run cannot name an accepted revision")
    _validate_summary_grounding(bundle)
    _validate_proposal_diagnostics(bundle)
    if bundle.edit is None:
        if bundle.current_revision != 0 or bundle.accepted_revision is not None:
            raise HarnessValidationError("a current or accepted revision requires an edit body")
        if (
            bundle.renders is not None
            or bundle.render_descriptor_sha256 is not None
            or bundle.checks
            or bundle.editorial_verification is not None
        ):
            raise HarnessValidationError("an incomplete run cannot contain render artifacts")
        if bundle.evidence is not None:
            validate_evidence(bundle.evidence)
            if content_sha256(bundle.evidence) != bundle.evidence_sha256:
                raise HarnessValidationError("evidence body hash differs from evidenceSha256")
            if (
                bundle.evidence.sourceId != bundle.source_id
                or bundle.evidence.sourceFingerprint != bundle.source_fingerprint
                or bundle.evidence.durationMs != bundle.duration_ms
            ):
                raise HarnessValidationError("evidence belongs to a different source")
        return
    if (
        bundle.evidence is None
        or bundle.evidence_sha256 is None
        or bundle.edit_sha256 is None
        or bundle.source_fingerprint is None
    ):
        raise HarnessValidationError("edit requires grounded evidence and accepted hashes")
    validate_evidence(bundle.evidence)
    if content_sha256(bundle.evidence) != bundle.evidence_sha256:
        raise HarnessValidationError("evidence body hash differs from evidenceSha256")
    if content_sha256(bundle.edit) != bundle.edit_sha256:
        raise HarnessValidationError("edit body hash differs from editSha256")
    if (
        bundle.evidence.sourceId != bundle.source_id
        or bundle.edit.sourceId != bundle.source_id
        or bundle.evidence.sourceFingerprint != bundle.source_fingerprint
    ):
        raise HarnessValidationError("bundle contains artifacts from different sources")
    if (
        bundle.evidence.durationMs != bundle.duration_ms
        or bundle.edit.durationMs != bundle.duration_ms
    ):
        raise HarnessValidationError("bundle duration differs across source artifacts")
    if bundle.edit.evidenceSha256 != bundle.evidence_sha256:
        raise HarnessValidationError("edit is grounded in a different evidence body")
    if bundle.edit.compilerVersion != bundle.provenance.compiler_version:
        raise HarnessValidationError("compiler provenance differs from the edit")
    if bundle.edit_revision != bundle.current_revision:
        raise HarnessValidationError("bundle edit is not the current run revision")
    if bundle.accepted_revision is not None and bundle.accepted_revision > bundle.current_revision:
        raise HarnessValidationError("accepted revision is newer than current revision")
    validate_edit(bundle.evidence, bundle.edit, expected_evidence_sha256=bundle.evidence_sha256)

    kept = {section.id for section in bundle.edit.sections if section.kind.value == "keep"}
    check_ids = [item.section_id for item in bundle.checks]
    if len(check_ids) != len(set(check_ids)) or not set(check_ids).issubset(kept):
        raise HarnessValidationError("checks contain duplicate or foreign section identities")
    required_names = required_check_names(bundle)
    for item in bundle.checks:
        if item.checks.editSha256 != bundle.edit_sha256:
            raise HarnessValidationError("checks name a different edit body")
        if content_sha256(item.checks) != item.sha256:
            raise HarnessValidationError("check body hash differs from its accepted hash")
        if any(check.sectionId != item.section_id for check in item.checks.technicalChecks):
            raise HarnessValidationError("technical check belongs to a different section")
        names = [check.name for check in item.checks.technicalChecks]
        if any(names.count(name) != 1 for name in required_names):
            raise HarnessValidationError(
                "required technical check names are incomplete or duplicate"
            )

    if bundle.renders is None:
        if bundle.render_descriptor_sha256 is not None:
            raise HarnessValidationError("render descriptor hash has no descriptor body")
    else:
        if bundle.render_descriptor_sha256 is None:
            raise HarnessValidationError("render descriptor body has no accepted hash")
        if content_sha256(bundle.renders) != bundle.render_descriptor_sha256:
            raise HarnessValidationError("render descriptor body hash differs from accepted hash")
        if bundle.renders.runId != bundle.run_id or bundle.renders.editSha256 != bundle.edit_sha256:
            raise HarnessValidationError("render descriptor belongs to a different run or edit")
        render_ids = [render.sectionId for render in bundle.renders.renders]
        if len(render_ids) != len(set(render_ids)) or set(render_ids) != kept:
            raise HarnessValidationError("render descriptor does not match current kept sections")
        duration_by_section = {
            section.id: rounded_milliseconds(
                rational(bundle.edit.boundaries[index + 1].time)
                - rational(bundle.edit.boundaries[index].time)
            )
            for index, section in enumerate(bundle.edit.sections)
            if section.kind.value == "keep"
        }
        if any(
            render.durationMs != duration_by_section[render.sectionId]
            for render in bundle.renders.renders
        ):
            raise HarnessValidationError("render duration differs from its edit section")
        checks_by_id = {item.artifact_id: item for item in bundle.checks}
        if any(
            render.checks is None
            or render.checks.id not in checks_by_id
            or render.checks.sha256 != checks_by_id[render.checks.id].sha256
            or checks_by_id[render.checks.id].section_id != render.sectionId
            for render in bundle.renders.renders
        ):
            raise HarnessValidationError("render descriptor does not reference every check body")

    verification = bundle.editorial_verification
    if verification is not None:
        refs = (
            verification.artifact,
            verification.edit,
            verification.descriptor,
            verification.model_response,
        )
        if any(ref.source_id != bundle.source_id for ref in refs):
            raise HarnessValidationError("editorial verification crosses source scope")
        if (
            verification.artifact.kind != "checks"
            or verification.edit.kind != "edit"
            or verification.descriptor.kind != "render"
            or verification.model_response.kind != "model_response"
        ):
            raise HarnessValidationError("editorial verification dependency kind is invalid")
        if verification.run_id != bundle.run_id or verification.revision != bundle.current_revision:
            raise HarnessValidationError(
                "editorial verification belongs to a different run revision"
            )
        if verification.edit.sha256 != bundle.edit_sha256:
            raise HarnessValidationError("editorial verification names a different edit")
        if (
            bundle.render_descriptor_sha256 is None
            or verification.descriptor.sha256 != bundle.render_descriptor_sha256
        ):
            raise HarnessValidationError("editorial verification names a different descriptor")
        if content_sha256(verification.body) != verification.artifact.sha256:
            raise HarnessValidationError("editorial verification body hash is invalid")
        expected_fingerprint = fingerprint_for(
            kind="checks",
            inputs={
                "descriptorArtifactId": str(verification.descriptor.id),
                "descriptorSha256": verification.descriptor.sha256,
                "editArtifactId": str(verification.edit.id),
                "editSha256": verification.edit.sha256,
                "modelResponseArtifactId": str(verification.model_response.id),
                "modelResponseSha256": verification.model_response.sha256,
            },
            config={"format": "chapter-verification/1"},
        )
        if verification.artifact.fingerprint != expected_fingerprint:
            raise HarnessValidationError("editorial verification fingerprint is invalid")


def validate_labels(bundle: EvaluationBundle, labels: HumanLabels) -> None:
    """Validate explicit labels and reject tuning/test source leakage."""
    if bundle.edit is None or bundle.evidence_sha256 is None:
        raise HarnessValidationError("human labels require a grounded current edit")
    splits_by_source: dict[str, set[str]] = {}
    known = {boundary.id: boundary for boundary in bundle.edit.boundaries[1:-1]}
    seen: set[tuple[str, str]] = set()
    for label in labels.labels:
        splits_by_source.setdefault(label.source_fingerprint, set()).add(label.split)
        if label.source_fingerprint != bundle.source_fingerprint:
            raise HarnessValidationError("human label belongs to a different source")
        if label.evidence_sha256 != bundle.evidence_sha256:
            raise HarnessValidationError("human label names a different evidence body")
        if label.split != bundle.split:
            raise HarnessValidationError("human label split differs from the evaluation bundle")
        boundary = known.get(label.boundary_id)
        if boundary is None or boundary.candidateId != label.candidate_id:
            raise HarnessValidationError("human label names a foreign boundary candidate")
        identity = label.annotator, label.boundary_id
        if identity in seen:
            raise HarnessValidationError("annotator labeled the same boundary more than once")
        seen.add(identity)
        if label.preferred_time_ms is not None and label.preferred_time_ms > bundle.duration_ms:
            raise HarnessValidationError("preferred boundary time lies outside the source")
        if (
            label.preferred_window is not None
            and label.preferred_window.end_ms > bundle.duration_ms
        ):
            raise HarnessValidationError("preferred review window lies outside the source")
    if any(len(splits) > 1 for splits in splits_by_source.values()):
        raise HarnessValidationError("one source fingerprint leaks across tuning and test splits")


def build_review_tasks(bundle: EvaluationBundle, *, radius_ms: int = 5000) -> HumanReviewTasks:
    """Build deterministic text-free boundary tasks for the existing review UI."""
    if radius_ms <= 0:
        raise ValueError("review radius must be positive")
    validate_bundle(bundle)
    if bundle.edit is None or bundle.evidence is None or bundle.evidence_sha256 is None:
        raise HarnessValidationError("human review tasks require a grounded current edit")
    candidates = tuple(bundle.evidence.boundaries)
    tasks: list[BoundaryReviewTask] = []
    for boundary in bundle.edit.boundaries[1:-1]:
        start = max(0, boundary.timeMs - radius_ms)
        end = min(bundle.duration_ms, boundary.timeMs + radius_ms)
        alternatives = tuple(
            ReviewAlternative(
                candidate_id=candidate.id,
                time_ms=candidate.timeMs,
                requires_review=candidate.requiresReview,
            )
            for candidate in candidates
            if candidate.id != boundary.candidateId and start <= candidate.timeMs <= end
        )
        stable = hashlib.sha256(
            canonical_json(
                {
                    "boundaryId": boundary.id,
                    "sourceFingerprint": bundle.source_fingerprint,
                }
            )
        ).hexdigest()
        tasks.append(
            BoundaryReviewTask(
                boundary_id=boundary.id,
                candidate_id=boundary.candidateId,
                source_identity=stable,
                window_start_ms=start,
                window_end_ms=end,
                proposed_time_ms=boundary.timeMs,
                alternatives=alternatives,
                uncertainty=tuple(boundary.reasons) if boundary.requiresReview else (),
                blinded_candidate_label=f"candidate-{stable[:12]}",
            )
        )
    return HumanReviewTasks(
        format="temnia-chapter-review-tasks/1",
        source_id=bundle.source_id,
        evidence_sha256=bundle.evidence_sha256,
        tasks=tuple(tasks),
    )
