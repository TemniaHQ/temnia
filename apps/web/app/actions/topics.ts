"use server";

import { createHash } from "node:crypto";
import {
  ChapterReviewInputSchema,
  ChapterRunInputSchema,
  TASK_QUEUES,
  TopicEditorialPatchInputSchema,
  WORKFLOWS,
} from "@temnia/contracts";
import { chapterReviewEvent, harnessRun, source, transcript } from "@temnia/db";
import { WorkflowNotFoundError } from "@temporalio/client";
import { and, eq, or, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { harnessSettings } from "@/lib/harness/config";
import {
  TOPIC_POLICY,
  TopicStartInstructionsSchema,
} from "@/lib/harness/topic-defaults";
import { getTemporalClient } from "@/lib/temporal/client";

const TopicReviewSchema = ChapterReviewInputSchema.refine(
  (command) =>
    ["accept", "reject", "cancel"].includes(command.action) &&
    command.boundaryId === null &&
    command.otherSectionId === null &&
    command.budgetMicros === null &&
    command.targetRevision === null &&
    command.targetTimeMs === null &&
    (command.action !== "cancel" || command.sectionId === null),
  { error: "Only topic acceptance, rejection and cancellation are supported." }
);

const INTENT_MEMO_KEY = "temniaIntentSha256";
const TEMPORAL_RPC_DEADLINE_MS = 5000;

const StartSchema = TopicStartInstructionsSchema.extend({
  requestKey: z.uuid(),
  runId: z.uuid(),
  sourceId: z.uuid(),
});

export type TopicActionResult =
  | { ok: true; pending?: true; runId: string }
  | {
      conflict?: true;
      message: string;
      ok: false;
      pending?: true;
      runId?: string;
    };

export interface PendingTopicWorkflowStatus {
  message: string;
  state: "absent" | "pending" | "terminal" | "unknown";
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** One button starts one program; the intent hash covers the input as sent. */
function topicGeneration(input: unknown) {
  return {
    intent: {
      editorialPolicy: TOPIC_POLICY,
      input,
      workflow: WORKFLOWS.topicSelection,
    },
    prefix: "topic-selection",
    workflow: WORKFLOWS.topicSelection,
  };
}

/**
 * Whitespace is not an instruction. An omitted brief leaves the single default
 * to the worker, which freezes it into the run row; a typed brief is sent
 * trimmed, so the two starts are different requests and a replay of either is
 * byte-identical.
 */
function topicBrief(instructions: {
  brief?: string | undefined;
}): string | undefined {
  const trimmed = instructions.brief?.trim();
  return trimmed ? trimmed : undefined;
}

function intentSha256(value: unknown): string {
  return createHash("sha256").update(stableJson(value)).digest("hex");
}

type IntentInspection =
  | { kind: "absent" }
  | { kind: "matched"; status: string }
  | { kind: "mismatch" }
  | { kind: "unknown" };

async function inspectTemporalIntent(
  workflowId: string,
  expectedHash: string
): Promise<IntentInspection> {
  try {
    const client = await getTemporalClient();
    const description = await client.withDeadline(
      Date.now() + TEMPORAL_RPC_DEADLINE_MS,
      () => client.workflow.getHandle(workflowId).describe()
    );
    const memoHash = description.memo?.[INTENT_MEMO_KEY];
    return memoHash === expectedHash
      ? { kind: "matched", status: description.status.name }
      : { kind: "mismatch" };
  } catch (error) {
    return error instanceof WorkflowNotFoundError
      ? { kind: "absent" }
      : { kind: "unknown" };
  }
}

function pendingStatus(
  kind: "topic discovery" | "review command",
  inspection: IntentInspection
): PendingTopicWorkflowStatus {
  if (inspection.kind === "absent") {
    return {
      message: `No Temporal execution is recorded for this ${kind}. Retry the same request.`,
      state: "absent",
    };
  }
  if (inspection.kind === "unknown") {
    return {
      message: `The ${kind} status could not be confirmed. Retry the status check or the same request.`,
      state: "unknown",
    };
  }
  if (inspection.kind === "mismatch") {
    return {
      message: `This ${kind} ID belongs to a different request, scope, or server-frozen configuration. Discard it and create a new request.`,
      state: "terminal",
    };
  }
  if (["RUNNING", "PAUSED", "CONTINUED_AS_NEW"].includes(inspection.status)) {
    return {
      message: `The ${kind} is still running. Waiting for its durable result.`,
      state: "pending",
    };
  }
  if (inspection.status === "COMPLETED") {
    return {
      message: `The ${kind} completed without publishing its durable result. Reconciliation is required; discard this request before creating a new one.`,
      state: "terminal",
    };
  }
  if (
    ["FAILED", "CANCELLED", "TERMINATED", "TIMED_OUT"].includes(
      inspection.status
    )
  ) {
    return {
      message: `The ${kind} stopped before publishing its durable result. Discard this request and create a new one.`,
      state: "terminal",
    };
  }
  return {
    message: `The ${kind} status could not be confirmed. Retry the status check or the same request.`,
    state: "unknown",
  };
}

export async function startTopicRun(
  input: unknown
): Promise<TopicActionResult> {
  const parsed = StartSchema.safeParse(input);
  if (!parsed.success) {
    return { message: "The topic request is invalid.", ok: false };
  }
  const brief = topicBrief(parsed.data);
  const availability = harnessSettings();
  if (!availability.available) {
    return { message: availability.message, ok: false };
  }
  const budgetMicros = availability.settings.maxRunBudgetMicros;
  const prepared = await scoped(async (tx, scope) => {
    const [existing] = await tx
      .select()
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.sourceId, parsed.data.sourceId),
          or(
            eq(harnessRun.id, parsed.data.runId),
            eq(harnessRun.requestKey, parsed.data.requestKey)
          )
        )
      )
      .limit(1);
    if (existing) {
      const initialBudget = Number(
        existing.routeSnapshot.initialBudgetMicros ?? existing.budgetMicros
      );
      const same =
        existing.routeSnapshot.editorialPolicy === TOPIC_POLICY &&
        existing.id === parsed.data.runId &&
        existing.requestKey === parsed.data.requestKey &&
        // An omitted brief is the worker's default, which this process does not
        // hold; the run row's frozen brief is the only copy of it.
        (brief === undefined || existing.brief === brief) &&
        initialBudget === budgetMicros &&
        stableJson(existing.config) ===
          stableJson(availability.settings.config);
      return same
        ? { existing: true as const }
        : {
            error: "That topic request ID already names a different request.",
          };
    }
    const [row] = await tx
      .select({
        deletionRequestedAt: source.deletionRequestedAt,
        sourceStatus: source.status,
        transcriptRevision: transcript.currentRevision,
        transcriptStatus: transcript.status,
      })
      .from(source)
      .leftJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, parsed.data.sourceId))
      .limit(1);
    if (!row) {
      return { error: "Source not found." } as const;
    }
    if (row.deletionRequestedAt) {
      return { error: "This source is pending deletion." } as const;
    }
    if (
      row.sourceStatus !== "ready" ||
      row.transcriptStatus !== "ready" ||
      !row.transcriptRevision
    ) {
      return {
        error: "A ready current transcript is required before topic discovery.",
      } as const;
    }
    return {
      input: ChapterRunInputSchema.parse({
        ...(brief === undefined ? {} : { brief }),
        budgetMicros,
        config: availability.settings.config,
        requestKey: parsed.data.requestKey,
        runId: parsed.data.runId,
        scope,
        sourceId: parsed.data.sourceId,
      }),
    } as const;
  });
  if ("error" in prepared) {
    return { message: prepared.error, ok: false };
  }
  if ("existing" in prepared) {
    return { ok: true, runId: parsed.data.runId };
  }
  const generation = topicGeneration(prepared.input);
  const intentHash = intentSha256(generation.intent);
  const workflowId = `${generation.prefix}/${parsed.data.runId}`;
  try {
    const client = await getTemporalClient();
    const description = await client.withDeadline(
      Date.now() + TEMPORAL_RPC_DEADLINE_MS,
      async () => {
        const handle = await client.workflow.start(generation.workflow, {
          args: [prepared.input],
          memo: { [INTENT_MEMO_KEY]: intentHash },
          taskQueue: TASK_QUEUES.pipeline,
          workflowExecutionTimeout: "12 hours",
          workflowId,
          workflowIdConflictPolicy: "USE_EXISTING",
          workflowIdReusePolicy: "REJECT_DUPLICATE",
        });
        return handle.describe();
      }
    );
    if (description.memo?.[INTENT_MEMO_KEY] !== intentHash) {
      return {
        conflict: true,
        message: "That topic request ID already names a different request.",
        ok: false,
        runId: parsed.data.runId,
      };
    }
  } catch {
    const inspection = await inspectTemporalIntent(workflowId, intentHash);
    if (inspection.kind === "mismatch") {
      return {
        conflict: true,
        message: "That topic request ID already names a different request.",
        ok: false,
        runId: parsed.data.runId,
      };
    }
    return {
      message:
        "Could not confirm whether topic discovery started. Check or retry this request.",
      ok: false,
      pending: true,
      runId: parsed.data.runId,
    };
  }
  revalidatePath(`/sources/${parsed.data.sourceId}`);
  return {
    message:
      "Topic discovery was dispatched. Waiting for the durable run record.",
    ok: false,
    pending: true,
    runId: parsed.data.runId,
  };
}

