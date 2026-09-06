/**
 * `pnpm --filter @temnia/db db:migrate`: apply pending migrations and the seed
 * to MIGRATE_DATABASE_URL (the owner connection).
 */
import { migrateDatabase } from "../src/migrate.ts";

async function main(): Promise<void> {
  const url = process.env.MIGRATE_DATABASE_URL;
  if (!url) {
    throw new Error("MIGRATE_DATABASE_URL is required");
  }
  await migrateDatabase(url);
  process.stdout.write("migrations applied, seed rows present\n");
}

main().catch((error: unknown) => {
  process.stderr.write(
    `migration failed: ${error instanceof Error ? error.message : String(error)}\n`
  );
  process.exit(1);
});
