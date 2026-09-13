"""Selection metrics refuse leakage, stale outputs and misleading denominators."""

# Synthetic artifacts exercise code contracts, not model editorial judgment.
# ruff: noqa: SLF001, TC003
# pyright: reportPrivateUsage=false
from __future__ import annotations

import argparse
import hashlib
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    PositiveRational,
    Scope,
    TopicProposal,
    TopicSelectionAssessment,
    TopicSelectionRecord,
)
from temnia_pipeline.evals.topic_cli import private_json, run_topic_command
from temnia_pipeline.evals.topic_comparison import (
    ComparisonInput,
    TopicComparisonManifest,
    compare_topics,
)
from temnia_pipeline.evals.topic_report import build_topic_report
from temnia_pipeline.evals.topic_retained import retained_author_diagnostic
from temnia_pipeline.evals.topics import (
    CandidateJudgment,
    OpportunityLabel,
    OpportunityLoss,
    OpportunityMatch,
    RetainedExpense,
    ReviewerCase,
    StageObservation,
    TopicArtifact,
    TopicConfiguration,
    TopicEvaluationBundle,
    TopicHumanLabels,
    TopicRevision,
    digest,
    topic_label_template,
    validate_topic_bundle,
    validate_topic_labels,
)
from temnia_pipeline.harness import topic_bundle_export as exporter
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence, compile_topics_v3
from temnia_pipeline.harness.topic_selection import content_hash, make_rubric
from test_topic_compiler import _candidate, _case, _span
from topic_fixtures import _cold, _criterion

NOW = datetime(2026, 9, 11, tzinfo=UTC)
RUBRIC = "c" * 64


def _artifact(
    body: dict[str, Any] | None,
    format_name: str,
    *,
    kind: str = "checks",
    dependencies: tuple[UUID, ...] = (),
) -> TopicArtifact:
    raw = canonical_json(body) if body is not None else b"synthetic media bytes"
    sha = hashlib.sha256(raw).hexdigest()
    identifier = uuid4()
    return TopicArtifact(
        id=identifier,
        source_id=_case().sourceId,
        sha256=sha,
        fingerprint=sha,
        kind=kind,
        size_bytes=len(raw),
        storage_key=f"sources/{_case().sourceId}/{identifier}",
        metadata={"format": format_name},
        dependencies=dependencies,
        body=body,
        body_status="included" if body is not None else "binary_reference",
    )


def _ref(artifact: TopicArtifact) -> dict[str, Any]:
    return HarnessArtifactRef(
        id=artifact.id,
        fingerprint=artifact.fingerprint,
        kind=HarnessArtifactKind(artifact.kind),
        sha256=artifact.sha256,
        sizeBytes=artifact.size_bytes,
        storageKey=artifact.storage_key or "missing",
    ).model_dump(mode="json")


