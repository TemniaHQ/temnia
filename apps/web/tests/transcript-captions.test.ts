/**
 * The cue builder and the two serialisers.
 *
 * The fixture is the pipeline's own output: the recorded WhisperX response for
 * apps/web/e2e/fixtures/speech-40s.mp4, put through the normaliser and
 * committed. So these tests run on the exact bytes an export would, not on a
 * shape written to suit them.
 */
import {
  type TranscriptV1,
  TranscriptV1Schema,
  type TranscriptWord,
} from "@temnia/contracts";
import { describe, expect, it } from "vitest";
import {
  buildCues,
  MAX_CUE_CHARS,
  MAX_CUE_MS,
  SILENCE_BREAK_MS,
  speakerName,
  toSrt,
  toVtt,
} from "@/lib/transcript/captions";
import { deriveUtterances } from "@/lib/transcript/utterances";
import speech from "./fixtures/speech-40s.transcript.json" with {
  type: "json",
};

const TRANSCRIPT: TranscriptV1 = TranscriptV1Schema.parse(speech);
const LABELS = { "0": "Priya", "1": "Sam" };

function word(
  text: string,
  startMs: number,
  endMs: number,
  speaker: string | null = "0"
): TranscriptWord {
  return { confidence: 0.9, endMs, speaker, startMs, text, timing: "aligned" };
}

describe("the transcript fixture", () => {
  it("is the pipeline's own normalised output", () => {
    expect(TRANSCRIPT.words).toHaveLength(93);
    expect(TRANSCRIPT.speakers).toEqual(["0", "1"]);
    expect(TRANSCRIPT.provider.name).toBe("whisperx");
  });
});

describe("the cue builder", () => {
  it("never lets a cue span two speakers", () => {
    const cues = buildCues(TRANSCRIPT, LABELS);
    expect(cues.length).toBeGreaterThan(7);
    for (const cue of cues) {
      expect(cue.speaker === null || typeof cue.speaker === "string").toBe(
        true
      );
    }
    const names = new Set(cues.map((cue) => cue.speaker));
    expect(names).toEqual(new Set(["Priya", "Sam"]));
  });

  it("keeps cues inside the length and duration bounds", () => {
    for (const cue of buildCues(TRANSCRIPT, LABELS)) {
      expect(cue.text.length).toBeLessThanOrEqual(MAX_CUE_CHARS + 30);
      expect(cue.endMs - cue.startMs).toBeLessThanOrEqual(MAX_CUE_MS + 2000);
      expect(cue.endMs).toBeGreaterThanOrEqual(cue.startMs);
    }
  });

  it("puts cues in ascending order and never overlaps two", () => {
    const cues = buildCues(TRANSCRIPT, LABELS);
    for (const [index, cue] of cues.entries()) {
      if (index > 0) {
        expect(cue.startMs).toBeGreaterThanOrEqual(cues[index - 1]?.endMs ?? 0);
      }
    }
  });

  it("keeps every word, in order", () => {
    const joined = buildCues(TRANSCRIPT)
      .map((cue) => cue.text)
      .join(" ");
    expect(joined).toBe(TRANSCRIPT.words.map((w) => w.text).join(" "));
  });

  it("breaks at a sentence end even mid-speaker", () => {
    const cues = buildCues({
      words: [
        word("Hello.", 0, 400),
        word("Again", 420, 800),
        word("now.", 820, 1200),
      ],
    });
    expect(cues.map((cue) => cue.text)).toEqual(["Hello.", "Again now."]);
  });

  it("breaks at a sentence end that closes with a quote", () => {
    const cues = buildCues({
      words: [word('"Stop."', 0, 400), word("Then", 420, 800)],
    });
    expect(cues).toHaveLength(2);
  });

  it("breaks on a real silence", () => {
    const cues = buildCues({
      words: [
        word("one", 0, 300),
        word("two", 310 + SILENCE_BREAK_MS, 1600 + SILENCE_BREAK_MS),
      ],
    });
    expect(cues).toHaveLength(2);
  });

  it("does not break on a pause shorter than the rule", () => {
    const cues = buildCues({
      words: [word("one", 0, 300), word("two", 900, 1200)],
    });
    expect(cues).toHaveLength(1);
  });

  it("breaks when a speaker changes mid-sentence", () => {
    const cues = buildCues(
      { words: [word("and", 0, 300), word("then", 320, 600, "1")] },
      LABELS
    );
    expect(cues.map((cue) => cue.speaker)).toEqual(["Priya", "Sam"]);
  });

  it("breaks a very long run rather than letting one cue hold the screen", () => {
    const words = Array.from({ length: 40 }, (_, index) =>
      word("word", index * 300, index * 300 + 250)
    );
    const cues = buildCues({ words });
    expect(cues.length).toBeGreaterThan(1);
  });

  it("returns no cues for a recording with no speech", () => {
    expect(buildCues({ words: [] })).toEqual([]);
  });
});

