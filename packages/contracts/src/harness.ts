import { z } from "zod";
import { ScopeSchema } from "./scope.ts";

const { MAX_SAFE_INTEGER } = Number;
const SHA256_PATTERN = /^[a-fA-F0-9]{64}$/;
const safeNonnegativeInteger = () => z.int().min(0).max(MAX_SAFE_INTEGER);
const safePositiveInteger = () => z.int().min(1).max(MAX_SAFE_INTEGER);
const nonemptyId = () => z.string().min(1).max(256);
const sha256 = () => z.string().regex(SHA256_PATTERN);

export const RationalTimeSchema = z
  .object({
    denominator: safePositiveInteger(),
    numerator: safeNonnegativeInteger(),
  })
  .strict()
  .meta({ id: "RationalTime", title: "RationalTime" });

export type RationalTime = z.infer<typeof RationalTimeSchema>;

export const PositiveRationalSchema = z
  .object({
    denominator: safePositiveInteger(),
    numerator: safePositiveInteger(),
  })
  .strict()
  .meta({ id: "PositiveRational", title: "PositiveRational" });

export type PositiveRational = z.infer<typeof PositiveRationalSchema>;

export const SignedRationalTimeSchema = z
  .object({
    denominator: safePositiveInteger(),
    numerator: z.int().min(-MAX_SAFE_INTEGER).max(MAX_SAFE_INTEGER),
  })
  .strict()
  .meta({ id: "SignedRationalTime", title: "SignedRationalTime" });

export type SignedRationalTime = z.infer<typeof SignedRationalTimeSchema>;

export const HarnessArtifactKindSchema = z
  .enum([
    "evidence",
    "proposal",
    "model_response",
    "edit",
    "render",
    "checks",
    "export",
    "speech_checkpoint",
    "speech_assignment",
  ])
  .meta({ id: "HarnessArtifactKind", title: "HarnessArtifactKind" });

export type HarnessArtifactKind = z.infer<typeof HarnessArtifactKindSchema>;

export const HarnessArtifactRefSchema = z
  .object({
    fingerprint: sha256(),
    id: z.uuid(),
    kind: HarnessArtifactKindSchema,
    sha256: sha256(),
    sizeBytes: safeNonnegativeInteger(),
    storageKey: z.string().min(1).max(2048),
  })
  .strict()
  .meta({ id: "HarnessArtifactRef", title: "HarnessArtifactRef" });

export type HarnessArtifactRef = z.infer<typeof HarnessArtifactRefSchema>;

export const HarnessEvidenceWordSchema = z
  .object({
    confidence: z.number().min(0).max(1).nullable(),
    endMs: safeNonnegativeInteger(),
    id: nonemptyId(),
    lineageIds: z.array(nonemptyId()).default([]),
    speaker: z.string().nullable(),
    startMs: safeNonnegativeInteger(),
    text: z.string().min(1),
    timing: z.enum(["aligned", "interpolated"]),
    wordIndex: safeNonnegativeInteger(),
  })
  .strict()
  .refine((word) => word.endMs >= word.startMs, {
    error: "a word cannot end before it starts",
  })
  .meta({ id: "HarnessEvidenceWord", title: "HarnessEvidenceWord" });

export type HarnessEvidenceWord = z.infer<typeof HarnessEvidenceWordSchema>;

export const HarnessEvidenceSentenceSchema = z
  .object({
    endMs: safeNonnegativeInteger(),
    id: nonemptyId(),
    speakers: z.array(z.string()),
    startMs: safeNonnegativeInteger(),
    text: z.string(),
    wordIds: z.array(nonemptyId()).min(1),
  })
  .strict()
  .refine((sentence) => sentence.endMs >= sentence.startMs, {
    error: "a sentence cannot end before it starts",
  })
  .meta({
    id: "HarnessEvidenceSentence",
    title: "HarnessEvidenceSentence",
  });

export type HarnessEvidenceSentence = z.infer<
  typeof HarnessEvidenceSentenceSchema
>;

export const HarnessEvidencePauseSchema = z
  .object({
    endMs: safeNonnegativeInteger(),
    id: nonemptyId(),
    leftWordId: nonemptyId().nullable(),
    rightWordId: nonemptyId().nullable(),
    startMs: safeNonnegativeInteger(),
  })
  .strict()
  .refine((pause) => pause.endMs >= pause.startMs, {
    error: "a pause cannot end before it starts",
  })
  .meta({ id: "HarnessEvidencePause", title: "HarnessEvidencePause" });

