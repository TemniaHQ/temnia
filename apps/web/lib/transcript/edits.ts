import type { TranscriptV1 } from "@temnia/contracts";
import { z } from "zod";
import {
  deriveUtterances,
  speakersOf,
  wordsByUtterance,
} from "@/lib/transcript/utterances";

/**
 * What a correction may change, and what changing it does to the rest.
 *
 * Kept apart from the server action so the rules are testable without a
 * database, a store, or Next's request context: everything here is a pure
 * function of a revision and an edit.
 */

/** One word's text replaced. Bounded, and never emptied: a deleted word is not an edit. */
export const WordEditSchema = z.object({
  index: z.int().nonnegative(),
  text: z.string().trim().min(1).max(200),
});

/** One turn reassigned to a speaker. */
export const UtteranceEditSchema = z.object({
  speaker: z.string().min(1).max(16),
  utteranceIndex: z.int().nonnegative(),
});

export const EditsSchema = z.union([
  z.array(WordEditSchema).min(1).max(200),
  UtteranceEditSchema,
]);

export type TranscriptEdits = z.infer<typeof EditsSchema>;

/** The message a viewer reads when someone else saved first. */
export const STALE_REVISION_MESSAGE =
  "Someone changed this transcript since you opened it. Reload to see the latest.";

/**
 * Apply an edit to a revision in place, or say why it does not apply.
 *
 * An edit addresses words and turns by position, which is only meaningful
 * against the revision it was made on. The caller checks that separately, and
 * this is the second line: an index the revision does not have is refused
 * rather than silently skipped.
 */
export function applyEdits(
  content: TranscriptV1,
  edits: TranscriptEdits
): string | null {
  const failure = Array.isArray(edits)
    ? applyWordEdits(content, edits)
    : applyUtteranceEdit(content, edits);
  if (failure) {
    return failure;
  }
  // Reassigning a turn can leave it sharing a speaker with the turn beside it,
  // and two adjacent turns of one speaker are one turn. Both derived lists are
  // rebuilt from the words, which are the only thing an edit changes.
  content.utterances = deriveUtterances(content.words);
  content.speakers = speakersOf(content.words);
  return null;
}

function applyWordEdits(
  content: TranscriptV1,
  edits: readonly z.infer<typeof WordEditSchema>[]
): string | null {
  for (const edit of edits) {
    const word = content.words[edit.index];
    if (!word) {
      return "that word is no longer in this transcript";
    }
    word.text = edit.text;
  }
  return null;
}

function applyUtteranceEdit(
  content: TranscriptV1,
  edit: z.infer<typeof UtteranceEditSchema>
): string | null {
  const group = wordsByUtterance(content.words, content.utterances)[
    edit.utteranceIndex
  ];
  if (!group || group.length === 0) {
    return "that speaker turn is no longer in this transcript";
  }
  for (const index of group) {
    const word = content.words[index];
    if (word) {
      word.speaker = edit.speaker;
    }
  }
  return null;
}
