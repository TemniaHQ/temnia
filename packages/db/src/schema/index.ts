/**
 * Every table Temnia owns is declared here and nowhere else.
 *
 * Rules (AGENTS.md, tenancy):
 * - a tenant table carries `organization_id`, is created with `pgPolicy` for the
 *   org-isolation predicate, and has forced RLS in the same migration;
 * - the isolation suite (`tests/isolation.test.ts`) asserts those facts against the
 *   migrated catalogue and probes each table with two seeded organizations;
 * - Python never declares DDL; it derives models from the migrated database.
 *
 * S0 ships no tables. S1 adds `organization` and `user` in Better Auth's column
 * shape, then `project` and `source`.
 */
export {};