def _bundle(*, technical_status: str = "pass") -> TopicEvaluationBundle:
    evidence = augment_topic_evidence(
        _case().model_copy(
            update={
                "audioSampleRate": 48000,
                "frameRate": PositiveRational(numerator=25, denominator=1),
            }
        )
    )
    run_id = uuid4()
    evidence_artifact = _artifact(
        evidence.model_dump(mode="json"), "source-evidence/1", kind="evidence"
    )
    proposal = TopicProposal(
        version=1,
        candidates=[_candidate("one", 0, 1), _candidate("two", 2, 3)],
        summary="Synthetic selection",
    )
    proposal_artifact = _artifact(
        proposal.model_dump(mode="json"),
        "topic-proposal/1",
        kind="edit",
        dependencies=(evidence_artifact.id,),
    )
    edit = compile_topics_v3(
        augment_topic_evidence(evidence),
        proposal,
        evidence_artifact_id=evidence_artifact.id,
        evidence_sha256=evidence_artifact.sha256,
    )
    edit_artifact = _artifact(
        edit.model_dump(mode="json"),
        "topic-edit/1",
        kind="edit",
        dependencies=(proposal_artifact.id, evidence_artifact.id),
    )
    artifacts = [evidence_artifact, proposal_artifact, edit_artifact]
    rendered: list[dict[str, Any]] = []
    for video in edit.videos:
        execution = _artifact(
            video.edit.model_dump(mode="json"),
            "chapter-edit/1",
            kind="edit",
            dependencies=(edit_artifact.id,),
        )
        media = _artifact(None, "chapter-media/1", kind="render", dependencies=(execution.id,))
        checks = _artifact(
            {
                "editorialReasons": [],
                "editorialStatus": "needs_review",
                "editSha256": execution.sha256,
                "technicalChecks": [
                    {
                        "name": "decode",
                        "status": technical_status,
                        "message": "Synthetic check",
                        "expected": None,
                        "measured": None,
                        "sectionId": video.candidate.id,
                    }
                ],
                "verifierFamily": None,
                "version": 1,
            },
            "chapter-checks/1",
            dependencies=(execution.id, media.id),
        )
        descriptor = _artifact(
            {
                "editSha256": execution.sha256,
                "format": "chapter-renders/1",
                "runId": str(run_id),
                "renders": [
                    {
                        "captions": None,
                        "checks": _ref(checks),
                        "durationMs": 2000,
                        "editSha256": execution.sha256,
                        "media": _ref(media),
                        "sectionId": video.candidate.id,
                    }
                ],
            },
            "chapter-renders/1",
            kind="render",
            dependencies=(execution.id, media.id, checks.id),
        )
        artifacts.extend((execution, media, checks, descriptor))
        rendered.append(
            {
                "candidateId": video.candidate.id,
                "execution": _ref(execution),
                "descriptor": _ref(descriptor),
            }
        )
    renders = _artifact(
        {
            "editSha256": edit_artifact.sha256,
            "format": "topic-renders/1",
            "runId": str(run_id),
            "videos": rendered,
        },
        "topic-renders/1",
        kind="render",
        dependencies=tuple(artifact.id for artifact in artifacts),
    )
    artifacts.append(renders)
    rubric = make_rubric("Find independently useful discussions.")
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "draft": {
                "proposal": proposal.model_dump(mode="json"),
                "opportunities": [
                    {
                        "id": f"opportunity:{candidate.id}",
                        "candidateIds": [candidate.id],
                        "coreSpans": [span.model_dump() for span in candidate.coreSpans],
                        "completionSpans": [
                            span.model_dump() for span in candidate.completionSpans
                        ],
                        "valueEvidenceSpans": [span.model_dump() for span in candidate.coreSpans],
                        "requiredContextSpans": [],
                        "meaningChangingFollowups": [],
                        "viewerPurpose": candidate.purpose,
                        "disposition": "proposed",
                        "dispositionReason": "A source-grounded opportunity.",
                    }
                    for candidate in proposal.candidates
                ],
            },
            "evidenceSha256": evidence_artifact.sha256,
            "rubric": rubric.model_dump(mode="json"),
            "rubricSha256": content_hash(rubric),
            "runId": str(run_id),
            "origin": "model",
            "parentSelectionSha256": None,
        }
    )
    record_artifact = _artifact(
        record.model_dump(mode="json"),
        "topic-selection/2",
        kind="proposal",
        dependencies=(evidence_artifact.id,),
    )
    artifacts.append(record_artifact)
    criterion = _criterion().model_dump(mode="json")
    assessment = _artifact(
        TopicSelectionAssessment.model_validate(
            {
                "format": "topic-selection-assessment/2",
                "runId": str(run_id),
                "selectionSha256": record_artifact.sha256,
                "evidenceSha256": evidence_artifact.sha256,
                "rubricSha256": content_hash(rubric),
                "coldReviews": [
                    {
                        **_cold("one").model_dump(mode="json"),
                        "value": {
                            **dict.fromkeys(
                                (
                                    "viewerReasonToWatch",
                                    "deliveredValue",
                                    "focusedDevelopment",
                                    "openingEffectiveness",
                                ),
                                criterion,
                            ),
                            "reconstructedPurpose": "A useful answer.",
                            "reconstructedTakeaway": "The answer is complete.",
                        },
                    }
                ],
                "portfolioReview": None,
                "findings": [],
                "executionStatus": "needs_review",
                "proposerFamily": "family-a",
                "verifierFamily": "family-b",
                "reasons": [],
                "responseArtifacts": [],
            }
        ).model_dump(mode="json"),
        "topic-selection-assessment/2",
        dependencies=(record_artifact.id, evidence_artifact.id),
    )
    artifacts.append(assessment)
    return TopicEvaluationBundle(
        source_id=evidence.sourceId,
        recording_group=str(evidence.sourceId),
        source_fingerprint=evidence.sourceFingerprint,
        duration_ms=evidence.durationMs,
        run_id=run_id,
        observed_at=NOW,
        split="held_out",
        status="needs_review",
        current_revision=1,
        configuration=TopicConfiguration(
            configuration_id="control",
            policy="standalone-topics/3",
            source_sha256="d" * 64,
            transcript_sha256=evidence.transcriptSha256,
            rubric_sha256=RUBRIC,
            program_identity={"version": "1"},
            author_identity={"model": "family-a"},
            reviewer_identity={"model": "family-b"},
            prompt_identity={"version": "1"},
            schema_identity={"version": "1"},
            media_identity={"version": "1"},
            execution_identity={"version": "1"},
        ),
        evidence_sha256=evidence_artifact.sha256,
        evidence=evidence,
        artifacts=tuple(artifacts),
        revisions=(TopicRevision(revision=1, artifact_id=edit_artifact.id, created_at=NOW),),
        stages=tuple(
            StageObservation(
                stage=stage,
                status="complete",
                artifact_sha256s=(
                    proposal_artifact.sha256
                    if stage in {"discovery", "selection"}
                    else edit_artifact.sha256
                    if stage == "compilation"
                    else renders.sha256,
                ),
                reason="Synthetic stage observation",
            )
            for stage in ("discovery", "selection", "compilation", "rendering")
        ),
        final_candidate_sha256s=tuple(
            digest(candidate.model_dump(mode="json")) for candidate in proposal.candidates
        ),
        final_selection_sha256=proposal_artifact.sha256,
        final_edit_sha256=edit_artifact.sha256,
        final_renders_sha256=renders.sha256,
    )


