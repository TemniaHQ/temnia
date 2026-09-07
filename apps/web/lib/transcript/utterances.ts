import type { TranscriptUtterance, TranscriptWord } from "@temnia/contracts";

/**
 * Consecutive words of one speaker, collapsed into turns.
 *
 * The same rule the pipeline's normaliser applies, in the language that has to
 * re-apply it: reassigning one turn's speaker rewrites those words and the
 * turns around it have to be re-derived from the result, because two adjacent
 * turns that end up with the same speaker are one turn.
 */
export function deriveUtterances(
  words: readonly TranscriptWord[]
): TranscriptUtterance[] {
  const turns: TranscriptUtterance[] = [];
  for (const word of words) {
    const last = turns.at(-1);
    if (last && last.speaker === word.speaker) {
      last.endMs = Math.max(last.endMs, word.endMs);
      continue;
    }
    turns.push({
      endMs: word.endMs,
      speaker: word.speaker,
      startMs: word.startMs,
    });
  }
  return turns;
}

/**
 * The words belonging to each utterance, in order.
 *
 * Utterances are derived from the words rather than stored against them, so
 * this walk is how an edit addressed by utterance finds what to change. The
 * two arrays agree by construction: both come from `deriveUtterances`.
 */
export function wordsByUtterance(
  words: readonly TranscriptWord[],
  utterances: readonly TranscriptUtterance[]
): number[][] {
  const groups: number[][] = utterances.map(() => []);
  let turn = 0;
  for (const [index, word] of words.entries()) {
    // Advanced on the next turn's start, not on this turn's end. A turn ends
    // at its last word's `endMs`, and the next speaker's first word can begin
    // on exactly that millisecond; a test against the end leaves that word in
    // the turn before it and sends a speaker reassignment to the wrong words.
    while (
      turn < utterances.length - 1 &&
      word.startMs >=
        (utterances[turn + 1]?.startMs ?? Number.POSITIVE_INFINITY)
    ) {
      turn += 1;
    }
    groups[turn]?.push(index);
  }
  return groups;
}

/** Every speaker id a word uses, in first-appearance order. */
export function speakersOf(words: readonly TranscriptWord[]): string[] {
  const seen: string[] = [];
  for (const word of words) {
    if (word.speaker !== null && !seen.includes(word.speaker)) {
      seen.push(word.speaker);
    }
  }
  return seen;
}
