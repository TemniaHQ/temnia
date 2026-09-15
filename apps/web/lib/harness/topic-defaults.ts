import { z } from "zod";

/** The one program the button starts; run rows carry it as `editorialPolicy`. */
export const TOPIC_POLICY = "standalone-topics/8";

/** A seat preference names a route ID from the frozen snapshot's pool for that seat. */
export const TopicRoutePreferencesSchema = z
  .object({
    author: z.string().min(1).max(256).optional(),
    verifier: z.string().min(1).max(256).optional(),
  })
  .strict();

/**
 * Instructions, allowance and model choices are the whole request. An absent brief is
 * not a web default: the worker applies the single editorial brief and freezes it into
 * the run, so the web never hashes a brief string of its own into a prompt. An absent
 * allowance takes the server default; an absent seat preference keeps the snapshot's
 * own pool order.
 */
export const TopicStartInstructionsSchema = z.object({
  brief: z.string().max(100_000).optional(),
  budgetMicros: z.number().int().positive().optional(),
  routes: TopicRoutePreferencesSchema.optional(),
});

export type TopicRoutePreferences = z.infer<typeof TopicRoutePreferencesSchema>;