def _judgment(
    bundle: TopicEvaluationBundle, ordinal: int = 0, **changes: object
) -> CandidateJudgment:
    return CandidateJudgment(
        candidate_sha256=bundle.final_candidate_sha256s[ordinal],
        rubric_sha256=RUBRIC,
        edit_sha256=bundle.final_edit_sha256,
        render_descriptor_sha256=bundle.final_renders_sha256,
        decision="accept",
        editorial_changes="none",
        cold_annotator="cold-editor",
        source_annotator="source-editor",
        cold_prior_source_exposure=False,
        cold_prior_variant_exposure=False,
        full_playback=True,
        source_fully_reviewed=True,
        observed_modalities=("audio", "video"),
        audience_value="strong",
        comprehension="pass",
        completion="pass",
        fidelity="pass",
        title_faithful="pass",
        media_quality="pass",
        severe_fidelity_defect=False,
        redundant_core=False,
        reason="Synthetic adjudication",
    ).model_copy(update=changes)


def _labels(bundle: TopicEvaluationBundle) -> TopicHumanLabels:
    assert bundle.evidence_sha256 is not None
    opportunities = tuple(
        OpportunityLabel(
            id=f"op-{i}",
            viewer_purpose="Explain a useful source discussion",
            worthwhile=True,
            core_spans=(_span(i * 2),),
            mandatory_spans=(_span(i * 2 + 1),),
            reason="Independent human fixture",
            annotator="opportunity-editor",
            independently_labeled=True,
            source_fully_reviewed=True,
        )
        for i in range(2)
    )
    return TopicHumanLabels(
        source_id=bundle.source_id,
        evidence_sha256=bundle.evidence_sha256,
        rubric_sha256=RUBRIC,
        recording_group=bundle.recording_group,
        split=bundle.split,
        opportunities_complete=True,
        opportunity_annotator="opportunity-editor",
        independent_source_review=True,
        opportunities=opportunities,
        judgments=(
            _judgment(bundle),
            _judgment(bundle, 1, decision="reject", audience_value="weak"),
        ),
        matches=(
            OpportunityMatch(
                opportunity_id="op-0",
                candidate_sha256=bundle.final_candidate_sha256s[0],
                independently_usable=True,
                mandatory_content_present=True,
                annotator="source-editor",
                reason="Actual purpose and mandatory speech represented",
            ),
        ),
    )


