"""Pure validation and extractive fallback for hierarchical chapter summaries."""

# Public refusals are intentionally content-free and stable.
# Pydantic resolves these annotations at runtime; fixed refusal names omit Error.
# ruff: noqa: C901, EM101, N815, N818, PLR0912, PLR0913, PLR0915, TC001, TC003, TRY003

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai import TextPart

from temnia_pipeline.contracts import HarnessArtifactRef, HarnessEvidence
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.models import (
    HierarchicalSummaryV1,
    SummaryUnit,
    normalized_summary_units,
)
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_summary_prompt,
    render_summary_reduction_prompt,
)

GROUNDING_FORMAT = "chapter-summary-grounding/1"
GROUNDING_POLICY_VERSION = "summary-grounding-v1"
COVERAGE_GROUNDING_POLICY_VERSION = "summary-grounding-v2"
MAX_SUMMARY_TEXT_LENGTH = 20_000


class SummaryGroundingRefusal(RuntimeError):
    """A summary cannot be safely grounded without another model call."""


class SummaryFallback(BaseModel):
    """Auditable description of one complete extractive unit replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unitId: Annotated[str, Field(min_length=1, max_length=256)]
    firstSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    lastSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    rejectedQuoteWordIds: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...]
    replacementQuoteWordIds: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...]
    provenance: Literal["extractive_source_fallback"] = "extractive_source_fallback"


class SummaryCoverageDiagnostic(BaseModel):
    """Content-free interval defect counts from one retained model response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: Literal["interval_cover_invalid"] = "interval_cover_invalid"
    originalUnitCount: Annotated[int, Field(gt=0)]
    coveredSentenceCount: Annotated[int, Field(ge=0)]
    gapSentenceCount: Annotated[int, Field(ge=0)]
    overlapSentenceCount: Annotated[int, Field(ge=0)]
    orderingViolationCount: Annotated[int, Field(ge=0)]


class SummaryCoverageFallback(BaseModel):
    """Auditable provenance for one whole-window source replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unitId: Annotated[str, Field(min_length=1, max_length=256)]
    firstSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    lastSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    replacementQuoteWordIds: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...]
    provenance: Literal["extractive_source_window_fallback"] = "extractive_source_window_fallback"


class GroundedSummary(BaseModel):
    """Pure summary result and its fallback audit entries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: HierarchicalSummaryV1
    fallbacks: tuple[SummaryFallback, ...] = ()
    coverageDiagnostic: SummaryCoverageDiagnostic | None = None
    coverageFallback: SummaryCoverageFallback | None = None


class SummaryGroundingReport(BaseModel):
    """Immutable per-window grounding report published by the activity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["chapter-summary-grounding/1"] = GROUNDING_FORMAT
    policyVersion: Literal["summary-grounding-v1"] = GROUNDING_POLICY_VERSION
    runId: UUID
    hierarchyLevel: Annotated[int, Field(ge=1, le=8)]
    modelStage: Annotated[str, Field(min_length=1, max_length=128)]
    windowId: Annotated[str, Field(min_length=1, max_length=256)]
    firstSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    lastSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    windowSentenceCount: Annotated[int, Field(gt=0)]
    windowPromptSha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    evidence: HarnessArtifactRef
    rawResponse: HarnessArtifactRef
    inputArtifacts: tuple[HarnessArtifactRef, ...] = ()
    sourceSummarySha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    normalizedSummary: HierarchicalSummaryV1
    fallbacks: tuple[SummaryFallback, ...] = ()


class SummaryGroundingReportV2(BaseModel):
    """Versioned whole-window coverage recovery without changing v1 bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["chapter-summary-grounding/1"] = GROUNDING_FORMAT
    policyVersion: Literal["summary-grounding-v2"] = COVERAGE_GROUNDING_POLICY_VERSION
    runId: UUID
    hierarchyLevel: Literal[1]
    modelStage: Annotated[str, Field(min_length=1, max_length=128)]
    windowId: Annotated[str, Field(min_length=1, max_length=256)]
    firstSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    lastSentenceId: Annotated[str, Field(min_length=1, max_length=256)]
    windowSentenceCount: Annotated[int, Field(gt=0)]
    windowPromptSha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    evidence: HarnessArtifactRef
    rawResponse: HarnessArtifactRef
    inputArtifacts: tuple[HarnessArtifactRef, ...] = ()
    sourceSummarySha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    normalizedSummary: HierarchicalSummaryV1
    fallbacks: tuple[SummaryFallback, ...] = ()
    coverageDiagnostic: SummaryCoverageDiagnostic
    coverageFallback: SummaryCoverageFallback


