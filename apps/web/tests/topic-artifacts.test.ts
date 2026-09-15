import { createHash } from "node:crypto";
import {
  type HarnessArtifactRef,
  TopicEditSpecSchema,
} from "@temnia/contracts";
import { describe, expect, it } from "vitest";
import type { ZodType } from "zod";
import type { ChapterArtifactRef, ChapterView } from "@/lib/harness/queries";
import {
  loadTopicRevision,
  reusedContextMs,
  topicDurationMs,
  topicEditorialStatus,
} from "@/lib/harness/topic-artifacts";

const SOURCE = "0192e8a0-0000-7000-8000-000000000010";
const ORG = "0192e8a0-0000-7000-8000-000000000001";
const RUN = "0192e8a0-0000-7000-8000-000000000020";
const EVIDENCE = "0192e8a0-0000-7000-8000-000000000030";
const evidenceSha256 = "a".repeat(64);
const proposalSha256 = "b".repeat(64);

function fixture(accepted = false) {
  const bodies = new Map<string, unknown>();
  let counter = 100;
  function put(
    kind: ChapterArtifactRef["kind"],
    body: unknown,
    metadata: Record<string, unknown> = {}
  ) {
    counter += 1;
    const id = `0192e8a0-0000-7000-8000-${counter.toString().padStart(12, "0")}`;
    const bytes = JSON.stringify(body);
    const ref: HarnessArtifactRef = {
      fingerprint: "f".repeat(64),
      id,
      kind,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      sizeBytes: Buffer.byteLength(bytes),
      storageKey: `org/${ORG}/source/${SOURCE}/harness/${id}.json`,
    };
    bodies.set(id, body);
    const webRef: ChapterArtifactRef = {
      ...ref,
      kind,
      metadata,
      url: `/api/media/${ref.storageKey}`,
    };
    return { ref, webRef };
  }
  function video(id: string) {
    return {
      candidate: {
        completionSpans: [{ firstSentenceId: "s1", lastSentenceId: "s1" }],
        coreSpans: [{ firstSentenceId: "s0", lastSentenceId: "s1" }],
        firstSentenceId: "s0",
        id,
        lastSentenceId: "s1",
        meaningChangingFollowups: [],
        purpose: "Answer a complete question.",
        reason: "The answer reaches its conclusion.",
        requiredContextSpans: [],
        title: id,
      },
      edit: {
        boundaries: [0, 10].map((seconds, index) => ({
          candidateId: null,
          id: `c${index}`,
          reasons: [],
          requiresReview: false,
          time: { denominator: 1, numerator: seconds },
          timeMs: seconds * 1000,
        })),
        compilerVersion: "test",
        durationMs: 10_000,
        evidenceArtifactId: EVIDENCE,
        evidenceSha256,
        sections: [
          {
            endBoundaryId: "c1",
            flags: [],
            id,
            kind: "keep",
            quoteWordIds: ["w0", "w1"],
            reason: "Complete answer.",
            reviewState: accepted ? "accepted" : "proposed",
            startBoundaryId: "c0",
            title: id,
          },
        ],
        sourceAudioSampleRate: null,
        sourceFrameRate: null,
        sourceId: SOURCE,
        version: 1,
      },
      keptSectionId: id,
    };
  }
  const portfolio = TopicEditSpecSchema.parse({
    compilerVersion: "topic-compiler/1",
    durationMs: 10_000,
    evidenceArtifactId: EVIDENCE,
    evidenceSha256,
    sourceId: SOURCE,
    summary: "Two independent outputs share context.",
    version: 1,
    videos: [video("one"), video("two")],
  });
  const criterion = { evidenceSpans: [], reason: "Supported.", status: "pass" };
  const assessment = {
    candidates: portfolio.videos.map(({ candidate }) => ({
      candidateId: candidate.id,
      coldReview: {
        candidateId: candidate.id,
        coherentTopic: criterion,
        completeDiscussion: criterion,
        intelligibleBeginning: criterion,
        titleFaithful: criterion,
      },
      reasons: [],
      sourceReview: {
        candidateId: candidate.id,
        completeContext: criterion,
        distinctPurpose: criterion,
        faithfulMeaning: criterion,
      },
      status: "passed",
    })),
  };
  const selectionRecord = put(
    "proposal",
    {
      draft: {
        opportunities: [
          {
            candidateIds: [],
            completionSpans: [{ firstSentenceId: "s1", lastSentenceId: "s1" }],
            coreSpans: [{ firstSentenceId: "s0", lastSentenceId: "s0" }],
            disposition: "needs_evidence",
            dispositionReason:
              "A known discussion still needs a useful treatment.",
            id: "omitted",
            meaningChangingFollowups: [],
            requiredContextSpans: [],
            valueEvidenceSpans: [
              { firstSentenceId: "s0", lastSentenceId: "s1" },
            ],
            viewerPurpose: "Understand an unresolved source discussion.",
          },
        ],
        proposal: {
          candidates: portfolio.videos.map((item) => item.candidate),
          summary: portfolio.summary,
          version: 1,
        },
      },
      evidenceSha256,
      format: "topic-selection/2",
      origin: "model",
      parentSelectionSha256: null,
      rubric: {
        assumedDomainKnowledge: [],
        audienceDescription: "Interested viewers.",
        exclusions: [],
        focus: "",
        languagePolicy: "Preserve source speech.",
        originalInstructions: "",
        version: 1,
        viewerGoals: ["Understand useful discussions."],
      },
      rubricSha256: "c".repeat(64),
      runId: RUN,
    },
    { format: "topic-selection/2", runId: RUN }
  );
  const selectionAssessment = {
    coldReviews: assessment.candidates.map((item) => ({
      ...item.coldReview,
      value: {
        deliveredValue: { ...criterion },
        focusedDevelopment: criterion,
        openingEffectiveness: criterion,
        reconstructedPurpose: "Explain a useful answer.",
        reconstructedTakeaway: "The answer is complete.",
        viewerReasonToWatch: criterion,
      },
    })),
    evidenceSha256,
    executionStatus: "complete",
    findings: [],
    format: "topic-selection-assessment/2",
    portfolioReview: {
      candidates: assessment.candidates.map((item) => item.sourceReview),
      findings: [],
      handoffs: [],
      missingOpportunities: [],
      opportunities: [],
      overlaps: [],
      selection: assessment.candidates.map((item) => ({
        candidateId: item.candidateId,
        disposition: "select",
        evidenceSpans: [{ firstSentenceId: "s0", lastSentenceId: "s1" }],
        reason: "Useful complete discussion.",
      })),
      summary: "Both discussions add value.",
    },
    proposerFamily: "one",
    reasons: [],
    responseArtifacts: [],
    rubricSha256: "c".repeat(64),
    runId: RUN,
    selectionSha256: selectionRecord.ref.sha256,
    verifierFamily: "two",
  };
  const assessmentArtifact = put("checks", selectionAssessment, {
    format: "topic-selection-assessment/2",
    runId: RUN,
  });
  const edit = put("edit", portfolio, {
    assessmentArtifactId: assessmentArtifact.ref.id,
    format: "topic-edit/1",
    proposalSha256,
    runId: RUN,
    selectionArtifactId: selectionRecord.ref.id,
    selectionSha256: selectionRecord.ref.sha256,
  });
  const children = portfolio.videos.map((item) => {
    const execution = put("edit", item.edit);
    const checksBody = {
      editorialReasons: [],
      editorialStatus: "not_run",
      editSha256: execution.ref.sha256,
      technicalChecks: [
        "full_decode",
        "duration",
        "video_presence",
        "audio_presence",
        "caption_bounds",
      ].map((name) => ({
        expected: 0,
        measured: 0,
        message: "Pass",
        name,
        sectionId: item.keptSectionId,
        status: "pass",
      })),
      verifierFamily: null,
      version: 1,
    };
    const checks = put("checks", checksBody);
    const media = put("render", "media");
    const descriptorBody = {
      editSha256: execution.ref.sha256,
      format: "chapter-renders/1",
      renders: [
        {
          captions: null,
          checks: checks.ref,
          durationMs: 10_000,
          editSha256: execution.ref.sha256,
          media: media.ref,
          sectionId: item.keptSectionId,
        },
      ],
      runId: RUN,
    };
    const descriptor = put("render", descriptorBody);
    return {
      candidateId: item.candidate.id,
      descriptor: descriptor.ref,
      execution: execution.ref,
    };
  });
  const descriptor = put(
    "render",
    {
      editSha256: edit.ref.sha256,
      format: "topic-renders/1",
      runId: RUN,
      videos: children,
    },
    { editSha256: edit.ref.sha256, format: "topic-renders/1", runId: RUN }
  );
  const manifest = put(
    "export",
    {
      editSha256: edit.ref.sha256,
      format: "topic-export/1",
      revision: 1,
      runId: RUN,
      videos: children,
    },
    { editSha256: edit.ref.sha256, format: "topic-export/1", runId: RUN }
  );
  const view: ChapterView = {
    acceptedEdit: accepted ? edit.webRef : null,
    artifacts: [
      edit.webRef,
      assessmentArtifact.webRef,
      descriptor.webRef,
      selectionRecord.webRef,
    ],
    currentEdit: edit.webRef,
    events: [],
    run: {
      acceptedRevision: accepted ? 1 : null,
      brief: "Default",
      budgetMicros: 1_000_000,
      createdAt: "2026-09-10T00:00:00Z",
      currentRevision: 1,
      currentTranscriptRevision: 1,
      dispatchCount: 2,
      errorMessage: null,
      evidenceTranscriptRevision: 1,
      id: RUN,
      projection: null,
      reservedMicros: 0,
      routes: { author: null, verifier: null },
      spentMicros: 100,
      stage: "review",
      status: "needs_review",
      synthetic: false,
    },
    runs: [],
  };
  const load = async <T>(ref: ChapterArtifactRef, schema: ZodType<T>) =>
    schema.parse(bodies.get(ref.id));
  return {
    bodies,
    children,
    descriptor,
    edit,
    load,
    manifest,
    portfolio,
    selectionAssessment,
    view,
  };
}

