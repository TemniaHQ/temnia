import { ChapterReviewInputSchema } from "@temnia/contracts";
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
