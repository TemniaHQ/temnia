CREATE TYPE "public"."transcript_revision_kind" AS ENUM('machine', 'correction');--> statement-breakpoint
CREATE TYPE "public"."transcript_status" AS ENUM('pending', 'processing', 'ready', 'failed');--> statement-breakpoint
ALTER TYPE "public"."usage_kind" ADD VALUE 'transcription_seconds';--> statement-breakpoint
CREATE TABLE "transcript" (
	"attempts" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"current_revision" integer,
	"error_message" text,
	"heartbeat_at" timestamp with time zone,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"language" text,
	"model" text,
	"organization_id" uuid NOT NULL,
	"percent" integer,
	"provider" text,
	"ready_at" timestamp with time zone,
	"source_id" uuid NOT NULL,
	"speaker_labels" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"stage" text,
	"status" "transcript_status" DEFAULT 'pending' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"workflow_id" text
);
--> statement-breakpoint
ALTER TABLE "transcript" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "transcript_revision" (
	"base_revision" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_by" uuid,
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"kind" "transcript_revision_kind" NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"organization_id" uuid NOT NULL,
	"revision" integer NOT NULL,
	"size_bytes" bigint NOT NULL,
	"storage_key" text NOT NULL,
	"transcript_id" uuid NOT NULL,
	"word_count" integer NOT NULL
);
--> statement-breakpoint
ALTER TABLE "transcript_revision" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "transcript" ADD CONSTRAINT "transcript_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript" ADD CONSTRAINT "transcript_source_id_source_id_fk" FOREIGN KEY ("source_id") REFERENCES "public"."source"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript_revision" ADD CONSTRAINT "transcript_revision_created_by_user_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript_revision" ADD CONSTRAINT "transcript_revision_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "transcript_revision" ADD CONSTRAINT "transcript_revision_transcript_id_transcript_id_fk" FOREIGN KEY ("transcript_id") REFERENCES "public"."transcript"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "transcript_source_idx" ON "transcript" USING btree ("source_id");--> statement-breakpoint
CREATE INDEX "transcript_organization_status_idx" ON "transcript" USING btree ("organization_id","status");--> statement-breakpoint
CREATE UNIQUE INDEX "transcript_revision_idx" ON "transcript_revision" USING btree ("transcript_id","revision");--> statement-breakpoint
CREATE POLICY "transcript_in_organization" ON "transcript" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("transcript"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("transcript"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "transcript_revision_in_organization" ON "transcript_revision" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("transcript_revision"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("transcript_revision"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);
--> statement-breakpoint
-- temnia: forced RLS and role grants
ALTER TABLE "transcript" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "transcript" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "transcript_revision" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "transcript_revision" TO "temnia_app", "temnia_pipeline";
