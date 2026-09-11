import { describe, expect, it } from "vitest";
import {
  TopicCandidateSchema,
  TopicCompiledVideoSchema,
  TopicEditSpecSchema,
  TopicProposalSchema,
} from "../src/topics.ts";

const sourceId = "0192e8a0-0000-7000-8000-000000000010";
const evidenceArtifactId = "0192e8a0-0000-7000-8000-000000000013";
const evidenceSha256 = "a".repeat(64);

function candidate(id: string) {
  return {
    completionSpans: [{ firstSentenceId: "s1", lastSentenceId: "s1" }],
    coreSpans: [{ firstSentenceId: "s0", lastSentenceId: "s1" }],
    firstSentenceId: "s0",
    id,
    lastSentenceId: "s1",
    meaningChangingFollowups: [],
    purpose: "Explain a complete discussion.",
    reason: "The discussion answers its opening question.",
    requiredContextSpans: [],
    title: `Discussion ${id}`,
  };
}

function video(id: string) {
  const topic = candidate(id);
  return {
    candidate: topic,
    edit: {
      boundaries: [
        {
          candidateId: "source-start",
          id: "c0",
          reasons: [],
          requiresReview: false,
          time: { denominator: 1, numerator: 0 },
          timeMs: 0,
        },
        {
          candidateId: "source-end",
          id: "c1",
          reasons: [],
          requiresReview: false,
          time: { denominator: 1, numerator: 10 },
          timeMs: 10_000,
        },
      ],
      compilerVersion: "chapter-compiler/1",
      durationMs: 10_000,
      evidenceArtifactId,
      evidenceSha256,
      sections: [
        {
          endBoundaryId: "c1",
          flags: [],
          id,
          kind: "keep" as const,
          quoteWordIds: ["w0", "w1"],
          reason: topic.reason,
          reviewState: "proposed" as const,
          startBoundaryId: "c0",
          title: topic.title,
        },
      ],
      sourceAudioSampleRate: null,
      sourceFrameRate: null,
      sourceId,
      version: 1 as const,
    },
    keptSectionId: id,
  };
}

describe("independent topic contracts", () => {
  it("allows a reasoned zero-output proposal without inventing source sections", () => {
    expect(
      TopicProposalSchema.parse({
        candidates: [],
        summary: "No independently complete discussion was found.",
        version: 1,
      }).candidates
    ).toEqual([]);
  });

  it("does not accept model-authored time or quote fields", () => {
    expect(
      TopicCandidateSchema.safeParse({
        ...candidate("one"),
        quoteWordIds: ["invented"],
      }).success
    ).toBe(false);
    expect(
      TopicCandidateSchema.safeParse({ ...candidate("one"), startMs: 1000 })
        .success
    ).toBe(false);
  });

  it("requires completion evidence and unique candidate identities", () => {
    expect(
      TopicCandidateSchema.safeParse({
        ...candidate("one"),
        completionSpans: [],
      }).success
    ).toBe(false);
    expect(
      TopicProposalSchema.safeParse({
        candidates: [candidate("same"), candidate("same")],
        summary: "Two outputs.",
        version: 1,
      }).success
    ).toBe(false);
  });

  it("allows overlapping independent timelines bound to the same evidence", () => {
    const portfolio = {
      compilerVersion: "topic-compiler/1",
      durationMs: 10_000,
      evidenceArtifactId,
      evidenceSha256,
      sourceId,
      summary: "Independent discussions reuse their source context.",
      version: 1,
      videos: [video("one"), video("two")],
    };
    expect(TopicEditSpecSchema.parse(portfolio).videos).toHaveLength(2);
    const second = portfolio.videos.at(1);
    expect(second).toBeDefined();
    if (second) {
      second.edit.evidenceSha256 = "b".repeat(64);
    }
    expect(TopicEditSpecSchema.safeParse(portfolio).success).toBe(false);
  });

  it("requires the execution to retain exactly its named topic", () => {
    const valid = video("one");
    expect(TopicCompiledVideoSchema.safeParse(valid).success).toBe(true);
    expect(
      TopicCompiledVideoSchema.safeParse({ ...valid, keptSectionId: "another" })
        .success
    ).toBe(false);
    const section = valid.edit.sections.at(0);
    expect(section).toBeDefined();
    if (section) {
      section.id = "other";
    }
    expect(TopicCompiledVideoSchema.safeParse(valid).success).toBe(false);
  });
});
