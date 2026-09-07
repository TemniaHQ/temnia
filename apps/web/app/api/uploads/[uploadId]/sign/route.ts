/**
 * POST /api/uploads/{uploadId}/sign: Uppy's signRequest, one URL per call.
 *
 * The body is Uppy's request shape ({ method, key, uploadId, partNumber }).
 * Only two operations reach the store from the browser: UploadPart (PUT) and
 * ListParts (GET). Complete and Abort are the app's own routes, and the
 * browser addresses those without signing. The key and the store's UploadId
 * must be the row's own; a browser cannot have a part signed for any other
 * object. Signing a part is the liveness signal (it touches
 * last_activity_at); listing parts never is, or an abandoned upload that gets
 * looked at would never be reaped.
 */
import { upload } from "@temnia/db";
import { and, eq, sql } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { signListPartsUrl, signPartUrl } from "@/lib/uploads/server";

const BodySchema = z.object({
  key: z.string().min(1),
  method: z.enum(["PUT", "GET", "POST", "DELETE"]),
  partNumber: z.int().min(1).max(10_000).optional(),
  uploadId: z.string().min(1).optional(),
});

export async function POST(
  request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]/sign">
): Promise<Response> {
  const { uploadId } = await context.params;
  const parsed = BodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: "invalid" }, { status: 400 });
  }
  const body = parsed.data;
  const isPart = body.method === "PUT" && body.partNumber !== undefined;
  const isList = body.method === "GET";
  if (!((isPart || isList) && body.uploadId)) {
    return Response.json(
      {
        error:
          "only parts and part listings are signed; create, complete, and abort are the app's own routes",
      },
      { status: 400 }
    );
  }

  const row = await scoped(async (tx) => {
    const where = and(eq(upload.id, uploadId), eq(upload.status, "active"));
    if (isPart) {
      const [found] = await tx
        .update(upload)
        .set({ lastActivityAt: sql`now()` })
        .where(where)
        .returning({
          key: upload.storageKey,
          multipartUploadId: upload.multipartUploadId,
        });
      return found;
    }
    const [found] = await tx
      .select({
        key: upload.storageKey,
        multipartUploadId: upload.multipartUploadId,
      })
      .from(upload)
      .where(where)
      .limit(1);
    return found;
  });
  if (!row) {
    return Response.json({ error: "upload not found" }, { status: 404 });
  }
  if (row.key !== body.key || row.multipartUploadId !== body.uploadId) {
    return Response.json(
      { error: "key does not belong to this upload" },
      { status: 400 }
    );
  }
  const url = isPart
    ? await signPartUrl(row.key, row.multipartUploadId, body.partNumber ?? 1)
    : await signListPartsUrl(row.key, row.multipartUploadId);
  return Response.json({ url });
}
