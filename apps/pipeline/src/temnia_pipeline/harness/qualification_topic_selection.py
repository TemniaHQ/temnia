"""Exact production topic request qualification, distinct from editorial acceptance."""

# Refusal messages and explicit fixture construction make this evidence contract auditable.
# ruff: noqa: EM101, TRY003
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.tools import GenerateToolJsonSchema

from temnia_pipeline.contracts import (
    TopicCandidate,
    TopicOpportunity,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSentenceSpan,
    TopicSourceBrowsePage,
    TopicSourceIndexSentence,
    TopicSourceNodeHit,
    TopicSourceReadPage,
    TopicSourceSearchPage,
)
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.qualification_fixture import synthetic_qualification_evidence
from temnia_pipeline.harness.topic_feasible import augment_topic_evidence
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    SELECTION_COLD_PROMPT_V3,
    SELECTION_INVENTORY_PROMPT,
    SELECTION_PATCH_PROMPT_V3,
    SELECTION_SOURCE_PROMPT_V3,
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    opportunity_inventory_prompt,
    selection_cold_prompt,
    selection_patch_prompt_v3,
    selection_prompt,
    selection_source_prompt,
    validate_opportunity_inventory,
    validate_selection,
    validate_selection_against_inventory,
)

if TYPE_CHECKING:
    from temnia_pipeline.contracts import HarnessEvidence

TOPIC_SELECTION_V3_STAGES = (
    "topic_inventory",
    "topic_author",
    "topic_cold",
    "topic_source",
    "topic_patch",
)
TOPIC_SELECTION_V3_SCHEMAS = {
    "topic_inventory": "topic-selection-draft/2",
    "topic_author": "topic-selection-draft/2",
    "topic_cold": SELECTION_COLD_PROMPT_V3,
    "topic_source": "topic-selection-portfolio/4",
    "topic_patch": SELECTION_PATCH_PROMPT_V3,
}
QUALIFICATION_SOURCE_INDEX = {
    "indexSha256": "0" * 64,
    "rootNodeId": "episode",
    "hierarchyDepth": 3,
    "sectionCount": 1,
    "regionCount": 1,
    "sentenceCount": 5,
    "durationMs": 5_000,
    "browsePageLimit": 16,
    "searchPageLimit": 12,
    "readSentenceLimit": 80,
    "readCharacterLimit": 64_000,
}


def _qualification_node(kind: Literal["section", "region"]) -> TopicSourceNodeHit:
    evidence = synthetic_qualification_evidence()
    section = kind == "section"
    return TopicSourceNodeHit.model_validate(
        {
            "id": "section-0001" if section else "region-0001",
            "kind": "section" if section else "region",
            "parentId": "episode" if section else "section-0001",
            "childCount": 1 if section else 0,
            "firstSentenceId": evidence.sentences[0].id,
            "lastSentenceId": evidence.sentences[-1].id,
            "startMs": evidence.sentences[0].startMs,
            "endMs": evidence.sentences[-1].endMs,
            "sentenceCount": len(evidence.sentences),
            "keywords": ["garden", "watering", "roots"],
            "preview": "A greeting leads into a complete garden-watering explanation.",
            "score": None,
        }
    )


async def browse_source(
    parent_id: str = "episode", cursor: int = 0, limit: int = 8
) -> TopicSourceBrowsePage:
    """Browse the synthetic qualification hierarchy using the production shape."""
    if cursor != 0 or limit < 1:
        raise ValueError("qualification source has only cursor zero")
    if parent_id not in {"episode", "section-0001"}:
        raise ValueError("qualification source has one episode and one section")
    return TopicSourceBrowsePage(
        indexSha256="0" * 64,
        parentId=parent_id,
        nodes=[_qualification_node("section" if parent_id == "episode" else "region")],
        nextCursor=None,
        complete=True,
    )


async def search_source(query: str, cursor: int = 0, limit: int = 6) -> TopicSourceSearchPage:
    """Search the synthetic qualification source using the production tool shape."""
    _ = cursor, limit
    region = _qualification_node("region").model_copy(update={"score": 1.0})
    return TopicSourceSearchPage(
        indexSha256="0" * 64,
        query=query,
        regions=[region],
        nextCursor=None,
        complete=True,
    )


async def read_source(
    first_sentence_id: str,
    last_sentence_id: str,
    cursor_sentence_id: str | None = None,
    limit: int = 40,
) -> TopicSourceReadPage:
    """Read exact synthetic sentences using the production bounded-read shape."""
    evidence = synthetic_qualification_evidence()
    positions = {sentence.id: offset for offset, sentence in enumerate(evidence.sentences)}
    first = positions[first_sentence_id]
    last = positions[last_sentence_id]
    cursor = positions[cursor_sentence_id] if cursor_sentence_id is not None else first
    end = min(last + 1, cursor + limit)
    sentences = [
        TopicSourceIndexSentence(
            id=sentence.id,
            startMs=sentence.startMs,
            endMs=sentence.endMs,
            speakers=sentence.speakers,
            text=sentence.text,
        )
        for sentence in evidence.sentences[cursor:end]
    ]
    return TopicSourceReadPage(
        indexSha256="0" * 64,
        sentences=sentences,
        nextSentenceId=evidence.sentences[end].id if end <= last else None,
        complete=end > last,
    )


def topic_source_qualification_tools(stage: str) -> list[Any]:
    """Expose the exact three tool names only on indexed production stages."""
    if stage in {"topic_inventory", "topic_author", "topic_source"}:
        return [browse_source, search_source, read_source]
    return []


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


def topic_selection_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Use the exact production prompts and native output types of the one program."""
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    inventory = topic_selection_qualification_inventory()
    return {
        "topic_inventory": (
            opportunity_inventory_prompt(QUALIFICATION_SOURCE_INDEX, record.rubric),
            TopicSelectionDraft,
            SELECTION_INVENTORY_PROMPT,
        ),
        "topic_author": (
            selection_prompt(QUALIFICATION_SOURCE_INDEX, record.rubric, source_inventory=inventory),
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
                evidence, record.draft, record.rubric, QUALIFICATION_SOURCE_INDEX
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


def validate_topic_selection_qualification_output(stage: str, output: object) -> None:
    """Source admission is measured separately from schema transport and publication quality."""
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    if stage == "topic_inventory" and isinstance(output, TopicSelectionDraft):
        validate_opportunity_inventory(evidence, output)
        return
    if stage == "topic_author" and isinstance(output, TopicSelectionDraft):
        validate_selection(evidence, output)
        validate_selection_against_inventory(topic_selection_qualification_inventory(), output)
        return
    if stage == "topic_patch" and isinstance(output, TopicSelectionPatchV3):
        apply_selection_patch(evidence, record, content_hash(record), assessment, output)
        return
    if stage not in {"topic_cold", "topic_source"}:
        raise ValueError("topic qualification output has the wrong stage or type")
    if stage == "topic_cold" and not isinstance(output, TopicSelectionColdReview):
        raise ValueError("topic qualification cold output has the wrong type")
    if stage == "topic_source" and not isinstance(output, TopicPortfolioReviewV4):
        raise ValueError("topic qualification source output has the wrong type")
    judged = assess_selection(
        evidence,
        record,
        content_hash(record),
        cold_reviews=[output] if isinstance(output, TopicSelectionColdReview) else [],
        source_review=output if isinstance(output, TopicPortfolioReviewV4) else None,
        author_family="synthetic-author",
        verifier_family="synthetic-reviewer",
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
