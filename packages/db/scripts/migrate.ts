/**
 * Applies pending migrations from ./drizzle. Idempotent and safe to run from
 * several containers at once: a session-level advisory lock serialises runners.
 * Deployed images run this before serving; a failure aborts the container so a
 * broken deploy is loud instead of quietly serving a stale schema.
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import pg from "pg";
// biome-ignore lint/performance/noNamespaceImport: drizzle takes the whole schema as one object
import * as schema from "../src/schema/index.ts";
import { seed } from "../src/seed.ts";

const LOCK_KEY = 0x74_65_6d_6e; // "temn"

async function main(): Promise<void> {
  const url = process.env.MIGRATE_DATABASE_URL;
  if (!url) {
    throw new Error("MIGRATE_DATABASE_URL is required");
  }
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  try {
    await client.query("SELECT pg_advisory_lock($1)", [LOCK_KEY]);
    const migrationsFolder = join(
      dirname(fileURLToPath(import.meta.url)),
      "..",
      "drizzle"
    );
    const db = drizzle(client, { schema });
    await migrate(db, { migrationsFolder });
    process.stdout.write("migrations applied\n");
    await seed(db);
    process.stdout.write("seed rows present\n");
  } finally {
    await client
      .query("SELECT pg_advisory_unlock($1)", [LOCK_KEY])
      .catch(() => undefined);
    await client.end();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(
    `migration failed: ${error instanceof Error ? error.message : String(error)}\n`
  );
  process.exit(1);
});
