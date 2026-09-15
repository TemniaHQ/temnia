import { z } from "zod";
import { HarnessArtifactRefSchema } from "./harness.ts";
import { ScopeSchema } from "./scope.ts";
import {
  TopicColdReviewSchema,
  TopicCriterionSchema,
  TopicSourceJudgmentSchema,
} from "./topic-runtime.ts";
import {
  TopicCandidateSchema,
  TopicProposalSchema,
  TopicSentenceSpanSchema,
} from "./topics.ts";

const identifier = () => z.string().min(1).max(256);
const reason = () => z.string().min(1);
const SHA256_PATTERN = /^[a-fA-F0-9]{64}$/;
const sha256 = () => z.string().regex(SHA256_PATTERN);
const spans = () => z.array(TopicSentenceSpanSchema);

/** One exact transcript sentence retained in the source index. */
export const TopicSourceIndexSentenceSchema = z
  .object({
    endMs: z.int().nonnegative(),
    id: identifier(),
    speakers: z.array(z.string()),
    startMs: z.int().nonnegative(),
    text: z.string(),
  })
  .strict()
  .meta({
    id: "TopicSourceIndexSentence",
    title: "TopicSourceIndexSentence",
  });
export type TopicSourceIndexSentence = z.infer<
  typeof TopicSourceIndexSentenceSchema
>;

/** One node in the exact episode -> section -> region navigation hierarchy. */
export const TopicSourceIndexNodeSchema = z
  .object({
    childIds: z.array(identifier()),
    embedding: z.array(z.number().finite()),
    endMs: z.int().nonnegative(),
    firstSentenceId: identifier(),
    id: identifier(),
    keywords: z.array(z.string()),
    kind: z.enum(["episode", "section", "region"]),
    lastSentenceId: identifier(),
    ordinal: z.int().nonnegative(),
    parentId: identifier().nullable(),
    preview: z.string(),
    sentenceCount: z.int().positive(),
    startMs: z.int().nonnegative(),
  })
  .strict()
  .meta({ id: "TopicSourceIndexNode", title: "TopicSourceIndexNode" });
export type TopicSourceIndexNode = z.infer<typeof TopicSourceIndexNodeSchema>;

/** Immutable hybrid-retrieval input derived only from one accepted evidence artifact. */
export const TopicSourceIndexSchema = z
  .object({
    embeddingDimensions: z.int().positive(),
    embeddingModel: identifier(),
    embeddingRevision: identifier(),
    evidenceSha256: sha256(),
    format: z.literal("topic-source-index/2"),
    nodes: z.array(TopicSourceIndexNodeSchema).min(3),
    regionMaxCharacters: z.int().positive(),
    regionMaxSentences: z.int().positive(),
    rootNodeId: identifier(),
    sectionMaxRegions: z.int().positive(),
    sentences: z.array(TopicSourceIndexSentenceSchema).min(1),
    sourceId: z.uuid(),
    transcriptId: z.uuid(),
    transcriptRevision: z.int().positive(),
  })
  .strict()
  .meta({ id: "TopicSourceIndex", title: "TopicSourceIndex" });
export type TopicSourceIndex = z.infer<typeof TopicSourceIndexSchema>;

/** Compact hierarchy metadata returned by browse and search tools. */
export const TopicSourceNodeHitSchema = z
  .object({
    childCount: z.int().nonnegative(),
    endMs: z.int().nonnegative(),
    firstSentenceId: identifier(),
    id: identifier(),
    keywords: z.array(z.string()),
    kind: z.enum(["section", "region"]),
    lastSentenceId: identifier(),
    parentId: identifier(),
    preview: z.string(),
    score: z.number().finite().nullable(),
    sentenceCount: z.int().positive(),
    startMs: z.int().nonnegative(),
  })
  .strict()
  .meta({ id: "TopicSourceNodeHit", title: "TopicSourceNodeHit" });
export type TopicSourceNodeHit = z.infer<typeof TopicSourceNodeHitSchema>;

export const TopicSourceBrowsePageSchema = z
  .object({
    complete: z.boolean(),
    indexSha256: sha256(),
    nextCursor: z.int().nonnegative().nullable(),
    nodes: z.array(TopicSourceNodeHitSchema),
    parentId: identifier(),
  })
  .strict()
  .meta({ id: "TopicSourceBrowsePage", title: "TopicSourceBrowsePage" });
