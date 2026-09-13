"""Exact production topic request qualification, distinct from editorial acceptance."""

# Refusal messages and explicit fixture construction make this evidence contract auditable.
# ruff: noqa: EM101, TRY003, C901
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.tools import GenerateToolJsonSchema

from temnia_pipeline.contracts import (
    TopicCandidate,
    TopicOpportunity,
    TopicPortfolioReview,
    TopicPortfolioReviewV3,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatch,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSentenceSpan,
)
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3
from temnia_pipeline.harness.qualification_fixture import synthetic_qualification_evidence
from temnia_pipeline.harness.topic_feasible import augment_topic_evidence
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    SELECTION_COLD_PROMPT,
    SELECTION_COLD_PROMPT_V3,
    SELECTION_INVENTORY_PROMPT,
    SELECTION_PATCH_PROMPT,
    SELECTION_PATCH_PROMPT_V3,
    SELECTION_POLICY,
    SELECTION_PROMPT,
    SELECTION_SOURCE_PROMPT,
    SELECTION_SOURCE_PROMPT_V3,
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    opportunity_inventory_prompt,
    selection_cold_prompt,
    selection_patch_prompt,
    selection_patch_prompt_v3,
    selection_prompt,
    selection_source_prompt,
    validate_opportunity_inventory,
    validate_selection,
    validate_selection_against_inventory,
)

if TYPE_CHECKING:
    from temnia_pipeline.contracts import HarnessEvidence

TOPIC_SELECTION_STAGES = ("topic_author", "topic_cold", "topic_source", "topic_patch")
TOPIC_SELECTION_V3_STAGES = (
    "topic_inventory",
    "topic_author",
    "topic_cold",
    "topic_source",
    "topic_patch",
)
TOPIC_SELECTION_SCHEMAS = {
    "topic_author": "topic-selection-draft/2",
    "topic_cold": "topic-selection-cold/2",
    "topic_source": "topic-selection-portfolio/2",
    "topic_patch": "topic-selection-patch/2",
}
TOPIC_SELECTION_V3_SCHEMAS = {
    "topic_inventory": "topic-selection-draft/2",
    "topic_author": "topic-selection-draft/2",
    "topic_cold": SELECTION_COLD_PROMPT_V3,
    "topic_source": "topic-selection-portfolio/4",
    "topic_patch": SELECTION_PATCH_PROMPT_V3,
}
STAGE_SEATS = {
    "topic_inventory": "verify",
    "topic_author": "propose",
    "topic_cold": "verify",
    "topic_source": "verify",
    "topic_patch": "propose",
}


