import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import {
  type ChapterRunConfig,
  ChapterRunConfigSchema,
  HarnessConfigSchema,
} from "@temnia/contracts";
import { z } from "zod";

const HarnessEnvironmentSchema = z.object({
  HARNESS_ALLOW_RECORDED: z.enum(["0", "1"]).default("0"),
  HARNESS_BACKEND: z.enum(["recorded", "gateway"]).optional(),
  HARNESS_DEFAULT_RUN_BUDGET_MICROS: z.coerce
    .number()
    .int()
    .positive()
    .optional(),
  HARNESS_ENABLED: z.enum(["0", "1"]).default("0"),
  HARNESS_EVIDENCE_WINDOW_SENTENCES: z.coerce.number().int().default(80),
  HARNESS_MAX_DISPATCHES: z.coerce.number().int().positive().optional(),
  HARNESS_MAX_OUTPUT_TOKENS: z.coerce.number().int().default(8192),
  HARNESS_MAX_RENDER_CONCURRENCY: z.coerce.number().int().default(2),
  HARNESS_MAX_REPAIRS: z.coerce.number().int().default(3),
  HARNESS_MAX_RUN_BUDGET_MICROS: z.coerce
    .number()
    .int()
    .positive()
    .default(10_000_000),
  HARNESS_ROUTE_SNAPSHOT_ID: z.string().min(1).max(256).optional(),
  HARNESS_ROUTE_SNAPSHOT_PATH: z.string().min(1).optional(),
});

/** One selectable model for one seat, read from the frozen route snapshot. */
export interface RouteOption {
  family: string;
  id: string;
  label: string;
}

export interface HarnessSettings {
  config: ChapterRunConfig;
  /** The allowance the panel proposes; the user may raise it up to the maximum. */
  defaultRunBudgetMicros: number;
  maxRunBudgetMicros: number;
  /** Author (propose) and reviewer (verify) pools in the snapshot's own order. */
  routes: { propose: RouteOption[]; verify: RouteOption[] };
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
/** Twenty dollars when the configuration names no default; never above the maximum. */
const FALLBACK_DEFAULT_BUDGET_MICROS = 20_000_000;

const RouteSnapshotFileSchema = z.object({
  routes: z.array(
    z.object({
      family: z.string().min(1),
      gateway_model: z.string().min(1),
      id: z.string().min(1),
      provider: z.string().min(1),
    })
  ),
  seats: z.record(
    z.string(),
    z.object({ route_ids: z.array(z.string().min(1)) })
  ),
  snapshot_id: z.string().min(1),
});

/**
 * Read the seat pools of the frozen snapshot the worker also loads. A missing or
 * unreadable file leaves both pools empty, which the panel shows as "server default";
 * the run then follows the snapshot's own order, exactly as before.
 */
export function routeOptions(
  path: string | undefined,
  expectedSnapshotId: string
): { propose: RouteOption[]; verify: RouteOption[] } {
  const empty = { propose: [], verify: [] };
  if (!path) {
    return empty;
  }
  let parsed: z.infer<typeof RouteSnapshotFileSchema>;
  try {
    parsed = RouteSnapshotFileSchema.parse(
      JSON.parse(readFileSync(path, "utf8"))
    );
  } catch {
    return empty;
  }
  if (parsed.snapshot_id !== expectedSnapshotId) {
    return empty;
  }
  const byId = new Map(parsed.routes.map((route) => [route.id, route]));
  const pool = (seat: string): RouteOption[] =>
    (parsed.seats[seat]?.route_ids ?? [])
      .map((id) => byId.get(id))
      .filter((route) => route !== undefined)
      .map((route) => ({
        family: route.family,
        id: route.id,
        label: `${route.gateway_model} via ${route.provider}`,
      }));
  return { propose: pool("propose"), verify: pool("verify") };
}

function settingsFor(
  backend: "recorded" | "gateway",
  limits: Omit<ChapterRunConfig, "backend" | "routeSnapshotId"> & {
    defaultRunBudgetMicros?: number | undefined;
    maxRunBudgetMicros: number;
  },
  routeSnapshotId: string,
  routes: { propose: RouteOption[]; verify: RouteOption[] }
): HarnessAvailability {
  const { defaultRunBudgetMicros, maxRunBudgetMicros, ...bounded } = limits;
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
      defaultRunBudgetMicros: Math.min(
        maxRunBudgetMicros,
        defaultRunBudgetMicros ?? FALLBACK_DEFAULT_BUDGET_MICROS
      ),
      maxRunBudgetMicros,
      routes,
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
    const {
      maxInFlightPerRoute: _inFlight,
      minDispatchIntervalSeconds: _interval,
      ...runLimits
    } = file.limits;
    result = settingsFor(
      file.backend,
      runLimits,
      file.routeSnapshot.id,
      routeOptions(
        resolve(dirname(path), file.routeSnapshot.path),
        file.routeSnapshot.id
      )
    );
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
      defaultRunBudgetMicros: parsed.data.HARNESS_DEFAULT_RUN_BUDGET_MICROS,
      evidenceWindowSentences: parsed.data.HARNESS_EVIDENCE_WINDOW_SENTENCES,
      maxDispatches: parsed.data.HARNESS_MAX_DISPATCHES ?? null,
      maxOutputTokens: parsed.data.HARNESS_MAX_OUTPUT_TOKENS,
      maxRenderConcurrency: parsed.data.HARNESS_MAX_RENDER_CONCURRENCY,
      maxRepairs: parsed.data.HARNESS_MAX_REPAIRS,
      maxRunBudgetMicros: parsed.data.HARNESS_MAX_RUN_BUDGET_MICROS,
    },
    parsed.data.HARNESS_ROUTE_SNAPSHOT_ID,
    routeOptions(
      parsed.data.HARNESS_ROUTE_SNAPSHOT_PATH,
      parsed.data.HARNESS_ROUTE_SNAPSHOT_ID
    )
  );
}
