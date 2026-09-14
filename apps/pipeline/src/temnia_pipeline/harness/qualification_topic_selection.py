"""Exact production topic request qualification, distinct from editorial acceptance."""

# Refusal messages and explicit fixture construction make this evidence contract auditable.
# ruff: noqa: EM101, TRY003
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from pydantic_ai.messages import (  # noqa: TC002 - inspected at runtime by ProcessHistory
    ModelMessage,
)
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.tools import GenerateToolJsonSchema

from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    Relation,
    TopicAuthorWorkItem,
    TopicCandidate,
    TopicCandidateInspectionPage,
    TopicCandidateRegionHit,
    TopicInventorySection,
    TopicMediaEvidencePage,
    TopicOpportunity,
    TopicOpportunityInventoryPlan,
    TopicPortfolioReviewV4,
    TopicProposal,
    TopicRepairPlan,
    TopicRepairWorkItem,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatchV3,
    TopicSelectionRecord,
    TopicSentenceSpan,
    TopicSourceBrowsePage,
    TopicSourceIndex,
    TopicSourceIndexSentence,
    TopicSourceNodeHit,
    TopicSourceReadPage,
    TopicSourceReviewWorkItem,
    TopicSourceSearchPage,
)
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.editorial_evidence import read_topic_media_evidence
from temnia_pipeline.harness.qualification_fixture import synthetic_qualification_evidence
from temnia_pipeline.harness.source_progress import compact_source_messages
from temnia_pipeline.harness.topic_author_packaging import (
    admit_author_shard,
    build_author_plan,
)
from temnia_pipeline.harness.topic_feasible import augment_topic_evidence
from temnia_pipeline.harness.topic_repair import (
    REPAIR_COMPONENT_PROMPT_VERSION,
    admit_repair_shard,
    repair_component_prompt,
)
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
    SELECTION_AUTHOR_SHARD_PROMPT,
    SELECTION_COLD_PROMPT_V3,
    SELECTION_COLD_PROMPT_V4,
    SELECTION_INVENTORY_PROMPT,
    SELECTION_INVENTORY_SHARD_PROMPT,
    SELECTION_PATCH_PROMPT_V3,
    SELECTION_SOURCE_PROMPT_V3,
    SELECTION_SOURCE_SHARD_PROMPT,
    apply_selection_patch,
    assess_selection,
    author_packaging_shard_prompt,
    content_hash,
    make_rubric,
    opportunity_inventory_prompt,
    opportunity_inventory_shard_prompt,
    selection_cold_prompt,
    selection_patch_prompt_v3,
    selection_prompt,
    selection_source_prompt,
    source_review_shard_prompt,
    validate_opportunity_inventory,
    validate_selection,
    validate_selection_against_inventory,
)
from temnia_pipeline.harness.topic_source_review import admit_source_review_shard