def topic_selection_qualification_case(
    *, combined_patch: bool = False
) -> tuple[HarnessEvidence, TopicSelectionRecord, TopicSelectionAssessment]:
    """An invented complete discussion with a separate incomplete ending; never customer gold."""
    evidence = synthetic_qualification_evidence()
    evidence = augment_topic_evidence(evidence)
    span = TopicSentenceSpan(
        firstSentenceId=evidence.sentences[1].id,
        lastSentenceId=evidence.sentences[2 if combined_patch else 3].id,
    )
    candidate = TopicCandidate(
        id="garden-care",
        title=(
            "A complete method for successful gardening"
            if combined_patch
            else "How regular watering supports garden roots"
        ),
        purpose="Explain a basic garden-care practice.",
        reason="A developed explanation available independently of the greeting.",
        firstSentenceId=span.firstSentenceId,
        lastSentenceId=span.lastSentenceId,
        coreSpans=[span],
        completionSpans=[
            TopicSentenceSpan(
                firstSentenceId=evidence.sentences[2 if combined_patch else 3].id,
                lastSentenceId=evidence.sentences[2 if combined_patch else 3].id,
            )
        ],
        requiredContextSpans=[],
        meaningChangingFollowups=[],
    )
    opportunity = TopicOpportunity.model_validate(
        {
            "id": "garden-value",
            "candidateIds": [candidate.id],
            "coreSpans": [span],
            "completionSpans": candidate.completionSpans,
            "requiredContextSpans": [],
            "meaningChangingFollowups": [],
            "valueEvidenceSpans": [span],
            "viewerPurpose": candidate.purpose,
            "disposition": "proposed",
            "dispositionReason": "This short explanation can stand independently.",
        }
    )
    candidates = [candidate]
    opportunities = [opportunity]
    if combined_patch:
        fragment_span = TopicSentenceSpan(
            firstSentenceId=evidence.sentences[4].id,
            lastSentenceId=evidence.sentences[4].id,
        )
        fragment = TopicCandidate(
            id="unfinished-followup",
            title="A follow-up condition",
            purpose="Assess whether the source contains another independent follow-up.",
            reason="The source appears to begin a follow-up after the garden explanation.",
            firstSentenceId=fragment_span.firstSentenceId,
            lastSentenceId=fragment_span.lastSentenceId,
            coreSpans=[fragment_span],
            completionSpans=[fragment_span],
            requiredContextSpans=[],
            meaningChangingFollowups=[],
        )
        fragment_opportunity = TopicOpportunity.model_validate(
            {
                "id": "unfinished-followup-value",
                "candidateIds": [fragment.id],
                "coreSpans": [fragment_span],
                "completionSpans": [fragment_span],
                "requiredContextSpans": [],
                "meaningChangingFollowups": [],
                "valueEvidenceSpans": [fragment_span],
                "viewerPurpose": fragment.purpose,
                "disposition": "proposed",
                "dispositionReason": "The apparent follow-up requires independent review.",
            }
        )
        candidates.append(fragment)
        opportunities.append(fragment_opportunity)
    rubric = make_rubric("Find worthwhile independent explanations for beginning gardeners.")
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": str(UUID(int=42)),
            "evidenceSha256": content_hash(evidence),
            "rubricSha256": content_hash(rubric),
            "rubric": rubric,
            "origin": "model",
            "parentSelectionSha256": None,
            "draft": TopicSelectionDraft(
                opportunities=opportunities,
                proposal=TopicProposal(
                    version=1,
                    candidates=candidates,
                    summary="One useful discussion and one explicit ending fragment.",
                ),
            ),
        }
    )
    findings: list[dict[str, object]] = [
        {
            "id": "synthetic-title",
            "kind": "unsupported_title",
            "severity": "required",
            "affectedCandidateIds": [candidate.id],
            "opportunityIds": [opportunity.id],
            "evidenceSpans": [span],
            "reason": (
                "The title claims a complete gardening method, but the source only states that "
                "a garden needs attention and that water supports healthy roots."
            ),
        }
    ]
    if combined_patch:
        findings.insert(
            0,
            {
                "id": "synthetic-ending",
                "kind": "unfinished_discussion",
                "severity": "required",
                "affectedCandidateIds": [candidate.id],
                "opportunityIds": [opportunity.id],
                "evidenceSpans": [
                    TopicSentenceSpan(
                        firstSentenceId=evidence.sentences[2].id,
                        lastSentenceId=evidence.sentences[3].id,
                    )
                ],
                "reason": "The selected discussion omits its explicit completion sentence.",
            },
        )
    # The fixture supplies no pretend critic results. V3 requires one coupled correction.
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
            "reasons": ["Synthetic title-correction fixture; no media or model judgment."],
            "findings": findings,
        }
    )
    return evidence, record, assessment


def topic_selection_qualification_inventory() -> TopicSelectionDraft:
    """Return the frozen pre-author source map used by the v3 transport proof."""
    _, record, _ = topic_selection_qualification_case(combined_patch=True)
    payload = record.draft.model_dump(mode="json")
    payload["proposal"]["candidates"] = []
    for item in payload["opportunities"]:
        item.update(
            candidateIds=[],
            disposition="needs_evidence",
            dispositionReason="Packaging is intentionally deferred.",
        )
    return TopicSelectionDraft.model_validate(payload)


