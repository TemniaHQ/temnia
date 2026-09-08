"use server";

import { randomUUID } from "node:crypto";
import {
  sourcePrefix,
  TASK_QUEUES,
  transcriptCorrectionKey,
  WORKFLOWS,
} from "@temnia/contracts";
import {
  source,
  transcript,
  transcriptRevision,
  usageLedger,
} from "@temnia/db";
import { WorkflowNotFoundError } from "@temporalio/client";
import { and, eq, isNull, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { getTemporalClient } from "@/lib/temporal/client";
import {
  applyEdits,
  EditsSchema,
  STALE_REVISION_MESSAGE,
  type TranscriptEdits,
} from "@/lib/transcript/edits";
import {
  discardRevision,
  readRevision,
  writeRevision,
} from "@/lib/transcript/queries";

const IdSchema = z.uuid();

/** Names a person typed. Bounded so a label cannot become a payload. */
const LabelsSchema = z.record(
  z.string().min(1).max(16),
  z.string().trim().min(1).max(80)
);

/**
 * Two refusals a correction has to keep apart.
 *
 * `stale` means somebody else saved first and the revision this edit was made
 * against is no longer current: nothing is wrong with the edit, and the answer
 * is to reload. `invalid` means the edit itself does not apply to the revision
 * it names, and the reader has to see that on the input they are still typing
 * in. Answering both with the reload notice tells somebody their own good edit
 * was overtaken by a change that never happened, and the reload loses it.
 */
export type TranscriptActionResult =
  | { ok: true; revision: number }
  | { ok: true }
  | { invalid?: true; message: string; ok: false; stale?: true };

const CLOSED_EXECUTIONS = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELED",
  "TERMINATED",
  "TIMED_OUT",
]);

const STILL_RUNNING_MESSAGE = "Transcription is still running.";
const NOT_QUEUED_MESSAGE = "Transcription could not be queued. Try again.";
const STATUS_UNKNOWN_MESSAGE =
  "Transcription status could not be checked. Try again in a moment.";
const START_UNKNOWN_MESSAGE =
  "Could not confirm whether transcription started. Try again in a moment.";
const ROW_CHANGED_MESSAGE =
  "Transcription changed while you were retrying. Refresh and try again.";

/** Control-plane uncertainty is never permission to replace a run. */
async function transcriptionExecution(
  sourceId: string
): Promise<"running" | "closed" | "unknown"> {
  try {
    const client = await getTemporalClient();
    const description = await client.workflow
      .getHandle(`transcribe-${sourceId}`)
      .describe();
    if (CLOSED_EXECUTIONS.has(description.status.name)) {
      return "closed";
    }
    // UNKNOWN provides no evidence of closure; CONTINUED_AS_NEW may already
    // have a live successor, so neither authorizes replacing its database row.
    return description.status.name === "RUNNING" ? "running" : "unknown";
  } catch (error) {
    return error instanceof WorkflowNotFoundError ? "closed" : "unknown";
  }
}

/**
 * Reserve only the row we inspected, after confirming its workflow is closed.
 * The temporary dispatch token in run_id owns this pending transition; the
 * worker replaces it with its real Temporal run id when it claims the row.
 * This also distinguishes concurrent retries of an already-pending row.
 */
