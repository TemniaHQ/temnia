import { readFileSync } from "node:fs";
import {
  type ChapterRunConfig,
  ChapterRunConfigSchema,
  HarnessConfigSchema,
} from "@temnia/contracts";
import { z } from "zod";

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

export interface HarnessSettings {
  config: ChapterRunConfig;
  maxRunBudgetMicros: number;
  synthetic: boolean;
}

export type HarnessAvailability =
  | { available: true; settings: HarnessSettings }
  | { available: false; message: string };

const NOT_ENABLED: HarnessAvailability = {
  available: false,
  message: "Topic videos are not enabled on this server.",
};
const RECORDED_DISABLED: HarnessAvailability = {
  available: false,
  message: "The recorded topic backend is disabled on this server.",
};
const INVALID_LIMITS: HarnessAvailability = {
  available: false,
  message:
    "Topic videos are unavailable because the server limits are invalid.",
};

function settingsFor(
  backend: "recorded" | "gateway",
  limits: Omit<ChapterRunConfig, "backend" | "routeSnapshotId"> & {
    maxRunBudgetMicros: number;
  },
  routeSnapshotId: string
): HarnessAvailability {
  const { maxRunBudgetMicros, ...bounded } = limits;
  const config = ChapterRunConfigSchema.safeParse({
    ...bounded,
    backend,
    routeSnapshotId,
  });
  if (!config.success) {
    return INVALID_LIMITS;
  }
  return {
    available: true,
    settings: {
      config: config.data,
      maxRunBudgetMicros,
      synthetic: backend === "recorded",
    },
  };
}

/** The committed deployment file is immutable per image; parse it once per process. */
const fileSettings = new Map<string, HarnessAvailability>();

function fromFile(path: string): HarnessAvailability {
  const cached = fileSettings.get(path);
  if (cached) {
    return cached;
  }
  const parsed = HarnessConfigSchema.safeParse(
    JSON.parse(readFileSync(path, "utf8"))
  );
  if (!parsed.success) {
    return {
      available: false,
      message:
        "Topic videos are unavailable because the server configuration file is invalid.",
    };
  }
  const file = parsed.data;
  let result: HarnessAvailability;
  if (!file.enabled) {
    result = NOT_ENABLED;
  } else if (file.backend === "recorded" && !file.allowRecorded) {
    result = RECORDED_DISABLED;
  } else {
    result = settingsFor(file.backend, file.limits, file.routeSnapshot.id);
  }
  fileSettings.set(path, result);
  return result;
}

/**
 * The one server-side gate for topic generation; the run config it returns must equal
 * the worker's. With `HARNESS_CONFIG_PATH` set (the deployed images bake it), the
 * committed file is the configuration and every other `HARNESS_*` entry is ignored, so
 * stale deployment environment cannot disagree with the worker.
 */
export function harnessSettings(
  environment: Record<string, string | undefined> = process.env
): HarnessAvailability {
  if (environment.HARNESS_CONFIG_PATH) {
    return fromFile(environment.HARNESS_CONFIG_PATH);
  }
  const parsed = HarnessEnvironmentSchema.safeParse(environment);
  if (!parsed.success || parsed.data.HARNESS_ENABLED !== "1") {
    return NOT_ENABLED;
  }
  if (!(parsed.data.HARNESS_BACKEND && parsed.data.HARNESS_ROUTE_SNAPSHOT_ID)) {
    return {
      available: false,
      message:
        "Topic videos are unavailable because the server configuration is incomplete.",
    };
  }
  if (
    parsed.data.HARNESS_BACKEND === "recorded" &&
    parsed.data.HARNESS_ALLOW_RECORDED !== "1"
  ) {
    return RECORDED_DISABLED;
  }
  return settingsFor(
    parsed.data.HARNESS_BACKEND,
    {
      evidenceWindowSentences: parsed.data.HARNESS_EVIDENCE_WINDOW_SENTENCES,
      maxDispatches: parsed.data.HARNESS_MAX_DISPATCHES,
      maxOutputTokens: parsed.data.HARNESS_MAX_OUTPUT_TOKENS,
      maxRenderConcurrency: parsed.data.HARNESS_MAX_RENDER_CONCURRENCY,
      maxRepairs: parsed.data.HARNESS_MAX_REPAIRS,
      maxRunBudgetMicros: parsed.data.HARNESS_MAX_RUN_BUDGET_MICROS,
    },
    parsed.data.HARNESS_ROUTE_SNAPSHOT_ID
  );
}
