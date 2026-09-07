import type { TranscriptV1, TranscriptWord } from "@temnia/contracts";

/**
 * Cues for SRT and VTT, built from the words.
 *
 * The rules are the legacy's, measured against real episodes and ported here
 * rather than pulled in with a captions library: a cue never spans two
 * speakers, never runs past the end of a sentence, never bridges a real
 * silence, and never gets so long or so wide that a player has to shrink it.
 * Thirty lines of serialiser is cheaper than a dependency that owns the rules.
 */

/** A cue ends at a sentence, allowing for a closing quote or bracket after the stop. */
const SENTENCE_END = /[.!?…]["')\]]?$/;

/** Longer than this between two words and they belong to different cues. */
export const SILENCE_BREAK_MS = 750;

/** Roughly two lines at a comfortable width. */
export const MAX_CUE_CHARS = 70;

/** No cue holds the screen longer than this, however long the sentence runs. */
export const MAX_CUE_MS = 7000;

export interface Cue {
  endMs: number;
  /** The display name, already resolved from the transcript's speaker labels. */
  speaker: string | null;
  startMs: number;
  text: string;
}

function cueText(words: readonly TranscriptWord[]): string {
  return words
    .map((word) => word.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/** The name a viewer reads for a speaker id, or the id, or nothing. */
export function speakerName(
  speaker: string | null,
  labels: Readonly<Record<string, string>>
): string | null {
  if (speaker === null) {
    return null;
  }
  const named = labels[speaker]?.trim();
  if (named) {
    return named;
  }
  return `Speaker ${Number.parseInt(speaker, 10) + 1 || speaker}`;
}

function shouldBreak(current: TranscriptWord[], word: TranscriptWord): boolean {
  const [first] = current;
  const previous = current.at(-1);
  if (!(first && previous)) {
    return false;
  }
  if (previous.speaker !== word.speaker) {
    return true;
  }
  if (SENTENCE_END.test(previous.text)) {
    return true;
  }
  if (word.startMs - previous.endMs > SILENCE_BREAK_MS) {
    return true;
  }
  if (cueText([...current, word]).length > MAX_CUE_CHARS) {
    return true;
  }
  return word.endMs - first.startMs > MAX_CUE_MS;
}

/**
 * Group a transcript's words into cues.
 *
 * A transcript with no words produces no cues, which is what an export of a
 * recording with no speech in it should be: an empty file, not an error.
 */
export function buildCues(
  transcript: Pick<TranscriptV1, "words">,
  labels: Readonly<Record<string, string>> = {}
): Cue[] {
  const cues: Cue[] = [];
  let current: TranscriptWord[] = [];

  const flush = () => {
    const [first] = current;
    if (!first) {
      return;
    }
    const text = cueText(current);
    if (text) {
      cues.push({
        endMs: current.at(-1)?.endMs ?? first.endMs,
        speaker: speakerName(first.speaker, labels),
        startMs: first.startMs,
        text,
      });
    }
    current = [];
  };

  for (const word of transcript.words) {
    if (shouldBreak(current, word)) {
      flush();
    }
    current.push(word);
  }
  flush();
  return cues;
}

function clock(ms: number, fractionSeparator: string): string {
  const total = Math.max(0, Math.round(ms));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  const millis = total % 1000;
  const pad = (value: number, width = 2) => String(value).padStart(width, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}${fractionSeparator}${pad(millis, 3)}`;
}

function line(cue: Cue): string {
  return cue.speaker ? `${cue.speaker}: ${cue.text}` : cue.text;
}

/** SubRip: one-based index, comma before the milliseconds, CRLF between blocks. */
export function toSrt(cues: readonly Cue[]): string {
  return cues
    .map(
      (cue, index) =>
        `${index + 1}\r\n${clock(cue.startMs, ",")} --> ${clock(cue.endMs, ",")}\r\n${line(cue)}\r\n`
    )
    .join("\r\n");
}

/** WebVTT: a header, a dot before the milliseconds, and a speaker as a voice span. */
export function toVtt(cues: readonly Cue[]): string {
  const blocks = cues.map((cue) => {
    const body = cue.speaker
      ? `<v ${cue.speaker.replace(/[<>]/g, "")}>${cue.text}`
      : cue.text;
    return `${clock(cue.startMs, ".")} --> ${clock(cue.endMs, ".")}\n${body}\n`;
  });
  return `WEBVTT\n\n${blocks.join("\n")}`;
}