export type HarnessEvidencePause = z.infer<typeof HarnessEvidencePauseSchema>;

export const HarnessBoundaryCandidateSchema = z
  .object({
    clearanceMs: safeNonnegativeInteger(),
    id: nonemptyId(),
    kind: z.enum(["edge", "sentence", "turn", "pause", "shot"]),
    reasons: z.array(z.string()),
    requiresReview: z.boolean(),
    score: z.number().finite(),
    sentenceId: nonemptyId().nullable(),
    timeMs: safeNonnegativeInteger(),
  })
  .strict()
  .meta({
    id: "HarnessBoundaryCandidate",
    title: "HarnessBoundaryCandidate",
  });

export type HarnessBoundaryCandidate = z.infer<
  typeof HarnessBoundaryCandidateSchema
>;

export const SpeechCoverageIntervalSchema = z
  .object({
    endMs: safeNonnegativeInteger(),
    startMs: safeNonnegativeInteger(),
  })
  .strict()
  .refine((interval) => interval.endMs >= interval.startMs, {
    error: "a speech interval cannot end before it starts",
  })
  .meta({
    id: "SpeechCoverageInterval",
    title: "SpeechCoverageInterval",
  });

export type SpeechCoverageInterval = z.infer<
  typeof SpeechCoverageIntervalSchema
>;

export const SpeechCoverageSchema = z
  .object({
    detector: z.string().nullable(),
    detectorHash: sha256().nullable(),
    detectorRevision: z.string().nullable(),
    intervals: z.array(SpeechCoverageIntervalSchema),
    status: z.enum(["unknown", "clear", "needs_review"]),
    uncoveredSpeechMs: safeNonnegativeInteger(),
    uncoveredTailMs: safeNonnegativeInteger(),
    warnings: z.array(z.string()),
  })
  .strict()
  .meta({ id: "SpeechCoverage", title: "SpeechCoverage" });

export type SpeechCoverage = z.infer<typeof SpeechCoverageSchema>;

export const HarnessEvidenceShotSchema = z
  .object({
    score: z.number().finite(),
    timeMs: safeNonnegativeInteger(),
  })
  .strict()
  .meta({ id: "HarnessEvidenceShot", title: "HarnessEvidenceShot" });

export type HarnessEvidenceShot = z.infer<typeof HarnessEvidenceShotSchema>;

function isUnique(values: readonly string[]): boolean {
  return new Set(values).size === values.length;
}

