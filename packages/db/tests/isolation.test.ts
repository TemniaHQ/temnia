/**
 * The isolation suite. Runs in the PR gate against the migrated test database.
 *
 * Three layers:
 * 1. Catalogue facts for every table Temnia owns: RLS enabled and forced, at
 *    least one policy, and a scope column (`organization_id`, or `id` for the
 *    organization root, or membership for `user`).
 * 2. Role facts: the app role cannot bypass RLS by construction.
 * 3. Probes, for every table in the catalogue, as the app role: a row written
 *    under organization A is invisible, unwritable, and undeletable under
 *    organization B; a row claiming A's scope cannot be inserted under B; an
 *    unscoped transaction sees nothing. A table without a probe fails the suite,
 *    so a new table cannot merge without one.
 */
import {
  PROBE_ORGANIZATION_ID,
  type Scope,
  SEEDED_SCOPE,
} from "@temnia/contracts";
import { eq, getTableName, sql } from "drizzle-orm";
import type { PgTable } from "drizzle-orm/pg-core";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createDatabase,
  type DatabaseHandle,
  withScope,
} from "../src/client.ts";
import {
  artifact,
  member,
  organization,
  project,
  source,
  transcript,
  transcriptRevision,
  upload,
  usageLedger,
  user,
} from "../src/schema/index.ts";
import { PROBE_SCOPE } from "../src/seed.ts";

const ownerUrl = process.env.TEST_DATABASE_URL;
if (!ownerUrl) {
  throw new Error(
    "TEST_DATABASE_URL is required: the isolation suite never skips silently (compose up, then export it)"
  );
}
const appRole = process.env.TEST_APP_ROLE ?? "temnia_app";
// The probes run as the app role. Dev and gate databases share the compose
// password; anything else sets TEST_APP_DATABASE_URL explicitly.
const CREDENTIALS = /\/\/[^@]+@/;
const POLICY_REJECTION = /row-level security|duplicate key/;
const appUrl =
  process.env.TEST_APP_DATABASE_URL ??
  ownerUrl.replace(CREDENTIALS, `//${appRole}:${appRole}@`);

const owner = new pg.Client({ connectionString: ownerUrl });
let app: DatabaseHandle;

const A: Scope = SEEDED_SCOPE;

/** drizzle wraps the driver error; the Postgres message is on `cause`. */
async function expectRejectedByPolicy(
  attempt: Promise<unknown>
): Promise<void> {
  let caught: unknown;
  try {
    await attempt;
  } catch (error) {
    caught = error;
  }
  expect(caught, "the write was accepted").toBeDefined();
  const root = (caught as { cause?: unknown }).cause ?? caught;
  const message = root instanceof Error ? root.message : String(root);
  expect(message).toMatch(POLICY_REJECTION);
}
const B: Scope = PROBE_SCOPE;

interface TableRow {
  has_organization_id: boolean;
  policy_count: number;
  rls_enabled: boolean;
  rls_forced: boolean;
  table_name: string;
}

/** Tables scoped by something other than an organization_id column. */
const SCOPE_EXCEPTIONS: Record<string, string> = {
  organization: "the organization root is scoped by its own id",
  user: "users are scoped through membership (Better Auth's shape has no organization_id)",
};

/**
 * One probe per table: creates a row under `scope` (using rows created earlier
 * for the same scope) and returns the values to attempt as a cross-scope insert.
 */
interface Probe {
  create: (
    scope: Scope,
    ctx: Map<string, string>
  ) => Promise<Record<string, unknown>>;
  table: PgTable;
}

