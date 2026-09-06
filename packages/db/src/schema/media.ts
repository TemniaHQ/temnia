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
    organizationPolicy("usage_ledger", table.organizationId),
  ]
).enableRLS();
