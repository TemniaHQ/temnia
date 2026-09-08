/**
 * `pnpm --filter @temnia/db db:generate [--name <name>]`
 *
 * Runs drizzle-kit generate, then completes the migration it wrote: for every
 * table the migration creates, append `FORCE ROW LEVEL SECURITY` and the DML
 * grants for the runtime roles. drizzle-kit 0.31 emits the policy and `ENABLE
 * ROW LEVEL SECURITY` from the schema but has no notion of FORCE (the owner
 * would otherwise bypass every policy) or of grants, and the tenancy rule is
 * that both land in the migration that creates the table.
 */
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const migrations = join(root, "drizzle");
const MARKER = "-- temnia: forced RLS and role grants";
const ADDED_UNIQUE_CONSTRAINT_PATTERN =
  /^ALTER TABLE "[^"]+" ADD CONSTRAINT "[^"]+" UNIQUE\([^;\n]+\);--> statement-breakpoint\n/gm;
const FIRST_FOREIGN_KEY_PATTERN =
  /^ALTER TABLE "[^"]+" ADD CONSTRAINT "[^"]+" FOREIGN KEY/m;
const FULL_DML = ["SELECT", "INSERT", "UPDATE", "DELETE"] as const;
const HARNESS_MUTABLE = new Set([
  "harness_run",
  "harness_operation",
  "harness_attempt",
  "harness_reservation",
]);
const HARNESS_IMMUTABLE = new Set([
  "harness_artifact",
  "harness_artifact_dependency",
  "chapter_revision",
  "chapter_review_event",
]);

interface Grants {
  app: readonly string[];
  pipeline: readonly string[];
}

function grantsFor(table: string): Grants {
  if (HARNESS_MUTABLE.has(table)) {
    return {
      app: ["SELECT"],
      pipeline: ["SELECT", "INSERT", "UPDATE"],
    };
  }
  if (HARNESS_IMMUTABLE.has(table)) {
    return { app: ["SELECT"], pipeline: ["SELECT", "INSERT"] };
  }
  return { app: FULL_DML, pipeline: FULL_DML };
}

function grantSql(table: string): string {
  const grants = grantsFor(table);
  return (
    `GRANT ${grants.app.join(", ")} ON "${table}" TO "temnia_app";` +
    `--> statement-breakpoint\nGRANT ${grants.pipeline.join(", ")} ON "${table}" TO "temnia_pipeline";`
  );
}

/**
 * drizzle-kit emits unique constraints added to existing tables after the new
 * tables' foreign keys. A new scoped FK cannot reference that key until the
 * constraint exists, so move only those generated UNIQUE statements ahead of
 * the first generated FK statement.
 */
function hoistAddedUniqueConstraints(sql: string): string {
  const uniqueStatements = sql.match(ADDED_UNIQUE_CONSTRAINT_PATTERN) ?? [];
  if (uniqueStatements.length === 0) {
    return sql;
  }
  const withoutUnique = sql.replace(ADDED_UNIQUE_CONSTRAINT_PATTERN, "");
  const firstForeignKey = withoutUnique.search(FIRST_FOREIGN_KEY_PATTERN);
  if (firstForeignKey === -1) {
    return sql;
  }
  return `${withoutUnique.slice(0, firstForeignKey)}${uniqueStatements.join("")}${withoutUnique.slice(firstForeignKey)}`;
}

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
  const sql = hoistAddedUniqueConstraints(readFileSync(path, "utf8"));
  if (sql.includes(MARKER)) {
    continue;
  }
  const tables = [...sql.matchAll(/CREATE TABLE "([^"]+)"/g)].flatMap(
    (match) => (match[1] ? [match[1]] : [])
  );
  if (tables.length === 0) {
    continue;
  }
  const tail = tables
    .map(
      (t) =>
        `ALTER TABLE "${t}" FORCE ROW LEVEL SECURITY;--> statement-breakpoint\n` +
        grantSql(t)
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