const probes: Probe[] = [
  {
    create: (scope) =>
      Promise.resolve({
        name: "probe",
        slug: `probe-${scope.organizationId.slice(-4)}-${Date.now()}`,
      }),
    table: organization,
  },
  {
    create: (scope) =>
      Promise.resolve({
        email: `probe-${scope.userId.slice(-4)}-${Date.now()}@example.invalid`,
        name: "probe",
      }),
    table: user,
  },
  {
    create: (scope) =>
      Promise.resolve({
        organizationId: scope.organizationId,
        role: "member",
        userId: scope.userId,
      }),
    table: member,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        name: "probe project",
        organizationId: scope.organizationId,
      };
      const [row] = await withScope(app.db, scope, (tx) =>
        tx.insert(project).values(values).returning({ id: project.id })
      );
      ctx.set("project", row?.id ?? "");
      return values;
    },
    table: project,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        contentType: "video/mp4",
        masterKey: `org/${scope.organizationId}/source/probe/master.mp4`,
        organizationId: scope.organizationId,
        originalFilename: "probe.mp4",
        projectId: ctx.get("project") ?? "",
        sizeBytes: 1,
        title: "probe source",
      };
      const [row] = await withScope(app.db, scope, (tx) =>
        tx.insert(source).values(values).returning({ id: source.id })
      );
      ctx.set("source", row?.id ?? "");
      return values;
    },
    table: source,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        fingerprint: `probe-${Date.now()}`,
        multipartUploadId: "probe",
        organizationId: scope.organizationId,
        partSizeBytes: 1,
        sizeBytes: 1,
        sourceId: ctx.get("source") ?? "",
        storageKey: `org/${scope.organizationId}/source/probe/master.mp4`,
      };
      await withScope(app.db, scope, (tx) => tx.insert(upload).values(values));
      return values;
    },
    table: upload,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        contentType: "application/json",
        kind: "shots" as const,
        organizationId: scope.organizationId,
        sizeBytes: 1,
        sourceId: ctx.get("source") ?? "",
        storageKey: `org/${scope.organizationId}/source/probe/shots.json`,
      };
      await withScope(app.db, scope, (tx) =>
        tx.insert(artifact).values(values)
      );
      return values;
    },
    table: artifact,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        kind: "storage_bytes" as const,
        organizationId: scope.organizationId,
        quantity: 1,
        sourceId: ctx.get("source") ?? "",
      };
      await withScope(app.db, scope, (tx) =>
        tx.insert(usageLedger).values(values)
      );
      return values;
    },
    table: usageLedger,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        organizationId: scope.organizationId,
        sourceId: ctx.get("source") ?? "",
      };
      const [row] = await withScope(app.db, scope, (tx) =>
        tx.insert(transcript).values(values).returning({ id: transcript.id })
      );
      ctx.set("transcript", row?.id ?? "");
      return values;
    },
    table: transcript,
  },
  {
    create: async (scope, ctx) => {
      const values = {
        kind: "machine" as const,
        organizationId: scope.organizationId,
        revision: 1,
        sizeBytes: 1,
        storageKey: `org/${scope.organizationId}/source/probe/transcript/rev-1.json`,
        transcriptId: ctx.get("transcript") ?? "",
        wordCount: 1,
      };
      await withScope(app.db, scope, (tx) =>
        tx.insert(transcriptRevision).values(values)
      );
      return values;
    },
    table: transcriptRevision,
  },
];

beforeAll(async () => {
  await owner.connect();
  app = createDatabase(appUrl, 2);
});

afterAll(async () => {
  // Owner cleanup of anything a probe left behind (cascades from project).
  await owner.query(`DELETE FROM project WHERE name = 'probe project'`);
  await owner.query(`DELETE FROM organization WHERE slug LIKE 'probe-%'`);
  await owner.query(
    `DELETE FROM "user" WHERE email LIKE 'probe-%@example.invalid'`
  );
  await owner.end();
  await app.close();
});

async function catalogue(): Promise<TableRow[]> {
  const { rows } = await owner.query<TableRow>(`
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
      AND c.relname NOT LIKE '\\_\\_drizzle%'
    ORDER BY c.relname
  `);
  return rows;
}

describe("every owned table", () => {
  it("has forced RLS, a policy, and a scope column", async () => {
    const rows = await catalogue();
    expect(rows.length).toBeGreaterThan(0);
    const offenders = rows.filter(
      (row) =>
        !(
          row.rls_enabled &&
          row.rls_forced &&
          row.policy_count > 0 &&
          (row.has_organization_id || row.table_name in SCOPE_EXCEPTIONS)
        )
    );
    expect(
      offenders,
      `tables missing tenant isolation: ${JSON.stringify(offenders)}`
    ).toEqual([]);
  });

  it("has a probe in this suite", async () => {
    const rows = await catalogue();
    const probed = new Set(probes.map((p) => getTableName(p.table)));
    const unprobed = rows
      .map((r) => r.table_name)
      .filter((t) => !probed.has(t));
    expect(unprobed, "add a probe for every new table").toEqual([]);
  });

  it("grants the app and pipeline roles DML and nothing more", async () => {
    const { rows } = await owner.query<{
      table_name: string;
      privileges: string[];
    }>(`
      SELECT table_name::text, array_agg(DISTINCT privilege_type::text ORDER BY privilege_type::text) AS privileges
      FROM information_schema.role_table_grants
      WHERE table_schema = 'public' AND grantee IN ('temnia_app', 'temnia_pipeline')
        AND table_name NOT LIKE '\\_\\_drizzle%'
      GROUP BY table_name
    `);
    const tables = (await catalogue()).map((r) => r.table_name);
    for (const table of tables) {
      const grant = rows.find((r) => r.table_name === table);
      expect(grant?.privileges, `${table} grants`).toEqual([
        "DELETE",
        "INSERT",
        "SELECT",
        "UPDATE",
      ]);
    }
  });
});

