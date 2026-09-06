/**
 * Identity tables in Better Auth's column shape (core `user`, organization
 * plugin `organization` and `member`; verified against better-auth 1.7.3 on
 * 2026-09-06). The Drizzle adapter resolves columns by the Drizzle property
 * key, so the keys are Better Auth's (`emailVerified`, `createdAt`) and the
 * database columns follow this repository's snake_case. S24 adds session,
 * account, verification, and invitation; its generated schema file is
 * reconciled by hand against this one (the CLI overwrites, it does not merge).
 * Deliberate departures from the generator's output: timestamptz instead of
 * timestamp, and uuidv7() instead of gen_random_uuid() as the id default;
 * Better Auth inserts `default` for ids, so any database default works.
 * Until S24 the rows are the seeded Temnia organization and user plus the
 * probe organization the isolation suite uses.
 *
 * Scope: `organization` is the root and is scoped by its own id; `user` is
 * visible through membership (or as the acting user, which is how a user row
 * is created before its membership); `member` is organization-owned.
 */
import { sql } from "drizzle-orm";
import {
  boolean,
  index,
  pgPolicy,
  pgTable,
  text,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";
import { createdAt, id, updatedAt } from "./columns.ts";
import {
  currentOrganizationId,
  currentUserId,
  pipelineRole,
  tenantRoles,
} from "./scope.ts";

export const organization = pgTable(
  "organization",
  {
    createdAt: createdAt(),
    id: id(),
    logo: text("logo"),
    metadata: text("metadata"),
    name: text("name").notNull(),
    slug: text("slug").notNull(),
  },
  (table) => [
    uniqueIndex("organization_slug_idx").on(table.slug),
    pgPolicy("organization_is_current", {
      as: "permissive",
      for: "all",
      to: tenantRoles,
      using: sql`${table.id} = ${currentOrganizationId}`,
      withCheck: sql`${table.id} = ${currentOrganizationId}`,
    }),
    // The one declared cross-tenant read: the pipeline's reaper enumerates
    // organizations, then scopes into each. Ids only; every other table stays
    // behind its scope for this role too.
    pgPolicy("organization_enumerable_by_pipeline", {
      as: "permissive",
      for: "select",
      to: pipelineRole,
      using: sql`true`,
    }),
  ]
).enableRLS();

export const user = pgTable(
  "user",
  {
    createdAt: createdAt(),
    email: text("email").notNull(),
    emailVerified: boolean("email_verified").notNull().default(false),
    id: id(),
    image: text("image"),
    name: text("name").notNull(),
    updatedAt: updatedAt(),
  },
  (table) => [
    uniqueIndex("user_email_idx").on(table.email),
    pgPolicy("user_is_member_or_self", {
      as: "permissive",
      for: "all",
      to: tenantRoles,
      using: sql`${table.id} = ${currentUserId} OR EXISTS (
        SELECT 1 FROM "member" m
        WHERE m.user_id = ${table.id} AND m.organization_id = ${currentOrganizationId}
      )`,
      withCheck: sql`${table.id} = ${currentUserId}`,
    }),
  ]
).enableRLS();

export const member = pgTable(
  "member",
  {
    createdAt: createdAt(),
    id: id(),
    organizationId: uuid("organization_id")
      .notNull()
      .references(() => organization.id, { onDelete: "cascade" }),
    role: text("role").notNull().default("member"),
    userId: uuid("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
  },
  (table) => [
    uniqueIndex("member_organization_user_idx").on(
      table.organizationId,
      table.userId
    ),
    index("member_user_idx").on(table.userId),
    pgPolicy("member_in_organization", {
      as: "permissive",
      for: "all",
      to: tenantRoles,
      using: sql`${table.organizationId} = ${currentOrganizationId}`,
      withCheck: sql`${table.organizationId} = ${currentOrganizationId}`,
    }),
  ]
).enableRLS();