def topic_selection_qualification_prompts(
    program_version: str = SELECTION_POLICY,
) -> dict[str, tuple[str, type[BaseModel], str]]:
    """Use the exact production prompts and native output types for one generation."""
    evidence, record, assessment = topic_selection_qualification_case(
        combined_patch=program_version == TOPIC_SELECTION_POLICY_V3
    )
    if program_version == TOPIC_SELECTION_POLICY_V3:
        inventory = topic_selection_qualification_inventory()
        return {
            "topic_inventory": (
                opportunity_inventory_prompt(evidence, record.rubric),
                TopicSelectionDraft,
                SELECTION_INVENTORY_PROMPT,
            ),
            "topic_author": (
                selection_prompt(evidence, record.rubric, source_inventory=inventory),
                TopicSelectionDraft,
                SELECTION_AUTHOR_PROMPT_V3,
            ),
            "topic_cold": (
                selection_cold_prompt(evidence, record.draft.proposal.candidates[0], record.rubric),
                TopicSelectionColdReview,
                SELECTION_COLD_PROMPT_V3,
            ),
            "topic_source": (
                selection_source_prompt(
                    evidence, record.draft, record.rubric, independent_projection=True
                ),
                TopicPortfolioReviewV4,
                SELECTION_SOURCE_PROMPT_V3,
            ),
            "topic_patch": (
                selection_patch_prompt_v3(evidence, record, assessment, content_hash(record)),
                TopicSelectionPatchV3,
                SELECTION_PATCH_PROMPT_V3,
            ),
        }
    if program_version != SELECTION_POLICY:
        raise ValueError("unknown topic selection qualification generation")
    return {
        "topic_author": (
            selection_prompt(evidence, record.rubric),
            TopicSelectionDraft,
            SELECTION_PROMPT,
        ),
        "topic_cold": (
            selection_cold_prompt(evidence, record.draft.proposal.candidates[0], record.rubric),
            TopicSelectionColdReview,
            SELECTION_COLD_PROMPT,
        ),
        "topic_source": (
            selection_source_prompt(evidence, record.draft, record.rubric),
            TopicPortfolioReview,
            SELECTION_SOURCE_PROMPT,
        ),
        "topic_patch": (
            selection_patch_prompt(evidence, record, assessment, content_hash(record)),
            TopicSelectionPatch,
            SELECTION_PATCH_PROMPT,
        ),
    }


def validate_topic_selection_qualification_output(
    stage: str, output: object, *, program_version: str = SELECTION_POLICY
) -> None:
    """Source admission is measured separately from schema transport and publication quality."""
    evidence, record, assessment = topic_selection_qualification_case(
        combined_patch=program_version == TOPIC_SELECTION_POLICY_V3
    )
    if stage == "topic_inventory" and isinstance(output, TopicSelectionDraft):
        if program_version != TOPIC_SELECTION_POLICY_V3:
            raise ValueError("inventory output belongs only to topic selection v3")
        validate_opportunity_inventory(evidence, output)
        return
    if stage == "topic_author" and isinstance(output, TopicSelectionDraft):
        validate_selection(evidence, output)
        if program_version == TOPIC_SELECTION_POLICY_V3:
            validate_selection_against_inventory(topic_selection_qualification_inventory(), output)
        return
    if stage == "topic_patch" and isinstance(output, (TopicSelectionPatch, TopicSelectionPatchV3)):
        apply_selection_patch(evidence, record, content_hash(record), assessment, output)
        return
    if stage not in {"topic_cold", "topic_source"}:
        raise ValueError("topic qualification output has the wrong stage or type")
    if stage == "topic_cold" and not isinstance(output, TopicSelectionColdReview):
        raise ValueError("topic qualification cold output has the wrong type")
    expected_source_type = (
        TopicPortfolioReviewV4
        if program_version == TOPIC_SELECTION_POLICY_V3
        else TopicPortfolioReview
    )
    if stage == "topic_source" and not isinstance(output, expected_source_type):
        raise ValueError("topic qualification source output has the wrong type")
    judged = assess_selection(
        evidence,
        record,
        content_hash(record),
        cold_reviews=[output] if isinstance(output, TopicSelectionColdReview) else [],
        source_review=(
            output
            if isinstance(
                output, (TopicPortfolioReview, TopicPortfolioReviewV3, TopicPortfolioReviewV4)
            )
            else None
        ),
        author_family="synthetic-author",
        verifier_family="synthetic-reviewer",
        require_source_candidate_reviews=program_version != TOPIC_SELECTION_POLICY_V3,
    )
    if stage == "topic_cold" and len(judged.coldReviews) != 1:
        raise ValueError("topic qualification cold observation is not source-grounded")
    if stage == "topic_source" and judged.portfolioReview is None:
        raise ValueError("topic qualification source observation is not source-grounded")


def native_schema_sha256(output_type: type[BaseModel]) -> str:
    """Match the pinned SDK native strict schema; transport tests detect SDK drift."""
    schema = TypeAdapter(output_type).json_schema(schema_generator=GenerateToolJsonSchema)
    schema.pop("description", None)
    return _sha(OpenAIJsonSchemaTransformer(schema, strict=True).walk())


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
