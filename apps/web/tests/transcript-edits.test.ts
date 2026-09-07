/**
 * What a correction may change, and what changing it does to the rest.
 *
 * Runs on the same committed revision the exports are tested against, so an
 * edit here is an edit to the bytes a real correction would load.
 */
import { type TranscriptV1, TranscriptV1Schema } from "@temnia/contracts";
import { beforeEach, describe, expect, it } from "vitest";
import { applyEdits, EditsSchema } from "@/lib/transcript/edits";
import {
  deriveUtterances,
  wordsByUtterance,
} from "@/lib/transcript/utterances";
import speech from "./fixtures/speech-40s.transcript.json" with {
  type: "json",
};

let content: TranscriptV1;

beforeEach(() => {
  content = TranscriptV1Schema.parse(structuredClone(speech));
});

describe("word edits", () => {
  it("replaces the text and leaves the timing alone", () => {
    const before = { ...content.words[3] };
    expect(applyEdits(content, [{ index: 3, text: "programme" }])).toBeNull();
    expect(content.words[3]?.text).toBe("programme");
    expect(content.words[3]?.startMs).toBe(before.startMs);
    expect(content.words[3]?.endMs).toBe(before.endMs);
    expect(content.words[3]?.speaker).toBe(before.speaker);
  });

  it("applies several at once", () => {
    applyEdits(content, [
      { index: 0, text: "Welcome," },
      { index: 1, text: "back" },
    ]);
    expect(content.words.slice(0, 2).map((w) => w.text)).toEqual([
      "Welcome,",
      "back",
    ]);
  });

  it("refuses an index the revision does not have", () => {
    expect(applyEdits(content, [{ index: 9999, text: "no" }])).toContain(
      "no longer in this transcript"
    );
  });

  it("still produces a transcript the contract accepts", () => {
    applyEdits(content, [{ index: 5, text: "team" }]);
    expect(TranscriptV1Schema.safeParse(content).success).toBe(true);
  });
});

describe("a speaker reassignment", () => {
  it("rewrites every word of that turn and nothing else", () => {
    const groups = wordsByUtterance(content.words, content.utterances);
    const turn = groups[1] ?? [];
    expect(turn.length).toBeGreaterThan(0);
    const untouched = content.words[(groups[3] ?? [])[0] ?? 0]?.speaker;
    expect(applyEdits(content, { speaker: "0", utteranceIndex: 1 })).toBeNull();
    for (const index of turn) {
      expect(content.words[index]?.speaker).toBe("0");
    }
    expect(content.words[(groups[3] ?? [])[0] ?? 0]?.speaker).toBe(untouched);
  });

  it("re-derives the turns, so three become one when the middle changes hands", () => {
    const before = content.utterances.length;
    applyEdits(content, { speaker: "0", utteranceIndex: 1 });
    // Turns 0, 1 and 2 are now all speaker 0 and collapse into one.
    expect(content.utterances.length).toBe(before - 2);
    expect(content.utterances[0]?.speaker).toBe("0");
  });

  it("re-derives the speaker list, dropping one that no longer speaks", () => {
    for (let index = 1; index < 8; index += 2) {
      applyEdits(content, { speaker: "0", utteranceIndex: 1 });
    }
    expect(content.speakers).toEqual(["0"]);
    expect(content.utterances).toHaveLength(1);
  });

  it("keeps a word that starts on the previous turn's last millisecond", () => {
    // Two speakers with no silence between them: the second turn's first word
    // begins exactly when the first turn ends. Grouping on the turn's end
    // leaves that word in the turn before it, and the reassignment then
    // rewrites a word belonging to somebody else.
    const words: TranscriptV1["words"] = [
      {
        confidence: 1,
        endMs: 800,
        speaker: "0",
        startMs: 0,
        text: "one",
        timing: "aligned",
      },
      {
        confidence: 1,
        endMs: 1200,
        speaker: "1",
        startMs: 800,
        text: "two",
        timing: "aligned",
      },
    ];
    expect(wordsByUtterance(words, deriveUtterances(words))).toEqual([[0], [1]]);
  });

  it("refuses a turn the revision does not have", () => {
    expect(applyEdits(content, { speaker: "0", utteranceIndex: 99 })).toContain(
      "no longer in this transcript"
    );
  });

  it("still produces a transcript the contract accepts", () => {
    applyEdits(content, { speaker: "2", utteranceIndex: 0 });
    expect(TranscriptV1Schema.safeParse(content).success).toBe(true);
  });
});

describe("what an edit may be", () => {
  it("refuses an empty word, because a deletion is not an edit", () => {
    expect(EditsSchema.safeParse([{ index: 0, text: "   " }]).success).toBe(
      false
    );
  });

  it("bounds the text a word may be replaced with", () => {
    expect(
      EditsSchema.safeParse([{ index: 0, text: "x".repeat(201) }]).success
    ).toBe(false);
  });

  it("bounds how many words one save may touch", () => {
    const many = Array.from({ length: 201 }, (_, index) => ({
      index,
      text: "x",
    }));
    expect(EditsSchema.safeParse(many).success).toBe(false);
  });

  it("refuses an empty batch", () => {
    expect(EditsSchema.safeParse([]).success).toBe(false);
  });

  it("refuses a negative index", () => {
    expect(EditsSchema.safeParse([{ index: -1, text: "x" }]).success).toBe(
      false
    );
  });

  it("accepts one speaker reassignment", () => {
    expect(
      EditsSchema.safeParse({ speaker: "1", utteranceIndex: 0 }).success
    ).toBe(true);
  });

  it("bounds the speaker id, which is a diarization label and not a name", () => {
    expect(
      EditsSchema.safeParse({
        speaker: "x".repeat(17),
        utteranceIndex: 0,
      }).success
    ).toBe(false);
  });
});