export type TopicSourceBrowsePage = z.infer<typeof TopicSourceBrowsePageSchema>;

export const TopicSourceSearchPageSchema = z
  .object({
    complete: z.boolean(),
    indexSha256: sha256(),
    nextCursor: z.int().nonnegative().nullable(),
    query: z.string().min(1),
    regions: z.array(TopicSourceNodeHitSchema),
  })
  .strict()
  .meta({ id: "TopicSourceSearchPage", title: "TopicSourceSearchPage" });
export type TopicSourceSearchPage = z.infer<typeof TopicSourceSearchPageSchema>;

export const TopicSourceSentenceFragmentSchema = z
  .object({
    endCharacter: z.int().positive(),
    id: identifier(),
    sentenceId: identifier(),
    startCharacter: z.int().nonnegative(),
    text: z.string(),
    totalCharacters: z.int().positive(),
  })
  .strict()
  .meta({
    id: "TopicSourceSentenceFragment",
    title: "TopicSourceSentenceFragment",
  });
export type TopicSourceSentenceFragment = z.infer<
  typeof TopicSourceSentenceFragmentSchema
>;

export const TopicSourceReadPageSchema = z
  .object({
    complete: z.boolean(),
    fragments: z.array(TopicSourceSentenceFragmentSchema).default([]),
    indexSha256: sha256(),
    nextCharacterOffset: z.int().nonnegative().nullable().default(null),
    nextSentenceId: identifier().nullable(),
    sentences: z.array(TopicSourceIndexSentenceSchema),
  })
  .strict()
  .meta({ id: "TopicSourceReadPage", title: "TopicSourceReadPage" });
export type TopicSourceReadPage = z.infer<typeof TopicSourceReadPageSchema>;

/** One source-index leaf as it intersects a selected candidate extent. */
export const TopicCandidateRegionHitSchema = z
  .object({
    endMs: z.int().nonnegative(),
    firstSentenceId: identifier(),
    id: identifier(),
    keywords: z.array(z.string()),
    lastSentenceId: identifier(),
    parentId: identifier(),
    preview: z.string(),
    relation: z.enum([
      "same_extent",
      "inside_candidate",
      "covers_candidate",
      "opening_overlap",
      "closing_overlap",
    ]),
    selectedFirstSentenceId: identifier(),
    selectedLastSentenceId: identifier(),
    sentenceCount: z.int().positive(),
    startMs: z.int().nonnegative(),
  })
  .strict()
  .meta({ id: "TopicCandidateRegionHit", title: "TopicCandidateRegionHit" });
export type TopicCandidateRegionHit = z.infer<
  typeof TopicCandidateRegionHitSchema
>;

/** Bounded internal source map for one candidate from one immutable selection. */
export const TopicCandidateInspectionPageSchema = z
  .object({
    candidateId: identifier(),
    complete: z.boolean(),
    indexSha256: sha256(),
    nextCursor: z.int().nonnegative().nullable(),
    regions: z.array(TopicCandidateRegionHitSchema),
    selectionSha256: sha256(),
  })
  .strict()
  .meta({
    id: "TopicCandidateInspectionPage",
    title: "TopicCandidateInspectionPage",
  });
export type TopicCandidateInspectionPage = z.infer<
  typeof TopicCandidateInspectionPageSchema
>;

/** One already-measured event; it is sensor data rather than playback observation. */
export const TopicMediaEvidenceEventSchema = z
  .object({
    alignedWordCount: z.int().nonnegative().nullable(),
    boundaryKind: z
      .enum(["edge", "sentence", "turn", "pause", "shot"])
      .nullable(),
    clearanceMs: z.int().nonnegative().nullable(),
    endMs: z.int().nonnegative().nullable(),
    id: identifier(),
    interpolatedWordCount: z.int().nonnegative().nullable(),
    kind: z.enum(["sentence", "boundary", "pause", "shot", "speech_interval"]),
    minimumConfidence: z.number().min(0).max(1).nullable(),
    reasons: z.array(z.string()),
    relatedIds: z.array(identifier()),
    requiresReview: z.boolean().nullable(),
    score: z.number().finite().nullable(),
    sentenceId: identifier().nullable(),
    timeMs: z.int().nonnegative(),
    wordCount: z.int().nonnegative().nullable(),
  })
  .strict()
  .meta({ id: "TopicMediaEvidenceEvent", title: "TopicMediaEvidenceEvent" });