def test_selection_precision_and_recall_are_separate_from_delivery() -> None:
    bundle = _bundle()
    report = build_topic_report(bundle, _labels(bundle))
    assert report.rates["editorialPrecision"].value == 0.5
    assert report.rates["usefulOpportunityRecall"].value == 0.5
    assert report.rates["deliveryYield"].value == 1
    assert report.counts["acceptedWithoutRepair"] == 1


@pytest.mark.parametrize("media_quality", ["fail", "unknown"])
def test_media_failure_or_unknown_does_not_change_editorial_quality(media_quality: str) -> None:
    bundle = _bundle()
    original = _labels(bundle)
    labels = original.model_copy(
        update={
            "judgments": (
                _judgment(bundle, media_quality=media_quality, decision="reject"),
                original.judgments[1],
            )
        }
    )
    before, after = build_topic_report(bundle, original), build_topic_report(bundle, labels)
    for metric in ("editorialPrecision", "usefulOpportunityRecall", "deliveryYield"):
        assert after.rates[metric] == before.rates[metric]
    assert after.counts["acceptedWithoutRepair"] == 0
    assert after.counts["editoriallyUsefulCandidates"] == 1
    if media_quality == "unknown":
        assert not after.qualification_complete
        assert after.rates["endToEndAcceptedOpportunityRecall"].value is None
    else:
        assert after.rates["endToEndAcceptedOpportunityRecall"].value == 0


def test_complete_text_review_measures_selection_without_media_qualification() -> None:
    bundle = _bundle()
    labels = _labels(bundle)
    text_judgments = tuple(
        judgment.model_copy(
            update={
                "edit_sha256": None,
                "render_descriptor_sha256": None,
                "full_playback": False,
                "full_selected_text_reviewed": True,
                "observed_modalities": ("text",),
                "media_quality": "unknown",
                "decision": "unknown",
            }
        )
        for judgment in labels.judgments
    )
    text_labels = labels.model_copy(update={"judgments": text_judgments})
    report = build_topic_report(bundle, text_labels)
    assert report.rates["editorialPrecision"].value == 0.5
    assert report.rates["usefulOpportunityRecall"].value == 0.5
    assert report.rates["fullMediaEvaluationCompletion"].value == 0
    assert report.rates["publicationAcceptance"].value is None
    assert report.rates["endToEndAcceptedYield"].value is None
    assert report.rates["endToEndAcceptedOpportunityRecall"].value is None
    assert report.counts["acceptedWithoutRepair"] == 0
    assert not report.qualification_complete
    assert "selection_assessment_includes_text_only_observations" in report.limitations
    partial = tuple(
        j.model_copy(update={"full_selected_text_reviewed": False}) for j in text_judgments
    )
    assert (
        build_topic_report(bundle, labels.model_copy(update={"judgments": partial}))
        .rates["editorialPrecision"]
        .value
        is None
    )


