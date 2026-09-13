import { z } from "zod";

/** The one program the button starts; run rows carry it as `editorialPolicy`. */
export const TOPIC_POLICY = "standalone-topics/3";

/**
 * Instructions are the whole request. An absent brief is not a web default: the
 * worker applies the single editorial brief and freezes it into the run, so the
 * web never hashes a brief string of its own into a prompt.
 */
export const TopicStartInstructionsSchema = z.object({
  brief: z.string().max(100_000).optional(),
});
