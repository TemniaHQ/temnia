import {
  ChapterReviewInputSchema,
  TopicCandidateSchema,
  TopicEditorialPatchInputSchema,
  TopicEditorialPatchOperationSchema,
} from "@temnia/contracts";
import { z } from "zod";
import { TopicStartInstructionsSchema } from "./topic-defaults";

export const TopicStartIntentSchema = TopicStartInstructionsSchema.extend({
  requestKey: z.uuid(),
  runId: z.uuid(),
  sourceId: z.uuid(),
});
export type TopicStartIntent = z.infer<typeof TopicStartIntentSchema>;

export const TopicReviewIntentSchema = z
  .object(ChapterReviewInputSchema.shape)
  .omit({
    scope: true,
  })
  .refine(
    (command) =>
      ["accept", "reject", "cancel"].includes(command.action) &&
      command.boundaryId === null &&
      command.otherSectionId === null &&
      command.budgetMicros === null &&
      command.targetRevision === null &&
      command.targetTimeMs === null &&
      (command.action === "cancel"
        ? command.sectionId === null
        : command.sectionId !== null)
  );
export type TopicReviewIntent = z.infer<typeof TopicReviewIntentSchema>;

export const TopicEditorialPatchIntentSchema =
  TopicEditorialPatchInputSchema.omit({ scope: true });
export type TopicEditorialPatchIntent = z.infer<
  typeof TopicEditorialPatchIntentSchema
>;

// Drafts are deliberately incomplete; submitted commands use the strict contract above.
const DraftSpanSchema = z.object({
  firstSentenceId: z.string(),
  lastSentenceId: z.string(),
});
export const TopicEditorialDraftSchema = z.object({
  baseRevision: z.number().int().positive(),
  drafts: z.array(
    TopicCandidateSchema.extend({
      completionSpans: z.array(DraftSpanSchema),
      coreSpans: z.array(DraftSpanSchema),
      firstSentenceId: z.string(),
      lastSentenceId: z.string(),
      meaningChangingFollowups: z.array(DraftSpanSchema),
      purpose: z.string(),
      reason: z.string(),
      requiredContextSpans: z.array(DraftSpanSchema),
      title: z.string(),
    })
  ),
  kind: TopicEditorialPatchOperationSchema.shape.kind,
  parents: z.array(z.string()),
  reason: z.string(),
});

export function restoreTopicIntent<T>(
  raw: string | null,
  schema: z.ZodType<T>
): T | null {
  if (!raw) {
    return null;
  }
  try {
    const result = schema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}
