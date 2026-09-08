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
  format: CaptionFormat,
  revision?: number
): Promise<Response> {
  const state = await getTranscript(sourceId);
  if (!state?.current || state.row.status !== "ready") {
    return new Response(null, { status: 404 });
  }
  const selected = revision
    ? state.revisions.find((item) => item.revision === revision)
    : state.current;
  if (!selected) {
    return new Response(null, { status: 404 });
  }
  const content = await readRevision(selected.storageKey);
  const parsed = TranscriptRevisionAnnotationsSchema.safeParse(
    selected.metadata.annotations
  );
  const labels = parsed.success
    ? Object.fromEntries(
        Object.entries(parsed.data.speakerIdentities).map(([raw, identity]) => [
          raw,
          identity.label,
        ])
      )
    : state.row.speakerLabels;
  const cues = buildCues(content, labels);
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
      ETag: `"rev-${selected.revision}"`,
    },
    status: 200,
  });
}

import { TranscriptRevisionAnnotationsSchema } from "@temnia/contracts";
