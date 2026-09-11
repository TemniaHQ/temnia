import { z } from "zod";
import { getTopicEditorialContext } from "@/lib/harness/queries";

export async function GET(
  request: Request,
  context: RouteContext<"/api/sources/[sourceId]/topics/context">
) {
  const { sourceId } = await context.params;
  const params = new URL(request.url).searchParams;
  const parsed = z
    .object({
      revision: z.coerce.number().int().positive(),
      runId: z.uuid(),
      sourceId: z.uuid(),
    })
    .safeParse({
      revision: params.get("revision"),
      runId: params.get("runId"),
      sourceId,
    });
  if (!parsed.success) {
    return Response.json({ message: "Not found" }, { status: 404 });
  }
  const result = await getTopicEditorialContext(
    parsed.data.sourceId,
    parsed.data.runId,
    parsed.data.revision
  );
  return result
    ? Response.json(result, {
        headers: { "Cache-Control": "private, no-store" },
      })
    : Response.json(
        { message: "The requested topic source and revision are unavailable." },
        { status: 404 }
      );
}
