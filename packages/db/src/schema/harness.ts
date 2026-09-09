import { sql } from "drizzle-orm";
import {
  bigint,
  check,
  foreignKey,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  primaryKey,
  text,
  timestamp,
  unique,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";
import { createdAt, id, updatedAt } from "./columns.ts";
import { user } from "./identity.ts";
import { source, transcript, transcriptRevision } from "./media.ts";
import { organizationId, organizationPolicy } from "./tenant.ts";

const timestamptz = (name: string) =>
  timestamp(name, { mode: "date", withTimezone: true });

const jsonObject = (name: string) =>
  jsonb(name).$type<Record<string, unknown>>().notNull().default({});

export const harnessArtifactKind = pgEnum("harness_artifact_kind", [
  "evidence",
  "proposal",
  "model_response",
  "edit",
  "render",
  "checks",
  "export",
  "speech_checkpoint",
  "speech_assignment",
]);

export const harnessArtifact = pgTable(
  "harness_artifact",
  {
    createdAt: createdAt(),
    fingerprint: text("fingerprint").notNull(),
    id: id(),
    kind: harnessArtifactKind("kind").notNull(),
    metadata: jsonObject("metadata"),
    organizationId: organizationId(),
    sha256: text("sha256").notNull(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    sourceId: uuid("source_id").notNull(),
    storageKey: text("storage_key").notNull(),
    transcriptId: uuid("transcript_id"),
    transcriptRevision: integer("transcript_revision"),
  },
  (table) => [
    check(
      "harness_artifact_fingerprint_length_check",
      sql`length(${table.fingerprint}) = 64`
    ),
    check(
      "harness_artifact_sha256_length_check",
      sql`length(${table.sha256}) = 64`
    ),
    check(
      "harness_artifact_size_nonnegative_check",
      sql`${table.sizeBytes} >= 0`
    ),
    check(
      "harness_artifact_transcript_pair_check",
      sql`(${table.transcriptId} IS NULL) = (${table.transcriptRevision} IS NULL)`
    ),
    check(
      "harness_artifact_evidence_transcript_check",
      sql`${table.kind} <> 'evidence' OR ${table.transcriptId} IS NOT NULL`
    ),
    check(
      "harness_artifact_transcript_revision_positive_check",
      sql`${table.transcriptRevision} IS NULL OR ${table.transcriptRevision} > 0`
    ),
    uniqueIndex("harness_artifact_fingerprint_idx").on(
      table.organizationId,
      table.sourceId,
      table.kind,
      table.fingerprint
    ),
    unique("harness_artifact_scoped_id_idx").on(
      table.organizationId,
      table.sourceId,
      table.id
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "harness_artifact_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.transcriptId],
      foreignColumns: [
        transcript.organizationId,
        transcript.sourceId,
        transcript.id,
      ],
      name: "harness_artifact_scoped_transcript_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [
        table.organizationId,
        table.transcriptId,
        table.transcriptRevision,
      ],
      foreignColumns: [
        transcriptRevision.organizationId,
        transcriptRevision.transcriptId,
        transcriptRevision.revision,
      ],
      name: "harness_artifact_scoped_transcript_revision_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_artifact", table.organizationId),
  ]
).enableRLS();

export const harnessArtifactDependency = pgTable(
  "harness_artifact_dependency",
  {
    artifactId: uuid("artifact_id").notNull(),
    createdAt: createdAt(),
    inputArtifactId: uuid("input_artifact_id").notNull(),
    organizationId: organizationId(),
    sourceId: uuid("source_id").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.organizationId, table.artifactId, table.inputArtifactId],
      name: "harness_artifact_dependency_pk",
    }),
    check(
      "harness_artifact_dependency_not_self_check",
      sql`${table.artifactId} <> ${table.inputArtifactId}`
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.artifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "harness_artifact_dependency_consumer_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.inputArtifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "harness_artifact_dependency_input_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_artifact_dependency", table.organizationId),
  ]
).enableRLS();

export const harnessRunLane = pgEnum("harness_run_lane", [
  "chapters",
  "transcription",
]);

export const harnessRunStatus = pgEnum("harness_run_status", [
  "pending",
  "running",
  "needs_review",
  "ready",
  "budget_paused",
  "outcome_unknown",
  "failed",
  "cancelled",
]);

