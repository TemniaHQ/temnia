/**
 * POST /api/uploads/{uploadId}/sign: part URLs for the browser to PUT to.
 * Signing is the liveness signal (it touches last_activity_at); listing parts
 * never is, or an abandoned upload that gets looked at would never be reaped.
 */
import { upload } from "@temnia/db";
import { and, eq, sql } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { z } from "zod";
import { scoped } from "@/lib/db";
import { signPartUrls } from "@/lib/uploads/server";

const BodySchema = z.object({
  partNumbers: z.array(z.int().min(1).max(10_000)).min(1).max(64),
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
  const row = await scoped(async (tx) => {
    const [found] = await tx
      .update(upload)
      .set({ lastActivityAt: sql`now()` })
      .where(and(eq(upload.id, uploadId), eq(upload.status, "active")))
      .returning({
        key: upload.storageKey,
        multipartUploadId: upload.multipartUploadId,
      });
    return found;
  });
  if (!row) {
    return Response.json({ error: "upload not found" }, { status: 404 });
  }
  const urls = await signPartUrls(
    row.key,
    row.multipartUploadId,
    parsed.data.partNumbers
  );
  return Response.json({
    urls: parsed.data.partNumbers.map((partNumber, i) => ({
      partNumber,
      url: urls[i],
    })),
  });
}
