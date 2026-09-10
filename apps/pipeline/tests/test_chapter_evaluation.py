from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Kind,
    ReviewState,
)
from temnia_pipeline.evals.chapter_report import build_report
from temnia_pipeline.evals.chapters import (
    AttemptFact,
    CheckArtifact,
    EditorialVerificationArtifact,
    EditorialVerificationBody,
    EvaluationBundle,
    HumanLabels,
    ImmutableArtifactFact,
    ProposalDiagnosticArtifact,
    SummaryGroundingArtifact,
    content_sha256,
    required_check_names,
    validate_bundle,
    validate_labels,
)
from temnia_pipeline.harness import bundle_export as bundle_export_module
from temnia_pipeline.harness import cli
from temnia_pipeline.harness.artifacts import canonical_json, fingerprint_for
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.gateway import CostObservation, GenerationIdentityError
from temnia_pipeline.harness.models import HierarchicalSummaryV1
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_summary_prompt,
    render_summary_reduction_prompt,
)
from temnia_pipeline.harness.proposal_diagnostics import (
    ProposalDiagnosticReport,
    ProposalUsageCounts,
    diagnostic_artifact_fingerprint,
)
from temnia_pipeline.harness.summary_grounding import (
    SummaryCoverageDiagnostic,
    SummaryCoverageFallback,
    SummaryFallback,
    SummaryGroundingReport,
    SummaryGroundingReportV2,
    grounding_artifact_fingerprint,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.scope import resolve_scope

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = UUID("10000000-0000-0000-0000-000000000001")
RUN = UUID("20000000-0000-0000-0000-000000000002")
ATTEMPT = UUID("30000000-0000-0000-0000-000000000003")
OPERATION = UUID("40000000-0000-0000-0000-000000000004")
RESULT = UUID("50000000-0000-0000-0000-000000000005")
EVIDENCE_ARTIFACT = UUID("60000000-0000-0000-0000-000000000006")
PRIOR_RESPONSE = UUID("51000000-0000-0000-0000-000000000005")
PRIOR_ATTEMPT = UUID("31000000-0000-0000-0000-000000000003")
PRIOR_OPERATION = UUID("41000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _evidence() -> HarnessEvidence:
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": 48000,
            "boundaries": [
                {
                    "clearanceMs": 0,
                    "id": "edge-start",
                    "kind": "edge",
                    "reasons": ["source_start"],
                    "requiresReview": False,
                    "score": 1.0,
                    "sentenceId": None,
                    "timeMs": 0,
                },
                {
                    "clearanceMs": 1000,
                    "id": "candidate-middle",
                    "kind": "pause",
                    "reasons": ["lexical_gap"],
                    "requiresReview": False,
                    "score": 0.8,
                    "sentenceId": "s1",
                    "timeMs": 5000,
                },
                {
                    "clearanceMs": 0,
                    "id": "edge-end",
                    "kind": "edge",
                    "reasons": ["source_end"],
                    "requiresReview": False,
                    "score": 1.0,
                    "sentenceId": None,
                    "timeMs": 10000,
                },
            ],
            "config": {
                "sourceObject": {"sha256": "d" * 64},
                "sourceTimeline": {"hasAudio": True, "hasVideo": True},
            },
            "durationMs": 10000,
            "frameRate": {"numerator": 25, "denominator": 1},
            "modelVersions": {"model:sentence": "fixture"},
            "pauses": [],
            "sentences": [
                {
                    "endMs": 1000,
                    "id": "s1",
                    "speakers": ["0"],
                    "startMs": 0,
                    "text": "first",
                    "wordIds": ["w1"],
                },
                {
                    "endMs": 6000,
                    "id": "s2",
                    "speakers": ["0"],
                    "startMs": 5000,
                    "text": "second",
                    "wordIds": ["w2"],
                },
            ],
            "shots": [],
            "sourceFingerprint": "a" * 64,
            "sourceId": str(SOURCE),
            "sourceStart": {"numerator": 0, "denominator": 1},
            "speechCoverage": {
                "detector": "fixture",
                "detectorHash": "b" * 64,
                "detectorRevision": "1",
                "intervals": [{"startMs": 0, "endMs": 6000}],
                "status": "clear",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": "70000000-0000-0000-0000-000000000007",
            "transcriptRevision": 1,
            "transcriptSha256": "c" * 64,
            "version": 1,
            "videoTimeBase": {"numerator": 1, "denominator": 12800},
            "words": [
                {
                    "confidence": 0.9,
                    "endMs": 1000,
                    "id": "w1",
                    "lineageIds": ["lw1"],
                    "speaker": "0",
                    "startMs": 0,
                    "text": "first",
                    "timing": "aligned",
                    "wordIndex": 0,
                },
                {
                    "confidence": 0.9,
                    "endMs": 6000,
                    "id": "w2",
                    "lineageIds": ["lw2"],
                    "speaker": "0",
                    "startMs": 5000,
                    "text": "second",
                    "timing": "aligned",
                    "wordIndex": 1,
                },
            ],
        }
    )


def _edit(evidence_sha: str) -> ChapterEditSpec:
    return ChapterEditSpec.model_validate(
        {
            "boundaries": [
                {
                    "candidateId": "edge-start",
                    "id": "b0",
                    "reasons": ["source_start"],
                    "requiresReview": False,
                    "time": {"numerator": 0, "denominator": 1},
                    "timeMs": 0,
                },
                {
                    "candidateId": "candidate-middle",
                    "id": "b1",
                    "reasons": ["lexical_gap"],
                    "requiresReview": False,
                    "time": {"numerator": 5, "denominator": 1},
                    "timeMs": 5000,
                },
                {
                    "candidateId": "edge-end",
                    "id": "b2",
                    "reasons": ["source_end"],
                    "requiresReview": False,
                    "time": {"numerator": 10, "denominator": 1},
                    "timeMs": 10000,
                },
            ],
            "compilerVersion": "compiler-fixture",
            "durationMs": 10000,
            "evidenceArtifactId": str(EVIDENCE_ARTIFACT),
            "evidenceSha256": evidence_sha,
            "sections": [
                {
                    "endBoundaryId": "b1",
                    "flags": [],
                    "id": "chapter-1",
                    "kind": "keep",
                    "quoteWordIds": ["w1"],
                    "reason": "first",
                    "reviewState": "accepted",
                    "startBoundaryId": "b0",
                    "title": "One",
                },
                {
                    "endBoundaryId": "b2",
                    "flags": [],
                    "id": "drop-1",
                    "kind": "drop",
                    "quoteWordIds": ["w2"],
                    "reason": "drop",
                    "reviewState": "proposed",
                    "startBoundaryId": "b1",
                    "title": "Drop",
                },
            ],
            "sourceAudioSampleRate": 48000,
            "sourceFrameRate": {"numerator": 25, "denominator": 1},
            "sourceId": str(SOURCE),
            "version": 1,
        }
    )


def _bundle(*, accepted: bool = False) -> EvaluationBundle:
    evidence = _evidence()
    evidence_sha = content_sha256(evidence)
    edit = _edit(evidence_sha)
    return EvaluationBundle.model_validate_json(
        json.dumps(
            {
                "format": "temnia-chapter-evaluation-bundle/1",
                "sourceId": str(SOURCE),
                "sourceFingerprint": "a" * 64,
                "durationMs": 10000,
                "sourceHasVideo": True,
                "sourceHasAudio": True,
                "runId": str(RUN),
                "observedAt": NOW.isoformat(),
                "status": "ready",
                "currentRevision": 1,
                "acceptedRevision": 1 if accepted else None,
                "evidenceSha256": evidence_sha,
                "evidence": evidence.model_dump(mode="json"),
                "editSha256": content_sha256(edit),
                "editRevision": 1,
                "edit": edit.model_dump(mode="json"),
                "checks": [],
                "reviewEvents": [
                    {
                        "action": "nudge",
                        "state": "applied",
                        "baseRevision": 0,
                        "resultingRevision": 1,
                        "createdAt": NOW.isoformat(),
                    },
                    {
                        "action": "accept",
                        "state": "applied",
                        "baseRevision": 1,
                        "resultingRevision": 1,
                        "createdAt": (NOW + timedelta(seconds=30)).isoformat(),
                    },
                ],
                "attempts": [
                    {
                        "id": str(ATTEMPT),
                        "operationId": str(OPERATION),
                        "attemptNumber": 1,
                        "state": "failed_known",
                        "provider": "gateway",
                        "model": "model-a",
                        "family": "family-a",
                        "routeId": "route-a",
                        "synthetic": False,
                        "estimatedCostMicros": 500,
                        "actualCostMicros": 123,
                        "costStatus": "reported",
                        "reservationActive": False,
                        "responsePresent": True,
                        "remoteHandle": "generation-a",
                        "resultArtifactId": None,
                        "usage": {"inputTokens": 11, "reasoningTokens": 3},
                        "dispatchedAt": NOW.isoformat(),
                        "finishedAt": (NOW + timedelta(seconds=2)).isoformat(),
                    },
                    {
                        "id": "30000000-0000-0000-0000-000000000004",
                        "operationId": "40000000-0000-0000-0000-000000000005",
                        "attemptNumber": 1,
                        "state": "outcome_unknown",
                        "provider": "gateway",
                        "model": "model-b",
                        "family": "family-b",
                        "routeId": "route-b",
                        "synthetic": False,
                        "estimatedCostMicros": 700,
                        "actualCostMicros": None,
                        "costStatus": "unknown",
                        "reservationActive": True,
                        "responsePresent": False,
                        "remoteHandle": "generation-b",
                        "resultArtifactId": None,
                        "usage": {"inputTokens": None, "reasoningTokens": None},
                        "dispatchedAt": NOW.isoformat(),
                        "finishedAt": None,
                    },
                    {
                        "id": "30000000-0000-0000-0000-000000000006",
                        "operationId": "40000000-0000-0000-0000-000000000007",
                        "attemptNumber": 1,
                        "state": "succeeded",
                        "provider": "recorded",
                        "model": "fixture",
                        "family": "fixture",
                        "routeId": "fixture",
                        "synthetic": True,
                        "estimatedCostMicros": 0,
                        "actualCostMicros": 0,
                        "costStatus": "reported",
                        "reservationActive": False,
                        "responsePresent": True,
                        "remoteHandle": None,
                        "resultArtifactId": str(RESULT),
                        "usage": {"outputTokens": 7},
                        "dispatchedAt": NOW.isoformat(),
                        "finishedAt": (NOW + timedelta(seconds=1)).isoformat(),
                    },
                ],
                "provenance": {
                    "sourceVersion": "sha256:a",
                    "modelVersions": {"family-a": "model-a"},
                    "compilerVersion": "compiler-fixture",
                    "promptVersions": {"propose": "v1"},
                    "routeSnapshotId": "route-snapshot",
                },
                "split": "test",
            }
        ),
        strict=True,
    )


def _bundle_with_grounding() -> EvaluationBundle:
    bundle = _bundle()
    assert bundle.evidence_sha256 is not None
    evidence_ref = HarnessArtifactRef(
        fingerprint="6" * 64,
        id=EVIDENCE_ARTIFACT,
        kind=HarnessArtifactKind.evidence,
        sha256=bundle.evidence_sha256,
        sizeBytes=100,
        storageKey=f"org/test/source/{SOURCE}/harness/evidence.json",
    )
    response_ref = HarnessArtifactRef(
        fingerprint="7" * 64,
        id=RESULT,
        kind=HarnessArtifactKind.model_response,
        sha256="8" * 64,
        sizeBytes=100,
        storageKey="private/model-response.json",
    )
    source_summary = HierarchicalSummaryV1.model_validate(
        {
            "version": 1,
            "units": [
                {
                    "id": "unit-one",
                    "firstSentenceId": "s1",
                    "lastSentenceId": "s1",
                    "quoteWordIds": ["w2"],
                    "text": "generated claim",
                },
                {
                    "id": "unit-two",
                    "firstSentenceId": "s2",
                    "lastSentenceId": "s2",
                    "quoteWordIds": ["w2"],
                    "text": "second",
                },
            ],
        }
    )
    normalized = source_summary.model_copy(
        update={
            "units": [
                source_summary.units[0].model_copy(
                    update={"quoteWordIds": ["w1"], "text": "first"}
                ),
                source_summary.units[1],
            ]
        }
    )
    evidence = _evidence()
    prompt = render_summary_prompt(
        PromptWindow(
            sourceId=SOURCE,
            evidenceSha256=bundle.evidence_sha256,
            windowId="window-0000",
            firstSentenceId="s1",
            lastSentenceId="s2",
            sentences=tuple(
                PromptSentence(
                    id=sentence.id,
                    text=sentence.text,
                    firstWordId=sentence.wordIds[0].root,
                    lastWordId=sentence.wordIds[-1].root,
                    speakers=tuple(sentence.speakers),
                )
                for sentence in evidence.sentences
            ),
        )
    )
    body = SummaryGroundingReport(
        runId=RUN,
        hierarchyLevel=1,
        modelStage="summary:window-0000",
        windowId="window-0000",
        firstSentenceId="s1",
        lastSentenceId="s2",
        windowSentenceCount=2,
        windowPromptSha256=hashlib.sha256(prompt.encode()).hexdigest(),
        evidence=evidence_ref,
        rawResponse=response_ref,
        sourceSummarySha256=content_sha256(source_summary),
        normalizedSummary=normalized,
        fallbacks=(
            SummaryFallback(
                unitId="unit-one",
                firstSentenceId="s1",
                lastSentenceId="s1",
                rejectedQuoteWordIds=("w2",),
                replacementQuoteWordIds=("w1",),
            ),
        ),
    )
    grounding = SummaryGroundingArtifact(
        artifact=ImmutableArtifactFact(
            id=UUID("90000000-0000-0000-0000-000000000009"),
            source_id=SOURCE,
            kind="checks",
            fingerprint=grounding_artifact_fingerprint(body),
            sha256=content_sha256(body),
            size_bytes=100,
            storage_key="private/grounding.json",
        ),
        body=body,
        dependencies=(
            ImmutableArtifactFact(
                id=evidence_ref.id,
                source_id=SOURCE,
                kind="evidence",
                fingerprint=evidence_ref.fingerprint,
                sha256=evidence_ref.sha256,
                size_bytes=evidence_ref.sizeBytes,
                storage_key=evidence_ref.storageKey,
            ),
            ImmutableArtifactFact(
                id=response_ref.id,
                source_id=SOURCE,
                kind="model_response",
                fingerprint=response_ref.fingerprint,
                sha256=response_ref.sha256,
                size_bytes=response_ref.sizeBytes,
                storage_key=response_ref.storageKey,
            ),
        ),
    )
    return bundle.model_copy(update={"summary_grounding": (grounding,)})


def _bundle_with_coverage_grounding() -> EvaluationBundle:
    bundle = _bundle_with_grounding()
    base = bundle.summary_grounding[0]
    source = base.body
    fallback_id = "coverage-fallback-" + hashlib.sha256(b"s1\0s2").hexdigest()[:24]
    replacement = HierarchicalSummaryV1.model_validate(
        {
            "version": 1,
            "units": [
                {
                    "id": fallback_id,
                    "firstSentenceId": "s1",
                    "lastSentenceId": "s2",
                    "quoteWordIds": ["w1", "w2"],
                    "text": "first second",
                }
            ],
        }
    )
    body = SummaryGroundingReportV2(
        runId=source.runId,
        hierarchyLevel=1,
        modelStage=source.modelStage,
        windowId=source.windowId,
        firstSentenceId=source.firstSentenceId,
        lastSentenceId=source.lastSentenceId,
        windowSentenceCount=source.windowSentenceCount,
        windowPromptSha256=source.windowPromptSha256,
        evidence=source.evidence,
        rawResponse=source.rawResponse,
        sourceSummarySha256=source.sourceSummarySha256,
        normalizedSummary=replacement,
        coverageDiagnostic=SummaryCoverageDiagnostic(
            originalUnitCount=2,
            coveredSentenceCount=1,
            gapSentenceCount=1,
            overlapSentenceCount=0,
            orderingViolationCount=0,
        ),
        coverageFallback=SummaryCoverageFallback(
            unitId=fallback_id,
            firstSentenceId="s1",
            lastSentenceId="s2",
            replacementQuoteWordIds=("w1", "w2"),
        ),
    )
    return bundle.model_copy(
        update={
            "summary_grounding": (
                base.model_copy(
                    update={
                        "body": body,
                        "artifact": base.artifact.model_copy(
                            update={
                                "fingerprint": grounding_artifact_fingerprint(body),
                                "sha256": content_sha256(body),
                            }
                        ),
                    }
                ),
            )
        }
    )


def _bundle_with_proposal_diagnostic() -> EvaluationBundle:
    bundle = _bundle()
    assert bundle.evidence_sha256 is not None
    evidence_ref = HarnessArtifactRef(
        fingerprint="6" * 64,
        id=EVIDENCE_ARTIFACT,
        kind=HarnessArtifactKind.evidence,
        sha256=bundle.evidence_sha256,
        sizeBytes=100,
        storageKey=f"org/test/source/{SOURCE}/harness/evidence.json",
    )
    response_ref = HarnessArtifactRef(
        fingerprint="7" * 64,
        id=RESULT,
        kind=HarnessArtifactKind.model_response,
        sha256="8" * 64,
        sizeBytes=200,
        storageKey=f"org/test/source/{SOURCE}/harness/proposal-response.json",
    )
    prior_response_ref = HarnessArtifactRef(
        fingerprint="9" * 64,
        id=PRIOR_RESPONSE,
        kind=HarnessArtifactKind.model_response,
        sha256="a" * 64,
        sizeBytes=180,
        storageKey=f"org/test/source/{SOURCE}/harness/prior-proposal-response.json",
    )
    attempt = next(item for item in bundle.attempts if item.result_artifact_id == RESULT)
    prior_attempt = attempt.model_copy(
        update={
            "id": PRIOR_ATTEMPT,
            "operation_id": PRIOR_OPERATION,
            "result_artifact_id": PRIOR_RESPONSE,
        }
    )
    body = ProposalDiagnosticReport(
        runId=RUN,
        modelStage="proposal:global",
        evidence=evidence_ref,
        response=response_ref,
        inputArtifacts=(prior_response_ref,),
        operationId=attempt.operation_id,
        attemptId=attempt.id,
        providerResponseId=attempt.remote_handle,
        routeId=cast("str", attempt.route_id),
        promptVersion="chapter-propose-compact-v1",
        schemaVersion="chapter-proposal-compact/1",
        maxOutputTokens=8192,
        finishReason="length",
        responsePartCount=1,
        textPartCount=1,
        usage=ProposalUsageCounts(inputTokens=100, outputTokens=8192),
        code="output_limit",
        message=(
            "Return one complete proposal within the output limit; shorten titles, reasons, and "
            "quotes."
        ),
    )
    diagnostic = ProposalDiagnosticArtifact(
        artifact=ImmutableArtifactFact(
            id=UUID("91000000-0000-0000-0000-000000000009"),
            source_id=SOURCE,
            kind="checks",
            fingerprint=diagnostic_artifact_fingerprint(body),
            sha256=content_sha256(body),
            size_bytes=len(canonical_json(body.model_dump(mode="json"))),
            storage_key=f"org/test/source/{SOURCE}/harness/proposal-diagnostic.json",
        ),
        body=body,
        dependencies=(
            ImmutableArtifactFact(
                id=evidence_ref.id,
                source_id=SOURCE,
                kind="evidence",
                fingerprint=evidence_ref.fingerprint,
                sha256=evidence_ref.sha256,
                size_bytes=evidence_ref.sizeBytes,
                storage_key=evidence_ref.storageKey,
            ),
            ImmutableArtifactFact(
                id=response_ref.id,
                source_id=SOURCE,
                kind="model_response",
                fingerprint=response_ref.fingerprint,
                sha256=response_ref.sha256,
                size_bytes=response_ref.sizeBytes,
                storage_key=response_ref.storageKey,
            ),
            ImmutableArtifactFact(
                id=prior_response_ref.id,
                source_id=SOURCE,
                kind="model_response",
                fingerprint=prior_response_ref.fingerprint,
                sha256=prior_response_ref.sha256,
                size_bytes=prior_response_ref.sizeBytes,
                storage_key=prior_response_ref.storageKey,
            ),
        ),
    )
    return bundle.model_copy(
        update={
            "attempts": (*bundle.attempts, prior_attempt),
            "proposal_diagnostics": (diagnostic,),
        }
    )


def test_bundle_rejects_corruption_mixed_source_and_invalid_cover() -> None:
    bundle = _bundle()
    validate_bundle(bundle)
    with pytest.raises(HarnessValidationError, match="evidence body hash"):
        validate_bundle(bundle.model_copy(update={"evidence_sha256": "d" * 64}))
    assert bundle.edit is not None
    other = bundle.edit.model_copy(update={"sourceId": UUID(int=9)})
    mixed = bundle.model_copy(update={"edit": other, "edit_sha256": content_sha256(other)})
    with pytest.raises(HarnessValidationError, match="different sources"):
        validate_bundle(mixed)
    broken = bundle.edit.model_copy(update={"boundaries": bundle.edit.boundaries[:-1]})
    invalid = bundle.model_copy(update={"edit": broken, "edit_sha256": content_sha256(broken)})
    with pytest.raises(HarnessValidationError, match="boundaries and sections"):
        validate_bundle(invalid)


def test_old_bundle_defaults_to_no_summary_grounding() -> None:
    raw = _bundle().model_dump(mode="json", by_alias=True)
    raw.pop("summaryGrounding")
    raw.pop("proposalDiagnostics")
    bundle = EvaluationBundle.model_validate_json(json.dumps(raw), strict=True)
    assert bundle.summary_grounding == ()
    assert bundle.proposal_diagnostics == ()
    assert build_report(bundle).summary_grounding is None


def test_proposal_diagnostic_is_portable_and_bound_to_accepted_response() -> None:
    bundle = _bundle_with_proposal_diagnostic()
    validate_bundle(bundle)
    item = bundle.proposal_diagnostics[0]
    assert item.artifact.size_bytes is not None

    without_prior_attempt = bundle.model_copy(
        update={
            "attempts": tuple(attempt for attempt in bundle.attempts if attempt.id != PRIOR_ATTEMPT)
        }
    )
    with pytest.raises(HarnessValidationError, match="input response is not an accepted"):
        validate_bundle(without_prior_attempt)

    changed_body = item.body.model_copy(update={"message": f"X{item.body.message[1:]}"})
    with pytest.raises(HarnessValidationError, match="body hash"):
        validate_bundle(
            bundle.model_copy(
                update={"proposal_diagnostics": (item.model_copy(update={"body": changed_body}),)}
            )
        )

    response_dependency = item.dependencies[1]
    changed_dependency = response_dependency.model_copy(
        update={"storage_key": f"org/test/source/{SOURCE}/harness/changed-response.json"}
    )
    with pytest.raises(HarnessValidationError, match="dependency identity"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "proposal_diagnostics": (
                        item.model_copy(
                            update={
                                "dependencies": (
                                    item.dependencies[0],
                                    changed_dependency,
                                    item.dependencies[2],
                                )
                            }
                        ),
                    )
                }
            )
        )

    for artifact, match in (
        (item.artifact.model_copy(update={"size_bytes": item.artifact.size_bytes + 1}), "size"),
        (item.artifact.model_copy(update={"storage_key": "../diagnostic.json"}), "storage path"),
        (item.artifact.model_copy(update={"source_id": UUID(int=99)}), "crosses source"),
    ):
        with pytest.raises(HarnessValidationError, match=match):
            validate_bundle(
                bundle.model_copy(
                    update={
                        "proposal_diagnostics": (item.model_copy(update={"artifact": artifact}),)
                    }
                )
            )

    unsupported_body = item.body.model_copy(update={"schemaVersion": "unknown-schema/1"})
    unsupported_artifact = item.artifact.model_copy(
        update={
            "fingerprint": diagnostic_artifact_fingerprint(unsupported_body),
            "sha256": content_sha256(unsupported_body),
            "size_bytes": len(canonical_json(unsupported_body.model_dump(mode="json"))),
        }
    )
    with pytest.raises(HarnessValidationError, match="schema is unsupported"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "proposal_diagnostics": (
                        item.model_copy(
                            update={"artifact": unsupported_artifact, "body": unsupported_body}
                        ),
                    )
                }
            )
        )

    wrong_attempt_body = item.body.model_copy(update={"attemptId": UUID(int=98)})
    wrong_attempt_artifact = item.artifact.model_copy(
        update={
            "sha256": content_sha256(wrong_attempt_body),
            "size_bytes": len(canonical_json(wrong_attempt_body.model_dump(mode="json"))),
        }
    )
    with pytest.raises(HarnessValidationError, match="accepted run attempt"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "proposal_diagnostics": (
                        item.model_copy(
                            update={
                                "artifact": wrong_attempt_artifact,
                                "body": wrong_attempt_body,
                            }
                        ),
                    )
                }
            )
        )

    foreign_body = item.body.model_copy(update={"runId": UUID(int=99)})
    foreign_artifact = item.artifact.model_copy(
        update={
            "fingerprint": diagnostic_artifact_fingerprint(foreign_body),
            "sha256": content_sha256(foreign_body),
        }
    )
    with pytest.raises(HarnessValidationError, match="different run"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "proposal_diagnostics": (
                        item.model_copy(
                            update={"artifact": foreign_artifact, "body": foreign_body}
                        ),
                    )
                }
            )
        )


