import { buildCues, toSrt, toVtt } from "@/lib/transcript/captions";
import { getTranscript, readRevision } from "@/lib/transcript/queries";

/**
 * The shared half of the two caption routes.
 *
 * Scope-resolved, and 404 for everything that is not a ready transcript in the
 * caller's own organization: a foreign source and a missing one answer
 * identically, so the export does not confirm that someone else's source id
 * exists. Never 403 (AGENTS.md, media).
 */

const FORMATS = {
  srt: { contentType: "application/x-subrip; charset=utf-8", render: toSrt },
  vtt: { contentType: "text/vtt; charset=utf-8", render: toVtt },
} as const;

export type CaptionFormat = keyof typeof FORMATS;

/** A title safe in a Content-Disposition filename, without eating the extension. */
function downloadName(title: string, format: CaptionFormat): string {
  const cleaned = title
    .replace(/[^\w \-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return `${cleaned || "transcript"}.${format}`;
}

export async function captionResponse(
  sourceId: string,
  format: CaptionFormat
): Promise<Response> {
  const state = await getTranscript(sourceId);
  if (!state?.current || state.row.status !== "ready") {
    return new Response(null, { status: 404 });
  }
  const content = await readRevision(state.current.storageKey);
  const cues = buildCues(content, state.row.speakerLabels);
  const { contentType, render } = FORMATS[format];
  return new Response(render(cues), {
    headers: {
      // Private: a transcript is tenant data and must not sit in a shared cache.
      "Cache-Control": "private, no-store",
      // Named after the source, so a folder of exports is readable.
      "Content-Disposition": `attachment; filename="${downloadName(state.sourceTitle, format)}"`,
      "Content-Type": contentType,
      // A correction writes a new revision and the number is in the tag, so a
      // cached export is only ever the export of the revision it names.
      ETag: `"rev-${state.current.revision}"`,
    },
    status: 200,
  });
}
