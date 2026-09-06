/**
 * Helpers for organization-owned tables. Every such table declares its
 * `organization_id` with `organizationId()` and its isolation with
 * `organizationPolicy()`, so the shape the isolation suite checks is written
 * once.
 */
import { sql } from "drizzle-orm";
import { type AnyPgColumn, pgPolicy, uuid } from "drizzle-orm/pg-core";
import { organization } from "./identity.ts";
import { currentOrganizationId, tenantRoles } from "./scope.ts";

export const organizationId = () =>
  uuid("organization_id")
    .notNull()
    .references(() => organization.id, { onDelete: "cascade" });

/** Rows are visible and writable only under the organization the transaction is scoped to. */
export const organizationPolicy = (table: string, column: AnyPgColumn) =>
  pgPolicy(`${table}_in_organization`, {
    as: "permissive",
    for: "all",
    to: tenantRoles,
    using: sql`${column} = ${currentOrganizationId}`,
    withCheck: sql`${column} = ${currentOrganizationId}`,
  });