type SummaryGroundingReportType = SummaryGroundingReport | SummaryGroundingReportV2
SUMMARY_GROUNDING_REPORT_ADAPTER: TypeAdapter[SummaryGroundingReportType] = TypeAdapter(
    Annotated[SummaryGroundingReportType, Field(discriminator="policyVersion")]
)


def read_summary_grounding_report(value: object) -> SummaryGroundingReportType:
    """Strictly decode either portable policy without changing its wire shape."""
    if isinstance(value, (bytes, str)):
        payload = value
    else:
        try:
            payload = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise SummaryGroundingRefusal(
                "The summary grounding report could not be verified."
            ) from error
    try:
        return SUMMARY_GROUNDING_REPORT_ADAPTER.validate_json(payload, strict=True)
    except ValueError as error:
        raise SummaryGroundingRefusal(
            "The summary grounding report could not be verified."
        ) from error


def coverage_fallback_window_count(report: SummaryGroundingReportType) -> int:
    """Return the explicit whole-window fallback count for portable metadata."""
    return int(isinstance(report, SummaryGroundingReportV2))


def fallback_unit_count(report: SummaryGroundingReportType) -> int:
    """Count quote-replaced units plus the v2 whole-window replacement."""
    return len(report.fallbacks) + coverage_fallback_window_count(report)


def grounding_artifact_fingerprint(report: SummaryGroundingReportType) -> str:
    """Recompute the immutable producer identity from the portable report."""
    from temnia_pipeline.harness import artifacts  # noqa: PLC0415

    return artifacts.fingerprint_for(
        kind="checks",
        inputs={
            "evidence": report.evidence.model_dump(mode="json"),
            "rawResponse": report.rawResponse.model_dump(mode="json"),
            "inputArtifacts": [value.model_dump(mode="json") for value in report.inputArtifacts],
            "runId": str(report.runId),
            "modelStage": report.modelStage,
            "hierarchyLevel": report.hierarchyLevel,
            "window": {
                "id": report.windowId,
                "firstSentenceId": report.firstSentenceId,
                "lastSentenceId": report.lastSentenceId,
                "sentenceCount": report.windowSentenceCount,
                "promptSha256": report.windowPromptSha256,
            },
            "sourceSummarySha256": report.sourceSummarySha256,
        },
        config={"format": GROUNDING_FORMAT, "policyVersion": report.policyVersion},
    )


def _normalized_cosmetic_labels(body: dict[str, object]) -> dict[str, object]:
    """Mirror the model boundary's deterministic invalid-label normalization."""
    result = normalized_summary_units(body.get("units"))
    if result is None:
        raise SummaryGroundingRefusal("The summary response shape could not be verified.")
    return {**body, "units": result[0]}


def normalized_summary_from_response(value: object) -> HierarchicalSummaryV1:
    """Recover the exact summary payload represented by a retained model response."""
    try:
        response = MODEL_RESPONSE_ADAPTER.validate_python(value)
    except (TypeError, ValueError) as error:
        raise SummaryGroundingRefusal(
            "The retained summary response could not be verified."
        ) from error
    text_parts = [part for part in response.parts if isinstance(part, TextPart)]
    if len(text_parts) != 1:
        raise SummaryGroundingRefusal("The retained summary response could not be verified.")
    try:
        raw: object = json.loads(text_parts[0].content)
    except (TypeError, ValueError) as error:
        raise SummaryGroundingRefusal(
            "The retained summary response could not be verified."
        ) from error
    if not isinstance(raw, dict):
        raise SummaryGroundingRefusal("The retained summary response could not be verified.")
    try:
        return HierarchicalSummaryV1.model_validate(
            _normalized_cosmetic_labels(cast("dict[str, object]", raw))
        )
    except ValueError as error:
        raise SummaryGroundingRefusal(
            "The retained summary response could not be verified."
        ) from error


