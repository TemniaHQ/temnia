import { beforeEach, describe, expect, it, vi } from "vitest";
import { deleteSource } from "@/app/actions/sources";

const SOURCE = "01992ffe-0a00-7000-8000-000000000001";
const RUN = "01992ffe-0a00-7000-8000-000000000004";
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