export type TopicMediaEvidenceEvent = z.infer<
  typeof TopicMediaEvidenceEventSchema
>;

/** Chronological, paginated sensor evidence for an exact transcript span. */
export const TopicMediaEvidencePageSchema = z
  .object({
    complete: z.boolean(),
    detector: z.string().nullable(),
    detectorRevision: z.string().nullable(),
    events: z.array(TopicMediaEvidenceEventSchema),
    evidenceSha256: sha256(),
    nextCursor: z.int().nonnegative().nullable(),
    sourceId: z.uuid(),
    span: TopicSentenceSpanSchema,
    speechCoverageStatus: z.enum(["unknown", "clear", "needs_review"]),
    warnings: z.array(z.string()),
  })
  .strict()
  .meta({ id: "TopicMediaEvidencePage", title: "TopicMediaEvidencePage" });
export type TopicMediaEvidencePage = z.infer<
  typeof TopicMediaEvidencePageSchema
>;

/** Audience information is allowed in cold review; episode answers are not. */
export const TopicEditorialRubricSchema = z
  .object({
    assumedDomainKnowledge: z.array(z.string()),
    audienceDescription: reason(),
    exclusions: z.array(z.string()),
    focus: z.string(),
    languagePolicy: reason(),
    originalInstructions: z.string(),
    version: z.literal(1),
    viewerGoals: z.array(z.string()),
  })
  .strict()
  .meta({ id: "TopicEditorialRubric", title: "TopicEditorialRubric" });
export type TopicEditorialRubric = z.infer<typeof TopicEditorialRubricSchema>;

/** An editorial hypothesis remains visible even before a suitable video exists. */
export const TopicOpportunitySchema = z
  .object({
    candidateIds: z.array(z.string()),
    completionSpans: spans(),
    coreSpans: spans().min(1),
    disposition: z.enum([
      "proposed",
      "not_useful_for_audience",
      "not_contiguously_extractable",
      "needs_evidence",
    ]),
    dispositionReason: reason(),
    id: identifier(),
    meaningChangingFollowups: spans(),
    requiredContextSpans: spans(),
    valueEvidenceSpans: spans().min(1),
    viewerPurpose: reason(),
  })
  .strict()
  .meta({ id: "TopicOpportunity", title: "TopicOpportunity" });
export type TopicOpportunity = z.infer<typeof TopicOpportunitySchema>;

export const TopicSelectionDraftSchema = z
  .object({
    opportunities: z.array(TopicOpportunitySchema),
    proposal: TopicProposalSchema,
  })
  .strict()
  .meta({ id: "TopicSelectionDraft", title: "TopicSelectionDraft" });
export type TopicSelectionDraft = z.infer<typeof TopicSelectionDraftSchema>;

/** One deterministic inventory ownership unit derived from a source-index section. */
export const TopicInventorySectionSchema = z
  .object({
    nextSectionId: identifier().nullable(),
    ordinal: z.int().nonnegative(),
    ownershipSpan: TopicSentenceSpanSchema,
    previousSectionId: identifier().nullable(),
    sectionId: identifier(),
  })
  .strict()
  .meta({ id: "TopicInventorySection", title: "TopicInventorySection" });
export type TopicInventorySection = z.infer<typeof TopicInventorySectionSchema>;

/** Complete ordered work plan for bounded independent opportunity discovery. */
export const TopicOpportunityInventoryPlanSchema = z
  .object({
    format: z.literal("topic-opportunity-inventory-plan/1"),
    indexSha256: sha256(),
    sections: z.array(TopicInventorySectionSchema).min(1),
  })
  .strict()
  .meta({
    id: "TopicOpportunityInventoryPlan",
    title: "TopicOpportunityInventoryPlan",
  });
export type TopicOpportunityInventoryPlan = z.infer<
  typeof TopicOpportunityInventoryPlanSchema
>;

/** One admitted model answer whose opportunity anchors belong to one section. */
export const TopicOpportunityInventoryShardSchema = z
  .object({
    format: z.literal("topic-opportunity-inventory-shard/1"),
    indexSha256: sha256(),
    inventory: TopicSelectionDraftSchema,
    planSha256: sha256(),
    section: TopicInventorySectionSchema,
  })
  .strict()
  .meta({
    id: "TopicOpportunityInventoryShard",
    title: "TopicOpportunityInventoryShard",
  });
