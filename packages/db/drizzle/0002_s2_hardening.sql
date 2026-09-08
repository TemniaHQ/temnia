ALTER TABLE "transcript" ADD COLUMN "run_id" text;--> statement-breakpoint
ALTER TABLE "usage_ledger" ADD COLUMN "idempotency_key" text;--> statement-breakpoint
CREATE UNIQUE INDEX "usage_ledger_idempotency_idx" ON "usage_ledger" USING btree ("idempotency_key") WHERE "usage_ledger"."idempotency_key" IS NOT NULL;