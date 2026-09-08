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

/** A contiguous hit in the current revision's flat word array. */
export interface TranscriptMatch {
  /** Index of the first matching word, inclusive. */
  firstWord: number;
  /** Index of the last matching word, inclusive. */
  lastWord: number;
}

/** Unicode letters, marks and numbers; punctuation separates phrase terms. */
const SEARCH_TERM = /[\p{L}\p{M}\p{N}]+/gu;

function normalizeSearchText(text: string): string {
  // Compatibility composition makes full-width/canonical variants agree.
  // Lowercasing without a locale keeps the result stable between server and
  // browser while still applying Unicode's default case mappings.
  return text.normalize("NFKC").toLowerCase();
}

function phraseTerms(text: string): string[] {
  return normalizeSearchText(text).match(SEARCH_TERM) ?? [];
}

function compareMatches(a: TranscriptMatch, b: TranscriptMatch): number {
  return a.firstWord - b.firstWord || a.lastWord - b.lastWord;
}

function phrasePrefix(terms: readonly string[]): number[] {
  const prefix = new Array<number>(terms.length).fill(0);
  for (let index = 1, matched = 0; index < terms.length; index += 1) {
    while (matched > 0 && terms[index] !== terms[matched]) {
      matched = prefix[matched - 1] ?? 0;
    }
    if (terms[index] === terms[matched]) {
      matched += 1;
    }
    prefix[index] = matched;
  }
  return prefix;
}

/**
 * Every literal word hit and multi-term phrase hit, in transcript order.
 *
 * Literal matching preserves the reader's original behavior: a one-word
 * query can match inside a word and can include punctuation. Phrase matching
 * additionally compares Unicode-normalized terms, so spaces, punctuation and
 * typographic punctuation can separate terms without becoming part of the
 * match. A phrase can span display paragraphs; its result remains one word
 * span for the virtualized reader to scroll to and highlight.
 */
export function findMatches(
  words: readonly Pick<TranscriptWord, "text">[],
  query: string
): TranscriptMatch[] {
  const needle = normalizeSearchText(query.trim());
  if (!needle) {
    return [];
  }

  const matches: TranscriptMatch[] = [];
  for (const [index, word] of words.entries()) {
    if (normalizeSearchText(word.text).includes(needle)) {
      matches.push({ firstWord: index, lastWord: index });
    }
  }

  const wanted = phraseTerms(query);
  if (wanted.length < 2) {
    return matches;
  }

  const transcriptTerms = words.flatMap((word, wordIndex) =>
    phraseTerms(word.text).map((text) => ({ text, wordIndex }))
  );
  const prefix = phrasePrefix(wanted);
  for (let index = 0, matched = 0; index < transcriptTerms.length; index += 1) {
    while (matched > 0 && transcriptTerms[index]?.text !== wanted[matched]) {
      matched = prefix[matched - 1] ?? 0;
    }
    if (transcriptTerms[index]?.text === wanted[matched]) {
      matched += 1;
    }
    if (matched === wanted.length) {
      const start = index - wanted.length + 1;
      const firstWord = transcriptTerms[start]?.wordIndex;
      const lastWord = transcriptTerms[index]?.wordIndex;
      if (firstWord !== undefined && lastWord !== undefined) {
        matches.push({ firstWord, lastWord });
      }
      matched = prefix[matched - 1] ?? 0;
    }
  }

  matches.sort(compareMatches);
  return matches.filter(
    (match, index) =>
      index === 0 || compareMatches(match, matches[index - 1] ?? match) !== 0
  );
}
