/**
 * S1: projects, sources, uploads, artifacts, and the usage ledger.
 *
 * A source is one uploaded master and everything derived from it. Its
 * lifecycle is `uploading → uploaded → processing → ready | failed` (PRD §4);
 * the ingest workflow owns the transitions after `uploaded`. Storage keys are
 * prefixed `org/{organizationId}/...` and that prefix is the authorization
 * boundary for every read (PRD §3), including the media proxy.
 */
import { sql } from "drizzle-orm";
import {
  bigint,
  index,
  integer,
  jsonb,
  numeric,
  pgEnum,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";
import { createdAt, id, updatedAt } from "./columns.ts";
import { user } from "./identity.ts";
import { organizationId, organizationPolicy } from "./tenant.ts";

const timestamptz = (name: string) =>
  timestamp(name, { mode: "date", withTimezone: true });

export const project = pgTable(
  "project",
  {
    createdAt: createdAt(),
    createdBy: uuid("created_by").references(() => user.id, {
      onDelete: "set null",
    }),
    id: id(),
    name: text("name").notNull(),
    organizationId: organizationId(),
    updatedAt: updatedAt(),
  },
  (table) => [
    index("project_organization_idx").on(table.organizationId, table.createdAt),
    organizationPolicy("project", table.organizationId),
  ]
).enableRLS();

export const sourceStatus = pgEnum("source_status", [
  "uploading",
  "uploaded",
  "processing",
  "ready",
  "failed",
]);

export const source = pgTable(
  "source",
  {
    audioChannels: integer("audio_channels"),
    audioCodec: text("audio_codec"),
    contentType: text("content_type").notNull(),
    createdAt: createdAt(),
    createdBy: uuid("created_by").references(() => user.id, {
      onDelete: "set null",
    }),
    /** Probed after upload; null until then. */
    durationMs: integer("duration_ms"),
    errorMessage: text("error_message"),
    fps: numeric("fps", { precision: 8, scale: 3 }),
    height: integer("height"),
    id: id(),
    ingestHeartbeatAt: timestamptz("ingest_heartbeat_at"),
    ingestPercent: integer("ingest_percent"),
    ingestStage: text("ingest_stage"),
    /** Ingest progress as the workflow reports it: stage, percent, message. */
    ingestWorkflowId: text("ingest_workflow_id"),
    /** The master's storage key, `org/{org}/source/{source}/master.{ext}`. */
    masterKey: text("master_key").notNull(),
    organizationId: organizationId(),
    originalFilename: text("original_filename").notNull(),
    projectId: uuid("project_id")
      .notNull()
      .references(() => project.id, { onDelete: "cascade" }),
    readyAt: timestamptz("ready_at"),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    status: sourceStatus("status").notNull().default("uploading"),
    title: text("title").notNull(),
    updatedAt: updatedAt(),
    uploadedAt: timestamptz("uploaded_at"),
    videoCodec: text("video_codec"),
    width: integer("width"),
  },
  (table) => [
    index("source_project_idx").on(table.projectId, table.createdAt),
    index("source_organization_status_idx").on(
      table.organizationId,
      table.status
    ),
    organizationPolicy("source", table.organizationId),
  ]
).enableRLS();

export const uploadStatus = pgEnum("upload_status", [
  "active",
  "completed",
  "aborted",
]);

/**
 * One S3 multipart upload for a source's master. The browser holds the upload
 * identity only in its uploader's file state; this row is what lets a
 * re-selected file, in any browser, reconnect to the same multipart upload
 * (adoption by fingerprint) and what the reaper aborts after the resume window.
 */
export const upload = pgTable(
  "upload",
  {
    completedAt: timestamptz("completed_at"),
    createdAt: createdAt(),
    /** sha256 of name, size, and lastModified: the same file re-selected has the same fingerprint. */
    fingerprint: text("fingerprint").notNull(),
    id: id(),
    lastActivityAt: timestamptz("last_activity_at").notNull().defaultNow(),
    /** The store's multipart UploadId. */
    multipartUploadId: text("multipart_upload_id").notNull(),
    organizationId: organizationId(),
    partSizeBytes: integer("part_size_bytes").notNull(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    sourceId: uuid("source_id")
      .notNull()
      .references(() => source.id, { onDelete: "cascade" }),
    status: uploadStatus("status").notNull().default("active"),
    storageKey: text("storage_key").notNull(),
  },
  (table) => [
    uniqueIndex("upload_active_fingerprint_idx")
      .on(table.organizationId, table.fingerprint)
      .where(sql`${table.status} = 'active'`),
    index("upload_status_activity_idx").on(table.status, table.lastActivityAt),
    organizationPolicy("upload", table.organizationId),
  ]
).enableRLS();

export const artifactKind = pgEnum("artifact_kind", [
  "master",
  "hls",
  "peaks",
  "thumbnails",
  "audio",
  "shots",
]);

/**
 * A derived (or the original) file set. `storage_key` is the entry point (the
 * master playlist, the peaks file, the sprite index); `storage_prefix` is set
 * when the artifact is a directory of files (HLS segments, thumbnails) and
 * `size_bytes` is the total over that prefix. The storage ledger entry for a
 * source is summed from these rows inside the finalize transaction.
 */
export const artifact = pgTable(
  "artifact",
  {
    contentType: text("content_type").notNull(),
    createdAt: createdAt(),
    id: id(),
    kind: artifactKind("kind").notNull(),
    /** Kind-specific facts: renditions and durations for hls, samples_per_pixel for peaks, interval for thumbnails. */
    metadata: jsonb("metadata")
      .$type<Record<string, unknown>>()
      .notNull()
      .default({}),
    organizationId: organizationId(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    sourceId: uuid("source_id")
      .notNull()
      .references(() => source.id, { onDelete: "cascade" }),
    storageKey: text("storage_key").notNull(),
    storagePrefix: text("storage_prefix"),
  },
  (table) => [
    uniqueIndex("artifact_source_kind_idx").on(table.sourceId, table.kind),
    organizationPolicy("artifact", table.organizationId),
  ]
).enableRLS();

export const usageKind = pgEnum("usage_kind", [
  "storage_bytes",
  "processing_seconds",
  "transcription_seconds",
]);

/**
 * Append-only. Anything metered writes here in the sprint it ships (sprint plan
 * §4 rule 1). Quantities are signed so a deleted source can write a negative
 * storage entry; the current storage figure is the sum.
 */
export const usageLedger = pgTable(
  "usage_ledger",
  {
    /** What produced the entry: the stage name, the rendition, the reaper. */
    detail: jsonb("detail")
      .$type<Record<string, unknown>>()
      .notNull()
      .default({}),
    id: id(),
    /**
     * Set by writers a retry can repeat (the pipeline's metering activities), so
     * an attempt whose acknowledgement was lost inserts once; the partial unique
     * index below is the guarantee. Web writes that are deltas need none.
     */
    idempotencyKey: text("idempotency_key"),
    kind: usageKind("kind").notNull(),
    organizationId: organizationId(),
    quantity: bigint("quantity", { mode: "number" }).notNull(),
    recordedAt: timestamptz("recorded_at").notNull().defaultNow(),
    sourceId: uuid("source_id").references(() => source.id, {
      onDelete: "set null",
    }),
    workflowId: text("workflow_id"),
  },
  (table) => [
    index("usage_ledger_organization_kind_idx").on(
      table.organizationId,
      table.kind,
      table.recordedAt
    ),
    uniqueIndex("usage_ledger_idempotency_idx")
      .on(table.idempotencyKey)
      .where(sql`${table.idempotencyKey} IS NOT NULL`),
    organizationPolicy("usage_ledger", table.organizationId),
  ]
).enableRLS();

export const transcriptStatus = pgEnum("transcript_status", [
  "pending",
  "processing",
  "ready",
  "failed",
]);

/**
 * One transcript per source. The row is the state and the progress; the words
 * themselves live in storage under `{sourcePrefix}transcript/rev-{N}.json`,
 * never in an artifact row, because re-ingesting a source clears its artifact
 * rows and a paid transcript has to survive that (S2 plan §9). Deleting the
 * source deletes both: the row cascades and the prefix delete takes the JSON.
 *
 * `speakerLabels` maps a diarization id ("0", "1") to the display name a user
 * typed. Renaming a speaker therefore never rewrites a revision, and giving
 * two ids the same name merges them on screen, which is the manual merge the
 * legacy needed after over-segmented diarization.
 */
export const transcript = pgTable(
  "transcript",
  {
    /** How many times transcription has been claimed for this source; the revision an attempt writes. */
    attempts: integer("attempts").notNull().default(0),
    createdAt: createdAt(),
    /** The revision the UI reads; null until the first one is written. */
    currentRevision: integer("current_revision"),
    errorMessage: text("error_message"),
    /** Mirrors the activity's Temporal heartbeat so the surface can say "stalled". */
    heartbeatAt: timestamptz("heartbeat_at"),
    id: id(),
    /** The detected language code, so a wrong guess on a music intro is visible. */
    language: text("language"),
    model: text("model"),
    organizationId: organizationId(),
    percent: integer("percent"),
    provider: text("provider"),
    readyAt: timestamptz("ready_at"),
    /**
     * The Temporal run that owns the row. The claim is idempotent per run (a
     * lost acknowledgement re-claims and gets the same attempt back) and every
     * later write the run makes is fenced on it, so a run that lost the row to
     * a later one can no longer change it.
     */
    runId: text("run_id"),
    sourceId: uuid("source_id")
      .notNull()
      .references(() => source.id, { onDelete: "cascade" }),
    /** Diarization id to the name a user typed; the same name on two ids merges them. */
    speakerLabels: jsonb("speaker_labels")
      .$type<Record<string, string>>()
      .notNull()
      .default({}),
    stage: text("stage"),
    status: transcriptStatus("status").notNull().default("pending"),
    updatedAt: updatedAt(),
    workflowId: text("workflow_id"),
  },
  (table) => [
    // Unique, not just an index: the claim activity upserts on it, which is
    // what makes a second workflow for the same source a no-op rather than a
    // second GPU job.
    uniqueIndex("transcript_source_idx").on(table.sourceId),
    index("transcript_organization_status_idx").on(
      table.organizationId,
      table.status
    ),
    organizationPolicy("transcript", table.organizationId),
  ]
).enableRLS();

export const transcriptRevisionKind = pgEnum("transcript_revision_kind", [
  "machine",
  "correction",
]);

/**
 * A revision is a new object in storage, never an overwrite. `machine` rows
 * come from an engine run, `correction` rows from a user edit and carry the
 * revision they were derived from; a save whose `baseRevision` is no longer
 * the transcript's current revision is refused, which is the optimistic
 * concurrency two open tabs need.
 */
export const transcriptRevision = pgTable(
  "transcript_revision",
  {
    baseRevision: integer("base_revision"),
    createdAt: createdAt(),
    createdBy: uuid("created_by").references(() => user.id, {
      onDelete: "set null",
    }),
    id: id(),
    kind: transcriptRevisionKind("kind").notNull(),
    /** The attempt that produced a machine revision, the edit that produced a correction. */
    metadata: jsonb("metadata")
      .$type<Record<string, unknown>>()
      .notNull()
      .default({}),
    organizationId: organizationId(),
    revision: integer("revision").notNull(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    storageKey: text("storage_key").notNull(),
    transcriptId: uuid("transcript_id")
      .notNull()
      .references(() => transcript.id, { onDelete: "cascade" }),
    wordCount: integer("word_count").notNull(),
  },
  (table) => [
    uniqueIndex("transcript_revision_idx").on(
      table.transcriptId,
      table.revision
    ),
    organizationPolicy("transcript_revision", table.organizationId),
  ]
).enableRLS();
