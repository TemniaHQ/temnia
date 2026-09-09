from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
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
    content_sha256,
    required_check_names,
    validate_bundle,
    validate_labels,
)
from temnia_pipeline.harness import cli
from temnia_pipeline.harness.artifacts import fingerprint_for
from temnia_pipeline.harness.gateway import CostObservation, GenerationIdentityError
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
            "config": {},
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


def test_accepted_denominator_requires_acknowledged_sections_descriptor_and_exact_checks() -> None:
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
                "reasons": ["grounded fixture passed"],
                "inspectedModalities": "text_evidence_and_technical_report",
            },
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
