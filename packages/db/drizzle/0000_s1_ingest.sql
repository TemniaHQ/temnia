CREATE TYPE "public"."artifact_kind" AS ENUM('master', 'hls', 'peaks', 'thumbnails', 'audio', 'shots');--> statement-breakpoint
CREATE TYPE "public"."source_status" AS ENUM('uploading', 'uploaded', 'processing', 'ready', 'failed');--> statement-breakpoint
CREATE TYPE "public"."upload_status" AS ENUM('active', 'completed', 'aborted');--> statement-breakpoint
CREATE TYPE "public"."usage_kind" AS ENUM('storage_bytes', 'processing_seconds');--> statement-breakpoint
CREATE TABLE "member" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"role" text DEFAULT 'member' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "member" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "organization" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"name" text NOT NULL,
	"slug" text NOT NULL,
	"logo" text,
	"metadata" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "organization" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "user" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"email_verified" boolean DEFAULT false NOT NULL,
	"image" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "user" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "artifact" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"source_id" uuid NOT NULL,
	"kind" "artifact_kind" NOT NULL,
	"storage_key" text NOT NULL,
	"storage_prefix" text,
	"content_type" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "artifact" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "project" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"name" text NOT NULL,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "project" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "source" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"project_id" uuid NOT NULL,
	"title" text NOT NULL,
	"original_filename" text NOT NULL,
	"content_type" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"master_key" text NOT NULL,
	"status" "source_status" DEFAULT 'uploading' NOT NULL,
	"duration_ms" integer,
	"width" integer,
	"height" integer,
	"fps" numeric(8, 3),
	"audio_channels" integer,
	"video_codec" text,
	"audio_codec" text,
	"ingest_workflow_id" text,
	"ingest_stage" text,
	"ingest_percent" integer,
	"ingest_heartbeat_at" timestamp with time zone,
	"error_message" text,
	"uploaded_at" timestamp with time zone,
	"ready_at" timestamp with time zone,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "source" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "upload" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"source_id" uuid NOT NULL,
	"storage_key" text NOT NULL,
	"multipart_upload_id" text NOT NULL,
	"part_size_bytes" integer NOT NULL,
	"size_bytes" bigint NOT NULL,
	"fingerprint" text NOT NULL,
	"status" "upload_status" DEFAULT 'active' NOT NULL,
	"last_activity_at" timestamp with time zone DEFAULT now() NOT NULL,
	"completed_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "upload" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "usage_ledger" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7() NOT NULL,
	"organization_id" uuid NOT NULL,
	"kind" "usage_kind" NOT NULL,
	"quantity" bigint NOT NULL,
	"source_id" uuid,
	"workflow_id" text,
	"detail" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"recorded_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "usage_ledger" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "member" ADD CONSTRAINT "member_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "member" ADD CONSTRAINT "member_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."user"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "artifact" ADD CONSTRAINT "artifact_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "artifact" ADD CONSTRAINT "artifact_source_id_source_id_fk" FOREIGN KEY ("source_id") REFERENCES "public"."source"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "project" ADD CONSTRAINT "project_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "project" ADD CONSTRAINT "project_created_by_user_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "source" ADD CONSTRAINT "source_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "source" ADD CONSTRAINT "source_project_id_project_id_fk" FOREIGN KEY ("project_id") REFERENCES "public"."project"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "source" ADD CONSTRAINT "source_created_by_user_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."user"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "upload" ADD CONSTRAINT "upload_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "upload" ADD CONSTRAINT "upload_source_id_source_id_fk" FOREIGN KEY ("source_id") REFERENCES "public"."source"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "usage_ledger" ADD CONSTRAINT "usage_ledger_organization_id_organization_id_fk" FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "usage_ledger" ADD CONSTRAINT "usage_ledger_source_id_source_id_fk" FOREIGN KEY ("source_id") REFERENCES "public"."source"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "member_organization_user_idx" ON "member" USING btree ("organization_id","user_id");--> statement-breakpoint
CREATE INDEX "member_user_idx" ON "member" USING btree ("user_id");--> statement-breakpoint
CREATE UNIQUE INDEX "organization_slug_idx" ON "organization" USING btree ("slug");--> statement-breakpoint
CREATE UNIQUE INDEX "user_email_idx" ON "user" USING btree ("email");--> statement-breakpoint
CREATE UNIQUE INDEX "artifact_source_kind_idx" ON "artifact" USING btree ("source_id","kind");--> statement-breakpoint
CREATE INDEX "project_organization_idx" ON "project" USING btree ("organization_id","created_at");--> statement-breakpoint
CREATE INDEX "source_project_idx" ON "source" USING btree ("project_id","created_at");--> statement-breakpoint
CREATE INDEX "source_organization_status_idx" ON "source" USING btree ("organization_id","status");--> statement-breakpoint
CREATE UNIQUE INDEX "upload_active_fingerprint_idx" ON "upload" USING btree ("organization_id","fingerprint") WHERE "upload"."status" = 'active';--> statement-breakpoint
CREATE INDEX "upload_status_activity_idx" ON "upload" USING btree ("status","last_activity_at");--> statement-breakpoint
CREATE INDEX "usage_ledger_organization_kind_idx" ON "usage_ledger" USING btree ("organization_id","kind","recorded_at");--> statement-breakpoint
CREATE POLICY "member_in_organization" ON "member" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("member"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("member"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "organization_is_current" ON "organization" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("organization"."id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("organization"."id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "user_is_member_or_self" ON "user" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("user"."id" = NULLIF(current_setting('app.user_id', true), '')::uuid OR EXISTS (
        SELECT 1 FROM "member" m
        WHERE m.user_id = "user"."id" AND m.organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
      )) WITH CHECK ("user"."id" = NULLIF(current_setting('app.user_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "artifact_in_organization" ON "artifact" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("artifact"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("artifact"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "project_in_organization" ON "project" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("project"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("project"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "source_in_organization" ON "source" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("source"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("source"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "upload_in_organization" ON "upload" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("upload"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("upload"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);--> statement-breakpoint
CREATE POLICY "usage_ledger_in_organization" ON "usage_ledger" AS PERMISSIVE FOR ALL TO "temnia_app", "temnia_pipeline" USING ("usage_ledger"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK ("usage_ledger"."organization_id" = NULLIF(current_setting('app.organization_id', true), '')::uuid);
--> statement-breakpoint
-- temnia: forced RLS and role grants
ALTER TABLE "member" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "member" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "organization" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "organization" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "user" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "user" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "artifact" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "artifact" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "project" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "project" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "source" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "source" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "upload" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "upload" TO "temnia_app", "temnia_pipeline";--> statement-breakpoint
ALTER TABLE "usage_ledger" FORCE ROW LEVEL SECURITY;--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON "usage_ledger" TO "temnia_app", "temnia_pipeline";