export async function reviewTopicCommand(
  input: unknown
): Promise<TopicActionResult> {
  const prepared = await scoped(async (tx, scope) => {
    const parsed = TopicReviewSchema.safeParse({
      ...(typeof input === "object" && input ? input : {}),
      scope,
    });
    if (!parsed.success) {
      return { error: "The topic review command is invalid." } as const;
    }
    const [run] = await tx
      .select({ id: harnessRun.id })
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.id, parsed.data.runId),
          eq(harnessRun.sourceId, parsed.data.sourceId),
          eq(harnessRun.lane, "chapters"),
          sql`${harnessRun.routeSnapshot}->>'editorialPolicy' = ${TOPIC_POLICY}`
        )
      )
      .limit(1);
    if (!run) {
      return { error: "Topic run not found." } as const;
    }
    const [existing] = await tx
      .select({
        payload: chapterReviewEvent.payload,
        state: chapterReviewEvent.state,
      })
      .from(chapterReviewEvent)
      .where(
        and(
          eq(chapterReviewEvent.runId, parsed.data.runId),
          eq(chapterReviewEvent.mutationKey, parsed.data.mutationKey)
        )
      )
      .limit(1);
    if (existing) {
      if (stableJson(existing.payload) !== stableJson(parsed.data)) {
        return {
          error: "That review command ID already names a different command.",
        } as const;
      }
      return { existing: existing.state } as const;
    }
    return { command: parsed.data } as const;
  });
  if ("error" in prepared) {
    return { message: prepared.error, ok: false };
  }
  if ("existing" in prepared) {
    if (prepared.existing === "applied") {
      return { ok: true, runId: (input as { runId: string }).runId };
    }
    return prepared.existing === "conflict"
      ? {
          conflict: true,
          message: "This command was based on an older topic revision.",
          ok: false,
          runId: (input as { runId: string }).runId,
        }
      : {
          message: "This review command was refused.",
          ok: false,
          runId: (input as { runId: string }).runId,
        };
  }
  const intentHash = intentSha256(prepared.command);
  const workflowId = `topic-review/${prepared.command.runId}/${prepared.command.mutationKey}`;
  try {
    const client = await getTemporalClient();
    const description = await client.withDeadline(
      Date.now() + TEMPORAL_RPC_DEADLINE_MS,
      async () => {
        const handle = await client.workflow.start(WORKFLOWS.topicReview, {
          args: [prepared.command],
          memo: { [INTENT_MEMO_KEY]: intentHash },
          taskQueue: TASK_QUEUES.pipeline,
          workflowExecutionTimeout: "12 hours",
          workflowId,
          workflowIdConflictPolicy: "USE_EXISTING",
          workflowIdReusePolicy: "REJECT_DUPLICATE",
        });
        return handle.describe();
      }
    );
    if (description.memo?.[INTENT_MEMO_KEY] !== intentHash) {
      return {
        conflict: true,
        message: "That review command ID already names a different command.",
        ok: false,
        runId: prepared.command.runId,
      };
    }
  } catch {
    const inspection = await inspectTemporalIntent(workflowId, intentHash);
    if (inspection.kind === "mismatch") {
      return {
        conflict: true,
        message: "That review command ID already names a different command.",
        ok: false,
        runId: prepared.command.runId,
      };
    }
    return {
      message:
        "Could not confirm this review command. Check or retry the same command.",
      ok: false,
      pending: true,
      runId: prepared.command.runId,
    };
  }
  revalidatePath(`/sources/${prepared.command.sourceId}`);
  return { ok: true, pending: true, runId: prepared.command.runId };
}

