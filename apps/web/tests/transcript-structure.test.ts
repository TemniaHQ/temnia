import { describe, expect, it } from "vitest";
import {
  applyStructuralCorrection,
  legacyAnnotations,
} from "@/lib/transcript/structure";

const TRANSCRIPT_ID = "01992ffe-0a00-7000-8000-000000000001";
const MUTATION = "01992ffe-0a00-7000-8000-000000000002";

function content() {
  return {
    durationMs: 3000,
    language: "en",
    provider: { model: "fixture", name: "recorded", version: "1" },
    speakers: ["a", "b"],
    utterances: [],
    version: 1 as const,
    words: [
      {
        confidence: 0.9,
        endMs: 1000,
        speaker: "a",
        startMs: 0,
        text: "one",
        timing: "aligned" as const,
      },
      {
        confidence: 0.8,
        endMs: 2000,
        speaker: "a",
        startMs: 1000,
        text: "two",
        timing: "aligned" as const,
      },
      {
        confidence: 0.7,
        endMs: 3000,
        speaker: "b",
        startMs: 2000,
        text: "three",
        timing: "aligned" as const,
      },
    ],
  };
}

describe("structural transcript corrections", () => {
  it("derives stable legacy identities and permits an explicit empty transcript", () => {
    const original = content();
    const defaultAnnotations = legacyAnnotations(TRANSCRIPT_ID, 1, original);
    expect(defaultAnnotations.speakerIdentities.a?.label).toBe("Speaker a");
    const numeric = { ...original, speakers: ["0", "1"] };
    expect(
      legacyAnnotations(TRANSCRIPT_ID, 1, numeric).speakerIdentities
    ).toMatchObject({
      "0": { label: "Speaker 1" },
      "1": { label: "Speaker 2" },
    });
    const annotations = legacyAnnotations(TRANSCRIPT_ID, 1, original, {
      a: "Alex",
      b: "Alex",
    });
    expect(annotations.speakerIdentities.a?.identityId).not.toBe(
      annotations.speakerIdentities.b?.identityId
    );
    const result = applyStructuralCorrection(original, annotations, {
      action: "delete",
      baseRevision: 1,
      mutationKey: MUTATION,
      targetIds: annotations.wordIdentities.map((identity) => identity.id),
    });
    expect(result.content.words).toEqual([]);
    expect(result.deletedIdentityIds).toHaveLength(3);
  });

  it("splits with deterministic child lineage and honest manual timing", () => {
    const original = content();
    const first = original.words.at(0);
    if (!first) {
      throw new Error("fixture word missing");
    }
    original.words[0] = { ...first, endMs: 0 };
    const annotations = legacyAnnotations(TRANSCRIPT_ID, 1, original);
    const parent = annotations.wordIdentities[0]?.id ?? "";
    const result = applyStructuralCorrection(original, annotations, {
      action: "split",
      baseRevision: 1,
      mutationKey: MUTATION,
      targetId: parent,
      tokens: ["zero", "time"],
    });
    expect(
      result.content.words
        .slice(0, 2)
        .map((word) => [word.startMs, word.endMs, word.timing])
    ).toEqual([
      [0, 0, "interpolated"],
      [0, 0, "interpolated"],
    ]);
    expect(
      result.annotations.wordIdentities
        .slice(0, 2)
        .map((identity) => identity.parentIds)
    ).toEqual([[parent], [parent]]);
  });

  it("inserts ordered tokens and rejects malformed neighbor times", () => {
    const original = content();
    const second = original.words.at(1);
    if (!second) {
      throw new Error("fixture word missing");
    }
    original.words[1] = { ...second, startMs: 1500 };
    const annotations = legacyAnnotations(TRANSCRIPT_ID, 1, original);
    const anchorId = annotations.wordIdentities[0]?.id ?? "";
    const command = {
      action: "insert" as const,
      anchorId,
      baseRevision: 1,
      endMs: 1200,
      mutationKey: MUTATION,
      side: "after" as const,
      speakerIdentityId: annotations.speakerIdentities.a?.identityId ?? null,
      startMs: 1000,
      tokens: ["new"],
    };
    expect(
      applyStructuralCorrection(original, annotations, command).content.words[1]
        ?.text
    ).toBe("new");
    expect(() =>
      applyStructuralCorrection(original, annotations, {
        ...command,
        endMs: 1600,
      })
    ).toThrow("out of order");
    expect(() =>
      applyStructuralCorrection(original, annotations, {
        ...command,
        speakerIdentityId: "missing-speaker",
      })
    ).toThrow("speaker identity is absent");
  });

  it("merges only contiguous words with one speaker and unions lineage", () => {
    const original = content();
    const annotations = legacyAnnotations(TRANSCRIPT_ID, 1, original);
    const firstTwo = annotations.wordIdentities
      .slice(0, 2)
      .map((identity) => identity.id);
    const result = applyStructuralCorrection(original, annotations, {
      action: "merge",
      baseRevision: 1,
      mutationKey: MUTATION,
      targetIds: firstTwo,
    });
    expect(result.content.words[0]?.text).toBe("one two");
    expect(result.annotations.wordIdentities[0]?.parentIds).toEqual(firstTwo);
    expect(() =>
      applyStructuralCorrection(original, annotations, {
        action: "merge",
        baseRevision: 1,
        mutationKey: MUTATION,
        targetIds: annotations.wordIdentities
          .slice(1)
          .map((identity) => identity.id),
      })
    ).toThrow("Reassign speakers");
  });

  it("keeps duplicate labels distinct until an explicit speaker merge", () => {
    const original = content();
    const annotations = legacyAnnotations(TRANSCRIPT_ID, 1, original, {
      a: "Host",
      b: "Guest",
    });
    const a = annotations.speakerIdentities.a?.identityId ?? "";
    const b = annotations.speakerIdentities.b?.identityId ?? "";
    const renamed = applyStructuralCorrection(original, annotations, {
      action: "rename_speakers",
      baseRevision: 1,
      labels: { [a]: "Same", [b]: "Same" },
      mutationKey: MUTATION,
    });
    expect(renamed.content.words[2]?.speaker).toBe("b");
    const merged = applyStructuralCorrection(original, renamed.annotations, {
      action: "merge_speakers",
      baseRevision: 1,
      mutationKey: MUTATION,
      sourceIdentityIds: [b],
      targetIdentityId: a,
    });
    expect(new Set(merged.content.words.map((word) => word.speaker))).toEqual(
      new Set(["a"])
    );
  });
});
