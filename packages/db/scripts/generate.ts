/**
 * `pnpm --filter @temnia/db db:generate [--name <name>]`
 *
 * Runs drizzle-kit generate, then completes the migration it wrote: for every
 * table the migration creates, append `FORCE ROW LEVEL SECURITY` and the DML
 * grants for the two application roles. drizzle-kit 0.31 emits the policy and
 * `ENABLE ROW LEVEL SECURITY` from the schema but has no notion of FORCE (the
 * owner would otherwise bypass every policy) or of grants, and the tenancy rule
 * is that both land in the migration that creates the table.
 */
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const migrations = join(root, "drizzle");
const ROLES = '"temnia_app", "temnia_pipeline"';
const MARKER = "-- temnia: forced RLS and role grants";

function sqlFiles(): Set<string> {
  return new Set(readdirSync(migrations).filter((f) => f.endsWith(".sql")));
}

const before = sqlFiles();
const result = spawnSync(
  "pnpm",
  ["exec", "drizzle-kit", "generate", ...process.argv.slice(2)],
  { cwd: root, stdio: "inherit" }
);
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const created = [...sqlFiles()].filter((f) => !before.has(f));
for (const file of created) {
  const path = join(migrations, file);
  const sql = readFileSync(path, "utf8");
  if (sql.includes(MARKER)) {
    continue;
  }
  const tables = [...sql.matchAll(/CREATE TABLE "([^"]+)"/g)].map((m) => m[1]);
  if (tables.length === 0) {
    continue;
  }
  const tail = tables
    .map(
      (t) =>
        `ALTER TABLE "${t}" FORCE ROW LEVEL SECURITY;--> statement-breakpoint\n` +
        `GRANT SELECT, INSERT, UPDATE, DELETE ON "${t}" TO ${ROLES};`
    )
    .join("--> statement-breakpoint\n");
  writeFileSync(
    path,
    `${sql.trimEnd()}\n--> statement-breakpoint\n${MARKER}\n${tail}\n`
  );
  process.stdout.write(
    `completed ${file}: FORCE RLS + grants for ${tables.join(", ")}\n`
  );
}
