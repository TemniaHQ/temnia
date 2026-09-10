import { z } from "zod";

// Server-owned policy: the client receives its version, never a second copy of
// the instructions. Keep published versions unchanged for pending requests.
export const DEFAULT_CHAPTER_BRIEF_VERSION = "topic-chapters/1";

const DEFAULT_BRIEFS = {
  [DEFAULT_CHAPTER_BRIEF_VERSION]:
    "Divide the complete recording into coherent chapters at meaningful topic changes. Let the content determine chapter count and length. Preserve explanations, context, questions and answers, and complete spoken thoughts; do not cut words or sentences. Keep all source content unless a clearly justified drop is needed; make each drop explicit with its reason. If the recording starts or ends mid-thought, do not invent missing context or present the fragment as complete: flag it for review, or explicitly drop only the incomplete fragment when doing so preserves the surrounding meaning. Give each kept chapter a concise, informative title.",
} as const;

export const ChapterStartInstructionsSchema = z.object({
  brief: z.string().max(100_000).optional(),
  defaultBriefVersion: z.enum([DEFAULT_CHAPTER_BRIEF_VERSION]).optional(),
});

export function resolveChapterBrief(
  input: z.infer<typeof ChapterStartInstructionsSchema>
): string {
  // Explicit unversioned strings belong to the original start contract,
  // including empty strings retained by an older browser after a lost ACK.
  if (
    input.brief !== undefined &&
    (input.defaultBriefVersion === undefined || input.brief.trim() !== "")
  ) {
    return input.brief;
  }
  return DEFAULT_BRIEFS[
    input.defaultBriefVersion ?? DEFAULT_CHAPTER_BRIEF_VERSION
  ];
}
