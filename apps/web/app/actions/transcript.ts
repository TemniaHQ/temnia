"use server";

import { createHash, randomUUID } from "node:crypto";
import {
  sourcePrefix,
  TASK_QUEUES,
  type TranscriptCorrectionCommand,
  TranscriptCorrectionCommandSchema,
  TranscriptCorrectionMetadataSchema,
  TranscriptRevisionAnnotationsSchema,
  transcriptCorrectionKey,
  WORKFLOWS,
} from "@temnia/contracts";
import {
  source,
  transcript,
  transcriptRevision,
  usageLedger,
} from "@temnia/db";
import {
  type WorkflowExecutionStatusName,
  WorkflowNotFoundError,
} from "@temporalio/client";
import { and, desc, eq, isNull, lte, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { getTemporalClient } from "@/lib/temporal/client";
import { STALE_REVISION_MESSAGE } from "@/lib/transcript/edits";
import {
  discardRevision,
  readRevision,
  writeRevisionArtifact,
} from "@/lib/transcript/queries";
import {
  applyStructuralCorrection,
  legacyAnnotations,
} from "@/lib/transcript/structure";

const IdSchema = z.uuid();

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

const CLOSED_EXECUTIONS = new Set<WorkflowExecutionStatusName>([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
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
        deletionRequestedAt: source.deletionRequestedAt,
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
  if (
    found?.status !== "ready" ||
    found.durationMs === null ||
    found.deletionRequestedAt
  ) {
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
    await tx.execute(
      sql`SELECT 1 FROM ${source} WHERE ${source.id} = ${id.data} FOR UPDATE`
    );
    const [currentSource] = await tx
      .select({ deletionRequestedAt: source.deletionRequestedAt })
      .from(source)
      .where(eq(source.id, id.data))
      .limit(1);
    if (!currentSource || currentSource.deletionRequestedAt) {
      return null;
    }
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

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function sameCommand(
  left: TranscriptCorrectionCommand,
  right: TranscriptCorrectionCommand
): boolean {
  return canonicalJson(left) === canonicalJson(right);
}

/**
 * Apply an identity-addressed structural correction as one immutable revision.
 * Object I/O happens outside both locking transactions; source then transcript
 * locks make deletion and concurrent saves resolve in one deterministic order.
 */
export async function correctTranscriptStructure(
  sourceId: string,
  input: unknown
): Promise<TranscriptActionResult> {
  const id = IdSchema.safeParse(sourceId);
  const parsed = TranscriptCorrectionCommandSchema.safeParse(input);
  if (!id.success) {
    return { message: "not a source id", ok: false };
  }
  if (!parsed.success) {
    return {
      invalid: true,
      message: "that structural correction cannot be saved",
      ok: false,
    };
  }
  const command = parsed.data;
  const loaded = await scoped(async (tx, scope) => {
    const [found] = await tx
      .select({
        deletionRequestedAt: source.deletionRequestedAt,
        row: transcript,
      })
      .from(source)
      .innerJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, id.data))
      .limit(1);
    if (!found || found.row.currentRevision === null) {
      return null;
    }
    const [prior] = await tx
      .select()
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, found.row.id),
          sql`${transcriptRevision.metadata}->>'mutationKey' = ${command.mutationKey}`
        )
      )
      .limit(1);
    if (prior) {
      const metadata = TranscriptCorrectionMetadataSchema.safeParse(
        prior.metadata
      );
      return metadata.success
        ? { duplicate: { metadata: metadata.data, revision: prior.revision } }
        : { keyConflict: true as const };
    }
    if (
      found.deletionRequestedAt ||
      found.row.currentRevision !== command.baseRevision
    ) {
      return {
        deleted: Boolean(found.deletionRequestedAt),
        stale: found.row.currentRevision !== command.baseRevision,
      };
    }
    const [current] = await tx
      .select()
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, found.row.id),
          eq(transcriptRevision.revision, command.baseRevision)
        )
      )
      .limit(1);
    const wantedRevision =
      command.action === "undo" ? command.targetRevision : command.baseRevision;
    const [wanted] =
      wantedRevision === command.baseRevision
        ? [current]
        : await tx
            .select()
            .from(transcriptRevision)
            .where(
              and(
                eq(transcriptRevision.transcriptId, found.row.id),
                eq(transcriptRevision.revision, wantedRevision)
              )
            )
            .limit(1);
    const [machine] = await tx
      .select({ revision: transcriptRevision.revision })
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, found.row.id),
          eq(transcriptRevision.kind, "machine"),
          lte(transcriptRevision.revision, wantedRevision)
        )
      )
      .orderBy(desc(transcriptRevision.revision))
      .limit(1);
    return current && wanted && machine
      ? {
          current,
          machineRevision: machine.revision,
          prefix: sourcePrefix(scope.organizationId, id.data),
          row: found.row,
          wanted,
        }
      : null;
  });
  if (!loaded) {
    return { message: "there is no transcript revision to correct", ok: false };
  }
  if ("duplicate" in loaded) {
    return sameCommand(loaded.duplicate.metadata.command, command)
      ? { ok: true, revision: loaded.duplicate.revision }
      : {
          invalid: true,
          message: "that mutation key already names a different correction",
          ok: false,
        };
  }
  if ("keyConflict" in loaded) {
    return {
      invalid: true,
      message: "that mutation key already names an unreadable correction",
      ok: false,
    };
  }
  if ("deleted" in loaded) {
    return loaded.deleted
      ? { message: "This source is pending deletion.", ok: false }
      : { message: STALE_REVISION_MESSAGE, ok: false, stale: true };
  }

  const wantedContent = await readRevision(loaded.wanted.storageKey);
  const annotationParse = TranscriptRevisionAnnotationsSchema.safeParse(
    loaded.wanted.metadata.annotations
  );
  const wantedAnnotations = annotationParse.success
    ? annotationParse.data
    : legacyAnnotations(
        loaded.row.id,
        loaded.machineRevision,
        wantedContent,
        loaded.row.speakerLabels
      );
  const structural =
    command.action === "undo"
      ? {
          affectedIdentityIds: wantedAnnotations.wordIdentities.map(
            (identity) => identity.id
          ),
          annotations: wantedAnnotations,
          content: wantedContent,
          deletedIdentityIds: [],
        }
      : applyStructuralCorrection(wantedContent, wantedAnnotations, command);
  const next = command.baseRevision + 1;
  const intentHash = createHash("sha256")
    .update(canonicalJson(command))
    .digest("hex");
  const key = transcriptCorrectionKey(
    loaded.prefix,
    next,
    `${command.mutationKey}-${intentHash.slice(0, 16)}`
  );
  const artifact = await writeRevisionArtifact(key, structural.content);
  const metadata = TranscriptCorrectionMetadataSchema.parse({
    action: command.action,
    affectedIdentityIds: structural.affectedIdentityIds,
    annotations: structural.annotations,
    artifactSha256: artifact.sha256,
    baseRevision: command.baseRevision,
    command,
    deletedIdentityIds: structural.deletedIdentityIds,
    mutationKey: command.mutationKey,
  });

  const saved = await scoped(async (tx, scope) => {
    await tx.execute(
      sql`SELECT 1 FROM ${source} WHERE ${source.id} = ${id.data} FOR UPDATE`
    );
    await tx.execute(
      sql`SELECT 1 FROM ${transcript} WHERE ${transcript.id} = ${loaded.row.id} FOR UPDATE`
    );
    const [state] = await tx
      .select({
        currentRevision: transcript.currentRevision,
        deletionRequestedAt: source.deletionRequestedAt,
      })
      .from(source)
      .innerJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, id.data))
      .limit(1);
    const [prior] = await tx
      .select()
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, loaded.row.id),
          sql`${transcriptRevision.metadata}->>'mutationKey' = ${command.mutationKey}`
        )
      )
      .limit(1);
    if (prior) {
      const priorMetadata = TranscriptCorrectionMetadataSchema.safeParse(
        prior.metadata
      );
      return priorMetadata.success &&
        sameCommand(priorMetadata.data.command, command)
        ? { duplicate: prior.revision }
        : { keyConflict: true as const };
    }
    if (state?.deletionRequestedAt) {
      return { deleted: true as const };
    }
    if (state?.currentRevision !== command.baseRevision) {
      return { stale: true as const };
    }
    await tx.insert(transcriptRevision).values({
      baseRevision: command.baseRevision,
      createdBy: scope.userId,
      kind: "correction",
      metadata,
      organizationId: scope.organizationId,
      revision: next,
      sizeBytes: artifact.sizeBytes,
      storageKey: key,
      transcriptId: loaded.row.id,
      wordCount: structural.content.words.length,
    });
    await tx
      .update(transcript)
      .set({ currentRevision: next })
      .where(eq(transcript.id, loaded.row.id));
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
    const [stored] = await tx
      .select({
        total: sql<number>`COALESCE(SUM(${transcriptRevision.sizeBytes}), 0)::bigint`,
      })
      .from(transcriptRevision)
      .where(eq(transcriptRevision.transcriptId, loaded.row.id));
    const delta = Number(stored?.total ?? 0) - Number(counted?.total ?? 0);
    if (delta !== 0) {
      await tx.insert(usageLedger).values({
        detail: { category: "transcript", revision: next },
        idempotencyKey: `transcript-correction:${loaded.row.id}:${command.mutationKey}`,
        kind: "storage_bytes",
        organizationId: scope.organizationId,
        quantity: delta,
        sourceId: id.data,
      });
    }
    return { saved: true as const };
  });
  if ("duplicate" in saved) {
    return { ok: true, revision: saved.duplicate };
  }
  if ("keyConflict" in saved) {
    await discardRevision(key);
    return {
      invalid: true,
      message: "that mutation key already names a different correction",
      ok: false,
    };
  }
  if ("deleted" in saved) {
    await discardRevision(key);
    return { message: "This source is pending deletion.", ok: false };
  }
  if ("stale" in saved) {
    await discardRevision(key);
    return { message: STALE_REVISION_MESSAGE, ok: false, stale: true };
  }
  revalidatePath(`/sources/${id.data}`);
  return { ok: true, revision: next };
}
