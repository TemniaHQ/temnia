/**
 * DELETE /api/uploads/{uploadId}: the user gave up. Abort at the store first,
 * then the row; the source row goes too, because it never became content.
 */
import { source, upload } from "@temnia/db";
import { and, eq } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { scoped } from "@/lib/db";
import { abortMultipart } from "@/lib/uploads/server";

export async function DELETE(
  _request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]">
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
  await abortMultipart(row.storageKey, row.multipartUploadId);
  await scoped(async (tx) => {
    await tx
      .update(upload)
      .set({ status: "aborted" })
      .where(eq(upload.id, row.id));
    await tx.delete(source).where(eq(source.id, row.sourceId));
  });
  return new Response(null, { status: 204 });
}