def test_failed_technical_delivery_does_not_change_editorial_quality() -> None:
    bundle = _bundle(technical_status="fail")
    report = build_topic_report(bundle, _labels(bundle))
    assert report.rates["editorialPrecision"].value == 0.5
    assert report.rates["usefulOpportunityRecall"].value == 0.5
    assert report.rates["deliveryYield"].value == 0
    assert report.counts["acceptedWithoutRepair"] == 0


def test_audio_only_inspection_does_not_qualify_video_publication() -> None:
    bundle = _bundle()
    labels = _labels(bundle)
    labels = labels.model_copy(
        update={
            "judgments": tuple(
                j.model_copy(update={"observed_modalities": ("audio",)}) for j in labels.judgments
            )
        }
    )
    report = build_topic_report(bundle, labels)
    assert report.rates["editorialPrecision"].value == 0.5
    assert report.counts["acceptedWithoutRepair"] == 0
    assert not report.qualification_complete


def test_empty_output_and_absent_human_labels_are_not_perfect() -> None:
    bundle = _bundle().model_copy(update={"final_candidate_sha256s": ()})
    report = build_topic_report(bundle)
    assert report.rates["editorialPrecision"].value is None
    assert report.rates["deliveryYield"].value is None
    assert report.rates["usefulOpportunityRecall"].value is None
    assert not report.qualification_complete


def test_label_template_freezes_identities_without_inventing_human_observations() -> None:
    bundle = _bundle()
    labels = topic_label_template(bundle)
    validate_topic_labels(bundle, labels)
    assert tuple(j.candidate_sha256 for j in labels.judgments) == bundle.final_candidate_sha256s
    assert not labels.opportunities_complete
    assert not labels.independent_source_review
    assert all(not j.full_playback and j.decision == "unknown" for j in labels.judgments)
    report = build_topic_report(bundle, labels)
    assert report.rates["editorialPrecision"].value is None
    assert report.rates["usefulOpportunityRecall"].value is None
    assert not report.qualification_complete


@pytest.mark.parametrize(
    "change",
    [
        {"cold_prior_source_exposure": True},
        {"cold_prior_variant_exposure": True},
        {"full_playback": False},
        {"editorial_changes": "content_boundary"},
        {"source_annotator": "cold-editor"},
    ],
)
def test_incomplete_or_contaminated_review_cannot_count_as_unassisted_acceptance(
    change: dict[str, Any],
) -> None:
    bundle = _bundle()
    labels = _labels(bundle).model_copy(update={"judgments": (_judgment(bundle, **change),)})
    assert build_topic_report(bundle, labels).counts["acceptedWithoutRepair"] == 0


def test_stale_media_and_wrong_rubric_are_refused() -> None:
    bundle = _bundle()
    labels = _labels(bundle)
    for changes in (
        {"rubric_sha256": "e" * 64},
        {"judgments": (_judgment(bundle, edit_sha256="e" * 64),)},
    ):
        with pytest.raises(ValueError, match=r"(artifact|source|edit|rubric|candidate|judgment)"):
            validate_topic_labels(bundle, labels.model_copy(update=changes))


def test_opportunity_match_cannot_claim_words_outside_candidate() -> None:
    bundle = _bundle()
    labels = _labels(bundle)
    bad = labels.matches[0].model_copy(update={"opportunity_id": "op-1"})
    with pytest.raises(ValueError, match="absent mandatory"):
        validate_topic_labels(bundle, labels.model_copy(update={"matches": (bad,)}))


def test_corrupt_body_missing_dependency_and_wrong_source_are_refused() -> None:
    bundle = _bundle()
    artifact = bundle.artifacts[1]
    for change in ({"sha256": "e" * 64}, {"dependencies": (uuid4(),)}, {"source_id": uuid4()}):
        with pytest.raises(ValueError, match=r"(artifact|source|edit|rubric|candidate|judgment)"):
            validate_topic_bundle(
                bundle.model_copy(
                    update={
                        "artifacts": (
                            bundle.artifacts[0],
                            artifact.model_copy(update=change),
                            *bundle.artifacts[2:],
                        )
                    }
                )
            )


