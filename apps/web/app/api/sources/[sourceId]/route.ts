/**
 * GET /api/sources/{sourceId}: the source row as the page sees it. Used by
 * scripts/upload-master.mjs to follow an ingest without a browser.
 */
import { source } from "@temnia/db";
import { eq } from "drizzle-orm";
import type { NextRequest } from "next/server";
import { scoped } from "@/lib/db";

export async function GET(
  _request: NextRequest,
  context: RouteContext<"/api/sources/[sourceId]">
): Promise<Response> {
  const { sourceId } = await context.params;
  const row = await scoped(async (tx) => {
    const [found] = await tx
      .select({
        durationMs: source.durationMs,
        errorMessage: source.errorMessage,
        id: source.id,
        ingestPercent: source.ingestPercent,
        ingestStage: source.ingestStage,
        readyAt: source.readyAt,
        status: source.status,
        title: source.title,
        uploadedAt: source.uploadedAt,
      })
      .from(source)
      .where(eq(source.id, sourceId))
      .limit(1);
    return found;
  });
  if (!row) {
    return Response.json({ error: "not found" }, { status: 404 });
  }
  return Response.json(row);
}
