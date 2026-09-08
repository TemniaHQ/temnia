/**
 * GET /api/sources/{sourceId}/transcript.srt: the transcript as SubRip, with
 * speaker names applied from the transcript row's labels.
 */
import type { NextRequest } from "next/server";
import { captionResponse } from "@/lib/transcript/export";

export async function GET(
  request: NextRequest,
  context: RouteContext<"/api/sources/[sourceId]/transcript.srt">
): Promise<Response> {
  const { sourceId } = await context.params;
  const revision = Number(request.nextUrl.searchParams.get("revision"));
  return captionResponse(
    sourceId,
    "srt",
    Number.isInteger(revision) && revision > 0 ? revision : undefined
  );
}
