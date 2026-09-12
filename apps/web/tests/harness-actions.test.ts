import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getPendingChapterWorkflowStatus,
  reviewChapterCommand,
  startChapterRun,
} from "@/app/actions/chapters";
import { deleteSource } from "@/app/actions/sources";
import {
  DEFAULT_CHAPTER_BRIEF_VERSION,
  resolveChapterBrief,
} from "@/lib/harness/default-brief";

const SOURCE = "01992ffe-0a00-7000-8000-000000000001";
const RUN = "01992ffe-0a00-7000-8000-000000000004";
const SHA256 = /^[0-9a-f]{64}$/;
const scope = {
  organizationId: "01992ffe-0a00-7000-8000-000000000002",
  userId: "01992ffe-0a00-7000-8000-000000000003",
};
const mocks = vi.hoisted(() => ({
  abortMultipart: vi.fn(),
  deadline: vi.fn(
    <Result>(_deadline: number | Date, operation: () => Promise<Result>) =>
      operation()
  ),
  deletePrefix: vi.fn(),
  getClient: vi.fn(),
  provesPreWriterIngestFailure: vi.fn(),
  scoped: vi.fn(),
}));

vi.mock("@/lib/db", () => ({ scoped: mocks.scoped }));
vi.mock("@/lib/storage/prefix", () => ({ deletePrefix: mocks.deletePrefix }));
vi.mock("@/lib/uploads/server", () => ({
  abortMultipart: mocks.abortMultipart,
}));
vi.mock("@/lib/temporal/client", () => ({
  getTemporalClient: mocks.getClient,
}));
vi.mock("@/lib/temporal/ingest-deletion-proof", () => ({
  provesPreWriterIngestFailure: mocks.provesPreWriterIngestFailure,
}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));

function transaction(responses: unknown[][], events: string[]) {
  return {
    delete: () => ({
      where: () => {
        events.push("source-delete");
        return Promise.resolve([]);
      },
    }),
    execute: () => {
      events.push("source-lock");
      return Promise.resolve([]);
    },
    insert: () => ({ values: () => Promise.resolve([]) }),
    select: () => {
      const query = {
        from: () => query,
        leftJoin: () => query,
        limit: () => Promise.resolve(responses.shift() ?? []),
        orderBy: () => query,
        // biome-ignore lint/suspicious/noThenProperty: this intentionally models an awaited Drizzle query
        then: (resolve: (value: unknown[]) => unknown) =>
          Promise.resolve(responses.shift() ?? []).then(resolve),
        where: () => query,
      };
      return query;
    },
    update: () => ({
      set: () => ({
        where: () => {
          const result = Promise.resolve([]);
          return Object.assign(result, {
            returning: () => Promise.resolve([]),
          });
        },
      }),
    }),
  };
}

function temporalClient(workflow: Record<string, unknown>) {
  return {
    withDeadline: mocks.deadline,
    workflow,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.HARNESS_ALLOW_RECORDED = "0";
  process.env.HARNESS_BACKEND = "gateway";
  process.env.HARNESS_ENABLED = "1";
  process.env.HARNESS_MAX_RUN_BUDGET_MICROS = "2000000";
  process.env.HARNESS_ROUTE_SNAPSHOT_ID = "snapshot-v1";
  mocks.provesPreWriterIngestFailure.mockResolvedValue(false);
});

