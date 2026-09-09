import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { verifiedArtifactJson } from "@/lib/harness/artifact";
import {
  appliedBudgetMatchesDraft,
  budgetInputFromMicros,
  type ChapterPanelMessage,
  chapterOutcomeUnknownMessage,
  chapterPlanningStoppedMessage,
  chapterWaitingMessage,
  clearMatchedStartMessage,
  newRunExposureMessage,
  summaryGroundingMessage,
} from "@/lib/harness/chapter-ui";
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

describe("chapter money", () => {
  it("parses exact decimal millionths and enforces the server cap", () => {
    expect(parseDollarMicros("12.345678", 20_000_000)).toBe(12_345_678);
    expect(parseDollarMicros("0.000001", 20_000_000)).toBe(1);
    expect(parseDollarMicros("0.0000001", 20_000_000)).toBeNull();
    expect(parseDollarMicros("20.000001", 20_000_000)).toBeNull();
    expect(parseDollarMicros("0", 20_000_000)).toBeNull();
  });

  it("renders exact integer micros for an editable existing-run budget", () => {
    expect(budgetInputFromMicros(3_000_000)).toBe("3.00");
    expect(budgetInputFromMicros(3_100_000)).toBe("3.10");
    expect(budgetInputFromMicros(999_257)).toBe("0.999257");
    expect(budgetInputFromMicros(1)).toBe("0.000001");
    expect(() => budgetInputFromMicros(Number.MAX_SAFE_INTEGER + 1)).toThrow(
      "budget micros must be a nonnegative safe integer"
    );
  });

  it("releases a dirty draft only for its exact durable applied command", () => {
    const applied = {
      commandState: "applied",
      currentDraft: "3.50",
      observedMicros: 3_500_000,
      runMatches: true,
      submittedDraft: "3.50",
      submittedMicros: 3_500_000,
    };
    expect(appliedBudgetMatchesDraft(applied)).toBe(true);
    expect(
      appliedBudgetMatchesDraft({ ...applied, commandState: "refused" })
    ).toBe(false);
    expect(
      appliedBudgetMatchesDraft({ ...applied, commandState: "pending" })
    ).toBe(false);
    expect(
      appliedBudgetMatchesDraft({ ...applied, currentDraft: "3.75" })
    ).toBe(false);
    expect(
      appliedBudgetMatchesDraft({ ...applied, observedMicros: 3_000_000 })
    ).toBe(false);
    expect(appliedBudgetMatchesDraft({ ...applied, runMatches: false })).toBe(
      false
    );
  });
});

