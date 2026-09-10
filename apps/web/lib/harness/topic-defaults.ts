import { z } from "zod";

export const TOPIC_POLICY = "standalone-topics/1";
export const DEFAULT_TOPIC_BRIEF_VERSION = "standalone-topics/1";

const DEFAULT_TOPIC_BRIEF =
  "Find worthwhile, complete discussions that can be published as independent topic videos on YouTube or Facebook. Let the source determine their number and length. Each video must orient a new viewer, develop a coherent purpose, and reach the speaker's actual conclusion, including uncertainty. Retain necessary questions, setup, corrections and caveats. Videos may reuse source context when needed to stand alone. Avoid redundant core ideas and do not manufacture a topic from housekeeping or an outro. Use truthful titles, preserve the original language and source meaning, and flag unresolved editorial or audio-edge uncertainty. Do not invent missing speech.";

export const TopicStartInstructionsSchema = z.object({
  brief: z.string().max(100_000).optional(),
  defaultBriefVersion: z.literal(DEFAULT_TOPIC_BRIEF_VERSION),
});

export function resolveTopicBrief(
  input: z.infer<typeof TopicStartInstructionsSchema>
): string {
  return input.brief?.trim() ? input.brief : DEFAULT_TOPIC_BRIEF;
}
