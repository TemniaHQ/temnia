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
 * this walk is how an edit addressed by utterance finds what to change. It
 * follows the same rule as `deriveUtterances`, a run of consecutive words by
 * one speaker, so the two agree by construction. Grouping by time did not:
 * two speakers whose first words start on the same millisecond gave the
 * first turn no words and the second both, and a reassignment then rewrote
 * somebody else's word (S2 review, I10).
 */
export function wordsByUtterance(
  words: readonly TranscriptWord[],
  utterances: readonly TranscriptUtterance[]
): number[][] {
  const groups: number[][] = utterances.map(() => []);
  let turn = -1;
  let speaker: string | null | undefined;
  for (const [index, word] of words.entries()) {
    if (turn < 0 || word.speaker !== speaker) {
      turn += 1;
      ({ speaker } = word);
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