describe("chapter server configuration", () => {
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
          maxDispatches: 32,
          maxRepairs: 1,
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
      message: "The recorded chapter backend is disabled on this server.",
    });
  });

  it("reports missing configuration as unavailable", () => {
    expect(harnessSettings({})).toEqual({
      available: false,
      message: "Chapter editing is not enabled on this server.",
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

describe("chapter revision pointers", () => {
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

describe("chapter panel status copy", () => {
  const startMessage: ChapterPanelMessage = {
    kind: "start",
    runId: "run-2",
    text: "Waiting for the durable run record.",
  };

  it.each(["immediate", "delayed", "recovered"])(
    "clears %s start feedback only when its exact durable run appears",
    () => {
      expect(clearMatchedStartMessage(startMessage, "run-2")).toBeNull();
    }
  );

  it("preserves an unmatched start and unrelated feedback", () => {
    expect(clearMatchedStartMessage(startMessage, "run-1")).toEqual(
      startMessage
    );
    expect(
      clearMatchedStartMessage(
        { kind: "command", text: "Review command could not be refreshed." },
        "run-2"
      )
    ).toEqual({
      kind: "command",
      text: "Review command could not be refreshed.",
    });
    expect(
      clearMatchedStartMessage(
        { kind: "refresh", text: "Chapter status could not be refreshed." },
        "run-2"
      )
    ).toEqual({
      kind: "refresh",
      text: "Chapter status could not be refreshed.",
    });
  });

  it("does not claim technical verification before an edit exists", () => {
    expect(
      chapterWaitingMessage({
        checkState: "loading",
        descriptorCheckState: "loading",
        hasSelectedEdit: false,
      })
    ).toBeNull();
  });

  it("distinguishes rendering from actual technical-check loading", () => {
    expect(
      chapterWaitingMessage({
        checkState: "loading",
        descriptorCheckState: "loading",
        hasSelectedEdit: true,
      })
    ).toBe(
      "Chapter renders are still being prepared. Technical checks follow each render."
    );
    expect(
      chapterWaitingMessage({
        checkState: "loading",
        descriptorCheckState: "loaded",
        hasSelectedEdit: true,
      })
    ).toBe(
      "Technical checks are still being verified. Acceptance and export are waiting."
    );
  });

  it("leaves invalid and terminal check states to the existing blocked copy", () => {
    expect(
      chapterWaitingMessage({
        checkState: "blocked",
        descriptorCheckState: "invalid",
        hasSelectedEdit: true,
      })
    ).toBeNull();
    expect(
      chapterWaitingMessage({
        checkState: "pass",
        descriptorCheckState: "loaded",
        hasSelectedEdit: true,
      })
    ).toBeNull();
  });

  it("reports source-excerpt grounding separately from technical checks", () => {
    expect(
      summaryGroundingMessage({
        fallbackQuoteCount: 1,
        fallbackUnitCount: 2,
      })
    ).toBe(
      "Used the original transcript for 2 summary passages after finding 1 mismatched source reference. Review these passages before accepting the chapters."
    );
    expect(
      summaryGroundingMessage({
        fallbackQuoteCount: 0,
        fallbackUnitCount: 0,
      })
    ).toBeNull();
  });

  it("explains a planning refusal that has no editable revision", () => {
    expect(chapterPlanningStoppedMessage("needs_review", 0)).toBe(
      "Planning stopped before an edit was produced. Review the reason and start a new run to try again."
    );
    expect(chapterPlanningStoppedMessage("needs_review", 1)).toBeNull();
    expect(chapterPlanningStoppedMessage("running", 0)).toBeNull();
  });

  it("explains unresolved provider exposure and a separately paid new run", () => {
    expect(chapterOutcomeUnknownMessage("outcome_unknown")).toBe(
      "The provider result is unconfirmed, so its possible charge stays reserved. Retry, Cancel, and Raise budget cannot resolve this run."
    );
    expect(chapterOutcomeUnknownMessage("failed")).toBeNull();
    expect(newRunExposureMessage("outcome_unknown")).toBe(
      "A new run sends new paid requests under a separate budget. The unresolved possible charge from the previous run remains."
    );
    expect(newRunExposureMessage("ready")).toBeNull();
  });
});

describe("immutable artifact cache", () => {
  it("downloads and hashes one artifact only once across poll objects", async () => {
    const body = JSON.stringify({ version: 1 });
    const bytes = new TextEncoder().encode(body);
    const fetcher = vi.fn(async () => new Response(bytes));
    vi.stubGlobal("fetch", fetcher);
    const artifact = {
      id: "artifact-1",
      kind: "checks" as const,
      metadata: {},
      sha256: createHash("sha256").update(bytes).digest("hex"),
      sizeBytes: bytes.byteLength,
      url: "/artifact-1",
    };
    const schema = z.object({ version: z.literal(1) });

    await expect(verifiedArtifactJson(artifact, schema)).resolves.toEqual({
      version: 1,
    });
    await expect(
      verifiedArtifactJson(
        { ...artifact, metadata: { freshPoll: true } },
        schema
      )
    ).resolves.toEqual({ version: 1 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects a declared artifact above the JSON cap before fetching it", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(
      verifiedArtifactJson(
        {
          id: "oversize",
          kind: "edit",
          metadata: {},
          sha256: "a".repeat(64),
          sizeBytes: 16 * 1024 * 1024 + 1,
          url: "/oversize",
        },
        z.unknown()
      )
    ).rejects.toThrow("too large");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("evicts the least recently used artifact after the entry budget", async () => {
    const body = JSON.stringify({ version: 1 });
    const bytes = new TextEncoder().encode(body);
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const fetcher = vi.fn(async () => new Response(bytes));
    vi.stubGlobal("fetch", fetcher);
    const schema = z.object({ version: z.literal(1) });
    const artifact = (index: number) => ({
      id: `lru-${index}`,
      kind: "checks" as const,
      metadata: {},
      sha256,
      sizeBytes: bytes.byteLength,
      url: `/lru-${index}`,
    });
    await Promise.all(
      Array.from({ length: 25 }, (_, index) =>
        verifiedArtifactJson(artifact(index), schema)
      )
    );
    await verifiedArtifactJson(artifact(0), schema);
    expect(fetcher).toHaveBeenCalledTimes(26);
  });
});
