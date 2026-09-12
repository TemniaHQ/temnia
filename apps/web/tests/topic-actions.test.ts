import { WORKFLOWS } from "@temnia/contracts";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  editTopicPortfolio,
  getPendingTopicWorkflowStatus,
  reviewTopicCommand,
  startTopicRun,
} from "@/app/actions/topics";
import {
  DEFAULT_TOPIC_BRIEF_VERSION,
  resolveTopicBrief,
  TOPIC_POLICY,
  TOPIC_SELECTION_POLICY,
  TOPIC_SELECTION_POLICY_V3,
} from "@/lib/harness/topic-defaults";
import {
  restoreTopicIntent,
  TopicReviewIntentSchema,
  TopicStartIntentSchema,
} from "@/lib/harness/topic-pending";

const SOURCE = "01992ffe-0a00-7000-8000-000000000001";
const RUN = "01992ffe-0a00-7000-8000-000000000004";
const SHA256 = /^[a-f0-9]{64}$/;
const scope = {
  organizationId: "01992ffe-0a00-7000-8000-000000000002",
  userId: "01992ffe-0a00-7000-8000-000000000003",
};
const intent = {
  defaultBriefVersion: TOPIC_POLICY,
  requestKey: RUN,
  runId: RUN,
  sourceId: SOURCE,
} as const;
const command = {
  action: "accept",
  baseRevision: 1,
  boundaryId: null,
  budgetMicros: null,
  mutationKey: SOURCE,
  otherSectionId: null,
  reason: "I listened to the full video.",
  runId: RUN,
  sectionId: "independent-one",
  sourceId: SOURCE,
  targetRevision: null,
  targetTimeMs: null,
};
const mocks = vi.hoisted(() => ({
  getClient: vi.fn(),
  scoped: vi.fn(),
  start: vi.fn(),
}));
vi.mock("@/lib/db", () => ({ scoped: mocks.scoped }));
vi.mock("@/lib/temporal/client", () => ({
  getTemporalClient: mocks.getClient,
}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));

function rows(responses: unknown[][]) {
  const query = {
    from: () => query,
    leftJoin: () => query,
    limit: () => Promise.resolve(responses.shift() ?? []),
    where: () => query,
  };
  mocks.scoped.mockImplementation((fn) => fn({ select: () => query }, scope));
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.HARNESS_ENABLED = "1";
  delete process.env.HARNESS_TOPIC_SELECTION_ENABLED;
  delete process.env.HARNESS_TOPIC_SELECTION_V3_ENABLED;
  process.env.HARNESS_BACKEND = "gateway";
  process.env.HARNESS_ROUTE_SNAPSHOT_ID = "qualified-snapshot";
  process.env.HARNESS_MAX_RUN_BUDGET_MICROS = "50000000";
  mocks.start.mockImplementation(async (_workflow, options) => ({
    describe: async () => ({ memo: options.memo, status: { name: "RUNNING" } }),
  }));
  mocks.getClient.mockResolvedValue({
    withDeadline: async (_deadline: number, operation: () => unknown) =>
      operation(),
    workflow: { start: mocks.start },
  });
});

function readySource() {
  rows([
    [],
    [
      {
        deletionRequestedAt: null,
        sourceStatus: "ready",
        transcriptRevision: 1,
        transcriptStatus: "ready",
      },
    ],
  ]);
}

