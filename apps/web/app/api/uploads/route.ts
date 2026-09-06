/**
 * POST /api/uploads: start, or adopt, a multipart upload for a project's master.
 *
 * The same file re-selected from any browser carries the same fingerprint
 * (name, size, lastModified). If an active upload with that fingerprint exists
 * and has been quiet for the grace period, the caller adopts it and receives
 * the parts the store already holds. The check runs inside the transaction
 * that would otherwise create a second row, and the partial unique index on
 * (organization, fingerprint) where active makes a duplicate unreachable.
 */
import { masterKey } from "@temnia/contracts";
import { project, source, upload } from "@temnia/db";
import { and, eq, sql } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { z } from "zod";
import { scoped } from "@/lib/db";
import {
  adoptGraceSeconds,
  createMultipart,
  fingerprintOf,
  listUploadedParts,
  missingParts,
  partCountFor,
  partSizeFor,
  type UploadedPart,
} from "@/lib/uploads/server";

const MAX_FILE_BYTES = 200 * 1024 * 1024 * 1024;
const MEDIA_TYPE = /^(video|audio)\//;
const EXTENSION = /\.[^.]+$/;

const BodySchema = z.object({
  lastModified: z.int().nonnegative(),
  name: z.string().min(1).max(512),
  projectId: z.uuid(),
  size: z.int().positive().max(MAX_FILE_BYTES),
  type: z.string().regex(MEDIA_TYPE, "only video and audio files are accepted"),
});

export interface UploadSession {
  partCount: number;
  partSize: number;
  resumed: boolean;
  sourceId: string;
  /** Parts the store already holds; the browser skips these. */
  uploaded: UploadedPart[];
  uploadId: string;
}

export async function POST(request: NextRequest): Promise<Response> {
  const parsed = BodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json(
      { error: parsed.error.issues[0]?.message ?? "invalid" },
      { status: 400 }
    );
  }
  const body = parsed.data;
  const fingerprint = fingerprintOf(
    body.projectId,
    body.name,
    body.size,
    body.lastModified
  );
  const partSize = partSizeFor(body.size);

  const session = await scoped(
    async (
      tx,
      scope
    ): Promise<UploadSession | { error: string; status: number }> => {
      const [owner] = await tx
        .select({ id: project.id })
        .from(project)
        .where(eq(project.id, body.projectId));
      if (!owner) {
        return { error: "project not found", status: 404 };
      }

      const [existing] = await tx
        .select({
          quiet: sql<boolean>`${upload.lastActivityAt} < now() - make_interval(secs => ${adoptGraceSeconds()})`,
          row: upload,
        })
        .from(upload)
        .where(
          and(eq(upload.fingerprint, fingerprint), eq(upload.status, "active"))
        )
        .limit(1);
      if (existing && !existing.quiet) {
        // Another browser is still signing parts for this file; adopting it
        // now would interleave two writers over the same part numbers.
        return { error: "an upload of this file is in progress", status: 409 };
      }
      if (existing) {
        const active = existing.row;
        const parts = await listUploadedParts(
          active.storageKey,
          active.multipartUploadId
        );
        if (parts === null) {
          // The store forgot it (lifecycle abort); the row is dead, start over.
          await tx
            .update(upload)
            .set({ status: "aborted" })
            .where(eq(upload.id, active.id));
          await tx.delete(source).where(eq(source.id, active.sourceId));
        } else {
          await tx
            .update(upload)
            .set({ lastActivityAt: sql`now()` })
            .where(eq(upload.id, active.id));
          return {
            partCount: partCountFor(active.sizeBytes, active.partSizeBytes),
            partSize: active.partSizeBytes,
            resumed: true,
            sourceId: active.sourceId,
            uploaded: parts.filter(
              (p) =>
                !missingParts(active.sizeBytes, active.partSizeBytes, [
                  p,
                ]).includes(p.partNumber)
            ),
            uploadId: active.id,
          };
        }
      }

      const [created] = await tx
        .insert(source)
        .values({
          contentType: body.type,
          createdBy: scope.userId,
          masterKey: "",
          organizationId: scope.organizationId,
          originalFilename: body.name,
          projectId: body.projectId,
          sizeBytes: body.size,
          status: "uploading",
          title: body.name.replace(EXTENSION, ""),
        })
        .returning({ id: source.id });
      if (!created) {
        return { error: "source not created", status: 500 };
      }
      const key = masterKey(scope.organizationId, created.id, body.name);
      await tx
        .update(source)
        .set({ masterKey: key })
        .where(eq(source.id, created.id));
      const multipartUploadId = await createMultipart(key, body.type);
      const [row] = await tx
        .insert(upload)
        .values({
          fingerprint,
          multipartUploadId,
          organizationId: scope.organizationId,
          partSizeBytes: partSize,
          sizeBytes: body.size,
          sourceId: created.id,
          storageKey: key,
        })
        .returning({ id: upload.id });
      if (!row) {
        return { error: "upload not created", status: 500 };
      }
      return {
        partCount: partCountFor(body.size, partSize),
        partSize,
        resumed: false,
        sourceId: created.id,
        uploaded: [],
        uploadId: row.id,
      };
    }
  );

  if ("error" in session) {
    return Response.json({ error: session.error }, { status: session.status });
  }
  return Response.json(session);
}
