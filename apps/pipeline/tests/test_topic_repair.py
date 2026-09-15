"""Connected-component repair planning, bounded prompts and atomic manifest admission."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    TopicCandidate,
    TopicOpportunity,
    TopicRepairPlan,
    TopicRepairShard,
    TopicRepairWorkItem,
    TopicSelectionAssessment,
    TopicSelectionFinding,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSourceIndex,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    topic_selection_qualification_case,
)
from temnia_pipeline.harness.source_index import build_topic_source_index, source_index_map
from temnia_pipeline.harness.topic_repair import (
    MAX_REPAIR_CANDIDATES,
    MAX_REPAIR_FINDINGS,
    MAX_REPAIR_OPPORTUNITIES,
    admit_repair_shard,
    assemble_repair_manifest,
    build_repair_plan,
    normalise_repair_patch,
    repair_component_prompt,
)
from temnia_pipeline.harness.topic_selection import content_hash, make_rubric
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_source_index import FixtureEncoder, _long_evidence
from test_topic_source_review import _selection as _large_selection

if TYPE_CHECKING:
    from pydantic import BaseModel


def _reference(value: BaseModel, suffix: str, kind: str = "checks") -> HarnessArtifactRef:
    digest = content_hash(value)
    return HarnessArtifactRef(
        id=uuid5(NAMESPACE_URL, f"{suffix}:{digest}"),
        fingerprint=digest,
        sha256=digest,
        kind=HarnessArtifactKind(kind),
        sizeBytes=1,
        storageKey=f"tests/{suffix}-{digest}.json",
    )


def _index_case() -> tuple[
    HarnessEvidence,
    TopicSourceIndex,
    TopicSelectionRecord,
    TopicSelectionAssessment,
    TopicRepairPlan,
]:
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    index = build_topic_source_index(
        evidence,
        evidence_sha256=record.evidenceSha256,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    plan = build_repair_plan(
        evidence,
        index,
        record,
        assessment,
        index_sha256="c" * 64,
        selection_sha256=content_hash(record),
        assessment_sha256=content_hash(assessment),
    )
    return evidence, index, record, assessment, plan


def _garden_patch(
    record: TopicSelectionRecord, work_item: TopicRepairWorkItem
) -> TopicSelectionPatchV3:
    candidate = record.draft.proposal.candidates[0]
    replacement = TopicCandidate.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "title": "Regular watering and garden roots",
            "lastSentenceId": "s000003",
            "completionSpans": [{"firstSentenceId": "s000003", "lastSentenceId": "s000003"}],
        }
    )
    return TopicSelectionPatchV3.model_validate(
        {
            "baseSelectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "summary": "Complete and accurately title the garden discussion.",
            "operations": [
                {
                    "id": f"{work_item.workItemId}:operation:garden",
                    "kind": "replace_candidate",
                    "affectedCandidateIds": [candidate.id],
                    "findingIds": [value.root for value in work_item.findingIds],
                    "opportunities": [],
                    "replacementCandidates": [replacement.model_dump(mode="json")],
                    "reason": "Both the completion and title now match the exact source.",
                }
            ],
        }
    )


def test_connected_findings_share_one_bounded_component_and_prompt() -> None:
    evidence, index, record, assessment, plan = _index_case()

    assert len(plan.workItems) == 1
    item = plan.workItems[0]
    assert [value.root for value in item.findingIds] == [
        "synthetic-ending",
        "synthetic-title",
    ]
    assert [value.root for value in item.candidateIds] == ["garden-care"]
    assert [value.root for value in item.opportunityIds] == ["garden-value"]
    assert len(item.findingIds) <= MAX_REPAIR_FINDINGS
    assert len(item.candidateIds) <= MAX_REPAIR_CANDIDATES
    assert len(item.opportunityIds) <= MAX_REPAIR_OPPORTUNITIES

    prompt = repair_component_prompt(
        record,
        assessment,
        plan,
        item,
        source_index_map(index, index_sha256="c" * 64),
    )
    assert '"workItemId":"repair-component-0001"' in prompt
    assert "browse_source" in prompt
    assert "read_source" in prompt
    assert all(sentence.text not in prompt for sentence in evidence.sentences)


def test_disjoint_finding_resources_make_independent_components() -> None:
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    fragment = record.draft.proposal.candidates[1]
    opportunity = record.draft.opportunities[1]
    extra = TopicSelectionFinding.model_validate(
        {
            **assessment.findings[0].model_dump(mode="json"),
            "id": "fragment-title",
            "kind": "unsupported_title",
            "affectedCandidateIds": [fragment.id],
            "opportunityIds": [opportunity.id],
            "evidenceSpans": [fragment.coreSpans[0]],
        }
    )
    split_assessment = assessment.model_copy(update={"findings": [*assessment.findings, extra]})
    index = build_topic_source_index(
        evidence,
        evidence_sha256=record.evidenceSha256,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )

    plan = build_repair_plan(
        evidence,
        index,
        record,
        split_assessment,
        index_sha256="c" * 64,
        selection_sha256=content_hash(record),
        assessment_sha256=content_hash(split_assessment),
    )

    assert [[value.root for value in item.findingIds] for item in plan.workItems] == [
        ["synthetic-ending", "synthetic-title"],
        ["fragment-title"],
    ]


def test_oversized_coupled_component_refuses_instead_of_splitting_authority() -> None:
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    finding = assessment.findings[0]
    oversized = assessment.model_copy(
        update={
            "findings": [
                finding.model_copy(update={"id": f"coupled-{offset:02d}"})
                for offset in range(MAX_REPAIR_FINDINGS + 1)
            ]
        }
    )
    index = build_topic_source_index(
        evidence,
        evidence_sha256=record.evidenceSha256,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )

    with pytest.raises(HarnessValidationError, match="exceeds twelve findings"):
        build_repair_plan(
            evidence,
            index,
            record,
            oversized,
            index_sha256="c" * 64,
            selection_sha256=content_hash(record),
            assessment_sha256=content_hash(oversized),
        )


def test_four_hour_repair_plan_keeps_each_independent_component_bounded() -> None:
    evidence = _long_evidence(2_400)
    evidence_sha256 = content_hash(evidence)
    index = build_topic_source_index(
        evidence,
        evidence_sha256=evidence_sha256,
        encoder=FixtureEncoder(),
        embedding_revision="b" * 40,
    )
    draft = _large_selection(evidence, 1_200)
    rubric = make_rubric("A technical audience.")
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": str(UUID(int=84)),
            "evidenceSha256": evidence_sha256,
            "rubricSha256": content_hash(rubric),
            "rubric": rubric.model_dump(mode="json"),
            "origin": "model",
            "parentSelectionSha256": None,
            "draft": draft.model_dump(mode="json"),
        }
    )
    assessment = TopicSelectionAssessment.model_validate(
        {
            "format": "topic-selection-assessment/2",
            "runId": record.runId,
            "selectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "proposerFamily": "synthetic-author",
            "verifierFamily": "synthetic-reviewer",
            "coldReviews": [],
            "portfolioReview": None,
            "executionStatus": "needs_review",
            "responseArtifacts": [],
            "reasons": ["Pathological independent repair scale fixture."],
            "findings": [
                {
                    "id": f"title-{ordinal:04d}",
                    "kind": "unsupported_title",
                    "severity": "required",
                    "affectedCandidateIds": [candidate.id],
                    "opportunityIds": [draft.opportunities[ordinal].id],
                    "evidenceSpans": [candidate.coreSpans[0].model_dump(mode="json")],
                    "reason": "The title needs an exact source-grounded correction.",
                }
                for ordinal, candidate in enumerate(draft.proposal.candidates)
            ],
        }
    )
    plan = build_repair_plan(
        evidence,
        index,
        record,
        assessment,
        index_sha256="c" * 64,
        selection_sha256=content_hash(record),
        assessment_sha256=content_hash(assessment),
    )

    assert evidence.durationMs == 4 * 60 * 60 * 1_000
    assert len(plan.workItems) == 1_200
    assert all(len(item.findingIds) == 1 for item in plan.workItems)
    assert all(len(item.candidateIds) == 1 for item in plan.workItems)
    assert all(len(item.opportunityIds) == 1 for item in plan.workItems)
    prompt = repair_component_prompt(
        record,
        assessment,
        plan,
        plan.workItems[0],
        source_index_map(index, index_sha256="c" * 64),
    )
    assert len(prompt.encode()) < 50_000
    assert "Source discussion sentence 2399" not in prompt


def test_complete_manifest_replays_shard_and_applies_one_atomic_patch() -> None:
    evidence, _, record, assessment, plan = _index_case()
    item = plan.workItems[0]
    patch = _garden_patch(record, item)
    response = _reference(patch, "response", "model_response")
    inspection = _reference(patch, "inspection")
    shard = admit_repair_shard(
        evidence,
        record,
        assessment,
        plan,
        item,
        patch,
        index_sha256="c" * 64,
        assessment_sha256=content_hash(assessment),
        response_artifact=response,
        inspection_artifact=inspection,
        author_family="synthetic-author",
    )
    shard_ref = _reference(shard, "shard")

    manifest = assemble_repair_manifest(
        evidence,
        record,
        assessment,
        plan,
        [shard],
        [shard_ref],
    )

    assert manifest.complete
    assert manifest.aggregatePatch == patch
    assert [value.root for value in manifest.workItemIds] == [item.workItemId]


def test_component_cannot_remap_an_opportunity_to_a_foreign_candidate() -> None:
    evidence, _, record, assessment, plan = _index_case()
    item = plan.workItems[0]
    patch = _garden_patch(record, item)
    operation = patch.operations[0]
    opportunity = record.draft.opportunities[0]
    foreign_candidate = record.draft.proposal.candidates[1]
    foreign_mapping = TopicOpportunity.model_validate(
        {
            **opportunity.model_dump(mode="json"),
            "candidateIds": [*opportunity.candidateIds, foreign_candidate.id],
        }
    )
    invalid = TopicSelectionPatchV3.model_validate(
        {
            **patch.model_dump(mode="json"),
            "operations": [
                {
                    **operation.model_dump(mode="json"),
                    "opportunities": [foreign_mapping.model_dump(mode="json")],
                }
            ],
        }
    )

    with pytest.raises(HarnessValidationError, match="mapping outside its component"):
        admit_repair_shard(
            evidence,
            record,
            assessment,
            plan,
            item,
            invalid,
            index_sha256="c" * 64,
            assessment_sha256=content_hash(assessment),
            response_artifact=_reference(invalid, "foreign-response", "model_response"),
            inspection_artifact=_reference(invalid, "foreign-inspection"),
            author_family="synthetic-author",
        )


def test_manifest_refuses_a_missing_component_without_applying_any_patch() -> None:
    evidence, _, record, assessment, plan = _index_case()

    with pytest.raises(HarnessValidationError, match="every exact planned shard"):
        assemble_repair_manifest(evidence, record, assessment, plan, [], [])


def test_manifest_rejects_candidate_write_conflicts_in_a_tampered_plan() -> None:
    evidence, _, record, assessment, plan = _index_case()
    first = plan.workItems[0].__class__.model_validate(
        {
            **plan.workItems[0].model_dump(mode="json"),
            "findingIds": ["synthetic-title"],
            "workItemId": "repair-component-0001",
        }
    )
    second = plan.workItems[0].__class__.model_validate(
        {
            **plan.workItems[0].model_dump(mode="json"),
            "ordinal": 1,
            "findingIds": ["synthetic-title"],
            "workItemId": "repair-component-0002",
        }
    )
    tampered = TopicRepairPlan.model_validate(
        {**plan.model_dump(mode="json"), "workItems": [first, second]}
    )
    candidate = record.draft.proposal.candidates[0]

    def retitle(item: TopicRepairWorkItem, title: str) -> TopicSelectionPatchV3:
        return TopicSelectionPatchV3.model_validate(
            {
                "baseSelectionSha256": content_hash(record),
                "evidenceSha256": record.evidenceSha256,
                "rubricSha256": record.rubricSha256,
                "summary": title,
                "operations": [
                    {
                        "id": f"{item.workItemId}:operation:title",
                        "kind": "retitle",
                        "affectedCandidateIds": [candidate.id],
                        "findingIds": [
                            str(getattr(value, "root", value)) for value in item.findingIds
                        ],
                        "opportunities": [],
                        "replacementCandidates": [
                            candidate.model_copy(update={"title": title}).model_dump(mode="json")
                        ],
                        "reason": "Use a source-supported title.",
                    }
                ],
            }
        )

    shards: list[TopicRepairShard] = []
    for item, title in ((first, "First title"), (second, "Second title")):
        patch = retitle(item, title)
        shards.append(
            TopicRepairShard.model_validate(
                {
                    "format": "topic-repair-shard/1",
                    "indexSha256": tampered.indexSha256,
                    "selectionSha256": tampered.selectionSha256,
                    "assessmentSha256": tampered.assessmentSha256,
                    "planSha256": content_hash(tampered),
                    "workItem": item,
                    "patch": patch,
                    "authorFamily": "synthetic-author",
                    "responseArtifact": _reference(patch, title, "model_response"),
                    "inspectionArtifact": _reference(patch, f"{title}-inspection"),
                }
            )
        )
    refs = [_reference(shard, f"shard-{offset}") for offset, shard in enumerate(shards)]

    with pytest.raises(HarnessValidationError, match="conflicting candidate writes"):
        assemble_repair_manifest(
            evidence,
            record,
            assessment,
            tampered,
            shards,
            refs,
        )


def test_cosmetic_identifier_shapes_are_normalised_rather_than_rejected() -> None:
    """The two shapes the frontier-seat runs were rejected on carry no editorial content."""
    evidence, _, record, assessment, plan = _index_case()
    item = plan.workItems[0]
    well_formed = _garden_patch(record, item)
    operation = well_formed.operations[0]
    fresh_id = f"{item.workItemId}:candidate:garden-care-complete"
    misshapen = well_formed.model_copy(
        update={
            "operations": [
                operation.model_copy(
                    update={
                        "id": f"{item.workItemId}:op:garden",
                        "replacementCandidates": [
                            operation.replacementCandidates[0].model_copy(update={"id": fresh_id})
                        ],
                    }
                )
            ]
        }
    )
    normalised, notes = normalise_repair_patch(record, item, misshapen)
    assert len(notes) == 2
    assert normalised.operations[0].id == f"{item.workItemId}:operation:garden"
    assert normalised.operations[0].replacementCandidates[0].id == operation.affectedCandidateIds[0]
    shard = admit_repair_shard(
        evidence,
        record,
        assessment,
        plan,
        item,
        misshapen,
        index_sha256="c" * 64,
        assessment_sha256=content_hash(assessment),
        response_artifact=_reference(misshapen, "misshapen-response", "model_response"),
        inspection_artifact=_reference(misshapen, "misshapen-inspection"),
        author_family="synthetic-author",
    )
    assert shard.patch == normalised
    # Assembly re-admits the shard's own patch; normalisation is idempotent so it agrees.
    again = admit_repair_shard(
        evidence,
        record,
        assessment,
        plan,
        item,
        shard.patch,
        index_sha256="c" * 64,
        assessment_sha256=content_hash(assessment),
        response_artifact=shard.responseArtifact,
        inspection_artifact=shard.inspectionArtifact,
        author_family="synthetic-author",
    )
    assert again == shard
    untouched, no_notes = normalise_repair_patch(record, item, well_formed)
    assert no_notes == ()
    assert untouched is well_formed


def test_an_edit_keeping_an_existing_but_different_candidate_id_is_not_rewritten() -> None:
    """Only a fresh id is cosmetic; naming another existing candidate is an editorial error."""
    evidence, _, record, assessment, plan = _index_case()
    item = plan.workItems[0]
    patch = _garden_patch(record, item)
    other = "some-other-existing-candidate"
    record = record.model_copy(
        update={
            "draft": record.draft.model_copy(
                update={
                    "proposal": record.draft.proposal.model_copy(
                        update={
                            "candidates": [
                                *record.draft.proposal.candidates,
                                record.draft.proposal.candidates[0].model_copy(
                                    update={"id": other}
                                ),
                            ]
                        }
                    )
                }
            )
        }
    )
    operation = patch.operations[0]
    wrong = patch.model_copy(
        update={
            "operations": [
                operation.model_copy(
                    update={
                        "replacementCandidates": [
                            operation.replacementCandidates[0].model_copy(update={"id": other})
                        ]
                    }
                )
            ]
        }
    )
    _, notes = normalise_repair_patch(record, item, wrong)
    assert notes == ()
    _ = evidence, assessment
