/**
 * The isolation suite. Runs in the PR gate against the migrated test database.
 *
 * Two layers:
 * 1. Catalogue facts that must hold for every table Temnia owns: RLS enabled and
 *    forced, at least one policy, an organization_id column. Vacuously true at S0
 *    (no tables); the first table without them fails the gate.
 * 2. Role facts: the app role cannot bypass RLS by construction.
 *
 * S1 adds the per-table probes: insert as organization A, read as organization B,
 * expect nothing, for every table in the catalogue.
 */
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const url = process.env.TEST_DATABASE_URL;
if (!url) {
  throw new Error(
    "TEST_DATABASE_URL is required: the isolation suite never skips silently (compose up, then export it)"
  );
}
const appRole = process.env.TEST_APP_ROLE ?? "temnia_app";

const client = new pg.Client({ connectionString: url });

interface TableRow {
  has_organization_id: boolean;
  policy_count: number;
  rls_enabled: boolean;
  rls_forced: boolean;
  table_name: string;
}

beforeAll(async () => {
  await client.connect();
});

afterAll(async () => {
  await client.end();
});

describe("every owned table", () => {
  it("has forced RLS, a policy, and an organization_id column", async () => {
    const { rows } = await client.query<TableRow>(`
      SELECT c.relname AS table_name,
             c.relrowsecurity AS rls_enabled,
             c.relforcerowsecurity AS rls_forced,
             (SELECT count(*)::int FROM pg_policy p WHERE p.polrelid = c.oid) AS policy_count,
             EXISTS (
               SELECT 1 FROM information_schema.columns col
               WHERE col.table_schema = 'public' AND col.table_name = c.relname
                 AND col.column_name = 'organization_id'
             ) AS has_organization_id
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relkind = 'r'
      ORDER BY c.relname
    `);
    const offenders = rows.filter(
      (row) =>
        !(
          row.rls_enabled &&
          row.rls_forced &&
          row.policy_count > 0 &&
          row.has_organization_id
        )
    );
    expect(
      offenders,
      `tables missing tenant isolation: ${JSON.stringify(offenders)}`
    ).toEqual([]);
  });
});

describe("the app role", () => {
  it("exists and cannot bypass RLS", async () => {
    const { rows } = await client.query<{
      rolsuper: boolean;
      rolbypassrls: boolean;
    }>("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", [
      appRole,
    ]);
    expect(rows, `role ${appRole} must exist`).toHaveLength(1);
    expect(rows[0]?.rolsuper).toBe(false);
    expect(rows[0]?.rolbypassrls).toBe(false);
  });

  it("owns no tables", async () => {
    const { rows } = await client.query(
      `SELECT c.relname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
       WHERE r.rolname = $1 AND c.relkind = 'r'`,
      [appRole]
    );
    expect(rows).toEqual([]);
  });
});
