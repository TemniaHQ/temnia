/**
 * POST /api/uploads/{uploadId}/complete: make the upload complete, once.
 *
 * Uppy is handed this route as the URL for CompleteMultipartUpload, so the
 * browser POSTs its part list here (XML) and expects an S3-shaped answer.
 * The store is the truth, not the body: if the object already exists at the
 * key with the right size the upload is complete; otherwise the store's own
 * part list is checked and the server completes the multipart itself. The
 * row transition runs once (a conditional update), so a second call, from a
 * retry or the upload script, answers the same success. Then the master's
 * bytes are metered and the ingest workflow starts.
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
import { storageSettings } from "@/lib/storage/client";
import { getTemporalClient } from "@/lib/temporal/client";
import { missingParts } from "@/lib/uploads/parts";
import {
  completeMultipart,
  headObjectSize,
  listUploadedParts,
} from "@/lib/uploads/server";

const XML = /xml/;

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function respond(
  wantsXml: boolean,
  status: number,
  body: {
    error?: string;
    key?: string;
    missing?: number[];
    sourceId?: string;
    workflowId?: string;
  }
): Response {
  if (!wantsXml) {
    return Response.json(body, { status });
  }
  if (body.error !== undefined) {
    return new Response(
      `<Error><Code>UploadIncomplete</Code><Message>${escapeXml(body.error)}</Message></Error>`,
      { headers: { "content-type": "application/xml" }, status }
    );
  }
  const { bucket, publicEndpoint } = storageSettings();
  const key = body.key ?? "";
  return new Response(
    `<CompleteMultipartUploadResult><Location>${escapeXml(`${publicEndpoint}/${bucket}/${key}`)}</Location><Bucket>${escapeXml(bucket)}</Bucket><Key>${escapeXml(key)}</Key></CompleteMultipartUploadResult>`,
    { headers: { "content-type": "application/xml" }, status }
  );
}

export async function POST(
  request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]/complete">
): Promise<Response> {
  const { uploadId } = await context.params;
  const wantsXml = XML.test(request.headers.get("content-type") ?? "");
  const row = await scoped(async (tx) => {
    const [found] = await tx
      .select()
      .from(upload)
      .where(eq(upload.id, uploadId))
      .limit(1);
    return found;
  });
  if (!row) {
    return respond(wantsXml, 404, { error: "upload not found" });
  }
  if (row.status === "aborted") {
    return respond(wantsXml, 410, { error: "this upload was cancelled" });
  }
  const workflowId = `ingest-${row.sourceId}`;

  if (row.status === "active") {
    // Storage first. The object exists when the browser completed the
    // multipart itself; otherwise the parts do, and the server completes.
    const stored = await headObjectSize(row.storageKey);
    if (stored === null) {
      const parts = await listUploadedParts(
        row.storageKey,
        row.multipartUploadId
      );
      if (parts === null) {
        return respond(wantsXml, 409, {
          error: "the store no longer has this upload",
        });
      }
      const missing = missingParts(row.sizeBytes, row.partSizeBytes, parts);
      if (missing.length > 0) {
        return respond(wantsXml, 409, { error: "parts missing", missing });
      }
      await completeMultipart(row.storageKey, row.multipartUploadId, parts);
    } else if (stored !== row.sizeBytes) {
      return respond(wantsXml, 409, {
        error: `the stored object is ${stored} bytes, the file is ${row.sizeBytes}`,
      });
    }

    const started = await scoped(async (tx, scope) => {
      const [transitioned] = await tx
        .update(upload)
        .set({ completedAt: sql`now()`, status: "completed" })
        .where(and(eq(upload.id, row.id), eq(upload.status, "active")))
        .returning({ id: upload.id });
      if (!transitioned) {
        return null; // another caller finished the row first
      }
      await tx
        .update(source)
        .set({
          ingestWorkflowId: workflowId,
          status: "uploaded",
          uploadedAt: sql`now()`,
        })
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
    if (started) {
      const client = await getTemporalClient();
      await client.workflow.start(WORKFLOWS.ingest, {
        args: [started.input],
        taskQueue: TASK_QUEUES.pipeline,
        workflowExecutionTimeout: "12 hours",
        workflowId,
        workflowIdConflictPolicy: "USE_EXISTING",
      });
    }
  }

  return respond(wantsXml, 200, {
    key: row.storageKey,
    sourceId: row.sourceId,
    workflowId,
  });
}