describe("topic workflow admission and pending identity", () => {
  it("starts the generic topic workflow with the server budget and independent-video brief", async () => {
    readySource();
    await expect(startTopicRun(intent)).resolves.toMatchObject({
      ok: false,
      pending: true,
      runId: RUN,
    });
    const [name, options] = mocks.start.mock.calls[0] ?? [];
    expect(name).toBe(WORKFLOWS.topicRun);
    expect(options.workflowId).toBe(`topic-run/${RUN}`);
    expect(options.args[0]).toMatchObject({
      brief: resolveTopicBrief(intent),
      budgetMicros: 50_000_000,
      scope,
    });
    expect(options.args[0].brief).toContain("reuse source context");
    expect(options.args[0].brief).not.toContain("exact-cover");
    expect(options.workflowIdReusePolicy).toBe("REJECT_DUPLICATE");
  });

  it("preserves custom instructions and changes the exact request identity", async () => {
    readySource();
    await startTopicRun(intent);
    const firstMemo = mocks.start.mock.calls[0]?.[1].memo;
    readySource();
    await startTopicRun({ ...intent, brief: "Keep the original language." });
    expect(mocks.start.mock.calls[1]?.[1].args[0].brief).toBe(
      "Keep the original language."
    );
    expect(mocks.start.mock.calls[1]?.[1].memo).not.toEqual(firstMemo);
  });

  it("refuses an existing legacy chapter identity instead of adopting it", async () => {
    rows([
      [
        {
          brief: resolveTopicBrief(intent),
          budgetMicros: 50_000_000,
          config: {},
          id: RUN,
          requestKey: RUN,
          routeSnapshot: { editorialPolicy: "chapter-editorial/1" },
        },
      ],
    ]);
    expect(await startTopicRun(intent)).toMatchObject({
      message: "That topic request ID already names a different request.",
      ok: false,
    });
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("retains an unknown start without dispatching a second execution", async () => {
    readySource();
    mocks.getClient.mockRejectedValue(new Error("unreachable"));
    expect(await startTopicRun(intent)).toMatchObject({
      ok: false,
      pending: true,
      runId: RUN,
    });
    expect(mocks.start).not.toHaveBeenCalled();
    rows([[{ id: SOURCE }]]);
    expect(
      await getPendingTopicWorkflowStatus({ intent, kind: "start" })
    ).toMatchObject({ state: "unknown" });
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("admits only accept/reject/cancel and addresses the candidate ID", async () => {
    rows([[{ id: RUN }], []]);
    expect(await reviewTopicCommand(command)).toMatchObject({
      ok: true,
      pending: true,
    });
    expect(mocks.start.mock.calls[0]?.[0]).toBe(WORKFLOWS.topicReview);
    expect(mocks.start.mock.calls[0]?.[1].args[0]).toMatchObject({
      scope,
      sectionId: "independent-one",
    });
    expect(mocks.start.mock.calls[0]?.[1].workflowId).toBe(
      `topic-review/${RUN}/${SOURCE}`
    );
    rows([]);
    expect(
      await reviewTopicCommand({
        ...command,
        action: "nudge_boundary",
        boundaryId: "c1",
        targetTimeMs: 500,
      })
    ).toMatchObject({ ok: false });
    expect(mocks.start).toHaveBeenCalledTimes(1);
  });

  it("restores topic pending requests only under the versioned topic shape", () => {
    expect(
      restoreTopicIntent(JSON.stringify(intent), TopicStartIntentSchema)
    ).toEqual(intent);
    expect(
      restoreTopicIntent(
        JSON.stringify({ ...intent, defaultBriefVersion: "topic-chapters/1" }),
        TopicStartIntentSchema
      )
    ).toBeNull();
    expect(
      restoreTopicIntent(JSON.stringify(command), TopicReviewIntentSchema)
    ).toEqual(command);
    expect(
      restoreTopicIntent(
        JSON.stringify({ ...command, action: "merge" }),
        TopicReviewIntentSchema
      )
    ).toBeNull();
    expect(TOPIC_POLICY).toBe("standalone-topics/1");
  });
});

describe("versioned selection rollout and human corrections", () => {
  it("keeps the new program gated without blocking a restored v1 intent", async () => {
    expect(DEFAULT_TOPIC_BRIEF_VERSION).toBe(TOPIC_SELECTION_POLICY_V3);
    expect(
      await startTopicRun({
        ...intent,
        defaultBriefVersion: TOPIC_SELECTION_POLICY,
      })
    ).toMatchObject({ ok: false });
    expect(mocks.start).not.toHaveBeenCalled();
    readySource();
    await startTopicRun(intent);
    expect(mocks.start.mock.calls[0]?.[0]).toBe(WORKFLOWS.topicRun);
  });

  it("dispatches v2 with its own frozen workflow and memo identity", async () => {
    process.env.HARNESS_TOPIC_SELECTION_ENABLED = "1";
    readySource();
    await startTopicRun({
      ...intent,
      defaultBriefVersion: TOPIC_SELECTION_POLICY,
    });
    const [name, options] = mocks.start.mock.calls[0] ?? [];
    expect(name).toBe(WORKFLOWS.topicSelection);
    expect(options.workflowId).toBe(`topic-selection/${RUN}`);
    const nextMemo = options.memo;
    readySource();
    await startTopicRun(intent);
    expect(mocks.start.mock.calls[1]?.[1].memo).not.toEqual(nextMemo);
  });

  it("dispatches v3 with its inventory-first workflow and memo identity", async () => {
    process.env.HARNESS_TOPIC_SELECTION_V3_ENABLED = "1";
    readySource();
    await startTopicRun({
      ...intent,
      defaultBriefVersion: TOPIC_SELECTION_POLICY_V3,
    });
    const [name, options] = mocks.start.mock.calls[0] ?? [];
    expect(name).toBe(WORKFLOWS.topicSelectionV3);
    expect(options.workflowId).toBe(`topic-selection-v3/${RUN}`);
    expect(options.memo.temniaIntentSha256).toMatch(SHA256);
  });

  it("uses the separate human mutation workflow and persists the complete command", async () => {
    rows([[{ id: RUN }], []]);
    const patch = {
      action: "topic_edit",
      baseEditSha256: "a".repeat(64),
      baseRevision: 1,
      correctionActiveSeconds: null,
      correctionMeasurementMethod: null,
      evidenceSha256: "b".repeat(64),
      mutationKey: SOURCE,
      operations: [
        {
          affectedCandidateIds: ["one"],
          kind: "drop",
          operationId: "drop-one",
          replacementCandidates: [],
        },
      ],
      reason: "This topic is redundant.",
      runId: RUN,
      sourceId: SOURCE,
      version: 1,
    };
    expect(await editTopicPortfolio(patch)).toMatchObject({
      ok: true,
      pending: true,
      runId: RUN,
    });
    expect(mocks.start.mock.calls[0]?.[0]).toBe(WORKFLOWS.topicEditorialPatch);
    expect(mocks.start.mock.calls[0]?.[1].args[0]).toEqual({ ...patch, scope });
    expect(mocks.start.mock.calls[0]?.[1].workflowId).toBe(
      `topic-edit/${RUN}/${SOURCE}`
    );
  });
});