export type TopicOpportunityInventoryShard = z.infer<
  typeof TopicOpportunityInventoryShardSchema
>;

/** Deterministic whole-source inventory assembled only from a complete shard set. */
export const TopicOpportunityInventoryManifestSchema = z
  .object({
    complete: z.literal(true),
    format: z.literal("topic-opportunity-inventory-manifest/1"),
    indexSha256: sha256(),
    inventory: TopicSelectionDraftSchema,
    planSha256: sha256(),
    sectionIds: z.array(identifier()).min(1),
    shardArtifacts: z.array(HarnessArtifactRefSchema).min(1),
  })
  .strict()
  .meta({
    id: "TopicOpportunityInventoryManifest",
    title: "TopicOpportunityInventoryManifest",
  });
export type TopicOpportunityInventoryManifest = z.infer<
  typeof TopicOpportunityInventoryManifestSchema
>;

/** One bounded set of inventoried opportunities that must be packaged together. */
export const TopicAuthorWorkItemSchema = z
  .object({
    batchOrdinal: z.int().nonnegative(),
    opportunityIds: z.array(identifier()).min(1).max(12),
    ordinal: z.int().nonnegative(),
    sectionId: identifier(),
    workItemId: identifier(),
  })
  .strict()
  .meta({ id: "TopicAuthorWorkItem", title: "TopicAuthorWorkItem" });
export type TopicAuthorWorkItem = z.infer<typeof TopicAuthorWorkItemSchema>;

/** Complete deterministic work plan for bounded author packaging. */
export const TopicAuthorPackagingPlanSchema = z
  .object({
    format: z.literal("topic-author-packaging-plan/1"),
    indexSha256: sha256(),
    inventorySha256: sha256(),
    maxOpportunitiesPerWorkItem: z.literal(12),
    workItems: z.array(TopicAuthorWorkItemSchema),
  })
  .strict()
  .meta({
    id: "TopicAuthorPackagingPlan",
    title: "TopicAuthorPackagingPlan",
  });
export type TopicAuthorPackagingPlan = z.infer<
  typeof TopicAuthorPackagingPlanSchema
>;

/** One admitted author answer restricted to its exact opportunity work item. */
export const TopicAuthorPackagingShardSchema = z
  .object({
    draft: TopicSelectionDraftSchema,
    format: z.literal("topic-author-packaging-shard/1"),
    generatorFamily: reason(),
    indexSha256: sha256(),
    inventorySha256: sha256(),
    planSha256: sha256(),
    workItem: TopicAuthorWorkItemSchema,
  })
  .strict()
  .meta({
    id: "TopicAuthorPackagingShard",
    title: "TopicAuthorPackagingShard",
  });
export type TopicAuthorPackagingShard = z.infer<
  typeof TopicAuthorPackagingShardSchema
>;

/** Whole selection assembled only after every author work item has been admitted. */
export const TopicAuthorPackagingManifestSchema = z
  .object({
    complete: z.literal(true),
    format: z.literal("topic-author-packaging-manifest/1"),
    generatorFamilies: z.array(reason()),
    indexSha256: sha256(),
    inventorySha256: sha256(),
    planSha256: sha256(),
    selection: TopicSelectionDraftSchema,
    shardArtifacts: z.array(HarnessArtifactRefSchema),
    workItemIds: z.array(identifier()),
  })
  .strict()
  .meta({
    id: "TopicAuthorPackagingManifest",
    title: "TopicAuthorPackagingManifest",
  });
export type TopicAuthorPackagingManifest = z.infer<
  typeof TopicAuthorPackagingManifestSchema
>;

export const TopicValueReviewSchema = z
  .object({
    deliveredValue: TopicCriterionSchema,
    focusedDevelopment: TopicCriterionSchema,
    openingEffectiveness: TopicCriterionSchema,
    reconstructedPurpose: reason(),
    reconstructedTakeaway: reason(),
    viewerReasonToWatch: TopicCriterionSchema,
  })
  .strict()
  .meta({ id: "TopicValueReview", title: "TopicValueReview" });
export type TopicValueReview = z.infer<typeof TopicValueReviewSchema>;

export const TopicSelectionColdReviewSchema = TopicColdReviewSchema.extend({
  value: TopicValueReviewSchema,
})
  .strict()
  .meta({ id: "TopicSelectionColdReview", title: "TopicSelectionColdReview" });
