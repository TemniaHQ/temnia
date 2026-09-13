import { z } from "zod";

/**
 * Only `standalone-topics/3` is ever started. The two earlier literals stay
 * because run rows and Temporal histories still carry them: every reader (run
 * lists, review, editorial corrections, exports) has to recognise them so old
 * runs remain visible and correctable.
 */
export const TOPIC_POLICY = "standalone-topics/1";
export const TOPIC_SELECTION_POLICY = "standalone-topics/2";
export const TOPIC_SELECTION_POLICY_V3 = "standalone-topics/3";
export const TOPIC_POLICIES = [
  TOPIC_POLICY,
  TOPIC_SELECTION_POLICY,
  TOPIC_SELECTION_POLICY_V3,
] as const;

export function isTopicPolicy(value: unknown): boolean {
  return TOPIC_POLICIES.some((policy) => policy === value);
}

/**
 * Instructions are the whole request. An absent brief is not a web default: the
 * worker applies the single editorial brief and freezes it into the run, so the
 * web never hashes a brief string of its own into a prompt.
 */
export const TopicStartInstructionsSchema = z.object({
  brief: z.string().max(100_000).optional(),
});