def test_reviewer_false_acceptance_and_invalid_among_approvals_differ() -> None:
    bundle = _bundle()
    assessment = bundle.artifacts[-1]
    candidate = bundle.final_candidate_sha256s[0]
    cases = tuple(
        ReviewerCase(
            candidate_sha256=candidate,
            judgment_artifact_sha256=assessment.sha256,
            criterion=criterion,
            human_valid=valid,
            model_decision="pass",
            annotator="calibrator",
            reason="Fixed criterion adjudication",
        )
        for criterion, valid in [
            ("cold.coherentTopic", True),
            ("cold.completeDiscussion", False),
            ("cold.titleFaithful", True),
        ]
    )
    labels = _labels(bundle).model_copy(update={"reviewer_cases": cases})
    report = build_topic_report(bundle, labels)
    assert report.rates["reviewerFalseAcceptance"].value == 1
    assert report.rates["invalidAmongApprovals"].value == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="actual model"):
        build_topic_report(
            bundle,
            labels.model_copy(
                update={"reviewer_cases": (cases[0].model_copy(update={"model_decision": "fail"}),)}
            ),
        )


def test_incomplete_discovery_cannot_be_labeled_a_discovery_miss() -> None:
    bundle = _bundle()
    labels = _labels(bundle).model_copy(
        update={
            "losses": (
                OpportunityLoss(
                    opportunity_id="op-1",
                    stage="discovery",
                    artifact_sha256s=(bundle.artifacts[1].sha256,),
                    annotator="editor",
                    reason="Missing despite a completed source pass",
                ),
            )
        }
    )
    validate_topic_labels(bundle, labels)
    with pytest.raises(ValueError, match="incomplete discovery"):
        validate_topic_labels(bundle.model_copy(update={"stages": ()}), labels)


def test_historical_receipt_cost_is_reported_without_new_dispatch() -> None:
    bundle = _bundle().model_copy(
        update={
            "retained_expense": RetainedExpense(
                external_attempt_id="external-attempt",
                generation_id="external-generation",
                response_sha256="a" * 64,
                receipt_sha256="b" * 64,
                actual_cost_micros=399667,
                estimated_exposure_micros=500000,
                usage={"reasoningTokens": 16114},
            )
        }
    )
    report = build_topic_report(bundle)
    assert report.money["historicalRetainedMicros"] == 399667
    assert report.money["newKnownMicros"] == report.money["newLiveDispatches"] == 0
    assert report.money["knownMicrosPerAcceptedVideo"] is None


def test_report_commands_are_local_private_and_do_not_overwrite_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline evaluation attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    bundle = _bundle()
    source = tmp_path / "bundle.json"
    output = tmp_path / "report.json"
    private_json(source, bundle.model_dump(mode="json", by_alias=True))
    args = argparse.Namespace(topic_command="report", bundle=source, labels=None, output=output)
    assert run_topic_command(args) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["qualificationComplete"] is False
    args.output = source
    with pytest.raises(ValueError, match="replace an input"):
        run_topic_command(args)