describe("source deletion fence", () => {
  it("locks first and refuses history before any storage or Temporal side effect", async () => {
    const events: string[] = [];
    const tx = transaction(
      [
        [
          {
            deletionRequestedAt: null,
            durationMs: null,
            projectId: "project",
            status: "ready",
            transcriptStatus: "ready",
            workflowId: null,
          },
        ],
        [{ id: RUN }],
      ],
      events
    );
    mocks.scoped.mockImplementation((fn) => fn(tx, scope));

    await expect(deleteSource(SOURCE)).resolves.toEqual({
      message: "This source has editing history and cannot be deleted.",
      ok: false,
    });
    expect(events).toEqual(["source-lock"]);
    expect(mocks.deletePrefix).not.toHaveBeenCalled();
    expect(mocks.abortMultipart).not.toHaveBeenCalled();
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("makes a fenced source lose the start race before Temporal dispatch", async () => {
    const tx = transaction(
      [
        [],
        [
          {
            deletionRequestedAt: new Date("2026-09-08T00:00:00Z"),
            sourceStatus: "ready",
            transcriptRevision: 1,
            transcriptStatus: "ready",
          },
        ],
      ],
      []
    );
    mocks.scoped.mockImplementation((fn) => fn(tx, scope));

    await expect(
      startChapterRun({
        brief: "Make complete chapters",
        budgetDollars: "1.00",
        requestKey: RUN,
        runId: RUN,
        sourceId: SOURCE,
      })
    ).resolves.toEqual({
      message: "This source is pending deletion.",
      ok: false,
    });
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("keeps the fence when a deterministic writer did not complete naturally", async () => {
    const tx = transaction(
      [
        [
          {
            deletionRequestedAt: null,
            durationMs: 24_000,
            projectId: "project",
            status: "ready",
            transcriptStatus: "ready",
            workflowId: null,
          },
        ],
        [],
        [],
        [],
      ],
      []
    );
    mocks.scoped.mockImplementation((fn) => fn(tx, scope));
    const cancel = vi.fn();
    const describeWorkflow = vi.fn(async () => ({
      status: { name: "FAILED" },
    }));
    mocks.getClient.mockResolvedValue(
      temporalClient({
        getHandle: () => ({ cancel, describe: describeWorkflow }),
      })
    );
    mocks.provesPreWriterIngestFailure.mockResolvedValue(true);

    await expect(deleteSource(SOURCE)).resolves.toEqual({
      message:
        "Media processing ended without proof that its external writers stopped. The source remains fenced until its outcome is reconciled.",
      ok: false,
    });
    expect(describeWorkflow).toHaveBeenCalledTimes(2);
    expect(cancel).not.toHaveBeenCalled();
    expect(mocks.provesPreWriterIngestFailure).not.toHaveBeenCalled();
    expect(mocks.deletePrefix).not.toHaveBeenCalled();
  });

  it("deletes after an exact known pre-writer ingest failure proof", async () => {
    const events: string[] = [];
    const tx = transaction(
      [
        [
          {
            deletionRequestedAt: null,
            durationMs: null,
            projectId: "project",
            status: "failed",
            transcriptStatus: null,
            workflowId: `ingest-${SOURCE}`,
          },
        ],
        [],
        [],
        [],
        [],
        [{ total: 0 }],
      ],
      events
    );
    mocks.scoped.mockImplementation((fn) => fn(tx, scope));
    const description = {
      historyLength: 23,
      historySize: 10_342,
      runId: RUN,
      status: { name: "FAILED" },
      type: "IngestWorkflow",
    };
    mocks.getClient.mockResolvedValue(
      temporalClient({
        getHandle: (workflowId: string) => ({
          describe: async () =>
            workflowId.startsWith("transcribe-")
              ? { status: { name: "COMPLETED" } }
              : description,
        }),
      })
    );
    mocks.provesPreWriterIngestFailure.mockResolvedValue(true);

    await expect(deleteSource(SOURCE)).resolves.toEqual({ ok: true });
    expect(mocks.provesPreWriterIngestFailure).toHaveBeenCalledWith(
      expect.anything(),
      `ingest-${SOURCE}`,
      description
    );
    expect(mocks.deletePrefix).toHaveBeenCalledWith(
      `org/${scope.organizationId}/source/${SOURCE}/`
    );
    expect(events).toContain("source-delete");
  });
});

describe("chapter action durable identity", () => {
  const readySource = {
    deletionRequestedAt: null,
    sourceStatus: "ready",
    transcriptRevision: 1,
    transcriptStatus: "ready",
  };
  const defaultBrief = resolveChapterBrief({
    defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
  });
  const startIdentity = {
    budgetDollars: "1.00",
    requestKey: RUN,
    runId: RUN,
    sourceId: SOURCE,
  };

  it.each([
    { expected: defaultBrief, instructions: {}, name: "omitted instructions" },
    {
      expected: defaultBrief,
      instructions: { defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION },
      name: "versioned default",
    },
    {
      expected: defaultBrief,
      instructions: {
        brief: "",
        defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      },
      name: "versioned empty instructions",
    },
    {
      expected: defaultBrief,
      instructions: {
        brief: " \n\t ",
        defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      },
      name: "versioned whitespace instructions",
    },
    {
      expected: "  Preserve every question.\n",
      instructions: {
        brief: "  Preserve every question.\n",
        defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      },
      name: "custom instruction bytes",
    },
    {
      expected: "",
      instructions: { brief: "" },
      name: "legacy empty instruction identity",
    },
    {
      expected: " \n ",
      instructions: { brief: " \n " },
      name: "legacy whitespace instruction identity",
    },
  ])(
    "freezes $name into the workflow and pending lookup",
    async ({ expected, instructions }) => {
      const transactions = [
        transaction([[], [readySource]], []),
        transaction([[{ id: SOURCE }]], []),
      ];
      mocks.scoped.mockImplementation((fn) => fn(transactions.shift(), scope));
      let memo: Record<string, unknown> = {};
      const describeWorkflow = async () => ({
        memo,
        status: { name: "RUNNING" },
      });
      const start = vi.fn((_workflow, options) => {
        ({ memo } = options);
        return Promise.resolve({ describe: describeWorkflow });
      });
      mocks.getClient.mockResolvedValue(
        temporalClient({
          getHandle: () => ({ describe: describeWorkflow }),
          start,
        })
      );
      const intent = { ...startIdentity, ...instructions };
      await expect(startChapterRun(intent)).resolves.toMatchObject({
        ok: false,
        pending: true,
        runId: RUN,
      });
      expect(start).toHaveBeenCalledExactlyOnceWith(
        "ChapterRunWorkflow",
        expect.objectContaining({
          args: [
            expect.objectContaining({
              brief: expected,
              budgetMicros: 1_000_000,
            }),
          ],
          workflowId: `chapter-${RUN}`,
        })
      );
      await expect(
        getPendingChapterWorkflowStatus({ intent, kind: "start" })
      ).resolves.toMatchObject({ state: "pending" });
      expect(start).toHaveBeenCalledTimes(1);
    }
  );

  it.each([
    {
      instructions: { defaultBriefVersion: "topic-chapters/unknown" },
      name: "unknown version",
    },
    {
      instructions: {
        brief: "Custom",
        defaultBriefVersion: "topic-chapters/unknown",
      },
      name: "unknown version with custom instructions",
    },
    {
      instructions: {
        brief: " ".repeat(100_001),
        defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      },
      name: "oversized whitespace instructions",
    },
    { instructions: { brief: null }, name: "null instructions" },
  ])("refuses $name before dispatch", async ({ instructions }) => {
    const intent = { ...startIdentity, ...instructions };
    await expect(startChapterRun(intent)).resolves.toEqual({
      message: "The chapter request is invalid.",
      ok: false,
    });
    await expect(
      getPendingChapterWorkflowStatus({ intent, kind: "start" })
    ).resolves.toMatchObject({ state: "terminal" });
    expect(mocks.scoped).not.toHaveBeenCalled();
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("reuses normalized default intent and refuses changed brief or budget", async () => {
    mocks.scoped.mockImplementation((fn) =>
      fn(transaction([[], [readySource]], []), scope)
    );
    let acceptedMemo: Record<string, unknown> | null = null;
    const start = vi.fn((_workflow, options) => {
      acceptedMemo ??= options.memo;
      return Promise.resolve({
        describe: async () => ({
          memo: acceptedMemo,
          status: { name: "RUNNING" },
        }),
      });
    });
    mocks.getClient.mockResolvedValue(temporalClient({ start }));
    const intent = {
      ...startIdentity,
      defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
    };
    await expect(startChapterRun(intent)).resolves.toMatchObject({
      pending: true,
    });
    await expect(
      startChapterRun({ ...intent, brief: " \n " })
    ).resolves.toMatchObject({ pending: true });
    expect(start.mock.calls[1]?.[1].memo).toEqual(
      start.mock.calls[0]?.[1].memo
    );
    await expect(
      startChapterRun({ ...intent, brief: "Changed instructions" })
    ).resolves.toMatchObject({ conflict: true, ok: false });
    await expect(
      startChapterRun({ ...intent, budgetDollars: "1.50" })
    ).resolves.toMatchObject({ conflict: true, ok: false });
  });

  it("returns a durable default run after normalization without redispatch", async () => {
    const existing = {
      brief: defaultBrief,
      budgetMicros: 1_500_000,
      config: {
        backend: "gateway",
        evidenceWindowSentences: 80,
        maxDispatches: 32,
        maxOutputTokens: 8192,
        maxRenderConcurrency: 2,
        maxRepairs: 3,
        routeSnapshotId: "snapshot-v1",
      },
      id: RUN,
      requestKey: RUN,
      routeSnapshot: { initialBudgetMicros: 1_000_000 },
    };
    mocks.scoped.mockImplementation((fn) =>
      fn(transaction([[existing]], []), scope)
    );
    await expect(
      startChapterRun({
        ...startIdentity,
        brief: "",
        defaultBriefVersion: DEFAULT_CHAPTER_BRIEF_VERSION,
      })
    ).resolves.toEqual({ ok: true, runId: RUN });
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("returns an exact existing start and refuses changed intent without Temporal", async () => {
    const existing = {
      brief: "Make complete chapters",
      budgetMicros: 1_000_000,
      config: {
        backend: "gateway",
        evidenceWindowSentences: 80,
        maxDispatches: 32,
        maxOutputTokens: 8192,
        maxRenderConcurrency: 2,
        maxRepairs: 3,
        routeSnapshotId: "snapshot-v1",
      },
      id: RUN,
      requestKey: RUN,
      routeSnapshot: { initialBudgetMicros: 1_000_000 },
    };
    mocks.scoped.mockImplementation((fn) =>
      fn(transaction([[existing]], []), scope)
    );
    const intent = {
      brief: existing.brief,
      budgetDollars: "1.00",
      requestKey: RUN,
      runId: RUN,
      sourceId: SOURCE,
    };
    await expect(startChapterRun(intent)).resolves.toEqual({
      ok: true,
      runId: RUN,
    });
    await expect(
      startChapterRun({ ...intent, brief: "Changed" })
    ).resolves.toEqual({
      message: "That chapter request ID already names a different request.",
      ok: false,
    });
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("returns an already applied exact review event without redispatch", async () => {
    const command = {
      action: "retry",
      baseRevision: 2,
      boundaryId: null,
      budgetMicros: null,
      mutationKey: RUN,
      otherSectionId: null,
      reason: "",
      runId: RUN,
      sectionId: null,
      sourceId: SOURCE,
      targetRevision: null,
      targetTimeMs: null,
    };
    mocks.scoped.mockImplementation((fn) =>
      fn(
        transaction(
          [
            [{ id: RUN }],
            [{ payload: { ...command, scope }, state: "applied" }],
          ],
          []
        ),
        scope
      )
    );
    await expect(reviewChapterCommand(command)).resolves.toEqual({
      ok: true,
      runId: RUN,
    });
    expect(mocks.getClient).not.toHaveBeenCalled();
  });

  it("refuses a pre-persistence start race when Temporal memo names another intent", async () => {
    const tx = transaction(
      [
        [],
        [
          {
            deletionRequestedAt: null,
            sourceStatus: "ready",
            transcriptRevision: 1,
            transcriptStatus: "ready",
          },
        ],
      ],
      []
    );
    mocks.scoped.mockImplementation((fn) => fn(tx, scope));
    let startedMemo: Record<string, unknown> = {};
    const start = vi.fn((_workflow, options) => {
      ({ memo: startedMemo } = options);
      return Promise.resolve({
        describe: async () => ({
          memo: { temniaIntentSha256: "different" },
          status: { name: "RUNNING" },
        }),
      });
    });
    mocks.getClient.mockResolvedValue(temporalClient({ start }));

    await expect(
      startChapterRun({
        brief: "Make complete chapters",
        budgetDollars: "1.00",
        requestKey: RUN,
        runId: RUN,
        sourceId: SOURCE,
      })
    ).resolves.toEqual({
      conflict: true,
      message: "That chapter request ID already names a different request.",
      ok: false,
      runId: RUN,
    });
    expect(startedMemo.temniaIntentSha256).toMatch(SHA256);
    expect(mocks.deadline).toHaveBeenCalledWith(
      expect.any(Number),
      expect.any(Function)
    );
  });

  it("binds pre-persistence identity to the server-frozen run configuration", async () => {
    const ready = {
      deletionRequestedAt: null,
      sourceStatus: "ready",
      transcriptRevision: 1,
      transcriptStatus: "ready",
    };
    const transactions = [
      transaction([[], [ready]], []),
      transaction([[], [ready]], []),
    ];
    mocks.scoped.mockImplementation((fn) => fn(transactions.shift(), scope));
    let acceptedMemo: Record<string, unknown> | null = null;
    const start = vi.fn((_workflow, options) => {
      acceptedMemo ??= options.memo;
      return Promise.resolve({
        describe: async () => ({
          memo: acceptedMemo,
          status: { name: "RUNNING" },
        }),
      });
    });
    mocks.getClient.mockResolvedValue(temporalClient({ start }));
    const intent = {
      brief: "Make complete chapters",
      budgetDollars: "1.00",
      requestKey: RUN,
      runId: RUN,
      sourceId: SOURCE,
    };
    await expect(startChapterRun(intent)).resolves.toMatchObject({
      ok: false,
      pending: true,
    });

    process.env.HARNESS_ROUTE_SNAPSHOT_ID = "snapshot-v2";
    await expect(startChapterRun(intent)).resolves.toMatchObject({
      conflict: true,
      ok: false,
    });
  });

  it("resolves a terminal workflow with no durable run instead of polling forever", async () => {
    const transactions = [
      transaction(
        [
          [],
          [
            {
              deletionRequestedAt: null,
              sourceStatus: "ready",
              transcriptRevision: 1,
              transcriptStatus: "ready",
            },
          ],
        ],
        []
      ),
      transaction([[{ id: SOURCE }]], []),
    ];
    mocks.scoped.mockImplementation((fn) => fn(transactions.shift(), scope));
    let memo: Record<string, unknown> = {};
    const start = vi.fn((_workflow, options) => {
      ({ memo } = options);
      return Promise.resolve({
        describe: async () => ({ memo, status: { name: "RUNNING" } }),
      });
    });
    const getHandle = vi.fn(() => ({
      describe: async () => ({ memo, status: { name: "FAILED" } }),
    }));
    mocks.getClient.mockResolvedValue(temporalClient({ getHandle, start }));
    const intent = {
      brief: "Make complete chapters",
      budgetDollars: "1.00",
      requestKey: RUN,
      runId: RUN,
      sourceId: SOURCE,
    };

    await expect(startChapterRun(intent)).resolves.toMatchObject({
      ok: false,
      pending: true,
      runId: RUN,
    });
    await expect(
      getPendingChapterWorkflowStatus({ intent, kind: "start" })
    ).resolves.toEqual({
      message:
        "The chapter editing stopped before publishing its durable result. Discard this request and create a new one.",
      state: "terminal",
    });
    expect(getHandle).toHaveBeenCalledWith(`chapter-${RUN}`);
  });

  it("refuses a pre-persistence review race when Temporal memo names another command", async () => {
    const command = {
      action: "retry",
      baseRevision: 2,
      boundaryId: null,
      budgetMicros: null,
      mutationKey: RUN,
      otherSectionId: null,
      reason: "",
      runId: RUN,
      sectionId: null,
      sourceId: SOURCE,
      targetRevision: null,
      targetTimeMs: null,
    };
    mocks.scoped.mockImplementation((fn) =>
      fn(transaction([[{ id: RUN }], []], []), scope)
    );
    const start = vi.fn(() =>
      Promise.resolve({
        describe: async () => ({
          memo: { temniaIntentSha256: "different" },
          status: { name: "RUNNING" },
        }),
      })
    );
    mocks.getClient.mockResolvedValue(temporalClient({ start }));

    await expect(reviewChapterCommand(command)).resolves.toEqual({
      conflict: true,
      message: "That review command ID already names a different command.",
      ok: false,
      runId: RUN,
    });
    expect(start).toHaveBeenCalledTimes(1);
  });
});