export type TopicSelectionColdReview = z.infer<
  typeof TopicSelectionColdReviewSchema
>;

export const TopicSelectionFindingSchema = z
  .object({
    affectedCandidateIds: z.array(z.string()),
    evidenceSpans: spans().min(1),
    id: identifier(),
    kind: z.enum([
      "missing_setup",
      "missing_qualification",
      "unfinished_discussion",
      "unsupported_title",
      "weak_viewer_value",
      "unfocused_extent",
      "duplicate_core",
      "missed_opportunity",
      "transcript_uncertainty",
      "physical_boundary_constraint",
    ]),
    opportunityIds: z.array(z.string()),
    reason: reason(),
    severity: z.enum(["required", "preference", "unknown"]),
  })
  .strict()
  .meta({ id: "TopicSelectionFinding", title: "TopicSelectionFinding" });
export type TopicSelectionFinding = z.infer<typeof TopicSelectionFindingSchema>;

export const TopicOpportunityJudgmentSchema = z
  .object({
    candidateIds: z.array(z.string()),
    evidenceSpans: spans().min(1),
    opportunityId: identifier(),
    reason: reason(),
    status: z.enum([
      "represented",
      "missing_candidate",
      "duplicate_core",
      "not_useful_for_audience",
      "not_contiguously_extractable",
      "unresolved",
    ]),
  })
  .strict()
  .meta({ id: "TopicOpportunityJudgment", title: "TopicOpportunityJudgment" });
export type TopicOpportunityJudgment = z.infer<
  typeof TopicOpportunityJudgmentSchema
>;

export const TopicSelectionDecisionSchema = z
  .object({
    candidateId: identifier(),
    disposition: z.enum(["select", "decline", "unresolved"]),
    evidenceSpans: spans().min(1),
    reason: reason(),
  })
  .strict()
  .meta({ id: "TopicSelectionDecision", title: "TopicSelectionDecision" });
export type TopicSelectionDecision = z.infer<
  typeof TopicSelectionDecisionSchema
>;

/** A source reviewer must account for every exact overlap supplied by the program. */
export const TopicCandidateOverlapJudgmentSchema = z
  .object({
    candidateIds: z.array(z.string()).length(2),
    classification: z.enum([
      "necessary_shared_context",
      "misallocated_topic_extent",
      "duplicate_core",
      "unresolved",
    ]),
    overlapSpan: TopicSentenceSpanSchema,
    reason: reason(),
  })
  .strict()
  .meta({
    id: "TopicCandidateOverlapJudgment",
    title: "TopicCandidateOverlapJudgment",
  });
export type TopicCandidateOverlapJudgment = z.infer<
  typeof TopicCandidateOverlapJudgmentSchema
>;

/** A source reviewer fixes semantic ownership at each adjacent, non-overlapping handoff. */
export const TopicCandidateHandoffJudgmentSchema = z
  .object({
    candidateIds: z.array(z.string()).length(2),
    classification: z.enum([
      "clean_handoff",
      "misallocated_topic_extent",
      "unresolved",
    ]),
    leftContextSpan: TopicSentenceSpanSchema,
    reason: reason(),
    recommendedLeftLastSentenceId: z.string().nullable(),
    recommendedRightFirstSentenceId: z.string().nullable(),
    rightContextSpan: TopicSentenceSpanSchema,
  })
  .strict()
  .meta({
    id: "TopicCandidateHandoffJudgment",
    title: "TopicCandidateHandoffJudgment",
  });
export type TopicCandidateHandoffJudgment = z.infer<
  typeof TopicCandidateHandoffJudgmentSchema
>;

/** Original source access makes new opportunities admissible, not automatically true. */
export const TopicPortfolioReviewSchema = z
  .object({
    candidates: z.array(TopicSourceJudgmentSchema),
    findings: z.array(TopicSelectionFindingSchema),
    missingOpportunities: z.array(TopicOpportunitySchema),
    opportunities: z.array(TopicOpportunityJudgmentSchema),
    selection: z.array(TopicSelectionDecisionSchema),
    summary: reason(),
  })
  .strict()
  .meta({ id: "TopicPortfolioReview", title: "TopicPortfolioReview" });
export type TopicPortfolioReview = z.infer<typeof TopicPortfolioReviewSchema>;

