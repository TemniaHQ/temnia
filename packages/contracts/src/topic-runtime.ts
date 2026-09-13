import { z } from "zod";
import { HarnessArtifactRefSchema } from "./harness.ts";
import { TopicSentenceSpanSchema } from "./topics.ts";

const identifier = () => z.string().min(1).max(256);
const SHA256 = /^[a-fA-F0-9]{64}$/;
const sha256 = () => z.string().regex(SHA256);

export const TopicCriterionSchema = z
  .object({
    evidenceSpans: z.array(TopicSentenceSpanSchema),
    reason: z.string().min(1),
    status: z.enum(["pass", "fail", "unknown"]),
  })
  .strict()
  .meta({ id: "TopicCriterion", title: "TopicCriterion" });
export type TopicCriterion = z.infer<typeof TopicCriterionSchema>;

/** This judge sees only the proposed clip and title, never the planner's rationale. */
export const TopicColdReviewSchema = z
  .object({
    candidateId: identifier(),
    coherentTopic: TopicCriterionSchema,
    completeDiscussion: TopicCriterionSchema,
    intelligibleBeginning: TopicCriterionSchema,
    titleFaithful: TopicCriterionSchema,
  })
  .strict()
  .meta({ id: "TopicColdReview", title: "TopicColdReview" });
export type TopicColdReview = z.infer<typeof TopicColdReviewSchema>;

export const TopicSourceJudgmentSchema = z
  .object({
    candidateId: identifier(),
    completeContext: TopicCriterionSchema,
    distinctPurpose: TopicCriterionSchema,
    faithfulMeaning: TopicCriterionSchema,
  })
  .strict()
  .meta({ id: "TopicSourceJudgment", title: "TopicSourceJudgment" });
export type TopicSourceJudgment = z.infer<typeof TopicSourceJudgmentSchema>;

/** Global source-context review catches corrections invisible in a cold clip. */
export const TopicSourceReviewSchema = z
  .object({
    candidates: z.array(TopicSourceJudgmentSchema),
    summary: z.string().min(1),
  })
  .strict()
  .meta({ id: "TopicSourceReview", title: "TopicSourceReview" });
export type TopicSourceReview = z.infer<typeof TopicSourceReviewSchema>;

/** Deterministic compiler evidence, kept separate from model judgments. */
export const TopicBoundaryIssueSchema = z
  .object({
    code: z.literal("no-safe-cut"),
    edge: z.enum(["opening", "ending", "execution"]),
    reason: z.string().min(1),
    supportingSentenceIds: z.array(identifier()).min(1),
  })
  .strict()
  .meta({ id: "TopicBoundaryIssue", title: "TopicBoundaryIssue" });
export type TopicBoundaryIssue = z.infer<typeof TopicBoundaryIssueSchema>;

export const TopicAssessmentCandidateSchema = z
  .object({
    candidateId: identifier(),
    coldReview: TopicColdReviewSchema.nullable(),
    physicalBoundaryIssues: z.array(TopicBoundaryIssueSchema).default([]),
    reasons: z.array(z.string()),
    sourceReview: TopicSourceJudgmentSchema.nullable(),
    status: z.enum(["passed", "needs_review", "rejected"]),
  })
  .strict()
  .meta({ id: "TopicAssessmentCandidate", title: "TopicAssessmentCandidate" });
export type TopicAssessmentCandidate = z.infer<
  typeof TopicAssessmentCandidateSchema
>;

export const TopicRenderedVideoSchema = z
  .object({
    candidateId: identifier(),
    descriptor: HarnessArtifactRefSchema,
    execution: HarnessArtifactRefSchema,
  })
  .strict()
  .meta({ id: "TopicRenderedVideo", title: "TopicRenderedVideo" });
export type TopicRenderedVideo = z.infer<typeof TopicRenderedVideoSchema>;

export const TopicRendersSchema = z
  .object({
    editSha256: sha256(),
    format: z.literal("topic-renders/1"),
    runId: z.uuid(),
    videos: z.array(TopicRenderedVideoSchema),
  })
  .strict()
  .meta({ id: "TopicRenders", title: "TopicRenders" });
export type TopicRenders = z.infer<typeof TopicRendersSchema>;

export const TopicExportSchema = z
  .object({
    editSha256: sha256(),
    format: z.literal("topic-export/1"),
    revision: z.int().positive(),
    runId: z.uuid(),
    videos: z.array(TopicRenderedVideoSchema),
  })
  .strict()
  .meta({ id: "TopicExport", title: "TopicExport" });
export type TopicExport = z.infer<typeof TopicExportSchema>;