export const HarnessEvidenceSchema = z
  .object({
    audioSampleRate: safePositiveInteger().nullable(),
    boundaries: z.array(HarnessBoundaryCandidateSchema),
    config: z.record(z.string(), z.unknown()),
    durationMs: safeNonnegativeInteger(),
    frameRate: PositiveRationalSchema.nullable(),
    modelVersions: z.record(z.string(), z.string()),
    pauses: z.array(HarnessEvidencePauseSchema),
    sentences: z.array(HarnessEvidenceSentenceSchema),
    shots: z.array(HarnessEvidenceShotSchema),
    sourceFingerprint: sha256(),
    sourceId: z.uuid(),
    sourceStart: SignedRationalTimeSchema,
    speechCoverage: SpeechCoverageSchema,
    transcriptId: z.uuid(),
    transcriptRevision: safePositiveInteger(),
    transcriptSha256: sha256(),
    version: z.literal(1),
    videoTimeBase: PositiveRationalSchema.nullable(),
    words: z.array(HarnessEvidenceWordSchema),
  })
  .strict()
  .superRefine((evidence, ctx) => {
    const wordIds = evidence.words.map((word) => word.id);
    const sentenceIds = evidence.sentences.map((sentence) => sentence.id);
    const boundaryIds = evidence.boundaries.map((boundary) => boundary.id);
    const wordIndexes = evidence.words.map((word) => word.wordIndex);
    if (
      !(isUnique(wordIds) && isUnique(sentenceIds) && isUnique(boundaryIds))
    ) {
      ctx.addIssue({ code: "custom", message: "evidence ids must be unique" });
    }
    if (!isUnique(wordIndexes.map(String))) {
      ctx.addIssue({ code: "custom", message: "word indexes must be unique" });
    }
    const groundedWords = evidence.sentences.flatMap(
      (sentence) => sentence.wordIds
    );
    if (
      groundedWords.length !== wordIds.length ||
      groundedWords.some((id, index) => id !== wordIds[index])
    ) {
      ctx.addIssue({
        code: "custom",
        message:
          "sentences must cover every evidence word exactly once and in order",
        path: ["sentences"],
      });
    }
    const knownWords = new Set(wordIds);
    const knownSentences = new Set(sentenceIds);
    for (const [index, pause] of evidence.pauses.entries()) {
      for (const wordId of [pause.leftWordId, pause.rightWordId]) {
        if (wordId !== null && !knownWords.has(wordId)) {
          ctx.addIssue({
            code: "custom",
            message: "pause refers to an unknown word",
            path: ["pauses", index],
          });
        }
      }
    }
    for (const [index, boundary] of evidence.boundaries.entries()) {
      if (
        boundary.sentenceId !== null &&
        !knownSentences.has(boundary.sentenceId)
      ) {
        ctx.addIssue({
          code: "custom",
          message: "boundary refers to an unknown sentence",
          path: ["boundaries", index, "sentenceId"],
        });
      }
    }
    const boundedTimes = [
      ...evidence.words.flatMap((word) => [word.startMs, word.endMs]),
      ...evidence.sentences.flatMap((sentence) => [
        sentence.startMs,
        sentence.endMs,
      ]),
      ...evidence.pauses.flatMap((pause) => [pause.startMs, pause.endMs]),
      ...evidence.boundaries.map((boundary) => boundary.timeMs),
      ...evidence.shots.map((shot) => shot.timeMs),
      ...evidence.speechCoverage.intervals.flatMap((interval) => [
        interval.startMs,
        interval.endMs,
      ]),
    ];
    if (boundedTimes.some((time) => time > evidence.durationMs)) {
      ctx.addIssue({
        code: "custom",
        message: "evidence time lies outside the source duration",
      });
    }
  })
  .meta({ id: "HarnessEvidence", title: "HarnessEvidence" });

export type HarnessEvidence = z.infer<typeof HarnessEvidenceSchema>;

export const ChapterProposalSectionSchema = z
  .object({
    firstSentenceId: nonemptyId(),
    id: nonemptyId(),
    kind: z.enum(["keep", "drop"]),
    lastSentenceId: nonemptyId(),
    quoteWordIds: z.array(nonemptyId()),
    reason: z.string(),
    title: z.string(),
  })
  .strict()
  .meta({
    id: "ChapterProposalSection",
    title: "ChapterProposalSection",
  });

export type ChapterProposalSection = z.infer<
  typeof ChapterProposalSectionSchema
>;

export const ChapterProposalSchema = z
  .object({
    sections: z.array(ChapterProposalSectionSchema).min(1),
    summary: z.string(),
    version: z.literal(1),
  })
  .strict()
  .refine(
    (proposal) => isUnique(proposal.sections.map((section) => section.id)),
    {
      error: "proposal section ids must be unique",
    }
  )
  .meta({ id: "ChapterProposal", title: "ChapterProposal" });

export type ChapterProposal = z.infer<typeof ChapterProposalSchema>;

function roundedMilliseconds(time: RationalTime): bigint {
  const numeratorMillisTwice = BigInt(time.numerator) * BigInt(2000);
  const denominator = BigInt(time.denominator);
  return (numeratorMillisTwice + denominator) / (BigInt(2) * denominator);
}

function compareRational(left: RationalTime, right: RationalTime): number {
  const leftScaled = BigInt(left.numerator) * BigInt(right.denominator);
  const rightScaled = BigInt(right.numerator) * BigInt(left.denominator);
  if (leftScaled < rightScaled) {
    return -1;
  }
  if (leftScaled > rightScaled) {
    return 1;
  }
  return 0;
}