export async function retryTranscription(
  sourceId: string
): Promise<TranscriptActionResult> {
  const id = IdSchema.safeParse(sourceId);
  if (!id.success) {
    return { message: "not a source id", ok: false };
  }
  const found = await scoped(async (tx) => {
    const [row] = await tx
      .select({
        durationMs: source.durationMs,
        status: source.status,
        transcriptAttempts: transcript.attempts,
        transcriptRevision: transcript.currentRevision,
        transcriptRunId: transcript.runId,
        transcriptStatus: transcript.status,
        // Preserve Postgres microseconds: a JS Date would truncate them and
        // make an unchanged Python-written row fail the comparison below.
        transcriptUpdatedAt: sql<string | null>`${transcript.updatedAt}::text`,
      })
      .from(source)
      .leftJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, id.data))
      .limit(1);
    return row;
  });
  if (found?.status !== "ready" || found.durationMs === null) {
    return {
      message: "this transcript cannot be retried right now",
      ok: false,
    };
  }
  const execution = await transcriptionExecution(id.data);
  if (execution !== "closed") {
    return {
      message:
        execution === "running"
          ? STILL_RUNNING_MESSAGE
          : STATUS_UNKNOWN_MESSAGE,
      ok: false,
    };
  }
  const { durationMs } = found;
  const dispatchToken = `dispatch:${randomUUID()}`;
  const started = await scoped(async (tx, scope) => {
    const reserved = {
      errorMessage: null,
      heartbeatAt: null,
      percent: null,
      runId: dispatchToken,
      stage: null,
      status: "pending" as const,
    };
    const rows = found.transcriptStatus
      ? await tx
          .update(transcript)
          .set(reserved)
          .where(
            and(
              eq(transcript.sourceId, id.data),
              eq(transcript.status, found.transcriptStatus),
              eq(transcript.attempts, found.transcriptAttempts ?? 0),
              found.transcriptRunId === null
                ? isNull(transcript.runId)
                : eq(transcript.runId, found.transcriptRunId),
              found.transcriptRevision === null
                ? isNull(transcript.currentRevision)
                : eq(transcript.currentRevision, found.transcriptRevision),
              sql`${transcript.updatedAt}::text = ${found.transcriptUpdatedAt}`
            )
          )
          .returning({ id: transcript.id })
      : await tx
          .insert(transcript)
          .values({
            ...reserved,
            organizationId: scope.organizationId,
            sourceId: id.data,
          })
          .onConflictDoNothing({ target: transcript.sourceId })
          .returning({ id: transcript.id });
    if (rows.length === 0) {
      return null;
    }
    const prefix = sourcePrefix(scope.organizationId, id.data);
    return {
      artifactPrefix: prefix,
      audioKey: `${prefix}audio/audio.m4a`,
      durationMs,
      scope,
      sourceId: id.data,
    };
  });
  if (!started) {
    revalidatePath(`/sources/${id.data}`);
    return { message: ROW_CHANGED_MESSAGE, ok: false };
  }
  try {
    const client = await getTemporalClient();
    await client.workflow.start(WORKFLOWS.transcribe, {
      args: [started],
      taskQueue: TASK_QUEUES.pipeline,
      workflowExecutionTimeout: "6 hours",
      workflowId: `transcribe-${id.data}`,
      workflowIdConflictPolicy: "USE_EXISTING",
      workflowIdReusePolicy: "ALLOW_DUPLICATE",
    });
  } catch (error) {
    // A lost start acknowledgement may still have launched a real workflow.
    // Preserve the reservation unless Temporal confirms no execution is live.
    const afterStart = await transcriptionExecution(id.data);
    if (afterStart !== "closed") {
      revalidatePath(`/sources/${id.data}`);
      return afterStart === "running"
        ? { ok: true }
        : { message: START_UNKNOWN_MESSAGE, ok: false };
    }
    const reason = error instanceof Error ? error.message : String(error);
    await scoped(async (tx) => {
      await tx
        .update(transcript)
        .set({
          errorMessage: `DispatchError: ${reason}`.slice(0, 2000),
          status: "failed",
        })
        .where(
          and(
            eq(transcript.sourceId, id.data),
            eq(transcript.status, "pending"),
            eq(transcript.runId, dispatchToken)
          )
        );
    });
    revalidatePath(`/sources/${id.data}`);
    return { message: NOT_QUEUED_MESSAGE, ok: false };
  }
  revalidatePath(`/sources/${id.data}`);
  return { ok: true };
}

/**
 * Rename the speakers.
 *
 * Names live on the transcript row, not in the revisions, so a rename is one
 * update and costs no storage. Giving two ids the same name merges them on
 * screen and in every export, which is the manual merge over-segmented
 * diarization needs; nothing auto-merges.
 */
export async function updateSpeakerLabels(
  sourceId: string,
  labels: Record<string, string>
): Promise<TranscriptActionResult> {
  const id = IdSchema.safeParse(sourceId);
  const parsed = LabelsSchema.safeParse(labels);
  if (!id.success) {
    return { message: "not a source id", ok: false };
  }
  if (!parsed.success) {
    return {
      invalid: true,
      message: "those speaker names cannot be saved",
      ok: false,
    };
  }
  const updated = await scoped(async (tx) => {
    const rows = await tx
      .update(transcript)
      .set({ speakerLabels: parsed.data })
      .where(eq(transcript.sourceId, id.data))
      .returning({ id: transcript.id });
    return rows.length > 0;
  });
  if (!updated) {
    return { message: "there is no transcript for this source", ok: false };
  }
  revalidatePath(`/sources/${id.data}`);
  return { ok: true };
}

/**
 * Save a correction as a new revision.
 *
 * Revisions are new objects, never overwrites, so the revision a reader has
 * open stays readable while someone else saves. `baseRevision` is checked
 * inside the transaction that writes the next one: two tabs editing the same
 * revision means the second save is refused with words the user can act on,
 * not a silent last-write-wins.
 */
