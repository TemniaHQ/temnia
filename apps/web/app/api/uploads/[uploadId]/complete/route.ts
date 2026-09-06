/**
 * POST /api/uploads/{uploadId}/complete: the browser has PUT every part.
 * The store's own part list is the truth (sizes are re-checked, because R2
 * replaces a part on re-upload and a failed re-upload loses the original),
 * then the upload completes, the master's bytes are metered, and the ingest
 * workflow starts.
 */
import {
  IngestInputSchema,
  sourcePrefix,
  TASK_QUEUES,
  WORKFLOWS,
} from "@temnia/contracts";
import { source, upload, usageLedger } from "@temnia/db";
import { and, eq, sql } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { scoped } from "@/lib/db";
import { getTemporalClient } from "@/lib/temporal/client";
import {
  completeMultipart,
  listUploadedParts,
  missingParts,
} from "@/lib/uploads/server";

export async function POST(
  _request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]/complete">
): Promise<Response> {
  const { uploadId } = await context.params;
  const row = await scoped(async (tx) => {
    const [found] = await tx
      .select()
      .from(upload)
      .where(and(eq(upload.id, uploadId), eq(upload.status, "active")))
      .limit(1);
    return found;
  });
  if (!row) {
    return Response.json({ error: "upload not found" }, { status: 404 });
  }
  const parts = await listUploadedParts(row.storageKey, row.multipartUploadId);
  if (parts === null) {
    return Response.json(
      { error: "the store no longer has this upload" },
      { status: 409 }
    );
  }
  const missing = missingParts(row.sizeBytes, row.partSizeBytes, parts);
  if (missing.length > 0) {
    return Response.json({ error: "parts missing", missing }, { status: 409 });
  }
  await completeMultipart(row.storageKey, row.multipartUploadId, parts);

  const started = await scoped(async (tx, scope) => {
    await tx
      .update(upload)
      .set({ completedAt: sql`now()`, status: "completed" })
      .where(eq(upload.id, row.id));
    await tx
      .update(source)
      .set({ status: "uploaded", uploadedAt: sql`now()` })
      .where(eq(source.id, row.sourceId));
    await tx.insert(usageLedger).values({
      detail: { category: "master", key: row.storageKey },
      kind: "storage_bytes",
      organizationId: scope.organizationId,
      quantity: row.sizeBytes,
      sourceId: row.sourceId,
    });
    return {
      input: IngestInputSchema.parse({
        artifactPrefix: sourcePrefix(scope.organizationId, row.sourceId),
        masterKey: row.storageKey,
        scope,
        sourceId: row.sourceId,
      }),
    };
  });

  const workflowId = `ingest-${row.sourceId}`;
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
      .set({ ingestWorkflowId: workflowId })
      .where(eq(source.id, row.sourceId))
  );
  return Response.json({ sourceId: row.sourceId, workflowId });
}