export const ChapterBoundarySchema = z
  .object({
    candidateId: nonemptyId().nullable(),
    id: nonemptyId(),
    reasons: z.array(z.string()),
    requiresReview: z.boolean(),
    time: RationalTimeSchema,
    timeMs: safeNonnegativeInteger(),
  })
  .strict()
  .refine(
    (boundary) =>
      roundedMilliseconds(boundary.time) === BigInt(boundary.timeMs),
    { error: "timeMs must be the rounded rational source-relative instant" }
  )
  .meta({ id: "ChapterBoundary", title: "ChapterBoundary" });

export type ChapterBoundary = z.infer<typeof ChapterBoundarySchema>;

export const ChapterSectionSchema = z
  .object({
    endBoundaryId: nonemptyId(),
    flags: z.array(z.string()),
    id: nonemptyId(),
    kind: z.enum(["keep", "drop"]),
    quoteWordIds: z.array(nonemptyId()),
    reason: z.string(),
    reviewState: z.enum(["proposed", "accepted", "rejected"]),
    startBoundaryId: nonemptyId(),
    title: z.string(),
  })
  .strict()
  .meta({ id: "ChapterSection", title: "ChapterSection" });

export type ChapterSection = z.infer<typeof ChapterSectionSchema>;

export const ChapterEditSpecSchema = z
  .object({
    boundaries: z.array(ChapterBoundarySchema).min(2),
    compilerVersion: z.string().min(1),
    durationMs: safeNonnegativeInteger(),
    evidenceArtifactId: z.uuid(),
    evidenceSha256: sha256(),
    sections: z.array(ChapterSectionSchema).min(1),
    sourceAudioSampleRate: safePositiveInteger().nullable(),
    sourceFrameRate: PositiveRationalSchema.nullable(),
    sourceId: z.uuid(),
    version: z.literal(1),
  })
  .strict()
  .superRefine((edit, ctx) => {
    if (!isUnique(edit.boundaries.map((boundary) => boundary.id))) {
      ctx.addIssue({ code: "custom", message: "boundary ids must be unique" });
    }
    if (!isUnique(edit.sections.map((section) => section.id))) {
      ctx.addIssue({ code: "custom", message: "section ids must be unique" });
    }
    const finalBoundary = edit.boundaries.at(-1);
    if (
      edit.sections.length !== edit.boundaries.length - 1 ||
      edit.boundaries[0]?.time.numerator !== 0 ||
      !finalBoundary ||
      BigInt(finalBoundary.time.numerator) * BigInt(1000) !==
        BigInt(edit.durationMs) * BigInt(finalBoundary.time.denominator)
    ) {
      ctx.addIssue({
        code: "custom",
        message: "sections and boundaries must exactly cover the source",
      });
      return;
    }
    for (const [index, section] of edit.sections.entries()) {
      const start = edit.boundaries[index];
      const end = edit.boundaries[index + 1];
      if (
        !(start && end) ||
        compareRational(start.time, end.time) >= 0 ||
        section.startBoundaryId !== start.id ||
        section.endBoundaryId !== end.id
      ) {
        ctx.addIssue({
          code: "custom",
          message: "each neighboring section must share exactly one boundary",
          path: ["sections", index],
        });
      }
    }
  })
  .meta({ id: "ChapterEditSpec", title: "ChapterEditSpec" });

export type ChapterEditSpec = z.infer<typeof ChapterEditSpecSchema>;

export const ChapterCheckSchema = z
  .object({
    expected: z.number().finite().nullable(),
    measured: z.number().finite().nullable(),
    message: z.string(),
    name: z.string().min(1),
    sectionId: nonemptyId(),
    status: z.enum(["pass", "warn", "fail"]),
  })
  .strict()
  .meta({ id: "ChapterCheck", title: "ChapterCheck" });

export type ChapterCheck = z.infer<typeof ChapterCheckSchema>;

export const ChapterChecksSchema = z
  .object({
    editorialReasons: z.array(z.string()),
    editorialStatus: z.enum(["not_run", "passed", "needs_review", "failed"]),
    editSha256: sha256(),
    technicalChecks: z.array(ChapterCheckSchema),
    verifierFamily: z.string().nullable(),
    version: z.literal(1),
  })
  .strict()
  .meta({ id: "ChapterChecks", title: "ChapterChecks" });

export type ChapterChecks = z.infer<typeof ChapterChecksSchema>;