export async function editTopicPortfolio(
  input: unknown
): Promise<TopicActionResult> {
  const prepared = await scoped(async (tx, scope) => {
    const parsed = TopicEditorialPatchInputSchema.safeParse({
      ...(typeof input === "object" && input ? input : {}),
      scope,
    });
    if (!parsed.success) {
      return { error: "The topic editorial correction is invalid." } as const;
    }
    const [run] = await tx
      .select({ id: harnessRun.id })
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.id, parsed.data.runId),
          eq(harnessRun.sourceId, parsed.data.sourceId),
          eq(harnessRun.lane, "chapters"),
          sql`${harnessRun.routeSnapshot}->>'editorialPolicy' = ${TOPIC_POLICY}`
        )
      )
      .limit(1);
    if (!run) {
      return { error: "Topic run not found." } as const;
    }
    const [existing] = await tx
      .select({
        payload: chapterReviewEvent.payload,
        state: chapterReviewEvent.state,
      })
      .from(chapterReviewEvent)
      .where(
        and(
          eq(chapterReviewEvent.runId, parsed.data.runId),
          eq(chapterReviewEvent.mutationKey, parsed.data.mutationKey)
        )
      )
      .limit(1);
    if (existing) {
      if (stableJson(existing.payload) !== stableJson(parsed.data)) {
        return {
          error:
            "That editorial correction ID already names a different command.",
        } as const;
      }
      return { existing: existing.state } as const;
    }
    return { command: parsed.data } as const;
  });
  if ("error" in prepared) {
    return { message: prepared.error, ok: false };
  }
  if ("existing" in prepared) {
    if (prepared.existing === "applied") {
      return { ok: true, runId: (input as { runId: string }).runId };
    }
    return prepared.existing === "conflict"
      ? {
          conflict: true,
          message: "This command was based on an older topic revision.",
          ok: false,
          runId: (input as { runId: string }).runId,
        }
      : {
          message: "This editorial correction was refused.",
          ok: false,
          runId: (input as { runId: string }).runId,
        };
  }
  const intentHash = intentSha256(prepared.command);
  const workflowId = `topic-edit/${prepared.command.runId}/${prepared.command.mutationKey}`;
  try {
    const client = await getTemporalClient();
    const description = await client.withDeadline(
      Date.now() + TEMPORAL_RPC_DEADLINE_MS,
      async () => {
        const handle = await client.workflow.start(
          WORKFLOWS.topicEditorialPatch,
          {
            args: [prepared.command],
            memo: { [INTENT_MEMO_KEY]: intentHash },
            taskQueue: TASK_QUEUES.pipeline,
            workflowExecutionTimeout: "12 hours",
            workflowId,
            workflowIdConflictPolicy: "USE_EXISTING",
            workflowIdReusePolicy: "REJECT_DUPLICATE",
          }
        );
        return handle.describe();
      }
    );
    if (description.memo?.[INTENT_MEMO_KEY] !== intentHash) {
      return {
        conflict: true,
        message:
          "That editorial correction ID already names a different command.",
        ok: false,
        runId: prepared.command.runId,
      };
    }
  } catch {
    const inspection = await inspectTemporalIntent(workflowId, intentHash);
    if (inspection.kind === "mismatch") {
      return {
        conflict: true,
        message:
          "That editorial correction ID already names a different command.",
        ok: false,
        runId: prepared.command.runId,
      };
    }
    return {
      message:
        "Could not confirm this editorial correction. Check or retry the same command.",
      ok: false,
      pending: true,
      runId: prepared.command.runId,
    };
  }
  revalidatePath(`/sources/${prepared.command.sourceId}`);
  return { ok: true, pending: true, runId: prepared.command.runId };
}

