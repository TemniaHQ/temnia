import { z } from "zod";
import { getTranscriptRevisionAnnotations } from "@/lib/transcript/queries";

export async function GET(
  request: Request,
  context: RouteContext<"/api/sources/[sourceId]/transcript-annotations">
) {
  const { sourceId } = await context.params;
  const revision = Number(new URL(request.url).searchParams.get("revision"));
  if (
    !(
      z.uuid().safeParse(sourceId).success &&
      z.int().positive().safeParse(revision).success
    )
  ) {
    return Response.json({ message: "Not found" }, { status: 404 });
  }
  const state = await getTranscriptRevisionAnnotations(sourceId, revision);
  return state
    ? Response.json(state, {
        headers: { "Cache-Control": "private, no-store" },
      })
    : Response.json({ message: "Not found" }, { status: 404 });
}
