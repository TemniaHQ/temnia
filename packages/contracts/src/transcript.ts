import { z } from "zod";
import { ScopeSchema } from "./scope.ts";

/**
 * The transcript, as both languages see it.
 *
 * The canonical transcript is a JSON object in storage under the source's
 * prefix (`transcript/rev-{N}.json`), never an artifact row: re-ingesting a
 * source clears its artifact rows, and a paid transcript must survive that
 * (S2 plan §9). Revisions are new objects; nothing is ever overwritten.
 *
 * Times are integer milliseconds everywhere. The provider boundary is the one
 * place a float second is rounded, in the normaliser, so no later stage can
 * introduce a different rounding of the same word.
 */

/** How a word's timing was arrived at. Interpolated words are shown as approximate. */
export const WordTimingSchema = z
  .enum(["aligned", "interpolated"])
  .meta({ id: "WordTiming", title: "WordTiming" });

export type WordTiming = z.infer<typeof WordTimingSchema>;

/**
 * One word. `speaker` is a diarization id (normalised to "0", "1", …) or null
 * when no turn covered the word; the display name comes from the transcript
 * row's speaker labels, so renaming a speaker never rewrites a revision.
 */
export const TranscriptWordSchema = z
  .object({
    confidence: z
      .number()
      .min(0)
      .max(1)
      .nullable()
      .describe("Alignment score, or null when the provider gave none."),
    endMs: z.int().nonnegative(),
    speaker: z.string().nullable(),
    startMs: z.int().nonnegative(),
    text: z.string().min(1),
    timing: WordTimingSchema,
  })
  .refine((word) => word.endMs >= word.startMs, {
    error: "a word cannot end before it starts",
  })
  .meta({ id: "TranscriptWord", title: "TranscriptWord" });

export type TranscriptWord = z.infer<typeof TranscriptWordSchema>;

/** A run of consecutive words by one speaker. Derived from the words, never authored. */
export const TranscriptUtteranceSchema = z
  .object({
    endMs: z.int().nonnegative(),
    speaker: z.string().nullable(),
    startMs: z.int().nonnegative(),
  })
  .meta({ id: "TranscriptUtterance", title: "TranscriptUtterance" });

export type TranscriptUtterance = z.infer<typeof TranscriptUtteranceSchema>;

/** Which engine produced a revision; recorded so a re-run is comparable. */
export const TranscriptProviderSchema = z
  .object({
    model: z.string().min(1),
    name: z.string().min(1),
    version: z.string().min(1),
  })
  .meta({ id: "TranscriptProvider", title: "TranscriptProvider" });

export type TranscriptProvider = z.infer<typeof TranscriptProviderSchema>;

/**
 * Words must ride in ascending start order, so the viewer's binary search and
 * the cue builder can both trust the array they are handed.
 */
function sortedByStart(words: readonly TranscriptWord[]): boolean {
  return words.every(
    (word, index) =>
      index === 0 || word.startMs >= (words[index - 1]?.startMs ?? 0)
  );
}

/**
 * The check is one-directional on purpose (S2 plan §9): a word ending after the
 * media does means the transcript belongs to different bytes than the ones we
 * measured, which is a failed run. A transcript that stops early is silence.
 */
export const DURATION_SLACK_MS = 2000;

export const TranscriptV1Schema = z
  .object({
    durationMs: z.int().nonnegative(),
    language: z
      .string()
      .min(1)
      .describe("The detected language code, so a wrong guess is visible."),
    provider: TranscriptProviderSchema,
    speakers: z
      .array(z.string())
      .describe("Every speaker id used by a word, in first-appearance order."),
    utterances: z.array(TranscriptUtteranceSchema),
    version: z.literal(1),
    words: z.array(TranscriptWordSchema),
  })
  .refine((transcript) => sortedByStart(transcript.words), {
    error: "words must be sorted by startMs",
  })
  .refine(
    (transcript) =>
      transcript.words.every(
        (word) => word.endMs <= transcript.durationMs + DURATION_SLACK_MS
      ),
    {
      error:
        "a word ends after the recording does; the transcript is not of this media",
    }
  )
  .meta({ id: "TranscriptV1", title: "TranscriptV1" });

export type TranscriptV1 = z.infer<typeof TranscriptV1Schema>;

