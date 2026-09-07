import type { TranscriptV1 } from "@temnia/contracts";

/**
 * Reading a revision in the browser.
 *
 * A revision is fetched from the org-scoped media proxy, which answers with an
 * ETag and honours `if-none-match`; the browser's own cache does the
 * revalidation, and a correction changes the key (`rev-{N}.json`) rather than
 * the contents, so a stale body is not a state that can happen.
 *
 * The body is not re-parsed through Zod here. It was validated against the
 * contract when it was written and again when the server read it back
 * (`queries.ts`), and a 2.5-hour episode is about 28 thousand words: paying for
 * a second full validation on the main thread would cost the reader more than
 * it could tell them. What is checked is the shape this module then indexes.
 */

export class TranscriptShapeError extends Error {}

function looksLikeTranscript(value: unknown): value is TranscriptV1 {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Partial<TranscriptV1>;
  return (
    candidate.version === 1 &&
    Array.isArray(candidate.words) &&
    Array.isArray(candidate.utterances) &&
    Array.isArray(candidate.speakers)
  );
}

export async function fetchRevision(
  url: string,
  signal?: AbortSignal
): Promise<TranscriptV1> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new TranscriptShapeError(
      `the transcript could not be read (${response.status})`
    );
  }
  const body: unknown = await response.json();
  if (!looksLikeTranscript(body)) {
    throw new TranscriptShapeError("the transcript is not in a shape we know");
  }
  return body;
}