def test_summary_grounding_is_portable_and_reports_reference_outcomes() -> None:
    bundle = _bundle_with_grounding()
    validate_bundle(bundle)
    metrics = build_report(bundle).summary_grounding
    assert metrics is not None
    assert metrics.report_count == 1
    assert metrics.summary_unit_count == 2
    assert metrics.first_pass_reference_valid_report_count == 0
    assert metrics.first_pass_reference_valid_unit_count == 1
    assert metrics.extractive_fallback_report_count == 1
    assert metrics.extractive_fallback_unit_count == 1
    assert metrics.coverage_fallback_report_count == 0
    assert metrics.coverage_fallback_unit_count == 0
    assert metrics.rejected_quote_anchor_count == 1


def test_summary_coverage_fallback_is_distinct_and_source_exact() -> None:
    bundle = _bundle_with_coverage_grounding()
    validate_bundle(bundle)
    metrics = build_report(bundle).summary_grounding
    assert metrics is not None
    assert metrics.first_pass_reference_valid_report_count == 0
    assert metrics.first_pass_reference_valid_unit_count == 0
    assert metrics.extractive_fallback_report_count == 0
    assert metrics.extractive_fallback_unit_count == 0
    assert metrics.coverage_fallback_report_count == 1
    assert metrics.coverage_fallback_unit_count == 1
    assert metrics.rejected_quote_anchor_count == 0

    item = bundle.summary_grounding[0]
    assert isinstance(item.body, SummaryGroundingReportV2)
    changed = item.body.model_copy(
        update={
            "coverageDiagnostic": item.body.coverageDiagnostic.model_copy(
                update={"gapSentenceCount": 0}
            )
        }
    )
    with pytest.raises(HarnessValidationError, match="coverage diagnostic"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "summary_grounding": (
                        item.model_copy(
                            update={
                                "body": changed,
                                "artifact": item.artifact.model_copy(
                                    update={
                                        "fingerprint": grounding_artifact_fingerprint(changed),
                                        "sha256": content_sha256(changed),
                                    }
                                ),
                            }
                        ),
                    )
                }
            )
        )