/**
 * The source review the program runs: every material candidate overlap and every adjacent
 * handoff is an exact, executable decision, so omitting one is structurally invalid.
 */
export const TopicPortfolioReviewV4Schema = TopicPortfolioReviewSchema.extend({
  handoffs: z.array(TopicCandidateHandoffJudgmentSchema),
  overlaps: z.array(TopicCandidateOverlapJudgmentSchema),
})
  .strict()
  .meta({ id: "TopicPortfolioReviewV4", title: "TopicPortfolioReviewV4" });
export type TopicPortfolioReviewV4 = z.infer<
  typeof TopicPortfolioReviewV4Schema
>;

/** Exact overlap input assigned to one bounded source-review relationship call. */
export const TopicSourceReviewOverlapTaskSchema = z
  .object({
    candidateIds: z.array(identifier()).length(2),
    overlapSpan: TopicSentenceSpanSchema,
  })
  .strict()
  .meta({
    id: "TopicSourceReviewOverlapTask",
    title: "TopicSourceReviewOverlapTask",
  });
export type TopicSourceReviewOverlapTask = z.infer<
  typeof TopicSourceReviewOverlapTaskSchema
>;

/** Exact adjacent handoff input assigned to one bounded relationship call. */
export const TopicSourceReviewHandoffTaskSchema = z
  .object({
    candidateIds: z.array(identifier()).length(2),
    leftContextSpan: TopicSentenceSpanSchema,
    rightContextSpan: TopicSentenceSpanSchema,
  })
  .strict()
  .meta({
    id: "TopicSourceReviewHandoffTask",
    title: "TopicSourceReviewHandoffTask",
  });
export type TopicSourceReviewHandoffTask = z.infer<
  typeof TopicSourceReviewHandoffTaskSchema
>;

/** One finite local or pairwise unit of independent source review. */
export const TopicSourceReviewWorkItemSchema = z
  .object({
    batchOrdinal: z.int().nonnegative(),
    candidateIds: z.array(identifier()).max(4),
    contextOpportunityIds: z.array(identifier()),
    discoverMissingOpportunities: z.boolean(),
    handoffs: z.array(TopicSourceReviewHandoffTaskSchema).max(2),
    inspectionCandidateIds: z.array(identifier()),
    kind: z.enum(["local", "omission", "overlap", "handoff"]),
    opportunityIds: z.array(identifier()).max(12),
    ordinal: z.int().nonnegative(),
    overlaps: z.array(TopicSourceReviewOverlapTaskSchema).max(2),
    sectionId: identifier(),
    sourceSpan: TopicSentenceSpanSchema.nullable(),
    workItemId: identifier(),
  })
  .strict()
  .meta({
    id: "TopicSourceReviewWorkItem",
    title: "TopicSourceReviewWorkItem",
  });
export type TopicSourceReviewWorkItem = z.infer<
  typeof TopicSourceReviewWorkItemSchema
>;

/** Complete immutable plan for bounded local, overlap and handoff review. */
export const TopicSourceReviewPlanSchema = z
  .object({
    format: z.literal("topic-source-review-plan/1"),
    indexSha256: sha256(),
    maxCandidatesPerLocalWorkItem: z.literal(4),
    maxOpportunitiesPerLocalWorkItem: z.literal(12),
    maxPairsPerRelationshipWorkItem: z.literal(2),
    selectionSha256: sha256(),
    workItems: z.array(TopicSourceReviewWorkItemSchema).min(1),
  })
  .strict()
  .meta({ id: "TopicSourceReviewPlan", title: "TopicSourceReviewPlan" });
export type TopicSourceReviewPlan = z.infer<typeof TopicSourceReviewPlanSchema>;

/** One independently admitted bounded source-review answer and its exact evidence trace. */
export const TopicSourceReviewShardSchema = z
  .object({
    format: z.literal("topic-source-review-shard/1"),
    indexSha256: sha256(),
    inspectionArtifact: HarnessArtifactRefSchema.nullable(),
    planSha256: sha256(),
    responseArtifact: HarnessArtifactRefSchema,
    review: TopicPortfolioReviewV4Schema,
    reviewerFamily: reason(),
    selectionSha256: sha256(),
    workItem: TopicSourceReviewWorkItemSchema,
  })
  .strict()
  .meta({ id: "TopicSourceReviewShard", title: "TopicSourceReviewShard" });
