import { WORKFLOWS } from "@temnia/contracts";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getPendingTopicWorkflowStatus,
  reviewTopicCommand,
  startTopicRun,
} from "@/app/actions/topics";
import {
  DEFAULT_TOPIC_BRIEF_VERSION,
  resolveTopicBrief,
  TOPIC_POLICY,
} from "@/lib/harness/topic-defaults";
import {
  restoreTopicIntent,
  TopicReviewIntentSchema,
  TopicStartIntentSchema,
} from "@/lib/harness/topic-pending";

const SOURCE = "01992ffe-0a00-7000-8000-000000000001";
const RUN = "01992ffe-0a00-7000-8000-000000000004";
const scope = {
  organizationId: "01992ffe-0a00-7000-8000-000000000002",
  userId: "01992ffe-0a00-7000-8000-000000000003",
};
const intent = {
  defaultBriefVersion: DEFAULT_TOPIC_BRIEF_VERSION,
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