if TYPE_CHECKING:
    from collections.abc import Callable

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
TOPIC_SELECTION_V4_STAGES = (
    "topic_inventory_shard",
    "topic_author",
    "topic_cold",
    "topic_source",
    "topic_patch",
)
TOPIC_SELECTION_V4_SCHEMAS = {
    "topic_inventory_shard": "topic-selection-draft/2",
    "topic_author": "topic-selection-draft/2",
    "topic_cold": SELECTION_COLD_PROMPT_V3,
    "topic_source": "topic-selection-portfolio/4",
    "topic_patch": SELECTION_PATCH_PROMPT_V3,
}
TOPIC_SELECTION_V5_STAGES = TOPIC_SELECTION_V4_STAGES
TOPIC_SELECTION_V5_SCHEMAS = dict(TOPIC_SELECTION_V4_SCHEMAS)
TOPIC_SELECTION_V6_STAGES = TOPIC_SELECTION_V5_STAGES
TOPIC_SELECTION_V6_SCHEMAS = dict(TOPIC_SELECTION_V5_SCHEMAS)
TOPIC_SELECTION_V7_STAGES = TOPIC_SELECTION_V6_STAGES
TOPIC_SELECTION_V7_SCHEMAS = {
    **TOPIC_SELECTION_V6_SCHEMAS,
    "topic_cold": SELECTION_COLD_PROMPT_V4,
    "topic_patch": REPAIR_COMPONENT_PROMPT_VERSION,
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


def topic_source_progress_processor(
    stage: str,
    suite: str = "topic-selection-v3",
) -> Callable[[list[ModelMessage]], list[ModelMessage]] | None:
    """Use the production compaction contract in indexed route pre-flight calls."""
    roles: dict[
        str, Literal["inventory", "author", "source_reviewer", "repair", "cold_reviewer"]
    ] = {
        "topic_inventory": "inventory",
        "topic_inventory_shard": "inventory",
        "topic_author": "author",
        "topic_source": "source_reviewer",
    }
    if suite == "topic-selection-v7":
        roles["topic_patch"] = "repair"
        roles["topic_cold"] = "cold_reviewer"
    role = roles.get(stage)
    if role is None:
        return None

    def process(messages: list[ModelMessage]) -> list[ModelMessage]:
        return compact_source_messages(
            messages,
            index_sha256=str(QUALIFICATION_SOURCE_INDEX["indexSha256"]),
            role=role,
            stage=stage,
        )

    return process


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


async def inspect_candidate(
    candidate_id: str, cursor: int = 0, limit: int = 8
) -> TopicCandidateInspectionPage:
    """Inspect the one synthetic candidate using the production result shape."""
    if candidate_id != "garden-care" or cursor != 0 or limit < 1:
        raise ValueError("qualification selection has one candidate at cursor zero")
    _, selection, _ = topic_selection_qualification_case()
    candidate = selection.draft.proposal.candidates[0]
    node = _qualification_node("region")
    return TopicCandidateInspectionPage(
        candidateId=candidate_id,
        complete=True,
        indexSha256="0" * 64,
        nextCursor=None,
        regions=[
            TopicCandidateRegionHit(
                id=node.id,
                parentId=node.parentId,
                firstSentenceId=node.firstSentenceId,
                lastSentenceId=node.lastSentenceId,
                selectedFirstSentenceId=candidate.firstSentenceId,
                selectedLastSentenceId=candidate.lastSentenceId,
                startMs=node.startMs,
                endMs=node.endMs,
                sentenceCount=node.sentenceCount,
                keywords=node.keywords,
                preview=node.preview,
                relation=Relation.covers_candidate,
            )
        ],
        selectionSha256="2" * 64,
    )


async def read_media_evidence(
    first_sentence_id: str,
    last_sentence_id: str,
    cursor: int = 0,
    limit: int = 40,
) -> TopicMediaEvidencePage:
    """Read synthetic measured events using the production result shape."""
    return read_topic_media_evidence(
        synthetic_qualification_evidence(),
        evidence_sha256="1" * 64,
        first_sentence_id=first_sentence_id,
        last_sentence_id=last_sentence_id,
        cursor=cursor,
        limit=limit,
    )


def topic_source_qualification_tools(stage: str, suite: str = "topic-selection-v3") -> list[Any]:
    """Expose the exact role-specific indexed production tools."""
    if stage in {"topic_inventory", "topic_inventory_shard", "topic_author"}:
        return [browse_source, search_source, read_source]
    if stage == "topic_source":
        return [
            browse_source,
            search_source,
            read_source,
            inspect_candidate,
            read_media_evidence,
        ]
    if stage == "topic_cold" and suite == "topic-selection-v7":
        return [read_source]
    if stage == "topic_patch" and suite == "topic-selection-v7":
        return [browse_source, search_source, read_source]
    return []


STAGE_SEATS = {
    "topic_inventory": "verify",
    "topic_inventory_shard": "verify",
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


def topic_selection_v4_qualification_inventory() -> TopicSelectionDraft:
    """Bind the qualification source to the production section ownership convention."""
    payload = topic_selection_qualification_inventory().model_dump(mode="json")
    for item in payload["opportunities"]:
        item["id"] = f"section-0001:{item['id']}"
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


def topic_selection_v4_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Render the exact bounded-inventory program request shapes."""
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    inventory = topic_selection_v4_qualification_inventory()
    section = TopicInventorySection(
        sectionId="section-0001",
        ordinal=0,
        ownershipSpan=TopicSentenceSpan(
            firstSentenceId=evidence.sentences[0].id,
            lastSentenceId=evidence.sentences[-1].id,
        ),
        previousSectionId=None,
        nextSectionId=None,
    )
    return {
        "topic_inventory_shard": (
            opportunity_inventory_shard_prompt(QUALIFICATION_SOURCE_INDEX, record.rubric, section),
            TopicSelectionDraft,
            SELECTION_INVENTORY_SHARD_PROMPT,
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


def _v5_qualification_work_item() -> TopicAuthorWorkItem:
    inventory = topic_selection_v4_qualification_inventory()
    return TopicAuthorWorkItem.model_validate(
        {
            "workItemId": "section-0001:author-0001",
            "ordinal": 0,
            "sectionId": "section-0001",
            "batchOrdinal": 0,
            "opportunityIds": [item.id for item in inventory.opportunities],
        }
    )


def topic_selection_v5_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Render the exact bounded-inventory and bounded-author request shapes."""
    prompts = topic_selection_v4_qualification_prompts()
    _, record, _ = topic_selection_qualification_case(combined_patch=True)
    inventory = topic_selection_v4_qualification_inventory()
    work_item = _v5_qualification_work_item()
    prompts["topic_author"] = (
        author_packaging_shard_prompt(
            QUALIFICATION_SOURCE_INDEX,
            record.rubric,
            work_item,
            inventory,
        ),
        TopicSelectionDraft,
        SELECTION_AUTHOR_SHARD_PROMPT,
    )
    return prompts


def _v6_qualification_work_item() -> TopicSourceReviewWorkItem:
    """One exact local candidate assignment for the changed source-review request."""
    return TopicSourceReviewWorkItem.model_validate(
        {
            "workItemId": "section-0001:source-local-0001",
            "ordinal": 0,
            "sectionId": "section-0001",
            "batchOrdinal": 0,
            "kind": "local",
            "candidateIds": ["garden-care"],
            "opportunityIds": [],
            "contextOpportunityIds": ["garden-value"],
            "inspectionCandidateIds": ["garden-care"],
            "discoverMissingOpportunities": False,
            "overlaps": [],
            "handoffs": [],
            "sourceSpan": None,
        }
    )


def topic_selection_v6_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Render the exact bounded inventory, author and source-review request shapes."""
    prompts = topic_selection_v5_qualification_prompts()
    _, record, _ = topic_selection_qualification_case(combined_patch=True)
    prompts["topic_source"] = (
        source_review_shard_prompt(
            record.draft,
            record.rubric,
            QUALIFICATION_SOURCE_INDEX,
            _v6_qualification_work_item(),
        ),
        TopicPortfolioReviewV4,
        SELECTION_SOURCE_SHARD_PROMPT,
    )
    return prompts


def _v7_qualification_repair_plan() -> TopicRepairPlan:
    """One coupled title/extent component for the changed indexed repair request."""
    _, record, assessment = topic_selection_qualification_case(combined_patch=True)
    work_item = TopicRepairWorkItem.model_validate(
        {
            "workItemId": "repair-component-0001",
            "ordinal": 0,
            "findingIds": [item.id for item in assessment.findings],
            "candidateIds": ["garden-care"],
            "opportunityIds": ["garden-value"],
            "browseParentIds": ["section-0001"],
        }
    )
    return TopicRepairPlan.model_validate(
        {
            "format": "topic-repair-plan/1",
            "indexSha256": "0" * 64,
            "selectionSha256": content_hash(record),
            "assessmentSha256": content_hash(assessment),
            "maxFindingsPerWorkItem": 12,
            "maxCandidatesPerWorkItem": 8,
            "maxOpportunitiesPerWorkItem": 24,
            "workItems": [work_item.model_dump(mode="json")],
        }
    )


def topic_selection_v7_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Render v6 requests plus the exact indexed connected-component repair request."""
    prompts = topic_selection_v6_qualification_prompts()
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    prompts["topic_cold"] = (
        selection_cold_prompt(
            evidence, record.draft.proposal.candidates[0], record.rubric, indexed=True
        ),
        TopicSelectionColdReview,
        SELECTION_COLD_PROMPT_V4,
    )
    plan = _v7_qualification_repair_plan()
    prompts["topic_patch"] = (
        repair_component_prompt(
            record,
            assessment,
            plan,
            plan.workItems[0],
            QUALIFICATION_SOURCE_INDEX,
        ),
        TopicSelectionPatchV3,
        REPAIR_COMPONENT_PROMPT_VERSION,
    )
    return prompts


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


def validate_topic_selection_v4_qualification_output(stage: str, output: object) -> None:
    """Ground the bounded shard and preserve the existing judgments for all later stages."""
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    if stage == "topic_inventory_shard" and isinstance(output, TopicSelectionDraft):
        validate_opportunity_inventory(evidence, output)
        prefix = "section-0001:"
        if any(not item.id.startswith(prefix) for item in output.opportunities):
            raise ValueError("topic qualification shard output lacks its ownership prefix")
        return
    if stage == "topic_author" and isinstance(output, TopicSelectionDraft):
        validate_selection(evidence, output)
        validate_selection_against_inventory(topic_selection_v4_qualification_inventory(), output)
        return
    validate_topic_selection_qualification_output(stage, output)


def validate_topic_selection_v5_qualification_output(stage: str, output: object) -> None:
    """Ground the bounded author shard against its exact synthetic inventory assignment."""
    if stage != "topic_author":
        validate_topic_selection_v4_qualification_output(stage, output)
        return
    if not isinstance(output, TopicSelectionDraft):
        raise TypeError("topic qualification author output has the wrong type")
    evidence, _, _ = topic_selection_qualification_case(combined_patch=True)
    inventory = topic_selection_v4_qualification_inventory()
    # Qualification uses the same deterministic author admission. Only its tiny section plan is
    # synthetic; the prompt, native schema and output validation are the production functions.
    section = TopicInventorySection(
        sectionId="section-0001",
        ordinal=0,
        ownershipSpan=TopicSentenceSpan(
            firstSentenceId=evidence.sentences[0].id,
            lastSentenceId=evidence.sentences[-1].id,
        ),
        previousSectionId=None,
        nextSectionId=None,
    )
    inventory_plan = TopicOpportunityInventoryPlan(
        format="topic-opportunity-inventory-plan/1",
        indexSha256="0" * 64,
        sections=[section],
    )
    plan = build_author_plan(
        inventory_plan,
        inventory,
        index_sha256="0" * 64,
        inventory_sha256="3" * 64,
    )
    admit_author_shard(
        evidence,
        inventory,
        plan,
        plan_sha256="4" * 64,
        work_item_id=plan.workItems[0].workItemId,
        draft=output,
        generator_family="synthetic-author",
    )


def validate_topic_selection_v6_qualification_output(stage: str, output: object) -> None:
    """Ground the changed source shard and delegate unchanged stage admission to v5."""
    if stage != "topic_source":
        validate_topic_selection_v5_qualification_output(stage, output)
        return
    if not isinstance(output, TopicPortfolioReviewV4):
        raise TypeError("topic qualification source output has the wrong type")
    evidence, record, _ = topic_selection_qualification_case(combined_patch=True)
    work_item = _v6_qualification_work_item()
    from temnia_pipeline.contracts import TopicSourceReviewPlan  # noqa: PLC0415

    plan = TopicSourceReviewPlan.model_validate(
        {
            "format": "topic-source-review-plan/1",
            "indexSha256": "0" * 64,
            "selectionSha256": content_hash(record),
            "maxCandidatesPerLocalWorkItem": 4,
            "maxOpportunitiesPerLocalWorkItem": 12,
            "maxPairsPerRelationshipWorkItem": 2,
            "workItems": [work_item.model_dump(mode="json")],
        }
    )
    response = HarnessArtifactRef.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000077",
            "fingerprint": "9" * 64,
            "sha256": "9" * 64,
            "kind": "model_response",
            "sizeBytes": 1,
            "storageKey": "qualification/source-response.json",
        }
    )
    admit_source_review_shard(
        evidence,
        TopicSourceIndex.model_construct(),
        record.draft,
        plan,
        plan_sha256="8" * 64,
        work_item_id=work_item.workItemId,
        review=output,
        reviewer_family="synthetic-reviewer",
        response_artifact=response,
        inspection_artifact=None,
    )