export type TopicSourceReviewShard = z.infer<
  typeof TopicSourceReviewShardSchema
>;

/** Whole portfolio review assembled only from every exact admitted review shard. */
export const TopicSourceReviewManifestSchema = z
  .object({
    complete: z.literal(true),
    format: z.literal("topic-source-review-manifest/1"),
    indexSha256: sha256(),
    inspectionArtifacts: z.array(HarnessArtifactRefSchema),
    planSha256: sha256(),
    responseArtifacts: z.array(HarnessArtifactRefSchema).min(1),
    review: TopicPortfolioReviewV4Schema,
    reviewerFamilies: z.array(reason()).min(1),
    selectionSha256: sha256(),
    shardArtifacts: z.array(HarnessArtifactRefSchema).min(1),
    workItemIds: z.array(identifier()).min(1),
  })
  .strict()
  .meta({
    id: "TopicSourceReviewManifest",
    title: "TopicSourceReviewManifest",
  });
export type TopicSourceReviewManifest = z.infer<
  typeof TopicSourceReviewManifestSchema
>;

/** V3 may replace both semantic boundaries in one accountable operation. */
export const TopicSelectionPatchOperationV3Schema = z
  .object({
    affectedCandidateIds: z.array(z.string()),
    findingIds: z.array(z.string()).min(1),
    id: identifier(),
    kind: z.enum([
      "extend_start",
      "extend_end",
      "replace_extent",
      "replace_candidate",
      "retitle",
      "merge",
      "split",
      "drop",
      "add_opportunity",
    ]),
    opportunities: z.array(TopicOpportunitySchema),
    reason: reason(),
    replacementCandidates: z.array(TopicCandidateSchema),
  })
  .strict()
  .meta({
    id: "TopicSelectionPatchOperationV3",
    title: "TopicSelectionPatchOperationV3",
  });
export type TopicSelectionPatchOperationV3 = z.infer<
  typeof TopicSelectionPatchOperationV3Schema
>;

export const TopicSelectionPatchV3Schema = z
  .object({
    baseSelectionSha256: sha256(),
    evidenceSha256: sha256(),
    operations: z.array(TopicSelectionPatchOperationV3Schema),
    rubricSha256: sha256(),
    summary: reason(),
  })
  .strict()
  .meta({ id: "TopicSelectionPatchV3", title: "TopicSelectionPatchV3" });
export type TopicSelectionPatchV3 = z.infer<typeof TopicSelectionPatchV3Schema>;

/** One connected set of findings whose candidate/opportunity writes cannot be split safely. */
export const TopicRepairWorkItemSchema = z
  .object({
    browseParentIds: z.array(identifier()).min(1).max(8),
    candidateIds: z.array(identifier()).max(8),
    findingIds: z.array(identifier()).min(1).max(12),
    opportunityIds: z.array(identifier()).max(24),
    ordinal: z.int().nonnegative(),
    workItemId: identifier(),
  })
  .strict()
  .meta({ id: "TopicRepairWorkItem", title: "TopicRepairWorkItem" });
export type TopicRepairWorkItem = z.infer<typeof TopicRepairWorkItemSchema>;

/** Immutable connected-component plan for one atomic repair revision. */
export const TopicRepairPlanSchema = z
  .object({
    assessmentSha256: sha256(),
    format: z.literal("topic-repair-plan/1"),
    indexSha256: sha256(),
    maxCandidatesPerWorkItem: z.literal(8),
    maxFindingsPerWorkItem: z.literal(12),
    maxOpportunitiesPerWorkItem: z.literal(24),
    selectionSha256: sha256(),
    workItems: z.array(TopicRepairWorkItemSchema).min(1),
  })
  .strict()
  .meta({ id: "TopicRepairPlan", title: "TopicRepairPlan" });
export type TopicRepairPlan = z.infer<typeof TopicRepairPlanSchema>;

/** One independently admitted component patch and its exact indexed-source trace. */
export const TopicRepairShardSchema = z
  .object({
    assessmentSha256: sha256(),
    authorFamily: reason(),
    format: z.literal("topic-repair-shard/1"),
    indexSha256: sha256(),
    inspectionArtifact: HarnessArtifactRefSchema,
    patch: TopicSelectionPatchV3Schema,
    planSha256: sha256(),
    responseArtifact: HarnessArtifactRefSchema,
    selectionSha256: sha256(),
    workItem: TopicRepairWorkItemSchema,
  })
  .strict()
  .meta({ id: "TopicRepairShard", title: "TopicRepairShard" });