export const harnessRun = pgTable(
  "harness_run",
  {
    acceptedRevision: integer("accepted_revision"),
    brief: text("brief").notNull().default(""),
    budgetMicros: bigint("budget_micros", { mode: "number" }).notNull(),
    config: jsonObject("config"),
    createdAt: createdAt(),
    currentRevision: integer("current_revision").notNull().default(0),
    dispatchCount: integer("dispatch_count").notNull().default(0),
    errorMessage: text("error_message"),
    evidenceArtifactId: uuid("evidence_artifact_id"),
    id: id(),
    lane: harnessRunLane("lane").notNull(),
    organizationId: organizationId(),
    repairCount: integer("repair_count").notNull().default(0),
    requestKey: text("request_key").notNull(),
    reservedMicros: bigint("reserved_micros", { mode: "number" })
      .notNull()
      .default(0),
    routeSnapshot: jsonObject("route_snapshot"),
    sourceId: uuid("source_id").notNull(),
    spentMicros: bigint("spent_micros", { mode: "number" })
      .notNull()
      .default(0),
    stage: text("stage"),
    status: harnessRunStatus("status").notNull().default("pending"),
    updatedAt: updatedAt(),
    workflowId: text("workflow_id"),
    workflowRunId: text("workflow_run_id"),
  },
  (table) => [
    check(
      "harness_run_budget_nonnegative_check",
      sql`${table.budgetMicros} >= 0`
    ),
    check(
      "harness_run_spent_nonnegative_check",
      sql`${table.spentMicros} >= 0`
    ),
    check(
      "harness_run_reserved_nonnegative_check",
      sql`${table.reservedMicros} >= 0`
    ),
    check(
      "harness_run_current_revision_nonnegative_check",
      sql`${table.currentRevision} >= 0`
    ),
    check(
      "harness_run_accepted_revision_positive_check",
      sql`${table.acceptedRevision} IS NULL OR ${table.acceptedRevision} > 0`
    ),
    check(
      "harness_run_dispatch_count_nonnegative_check",
      sql`${table.dispatchCount} >= 0`
    ),
    check(
      "harness_run_repair_count_nonnegative_check",
      sql`${table.repairCount} >= 0`
    ),
    uniqueIndex("harness_run_request_key_idx").on(
      table.organizationId,
      table.sourceId,
      table.requestKey
    ),
    unique("harness_run_scoped_id_idx").on(
      table.organizationId,
      table.sourceId,
      table.id
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "harness_run_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.evidenceArtifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "harness_run_scoped_evidence_artifact_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_run", table.organizationId),
  ]
).enableRLS();

export const harnessOperationKind = pgEnum("harness_operation_kind", [
  "recognize",
  "align",
  "diarize",
  "speech_coverage",
  "evidence",
  "model",
  "compile",
  "render",
  "check",
  "export",
]);

export const harnessOperationStatus = pgEnum("harness_operation_status", [
  "pending",
  "running",
  "succeeded",
  "failed",
  "outcome_unknown",
  "cancelled",
]);

export const harnessOperation = pgTable(
  "harness_operation",
  {
    configHash: text("config_hash").notNull(),
    createdAt: createdAt(),
    id: id(),
    inputHash: text("input_hash").notNull(),
    kind: harnessOperationKind("kind").notNull(),
    organizationId: organizationId(),
    resultArtifactId: uuid("result_artifact_id"),
    runId: uuid("run_id"),
    semanticKey: text("semantic_key").notNull(),
    sourceId: uuid("source_id").notNull(),
    stage: text("stage").notNull(),
    status: harnessOperationStatus("status").notNull().default("pending"),
    updatedAt: updatedAt(),
  },
  (table) => [
    check(
      "harness_operation_semantic_key_length_check",
      sql`length(${table.semanticKey}) = 64`
    ),
    check(
      "harness_operation_input_hash_length_check",
      sql`length(${table.inputHash}) = 64`
    ),
    check(
      "harness_operation_config_hash_length_check",
      sql`length(${table.configHash}) = 64`
    ),
    uniqueIndex("harness_operation_semantic_key_idx").on(
      table.organizationId,
      table.sourceId,
      table.semanticKey
    ),
    unique("harness_operation_scoped_id_idx").on(
      table.organizationId,
      table.sourceId,
      table.id
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "harness_operation_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.runId],
      foreignColumns: [
        harnessRun.organizationId,
        harnessRun.sourceId,
        harnessRun.id,
      ],
      name: "harness_operation_scoped_run_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.resultArtifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "harness_operation_scoped_result_artifact_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_operation", table.organizationId),
  ]
).enableRLS();

export const harnessAttemptState = pgEnum("harness_attempt_state", [
  "reserved",
  "dispatching",
  "running",
  "succeeded",
  "failed_known",
  "outcome_unknown",
  "cancel_requested",
  "cancelled_confirmed",
]);

export const harnessCostStatus = pgEnum("harness_cost_status", [
  "estimated",
  "reported",
  "reconciled",
  "unknown",
]);

export const harnessAttempt = pgTable(
  "harness_attempt",
  {
    actualCostMicros: bigint("actual_cost_micros", { mode: "number" }),
    attemptNumber: integer("attempt_number").notNull(),
    costStatus: harnessCostStatus("cost_status").notNull(),
    createdAt: createdAt(),
    dispatchedAt: timestamptz("dispatched_at"),
    errorCode: text("error_code"),
    errorMessage: text("error_message"),
    estimatedCostMicros: bigint("estimated_cost_micros", {
      mode: "number",
    }).notNull(),
    family: text("family"),
    finishedAt: timestamptz("finished_at"),
    heartbeatAt: timestamptz("heartbeat_at"),
    id: id(),
    model: text("model"),
    operationId: uuid("operation_id").notNull(),
    organizationId: organizationId(),
    ownerToken: text("owner_token").notNull(),
    provider: text("provider").notNull(),
    remoteHandle: text("remote_handle"),
    requestHash: text("request_hash").notNull(),
    resultArtifactId: uuid("result_artifact_id"),
    route: jsonObject("route"),
    runId: uuid("run_id"),
    sourceId: uuid("source_id").notNull(),
    state: harnessAttemptState("state").notNull().default("reserved"),
    usage: jsonObject("usage"),
  },
  (table) => [
    check(
      "harness_attempt_number_positive_check",
      sql`${table.attemptNumber} > 0`
    ),
    check(
      "harness_attempt_request_hash_length_check",
      sql`length(${table.requestHash}) = 64`
    ),
    check(
      "harness_attempt_estimated_cost_nonnegative_check",
      sql`${table.estimatedCostMicros} >= 0`
    ),
    check(
      "harness_attempt_actual_cost_nonnegative_check",
      sql`${table.actualCostMicros} IS NULL OR ${table.actualCostMicros} >= 0`
    ),
    uniqueIndex("harness_attempt_number_idx").on(
      table.organizationId,
      table.operationId,
      table.attemptNumber
    ),
    unique("harness_attempt_scoped_id_idx").on(
      table.organizationId,
      table.sourceId,
      table.id
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "harness_attempt_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.operationId],
      foreignColumns: [
        harnessOperation.organizationId,
        harnessOperation.sourceId,
        harnessOperation.id,
      ],
      name: "harness_attempt_scoped_operation_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.runId],
      foreignColumns: [
        harnessRun.organizationId,
        harnessRun.sourceId,
        harnessRun.id,
      ],
      name: "harness_attempt_scoped_run_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.resultArtifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "harness_attempt_scoped_result_artifact_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_attempt", table.organizationId),
  ]
).enableRLS();

export const harnessReservationState = pgEnum("harness_reservation_state", [
  "active",
  "settled",
  "released",
]);

export const harnessReservation = pgTable(
  "harness_reservation",
  {
    amountMicros: bigint("amount_micros", { mode: "number" }).notNull(),
    attemptId: uuid("attempt_id").notNull(),
    createdAt: createdAt(),
    id: id(),
    organizationId: organizationId(),
    runId: uuid("run_id").notNull(),
    settledAt: timestamptz("settled_at"),
    settledMicros: bigint("settled_micros", { mode: "number" }),
    sourceId: uuid("source_id").notNull(),
    state: harnessReservationState("state").notNull().default("active"),
  },
  (table) => [
    check(
      "harness_reservation_amount_nonnegative_check",
      sql`${table.amountMicros} >= 0`
    ),
    check(
      "harness_reservation_settled_nonnegative_check",
      sql`${table.settledMicros} IS NULL OR ${table.settledMicros} >= 0`
    ),
    uniqueIndex("harness_reservation_attempt_idx").on(table.attemptId),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "harness_reservation_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.runId],
      foreignColumns: [
        harnessRun.organizationId,
        harnessRun.sourceId,
        harnessRun.id,
      ],
      name: "harness_reservation_scoped_run_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.attemptId],
      foreignColumns: [
        harnessAttempt.organizationId,
        harnessAttempt.sourceId,
        harnessAttempt.id,
      ],
      name: "harness_reservation_scoped_attempt_fk",
    }).onDelete("restrict"),
    organizationPolicy("harness_reservation", table.organizationId),
  ]
).enableRLS();

export const chapterRevision = pgTable(
  "chapter_revision",
  {
    artifactId: uuid("artifact_id").notNull(),
    baseRevision: integer("base_revision"),
    createdAt: createdAt(),
    createdBy: uuid("created_by").references(() => user.id, {
      onDelete: "set null",
    }),
    id: id(),
    mutationKey: text("mutation_key").notNull(),
    organizationId: organizationId(),
    revision: integer("revision").notNull(),
    runId: uuid("run_id").notNull(),
    sourceId: uuid("source_id").notNull(),
  },
  (table) => [
    check(
      "chapter_revision_revision_positive_check",
      sql`${table.revision} > 0`
    ),
    check(
      "chapter_revision_base_revision_positive_check",
      sql`${table.baseRevision} IS NULL OR ${table.baseRevision} > 0`
    ),
    uniqueIndex("chapter_revision_number_idx").on(
      table.organizationId,
      table.runId,
      table.revision
    ),
    uniqueIndex("chapter_revision_mutation_idx").on(
      table.organizationId,
      table.runId,
      table.mutationKey
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "chapter_revision_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.runId],
      foreignColumns: [
        harnessRun.organizationId,
        harnessRun.sourceId,
        harnessRun.id,
      ],
      name: "chapter_revision_scoped_run_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.artifactId],
      foreignColumns: [
        harnessArtifact.organizationId,
        harnessArtifact.sourceId,
        harnessArtifact.id,
      ],
      name: "chapter_revision_scoped_artifact_fk",
    }).onDelete("restrict"),
    organizationPolicy("chapter_revision", table.organizationId),
  ]
).enableRLS();

