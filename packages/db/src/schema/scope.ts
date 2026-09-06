import { sql } from "drizzle-orm";
import { pgRole } from "drizzle-orm/pg-core";

/**
 * The two database roles that read and write tenant rows. Both exist before
 * the first migration (infra/dev/postgres-init/01-roles.sql locally, the staging
 * runbook §3 on the box); `.existing()` tells drizzle-kit not to create them.
 */
export const appRole = pgRole("temnia_app").existing();
export const pipelineRole = pgRole("temnia_pipeline").existing();

/** Roles every tenant policy is granted to. */
export const tenantRoles = [appRole, pipelineRole];

/**
 * The organization a transaction acts for, set with
 * `SET LOCAL app.organization_id = '<uuid>'` by the scoped client and read by
 * every policy. `current_setting(..., true)` returns NULL when the setting was
 * never defined and '' once a previous transaction on the same pooled
 * connection defined it; NULLIF folds both to NULL, and a NULL predicate admits
 * nothing: a connection that forgot to set scope sees no rows and can write none.
 */
export const currentOrganizationId = sql`NULLIF(current_setting('app.organization_id', true), '')::uuid`;

/** The acting user, same mechanism; used only where a row is user-owned rather than organization-owned. */
export const currentUserId = sql`NULLIF(current_setting('app.user_id', true), '')::uuid`;