export type TopicRepairShard = z.infer<typeof TopicRepairShardSchema>;

/** Complete disjoint shard set whose aggregate patch is safe to apply as one revision. */
export const TopicRepairManifestSchema = z
  .object({
    aggregatePatch: TopicSelectionPatchV3Schema,
    assessmentSha256: sha256(),
    authorFamilies: z.array(reason()).min(1),
    complete: z.literal(true),
    format: z.literal("topic-repair-manifest/1"),
    indexSha256: sha256(),
    inspectionArtifacts: z.array(HarnessArtifactRefSchema).min(1),
    planSha256: sha256(),
    responseArtifacts: z.array(HarnessArtifactRefSchema).min(1),
    selectionSha256: sha256(),
    shardArtifacts: z.array(HarnessArtifactRefSchema).min(1),
    workItemIds: z.array(identifier()).min(1),
  })
  .strict()
  .meta({ id: "TopicRepairManifest", title: "TopicRepairManifest" });
export type TopicRepairManifest = z.infer<typeof TopicRepairManifestSchema>;

/** Persisted identity is supplied by code, never trusted from a model's answer. */
export const TopicSelectionRecordSchema = z
  .object({
    draft: TopicSelectionDraftSchema,
    evidenceSha256: sha256(),
    format: z.literal("topic-selection/2"),
    origin: z.enum(["model", "human", "retained_diagnostic"]),
    parentSelectionSha256: sha256().nullable(),
    rubric: TopicEditorialRubricSchema,
    rubricSha256: sha256(),
    runId: z.uuid(),
  })
  .strict()
  .meta({ id: "TopicSelectionRecord", title: "TopicSelectionRecord" });
export type TopicSelectionRecord = z.infer<typeof TopicSelectionRecordSchema>;

export const TopicSelectionAssessmentSchema = z
  .object({
    coldReviews: z.array(TopicSelectionColdReviewSchema),
    evidenceSha256: sha256(),
    executionStatus: z.enum(["complete", "needs_review", "execution_limited"]),
    findings: z.array(TopicSelectionFindingSchema),
    format: z.literal("topic-selection-assessment/2"),
    portfolioReview: TopicPortfolioReviewV4Schema.nullable(),
    proposerFamily: reason(),
    reasons: z.array(z.string()),
    responseArtifacts: z.array(HarnessArtifactRefSchema),
    rubricSha256: sha256(),
    runId: z.uuid(),
    selectionSha256: sha256(),
    verifierFamily: z.string().nullable(),
  })
  .strict()
  .meta({ id: "TopicSelectionAssessment", title: "TopicSelectionAssessment" });
export type TopicSelectionAssessment = z.infer<
  typeof TopicSelectionAssessmentSchema
>;

export const TopicEditorialPatchOperationSchema = z
  .object({
    affectedCandidateIds: z.array(z.string()),
    kind: z.enum(["adjust_extent", "retitle", "add", "drop", "merge", "split"]),
    operationId: identifier(),
    replacementCandidates: z.array(TopicCandidateSchema),
  })
  .strict()
  .meta({
    id: "TopicEditorialPatchOperation",
    title: "TopicEditorialPatchOperation",
  });
export type TopicEditorialPatchOperation = z.infer<
  typeof TopicEditorialPatchOperationSchema
>;

export const TopicEditorialPatchInputSchema = z
  .object({
    action: z.literal("topic_edit"),
    baseEditSha256: sha256(),
    baseRevision: z.int().positive(),
    correctionActiveSeconds: z.number().finite().nonnegative().nullable(),
    correctionMeasurementMethod: z.string().min(1).nullable(),
    evidenceSha256: sha256(),
    mutationKey: z.uuid(),
    operations: z.array(TopicEditorialPatchOperationSchema).min(1),
    reason: reason(),
    runId: z.uuid(),
    scope: ScopeSchema,
    sourceId: z.uuid(),
    version: z.literal(1),
  })
  .strict()
  .meta({ id: "TopicEditorialPatchInput", title: "TopicEditorialPatchInput" });
export type TopicEditorialPatchInput = z.infer<
  typeof TopicEditorialPatchInputSchema
>;
