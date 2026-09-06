import { defineConfig } from "drizzle-kit";

// drizzle-kit needs the owner connection: it creates tables, policies, and
// forced RLS. The app role never migrates.
const url = process.env.MIGRATE_DATABASE_URL;
if (!url) {
  throw new Error("MIGRATE_DATABASE_URL is required for drizzle-kit");
}

export default defineConfig({
  dbCredentials: { url },
  dialect: "postgresql",
  out: "./drizzle",
  schema: "./src/schema/index.ts",
  strict: true,
  verbose: true,
});
