import {
  type ChapterRunConfig,
  ChapterRunConfigSchema,
} from "@temnia/contracts";
import { z } from "zod";
import { DEFAULT_CHAPTER_BRIEF_VERSION } from "@/lib/harness/default-brief";

const HarnessEnvironmentSchema = z.object({
  HARNESS_ALLOW_RECORDED: z.enum(["0", "1"]).default("0"),
  HARNESS_BACKEND: z.enum(["recorded", "gateway"]).optional(),
  HARNESS_ENABLED: z.enum(["0", "1"]).default("0"),
  HARNESS_EVIDENCE_WINDOW_SENTENCES: z.coerce.number().int().default(80),
  HARNESS_MAX_DISPATCHES: z.coerce.number().int().default(32),
  HARNESS_MAX_OUTPUT_TOKENS: z.coerce.number().int().default(8192),
  HARNESS_MAX_RENDER_CONCURRENCY: z.coerce.number().int().default(2),
  HARNESS_MAX_REPAIRS: z.coerce.number().int().default(3),
  HARNESS_MAX_RUN_BUDGET_MICROS: z.coerce
    .number()
    .int()
    .positive()
    .default(10_000_000),
  HARNESS_ROUTE_SNAPSHOT_ID: z.string().min(1).max(256).optional(),
});

const ChapterCreationSchema = z.object({
  HARNESS_CHAPTERS_ENABLED: z.enum(["0", "1"]).default("1"),
});

/**
 * One set of server settings feeds two lanes, so an unavailable server has to
 * say which lane the reader is looking at. The words differ; nothing else does.
 */
export type HarnessFeature = "chapters" | "topics";

const UNAVAILABLE: Record<
  HarnessFeature,
  { disabled: string; incomplete: string; limits: string; recorded: string }
> = {
  chapters: {
    disabled: "Chapter editing is not enabled on this server.",
    incomplete:
      "Chapter editing is unavailable because its server configuration is incomplete.",
    limits: "Chapter editing is unavailable because its limits are invalid.",
    recorded: "The recorded chapter backend is disabled on this server.",
  },
  topics: {
    disabled: "Topic videos are not enabled on this server.",
    incomplete:
      "Topic videos are unavailable because the server configuration is incomplete.",
    limits:
      "Topic videos are unavailable because the server limits are invalid.",
    recorded: "The recorded topic backend is disabled on this server.",
  },
};

export const CHAPTER_CREATION_PAUSED_MESSAGE =
  "Chapter creation is paused on this server. Existing chapter runs remain reviewable.";

export interface HarnessSettings {
  config: ChapterRunConfig;
  defaultBriefVersion: string;
  maxRunBudgetMicros: number;
  synthetic: boolean;
}

export type HarnessAvailability =
  | { available: true; settings: HarnessSettings }
  | { available: false; message: string };

export function harnessSettings(
  feature: HarnessFeature = "chapters",
  environment: Record<string, string | undefined> = process.env
): HarnessAvailability {
  const words = UNAVAILABLE[feature];
  const parsed = HarnessEnvironmentSchema.safeParse(environment);
  if (!parsed.success || parsed.data.HARNESS_ENABLED !== "1") {
    return { available: false, message: words.disabled };
  }
  if (!(parsed.data.HARNESS_BACKEND && parsed.data.HARNESS_ROUTE_SNAPSHOT_ID)) {
    return { available: false, message: words.incomplete };
  }
  const synthetic = parsed.data.HARNESS_BACKEND === "recorded";
  if (synthetic && parsed.data.HARNESS_ALLOW_RECORDED !== "1") {
    return { available: false, message: words.recorded };
  }
  const config = ChapterRunConfigSchema.safeParse({
    backend: parsed.data.HARNESS_BACKEND,
    evidenceWindowSentences: parsed.data.HARNESS_EVIDENCE_WINDOW_SENTENCES,
    maxDispatches: parsed.data.HARNESS_MAX_DISPATCHES,
    maxOutputTokens: parsed.data.HARNESS_MAX_OUTPUT_TOKENS,
    maxRenderConcurrency: parsed.data.HARNESS_MAX_RENDER_CONCURRENCY,
    maxRepairs: parsed.data.HARNESS_MAX_REPAIRS,
    routeSnapshotId: parsed.data.HARNESS_ROUTE_SNAPSHOT_ID,
  });
  if (!config.success) {
    return { available: false, message: words.limits };
  }
  return {
    available: true,
    settings: {
      config: config.data,
      defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      maxRunBudgetMicros: parsed.data.HARNESS_MAX_RUN_BUDGET_MICROS,
      synthetic,
    },
  };
}

/**
 * The chapter lane is parked on staging while one worker serves one queue.
 * Only new runs stop; every existing chapter run stays readable and reviewable.
 * A value that is neither "0" nor "1" pauses creation rather than guessing,
 * and the panel says so in words.
 */
export function chapterCreationPaused(
  environment: Record<string, string | undefined> = process.env
): boolean {
  const parsed = ChapterCreationSchema.safeParse(environment);
  return !parsed.success || parsed.data.HARNESS_CHAPTERS_ENABLED === "0";
}
