// FROZEN COPY from Mitosia/mitosia-legacy `lib/intelligence/moments.ts` at
// commit b642b774b48ad45910acb168d8dad86796da7e64, trimmed on 2026-09-07 to the
// transitive closure `grid.ts` needs plus the word-timeline helpers the eval
// scorers read. Kept: SENTENCE_TERMINAL, DEFAULT_PAUSE_GAP_MS, sentenceStarts,
// pauseBoundaries, MsRange, sentenceStartTimes, sentenceEndTimes, the LEAD_IN_*
// caps, the single-turn captureLeadIn, speakerTurnStartTimes.
//
// Cut, because nothing in this directory calls them and they drag in the rest
// of the intelligence tree: SNAP_MAX_GROWTH_MS, SENSITIVE_RISK_THRESHOLD, the
// DEDUPE_* thresholds, MomentScores, MOMENT_COMPOSITE_WEIGHTS, compositeScore,
// snapDown/snapUp/snapToSentences, spanText, AnchorGrounding and
// groundMomentAnchor (the only importers of `./grounding`), the chunk/dedupe
// machinery, buildMomentRows, and dedupeAndRankMomentRows. The import of
// `alignExtraction`/`tokenizeWords` from `./grounding` went with them, which is
// why `grounding.ts` is not vendored here.
//
// The only other edit is the import path. Do not change the behaviour: the
// committed renderings under apps/pipeline/tests/fixtures/substrate/ are dumped
// from this code, and the Python port is asserted byte-for-byte against them.

import type { TranscriptWord } from "../transcription/types.ts";

// Deterministic post-processing for moment discovery (S6): the model
// proposes, this file disposes (D3). Boundary snapping to the sentence
// grid, anchor grounding through the S5 aligner, range/embedding dedupe,
// composite scoring, ranking — all pure functions, all unit-tested, no
// model judgment anywhere. Client-safe (the review UI's nudge buttons
// import the sentence-grid helpers).
//
// Boundary snapping is word-timeline math on purpose (AGENTS §Source
// intelligence, S5 finding): punctuated words are sentence boundaries and
// inter-word gaps are pause detection — no ffmpeg silence/energy pass.

// A word ends a sentence when it ends with terminal punctuation, optionally
// followed by closing quotes/brackets — the per-word form of scoreSummary's
// SENTENCE_END heuristic.
const SENTENCE_TERMINAL = /[.!?]["”'’)\]]*$/u;

// An inter-word gap this long reads as a pause a cut can land on.
export const DEFAULT_PAUSE_GAP_MS = 700;

// Word indices where a sentence begins: the first word, and every word
// whose predecessor ends with terminal punctuation.
export function sentenceStarts(words: readonly TranscriptWord[]): number[] {
  const starts: number[] = [];
  for (let index = 0; index < words.length; index += 1) {
    if (index === 0) {
      starts.push(0);
      continue;
    }
    const previous = words[index - 1];
    if (previous && SENTENCE_TERMINAL.test(previous.text)) {
      starts.push(index);
    }
  }
  return starts;
}

// Word indices preceded by an inter-word silence of at least minGapMs.
export function pauseBoundaries(
  words: readonly TranscriptWord[],
  minGapMs: number = DEFAULT_PAUSE_GAP_MS
): number[] {
  const boundaries: number[] = [];
  for (let index = 1; index < words.length; index += 1) {
    const previous = words[index - 1];
    const word = words[index];
    if (previous && word && word.startMs - previous.endMs >= minGapMs) {
      boundaries.push(index);
    }
  }
  return boundaries;
}

export interface MsRange {
  endMs: number;
  startMs: number;
}

// Snap-point times derived from the word timeline. A sentence START plays
// from its first word's startMs; a sentence END stops at its last word's
// endMs (the word before the next start, or the final word).
export function sentenceStartTimes(words: readonly TranscriptWord[]): number[] {
  return sentenceStarts(words)
    .map((index) => words[index]?.startMs)
    .filter((time): time is number => time !== undefined);
}

export function sentenceEndTimes(words: readonly TranscriptWord[]): number[] {
  const starts = sentenceStarts(words);
  const times: number[] = [];
  for (const start of starts) {
    if (start === 0) {
      continue;
    }
    const previous = words[start - 1];
    if (previous) {
      times.push(previous.endMs);
    }
  }
  const last = words.at(-1);
  if (last) {
    times.push(last.endMs);
  }
  return times;
}

// Lead-in capture (the Brett Lee finding, staging 2026-08-25): a moment
// that opens with one speaker answering owes its meaning to the short
// other-speaker turn right before it — the interviewer's question or
// setup. The model anchors on the answer (that's where the anchor text
// lives) and sentence-snapping cannot reason about conversation structure,
// so the span started mid-exchange and the clip opened without its
// context. Deterministic rule: when the immediately preceding turn is by a
// DIFFERENT speaker, short enough to be a prompt, and close enough to be
// part of the exchange, the span grows to include it.
export const LEAD_IN_MAX_TURN_MS = 20_000;
export const LEAD_IN_MAX_GAP_MS = 3000;

export function captureLeadIn(
  range: MsRange,
  words: readonly TranscriptWord[]
): MsRange {
  const openerIndex = words.findIndex((word) => word.startMs >= range.startMs);
  if (openerIndex <= 0) {
    return range;
  }
  const opener = words[openerIndex];
  const previous = words[openerIndex - 1];
  if (!(opener && previous)) {
    return range;
  }
  // Same speaker before the opener = mid-monologue start (snapping's job),
  // and unknown speakers give the rule nothing to reason with.
  if (
    opener.speaker === null ||
    previous.speaker === null ||
    previous.speaker === opener.speaker
  ) {
    return range;
  }
  if (opener.startMs - previous.endMs > LEAD_IN_MAX_GAP_MS) {
    return range;
  }
  // Walk back to where the preceding speaker's turn began.
  let turnStart = openerIndex - 1;
  while (turnStart > 0 && words[turnStart - 1]?.speaker === previous.speaker) {
    turnStart -= 1;
  }
  const first = words[turnStart];
  if (!first) {
    return range;
  }
  // A long preceding turn is the other speaker's own moment, not a setup.
  if (previous.endMs - first.startMs > LEAD_IN_MAX_TURN_MS) {
    return range;
  }
  return { endMs: range.endMs, startMs: first.startMs };
}

// Times where a speaker turn begins — lead-in starts land here, so they
// belong to the valid boundary grid alongside sentence starts.
export function speakerTurnStartTimes(
  words: readonly TranscriptWord[]
): number[] {
  const times: number[] = [];
  for (const [index, word] of words.entries()) {
    if (index === 0 || words[index - 1]?.speaker !== word.speaker) {
      times.push(word.startMs);
    }
  }
  return times;
}
