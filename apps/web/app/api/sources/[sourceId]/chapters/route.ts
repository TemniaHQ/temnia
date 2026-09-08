import { z } from "zod";
import { getChapterView } from "@/lib/harness/queries";

export async function GET(
  request: Request,
  context: RouteContext<"/api/sources/[sourceId]/chapters">
) {
  const { sourceId } = await context.params;
  if (!z.uuid().safeParse(sourceId).success) {
    return Response.json({ message: "Not found" }, { status: 404 });
  }
  const runId = new URL(request.url).searchParams.get("runId") ?? undefined;
  const mutationKey =
    new URL(request.url).searchParams.get("mutationKey") ?? undefined;
  if (runId && !z.uuid().safeParse(runId).success) {
    return Response.json({ message: "Not found" }, { status: 404 });
  }
  if (mutationKey && !z.uuid().safeParse(mutationKey).success) {
    return Response.json({ message: "Not found" }, { status: 404 });
  }
  return Response.json(await getChapterView(sourceId, runId, mutationKey), {
    headers: { "Cache-Control": "private, no-store" },
  });
}