export const ChapterRenderSchema = z
  .object({
    captions: HarnessArtifactRefSchema.nullable(),
    checks: HarnessArtifactRefSchema.nullable(),
    durationMs: safeNonnegativeInteger(),
    editSha256: sha256(),
    media: HarnessArtifactRefSchema,
    sectionId: nonemptyId(),
  })
  .strict()
  .meta({ id: "ChapterRender", title: "ChapterRender" });

export type ChapterRender = z.infer<typeof ChapterRenderSchema>;

/** One immutable descriptor for every chapter rendered from an edit revision. */
export const ChapterRendersSchema = z
  .object({
    editSha256: sha256(),
    format: z.literal("chapter-renders/1"),
    renders: z.array(ChapterRenderSchema),
    runId: z.uuid(),
  })
  .strict()
  .superRefine((descriptor, ctx) => {
    for (const [index, render] of descriptor.renders.entries()) {
      if (render.editSha256 !== descriptor.editSha256) {
        ctx.addIssue({
          code: "custom",
          message: "every render must name the descriptor edit",
          path: ["renders", index, "editSha256"],
        });
      }
    }
  })
  .meta({ id: "ChapterRenders", title: "ChapterRenders" });

export type ChapterRenders = z.infer<typeof ChapterRendersSchema>;

export const ChapterExportSchema = z
  .object({
    chapters: z.array(ChapterRenderSchema),
    createdAt: z.iso.datetime({ offset: true }),
    editSha256: sha256(),
    manifestKey: z.string().min(1).max(2048),
    revision: safePositiveInteger(),
    runId: z.uuid(),
    version: z.literal(1),
  })
  .strict()
  .meta({ id: "ChapterExport", title: "ChapterExport" });

export type ChapterExport = z.infer<typeof ChapterExportSchema>;

export const HarnessRunStatusSchema = z
  .enum([
    "pending",
    "running",
    "needs_review",
    "ready",
    "budget_paused",
    "outcome_unknown",
    "failed",
    "cancelled",
  ])
  .meta({ id: "HarnessRunStatus", title: "HarnessRunStatus" });

export type HarnessRunStatus = z.infer<typeof HarnessRunStatusSchema>;

export const ChapterRunConfigSchema = z
  .object({
    backend: z.enum(["recorded", "gateway"]),
    evidenceWindowSentences: z.int().min(1).max(512).default(80),
    maxDispatches: z.int().min(1).max(128).default(32),
    maxOutputTokens: z.int().min(256).max(65_536).default(8192),
    maxRenderConcurrency: z.int().min(1).max(4).default(2),
    maxRepairs: z.int().min(0).max(3).default(3),
    routeSnapshotId: z.string().min(1).max(256),
  })
  .strict()
  .meta({ id: "ChapterRunConfig", title: "ChapterRunConfig" });

export type ChapterRunConfig = z.infer<typeof ChapterRunConfigSchema>;

/**
 * One committed file per deployment carries the whole harness configuration; both images
 * read it, so the web's run config equals the worker's by construction. `routeSnapshot.path`
 * is relative to the file; `id` is the snapshot's canonical SHA-256, checked at worker boot.
 */
export const HarnessConfigSchema = z
  .object({
    allowRecorded: z.boolean(),
    backend: z.enum(["recorded", "gateway"]),
    enabled: z.boolean(),
    format: z.literal("harness-config/1"),
    gateway: z.enum(["vercel", "openrouter"]),
    limits: z
      .object({
        evidenceWindowSentences: z.int().min(1).max(512),
        maxDispatches: z.int().min(1).max(128),
        maxOutputTokens: z.int().min(256).max(65_536),
        maxRenderConcurrency: z.int().min(1).max(4),
        maxRepairs: z.int().min(0).max(3),
        maxRunBudgetMicros: z.int().positive(),
      })
      .strict()
      .meta({ id: "HarnessConfigLimits", title: "HarnessConfigLimits" }),
    recordedFixturePath: z.string().min(1).nullable(),
    routeSnapshot: z
      .object({
        id: z.string().regex(/^[a-f0-9]{64}$/),
        path: z.string().min(1),
      })
      .strict()
      .meta({
        id: "HarnessConfigRouteSnapshot",
        title: "HarnessConfigRouteSnapshot",
      }),
    topicShotDetector: z.enum(["pyscenedetect-adaptive", "scdet"]),
  })
  .strict()
  .meta({ id: "HarnessConfig", title: "HarnessConfig" });