def allowed_anchors_from_exact_prompt(
    *,
    evidence: HarnessEvidence,
    evidence_sha256: str,
    source_id: UUID,
    window_id: str,
    window_first_sentence_id: str,
    window_last_sentence_id: str,
    window_sentence_count: int,
    prompt: str,
    hierarchy_level: int,
    input_reports: tuple[SummaryGroundingReportType, ...] = (),
) -> frozenset[str]:
    """Rebuild the exact prompt and return only anchors actually shown to the model."""
    positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    try:
        start = positions[window_first_sentence_id]
        end = positions[window_last_sentence_id]
    except KeyError as error:
        raise SummaryGroundingRefusal("The summary window names a foreign sentence.") from error
    if end < start or end - start + 1 != window_sentence_count:
        raise SummaryGroundingRefusal("The summary window does not match the accepted evidence.")
    language = evidence.config.get("detectedLanguage")
    detected_language = language.strip() if isinstance(language, str) and language.strip() else None
    source_sentences = evidence.sentences[start : end + 1]
    if hierarchy_level == 1:
        exact_window = PromptWindow(
            sourceId=source_id,
            evidenceSha256=evidence_sha256,
            windowId=window_id,
            firstSentenceId=window_first_sentence_id,
            lastSentenceId=window_last_sentence_id,
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
        )
        if prompt != render_summary_prompt(exact_window, detected_language=detected_language):
            raise SummaryGroundingRefusal(
                "The retained summary prompt does not match the accepted evidence."
            )
        return frozenset(
            anchor
            for sentence in exact_window.sentences
            for anchor in (sentence.firstWordId, sentence.lastWordId)
            if anchor is not None
        )

    selected = sorted(
        (
            report
            for report in input_reports
            if report.firstSentenceId in positions
            and report.lastSentenceId in positions
            and start <= positions[report.firstSentenceId]
            and positions[report.lastSentenceId] <= end
        ),
        key=lambda report: positions[report.firstSentenceId],
    )
    expected_start = start
    for report in selected:
        report_start = positions[report.firstSentenceId]
        report_end = positions[report.lastSentenceId]
        if report_start != expected_start or report_end < report_start:
            raise SummaryGroundingRefusal(
                "The reduction prompt inputs do not exactly cover their source window."
            )
        expected_start = report_end + 1
    if not selected or expected_start != end + 1:
        raise SummaryGroundingRefusal(
            "The reduction prompt inputs do not exactly cover their source window."
        )
    selected_summaries = [report.normalizedSummary.model_dump(mode="json") for report in selected]
    expected_prompt = render_summary_reduction_prompt(
        source_id=source_id,
        evidence_sha256=evidence_sha256,
        summaries=selected_summaries,
        hierarchy_level=hierarchy_level,
        detected_language=detected_language,
    )
    if prompt != expected_prompt:
        raise SummaryGroundingRefusal(
            "The retained reduction prompt does not match its grounded inputs."
        )
    return frozenset(
        quote
        for report in selected
        for unit in report.normalizedSummary.units
        for quote in unit.quoteWordIds
    )


