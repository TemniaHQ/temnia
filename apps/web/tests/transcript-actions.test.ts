import { WorkflowNotFoundError } from "@temporalio/client";
import type { SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { retryTranscription } from "@/app/actions/transcript";

const DISPATCH_TOKEN = /^dispatch:/;
const SOURCE = "01992ffe-0a00-7000-8000-000000000001";
const scope = {
  organizationId: "01992ffe-0a00-7000-8000-000000000002",
  userId: "01992ffe-0a00-7000-8000-000000000003",
};
const mocks = vi.hoisted(() => ({
  describe: vi.fn(),
  getClient: vi.fn(),
  scoped: vi.fn(),
  start: vi.fn(),
}));
vi.mock("@/lib/db", () => ({ scoped: mocks.scoped }));
vi.mock("@/lib/temporal/client", () => ({
  getTemporalClient: mocks.getClient,
}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/transcript/queries", () => ({
  discardRevision: vi.fn(),
  readRevision: vi.fn(),
  writeRevision: vi.fn(),
}));

const snapshot = {
  durationMs: 40_000,
  status: "ready",
  transcriptAttempts: 2,
  transcriptRevision: 1,
  transcriptRunId: "finished-run",
  transcriptStatus: "failed",
  transcriptUpdatedAt: "2026-09-08 01:02:03.123456+00",
};
interface Mutation {
  condition?: SQL;
  values: Record<string, unknown>;
}
let observed:
  | typeof snapshot
  | { [Key in keyof typeof snapshot]: (typeof snapshot)[Key] | null };
let mutations: Mutation[];
let reservations: unknown[][];

/** The DB supplies CAS outcomes; assertions inspect the real Drizzle predicate. */
function transaction() {
  return {
    insert: () => ({
      values: (values: Record<string, unknown>) => {
        mutations.push({ values });
        return {
          onConflictDoNothing: () => ({
            returning: () => Promise.resolve(reservations.shift() ?? []),
          }),
        };
      },
    }),
    select: () => {
      const query = {
        from: () => query,
        leftJoin: () => query,
        limit: () => Promise.resolve([{ ...observed }]),
        where: () => query,
      };
      return query;
    },
    update: () => ({
      set: (values: Record<string, unknown>) => ({
        where: (condition: SQL) => {
          mutations.push({ condition, values });
          return {
            returning: () => Promise.resolve(reservations.shift() ?? []),
          };
        },
      }),
    }),
  };
}

function predicate(mutation: Mutation | undefined) {
  if (!mutation?.condition) {
    throw new Error("Expected a conditional mutation");
  }
  return new PgDialect().sqlToQuery(mutation.condition);
}

beforeEach(() => {
  vi.resetAllMocks();
  observed = { ...snapshot };
  mutations = [];
  reservations = [[{ id: "transcript-id" }]];
  mocks.scoped.mockImplementation(
    (
      fn: (
        tx: ReturnType<typeof transaction>,
        currentScope: typeof scope
      ) => unknown
    ) => fn(transaction(), scope)
  );
  mocks.getClient.mockResolvedValue({
    workflow: {
      getHandle: () => ({ describe: mocks.describe }),
      start: mocks.start,
    },
  });
  mocks.describe.mockResolvedValue({ status: { name: "FAILED" } });
  mocks.start.mockResolvedValue({});
});

