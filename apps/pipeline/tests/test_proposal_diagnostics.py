"""Content-free proposal diagnostics stay deterministic and bounded."""

from __future__ import annotations

import json
import uuid
from typing import Literal

import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.harness.proposal_diagnostics import (
    ProposalDiagnosticReport,
    ProposalUsageCounts,
    diagnose_response,
    validate_diagnostic_report,
)
from temnia_pipeline.harness.runtime_types import ProposalDiagnosticIssue


def _response(
    body: object, *, finish_reason: Literal["stop", "length"] | None = "stop"
) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(json.dumps(body, separators=(",", ":")))],
        usage=RequestUsage(input_tokens=7, output_tokens=11),
        finish_reason=finish_reason,
        model_name="fixture",
        provider_name="fixture",
    )


def _compact_section(**extra: object) -> dict[str, object]:
    return {
        "firstSentenceId": "sentence-0",
        "kind": "keep",
        "lastSentenceId": "sentence-1",
        "quoteWordIds": ["word-0"],
        "reason": "A short reason.",
        "title": "A short title",
        **extra,
    }


def test_valid_compact_json_reports_targeted_compiler_refusal() -> None:
    code, message, issues, compiler_code = diagnose_response(
        response=_response({"sections": [_compact_section()], "summary": "Summary", "version": 1}),
        schema_version="chapter-proposal-compact/1",
        compiler_refusal="proposal does not cover every evidence sentence",
    )

    assert code == "compiler_refusal"
    assert compiler_code == "incomplete_source_cover"
    assert message == "Return sections that cover every supplied source sentence."
    assert issues == ()

    code, _, _, compiler_code = diagnose_response(
        response=_response(
            {"sections": [_compact_section()], "summary": "Summary", "version": 1},
            finish_reason="length",
        ),
        schema_version="chapter-proposal-compact/1",
        compiler_refusal="proposal does not cover every evidence sentence",
    )
    assert code == "compiler_refusal"
    assert compiler_code == "incomplete_source_cover"


def test_output_limit_precedes_partial_json_and_exposes_no_content() -> None:
    response = ModelResponse(
        parts=[TextPart('{"sections":[{"title":"private')],
        usage=RequestUsage(input_tokens=100, output_tokens=8192),
        finish_reason="length",
    )
    code, message, issues, compiler_code = diagnose_response(
        response=response,
        schema_version="chapter-proposal-compact/1",
        compiler_refusal=None,
    )

    assert code == "output_limit"
    assert "private" not in message
    assert issues == ()
    assert compiler_code is None


def test_invalid_json_and_schema_paths_are_content_free_and_bounded() -> None:
    invalid_json = ModelResponse(parts=[TextPart("private model prose")])
    code, message, issues, _ = diagnose_response(
        response=invalid_json,
        schema_version="chapter-proposal-compact/1",
        compiler_refusal=None,
    )
    assert code == "invalid_json"
    assert "private" not in message
    assert [(issue.path, issue.code) for issue in issues] == [("$", "json_invalid")]

    invalid_schema = _response(
        {
            "private source text": "must never enter the diagnostic",
            "sections": [_compact_section(title=7)],
            "summary": "Summary",
            "version": 1,
        }
    )
    code, message, issues, _ = diagnose_response(
        response=invalid_schema,
        schema_version="chapter-proposal-compact/1",
        compiler_refusal=None,
    )
    assert code == "invalid_schema"
    assert "private" not in message
    assert len(issues) <= 32
    serialized = json.dumps([issue.model_dump(mode="json") for issue in issues])
    assert "private source text" not in serialized
    assert "must never enter" not in serialized
    assert "unexpected_field" in serialized
    assert "$.sections[0].title" in serialized


def test_valid_response_without_compiler_refusal_is_not_misdiagnosed() -> None:
    with pytest.raises(ValueError, match="requires a compiler refusal"):
        diagnose_response(
            response=_response(
                {"sections": [_compact_section()], "summary": "Summary", "version": 1}
            ),
            schema_version="chapter-proposal-compact/1",
            compiler_refusal=None,
        )


def test_unknown_schema_is_refused_before_reading_model_content() -> None:
    with pytest.raises(ValueError, match="unsupported proposal diagnostic schema"):
        diagnose_response(
            response=_response({"secret": "value"}),
            schema_version="unknown/1",
            compiler_refusal=None,
        )


def test_portable_report_reproduces_and_rejects_changed_counts() -> None:
    response = ModelResponse(
        parts=[TextPart("not-json")],
        usage=RequestUsage(input_tokens=7, output_tokens=11),
        finish_reason="stop",
    )
    reference = HarnessArtifactRef(
        id=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
        kind=HarnessArtifactKind.model_response,
        fingerprint="a" * 64,
        sha256="b" * 64,
        sizeBytes=10,
        storageKey="fixture/response.json",
    )
    evidence = reference.model_copy(update={"kind": HarnessArtifactKind.evidence})
    report = ProposalDiagnosticReport(
        runId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
        modelStage="proposal:v2:global",
        evidence=evidence,
        response=reference,
        operationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000003"),
        attemptId=uuid.UUID("0192e8a0-0000-7000-8000-000000000004"),
        routeId="fixture",
        promptVersion="chapter-propose-v3",
        schemaVersion="chapter-proposal-compact/1",
        maxOutputTokens=8192,
        finishReason="stop",
        responsePartCount=1,
        textPartCount=1,
        usage=ProposalUsageCounts(inputTokens=7, outputTokens=11),
        code="invalid_json",
        message="Return one complete JSON proposal object with no surrounding text.",
        issues=(ProposalDiagnosticIssue(path="$", code="json_invalid"),),
    )
    assert validate_diagnostic_report(report, response) is report
    with pytest.raises(ValueError, match="does not reproduce"):
        validate_diagnostic_report(
            report.model_copy(
                update={"usage": ProposalUsageCounts(inputTokens=7, outputTokens=12)}
            ),
            response,
        )