def test_model_swap_comparison_detects_changed_reviewer(tmp_path: Path) -> None:
    control = _bundle()
    candidate = control.model_copy(
        update={
            "configuration": control.configuration.model_copy(
                update={
                    "configuration_id": "challenger",
                    "author_identity": {"model": "family-c"},
                    "reviewer_identity": {"model": "family-d"},
                }
            )
        }
    )
    inputs: list[ComparisonInput] = []
    for name, bundle in (("control", control), ("challenger", candidate)):
        path = tmp_path / f"{name}.json"
        private_json(path, bundle.model_dump(mode="json", by_alias=True))
        inputs.append(
            ComparisonInput(
                bundle=path.name,
                expected_bundle_sha256=digest(bundle.model_dump(mode="json", by_alias=True)),
            )
        )
    manifest = TopicComparisonManifest(
        experiment_id="paired-model",
        mode="model_swap",
        baseline_configuration_id="control",
        allowed_changed_factors=("author_identity",),
        inputs=tuple(inputs),
        rationale="One qualified model substitution",
    )
    report = compare_topics(manifest, directory=tmp_path)
    assert not report.comparable
    assert any("changed_fixed_factor:reviewer_identity" in reason for reason in report.reasons)
    assert report.winner is None


def _retained_files(directory: Path) -> tuple[str, str]:
    evidence = _case()
    proposal = TopicProposal(
        version=1, candidates=[_candidate("one", 0, 1)], summary="Synthetic retained author"
    )
    request = json.loads(
        canonical_json(
            {
                "model": "fixture-model",
                "messages": [{"role": "user", "content": "Synthetic source"}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "TopicProposal",
                        "schema": TopicProposal.model_json_schema(),
                    },
                },
            }
        )
    )
    response = {
        "id": "fixture-generation",
        "model": "fixture-model",
        "provider": "Fixture",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": proposal.model_dump_json(),
                    "reasoning": "PRIVATE_REASONING_MUST_NOT_BE_EXPORTED",
                },
            }
        ],
        "usage": {
            "cost": 0.125,
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }

    def write(name: str, value: object) -> str:
        path = directory / name
        private_json(path, value)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    request_hash = write("openrouter-request.json", request)
    response_hash = write("inference-body.bin", response)
    write("evidence-canonical.json", evidence.model_dump(mode="json"))
    refs = [{"id": str(uuid4()), "sha256": digest(evidence.model_dump(mode="json"))}]
    preparation = {
        "sourceId": str(evidence.sourceId),
        "sourceSha256": "d" * 64,
        "transcriptSha256": evidence.transcriptSha256,
        "model": "fixture-model",
        "openrouterWireBodySha256": request_hash,
        "evidence": refs,
        "nativeSchemaSha256": hashlib.sha256(
            (
                json.dumps(
                    request["response_format"], ensure_ascii=False, allow_nan=False, indent=2
                )
                + "\n"
            ).encode()
        ).hexdigest(),
    }
    preparation_hash = write("preparation-receipt.json", preparation)
    write(
        "attempt-intent.json",
        {
            "attemptId": "external-test",
            "requestSha256": request_hash,
            "preparationReceiptSha256": preparation_hash,
            "evidence": refs,
        },
    )
    write(
        "inference-transport.json",
        {
            "complete": True,
            "httpStatus": 200,
            "bodySha256": response_hash,
            "finishedAt": NOW.isoformat(),
        },
    )
    generation_hash = write(
        "generation-followup-body.bin", {"data": {"id": "fixture-generation", "total_cost": 0.125}}
    )
    write(
        "accounting-reconciliation.json",
        {
            "generationId": "fixture-generation",
            "inferenceBodySha256": response_hash,
            "generationMetadataConfirmed": True,
            "costsAgree": True,
            "followupTransport": {"bodySha256": generation_hash},
            "generationReportedCostUsd": "0.125",
            "responseReportedCostMicrosRoundedUp": 125000,
        },
    )
    return preparation_hash, response_hash


def test_retained_author_is_not_a_new_workflow_or_free_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("retained diagnostic attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    preparation, response = _retained_files(tmp_path)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()
    }
    bundle = retained_author_diagnostic(
        directory=tmp_path, preparation_sha256=preparation, response_sha256=response
    )
    report = build_topic_report(bundle)
    assert bundle.run_id is None
    assert bundle.attempts == ()
    assert report.money["newLiveDispatches"] == 0
    assert report.money["historicalRetainedMicros"] == 125000
    assert report.rates["deliveryYield"].value is None
    assert report.rates["sourceCompletion"].value is None
    assert "PRIVATE_REASONING" not in bundle.model_dump_json()
    assert before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()
    }
    with pytest.raises(ValueError, match="caller-frozen"):
        retained_author_diagnostic(
            directory=tmp_path, preparation_sha256=preparation, response_sha256="0" * 64
        )