/**
 * Input to `TranscribeWorkflow`. Transcription is its own workflow, started as
 * an abandoned child after the ingest finalizes, so a transcription failure
 * never touches a finished ingest.
 */
export const TranscribeInputSchema = z
  .object({
    artifactPrefix: z
      .string()
      .min(1)
      .describe("The source prefix; the transcript is written under it."),
    audioKey: z
      .string()
      .min(1)
      .describe("The audio extract the provider reads; never the master."),
    durationMs: z
      .int()
      .positive()
      .describe("The probed duration; what the engine is metered against."),
    scope: ScopeSchema,
    sourceId: z.uuid(),
  })
  .meta({ id: "TranscribeInput", title: "TranscribeInput" });

export type TranscribeInput = z.infer<typeof TranscribeInputSchema>;

/** What the workflow reports back: the revision it wrote and what produced it. */
export const TranscribeOutputSchema = z
  .object({
    durationMs: z.int().nonnegative(),
    language: z.string().min(1),
    organizationId: z
      .uuid()
      .describe("Echoed from the input scope, never invented."),
    provider: TranscriptProviderSchema,
    revision: z.int().positive(),
    sourceId: z.uuid(),
    speakerCount: z.int().nonnegative(),
    storageKey: z.string().min(1),
    wordCount: z.int().nonnegative(),
  })
  .meta({ id: "TranscribeOutput", title: "TranscribeOutput" });

export type TranscribeOutput = z.infer<typeof TranscribeOutputSchema>;

/**
 * Stage names the transcription workflow writes on the transcript row.
 * `retrying` is the state between two attempts: the row stays `processing`, so
 * the surface never flashes Failed while a retry is coming (S2 plan §5).
 */
export const TranscriptStageSchema = z
  .enum([
    "planning",
    "download",
    "model",
    "transcribe",
    "align",
    "diarize",
    "speech_coverage",
    "write",
    "retrying",
  ])
  .meta({ id: "TranscriptStage", title: "TranscriptStage" });

export type TranscriptStage = z.infer<typeof TranscriptStageSchema>;

/** Where a transcript revision lives, relative to the source prefix. */
export const TRANSCRIPT_SUBDIR = "transcript/";

export function transcriptRevisionKey(
  artifactPrefix: string,
  revision: number
): string {
  return `${artifactPrefix}${TRANSCRIPT_SUBDIR}rev-${revision}.json`;
}

/**
 * Where a correction is written before it is published.
 *
 * Two tabs saving against the same revision both compute the same next number
 * and, with one key per number, the loser's bytes could sit behind the
 * winner's pointer (S2 review, I03). Each attempt therefore writes its own
 * object, and the compare-and-swap on the transcript row publishes exactly one
 * of them; readers take the key from the revision row, never from the number.
 */
export function transcriptCorrectionKey(
  artifactPrefix: string,
  revision: number,
  attempt: string
): string {
  return `${artifactPrefix}${TRANSCRIPT_SUBDIR}rev-${revision}-${attempt}.json`;
}

/** The provider's untouched response, kept for fixtures and S12 calibration. */
export function transcriptRawKey(
  artifactPrefix: string,
  attempt: number
): string {
  return `${artifactPrefix}${TRANSCRIPT_SUBDIR}raw-${attempt}.json`;
}

const identityId = () => z.string().min(1).max(256);
const uniqueIdentityIds = z
  .array(identityId())
  .max(500)
  .refine((values) => new Set(values).size === values.length, {
    error: "identity ids must be unique",
  });

export const TranscriptWordIdentitySchema = z
  .object({
    id: identityId(),
    parentIds: uniqueIdentityIds,
    timingOrigin: z.enum(["provider", "manual"]),
  })
  .strict()
  .meta({ id: "TranscriptWordIdentity", title: "TranscriptWordIdentity" });
export type TranscriptWordIdentity = z.infer<
  typeof TranscriptWordIdentitySchema
>;

export const TranscriptSpeakerIdentitySchema = z
  .object({
    identityId: identityId(),
    label: z.string().max(80),
    parentIdentityIds: uniqueIdentityIds,
  })
  .strict()
  .meta({
    id: "TranscriptSpeakerIdentity",
    title: "TranscriptSpeakerIdentity",
  });
export type TranscriptSpeakerIdentity = z.infer<
  typeof TranscriptSpeakerIdentitySchema