def ground_summary(
    *,
    evidence: HarnessEvidence,
    window_first_sentence_id: str,
    window_last_sentence_id: str,
    window_sentence_count: int,
    summary: HierarchicalSummaryV1,
    allowed_model_anchors: frozenset[str],
    hierarchy_level: int = 1,
) -> GroundedSummary:
    """Validate exact cover and apply only policy-eligible source fallbacks."""
    sentence_positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    if len(sentence_positions) != len(evidence.sentences):
        raise SummaryGroundingRefusal("The accepted evidence has duplicate sentence identities.")
    try:
        window_start = sentence_positions[window_first_sentence_id]
        window_end = sentence_positions[window_last_sentence_id]
    except KeyError as error:
        raise SummaryGroundingRefusal("The summary window names a foreign sentence.") from error
    if window_end < window_start or window_end - window_start + 1 != window_sentence_count:
        raise SummaryGroundingRefusal("The summary window does not match the accepted evidence.")

    word_owners = {
        word.root: sentence.id for sentence in evidence.sentences for word in sentence.wordIds
    }
    source_window = evidence.sentences[window_start : window_end + 1]
    if any(not sentence.wordIds for sentence in source_window):
        raise SummaryGroundingRefusal("A grounded source excerpt has no immutable word anchors.")
    visible_source_anchors = {
        word.root
        for sentence in source_window
        for word in (sentence.wordIds[0], sentence.wordIds[-1])
    }
    ranges: list[tuple[int, int]] = []
    for unit in summary.units:
        try:
            unit_start = sentence_positions[unit.firstSentenceId]
            unit_end = sentence_positions[unit.lastSentenceId]
        except KeyError as error:
            raise SummaryGroundingRefusal("A summary unit names a foreign sentence.") from error
        if not window_start <= unit_start <= unit_end <= window_end:
            raise SummaryGroundingRefusal(
                "A summary unit range lies outside its accepted source window."
            )
        ranges.append((unit_start, unit_end))
        for quote in unit.quoteWordIds:
            if quote not in visible_source_anchors or quote not in allowed_model_anchors:
                raise SummaryGroundingRefusal(
                    "A summary quote is not an anchor present in its model input."
                )
            owner = word_owners.get(quote)
            if owner is None:
                raise SummaryGroundingRefusal("A summary quote names a foreign source word.")
            owner_position = sentence_positions[owner]
            if not window_start <= owner_position <= window_end:
                raise SummaryGroundingRefusal("A summary quote lies outside its source window.")

    expected_unit_start = window_start
    exact_cover = True
    for unit_start, unit_end in ranges:
        if unit_start != expected_unit_start:
            exact_cover = False
        expected_unit_start = unit_end + 1
    exact_cover = exact_cover and expected_unit_start == window_end + 1
    if not exact_cover:
        if hierarchy_level != 1:
            raise SummaryGroundingRefusal(
                "The summary units do not exactly cover their source window."
            )
        source_sentences = source_window
        excerpt = " ".join(sentence.text for sentence in source_sentences)
        if not excerpt.strip() or len(excerpt) > MAX_SUMMARY_TEXT_LENGTH:
            raise SummaryGroundingRefusal(
                "A grounded source excerpt is empty or exceeds the summary unit limit."
            )
        replacement_anchors = tuple(
            dict.fromkeys(
                (
                    source_sentences[0].wordIds[0].root,
                    source_sentences[-1].wordIds[-1].root,
                )
            )
        )
        coverage = [0] * window_sentence_count
        ordering_violations = 0
        previous_start: int | None = None
        for unit_start, unit_end in ranges:
            if previous_start is not None and unit_start < previous_start:
                ordering_violations += 1
            previous_start = unit_start
            for position in range(unit_start, unit_end + 1):
                coverage[position - window_start] += 1
        unit_id = (
            "coverage-fallback-"
            + hashlib.sha256(
                f"{window_first_sentence_id}\0{window_last_sentence_id}".encode()
            ).hexdigest()[:24]
        )
        fallback_unit = SummaryUnit(
            id=unit_id,
            firstSentenceId=window_first_sentence_id,
            lastSentenceId=window_last_sentence_id,
            quoteWordIds=list(replacement_anchors),
            text=excerpt,
        )
        return GroundedSummary(
            summary=summary.model_copy(update={"units": [fallback_unit]}),
            coverageDiagnostic=SummaryCoverageDiagnostic(
                originalUnitCount=len(summary.units),
                coveredSentenceCount=sum(value > 0 for value in coverage),
                gapSentenceCount=sum(value == 0 for value in coverage),
                overlapSentenceCount=sum(value > 1 for value in coverage),
                orderingViolationCount=ordering_violations,
            ),
            coverageFallback=SummaryCoverageFallback(
                unitId=unit_id,
                firstSentenceId=window_first_sentence_id,
                lastSentenceId=window_last_sentence_id,
                replacementQuoteWordIds=replacement_anchors,
            ),
        )

    normalized_units: list[SummaryUnit] = []
    fallbacks: list[SummaryFallback] = []
    for unit, (unit_start, unit_end) in zip(summary.units, ranges, strict=True):
        rejected: list[str] = []
        for quote in unit.quoteWordIds:
            owner = word_owners[quote]
            owner_position = sentence_positions[owner]
            if not unit_start <= owner_position <= unit_end:
                rejected.append(quote)
        if rejected:
            source_sentences = evidence.sentences[unit_start : unit_end + 1]
            excerpt = " ".join(sentence.text for sentence in source_sentences)
            if not excerpt.strip() or len(excerpt) > MAX_SUMMARY_TEXT_LENGTH:
                raise SummaryGroundingRefusal(
                    "A grounded source excerpt is empty or exceeds the summary unit limit."
                )
            replacement_anchors = tuple(
                dict.fromkeys(
                    (
                        source_sentences[0].wordIds[0].root,
                        source_sentences[-1].wordIds[-1].root,
                    )
                )
            )
            replacement = unit.model_copy(
                update={"quoteWordIds": list(replacement_anchors), "text": excerpt}
            )
            normalized_units.append(replacement)
            fallbacks.append(
                SummaryFallback(
                    unitId=unit.id,
                    firstSentenceId=unit.firstSentenceId,
                    lastSentenceId=unit.lastSentenceId,
                    rejectedQuoteWordIds=tuple(rejected),
                    replacementQuoteWordIds=replacement_anchors,
                )
            )
        else:
            normalized_units.append(unit)
    return GroundedSummary(
        summary=summary.model_copy(update={"units": normalized_units}),
        fallbacks=tuple(fallbacks),
    )
