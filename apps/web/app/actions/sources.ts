"use server";

import {
  IngestInputSchema,
  sourcePrefix,
  TASK_QUEUES,
  WORKFLOWS,
} from "@temnia/contracts";
import {
  harnessArtifact,
  harnessOperation,
  harnessRun,
  source,
  transcript,
  upload,
  usageLedger,
} from "@temnia/db";
import { WorkflowNotFoundError } from "@temporalio/client";
import { and, eq, sql } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { deletePrefix } from "@/lib/storage/prefix";
import { getTemporalClient } from "@/lib/temporal/client";
import { abortMultipart } from "@/lib/uploads/server";

const IdSchema = z.uuid();

export type SourceActionResult = { ok: true } | { ok: false; message: string };

/**
 * Starts the ingest again for a source whose previous run is over: a failed
 * one, a re-ingest of a ready one, or an uploaded one whose workflow gave up
 * before it could claim the row. Never for a source still uploading.
 */
export async function retryIngest(
  sourceId: string
): Promise<SourceActionResult> {
  const id = IdSchema.safeParse(sourceId);
  if (!id.success) {
    return { message: "not a source id", ok: false };
  }
  const started = await scoped(async (tx, scope) => {
    const [row] = await tx
      .select({
        deletionRequestedAt: source.deletionRequestedAt,
        masterKey: source.masterKey,
        projectId: source.projectId,
        status: source.status,
      })
      .from(source)
      .where(eq(source.id, id.data))
      .limit(1);
    if (
      !row ||
      row.deletionRequestedAt ||
      row.status === "uploading" ||
      row.status === "processing"
    ) {
      return null;
    }
    return {
      input: IngestInputSchema.parse({
        artifactPrefix: sourcePrefix(scope.organizationId, id.data),
        masterKey: row.masterKey,
        scope,
        sourceId: id.data,
      }),
      projectId: row.projectId,
    };
  });
  if (!started) {
    return { message: "this source cannot be retried right now", ok: false };
  }
  const workflowId = `ingest-${id.data}`;
  const client = await getTemporalClient();
  await client.workflow.start(WORKFLOWS.ingest, {
    args: [started.input],
    taskQueue: TASK_QUEUES.pipeline,
    workflowExecutionTimeout: "12 hours",
    workflowId,
    workflowIdConflictPolicy: "USE_EXISTING",
  });
  await scoped((tx) =>
    tx
      .update(source)
      .set({
        errorMessage: null,
        ingestWorkflowId: workflowId,
        status: "uploaded",
      })
      .where(and(eq(source.id, id.data), eq(source.status, "failed")))
  );
  revalidatePath(`/projects/${started.projectId}`);
  return { ok: true };
}

/**
 * Removes a source: any open multipart upload is aborted at the store, every
 * object under the source's prefix is deleted, the storage the ledger counted
 * for it is written back as a negative entry, then the row goes (its upload
 * and artifact rows cascade). Both deterministic writer workflows must be
 * proven naturally completed or absent before storage is touched.
 */
// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: deletion deliberately keeps the source lock, history fence, both remote-writer proofs, storage cleanup, ledger, and row deletion in one fail-closed operation
export async function deleteSource(
  sourceId: string
): Promise<SourceActionResult> {
  const id = IdSchema.safeParse(sourceId);
  if (!id.success) {
    return { message: "not a source id", ok: false };
  }
  const found = await scoped(async (tx, scope) => {
    await tx.execute(
      sql`SELECT 1 FROM ${source} WHERE ${source.id} = ${id.data} FOR UPDATE`
    );
    const [row] = await tx
      .select({
        deletionRequestedAt: source.deletionRequestedAt,
        projectId: source.projectId,
        status: source.status,
        transcriptStatus: transcript.status,
        workflowId: source.ingestWorkflowId,
      })
      .from(source)
      .leftJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, id.data))
      .limit(1);
    if (!row) {
      return null;
    }
    for (const historyTable of [
      harnessRun,
      harnessArtifact,
      harnessOperation,
    ]) {
      // biome-ignore lint/performance/noAwaitInLoops: one transaction and three indexed existence probes
      const history = await tx
        .select({ id: historyTable.id })
        .from(historyTable)
        .where(eq(historyTable.sourceId, id.data))
        .limit(1);
      if (history.length > 0) {
        return { history: true as const, projectId: row.projectId };
      }
    }
    if (!row.deletionRequestedAt) {
      await tx
        .update(source)
        .set({ deletionRequestedAt: new Date() })
        .where(eq(source.id, id.data));
    }
    const open = await tx
      .select({
        key: upload.storageKey,
        multipartUploadId: upload.multipartUploadId,
      })
      .from(upload)
      .where(and(eq(upload.sourceId, id.data), eq(upload.status, "active")));
    return {
      ...row,
      history: false as const,
      open,
      prefix: sourcePrefix(scope.organizationId, id.data),
    };
  });
  if (!found) {
    return { message: "source not found", ok: false };
  }
  if (found.history) {
    return {
      message: "This source has editing history and cannot be deleted.",
      ok: false,
    };
  }
  const client = await getTemporalClient().catch(() => null);
  let writerBlock: "running" | "reconcile" | "unknown" | null = client
    ? null
    : "unknown";
  if (client) {
    for (const workflowId of [`ingest-${id.data}`, `transcribe-${id.data}`]) {
      try {
        // biome-ignore lint/performance/noAwaitInLoops: both deterministic external writers need individual closure evidence
        const description = await client.workflow
          .getHandle(workflowId)
          .describe();
        if (description.status.name === "COMPLETED") {
          continue;
        }
        if (
          ["RUNNING", "PAUSED", "CONTINUED_AS_NEW"].includes(
            description.status.name
          )
        ) {
          writerBlock ??= "running";
        } else {
          writerBlock = "reconcile";
        }
      } catch (error) {
        if (!(error instanceof WorkflowNotFoundError)) {
          writerBlock ??= "unknown";
        }
      }
    }
  }
  if (writerBlock) {
    const messages = {
      reconcile:
        "Media processing ended without proof that its external writers stopped. The source remains fenced until its outcome is reconciled.",
      running:
        "Media processing is still running. The source remains fenced; try deletion again after it completes.",
      unknown:
        "Deletion could not confirm the media processing outcome. The source remains fenced; try again later.",
    } as const;
    return {
      message: messages[writerBlock],
      ok: false,
    };
  }
  for (const part of found.open) {
    // biome-ignore lint/performance/noAwaitInLoops: at most one open upload per source
    await abortMultipart(part.key, part.multipartUploadId);
  }
  await deletePrefix(found.prefix);
  await scoped(async (tx, scope) => {
    const [counted] = await tx
      .select({
        total: sql<number>`COALESCE(SUM(${usageLedger.quantity}), 0)::bigint`,
      })
      .from(usageLedger)
      .where(
        and(
          eq(usageLedger.sourceId, id.data),
          eq(usageLedger.kind, "storage_bytes")
        )
      );
    const total = Number(counted?.total ?? 0);
    if (total !== 0) {
      await tx.insert(usageLedger).values({
        detail: { category: "deleted" },
        kind: "storage_bytes",
        organizationId: scope.organizationId,
        quantity: -total,
        sourceId: id.data,
      });
    }
    await tx.delete(source).where(eq(source.id, id.data));
  });
  revalidatePath(`/projects/${found.projectId}`);
  return { ok: true };
}