>;

export const TranscriptRevisionAnnotationsSchema = z
  .object({
    speakerIdentities: z.record(
      z.string().min(1).max(256),
      TranscriptSpeakerIdentitySchema
    ),
    version: z.literal(1),
    wordIdentities: z.array(TranscriptWordIdentitySchema).max(250_000),
  })
  .strict()
  .superRefine((annotations, ctx) => {
    const wordIds = annotations.wordIdentities.map((identity) => identity.id);
    if (new Set(wordIds).size !== wordIds.length) {
      ctx.addIssue({
        code: "custom",
        message: "word identity ids must be unique",
        path: ["wordIdentities"],
      });
    }
    const speakerIds = Object.values(annotations.speakerIdentities).map(
      (identity) => identity.identityId
    );
    if (new Set(speakerIds).size !== speakerIds.length) {
      ctx.addIssue({
        code: "custom",
        message: "speaker identity ids must be unique",
        path: ["speakerIdentities"],
      });
    }
  })
  .meta({
    id: "TranscriptRevisionAnnotations",
    title: "TranscriptRevisionAnnotations",
  });
export type TranscriptRevisionAnnotations = z.infer<
  typeof TranscriptRevisionAnnotationsSchema
>;

const commandBase = {
  baseRevision: z.int().positive().max(Number.MAX_SAFE_INTEGER),
  mutationKey: z.uuid(),
};
const targetIds = uniqueIdentityIds.min(1).max(200);
const manualTokens = z.array(z.string().trim().min(1).max(200)).min(1).max(50);

export const TranscriptCorrectionCommandSchema = z
  .discriminatedUnion("action", [
    z
      .object({
        ...commandBase,
        action: z.literal("replace"),
        targetId: identityId(),
        text: z.string().trim().min(1).max(200),
      })
      .strict(),
    z
      .object({ ...commandBase, action: z.literal("delete"), targetIds })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("insert"),
        anchorId: identityId().nullable(),
        endMs: z.int().nonnegative(),
        side: z.enum(["before", "after"]),
        speakerIdentityId: identityId().nullable(),
        startMs: z.int().nonnegative(),
        tokens: manualTokens,
      })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("split"),
        targetId: identityId(),
        tokens: manualTokens,
      })
      .strict(),
    z
      .object({ ...commandBase, action: z.literal("merge"), targetIds })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("rename_speakers"),
        labels: z.record(identityId(), z.string().trim().min(1).max(80)),
      })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("reassign_speaker"),
        targetIds,
        targetSpeakerIdentityId: identityId(),
      })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("merge_speakers"),
        sourceIdentityIds: targetIds,
        targetIdentityId: identityId(),
      })
      .strict(),
    z
      .object({
        ...commandBase,
        action: z.literal("undo"),
        targetRevision: z.int().positive().max(Number.MAX_SAFE_INTEGER),
      })
      .strict(),
  ])
  .superRefine((command, ctx) => {
    if (
      command.action === "undo" &&
      command.targetRevision >= command.baseRevision
    ) {
      ctx.addIssue({
        code: "custom",
        message: "undo targetRevision must be earlier than baseRevision",
        path: ["targetRevision"],
      });
    }
  });
export type TranscriptCorrectionCommand = z.infer<
  typeof TranscriptCorrectionCommandSchema
>;

export const TranscriptCorrectionMetadataSchema = z
  .object({
    action: z.enum([
      "replace",
      "delete",
      "insert",
      "split",
      "merge",
      "rename_speakers",
      "reassign_speaker",
      "merge_speakers",
      "undo",
    ]),
    affectedIdentityIds: uniqueIdentityIds,
    annotations: TranscriptRevisionAnnotationsSchema,
    artifactSha256: z.string().regex(/^[a-fA-F0-9]{64}$/),
    baseRevision: z.int().positive().max(Number.MAX_SAFE_INTEGER),
    command: TranscriptCorrectionCommandSchema,
    deletedIdentityIds: uniqueIdentityIds,
    mutationKey: z.uuid(),
  })
  .strict()
  .meta({
    id: "TranscriptCorrectionMetadata",
    title: "TranscriptCorrectionMetadata",
  });
export type TranscriptCorrectionMetadata = z.infer<
  typeof TranscriptCorrectionMetadataSchema
>;
