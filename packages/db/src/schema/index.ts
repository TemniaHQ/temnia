// biome-ignore-all lint/performance/noBarrelFile: the schema is one module, for drizzle and for drizzle-kit
/**
 * Every table Temnia owns is declared here and nowhere else.
 *
 * Rules (AGENTS.md, tenancy):
 * - a tenant table carries `organization_id`, is created with `pgPolicy` for the
 *   org-isolation predicate, and has forced RLS in the same migration
 *   (`scripts/generate.ts` appends the FORCE and the role grants drizzle-kit
 *   does not emit);
 * - the isolation suite (`tests/isolation.test.ts`) asserts those facts against the
 *   migrated catalogue and probes each table with two seeded organizations;
 * - Python never declares DDL; it derives models from the migrated database.
 */
export * from "./columns.ts";
export * from "./identity.ts";
export * from "./media.ts";
export * from "./scope.ts";
export * from "./tenant.ts";