describe("the app role", () => {
  it("exists and cannot bypass RLS", async () => {
    const { rows } = await owner.query<{
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
    const { rows } = await owner.query(
      `SELECT c.relname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
       WHERE r.rolname = $1 AND c.relkind = 'r'`,
      [appRole]
    );
    expect(rows).toEqual([]);
  });
});

describe("cross-organization probes", () => {
  const ctxA = new Map<string, string>();

  it("sees the seeded rows under their own scope only", async () => {
    const mine = await withScope(app.db, A, (tx) =>
      tx.select().from(organization)
    );
    expect(mine.map((o) => o.id)).toEqual([A.organizationId]);
    const theirs = await withScope(app.db, B, (tx) =>
      tx.select().from(organization)
    );
    expect(theirs.map((o) => o.id)).toEqual([PROBE_ORGANIZATION_ID]);
    const usersA = await withScope(app.db, A, (tx) => tx.select().from(user));
    expect(usersA.map((u) => u.id)).toEqual([A.userId]);
  });

  for (const probe of probes) {
    const name = getTableName(probe.table);
    describe(name, () => {
      let crossValues: Record<string, unknown>;

      it("a row written under A is invisible under B and without scope", async () => {
        crossValues = await probe.create(A, ctxA);
        const underA = await withScope(app.db, A, (tx) =>
          tx.select().from(probe.table)
        );
        expect(underA.length).toBeGreaterThan(0);
        const underB = await withScope(app.db, B, (tx) =>
          tx.select().from(probe.table)
        );
        const idsA = new Set(underA.map((r) => (r as { id: string }).id));
        expect(
          underB.filter((r) => idsA.has((r as { id: string }).id))
        ).toEqual([]);
        const unscoped = await app.db.select().from(probe.table);
        expect(unscoped).toEqual([]);
      });

      it("cannot be updated or deleted under B", async () => {
        const table = probe.table as unknown as { id: never };
        const [row] = await withScope(app.db, A, (tx) =>
          tx.select().from(probe.table).limit(1)
        );
        const { id } = row as { id: string };
        const updated = await withScope(app.db, B, (tx) =>
          tx.update(probe.table).set({}).where(eq(table.id, id)).returning()
        ).catch(() => []);
        expect(updated).toEqual([]);
        const deleted = await withScope(app.db, B, (tx) =>
          tx.delete(probe.table).where(eq(table.id, id)).returning()
        );
        expect(deleted).toEqual([]);
        const still = await withScope(app.db, A, (tx) =>
          tx.select().from(probe.table).where(eq(table.id, id))
        );
        expect(still).toHaveLength(1);
      });

      it("rejects a row claiming A's scope written under B", async () => {
        if (name === "organization" || name === "user") {
          // The root and user tables have no organization_id; B may only write its own id.
          await expectRejectedByPolicy(
            withScope(app.db, B, (tx) =>
              tx
                .insert(probe.table)
                .values({ ...crossValues, id: A.organizationId })
            )
          );
          return;
        }
        await expectRejectedByPolicy(
          withScope(app.db, B, (tx) =>
            tx.insert(probe.table).values(crossValues)
          )
        );
      });
    });
  }

  it("counts what the probes left, per scope", async () => {
    const [countA] = await withScope(app.db, A, (tx) =>
      tx.select({ n: sql<number>`count(*)::int` }).from(project)
    );
    const [countB] = await withScope(app.db, B, (tx) =>
      tx.select({ n: sql<number>`count(*)::int` }).from(project)
    );
    expect(countA?.n).toBeGreaterThan(0);
    expect(countB?.n).toBe(0);
  });
});
