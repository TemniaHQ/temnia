import { z } from "zod";
import { ScopeSchema } from "./scope.ts";

/**
 * Input to the ingest workflow. The web app resolves scope, creates the source
 * row, and starts this after the multipart upload completes. Keys are computed
 * by the storage helpers; the pipeline writes only under `artifactPrefix`.
 */
export const IngestInputSchema = z
  .object({
    artifactPrefix: z
      .string()
      .min(1)
      .describe(
        "Prefix every derived artifact is written under; ends with '/'."
      ),
    masterKey: z.string().min(1).describe("The uploaded master's storage key."),
    scope: ScopeSchema,
    sourceId: z.uuid(),
  })
  .meta({ id: "IngestInput", title: "IngestInput" });

export type IngestInput = z.infer<typeof IngestInputSchema>;

/** What the pipeline learned from probing the master. */
export const ProbeResultSchema = z
  .object({
    audioChannels: z.int().nonnegative().nullable(),
    audioCodec: z.string().nullable(),
    durationMs: z.int().positive(),
    fps: z.number().positive().nullable(),
    height: z.int().positive().nullable(),
    sizeBytes: z.int().nonnegative(),
    variableFrameRate: z.boolean(),
    videoCodec: z.string().nullable(),
    width: z.int().positive().nullable(),
  })
  .meta({ id: "ProbeResult", title: "ProbeResult" });

export type ProbeResult = z.infer<typeof ProbeResultSchema>;

export const ArtifactKindSchema = z
  .enum(["master", "hls", "peaks", "thumbnails", "audio", "shots"])
  .meta({ id: "ArtifactKind", title: "ArtifactKind" });

/** One artifact the pipeline produced; the finalize step writes a row per entry. */
export const ArtifactRecordSchema = z
  .object({
    contentType: z.string().min(1),
    kind: ArtifactKindSchema,
    metadata: z.record(z.string(), z.unknown()),
    sizeBytes: z.int().nonnegative(),
    storageKey: z.string().min(1),
    storagePrefix: z.string().nullable(),
  })
  .meta({ id: "ArtifactRecord", title: "ArtifactRecord" });

export type ArtifactRecord = z.infer<typeof ArtifactRecordSchema>;

/** Stage names the ingest workflow reports on the source row. */
export const IngestStageSchema = z
  .enum([
    "probe",
    "hls",
    "publish",
    "thumbnails",
    "audio",
    "peaks",
    "shots",
    "finalize",
  ])
  .meta({ id: "IngestStage", title: "IngestStage" });

export type IngestStage = z.infer<typeof IngestStageSchema>;

/** Result of the ingest workflow, echoed to the web app. */
export const IngestOutputSchema = z
  .object({
    artifacts: z.array(ArtifactRecordSchema),
    organizationId: z
      .uuid()
      .describe("Echoed from the input scope, never invented."),
    probe: ProbeResultSchema,
    processingSeconds: z
      .int()
      .nonnegative()
      .describe("Wall-clock seconds the worker spent."),
    sourceId: z.uuid(),
    storageBytes: z
      .int()
      .nonnegative()
      .describe("Sum of the artifact rows written."),
  })
  .meta({ id: "IngestOutput", title: "IngestOutput" });

export type IngestOutput = z.infer<typeof IngestOutputSchema>;
