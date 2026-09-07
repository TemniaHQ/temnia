import type { TranscriptUtterance, TranscriptWord } from "@temnia/contracts";
import { wordsByUtterance } from "@/lib/transcript/utterances";

/**
 * The viewer's display rules, apart from the viewer.
 *
 * A transcript is a flat array of words; a reader needs paragraphs. The rules
 * are the legacy viewer's, measured against real episodes: a paragraph never
 * spans two speakers, never bridges a long pause, and never grows past the
 * point where the row is taller than the pane. Everything here is a pure
 * function of a revision, so the rules are tested without a browser.
 */

/** Longer than this between two words and the reader has lost the thread. */
export const PARAGRAPH_GAP_MS = 2500;

/** About a minute of speech. A row taller than the pane cannot be scrolled to. */
export const PARAGRAPH_MAX_WORDS = 120;

/** Below this a word is shown as uncertain, with its score in a tooltip. */
export const LOW_CONFIDENCE = 0.5;

export interface Paragraph {
  endMs: number;
  /** Index into the flat word array of the first word, inclusive. */
  firstWord: number;
  /** Index of the last word, inclusive. */
  lastWord: number;
  speaker: string | null;
  startMs: number;
  /** Which turn this paragraph is part of; what a speaker reassignment edits. */
  utteranceIndex: number;
}

/**
 * Split the words into display paragraphs.
 *
 * Built out of the turns rather than beside them: a paragraph is always inside
 * exactly one utterance, which is what lets the speaker chip on a paragraph
 * send `utteranceIndex` to `correctTranscript` and mean it. The speaker-change
 * rule is therefore the turn boundary itself, and only the pause and the
 * length rules are applied within a turn.
 */
export function buildParagraphs(
  words: readonly TranscriptWord[],
  utterances: readonly TranscriptUtterance[]
): Paragraph[] {
  const paragraphs: Paragraph[] = [];
  const groups = wordsByUtterance(words, utterances);
  for (const [utteranceIndex, group] of groups.entries()) {
    let open: Paragraph | null = null;
    let length = 0;
    for (const index of group) {
      const word = words[index];
      if (!word) {
        continue;
      }
      if (
        open === null ||
        length >= PARAGRAPH_MAX_WORDS ||
        word.startMs - open.endMs > PARAGRAPH_GAP_MS
      ) {
        open = {
          endMs: word.endMs,
          firstWord: index,
          lastWord: index,
          speaker: word.speaker,
          startMs: word.startMs,
          utteranceIndex,
        };
        length = 1;
        paragraphs.push(open);
        continue;
      }
      open.endMs = Math.max(open.endMs, word.endMs);
      open.lastWord = index;
      length += 1;
    }
  }
  return paragraphs;
}

/**
 * The word the playhead is in, by binary search.
 *
 * The last word that had started by `ms`, which is -1 before the first word.
 * A gap keeps the word before it rather than highlighting nothing: during a
 * pause the reader still wants to see where the recording has got to, and a
 * highlight that blinks off between every sentence is worse than one that
 * lingers.
 */
export function wordAt(
  words: readonly Pick<TranscriptWord, "startMs">[],
  ms: number
): number {
  let low = 0;
  let high = words.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if ((words[mid]?.startMs ?? 0) <= ms) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

/** The paragraph holding a word index, by binary search, or -1. */
export function paragraphAt(
  paragraphs: readonly Paragraph[],
  wordIndex: number
): number {
  if (wordIndex < 0) {
    return -1;
  }
  let low = 0;
  let high = paragraphs.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if ((paragraphs[mid]?.firstWord ?? 0) <= wordIndex) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found >= 0 && (paragraphs[found]?.lastWord ?? -1) >= wordIndex
    ? found
    : -1;
}

/**
 * Every word whose text contains the query, case-insensitively.
 *
 * Per word, not across words: the words are what the viewer can scroll to and
 * highlight, and a phrase that straddles two of them has no single place to
 * put the focus ring. Semantic and phrase search arrive with embeddings at S7.
 */
export function findMatches(
  words: readonly Pick<TranscriptWord, "text">[],
  query: string
): number[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return [];
  }
  const matches: number[] = [];
  for (const [index, word] of words.entries()) {
    if (word.text.toLowerCase().includes(needle)) {
      matches.push(index);
    }
  }
  return matches;
}
