import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { technicalEligibility } from "@/lib/harness/checks";
import {
  clearSessionIntent,
  clearSessionUuid,
  readSessionIntent,
  stableSessionUuid,
  writeSessionIntent,
} from "@/lib/harness/client";
import { harnessSettings } from "@/lib/harness/config";
import { parseDollarMicros } from "@/lib/harness/money";
import { artifactIdsForRevisionPointers } from "@/lib/harness/queries";

describe("run budget money", () => {
  it("parses exact decimal millionths and enforces the server cap", () => {
    expect(parseDollarMicros("12.345678", 20_000_000)).toBe(12_345_678);
    expect(parseDollarMicros("0.000001", 20_000_000)).toBe(1);
    expect(parseDollarMicros("0.0000001", 20_000_000)).toBeNull();
    expect(parseDollarMicros("20.000001", 20_000_000)).toBeNull();
    expect(parseDollarMicros("0", 20_000_000)).toBeNull();
  });
});

describe("topic server configuration", () => {
  const configured = {
    HARNESS_ALLOW_RECORDED: "0",
    HARNESS_BACKEND: "gateway",
    HARNESS_ENABLED: "1",
    HARNESS_MAX_RUN_BUDGET_MICROS: "20000000",
    HARNESS_ROUTE_SNAPSHOT_ID: "snapshot-v1",
  };

  it("returns bounded defaults without exposing route credentials", () => {
    const result = harnessSettings(configured);
    expect(result).toMatchObject({
      available: true,
      settings: {
        config: {
          maxDispatches: null,
          maxRepairs: 3,
          routeSnapshotId: "snapshot-v1",
        },
        maxRunBudgetMicros: 20_000_000,
        synthetic: false,
      },
    });
  });

  it("refuses an unapproved recorded backend in every environment", () => {
    expect(
      harnessSettings({ ...configured, HARNESS_BACKEND: "recorded" })
    ).toEqual({
      available: false,
      message: "The recorded topic backend is disabled on this server.",
    });
  });

  it("reports missing configuration as unavailable in the topic lane's words", () => {
    expect(harnessSettings({})).toEqual({
      available: false,
      message: "Topic videos are not enabled on this server.",
    });
    expect(harnessSettings({ HARNESS_ENABLED: "1" })).toEqual({
      available: false,
      message:
        "Topic videos are unavailable because the server configuration is incomplete.",
    });
  });

  it("reads the committed deployment file and ignores stale environment entries", () => {
    const staging = resolve(
      import.meta.dirname,
      "../../pipeline/harness/staging.json"
    );
    const result = harnessSettings({
      HARNESS_CONFIG_PATH: staging,
      HARNESS_ENABLED: "0",
      HARNESS_MAX_OUTPUT_TOKENS: "8192",
      HARNESS_ROUTE_SNAPSHOT_ID: "stale",
    });
    expect(result).toMatchObject({
      available: true,
      settings: {
        config: {
          backend: "gateway",
          maxDispatches: null,
          maxOutputTokens: 65_536,
          routeSnapshotId:
            "0a40fd581f1f966cfb05842d171192331b224a8eda59fd264f64d2246e7262b2",
        },
        defaultRunBudgetMicros: 20_000_000,
        maxRunBudgetMicros: 100_000_000,
        synthetic: false,
      },
    });
    if (result.available) {
      expect(result.settings.routes.propose.map((route) => route.id)).toEqual([
        "anthropic-claude-opus-5-high",
        "openai-gpt-5.6-sol-high",
      ]);
      expect(result.settings.routes.verify[0]?.family).toBe("openai");
      expect(result.settings.routes.propose[0]?.label).toBe(
        "claude-opus-5 via anthropic"
      );
    }
  });

  it("refuses a deployment file that is disabled, recorded without approval, or invalid", () => {
    const directory = mkdtempSync(join(tmpdir(), "harness-config-"));
    const write = (name: string, body: unknown) => {
      const path = join(directory, name);
      writeFileSync(path, JSON.stringify(body));
      return path;
    };
    const base = {
      allowRecorded: false,
      backend: "gateway",
      enabled: true,
      format: "harness-config/1",
      gateway: "openrouter",
      limits: {
        evidenceWindowSentences: 80,
        maxDispatches: 64,
        maxOutputTokens: 65_536,
        maxRenderConcurrency: 2,
        maxRepairs: 3,
        maxRunBudgetMicros: 20_000_000,
      },
      recordedFixturePath: null,
      render: { backend: "local", encoder: "libx264" },
      routeSnapshot: { id: "a".repeat(64), path: "routes.json" },
      topicShotDetector: "scdet",
    };
    expect(
      harnessSettings({
        HARNESS_CONFIG_PATH: write("disabled.json", {
          ...base,
          enabled: false,
        }),
      })
    ).toEqual({
      available: false,
      message: "Topic videos are not enabled on this server.",
    });
    expect(
      harnessSettings({
        HARNESS_CONFIG_PATH: write("recorded.json", {
          ...base,
          backend: "recorded",
        }),
      })
    ).toEqual({
      available: false,
      message: "The recorded topic backend is disabled on this server.",
    });
    expect(
      harnessSettings({
        HARNESS_CONFIG_PATH: write("invalid.json", { ...base, limits: {} }),
      })
    ).toEqual({
      available: false,
      message:
        "Topic videos are unavailable because the server configuration file is invalid.",
    });
  });

  it("matches the worker's published shared fixture", () => {
    expect(
      harnessSettings({
        HARNESS_ALLOW_RECORDED: "1",
        HARNESS_BACKEND: "recorded",
        HARNESS_ENABLED: "1",
        HARNESS_ROUTE_SNAPSHOT_ID: "synthetic-v1",
      })
    ).toMatchObject({
      available: true,
      settings: { maxRunBudgetMicros: 10_000_000, synthetic: true },
    });
  });
});