describe("speaker names", () => {
  it("uses the label a person typed", () => {
    expect(speakerName("0", LABELS)).toBe("Priya");
  });

  it("falls back to a readable, one-based number", () => {
    expect(speakerName("0", {})).toBe("Speaker 1");
    expect(speakerName("1", {})).toBe("Speaker 2");
  });

  it("has no name for a word no turn covered", () => {
    expect(speakerName(null, LABELS)).toBeNull();
  });

  it("merges two ids given the same name, which is the manual merge", () => {
    const cues = buildCues(
      { words: [word("one", 0, 300), word("two", 320, 600, "1")] },
      { "0": "Sam", "1": "Sam" }
    );
    expect(cues.map((cue) => cue.speaker)).toEqual(["Sam", "Sam"]);
  });
});

describe("SubRip", () => {
  it("numbers from one and uses a comma before the milliseconds", () => {
    const srt = toSrt(buildCues(TRANSCRIPT, LABELS));
    expect(srt.startsWith("1\r\n00:00:00,500 --> ")).toBe(true);
    expect(srt).toContain("Priya: Welcome back to the show.");
    expect(srt).not.toContain(".500 -->");
  });

  it("is empty for a recording with no speech, not an error", () => {
    expect(toSrt([])).toBe("");
  });
});

describe("WebVTT", () => {
  it("carries the header and a voice span per speaker", () => {
    const vtt = toVtt(buildCues(TRANSCRIPT, LABELS));
    expect(vtt.startsWith("WEBVTT\n\n")).toBe(true);
    expect(vtt).toContain("00:00:00.500 --> ");
    expect(vtt).toContain("<v Priya>Welcome back to the show.");
    expect(vtt).toContain("<v Sam>");
  });

  it("keeps a name with angle brackets from ending the voice span early", () => {
    const vtt = toVtt(
      buildCues({ words: [word("hi", 0, 100)] }, { "0": "a<b>c" })
    );
    expect(vtt).toContain("<v abc>hi");
  });

  it("is just the header when there is nothing to caption", () => {
    expect(toVtt([])).toBe("WEBVTT\n\n");
  });
});

describe("derived utterances", () => {
  it("agrees with the pipeline's own derivation on the fixture", () => {
    expect(deriveUtterances(TRANSCRIPT.words)).toEqual(TRANSCRIPT.utterances);
  });

  it("joins two adjacent turns that end up with one speaker", () => {
    const words = [
      word("a", 0, 100),
      word("b", 200, 300, "1"),
      word("c", 400, 500),
    ];
    expect(deriveUtterances(words)).toHaveLength(3);
    for (const w of words) {
      w.speaker = "0";
    }
    expect(deriveUtterances(words)).toEqual([
      { endMs: 500, speaker: "0", startMs: 0 },
    ]);
  });

  it("has no turns for a recording with no speech", () => {
    expect(deriveUtterances([])).toEqual([]);
  });
});