export async function correctTranscript(
  sourceId: string,
  baseRevision: number,
  edits: TranscriptEdits
): Promise<TranscriptActionResult> {
  const id = IdSchema.safeParse(sourceId);
  const base = z.int().positive().safeParse(baseRevision);
  const parsed = EditsSchema.safeParse(edits);
  if (!(id.success && base.success)) {
    return { message: "not a source id", ok: false };
  }
  if (!parsed.success) {
    return { invalid: true, message: "that edit cannot be saved", ok: false };
  }

  const loaded = await scoped(async (tx, scope) => {
    const [row] = await tx
      .select()
      .from(transcript)
      .where(eq(transcript.sourceId, id.data))
      .limit(1);
    if (!row || row.currentRevision === null) {
      return null;
    }
    const [current] = await tx
      .select()
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, row.id),
          eq(transcriptRevision.revision, row.currentRevision)
        )
      )
      .limit(1);
    return current
      ? { current, prefix: sourcePrefix(scope.organizationId, id.data), row }
      : null;
  });
  if (!loaded) {
    return { message: "there is no transcript for this source", ok: false };
  }
  if (loaded.row.currentRevision !== base.data) {
    return { message: STALE_REVISION_MESSAGE, ok: false, stale: true };
  }

  const content = await readRevision(loaded.current.storageKey);
  const failure = applyEdits(content, parsed.data);
  if (failure) {
    // The base revision was current a line ago, so this is the edit and not
    // the revision: an index this transcript does not have. Reloading would
    // not help and would throw away what the reader typed.
    return { invalid: true, message: failure, ok: false };
  }

  const next = base.data + 1;
  // One object per attempt. Two tabs saving against the same revision both
  // upload; the compare-and-swap below publishes one of them and the other's
  // object is discarded, so the accepted pointer never serves the loser's
  // bytes (S2 review, I03).
  const key = transcriptCorrectionKey(
    loaded.prefix,
    next,
    randomUUID().slice(0, 8)
  );
  const sizeBytes = await writeRevision(key, content);

  const saved = await scoped(async (tx, scope) => {
    // The guard is inside the write: between the read above and here another
    // tab may have saved, and the update matching nothing is what says so.
    const moved = await tx
      .update(transcript)
      .set({ currentRevision: next })
      .where(
        and(
          eq(transcript.id, loaded.row.id),
          eq(transcript.currentRevision, base.data)
        )
      )
      .returning({ id: transcript.id });
    if (moved.length === 0) {
      return false;
    }
    await tx.insert(transcriptRevision).values({
      baseRevision: base.data,
      createdBy: scope.userId,
      kind: "correction",
      metadata: {
        edits: Array.isArray(parsed.data) ? parsed.data.length : 1,
        kind: Array.isArray(parsed.data) ? "words" : "speaker",
      },
      organizationId: scope.organizationId,
      revision: next,
      sizeBytes,
      storageKey: key,
      transcriptId: loaded.row.id,
      wordCount: content.words.length,
    });
    const [counted] = await tx
      .select({
        total: sql<number>`COALESCE(SUM(${usageLedger.quantity}), 0)::bigint`,
      })
      .from(usageLedger)
      .where(
        and(
          eq(usageLedger.sourceId, id.data),
          eq(usageLedger.kind, "storage_bytes"),
          sql`${usageLedger.detail}->>'category' = 'transcript'`
        )
      );
    // The same delta pattern the pipeline meters with: every revision object
    // that exists, minus what the transcript category has already counted.
    const [stored] = await tx
      .select({
        total: sql<number>`COALESCE(SUM(${transcriptRevision.sizeBytes}), 0)::bigint`,
      })
      .from(transcriptRevision)
      .where(eq(transcriptRevision.transcriptId, loaded.row.id));
    const delta = Number(stored?.total ?? 0) - Number(counted?.total ?? 0);
    if (delta !== 0) {
      await tx.insert(usageLedger).values({
        detail: {
          category: "transcript",
          revision: next,
          total: Number(stored?.total ?? 0),
        },
        kind: "storage_bytes",
        organizationId: scope.organizationId,
        quantity: delta,
        sourceId: id.data,
      });
    }
    return true;
  });
  if (!saved) {
    await discardRevision(key);
    return { message: STALE_REVISION_MESSAGE, ok: false, stale: true };
  }
  revalidatePath(`/sources/${id.data}`);
  return { ok: true, revision: next };
}
