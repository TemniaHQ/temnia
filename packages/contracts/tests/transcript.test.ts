/**
 * The transcript contract's refinements.
 *
 * These checks do not cross into JSON Schema (Zod drops refinements when it
 * emits one), so the pipeline re-states them in
 * `temnia_pipeline/transcription/normalize.py` and its tests. This suite is
 * the TypeScript half of that pair: what a correction writes back must be
 * refused here on exactly the same grounds.
 */
import { describe, expect, it } from "vitest";
import {
  DURATION_SLACK_MS,
  type TranscriptV1,
  TranscriptV1Schema,
  TranscriptWordSchema,
  transcriptRawKey,
  transcriptRevisionKey,
} from "../src/index.ts";

const PREFIX = "org/0192e8a0-0000-7000-8000-000000000001/source/abc/";

function word(
  startMs: number,
  endMs: number,
  text = "word",
  speaker: string | null = "0"
) {
  return {
    confidence: 0.9,
    endMs,
    speaker,
    startMs,
    text,
    timing: "aligned" as const,
  };
}

const TRANSCRIPT: TranscriptV1 = {
  durationMs: 10_000,
  language: "en",
  provider: { model: "large-v3", name: "whisperx", version: "3.8.6" },
  speakers: ["0"],
  utterances: [{ endMs: 2000, speaker: "0", startMs: 0 }],
  version: 1,
  words: [word(0, 500, "hello"), word(600, 2000, "there")],
};

describe("the transcript contract", () => {
  it("accepts a well-formed transcript", () => {
    expect(TranscriptV1Schema.parse(TRANSCRIPT)).toEqual(TRANSCRIPT);
  });

  it("refuses a word that ends before it starts", () => {
    expect(TranscriptWordSchema.safeParse(word(900, 400)).success).toBe(false);
  });

  it("refuses fractional milliseconds", () => {
    expect(TranscriptWordSchema.safeParse(word(0.5, 400)).success).toBe(false);
  });

  it("refuses a confidence outside zero and one", () => {
    expect(
      TranscriptWordSchema.safeParse({ ...word(0, 1), confidence: 1.2 }).success
    ).toBe(false);
  });

  it("accepts a word with no speaker and no confidence", () => {
    expect(
      TranscriptWordSchema.safeParse({
        ...word(0, 1, "um", null),
        confidence: null,
        timing: "interpolated",
      }).success
    ).toBe(true);
  });

  it("refuses words out of start order", () => {
    const result = TranscriptV1Schema.safeParse({
      ...TRANSCRIPT,
      words: [word(600, 2000), word(0, 500)],
    });
    expect(result.success).toBe(false);
  });

  it("refuses a word that ends after the recording plus the slack", () => {
    const result = TranscriptV1Schema.safeParse({
      ...TRANSCRIPT,
      words: [word(0, 10_000 + DURATION_SLACK_MS + 1)],
    });
    expect(result.success).toBe(false);
  });

  it("allows a word inside the slack, because a container rounds", () => {
    const result = TranscriptV1Schema.safeParse({
      ...TRANSCRIPT,
      words: [word(0, 10_000 + DURATION_SLACK_MS)],
    });
    expect(result.success).toBe(true);
  });

  it("allows a transcript that stops long before the recording does", () => {
    const result = TranscriptV1Schema.safeParse({
      ...TRANSCRIPT,
      durationMs: 9_000_000,
    });
    expect(result.success).toBe(true);
  });

  it("allows an empty transcript, which is what no speech looks like", () => {
    const result = TranscriptV1Schema.safeParse({
      ...TRANSCRIPT,
      speakers: [],
      utterances: [],
      words: [],
    });
    expect(result.success).toBe(true);
  });

  it("pins the revision and raw keys under the source prefix", () => {
    expect(transcriptRevisionKey(PREFIX, 2)).toBe(
      `${PREFIX}transcript/rev-2.json`
    );
    expect(transcriptRawKey(PREFIX, 1)).toBe(`${PREFIX}transcript/raw-1.json`);
  });
});