describe("independent topic artifact graph", () => {
  it("binds judgments to the exact selection and does not hide weak value behind basic passes", async () => {
    const f = fixture();
    const [first] = f.selectionAssessment.coldReviews;
    if (!first) {
      throw new Error("Missing fixture review");
    }
    first.value.deliveredValue = {
      ...first.value.deliveredValue,
      reason: "The answer never delivers its claim.",
      status: "fail",
    };
    const result = await loadTopicRevision(SOURCE, f.view, false, f.load);
    expect(result?.selectionAssessment?.format).toBe(
      "topic-selection-assessment/2"
    );
    expect(result?.selection?.draft.opportunities[0]?.id).toBe("omitted");
    expect(result?.videos[0]?.assessment?.status).toBe("needs_review");
    expect(result?.videos[1]?.assessment?.status).toBe("passed");
    f.selectionAssessment.selectionSha256 = "d".repeat(64);
    await expect(
      loadTopicRevision(SOURCE, f.view, false, f.load)
    ).rejects.toThrow("another source or selection");
  });
  it("rounds the rational duration instead of subtracting rounded endpoints", () => {
    expect(
      topicDurationMs(
        { denominator: 3, numerator: 1 },
        { denominator: 3, numerator: 2 }
      )
    ).toBe(333);
  });
  it("plays overlapping candidates without imposing source tiling or human acceptance", async () => {
    const f = fixture();
    const result = await loadTopicRevision(SOURCE, f.view, false, f.load);
    expect(result?.videos).toHaveLength(2);
    const first = result?.videos[0];
    expect(first?.technicalPass).toBe(true);
    expect(first?.mediaUrl).toContain(`/source/${SOURCE}/`);
    expect(first?.video.edit.sections[0]?.reviewState).toBe("proposed");
    if (!(first && result)) {
      throw new Error("Missing fixture video");
    }
    expect(reusedContextMs(first, [...result.videos, first])).toBe(10_000);
    expect(
      topicEditorialStatus(result.selectionAssessment ?? null, first.assessment)
    ).toBe("Passed text review");
    expect(result.exportUrl).toBeNull();
  });

  it("requires the recorded human state even when a candidate export is present", async () => {
    const f = fixture();
    f.view.artifacts.push(f.manifest.webRef);
    await expect(
      loadTopicRevision(SOURCE, f.view, false, f.load)
    ).rejects.toThrow("unaccepted");
  });

  it("exposes only accepted manifest members and supports a partial accepted current revision", async () => {
    const f = fixture(true);
    f.view.artifacts.push(f.manifest.webRef);
    if (f.view.run) {
      f.view.run.acceptedRevision = null;
    }
    f.view.acceptedEdit = null;
    const result = await loadTopicRevision(SOURCE, f.view, false, f.load);
    expect(result?.exportedCandidateIds).toEqual(["one", "two"]);
    expect(result?.exportUrl).toContain(f.manifest.ref.id);
  });

  it("refuses nested descriptors, executions and media from another source or run", async () => {
    const f = fixture();
    const [child] = f.children;
    if (!child) {
      throw new Error("Missing child");
    }
    child.execution.storageKey = child.execution.storageKey.replace(
      SOURCE,
      RUN
    );
    await expect(
      loadTopicRevision(SOURCE, f.view, false, f.load)
    ).rejects.toThrow("scope");
    child.execution.storageKey = child.execution.storageKey.replace(
      `/source/${RUN}/`,
      `/source/${SOURCE}/`
    );
    const descriptor = f.bodies.get(child.descriptor.id) as { runId: string };
    descriptor.runId = SOURCE;
    await expect(
      loadTopicRevision(SOURCE, f.view, false, f.load)
    ).rejects.toThrow("differs from its execution");
  });

  it("does not treat absent, same-family or incomplete reviews as passed", async () => {
    const f = fixture();
    f.selectionAssessment.verifierFamily = f.selectionAssessment.proposerFamily;
    const result = await loadTopicRevision(SOURCE, f.view, false, f.load);
    expect(
      topicEditorialStatus(
        result?.selectionAssessment ?? null,
        result?.videos[0]?.assessment ?? null
      )
    ).toBe("Unverified");
    expect(topicEditorialStatus(null, null)).toBe("Unverified");
    f.selectionAssessment.evidenceSha256 = "c".repeat(64);
    await expect(
      loadTopicRevision(SOURCE, f.view, false, f.load)
    ).rejects.toThrow("another source or selection");
  });
});