def validate_topic_selection_v7_qualification_output(stage: str, output: object) -> None:
    """Ground the indexed repair component and delegate unchanged v6 stages."""
    if stage != "topic_patch":
        validate_topic_selection_v6_qualification_output(stage, output)
        return
    if not isinstance(output, TopicSelectionPatchV3):
        raise TypeError("topic qualification repair output has the wrong type")
    evidence, record, assessment = topic_selection_qualification_case(combined_patch=True)
    plan = _v7_qualification_repair_plan()
    response = HarnessArtifactRef.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000079",
            "fingerprint": "7" * 64,
            "sha256": "7" * 64,
            "kind": "model_response",
            "sizeBytes": 1,
            "storageKey": "qualification/repair-response.json",
        }
    )
    inspection = HarnessArtifactRef.model_validate(
        {
            **response.model_dump(mode="json"),
            "id": str(UUID("00000000-0000-0000-0000-000000000080")),
            "kind": "checks",
        }
    )
    admit_repair_shard(
        evidence,
        record,
        assessment,
        plan,
        plan.workItems[0],
        output,
        index_sha256="0" * 64,
        assessment_sha256=content_hash(assessment),
        response_artifact=response,
        inspection_artifact=inspection,
        author_family="synthetic-author",
    )


def native_schema_sha256(output_type: type[BaseModel]) -> str:
    """Match the pinned SDK native strict schema; transport tests detect SDK drift."""
    schema = TypeAdapter(output_type).json_schema(schema_generator=GenerateToolJsonSchema)
    schema.pop("description", None)
    return _sha(OpenAIJsonSchemaTransformer(schema, strict=True).walk())


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