def test_retained_cost_receipt_must_match_actual_generation(tmp_path: Path) -> None:
    preparation, response = _retained_files(tmp_path)
    path = tmp_path / "generation-followup-body.bin"
    value = json.loads(path.read_bytes())
    value["data"]["total_cost"] = 0.25
    private_json(path, value)
    with pytest.raises(ValueError, match="generation receipt"):
        retained_author_diagnostic(
            directory=tmp_path, preparation_sha256=preparation, response_sha256=response
        )


@pytest.mark.asyncio
async def test_topic_export_preserves_revision_closure_and_private_media_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _bundle()
    scope = Scope(organizationId=uuid4(), userId=uuid4())
    rows: dict[UUID, dict[str, Any]] = {}
    bodies: dict[UUID, object] = {}
    for ordinal, artifact in enumerate(original.artifacts):
        rows[artifact.id] = {
            "id": artifact.id,
            "source_id": artifact.source_id,
            "sha256": artifact.sha256,
            "fingerprint": artifact.fingerprint,
            "kind": artifact.kind,
            "size_bytes": artifact.size_bytes,
            "storage_key": artifact.storage_key,
            "metadata": dict(artifact.metadata),
            "created_at": NOW.replace(microsecond=ordinal),
        }
        bodies[artifact.id] = artifact.body
    evidence = original.artifacts[0]
    snapshot = exporter._TopicSnapshot(
        run={
            "id": original.run_id,
            "source_id": original.source_id,
            "current_revision": 1,
            "accepted_revision": None,
            "evidence_artifact_id": evidence.id,
            "route_snapshot": {
                "editorialPolicy": "standalone-topics/3",
                "pinnedSource": {"sha256": "d" * 64},
            },
            "brief": "Generic standalone discussions",
            "status": "needs_review",
            "config": {"routeSnapshotId": "f" * 64},
        },
        source={"duration_ms": original.duration_ms},
        observed={"observed_at": NOW},
        revision_rows=[
            {
                "revision": 1,
                "artifact_id": original.revisions[0].artifact_id,
                "base_revision": None,
                "created_at": NOW,
            }
        ],
        events=[],
        attempts=[],
        rows=rows,
        dependencies={artifact.id: artifact.dependencies for artifact in original.artifacts},
    )

    async def read_snapshot(
        _database_url: str, *, scope: Scope, run_id: UUID
    ) -> exporter._TopicSnapshot:
        assert scope.organizationId is not None
        assert run_id == original.run_id
        return snapshot

    async def read_body(
        _database_url: str, *, scope: Scope, source_id: UUID, store: object, artifact_id: UUID
    ) -> object:
        assert scope.organizationId is not None
        assert store is not None
        assert source_id == original.source_id
        assert bodies[artifact_id] is not None, "binary media must not be read into JSON"
        return bodies[artifact_id]

    monkeypatch.setattr(exporter, "_read_topic_snapshot", read_snapshot)
    monkeypatch.setattr(exporter.artifacts, "read_artifact_json", read_body)
    result = await exporter.export_topic_bundle(
        "unused",
        scope=scope,
        store=cast("Any", object()),
        run_id=cast("Any", original.run_id),
        split="held_out",
    )
    assert result.final_edit_sha256 == original.final_edit_sha256
    assert result.final_renders_sha256 == original.final_renders_sha256
    assert result.final_candidate_sha256s == original.final_candidate_sha256s
    assert len(result.artifacts) == len(original.artifacts)
    assert any(artifact.body_status == "binary_reference" for artifact in result.artifacts)