export type HarnessConfig = z.infer<typeof HarnessConfigSchema>;

export const ChapterRunInputSchema = z
  .object({
    // Absent means the worker applies the lane's single default brief and
    // freezes the effective text on the run row.
    brief: z.string().max(100_000).optional(),
    budgetMicros: safePositiveInteger(),
    config: ChapterRunConfigSchema,
    requestKey: z.uuid(),
    runId: z.uuid(),
    scope: ScopeSchema,
    sourceId: z.uuid(),
  })
  .strict()
  .meta({ id: "ChapterRunInput", title: "ChapterRunInput" });

export type ChapterRunInput = z.infer<typeof ChapterRunInputSchema>;

export const ChapterRunOutputSchema = z
  .object({
    editArtifact: HarnessArtifactRefSchema.nullable(),
    errorMessage: z.string().nullable(),
    evidenceArtifact: HarnessArtifactRefSchema.nullable(),
    revision: safePositiveInteger().nullable(),
    runId: z.uuid(),
    status: HarnessRunStatusSchema,
  })
  .strict()
  .meta({ id: "ChapterRunOutput", title: "ChapterRunOutput" });

export type ChapterRunOutput = z.infer<typeof ChapterRunOutputSchema>;

export const ChapterReviewActionSchema = z
  .enum([
    "accept",
    "reject",
    "nudge",
    "restore",
    "merge",
    "undo",
    "retry",
    "cancel",
    "raise_budget",
  ])
  .meta({ id: "ChapterReviewAction", title: "ChapterReviewAction" });

export type ChapterReviewAction = z.infer<typeof ChapterReviewActionSchema>;

export const ChapterReviewInputSchema = z
  .object({
    action: ChapterReviewActionSchema,
    baseRevision: safeNonnegativeInteger(),
    boundaryId: nonemptyId().nullable(),
    budgetMicros: safePositiveInteger().nullable(),
    mutationKey: z.uuid(),
    otherSectionId: nonemptyId().nullable(),
    reason: z.string(),
    runId: z.uuid(),
    scope: ScopeSchema,
    sectionId: nonemptyId().nullable(),
    sourceId: z.uuid(),
    targetRevision: safePositiveInteger().nullable(),
    targetTimeMs: safeNonnegativeInteger().nullable(),
  })
  .strict()
  .superRefine((input, ctx) => {
    const requireFields = (fields: (keyof typeof input)[]) => {
      for (const field of fields) {
        if (input[field] === null) {
          ctx.addIssue({
            code: "custom",
            message: `${field} is required for ${input.action}`,
            path: [field],
          });
        }
      }
    };
    if (
      input.action === "accept" ||
      input.action === "reject" ||
      input.action === "restore"
    ) {
      requireFields(["sectionId"]);
    } else if (input.action === "nudge") {
      requireFields(["boundaryId", "targetTimeMs"]);
    } else if (input.action === "merge") {
      requireFields(["sectionId", "otherSectionId"]);
    } else if (input.action === "raise_budget") {
      requireFields(["budgetMicros"]);
    } else if (input.action === "undo") {
      requireFields(["targetRevision"]);
      if (
        input.targetRevision !== null &&
        input.targetRevision >= input.baseRevision
      ) {
        ctx.addIssue({
          code: "custom",
          message: "targetRevision must be earlier than baseRevision",
          path: ["targetRevision"],
        });
      }
    }
    if (input.action !== "undo" && input.targetRevision !== null) {
      ctx.addIssue({
        code: "custom",
        message: "targetRevision is only valid for undo",
        path: ["targetRevision"],
      });
    }
  })
  .meta({ id: "ChapterReviewInput", title: "ChapterReviewInput" });

export type ChapterReviewInput = z.infer<typeof ChapterReviewInputSchema>;

export const ChapterReviewOutputSchema = z
  .object({
    message: z.string(),
    mutationKey: z.uuid(),
    revision: safePositiveInteger().nullable(),
    runId: z.uuid(),
    state: z.enum(["applied", "conflict", "refused"]),
  })
  .strict()
  .meta({ id: "ChapterReviewOutput", title: "ChapterReviewOutput" });

export type ChapterReviewOutput = z.infer<typeof ChapterReviewOutputSchema>;
