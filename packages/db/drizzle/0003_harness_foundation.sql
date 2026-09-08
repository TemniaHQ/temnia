CREATE TYPE "public"."chapter_review_action" AS ENUM('accept', 'reject', 'nudge', 'restore', 'merge', 'undo', 'retry', 'cancel', 'raise_budget');--> statement-breakpoint
CREATE TYPE "public"."chapter_review_event_state" AS ENUM('applied', 'conflict', 'refused');--> statement-breakpoint
CREATE TYPE "public"."harness_artifact_kind" AS ENUM('evidence', 'proposal', 'model_response', 'edit', 'render', 'checks', 'export', 'speech_checkpoint');--> statement-breakpoint
CREATE TYPE "public"."harness_attempt_state" AS ENUM('reserved', 'dispatching', 'running', 'succeeded', 'failed_known', 'outcome_unknown', 'cancel_requested', 'cancelled_confirmed');--> statement-breakpoint
CREATE TYPE "public"."harness_cost_status" AS ENUM('estimated', 'reported', 'reconciled', 'unknown');--> statement-breakpoint
CREATE TYPE "public"."harness_operation_kind" AS ENUM('recognize', 'align', 'diarize', 'speech_coverage', 'evidence', 'model', 'compile', 'render', 'check', 'export');--> statement-breakpoint
CREATE TYPE "public"."harness_operation_status" AS ENUM('pending', 'running', 'succeeded', 'failed', 'outcome_unknown', 'cancelled');--> statement-breakpoint
CREATE TYPE "public"."harness_reservation_state" AS ENUM('active', 'settled', 'released');--> statement-breakpoint
CREATE TYPE "public"."harness_run_lane" AS ENUM('chapters', 'transcription');--> statement-breakpoint
CREATE TYPE "public"."harness_run_status" AS ENUM('pending', 'running', 'needs_review', 'ready', 'budget_paused', 'outcome_unknown', 'failed', 'cancelled');--> statement-breakpoint
ALTER TYPE "public"."usage_kind" ADD VALUE 'provider_cost_micros';--> statement-breakpoint
CREATE TABLE "chapter_review_event" (
	"action" "chapter_review_action" NOT NULL,
	"base_revision" integer NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_by" uuid,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"mutation_key" text NOT NULL,
	"organization_id" uuid NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"result" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"resulting_revision" integer,
	"run_id" uuid NOT NULL,
	"source_id" uuid NOT NULL,
	"state" "chapter_review_event_state" NOT NULL,
	CONSTRAINT "chapter_review_event_base_revision_nonnegative_check" CHECK ("chapter_review_event"."base_revision" >= 0),
	CONSTRAINT "chapter_review_event_resulting_revision_positive_check" CHECK ("chapter_review_event"."resulting_revision" IS NULL OR "chapter_review_event"."resulting_revision" > 0)
);
--> statement-breakpoint
ALTER TABLE "chapter_review_event" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "chapter_revision" (
	"artifact_id" uuid NOT NULL,
	"base_revision" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_by" uuid,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"mutation_key" text NOT NULL,
	"organization_id" uuid NOT NULL,
	"revision" integer NOT NULL,
	"run_id" uuid NOT NULL,
	"source_id" uuid NOT NULL,
	CONSTRAINT "chapter_revision_revision_positive_check" CHECK ("chapter_revision"."revision" > 0),
	CONSTRAINT "chapter_revision_base_revision_positive_check" CHECK ("chapter_revision"."base_revision" IS NULL OR "chapter_revision"."base_revision" > 0)
);
--> statement-breakpoint
ALTER TABLE "chapter_revision" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_artifact" (
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"fingerprint" text NOT NULL,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"kind" "harness_artifact_kind" NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"organization_id" uuid NOT NULL,
	"sha256" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"source_id" uuid NOT NULL,
	"storage_key" text NOT NULL,
	"transcript_id" uuid,
	"transcript_revision" integer,
	CONSTRAINT "harness_artifact_scoped_id_idx" UNIQUE("organization_id","source_id","id"),
	CONSTRAINT "harness_artifact_fingerprint_length_check" CHECK (length("harness_artifact"."fingerprint") = 64),
	CONSTRAINT "harness_artifact_sha256_length_check" CHECK (length("harness_artifact"."sha256") = 64),
	CONSTRAINT "harness_artifact_size_nonnegative_check" CHECK ("harness_artifact"."size_bytes" >= 0),
	CONSTRAINT "harness_artifact_transcript_pair_check" CHECK (("harness_artifact"."transcript_id" IS NULL) = ("harness_artifact"."transcript_revision" IS NULL)),
	CONSTRAINT "harness_artifact_evidence_transcript_check" CHECK ("harness_artifact"."kind" <> 'evidence' OR "harness_artifact"."transcript_id" IS NOT NULL),
	CONSTRAINT "harness_artifact_transcript_revision_positive_check" CHECK ("harness_artifact"."transcript_revision" IS NULL OR "harness_artifact"."transcript_revision" > 0)
);
--> statement-breakpoint
ALTER TABLE "harness_artifact" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_artifact_dependency" (
	"artifact_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"input_artifact_id" uuid NOT NULL,
	"organization_id" uuid NOT NULL,
	"source_id" uuid NOT NULL,
	CONSTRAINT "harness_artifact_dependency_pk" PRIMARY KEY("organization_id","artifact_id","input_artifact_id"),
	CONSTRAINT "harness_artifact_dependency_not_self_check" CHECK ("harness_artifact_dependency"."artifact_id" <> "harness_artifact_dependency"."input_artifact_id")
);
--> statement-breakpoint
ALTER TABLE "harness_artifact_dependency" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_attempt" (
	"actual_cost_micros" bigint,
	"attempt_number" integer NOT NULL,
	"cost_status" "harness_cost_status" NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"dispatched_at" timestamp with time zone,
	"error_code" text,
	"error_message" text,
	"estimated_cost_micros" bigint NOT NULL,
	"family" text,
	"finished_at" timestamp with time zone,
	"heartbeat_at" timestamp with time zone,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"model" text,
	"operation_id" uuid NOT NULL,
	"organization_id" uuid NOT NULL,
	"owner_token" text NOT NULL,
	"provider" text NOT NULL,
	"remote_handle" text,
	"request_hash" text NOT NULL,
	"result_artifact_id" uuid,
	"route" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"run_id" uuid,
	"source_id" uuid NOT NULL,
	"state" "harness_attempt_state" DEFAULT 'reserved' NOT NULL,
	"usage" jsonb DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT "harness_attempt_scoped_id_idx" UNIQUE("organization_id","source_id","id"),
	CONSTRAINT "harness_attempt_number_positive_check" CHECK ("harness_attempt"."attempt_number" > 0),
	CONSTRAINT "harness_attempt_request_hash_length_check" CHECK (length("harness_attempt"."request_hash") = 64),
	CONSTRAINT "harness_attempt_estimated_cost_nonnegative_check" CHECK ("harness_attempt"."estimated_cost_micros" >= 0),
	CONSTRAINT "harness_attempt_actual_cost_nonnegative_check" CHECK ("harness_attempt"."actual_cost_micros" IS NULL OR "harness_attempt"."actual_cost_micros" >= 0)
);
--> statement-breakpoint
ALTER TABLE "harness_attempt" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_operation" (
	"config_hash" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"input_hash" text NOT NULL,
	"kind" "harness_operation_kind" NOT NULL,
	"organization_id" uuid NOT NULL,
	"result_artifact_id" uuid,
	"run_id" uuid,
	"semantic_key" text NOT NULL,
	"source_id" uuid NOT NULL,
	"stage" text NOT NULL,
	"status" "harness_operation_status" DEFAULT 'pending' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "harness_operation_scoped_id_idx" UNIQUE("organization_id","source_id","id"),
	CONSTRAINT "harness_operation_semantic_key_length_check" CHECK (length("harness_operation"."semantic_key") = 64),
	CONSTRAINT "harness_operation_input_hash_length_check" CHECK (length("harness_operation"."input_hash") = 64),
	CONSTRAINT "harness_operation_config_hash_length_check" CHECK (length("harness_operation"."config_hash") = 64)
);
--> statement-breakpoint
ALTER TABLE "harness_operation" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_reservation" (
	"amount_micros" bigint NOT NULL,
	"attempt_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"run_id" uuid NOT NULL,
	"settled_at" timestamp with time zone,
	"settled_micros" bigint,
	"source_id" uuid NOT NULL,
	"state" "harness_reservation_state" DEFAULT 'active' NOT NULL,
	CONSTRAINT "harness_reservation_amount_nonnegative_check" CHECK ("harness_reservation"."amount_micros" >= 0),
	CONSTRAINT "harness_reservation_settled_nonnegative_check" CHECK ("harness_reservation"."settled_micros" IS NULL OR "harness_reservation"."settled_micros" >= 0)
);
--> statement-breakpoint
ALTER TABLE "harness_reservation" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "harness_run" (
	"accepted_revision" integer,
	"brief" text DEFAULT '' NOT NULL,
	"budget_micros" bigint NOT NULL,
	"config" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"current_revision" integer DEFAULT 0 NOT NULL,
	"dispatch_count" integer DEFAULT 0 NOT NULL,
	"error_message" text,
	"evidence_artifact_id" uuid,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"lane" "harness_run_lane" NOT NULL,
	"organization_id" uuid NOT NULL,
	"repair_count" integer DEFAULT 0 NOT NULL,
	"request_key" text NOT NULL,
	"reserved_micros" bigint DEFAULT 0 NOT NULL,
	"route_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"source_id" uuid NOT NULL,
	"spent_micros" bigint DEFAULT 0 NOT NULL,
	"stage" text,
	"status" "harness_run_status" DEFAULT 'pending' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"workflow_id" text,
	"workflow_run_id" text,
	CONSTRAINT "harness_run_scoped_id_idx" UNIQUE("organization_id","source_id","id"),
	CONSTRAINT "harness_run_budget_nonnegative_check" CHECK ("harness_run"."budget_micros" >= 0),
	CONSTRAINT "harness_run_spent_nonnegative_check" CHECK ("harness_run"."spent_micros" >= 0),
	CONSTRAINT "harness_run_reserved_nonnegative_check" CHECK ("harness_run"."reserved_micros" >= 0),
	CONSTRAINT "harness_run_current_revision_nonnegative_check" CHECK ("harness_run"."current_revision" >= 0),
	CONSTRAINT "harness_run_accepted_revision_positive_check" CHECK ("harness_run"."accepted_revision" IS NULL OR "harness_run"."accepted_revision" > 0),
	CONSTRAINT "harness_run_dispatch_count_nonnegative_check" CHECK ("harness_run"."dispatch_count" >= 0),
	CONSTRAINT "harness_run_repair_count_nonnegative_check" CHECK ("harness_run"."repair_count" >= 0)
);
--> statement-breakpoint
ALTER TABLE "harness_run" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "source" ADD COLUMN "deletion_requested_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "source" ADD CONSTRAINT "source_organization_id_idx" UNIQUE("organization_id","id");--> statement-breakpoint
ALTER TABLE "transcript" ADD CONSTRAINT "transcript_organization_source_id_idx" UNIQUE("organization_id","source_id","id");--> statement-breakpoint
ALTER TABLE "transcript_revision" ADD CONSTRAINT "transcript_revision_organization_transcript_revision_idx" UNIQUE("organization_id","transcript_id","revision");--> statement-breakpoint
ALTER TABLE "chapter_review_event" ADD CONSTRAINT "chapter_review_event_created_by_user_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_review_event" ADD CONSTRAINT "chapter_review_event_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_review_event" ADD CONSTRAINT "chapter_review_event_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_review_event" ADD CONSTRAINT "chapter_review_event_scoped_run_fk" FOREIGN KEY ("organization_id","source_id","run_id") REFERENCES "public"."harness_run"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_revision" ADD CONSTRAINT "chapter_revision_created_by_user_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_revision" ADD CONSTRAINT "chapter_revision_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_revision" ADD CONSTRAINT "chapter_revision_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_revision" ADD CONSTRAINT "chapter_revision_scoped_run_fk" FOREIGN KEY ("organization_id","source_id","run_id") REFERENCES "public"."harness_run"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "chapter_revision" ADD CONSTRAINT "chapter_revision_scoped_artifact_fk" FOREIGN KEY ("organization_id","source_id","artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact" ADD CONSTRAINT "harness_artifact_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact" ADD CONSTRAINT "harness_artifact_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact" ADD CONSTRAINT "harness_artifact_scoped_transcript_fk" FOREIGN KEY ("organization_id","source_id","transcript_id") REFERENCES "public"."transcript"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact" ADD CONSTRAINT "harness_artifact_scoped_transcript_revision_fk" FOREIGN KEY ("organization_id","transcript_id","transcript_revision") REFERENCES "public"."transcript_revision"("organization_id","transcript_id","revision") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact_dependency" ADD CONSTRAINT "harness_artifact_dependency_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact_dependency" ADD CONSTRAINT "harness_artifact_dependency_consumer_fk" FOREIGN KEY ("organization_id","source_id","artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_artifact_dependency" ADD CONSTRAINT "harness_artifact_dependency_input_fk" FOREIGN KEY ("organization_id","source_id","input_artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_attempt" ADD CONSTRAINT "harness_attempt_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_attempt" ADD CONSTRAINT "harness_attempt_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_attempt" ADD CONSTRAINT "harness_attempt_scoped_operation_fk" FOREIGN KEY ("organization_id","source_id","operation_id") REFERENCES "public"."harness_operation"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_attempt" ADD CONSTRAINT "harness_attempt_scoped_run_fk" FOREIGN KEY ("organization_id","source_id","run_id") REFERENCES "public"."harness_run"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_attempt" ADD CONSTRAINT "harness_attempt_scoped_result_artifact_fk" FOREIGN KEY ("organization_id","source_id","result_artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_operation" ADD CONSTRAINT "harness_operation_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_operation" ADD CONSTRAINT "harness_operation_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_operation" ADD CONSTRAINT "harness_operation_scoped_run_fk" FOREIGN KEY ("organization_id","source_id","run_id") REFERENCES "public"."harness_run"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_operation" ADD CONSTRAINT "harness_operation_scoped_result_artifact_fk" FOREIGN KEY ("organization_id","source_id","result_artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_reservation" ADD CONSTRAINT "harness_reservation_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_reservation" ADD CONSTRAINT "harness_reservation_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_reservation" ADD CONSTRAINT "harness_reservation_scoped_run_fk" FOREIGN KEY ("organization_id","source_id","run_id") REFERENCES "public"."harness_run"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_reservation" ADD CONSTRAINT "harness_reservation_scoped_attempt_fk" FOREIGN KEY ("organization_id","source_id","attempt_id") REFERENCES "public"."harness_attempt"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_run" ADD CONSTRAINT "harness_run_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_run" ADD CONSTRAINT "harness_run_scoped_source_fk" FOREIGN KEY ("organization_id","source_id") REFERENCES "public"."source"("organization_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "harness_run" ADD CONSTRAINT "harness_run_scoped_evidence_artifact_fk" FOREIGN KEY ("organization_id","source_id","evidence_artifact_id") REFERENCES "public"."harness_artifact"("organization_id","source_id","id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "chapter_review_event_mutation_idx" ON "chapter_review_event" USING btree ("organization_id","run_id","mutation_key");--> statement-breakpoint
CREATE UNIQUE INDEX "chapter_revision_number_idx" ON "chapter_revision" USING btree ("organization_id","run_id","revision");--> statement-breakpoint
CREATE UNIQUE INDEX "chapter_revision_mutation_idx" ON "chapter_revision" USING btree ("organization_id","run_id","mutation_key");--> statement-breakpoint
CREATE UNIQUE INDEX "harness_artifact_fingerprint_idx" ON "harness_artifact" USING btree ("organization_id","source_id","kind","fingerprint");--> statement-breakpoint
CREATE UNIQUE INDEX "harness_attempt_number_idx" ON "harness_attempt" USING btree ("organization_id","operation_id","attempt_number");--> statement-breakpoint
CREATE UNIQUE INDEX "harness_operation_semantic_key_idx" ON "harness_operation" USING btree ("organization_id","source_id","semantic_key");--> statement-breakpoint
CREATE UNIQUE INDEX "harness_reservation_attempt_idx" ON "harness_reservation" USING btree ("attempt_id");--> statement-breakpoint
CREATE UNIQUE INDEX "harness_run_request_key_idx" ON "harness_run" USING btree ("organization_id","source_id","request_key");--> statement-breakpoint
CREATE POLICY "chapter_review_event_in_organization" ON "chapter_review_event" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("chapter_review_event"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("chapter_review_event"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "chapter_revision_in_organization" ON "chapter_revision" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("chapter_revision"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("chapter_revision"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_artifact_in_organization" ON "harness_artifact" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_artifact"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_artifact"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_artifact_dependency_in_organization" ON "harness_artifact_dependency" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_artifact_dependency"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_artifact_dependency"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_attempt_in_organization" ON "harness_attempt" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_attempt"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_attempt"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_operation_in_organization" ON "harness_operation" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_operation"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_operation"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_reservation_in_organization" ON "harness_reservation" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_reservation"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_reservation"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "harness_run_in_organization" ON "harness_run" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("harness_run"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("harness_run"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);
--> statement-breakpoint
-- temnia: forced RLS and role grants
ALTER TABLE "chapter_review_event" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "chapter_review_event" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT ON "chapter_review_event" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "chapter_revision" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "chapter_revision" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT ON "chapter_revision" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_artifact" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_artifact" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT ON "harness_artifact" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_artifact_dependency" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_artifact_dependency" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT ON "harness_artifact_dependency" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_attempt" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_attempt" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON "harness_attempt" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_operation" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_operation" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON "harness_operation" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_reservation" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_reservation" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON "harness_reservation" TO "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "harness_run" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT ON "harness_run" TO "temnia_app";--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON "harness_run" TO "temnia_pipeline";