const PendingWorkflowSchema = z.discriminatedUnion("kind", [
  z.object({ intent: StartSchema, kind: z.literal("start") }),
  z.object({ intent: z.unknown(), kind: z.literal("review") }),
  z.object({ intent: z.unknown(), kind: z.literal("patch") }),
]);

/** Inspect only one deterministic pending execution after its durable row is absent. */
export async function getPendingTopicWorkflowStatus(
  input: unknown
): Promise<PendingTopicWorkflowStatus> {
  const parsed = PendingWorkflowSchema.safeParse(input);
  if (!parsed.success) {
    return { message: "The pending request is invalid.", state: "terminal" };
  }
  if (parsed.data.kind === "start") {
    const { intent } = parsed.data;
    const availability = harnessSettings();
    if (!availability.available) {
      return { message: availability.message, state: "unknown" };
    }
    const budgetMicros = availability.settings.maxRunBudgetMicros;
    const ownedScope = await scoped(async (tx, scope) => {
      const [row] = await tx
        .select({ id: source.id })
        .from(source)
        .where(eq(source.id, intent.sourceId))
        .limit(1);
      return row ? scope : null;
    });
    if (!ownedScope) {
      return { message: "Source not found.", state: "terminal" };
    }
    const brief = topicBrief(intent);
    const workflowInput = ChapterRunInputSchema.parse({
      ...(brief === undefined ? {} : { brief }),
      budgetMicros,
      config: availability.settings.config,
      requestKey: intent.requestKey,
      runId: intent.runId,
      scope: ownedScope,
      sourceId: intent.sourceId,
    });
    const generation = topicGeneration(workflowInput);
    const inspection = await inspectTemporalIntent(
      `${generation.prefix}/${intent.runId}`,
      intentSha256(generation.intent)
    );
    return pendingStatus("topic discovery", inspection);
  }
  const prepared = await scoped(async (tx, scope) => {
    const commandSchema =
      parsed.data.kind === "patch"
        ? TopicEditorialPatchInputSchema
        : TopicReviewSchema;
    const command = commandSchema.safeParse({
      ...(typeof parsed.data.intent === "object" && parsed.data.intent
        ? parsed.data.intent
        : {}),
      scope,
    });
    if (!command.success) {
      return null;
    }
    const [run] = await tx
      .select({ id: harnessRun.id })
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.id, command.data.runId),
          eq(harnessRun.sourceId, command.data.sourceId),
          eq(harnessRun.lane, "chapters"),
          sql`${harnessRun.routeSnapshot}->>'editorialPolicy' = ${TOPIC_POLICY}`
        )
      )
      .limit(1);
    return run ? command.data : null;
  });
  if (!prepared) {
    return {
      message: "The pending review command is invalid or inaccessible.",
      state: "terminal",
    };
  }
  const inspection = await inspectTemporalIntent(
    `${parsed.data.kind === "patch" ? "topic-edit" : "topic-review"}/${prepared.runId}/${prepared.mutationKey}`,
    intentSha256(prepared)
  );
  return pendingStatus("review command", inspection);
}
