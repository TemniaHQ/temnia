/**
 * The viewer's display rules.
 *
 * The same committed fixture the caption tests read (the recorded WhisperX
 * response for e2e/fixtures/speech-40s.mp4, normalised), plus hand-built words
 * for the boundaries a 40-second clip has no examples of.
 */
import {
  type TranscriptV1,
  TranscriptV1Schema,
  type TranscriptWord,
} from "@temnia/contracts";
import { describe, expect, it } from "vitest";
import {
  buildParagraphs,
  findMatches,
  PARAGRAPH_GAP_MS,
  PARAGRAPH_MAX_WORDS,
  paragraphAt,
  wordAt,
} from "@/lib/transcript/paragraphs";
import { deriveUtterances } from "@/lib/transcript/utterances";
import speech from "./fixtures/speech-40s.transcript.json" with {
  type: "json",
};

const TRANSCRIPT: TranscriptV1 = TranscriptV1Schema.parse(speech);

function word(
  text: string,
  startMs: number,
  endMs: number,
  speaker: string | null = "0"
): TranscriptWord {
  return { confidence: 0.9, endMs, speaker, startMs, text, timing: "aligned" };
}

function run(words: TranscriptWord[]) {
  return buildParagraphs(words, deriveUtterances(words));
}

describe("buildParagraphs", () => {
  it("covers every word exactly once, in order", () => {
    const paragraphs = buildParagraphs(TRANSCRIPT.words, TRANSCRIPT.utterances);
    const seen: number[] = [];
    for (const paragraph of paragraphs) {
      for (let i = paragraph.firstWord; i <= paragraph.lastWord; i += 1) {
        seen.push(i);
      }
    }
    expect(seen).toEqual(TRANSCRIPT.words.map((_, index) => index));
  });

  it("puts every paragraph inside one speaker turn", () => {
    const paragraphs = buildParagraphs(TRANSCRIPT.words, TRANSCRIPT.utterances);
    for (const paragraph of paragraphs) {
      const turn = TRANSCRIPT.utterances[paragraph.utteranceIndex];
      expect(paragraph.speaker).toBe(turn?.speaker);
      expect(paragraph.startMs).toBeGreaterThanOrEqual(turn?.startMs ?? 0);
      expect(paragraph.endMs).toBeLessThanOrEqual(turn?.endMs ?? 0);
    }
  });

  it("breaks on a speaker change", () => {
    const paragraphs = run([
      word("one", 0, 400, "0"),
      word("two", 400, 800, "0"),
      word("three", 800, 1200, "1"),
    ]);
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]?.speaker).toBe("0");
    expect(paragraphs[1]?.speaker).toBe("1");
    expect(paragraphs[1]?.utteranceIndex).toBe(1);
  });

  it("breaks on a pause longer than the gap and not on a shorter one", () => {
    const short = run([
      word("one", 0, 400),
      word("two", 400 + PARAGRAPH_GAP_MS, 400 + PARAGRAPH_GAP_MS + 100),
    ]);
    expect(short).toHaveLength(1);
    const long = run([
      word("one", 0, 400),
      word("two", 401 + PARAGRAPH_GAP_MS, 500 + PARAGRAPH_GAP_MS + 100),
    ]);
    expect(long).toHaveLength(2);
  });

  it("breaks a monologue at the word limit", () => {
    const words = Array.from({ length: PARAGRAPH_MAX_WORDS * 2 + 1 }, (_, i) =>
      word(`w${i}`, i * 100, i * 100 + 90)
    );
    const paragraphs = run(words);
    expect(paragraphs).toHaveLength(3);
    expect(paragraphs[0]?.lastWord).toBe(PARAGRAPH_MAX_WORDS - 1);
    expect(paragraphs[2]?.firstWord).toBe(PARAGRAPH_MAX_WORDS * 2);
  });

  it("has no paragraphs for a transcript with no words", () => {
    expect(buildParagraphs([], [])).toEqual([]);
  });
});

describe("wordAt", () => {
  const words = [
    word("one", 0, 400),
    word("two", 1000, 1400),
    word("three", 5000, 5400),
  ];

  it("is -1 before the first word starts", () => {
    expect(wordAt(words, -1)).toBe(-1);
  });

  it("finds the word covering the time", () => {
    expect(wordAt(words, 0)).toBe(0);
    expect(wordAt(words, 1200)).toBe(1);
    expect(wordAt(words, 5400)).toBe(2);
  });

  it("keeps the last word that started when the time is in a gap", () => {
    expect(wordAt(words, 900)).toBe(0);
    expect(wordAt(words, 60_000)).toBe(2);
  });

  it("agrees with a linear scan over the fixture", () => {
    for (let ms = 0; ms < TRANSCRIPT.durationMs; ms += 97) {
      let expected = -1;
      for (const [index, w] of TRANSCRIPT.words.entries()) {
        if (w.startMs <= ms) {
          expected = index;
        }
      }
      expect(wordAt(TRANSCRIPT.words, ms)).toBe(expected);
    }
  });

  it("is -1 for a transcript with no words", () => {
    expect(wordAt([], 1000)).toBe(-1);
  });
});

describe("paragraphAt", () => {
  const paragraphs = buildParagraphs(TRANSCRIPT.words, TRANSCRIPT.utterances);

  it("finds the paragraph holding each word", () => {
    for (const [index, paragraph] of paragraphs.entries()) {
      expect(paragraphAt(paragraphs, paragraph.firstWord)).toBe(index);
      expect(paragraphAt(paragraphs, paragraph.lastWord)).toBe(index);
    }
  });

  it("is -1 for no word and for a word past the end", () => {
    expect(paragraphAt(paragraphs, -1)).toBe(-1);
    expect(paragraphAt(paragraphs, TRANSCRIPT.words.length)).toBe(-1);
  });
});

describe("findMatches", () => {
  it("matches case-insensitively and counts every hit", () => {
    const hits = findMatches(TRANSCRIPT.words, "THE");
    expect(hits.length).toBeGreaterThan(1);
    for (const index of hits) {
      expect(TRANSCRIPT.words[index]?.text.toLowerCase()).toContain("the");
    }
  });

  it("matches inside a word, punctuation and all", () => {
    expect(findMatches([word("show.", 0, 1)], "how")).toEqual([0]);
    expect(findMatches([word("show.", 0, 1)], "show.")).toEqual([0]);
  });

  it("is empty for an empty query", () => {
    expect(findMatches(TRANSCRIPT.words, "   ")).toEqual([]);
  });
});
