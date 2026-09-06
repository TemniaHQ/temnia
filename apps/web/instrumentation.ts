/**
 * The release phase. When MIGRATE_DATABASE_URL is set, the server applies
 * pending migrations and the seed before it serves a request; a failure
 * throws, the process exits non-zero, and Dokploy keeps the previous
 * container. Migrations never run at build time (AGENTS.md, conventions).
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") {
    return;
  }
  const url = process.env.MIGRATE_DATABASE_URL;
  if (!url) {
    return;
  }
  const { migrateDatabase } = await import("@temnia/db/migrate");
  await migrateDatabase(url);
  // process.stdout is not available in the edge compile of this file.
  console.info("release: migrations applied, seed rows present");
}
