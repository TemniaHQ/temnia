import { describe, expect, it } from "vitest";
import {
  ChapterBoundarySchema,
  ChapterEditSpecSchema,
  ChapterExportSchema,
  ChapterReviewInputSchema,
  ChapterRunInputSchema,
  HarnessArtifactRefSchema,
  HarnessEvidenceSchema,
  RationalTimeSchema,
  SEEDED_SCOPE,
} from "../src/index.ts";

const SOURCE_ID = "0192e8a0-0000-7000-8000-000000000010";
const RUN_ID = "0192e8a0-0000-7000-8000-000000000011";
const MUTATION_KEY = "0192e8a0-0000-7000-8000-000000000012";
const ARTIFACT_ID = "0192e8a0-0000-7000-8000-000000000013";
const TRANSCRIPT_ID = "0192e8a0-0000-7000-8000-000000000014";
const SHA = "a".repeat(64);

const validRunInput = {
  brief: "Preserve the explanation and remove the break.",
  budgetMicros: 50_000,
  config: {
    backend: "recorded" as const,
    evidenceWindowSentences: 200,
    maxDispatches: 4,
    maxOutputTokens: 8000,
    maxRenderConcurrency: 4,
    maxRepairs: 1,
    routeSnapshotId: "recorded:test-route-v1",
  },
  requestKey: MUTATION_KEY,
  runId: RUN_ID,
  scope: SEEDED_SCOPE,
  sourceId: SOURCE_ID,
};

const validReviewInput = {
  action: "nudge" as const,
  baseRevision: 1,
  boundaryId: "boundary-1",
  budgetMicros: null,
  mutationKey: MUTATION_KEY,
  otherSectionId: null,
  reason: "Move to the end of the pause.",
  runId: RUN_ID,
  scope: SEEDED_SCOPE,
  sectionId: null,
  sourceId: SOURCE_ID,
  targetRevision: null,
  targetTimeMs: 1000,
};

describe("harness public contracts", () => {
  it("allows source-scaled dispatching while retaining an explicit operator ceiling", () => {
    for (const maxDispatches of [null, 256, 4]) {
      const result = ChapterRunInputSchema.parse({
        ...validRunInput,
        config: { ...validRunInput.config, maxDispatches },
      });
      expect(result.config.maxDispatches).toBe(maxDispatches);
    }
  });
  it("accepts only an offset-bearing RFC 3339 export timestamp", () => {
    const valid = {
      chapters: [],
      createdAt: "2026-09-08T18:30:00Z",
      editSha256: SHA,
      manifestKey: "org/o/source/s/harness/export.json",
      revision: 1,
      runId: RUN_ID,
      version: 1 as const,
    };
    expect(ChapterExportSchema.parse(valid)).toEqual(valid);
    expect(
      ChapterExportSchema.safeParse({
        ...valid,
        createdAt: "2026-09-08T18:30:00",
      }).success
    ).toBe(false);
  });

  it("accepts a finite run input and an immutable artifact reference", () => {
    expect(ChapterRunInputSchema.parse(validRunInput)).toEqual(validRunInput);
    expect(
      HarnessArtifactRefSchema.safeParse({
        fingerprint: SHA,
        id: ARTIFACT_ID,
        kind: "model_response",
        sha256: SHA,
        sizeBytes: 512,
        storageKey: "org/o/source/s/harness/model-response.json",
      }).success
    ).toBe(true);
  });

  it("rejects unknown fields, unsafe money and invalid enum actions", () => {
    expect(
      ChapterRunInputSchema.safeParse({ ...validRunInput, secret: "token" })
        .success
    ).toBe(false);
    expect(
      ChapterRunInputSchema.safeParse({
        ...validRunInput,
        budgetMicros: Number.MAX_SAFE_INTEGER + 1,
      }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        action: "move",
      }).success
    ).toBe(false);
  });

  it("rejects negative edit time and incomplete action arguments", () => {
    expect(
      RationalTimeSchema.safeParse({ denominator: 1000, numerator: -1 }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        targetTimeMs: -1,
      }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        boundaryId: null,
      }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        action: "raise_budget",
        boundaryId: null,
        budgetMicros: null,
        targetTimeMs: null,
      }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        action: "undo",
        boundaryId: null,
        targetRevision: 1,
        targetTimeMs: null,
      }).success
    ).toBe(false);
    expect(
      ChapterReviewInputSchema.safeParse({
        ...validReviewInput,
        action: "undo",
        baseRevision: 2,
        boundaryId: null,
        targetRevision: 1,
        targetTimeMs: null,
      }).success
    ).toBe(true);
  });
});

