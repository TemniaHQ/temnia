"""Content-free diagnostics for retained chapter proposal responses."""

# Stable diagnostic codes are consumed by a bounded repair prompt.
# ruff: noqa: EM101, N815, TC003, TRY003

from __future__ import annotations

import json
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic_ai import ModelResponse, TextPart

from temnia_pipeline.contracts import ChapterProposal, HarnessArtifactRef
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.runtime_types import ProposalDiagnosticIssue

DIAGNOSTIC_FORMAT = "chapter-proposal-diagnostic/1"
DIAGNOSTIC_POLICY_VERSION = "proposal-diagnostic-v1"
MAX_DIAGNOSTIC_ISSUES = 32
MAX_DIAGNOSTIC_PATH_LENGTH = 256
MAX_DIAGNOSTIC_INDEX = 1_000_000
_SCHEMA_FIELDS = frozenset(
    {
        "firstSentenceId",
        "id",
        "kind",
        "lastSentenceId",
        "quoteWordIds",
        "reason",
        "sections",
        "summary",
        "title",
        "version",
    }
)
SUPPORTED_PROPOSAL_SCHEMAS = frozenset({"chapter-proposal/1", "chapter-proposal-compact/1"})
_COMPILER_CODES = {
    "proposal refers to an unknown sentence": "foreign_sentence_reference",
    "proposal sentence ranges must be ordered without gaps or overlaps": "range_not_exact_cover",
    "proposal quote word must belong to its proposed section": "foreign_quote_reference",
    "proposal does not cover every evidence sentence": "incomplete_source_cover",
    "no grounded internal boundary candidate exists for a proposal transition": (
        "no_boundary_candidate"
    ),
    "no monotonic grounded candidate path preserves positive sections and quote ownership": (
        "no_compilable_boundary_path"
    ),
    "evidence must contain one exact candidate per source edge": "invalid_source_edge",
    "quantized rational time exceeds public safe integers": "invalid_quantized_time",
}
_COMPILER_MESSAGES = {
    "foreign_sentence_reference": "Use only sentence references from the supplied evidence.",
    "range_not_exact_cover": "Return ordered section ranges with no gaps or overlaps.",
    "foreign_quote_reference": "Use quote references that belong to their section range.",
    "incomplete_source_cover": "Return sections that cover every supplied source sentence.",
    "no_boundary_candidate": "Choose adjacent source ranges that have a grounded cut candidate.",
    "no_compilable_boundary_path": (
        "Choose ordered ranges and quotes that admit one grounded boundary path."
    ),
    "invalid_source_edge": "Return a complete source cover using the supplied endpoint references.",
    "invalid_quantized_time": "Return source ranges that compile within supported timeline bounds.",
    "compiler_refusal": (
        "Return an exact ordered source cover with valid sentence and quote references."
    ),
}

DiagnosticCode = Literal["output_limit", "invalid_json", "invalid_schema", "compiler_refusal"]


class ProposalUsageCounts(BaseModel):
    """Portable token counts copied from the retained response envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    inputTokens: Annotated[int, Field(ge=0)]
    outputTokens: Annotated[int, Field(ge=0)]


class ProposalDiagnosticReport(BaseModel):
    """Immutable content-free explanation of one rejected proposal response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["chapter-proposal-diagnostic/1"] = DIAGNOSTIC_FORMAT
    policyVersion: Literal["proposal-diagnostic-v1"] = DIAGNOSTIC_POLICY_VERSION
    runId: UUID
    modelStage: Annotated[str, Field(min_length=1, max_length=128)]
    evidence: HarnessArtifactRef
    response: HarnessArtifactRef
    inputArtifacts: tuple[HarnessArtifactRef, ...] = ()
    operationId: UUID
    attemptId: UUID
    providerResponseId: Annotated[str | None, Field(max_length=512)] = None
    routeId: Annotated[str, Field(min_length=1, max_length=128)]
    promptVersion: Annotated[str, Field(min_length=1, max_length=128)]
    schemaVersion: Annotated[str, Field(min_length=1, max_length=128)]
    maxOutputTokens: Annotated[int, Field(gt=0)]
    finishReason: Annotated[str | None, Field(max_length=64)] = None
    responsePartCount: Annotated[int, Field(ge=0)]
    textPartCount: Annotated[int, Field(ge=0)]
    usage: ProposalUsageCounts
    code: DiagnosticCode
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    issues: Annotated[
        tuple[ProposalDiagnosticIssue, ...], Field(max_length=MAX_DIAGNOSTIC_ISSUES)
    ] = ()
    compilerCode: Annotated[str | None, Field(max_length=128)] = None


def diagnostic_artifact_fingerprint(report: ProposalDiagnosticReport) -> str:
    """Derive the immutable diagnostic identity without its explanatory result."""
    return artifacts.fingerprint_for(
        kind="checks",
        inputs={
            "evidence": report.evidence.model_dump(mode="json"),
            "response": report.response.model_dump(mode="json"),
            "inputArtifacts": [value.model_dump(mode="json") for value in report.inputArtifacts],
            "runId": str(report.runId),
            "modelStage": report.modelStage,
        },
        config={
            "format": DIAGNOSTIC_FORMAT,
            "policyVersion": DIAGNOSTIC_POLICY_VERSION,
            "routeId": report.routeId,
            "promptVersion": report.promptVersion,
            "schemaVersion": report.schemaVersion,
            "maxOutputTokens": report.maxOutputTokens,
            "compilerCode": report.compilerCode,
        },
    )