def test_summary_grounding_reduction_uses_only_contained_prior_windows() -> None:
    bundle = _bundle_with_grounding()
    assert bundle.evidence is not None
    base = bundle.summary_grounding[0]
    first_prompt = render_summary_prompt(
        PromptWindow(
            sourceId=SOURCE,
            evidenceSha256=base.body.evidence.sha256,
            windowId="window-0000",
            firstSentenceId="s1",
            lastSentenceId="s1",
            sentences=(
                PromptSentence(
                    id="s1",
                    text="first",
                    firstWordId="w1",
                    lastWordId="w1",
                    speakers=("0",),
                ),
            ),
        )
    )
    first_body = base.body.model_copy(
        update={
            "lastSentenceId": "s1",
            "windowSentenceCount": 1,
            "windowPromptSha256": hashlib.sha256(first_prompt.encode()).hexdigest(),
            "normalizedSummary": base.body.normalizedSummary.model_copy(
                update={"units": base.body.normalizedSummary.units[:1]}
            ),
        }
    )
    first = base.model_copy(
        update={
            "body": first_body,
            "artifact": base.artifact.model_copy(
                update={
                    "fingerprint": grounding_artifact_fingerprint(first_body),
                    "sha256": content_sha256(first_body),
                }
            ),
        }
    )
    second_prompt = render_summary_prompt(
        PromptWindow(
            sourceId=SOURCE,
            evidenceSha256=base.body.evidence.sha256,
            windowId="window-0001",
            firstSentenceId="s2",
            lastSentenceId="s2",
            sentences=(
                PromptSentence(
                    id="s2",
                    text="second",
                    firstWordId="w2",
                    lastWordId="w2",
                    speakers=("0",),
                ),
            ),
        )
    )
    second_body = base.body.model_copy(
        update={
            "modelStage": "summary:window-0001",
            "windowId": "window-0001",
            "firstSentenceId": "s2",
            "lastSentenceId": "s2",
            "windowSentenceCount": 1,
            "windowPromptSha256": hashlib.sha256(second_prompt.encode()).hexdigest(),
            "normalizedSummary": base.body.normalizedSummary.model_copy(
                update={"units": base.body.normalizedSummary.units[1:]}
            ),
            "fallbacks": (),
        }
    )
    second = base.model_copy(
        update={
            "body": second_body,
            "artifact": base.artifact.model_copy(
                update={
                    "id": UUID("90000000-0000-0000-0000-000000000010"),
                    "fingerprint": grounding_artifact_fingerprint(second_body),
                    "sha256": content_sha256(second_body),
                }
            ),
        }
    )

    def report_ref(item: SummaryGroundingArtifact) -> HarnessArtifactRef:
        assert item.artifact.size_bytes is not None
        assert item.artifact.storage_key is not None
        return HarnessArtifactRef(
            fingerprint=item.artifact.fingerprint,
            id=item.artifact.id,
            kind=HarnessArtifactKind.checks,
            sha256=item.artifact.sha256,
            sizeBytes=item.artifact.size_bytes,
            storageKey=item.artifact.storage_key,
        )

    parent_prompt = render_summary_reduction_prompt(
        source_id=SOURCE,
        evidence_sha256=first_body.evidence.sha256,
        summaries=[first_body.normalizedSummary.model_dump(mode="json")],
        hierarchy_level=2,
    )
    parent_body = first_body.model_copy(
        update={
            "hierarchyLevel": 2,
            "modelStage": "summary:level:2:reduction-0000",
            "windowId": "reduction-0000",
            "windowPromptSha256": hashlib.sha256(parent_prompt.encode()).hexdigest(),
            "inputArtifacts": (report_ref(first), report_ref(second)),
            "fallbacks": (),
        }
    )
    parent = base.model_copy(
        update={
            "body": parent_body,
            "artifact": base.artifact.model_copy(
                update={
                    "id": UUID("90000000-0000-0000-0000-000000000011"),
                    "fingerprint": grounding_artifact_fingerprint(parent_body),
                    "sha256": content_sha256(parent_body),
                }
            ),
            "dependencies": (
                *base.dependencies,
                first.artifact,
                second.artifact,
            ),
        }
    )

    validate_bundle(bundle.model_copy(update={"summary_grounding": (first, second, parent)}))