describe("harness artifact validators", () => {
  const evidence = {
    audioSampleRate: 48_000,
    boundaries: [
      {
        clearanceMs: 100,
        id: "candidate-1",
        kind: "sentence" as const,
        reasons: [],
        requiresReview: false,
        score: 0.9,
        sentenceId: "sentence-1",
        timeMs: 1000,
      },
    ],
    config: {},
    durationMs: 2000,
    frameRate: { denominator: 1001, numerator: 30_000 },
    modelVersions: { segmenter: "sat-3l-sm" },
    pauses: [
      {
        endMs: 1100,
        id: "pause-1",
        leftWordId: "word-1",
        rightWordId: "word-2",
        startMs: 900,
      },
    ],
    sentences: [
      {
        endMs: 1800,
        id: "sentence-1",
        speakers: ["0"],
        startMs: 100,
        text: "Hello world",
        wordIds: ["word-1", "word-2"],
      },
    ],
    shots: [{ score: 0.7, timeMs: 1000 }],
    sourceFingerprint: SHA,
    sourceId: SOURCE_ID,
    sourceStart: { denominator: 1000, numerator: -240 },
    speechCoverage: {
      detector: null,
      detectorHash: null,
      detectorRevision: null,
      intervals: [],
      status: "unknown" as const,
      uncoveredSpeechMs: 0,
      uncoveredTailMs: 0,
      warnings: [],
    },
    transcriptId: TRANSCRIPT_ID,
    transcriptRevision: 1,
    transcriptSha256: SHA,
    version: 1 as const,
    videoTimeBase: { denominator: 90_000, numerator: 1 },
    words: [
      {
        confidence: 0.9,
        endMs: 500,
        id: "word-1",
        lineageIds: [],
        speaker: "0",
        startMs: 100,
        text: "Hello",
        timing: "aligned" as const,
        wordIndex: 0,
      },
      {
        confidence: null,
        endMs: 1800,
        id: "word-2",
        lineageIds: [],
        speaker: "0",
        startMs: 1200,
        text: "world",
        timing: "interpolated" as const,
        wordIndex: 1,
      },
    ],
  };

  it("accepts a signed source offset and fully grounded evidence", () => {
    expect(HarnessEvidenceSchema.safeParse(evidence).success).toBe(true);
  });

  it("rejects missing, repeated or out-of-range evidence grounding", () => {
    expect(
      HarnessEvidenceSchema.safeParse({
        ...evidence,
        sentences: [{ ...evidence.sentences[0], wordIds: ["word-1"] }],
      }).success
    ).toBe(false);
    expect(
      HarnessEvidenceSchema.safeParse({
        ...evidence,
        pauses: [{ ...evidence.pauses[0], rightWordId: "unknown" }],
      }).success
    ).toBe(false);
    expect(
      HarnessEvidenceSchema.safeParse({
        ...evidence,
        shots: [{ score: 1, timeMs: 2001 }],
      }).success
    ).toBe(false);
  });

  it("enforces an exact source cover with one shared boundary", () => {
    const edit = {
      boundaries: [
        {
          candidateId: null,
          id: "start",
          reasons: [],
          requiresReview: false,
          time: { denominator: 1000, numerator: 0 },
          timeMs: 0,
        },
        {
          candidateId: "candidate-1",
          id: "middle",
          reasons: [],
          requiresReview: false,
          time: { denominator: 1000, numerator: 1000 },
          timeMs: 1000,
        },
        {
          candidateId: null,
          id: "end",
          reasons: [],
          requiresReview: false,
          time: { denominator: 1000, numerator: 2000 },
          timeMs: 2000,
        },
      ],
      compilerVersion: "1",
      durationMs: 2000,
      evidenceArtifactId: ARTIFACT_ID,
      evidenceSha256: SHA,
      sections: [
        {
          endBoundaryId: "middle",
          flags: [],
          id: "section-1",
          kind: "keep" as const,
          quoteWordIds: ["word-1"],
          reason: "Opening",
          reviewState: "proposed" as const,
          startBoundaryId: "start",
          title: "Opening",
        },
        {
          endBoundaryId: "end",
          flags: [],
          id: "section-2",
          kind: "drop" as const,
          quoteWordIds: [],
          reason: "Break",
          reviewState: "proposed" as const,
          startBoundaryId: "middle",
          title: "Break",
        },
      ],
      sourceAudioSampleRate: 48_000,
      sourceFrameRate: { denominator: 1001, numerator: 30_000 },
      sourceId: SOURCE_ID,
      version: 1 as const,
    };
    expect(ChapterEditSpecSchema.safeParse(edit).success).toBe(true);
    expect(
      ChapterEditSpecSchema.safeParse({
        ...edit,
        sections: [
          edit.sections[0],
          { ...edit.sections[1], startBoundaryId: "start" },
        ],
      }).success
    ).toBe(false);
    expect(
      ChapterEditSpecSchema.safeParse({
        ...edit,
        boundaries: edit.boundaries.map((boundary, index) =>
          index === 2
            ? {
                ...boundary,
                time: { denominator: 1000, numerator: 1999 },
                timeMs: 1999,
              }
            : boundary
        ),
      }).success
    ).toBe(false);

    const zeroLength = {
      ...edit,
      boundaries: [
        edit.boundaries[0],
        edit.boundaries[1],
        {
          ...edit.boundaries[2],
          time: { denominator: 1, numerator: 1 },
          timeMs: 1000,
        },
      ],
      durationMs: 1000,
    };
    expect(ChapterEditSpecSchema.safeParse(zeroLength).success).toBe(false);

    const sameRoundedMillisecond = {
      ...edit,
      boundaries: [
        edit.boundaries[0],
        {
          ...edit.boundaries[1],
          id: "middle-a",
          time: { denominator: 10_000, numerator: 10_001 },
          timeMs: 1000,
        },
        {
          ...edit.boundaries[1],
          id: "middle-b",
          time: { denominator: 5000, numerator: 5002 },
          timeMs: 1000,
        },
        edit.boundaries[2],
      ],
      sections: [
        { ...edit.sections[0], endBoundaryId: "middle-a" },
        {
          ...edit.sections[0],
          endBoundaryId: "middle-b",
          id: "section-between-frames",
          startBoundaryId: "middle-a",
        },
        { ...edit.sections[1], startBoundaryId: "middle-b" },
      ],
    };
    expect(
      ChapterEditSpecSchema.safeParse(sameRoundedMillisecond).success
    ).toBe(true);
  });

  it("uses exact arithmetic at the public safe-integer ceiling", () => {
    const safe = Number.MAX_SAFE_INTEGER;
    expect(
      ChapterBoundarySchema.safeParse({
        candidateId: null,
        id: "large-rational",
        reasons: [],
        requiresReview: false,
        time: { denominator: safe, numerator: safe },
        timeMs: 1000,
      }).success
    ).toBe(true);

    expect(
      ChapterEditSpecSchema.safeParse({
        boundaries: [
          {
            candidateId: null,
            id: "start",
            reasons: [],
            requiresReview: false,
            time: { denominator: 1, numerator: 0 },
            timeMs: 0,
          },
          {
            candidateId: null,
            id: "end",
            reasons: [],
            requiresReview: false,
            time: { denominator: 1000, numerator: safe },
            timeMs: safe,
          },
        ],
        compilerVersion: "1",
        durationMs: safe,
        evidenceArtifactId: ARTIFACT_ID,
        evidenceSha256: SHA,
        sections: [
          {
            endBoundaryId: "end",
            flags: [],
            id: "all",
            kind: "keep",
            quoteWordIds: [],
            reason: "All",
            reviewState: "proposed",
            startBoundaryId: "start",
            title: "All",
          },
        ],
        sourceAudioSampleRate: null,
        sourceFrameRate: null,
        sourceId: SOURCE_ID,
        version: 1,
      }).success
    ).toBe(true);
  });
});
