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

/** V3 makes omission of a material candidate-overlap decision structurally invalid. */
export const TopicPortfolioReviewV3Schema = TopicPortfolioReviewSchema.extend({
  overlaps: z.array(TopicCandidateOverlapJudgmentSchema),
})
  .strict()
  .meta({ id: "TopicPortfolioReviewV3", title: "TopicPortfolioReviewV3" });
export type TopicPortfolioReviewV3 = z.infer<
  typeof TopicPortfolioReviewV3Schema
>;

/** V4 makes every adjacent non-overlapping topic handoff an exact, executable decision. */
export const TopicPortfolioReviewV4Schema = TopicPortfolioReviewV3Schema.extend(
  {
    handoffs: z.array(TopicCandidateHandoffJudgmentSchema),
  }
)
  .strict()
  .meta({ id: "TopicPortfolioReviewV4", title: "TopicPortfolioReviewV4" });
export type TopicPortfolioReviewV4 = z.infer<
  typeof TopicPortfolioReviewV4Schema
>;

export const TopicSelectionPatchOperationSchema = z
  .object({
    affectedCandidateIds: z.array(z.string()),
    findingIds: z.array(z.string()).min(1),
    id: identifier(),
    kind: z.enum([
      "extend_start",
      "extend_end",
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
    id: "TopicSelectionPatchOperation",
    title: "TopicSelectionPatchOperation",
  });
export type TopicSelectionPatchOperation = z.infer<
  typeof TopicSelectionPatchOperationSchema
>;

export const TopicSelectionPatchSchema = z
  .object({
    baseSelectionSha256: sha256(),
    evidenceSha256: sha256(),
    operations: z.array(TopicSelectionPatchOperationSchema),
    rubricSha256: sha256(),
    summary: reason(),
  })
  .strict()
  .meta({ id: "TopicSelectionPatch", title: "TopicSelectionPatch" });
export type TopicSelectionPatch = z.infer<typeof TopicSelectionPatchSchema>;

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
    portfolioReview: z
      .union([
        TopicPortfolioReviewSchema,
        TopicPortfolioReviewV3Schema,
        TopicPortfolioReviewV4Schema,
      ])
      .nullable(),
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