def _path(value: tuple[object, ...]) -> str:
    parts = ["$"]
    for item in value:
        if isinstance(item, int):
            rendered = f"[{item}]" if 0 <= item <= MAX_DIAGNOSTIC_INDEX else "[index]"
        elif isinstance(item, str):
            rendered = f".{item}" if item in _SCHEMA_FIELDS else ".unexpected_field"
        else:
            rendered = ".unexpected_field"
        if sum(map(len, parts)) + len(rendered) > MAX_DIAGNOSTIC_PATH_LENGTH:
            break
        parts.append(rendered)
    return "".join(parts)


def _validation_issues(error: ValidationError) -> tuple[ProposalDiagnosticIssue, ...]:
    return tuple(
        ProposalDiagnosticIssue(
            path=_path(cast("tuple[object, ...]", value["loc"])),
            code=str(value["type"])[:128],
        )
        for value in error.errors(include_url=False, include_context=False, include_input=False)[
            :MAX_DIAGNOSTIC_ISSUES
        ]
    )


def _compiler_code(refusal: str) -> str:
    return _COMPILER_CODES.get(refusal, "compiler_refusal")


def _schema_adapter(schema_version: str) -> TypeAdapter[object]:
    if schema_version == "chapter-proposal/1":
        return TypeAdapter(ChapterProposal)
    if schema_version == "chapter-proposal-compact/1":
        from temnia_pipeline.harness.models import CompactChapterProposal  # noqa: PLC0415

        return TypeAdapter(CompactChapterProposal)
    raise ValueError("unsupported proposal diagnostic schema")


def diagnose_response(  # noqa: PLR0911
    *,
    response: ModelResponse,
    schema_version: str,
    compiler_refusal: str | None,
) -> tuple[DiagnosticCode, str, tuple[ProposalDiagnosticIssue, ...], str | None]:
    """Classify a retained response without exposing its text or validation values."""
    if schema_version not in SUPPORTED_PROPOSAL_SCHEMAS:
        raise ValueError("unsupported proposal diagnostic schema")
    text_parts = [part for part in response.parts if isinstance(part, TextPart)]
    if len(text_parts) != 1:
        if response.finish_reason == "length":
            return (
                "output_limit",
                (
                    "Return one complete proposal within the output limit; "
                    "shorten titles, reasons, and quotes."
                ),
                (),
                None,
            )
        issue = ProposalDiagnosticIssue(path="$.parts", code="single_text_part_required")
        return (
            "invalid_schema",
            "Return one complete proposal object matching the required schema.",
            (issue,),
            None,
        )
    try:
        json.loads(text_parts[0].content)
    except (TypeError, ValueError):
        if response.finish_reason == "length":
            return (
                "output_limit",
                (
                    "Return one complete proposal within the output limit; "
                    "shorten titles, reasons, and quotes."
                ),
                (),
                None,
            )
        return (
            "invalid_json",
            "Return one complete JSON proposal object with no surrounding text.",
            (ProposalDiagnosticIssue(path="$", code="json_invalid"),),
            None,
        )
    try:
        _schema_adapter(schema_version).validate_json(text_parts[0].content, strict=True)
    except ValidationError as error:
        if response.finish_reason == "length":
            return (
                "output_limit",
                (
                    "Return one complete proposal within the output limit; "
                    "shorten titles, reasons, and quotes."
                ),
                (),
                None,
            )
        return (
            "invalid_schema",
            "Return one complete proposal object matching the required schema.",
            _validation_issues(error),
            None,
        )
    if compiler_refusal is None:
        raise ValueError("a valid retained proposal requires a compiler refusal")
    compiler_code = _compiler_code(compiler_refusal)
    return (
        "compiler_refusal",
        _COMPILER_MESSAGES[compiler_code],
        (),
        compiler_code,
    )


def validate_diagnostic_report(
    report: ProposalDiagnosticReport, response: ModelResponse
) -> ProposalDiagnosticReport:
    """Reproduce every response-derived report field without duplicating policy strings."""
    compiler_refusal: str | None = None
    if report.code == "compiler_refusal":
        if report.compilerCode not in _COMPILER_MESSAGES:
            raise ValueError("diagnostic has an unknown compiler code")
        compiler_refusal = next(
            (refusal for refusal, code in _COMPILER_CODES.items() if code == report.compilerCode),
            "unrecognized bounded compiler refusal",
        )
    code, message, issues, compiler_code = diagnose_response(
        response=response,
        schema_version=report.schemaVersion,
        compiler_refusal=compiler_refusal,
    )
    expected = (
        str(response.finish_reason) if response.finish_reason is not None else None,
        len(response.parts),
        sum(isinstance(part, TextPart) for part in response.parts),
        response.usage.input_tokens,
        response.usage.output_tokens,
        code,
        message,
        issues,
        compiler_code,
    )
    actual = (
        report.finishReason,
        report.responsePartCount,
        report.textPartCount,
        report.usage.inputTokens,
        report.usage.outputTokens,
        report.code,
        report.message,
        report.issues,
        report.compilerCode,
    )
    if actual != expected:
        raise ValueError("proposal diagnostic does not reproduce from its retained response")
    return report