describe("uncertain command identity", () => {
  const values = new Map<string, string>();

  afterEach(() => {
    vi.unstubAllGlobals();
    values.clear();
  });

  it("reuses one UUID until a deliberate request clears it", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    });
    const first = stableSessionUuid("chapter:test");
    expect(stableSessionUuid("chapter:test")).toBe(first);
    clearSessionUuid("chapter:test");
    expect(stableSessionUuid("chapter:test")).not.toBe(first);
  });

  it("persists the complete frozen intent across a reload", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    });
    const intent = { brief: "Frozen", budgetDollars: "1.00", runId: "run" };
    writeSessionIntent("pending", intent);
    expect(readSessionIntent("pending")).toEqual(intent);
    clearSessionIntent("pending");
    expect(readSessionIntent("pending")).toBeNull();
  });
});

describe("technical acceptance eligibility", () => {
  const check = (name: string, status: "pass" | "warn" | "fail" = "pass") => ({
    expected: 1,
    measured: 1,
    message: name,
    name,
    sectionId: "chapter-1",
    status,
  });

  it("requires the measured base set and allows visible warnings", () => {
    const result = technicalEligibility({
      editorialReasons: [],
      editorialStatus: "not_run",
      editSha256: "a".repeat(64),
      technicalChecks: [
        check("full_decode"),
        check("duration", "warn"),
        check("video_presence", "pass"),
        check("audio_presence", "pass"),
        check("video_codec_h264"),
        check("video_width"),
        check("video_height"),
        check("rotation"),
        check("sample_aspect_ratio"),
        check("video_start_offset"),
        check("video_end"),
        check("audio_codec_aac"),
        check("audio_layout"),
        check("audio_channels"),
        check("audio_start_offset"),
        check("audio_end"),
        check("caption_bounds"),
      ],
      verifierFamily: null,
      version: 1,
    });
    expect(result.eligible).toBe(true);
    expect(result.warnings).toEqual(["duration: duration"]);
  });

  it("blocks an empty or incomplete measured set", () => {
    const base = {
      editorialReasons: [],
      editorialStatus: "not_run" as const,
      editSha256: "a".repeat(64),
      verifierFamily: null,
      version: 1 as const,
    };
    expect(
      technicalEligibility({ ...base, technicalChecks: [] }).eligible
    ).toBe(false);
    expect(
      technicalEligibility({
        ...base,
        technicalChecks: [check("full_decode")],
      }).eligible
    ).toBe(false);
  });

  it("blocks a duplicated required measurement", () => {
    const required = [
      "full_decode",
      "duration",
      "video_presence",
      "audio_presence",
      "caption_bounds",
    ];
    expect(
      technicalEligibility({
        editorialReasons: [],
        editorialStatus: "not_run",
        editSha256: "a".repeat(64),
        technicalChecks: [
          ...required.map((name) => check(name)),
          check("duration"),
        ],
        verifierFamily: null,
        version: 1,
      }).eligible
    ).toBe(false);
  });
});

describe("topic revision pointers", () => {
  it("keeps distinct current and accepted edit artifacts visible", () => {
    expect(
      artifactIdsForRevisionPointers(
        [
          { artifactId: "accepted", revision: 2 },
          { artifactId: "current", revision: 4 },
        ],
        4,
        2
      )
    ).toEqual({ accepted: "accepted", current: "current" });
  });
});