def test_summary_grounding_rejects_corrupt_identity_lineage_and_fallback() -> None:
    bundle = _bundle_with_grounding()
    item = bundle.summary_grounding[0]
    with pytest.raises(HarnessValidationError, match="body hash"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "summary_grounding": (
                        item.model_copy(
                            update={
                                "artifact": item.artifact.model_copy(update={"sha256": "0" * 64})
                            }
                        ),
                    )
                }
            )
        )
    with pytest.raises(HarnessValidationError, match="dependency closure"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "summary_grounding": (
                        item.model_copy(update={"dependencies": item.dependencies[:-1]}),
                    )
                }
            )
        )
    bad_summary = item.body.normalizedSummary.model_copy(
        update={
            "units": [
                item.body.normalizedSummary.units[0].model_copy(
                    update={"text": "not the exact source excerpt"}
                ),
                item.body.normalizedSummary.units[1],
            ]
        }
    )
    bad_body = item.body.model_copy(update={"normalizedSummary": bad_summary})
    bad_item = item.model_copy(
        update={
            "body": bad_body,
            "artifact": item.artifact.model_copy(
                update={
                    "fingerprint": grounding_artifact_fingerprint(bad_body),
                    "sha256": content_sha256(bad_body),
                }
            ),
        }
    )
    with pytest.raises(HarnessValidationError, match="fallback content"):
        validate_bundle(bundle.model_copy(update={"summary_grounding": (bad_item,)}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_bytes", 101),
        ("storage_key", "private/changed-evidence.json"),
    ],
)
def test_summary_grounding_rejects_changed_dependency_location(
    field: str, value: int | str
) -> None:
    bundle = _bundle_with_grounding()
    item = bundle.summary_grounding[0]
    changed = item.dependencies[0].model_copy(update={field: value})
    with pytest.raises(HarnessValidationError, match="dependency identity"):
        validate_bundle(
            bundle.model_copy(
                update={
                    "summary_grounding": (
                        item.model_copy(update={"dependencies": (changed, *item.dependencies[1:])}),
                    )
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["v1", "v2"])
async def test_exporter_includes_revision_zero_summary_grounding_with_raw_lineage(
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    coverage_failure = policy == "v2"
    fixture = _bundle_with_coverage_grounding() if coverage_failure else _bundle_with_grounding()
    grounding = fixture.summary_grounding[0]
    assert fixture.evidence is not None
    evidence_ref = grounding.body.evidence
    response_ref = grounding.body.rawResponse
    source_summary = HierarchicalSummaryV1.model_validate(
        {
            "version": 1,
            "units": [
                {
                    "id": "unit-one",
                    "firstSentenceId": "s1",
                    "lastSentenceId": "s1",
                    "quoteWordIds": ["w2"],
                    "text": "generated claim",
                },
                {
                    "id": "unit-two",
                    "firstSentenceId": "s2",
                    "lastSentenceId": "s2",
                    "quoteWordIds": ["w2"],
                    "text": "second",
                },
            ],
        }
    )
    if coverage_failure:
        source_summary = source_summary.model_copy(update={"units": source_summary.units[:1]})
        assert isinstance(grounding.body, SummaryGroundingReportV2)
        grounding_body = grounding.body.model_copy(
            update={
                "sourceSummarySha256": content_sha256(source_summary),
                "coverageDiagnostic": grounding.body.coverageDiagnostic.model_copy(
                    update={"originalUnitCount": 1}
                ),
            }
        )
        grounding = grounding.model_copy(
            update={
                "body": grounding_body,
                "artifact": grounding.artifact.model_copy(
                    update={
                        "fingerprint": grounding_artifact_fingerprint(grounding_body),
                        "sha256": content_sha256(grounding_body),
                    }
                ),
            }
        )
        fixture = fixture.model_copy(update={"summary_grounding": (grounding,)})
    response_body = MODEL_RESPONSE_ADAPTER.dump_python(
        ModelResponse(
            parts=[TextPart(json.dumps(source_summary.model_dump(mode="json")))],
            model_name="fixture",
            provider_name="recorded",
        ),
        mode="json",
    )
    grounding_id = grounding.artifact.id
    rows: dict[UUID, dict[str, Any]] = {
        evidence_ref.id: {
            "id": evidence_ref.id,
            "source_id": SOURCE,
            "kind": "evidence",
            "fingerprint": evidence_ref.fingerprint,
            "sha256": evidence_ref.sha256,
            "size_bytes": evidence_ref.sizeBytes,
            "storage_key": evidence_ref.storageKey,
            "metadata": {"format": "harness-evidence/1"},
        },
        response_ref.id: {
            "id": response_ref.id,
            "source_id": SOURCE,
            "kind": "model_response",
            "fingerprint": response_ref.fingerprint,
            "sha256": response_ref.sha256,
            "size_bytes": response_ref.sizeBytes,
            "storage_key": response_ref.storageKey,
            "metadata": {
                "cassetteMode": "off",
                "promptVersion": "summary-v1",
                "synthetic": True,
            },
        },
        grounding_id: {
            "id": grounding_id,
            "source_id": SOURCE,
            "kind": "checks",
            "fingerprint": grounding.artifact.fingerprint,
            "sha256": grounding.artifact.sha256,
            "size_bytes": 100,
            "storage_key": "private/grounding.json",
            "metadata": {
                "format": grounding.body.format,
                "runId": str(RUN),
                "windowId": grounding.body.windowId,
                "hierarchyLevel": grounding.body.hierarchyLevel,
                "modelStage": grounding.body.modelStage,
                "fallbackUnitCount": 1,
                "fallbackQuoteCount": int(not coverage_failure),
                **({"coverageFallbackWindowCount": 1} if coverage_failure else {}),
            },
        },
    }
    snapshot_type = vars(bundle_export_module)["_Snapshot"]
    snapshot = snapshot_type(
        run={
            "id": RUN,
            "source_id": SOURCE,
            "evidence_artifact_id": EVIDENCE_ARTIFACT,
            "status": "failed",
            "current_revision": 0,
            "accepted_revision": None,
            "config": {"routeSnapshotId": "route-snapshot"},
        },
        source={"id": SOURCE, "duration_ms": 10000},
        observed_at=NOW,
        revision=None,
        descriptor_id=None,
        verification_id=None,
        grounding_ids=(grounding_id,),
        diagnostic_ids=(),
        rows=rows,
        dependencies={grounding_id: (evidence_ref.id, response_ref.id)},
        attempts=(
            {
                "id": UUID("30000000-0000-0000-0000-000000000006"),
                "operation_id": UUID("40000000-0000-0000-0000-000000000007"),
                "attempt_number": 1,
                "state": "succeeded",
                "provider": "recorded",
                "model": "fixture",
                "family": "fixture",
                "route": {"id": "fixture"},
                "estimated_cost_micros": 0,
                "actual_cost_micros": 0,
                "cost_status": "reported",
                "reservation_state": None,
                "result_artifact_id": response_ref.id,
                "remote_handle": None,
                "usage": {"outputTokens": 7},
                "dispatched_at": NOW,
                "finished_at": NOW + timedelta(seconds=1),
            },
        ),
        events=(),
    )
    bodies = {
        evidence_ref.id: cast("HarnessEvidence", fixture.evidence).model_dump(mode="json"),
        response_ref.id: response_body,
        grounding_id: grounding.body.model_dump(mode="json"),
    }

    async def read_snapshot(*_args: object, **_kwargs: object) -> object:
        return snapshot

    async def read_artifact_json(*_args: object, **kwargs: object) -> object:
        return bodies[cast("UUID", kwargs["artifact_id"])]

    monkeypatch.setattr(bundle_export_module, "_read_snapshot", read_snapshot)
    monkeypatch.setattr(bundle_export_module.artifacts, "read_artifact_json", read_artifact_json)
    exported = await bundle_export_module.export_evaluation_bundle(
        "postgresql://unused",
        scope=resolve_scope(),
        store=cast("Any", object()),
        run_id=RUN,
    )
    assert exported.current_revision == 0
    assert exported.status == "failed"
    assert exported.summary_grounding == (grounding,)
    changed_summary = source_summary.model_copy(
        update={
            "units": [
                source_summary.units[0].model_copy(update={"text": "changed raw bytes"}),
                *source_summary.units[1:],
            ]
        }
    )
    bodies[response_ref.id] = MODEL_RESPONSE_ADAPTER.dump_python(
        ModelResponse(
            parts=[TextPart(json.dumps(changed_summary.model_dump(mode="json")))],
            model_name="fixture",
            provider_name="recorded",
        ),
        mode="json",
    )
    with pytest.raises(ValueError, match="source summary hash"):
        await bundle_export_module.export_evaluation_bundle(
            "postgresql://unused",
            scope=resolve_scope(),
            store=cast("Any", object()),
            run_id=RUN,
        )


@pytest.mark.asyncio
async def test_exporter_includes_revision_zero_proposal_diagnostic_with_raw_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _bundle_with_proposal_diagnostic()
    diagnostic = fixture.proposal_diagnostics[0]
    assert fixture.evidence is not None
    evidence_ref = diagnostic.body.evidence
    response_ref = diagnostic.body.response
    prior_response_ref = diagnostic.body.inputArtifacts[0]
    response = ModelResponse(
        parts=[TextPart('{"sections":[{"title":"private')],
        usage=RequestUsage(input_tokens=100, output_tokens=8192),
        finish_reason="length",
        model_name="fixture",
        provider_name="recorded",
    )
    response_body = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json")
    rows: dict[UUID, dict[str, Any]] = {
        evidence_ref.id: {
            "id": evidence_ref.id,
            "source_id": SOURCE,
            "kind": "evidence",
            "fingerprint": evidence_ref.fingerprint,
            "sha256": evidence_ref.sha256,
            "size_bytes": evidence_ref.sizeBytes,
            "storage_key": evidence_ref.storageKey,
            "metadata": {"format": "harness-evidence/1"},
        },
        response_ref.id: {
            "id": response_ref.id,
            "source_id": SOURCE,
            "kind": "model_response",
            "fingerprint": response_ref.fingerprint,
            "sha256": response_ref.sha256,
            "size_bytes": response_ref.sizeBytes,
            "storage_key": response_ref.storageKey,
            "metadata": {
                "attemptId": str(diagnostic.body.attemptId),
                "operationId": str(diagnostic.body.operationId),
                "runId": str(RUN),
                "routeId": diagnostic.body.routeId,
                "promptVersion": diagnostic.body.promptVersion,
                "schemaVersion": diagnostic.body.schemaVersion,
                "maxOutputTokens": diagnostic.body.maxOutputTokens,
                "synthetic": True,
            },
        },
        prior_response_ref.id: {
            "id": prior_response_ref.id,
            "source_id": SOURCE,
            "kind": "model_response",
            "fingerprint": prior_response_ref.fingerprint,
            "sha256": prior_response_ref.sha256,
            "size_bytes": prior_response_ref.sizeBytes,
            "storage_key": prior_response_ref.storageKey,
            "metadata": {
                "runId": str(RUN),
                "synthetic": True,
            },
        },
        diagnostic.artifact.id: {
            "id": diagnostic.artifact.id,
            "source_id": SOURCE,
            "kind": "checks",
            "fingerprint": diagnostic.artifact.fingerprint,
            "sha256": diagnostic.artifact.sha256,
            "size_bytes": diagnostic.artifact.size_bytes,
            "storage_key": diagnostic.artifact.storage_key,
            "metadata": {
                "attemptId": str(diagnostic.body.attemptId),
                "code": diagnostic.body.code,
                "evidenceArtifactId": str(evidence_ref.id),
                "format": diagnostic.body.format,
                "maxOutputTokens": diagnostic.body.maxOutputTokens,
                "modelStage": diagnostic.body.modelStage,
                "operationId": str(diagnostic.body.operationId),
                "promptVersion": diagnostic.body.promptVersion,
                "responseArtifactId": str(response_ref.id),
                "routeId": diagnostic.body.routeId,
                "runId": str(RUN),
                "schemaVersion": diagnostic.body.schemaVersion,
            },
        },
    }
    snapshot_type = vars(bundle_export_module)["_Snapshot"]
    snapshot = snapshot_type(
        run={
            "id": RUN,
            "source_id": SOURCE,
            "evidence_artifact_id": EVIDENCE_ARTIFACT,
            "status": "failed",
            "current_revision": 0,
            "accepted_revision": None,
            "config": {"routeSnapshotId": "route-snapshot"},
        },
        source={"id": SOURCE, "duration_ms": 10000},
        observed_at=NOW,
        revision=None,
        descriptor_id=None,
        verification_id=None,
        grounding_ids=(),
        diagnostic_ids=(diagnostic.artifact.id,),
        rows=rows,
        dependencies={
            diagnostic.artifact.id: (
                evidence_ref.id,
                response_ref.id,
                prior_response_ref.id,
            )
        },
        attempts=(
            {
                "id": diagnostic.body.attemptId,
                "operation_id": diagnostic.body.operationId,
                "attempt_number": 1,
                "state": "succeeded",
                "provider": "recorded",
                "model": "fixture",
                "family": "fixture",
                "route": {"id": diagnostic.body.routeId},
                "estimated_cost_micros": 0,
                "actual_cost_micros": 0,
                "cost_status": "reported",
                "reservation_state": None,
                "result_artifact_id": response_ref.id,
                "remote_handle": None,
                "usage": {"inputTokens": 100, "outputTokens": 8192},
                "dispatched_at": NOW,
                "finished_at": NOW + timedelta(seconds=1),
            },
            {
                "id": PRIOR_ATTEMPT,
                "operation_id": PRIOR_OPERATION,
                "attempt_number": 1,
                "state": "succeeded",
                "provider": "recorded",
                "model": "fixture",
                "family": "fixture",
                "route": {"id": "fixture"},
                "estimated_cost_micros": 0,
                "actual_cost_micros": 0,
                "cost_status": "reported",
                "reservation_state": None,
                "result_artifact_id": prior_response_ref.id,
                "remote_handle": None,
                "usage": {"inputTokens": 50, "outputTokens": 10},
                "dispatched_at": NOW,
                "finished_at": NOW + timedelta(seconds=1),
            },
        ),
        events=(),
    )
    bodies = {
        evidence_ref.id: fixture.evidence.model_dump(mode="json"),
        response_ref.id: response_body,
        prior_response_ref.id: MODEL_RESPONSE_ADAPTER.dump_python(
            ModelResponse(
                parts=[TextPart('{"sections":[]}')],
                usage=RequestUsage(input_tokens=50, output_tokens=10),
                finish_reason="stop",
                model_name="fixture",
                provider_name="recorded",
            ),
            mode="json",
        ),
        diagnostic.artifact.id: diagnostic.body.model_dump(mode="json"),
    }

    async def read_snapshot(*_args: object, **_kwargs: object) -> object:
        return snapshot

    async def read_artifact_json(*_args: object, **kwargs: object) -> object:
        return bodies[cast("UUID", kwargs["artifact_id"])]

    monkeypatch.setattr(bundle_export_module, "_read_snapshot", read_snapshot)
    monkeypatch.setattr(bundle_export_module.artifacts, "read_artifact_json", read_artifact_json)
    exported = await bundle_export_module.export_evaluation_bundle(
        "postgresql://unused",
        scope=resolve_scope(),
        store=cast("Any", object()),
        run_id=RUN,
    )
    assert exported.current_revision == 0
    assert exported.status == "failed"
    assert exported.proposal_diagnostics == (diagnostic,)

    bodies[response_ref.id] = MODEL_RESPONSE_ADAPTER.dump_python(
        replace(response, finish_reason="stop"), mode="json"
    )
    with pytest.raises(ValueError, match="differs from its retained response"):
        await bundle_export_module.export_evaluation_bundle(
            "postgresql://unused",
            scope=resolve_scope(),
            store=cast("Any", object()),
            run_id=RUN,
        )


def test_report_keeps_unknown_labels_cost_and_event_span_distinct() -> None:
    report = build_report(_bundle())
    assert report.boundary_metrics.accepted_count is None
    assert report.boundary_metrics.correction_active_seconds is None
    assert report.event_elapsed_span_seconds == 30
    assert report.review_nudge_count == 1
    assert report.accepted_chapter_count is None
    assert report.money.known_spent_micros == 123
    assert report.money.active_reserved_exposure_micros == 700
    assert report.money.live_dispatch_count == 2
    assert report.money.synthetic_attempt_count == 1
    assert report.money.replayed_attempt_count == 0
    assert report.money.missing_cost_count == 1
    assert report.money.cost_per_accepted_chapter_micros is None
    assert report.money.live_cost_comparison_status == "incomplete"
    assert report.money.usage_totals == {
        "inputTokens": 11,
        "outputTokens": 7,
        "reasoningTokens": 3,
    }


def test_attempt_usage_accepts_real_nested_shape_and_aggregates_only_tokens() -> None:
    bundle = _bundle()
    nested = {
        "cache_audio_read_tokens": 0,
        "cache_read_tokens": 2,
        "cache_write_tokens": 3,
        "cost": 999,
        "details": {"vendor": {"unmodeled": True}},
        "gateway": {
            "actual_cost_micros": 123,
            "components": {"price": 42},
            "status": "reported",
        },
        "input_audio_tokens": 5,
        "input_tokens": 387,
        "output_audio_tokens": 7,
        "output_tokens": 132,
    }
    first = bundle.attempts[0].model_copy(update={"usage": nested})
    updated = bundle.model_copy(update={"attempts": (first, *bundle.attempts[1:])})
    reparsed = EvaluationBundle.model_validate_json(
        updated.model_dump_json(by_alias=True), strict=True
    )
    report = build_report(reparsed)
    assert report.money.usage_totals["input_tokens"] == 387
    assert report.money.usage_totals["output_tokens"] == 132
    assert "cost" not in report.money.usage_totals
    assert "actual_cost_micros" not in report.money.usage_totals

    invalid_usage = {**nested, "details": {"score": float("nan")}}
    invalid = first.model_dump()
    invalid["usage"] = invalid_usage
    with pytest.raises(ValueError, match="nonfinite"):
        AttemptFact.model_validate(invalid, strict=True)
    for invalid_value in (True, -1, "12"):
        invalid = first.model_dump()
        invalid["usage"] = {"input_tokens": invalid_value}
        with pytest.raises(ValueError, match="nonnegative integer or null"):
            AttemptFact.model_validate(invalid, strict=True)

    assert "input_tokens" in build_report(_bundle()).money.usage_unknown_fields


def test_cassette_replay_is_not_ranked_as_live_or_synthetic_spend() -> None:
    bundle = _bundle()
    replayed = bundle.attempts[0].model_copy(
        update={
            "actual_cost_micros": 0,
            "replayed": True,
        }
    )
    updated = bundle.model_copy(update={"attempts": (replayed, *bundle.attempts[1:])})
    report = build_report(updated)
    assert report.money.physical_attempt_count == 3
    assert report.money.replayed_attempt_count == 1
    assert report.money.synthetic_attempt_count == 1
    assert report.money.live_dispatch_count == 1
    assert report.money.known_spent_micros == 0


def test_failed_run_before_evidence_or_edit_still_reports_all_attempt_costs() -> None:
    raw = _bundle().model_dump(mode="json", by_alias=True)
    raw.update(
        {
            "status": "outcome_unknown",
            "sourceFingerprint": None,
            "currentRevision": 0,
            "acceptedRevision": None,
            "evidenceSha256": None,
            "evidence": None,
            "editSha256": None,
            "editRevision": None,
            "edit": None,
        }
    )
    incomplete = EvaluationBundle.model_validate_json(json.dumps(raw), strict=True)
    report = build_report(incomplete)
    assert report.evidence_sha256 is None
    assert report.source_fingerprint is None
    assert report.edit_sha256 is None
    assert report.exact_cover_violations is None
    assert report.chapter_count is None
    assert report.keep_duration_ms is None
    assert report.accepted_chapter_count is None
    assert report.technical_failure_count is None
    assert report.money.physical_attempt_count == 3
    assert report.money.known_spent_micros == 123
    assert "chapter_edit_not_available" in report.comparison_reasons

    invalid_attempt = incomplete.attempts[-1].model_copy(update={"provider": "gateway"})
    invalid = incomplete.model_copy(
        update={"attempts": (*incomplete.attempts[:-1], invalid_attempt)}
    )
    with pytest.raises(HarnessValidationError, match="synthetic attempt"):
        validate_bundle(invalid)

    live_marked_synthetic = incomplete.attempts[0].model_copy(update={"synthetic": True})
    invalid = incomplete.model_copy(
        update={"attempts": (live_marked_synthetic, *incomplete.attempts[1:])}
    )
    with pytest.raises(HarnessValidationError, match="synthetic attempt flag"):
        validate_bundle(invalid)


def test_incomplete_bundle_rejects_orphan_revision_and_artifacts() -> None:
    raw = _bundle().model_dump(mode="json", by_alias=True)
    raw.update(
        {
            "sourceFingerprint": None,
            "evidenceSha256": None,
            "evidence": None,
            "editSha256": None,
            "edit": None,
        }
    )
    orphan = EvaluationBundle.model_validate_json(json.dumps(raw), strict=True)
    with pytest.raises(HarnessValidationError, match="edit body, hash, and revision"):
        validate_bundle(orphan)


def test_human_labels_are_explicit_and_cannot_leak_source_split() -> None:
    bundle = _bundle(accepted=True)
    base = {
        "sourceFingerprint": bundle.source_fingerprint,
        "evidenceSha256": bundle.evidence_sha256,
        "boundaryId": "b1",
        "candidateId": "candidate-middle",
        "accepted": True,
        "preferredTimeMs": 5100,
        "preferredWindow": {"startMs": 4000, "endMs": 6000},
        "correctionActiveSeconds": 12,
        "correctionMeasurementMethod": "stopwatch",
        "reviewedAt": NOW.isoformat(),
    }
    labels = HumanLabels.model_validate_json(
        json.dumps(
            {
                "format": "temnia-chapter-human-labels/1",
                "labels": [{**base, "annotator": "a", "split": "test"}],
            }
        ),
        strict=True,
    )
    report = build_report(bundle, labels)
    assert report.boundary_metrics.acceptance_rate == 1
    assert report.boundary_metrics.mean_adjustment_ms == 100
    assert report.boundary_metrics.correction_active_seconds == 12
    assert report.accepted_chapter_count is None
    leaking = HumanLabels.model_validate_json(
        json.dumps(
            {
                "format": "temnia-chapter-human-labels/1",
                "labels": [
                    {**base, "annotator": "a", "split": "test"},
                    {**base, "annotator": "b", "split": "tuning"},
                ],
            }
        ),
        strict=True,
    )
    with pytest.raises(HarnessValidationError, match="split differs"):
        validate_labels(bundle, leaking)


@pytest.mark.parametrize("editorial_version", [1, 2])
def test_accepted_denominator_requires_acknowledged_sections_descriptor_and_exact_checks(
    editorial_version: int,
) -> None:
    original = _bundle(accepted=True)
    assert original.edit is not None
    sections = [
        section.model_copy(update={"reviewState": ReviewState.accepted, "kind": Kind.keep})
        for section in original.edit.sections
    ]
    edit = original.edit.model_copy(update={"sections": sections})
    edit_sha = content_sha256(edit)
    check_artifacts: list[CheckArtifact] = []
    for index, section_id in enumerate(("chapter-1", "drop-1"), start=8):
        checks = ChapterChecks.model_validate(
            {
                "editorialReasons": [],
                "editorialStatus": "passed",
                "editSha256": edit_sha,
                "technicalChecks": [
                    {
                        "expected": None,
                        "measured": None,
                        "message": "verified fixture",
                        "name": name,
                        "sectionId": section_id,
                        "status": "pass",
                    }
                    for name in sorted(required_check_names(original))
                ],
                "verifierFamily": "independent-family",
                "version": 1,
            }
        )
        check_artifacts.append(
            CheckArtifact(
                artifact_id=UUID(f"80000000-0000-0000-0000-{index:012d}"),
                section_id=section_id,
                sha256=content_sha256(checks),
                checks=checks,
            )
        )
    ref_base = {
        "fingerprint": "d" * 64,
        "sizeBytes": 10,
        "storageKey": "scoped/key",
    }
    renders = ChapterRenders.model_validate(
        {
            "editSha256": edit_sha,
            "format": "chapter-renders/1",
            "renders": [
                {
                    "captions": {
                        **ref_base,
                        "id": "81000000-0000-0000-0000-000000000008",
                        "kind": "render",
                        "sha256": "e" * 64,
                    },
                    "checks": {
                        **ref_base,
                        "id": str(check_artifacts[0].artifact_id),
                        "kind": "checks",
                        "sha256": check_artifacts[0].sha256,
                    },
                    "durationMs": 5000,
                    "editSha256": edit_sha,
                    "media": {
                        **ref_base,
                        "id": "82000000-0000-0000-0000-000000000008",
                        "kind": "render",
                        "sha256": "f" * 64,
                    },
                    "sectionId": "chapter-1",
                },
                {
                    "captions": {
                        **ref_base,
                        "id": "81000000-0000-0000-0000-000000000009",
                        "kind": "render",
                        "sha256": "e" * 64,
                    },
                    "checks": {
                        **ref_base,
                        "id": str(check_artifacts[1].artifact_id),
                        "kind": "checks",
                        "sha256": check_artifacts[1].sha256,
                    },
                    "durationMs": 5000,
                    "editSha256": edit_sha,
                    "media": {
                        **ref_base,
                        "id": "82000000-0000-0000-0000-000000000009",
                        "kind": "render",
                        "sha256": "f" * 64,
                    },
                    "sectionId": "drop-1",
                },
            ],
            "runId": str(RUN),
        }
    )
    bundle = original.model_copy(
        update={
            "edit": edit,
            "edit_sha256": edit_sha,
            "checks": tuple(check_artifacts),
            "renders": renders,
            "render_descriptor_sha256": content_sha256(renders),
        }
    )
    assert build_report(bundle).accepted_chapter_count == 2

    first, second = renders.renders
    swapped = renders.model_copy(
        update={
            "renders": [
                first.model_copy(update={"checks": second.checks}),
                second.model_copy(update={"checks": first.checks}),
            ]
        }
    )
    invalid = bundle.model_copy(
        update={
            "renders": swapped,
            "render_descriptor_sha256": content_sha256(swapped),
        }
    )
    with pytest.raises(HarnessValidationError, match="reference every check"):
        validate_bundle(invalid)

    edit_ref = {
        "id": "83000000-0000-0000-0000-000000000008",
        "sourceId": str(SOURCE),
        "kind": "edit",
        "fingerprint": "1" * 64,
        "sha256": edit_sha,
    }
    descriptor_ref = {
        "id": "84000000-0000-0000-0000-000000000008",
        "sourceId": str(SOURCE),
        "kind": "render",
        "fingerprint": "2" * 64,
        "sha256": content_sha256(renders),
    }
    model_ref = {
        "id": "85000000-0000-0000-0000-000000000008",
        "sourceId": str(SOURCE),
        "kind": "model_response",
        "fingerprint": "3" * 64,
        "sha256": "4" * 64,
    }
    body = EditorialVerificationBody.model_validate(
        {
            "format": "chapter-verification/1",
            "verdict": {
                "version": 1,
                "status": "passed",
                "reasons": ["grounded fixture passed"] if editorial_version == 1 else [],
                "inspectedModalities": "text_evidence_and_technical_report",
            },
            **(
                {
                    "editorial": {
                        "version": 2,
                        "status": "passed",
                        "findings": (),
                        "inspectedModalities": "text_evidence_and_edit_context",
                    }
                }
                if editorial_version == 2
                else {}
            ),
        }
    )
    verification_sha = content_sha256(body)
    verification_fingerprint = fingerprint_for(
        kind="checks",
        inputs={
            "descriptorArtifactId": descriptor_ref["id"],
            "descriptorSha256": descriptor_ref["sha256"],
            "editArtifactId": edit_ref["id"],
            "editSha256": edit_ref["sha256"],
            "modelResponseArtifactId": model_ref["id"],
            "modelResponseSha256": model_ref["sha256"],
        },
        config={"format": "chapter-verification/1"},
    )
    verification = EditorialVerificationArtifact.model_validate_json(
        json.dumps(
            {
                "artifact": {
                    "id": "86000000-0000-0000-0000-000000000008",
                    "sourceId": str(SOURCE),
                    "kind": "checks",
                    "fingerprint": verification_fingerprint,
                    "sha256": verification_sha,
                },
                "body": body.model_dump(mode="json"),
                "runId": str(RUN),
                "revision": 1,
                "verifierFamily": "independent-family",
                "edit": edit_ref,
                "descriptor": descriptor_ref,
                "modelResponse": model_ref,
            }
        ),
        strict=True,
    )
    verified = bundle.model_copy(update={"editorial_verification": verification})
    verified_report = build_report(verified)
    assert verified_report.editorial_statuses == {"passed": 1}
    assert verified_report.editorial_verifier_families == ("independent-family",)
    assert verified_report.editorial_verdict_modality == "code_and_model"
    assert verified_report.editorial_verification_sha256 == verification_sha
    serialized = verified.model_dump_json(by_alias=True)
    roundtrip = EvaluationBundle.model_validate_json(serialized)
    validate_bundle(roundtrip)
    assert roundtrip == verified
    assert roundtrip.editorial_verification is not None
    assert ("editorial" in roundtrip.editorial_verification.body.model_dump()) == (
        editorial_version == 2
    )


def test_cli_report_writes_atomically_and_validation_failure_is_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _bundle()
    bundle_path = tmp_path / "bundle.json"
    evidence_path = tmp_path / "evidence.json"
    output = tmp_path / "report.json"
    bundle_path.write_text(bundle.model_dump_json(by_alias=True))
    assert bundle.evidence is not None
    evidence_path.write_text(bundle.evidence.model_dump_json())
    assert cli.main(["report", "--bundle", str(bundle_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["format"] == "temnia-chapter-evaluation-report/1"
    assert not list(tmp_path.glob(".report.json.*"))
    assert cli.main(["validate", str(bundle_path), "--evidence", str(evidence_path)]) == 0
    captured = capsys.readouterr()
    assert "known spend micros" in captured.out
    bad = json.loads(bundle_path.read_text())
    bad["editSha256"] = "f" * 64
    bundle_path.write_text(json.dumps(bad))
    with pytest.raises(SystemExit) as raised:
        cli.main(["validate", str(bundle_path), "--evidence", str(evidence_path)])
    assert raised.value.code == 1


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    assert "reconcile-cost" in capsys.readouterr().out


def test_cli_export_bundle_writes_valid_mode_0600_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    output = tmp_path / "private" / "run-bundle.json"

    async def export(*_args: object, **kwargs: object) -> EvaluationBundle:
        assert kwargs["run_id"] == RUN
        assert kwargs["split"] == "test"
        return bundle.model_copy(update={"split": "test"})

    monkeypatch.setattr(cli, "export_evaluation_bundle", export)
    monkeypatch.setenv("PIPELINE_DATABASE_URL", "postgresql://unused")
    assert (
        cli.main(
            [
                "export-bundle",
                "--run-id",
                str(RUN),
                "--split",
                "test",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    exported = EvaluationBundle.model_validate_json(output.read_bytes(), strict=True)
    assert exported.split == "test"
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(output.parent.glob(".run-bundle.json.*"))


def test_cli_uses_the_shared_seeded_scope_seam() -> None:
    scope = resolve_scope()
    assert str(scope.organizationId) == "0192e8a0-0000-7000-8000-000000000001"
    assert str(scope.userId) == "0192e8a0-0000-7000-8000-000000000002"


@pytest.mark.asyncio
async def test_reconciliation_is_bounded_idempotent_and_refuses_wrong_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = {
        "id": "route-a",
        "gateway_model": "vendor/model-a",
        "family": "family-a",
        "provider": "provider-a",
        "open_weight": False,
        "context_tokens": 10000,
        "max_output_tokens": 1000,
        "eligibility": {
            "zero_data_retention": True,
            "strict_json_schema": True,
            "cache_qualified": False,
            "probe_artifact_sha256": "a" * 64,
            "probed_at": "2026-09-08",
        },
        "prices": {
            "unit": "micros_per_million_tokens",
            "input": 1,
            "output": 2,
            "cache_read": None,
            "cache_write": None,
            "request_surcharge": 0,
        },
        "reasoning_effort": None,
        "service_tier": None,
        "cache_enabled": False,
    }
    row: dict[str, Any] = {
        "id": ATTEMPT,
        "operation_id": OPERATION,
        "owner_token": "owner",
        "state": "succeeded",
        "cost_status": "unknown",
        "remote_handle": "generation-a",
        "result_artifact_id": RESULT,
        "route": route,
        "source_id": SOURCE,
        "operation_status": "succeeded",
        "operation_artifact_id": RESULT,
    }

    async def rows(_database_url: str, _run_id: UUID) -> list[dict[str, Any]]:
        return [row]

    settlements: list[int] = []

    async def lookup(*_args: object, **_kwargs: object) -> CostObservation:
        return CostObservation(
            status="reported",
            actual_cost_micros=123,
            components={"totalCost": "0.000123", "reasoningTokens": 5},
        )

    async def settle(*_args: object, **kwargs: object) -> object:
        amount = kwargs["observed_cost_micros"]
        assert isinstance(amount, int)
        settlements.append(amount)
        return object()

    monkeypatch.setattr(cli, "_reconciliation_rows", rows)
    for _ in range(2):
        result = await cli.reconcile_run_costs(
            "unused",
            run_id=RUN,
            api_key="secret",
            apply=True,
            lookup=lookup,
            settle=settle,
        )
        assert result[0]["actualCostMicros"] == 123
    assert settlements == [123, 123]

    bad = {**row, "state": "outcome_unknown", "result_artifact_id": None}

    async def mixed_rows(_database_url: str, _run_id: UUID) -> list[dict[str, Any]]:
        return [bad, row]

    monkeypatch.setattr(cli, "_reconciliation_rows", mixed_rows)
    mixed = await cli.reconcile_run_costs(
        "unused", run_id=RUN, api_key="secret", apply=False, lookup=lookup, settle=settle
    )
    assert [item["status"] for item in mixed] == ["skipped", "reported"]

    async def wrong_lookup(*_args: object, **_kwargs: object) -> CostObservation:
        message = "wrong provider model identity"
        raise GenerationIdentityError(message)

    with pytest.raises(GenerationIdentityError, match="wrong provider"):
        await cli.reconcile_run_costs(
            "unused",
            run_id=RUN,
            api_key="secret",
            apply=False,
            lookup=wrong_lookup,
            settle=settle,
        )