export const chapterReviewAction = pgEnum("chapter_review_action", [
  "accept",
  "reject",
  "nudge",
  "restore",
  "merge",
  "undo",
  "retry",
  "cancel",
  "raise_budget",
]);

export const chapterReviewEventState = pgEnum("chapter_review_event_state", [
  "applied",
  "conflict",
  "refused",
]);

export const chapterReviewEvent = pgTable(
  "chapter_review_event",
  {
    action: chapterReviewAction("action").notNull(),
    baseRevision: integer("base_revision").notNull(),
    createdAt: createdAt(),
    createdBy: uuid("created_by").references(() => user.id, {
      onDelete: "set null",
    }),
    id: id(),
    mutationKey: text("mutation_key").notNull(),
    organizationId: organizationId(),
    payload: jsonObject("payload"),
    result: jsonObject("result"),
    resultingRevision: integer("resulting_revision"),
    runId: uuid("run_id").notNull(),
    sourceId: uuid("source_id").notNull(),
    state: chapterReviewEventState("state").notNull(),
  },
  (table) => [
    check(
      "chapter_review_event_base_revision_nonnegative_check",
      sql`${table.baseRevision} >= 0`
    ),
    check(
      "chapter_review_event_resulting_revision_positive_check",
      sql`${table.resultingRevision} IS NULL OR ${table.resultingRevision} > 0`
    ),
    uniqueIndex("chapter_review_event_mutation_idx").on(
      table.organizationId,
      table.runId,
      table.mutationKey
    ),
    foreignKey({
      columns: [table.organizationId, table.sourceId],
      foreignColumns: [source.organizationId, source.id],
      name: "chapter_review_event_scoped_source_fk",
    }).onDelete("restrict"),
    foreignKey({
      columns: [table.organizationId, table.sourceId, table.runId],
      foreignColumns: [
        harnessRun.organizationId,
        harnessRun.sourceId,
        harnessRun.id,
      ],
      name: "chapter_review_event_scoped_run_fk",
    }).onDelete("restrict"),
    organizationPolicy("chapter_review_event", table.organizationId),
  ]
).enableRLS();
