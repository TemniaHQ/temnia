import { z } from "zod";
import { ChapterEditSpecSchema } from "./harness.ts";

const identifier = () => z.string().min(1).max(256);
const explanation = () => z.string().min(1);
const SHA256_PATTERN = /^[a-fA-F0-9]{64}$/;
const sha256 = () => z.string().regex(SHA256_PATTERN);

/** Inclusive lexical extents; semantic membership is validated against evidence. */
export const TopicSentenceSpanSchema = z
  .object({
    firstSentenceId: identifier(),
    lastSentenceId: identifier(),
  })
  .strict()
  .meta({ id: "TopicSentenceSpan", title: "TopicSentenceSpan" });

export type TopicSentenceSpan = z.infer<typeof TopicSentenceSpanSchema>;

/** One independently useful, contiguous source discussion. Other videos may overlap. */
export const TopicCandidateSchema = z
  .object({
    completionSpans: z.array(TopicSentenceSpanSchema).min(1),
    coreSpans: z.array(TopicSentenceSpanSchema).min(1),
    firstSentenceId: identifier(),
    id: identifier(),
    lastSentenceId: identifier(),
    meaningChangingFollowups: z.array(TopicSentenceSpanSchema),
    purpose: explanation(),
    reason: explanation(),
    requiredContextSpans: z.array(TopicSentenceSpanSchema),
    title: explanation(),
  })
  .strict()
  .meta({ id: "TopicCandidate", title: "TopicCandidate" });

export type TopicCandidate = z.infer<typeof TopicCandidateSchema>;

/** Zero useful candidates is an explicit result, not a manufactured clip. */
export const TopicProposalSchema = z
  .object({
    candidates: z.array(TopicCandidateSchema),
    summary: explanation(),
    version: z.literal(1),
  })
  .strict()
  .refine(
    (proposal) =>
      new Set(proposal.candidates.map((candidate) => candidate.id)).size ===
      proposal.candidates.length,
    { error: "topic candidate ids must be unique" }
  )
  .meta({ id: "TopicProposal", title: "TopicProposal" });

export type TopicProposal = z.infer<typeof TopicProposalSchema>;

/** Reuses chapter execution for one keep plus unexported source context. */
export const TopicCompiledVideoSchema = z
  .object({
    candidate: TopicCandidateSchema,
    edit: ChapterEditSpecSchema,
    keptSectionId: identifier(),
  })
  .strict()
  .refine(
    (video) => {
      const kept = video.edit.sections.filter(
        (section) => section.kind === "keep"
      );
      return (
        kept.length === 1 &&
        kept[0]?.id === video.keptSectionId &&
        video.keptSectionId === video.candidate.id
      );
    },
    { error: "a topic execution must keep exactly its named candidate" }
  )
  .meta({ id: "TopicCompiledVideo", title: "TopicCompiledVideo" });

export type TopicCompiledVideo = z.infer<typeof TopicCompiledVideoSchema>;

/** Independent video plans, not one global non-overlapping publication partition. */
export const TopicEditSpecSchema = z
  .object({
    compilerVersion: explanation(),
    durationMs: z.int().min(0).max(Number.MAX_SAFE_INTEGER),
    evidenceArtifactId: z.uuid(),
    evidenceSha256: sha256(),
    sourceId: z.uuid(),
    summary: explanation(),
    version: z.literal(1),
    videos: z.array(TopicCompiledVideoSchema),
  })
  .strict()
  .superRefine((portfolio, ctx) => {
    const ids = portfolio.videos.map((video) => video.candidate.id);
    if (new Set(ids).size !== ids.length) {
      ctx.addIssue({
        code: "custom",
        message: "topic candidate ids must be unique",
      });
    }
    for (const [index, video] of portfolio.videos.entries()) {
      if (
        video.edit.sourceId !== portfolio.sourceId ||
        video.edit.durationMs !== portfolio.durationMs ||
        video.edit.evidenceArtifactId !== portfolio.evidenceArtifactId ||
        video.edit.evidenceSha256 !== portfolio.evidenceSha256
      ) {
        ctx.addIssue({
          code: "custom",
          message:
            "every topic execution must name the portfolio evidence and source",
          path: ["videos", index, "edit"],
        });
      }
    }
  })
  .meta({ id: "TopicEditSpec", title: "TopicEditSpec" });

export type TopicEditSpec = z.infer<typeof TopicEditSpecSchema>;
