/**
 * Applies pending migrations, then the seed. Idempotent and safe to run from
 * several processes at once: a session-level advisory lock serialises runners.
 * Deployed images call this at start (the web app's instrumentation hook), so
 * a broken migration aborts the container loudly instead of serving a stale
 * schema.
 */
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import pg from "pg";
// biome-ignore lint/performance/noNamespaceImport: drizzle takes the whole schema as one object
import * as schema from "./schema/index.ts";
import { seed } from "./seed.ts";

const LOCK_KEY = 0x74_65_6d_6e; // "temn"

/**
 * Where the SQL migrations live. In the repository that is packages/db/drizzle
 * next to this file; in a standalone Next.js image the source is bundled, so
 * the folder is found from the working directory (the repository root mirror)
 * or from MIGRATIONS_DIR.
 */
export function findMigrationsFolder(): string {
  const candidates = [
    process.env.MIGRATIONS_DIR,
    resolve(process.cwd(), "packages/db/drizzle"),
    resolve(process.cwd(), "../../packages/db/drizzle"),
  ];
  try {
    candidates.push(
      join(dirname(fileURLToPath(import.meta.url)), "..", "drizzle")
    );
  } catch {
    // bundled: import.meta.url is not a file URL
  }
  for (const candidate of candidates) {
    if (candidate && existsSync(join(candidate, "meta", "_journal.json"))) {
      return candidate;
    }
  }
  throw new Error(
    `no migrations folder found; set MIGRATIONS_DIR (tried ${candidates.filter(Boolean).join(", ")})`
  );
}

export async function migrateDatabase(
  connectionString: string,
  migrationsFolder = findMigrationsFolder()
): Promise<void> {
  const client = new pg.Client({ connectionString });
  await client.connect();
  try {
    await client.query("SELECT pg_advisory_lock($1)", [LOCK_KEY]);
    const db = drizzle(client, { schema });
    await migrate(db, { migrationsFolder });
    await seed(db);
  } finally {
    await client
      .query("SELECT pg_advisory_unlock($1)", [LOCK_KEY])
      .catch(() => undefined);
    await client.end();
  }
}
