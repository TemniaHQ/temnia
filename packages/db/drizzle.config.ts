import { defineConfig } from "drizzle-kit";

// drizzle-kit needs the owner connection for studio and introspection: it
// creates tables and policies; the app role never migrates. `generate` reads
// only the schema and the snapshots, so a missing URL falls back to a
// placeholder there.
const url =
  process.env.MIGRATE_DATABASE_URL ?? "postgres://unset@localhost:1/unset";

export default defineConfig({
  dbCredentials: { url },
  dialect: "postgresql",
  out: "./drizzle",
  schema: "./src/schema/index.ts",
  strict: true,
  verbose: true,
});