describe("retryTranscription ownership", () => {
  it.each(["UNKNOWN", "CONTINUED_AS_NEW"])(
    "does not treat Temporal status %s as a closed execution",
    async (name) => {
      mocks.describe.mockResolvedValue({ status: { name } });
      expect(await retryTranscription(SOURCE)).toMatchObject({
        message:
          "Transcription status could not be checked. Try again in a moment.",
        ok: false,
      });
      expect(mutations).toEqual([]);
      expect(mocks.start).not.toHaveBeenCalled();
    }
  );

  it.each(["pending", "processing", "failed", "ready"])(
    "leaves a %s row alone when Temporal cannot establish its status",
    async (status) => {
      observed.transcriptStatus = status;
      mocks.describe.mockRejectedValue(new Error("control plane unavailable"));
      expect(await retryTranscription(SOURCE)).toEqual({
        message:
          "Transcription status could not be checked. Try again in a moment.",
        ok: false,
      });
      expect(mutations).toEqual([]);
      expect(mocks.start).not.toHaveBeenCalled();
    }
  );

  it("does not replace a pending run that is already live but has not claimed", async () => {
    observed.transcriptStatus = "pending";
    mocks.describe.mockResolvedValue({ status: { name: "RUNNING" } });
    expect(await retryTranscription(SOURCE)).toMatchObject({ ok: false });
    expect(mutations).toEqual([]);
  });

  it("starts once when two retries compete for the same observed row", async () => {
    reservations = [[{ id: "transcript-id" }], []];
    const results = await Promise.all([
      retryTranscription(SOURCE),
      retryTranscription(SOURCE),
    ]);
    expect(results.filter((result) => result.ok)).toHaveLength(1);
    expect(mocks.start).toHaveBeenCalledOnce();
    for (const mutation of mutations) {
      const query = predicate(mutation);
      expect(query.sql).toContain('"transcript"."run_id" =');
      expect(query.sql).toContain('"transcript"."status" =');
      expect(query.sql).toContain('"transcript"."attempts" =');
      expect(query.params).toEqual([
        SOURCE,
        "failed",
        2,
        "finished-run",
        1,
        snapshot.transcriptUpdatedAt,
      ]);
      expect(mutation.values.runId).toMatch(DISPATCH_TOKEN);
    }
    expect(mutations[0]?.values.runId).not.toBe(mutations[1]?.values.runId);
  });

  it("does not launch after a newer claim wins the reservation comparison", async () => {
    observed.transcriptStatus = "processing";
    reservations = [[]];
    expect(await retryTranscription(SOURCE)).toEqual({
      message:
        "Transcription changed while you were retrying. Refresh and try again.",
      ok: false,
    });
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("compares nullable run/revision with IS NULL", async () => {
    observed.transcriptRunId = null;
    observed.transcriptRevision = null;
    await retryTranscription(SOURCE);
    const query = predicate(mutations[0]);
    expect(query.sql).toContain('"transcript"."run_id" is null');
    expect(query.sql).toContain('"transcript"."current_revision" is null');
    expect(query.params).not.toContain(null);
  });

  it("reserves an absent row only when its insert wins", async () => {
    observed.transcriptStatus = null;
    reservations = [[]];
    mocks.describe.mockRejectedValue(
      new WorkflowNotFoundError("missing", `transcribe-${SOURCE}`, undefined)
    );
    expect(await retryTranscription(SOURCE)).toMatchObject({ ok: false });
    expect(mutations[0]?.values).toMatchObject({
      organizationId: scope.organizationId,
      sourceId: SOURCE,
    });
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it.each(["RUNNING", "unknown"])(
    "preserves the pending reservation after a lost start ACK and %s status",
    async (status) => {
      mocks.start.mockRejectedValue(new Error("acknowledgement lost"));
      if (status === "unknown") {
        mocks.describe
          .mockResolvedValueOnce({ status: { name: "FAILED" } })
          .mockRejectedValueOnce(new Error("status unavailable"));
      } else {
        mocks.describe
          .mockResolvedValueOnce({ status: { name: "FAILED" } })
          .mockResolvedValueOnce({ status: { name: status } });
      }
      const result = await retryTranscription(SOURCE);
      expect(result.ok).toBe(status === "RUNNING");
      expect(mutations).toHaveLength(1);
      expect(mutations[0]?.values.status).toBe("pending");
    }
  );

  it("marks dispatch failure only while its own pending token still owns the row", async () => {
    mocks.start.mockRejectedValue(new Error("connection refused"));
    expect(await retryTranscription(SOURCE)).toEqual({
      message: "Transcription could not be queued. Try again.",
      ok: false,
    });
    expect(mutations).toHaveLength(2);
    expect(predicate(mutations[1]).params).toEqual([
      SOURCE,
      "pending",
      mutations[0]?.values.runId,
    ]);
    expect(mutations[1]?.values.status).toBe("failed");
  });
});
