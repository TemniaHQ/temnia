/**
 * The scoped database client.
 *
 * Every read or write of tenant data runs inside `withScope`, which opens a
 * transaction and sets the organization (and user) GUCs the policies read.
 * The GUCs are `SET LOCAL`, so they die with the transaction and a pooled
 * connection never carries a scope into its next borrower.
 */
import type { Scope } from "@temnia/contracts";
import { sql } from "drizzle-orm";
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import pg from "pg";
// biome-ignore lint/performance/noNamespaceImport: drizzle takes the whole schema as one object
import * as schema from "./schema/index.ts";

export type Database = NodePgDatabase<typeof schema>;
export type ScopedTransaction = Parameters<
  Parameters<Database["transaction"]>[0]
>[0];

export interface DatabaseHandle {
  close: () => Promise<void>;
  db: Database;
  pool: pg.Pool;
}

export function createDatabase(
  connectionString: string,
  max = 10
): DatabaseHandle {
  const pool = new pg.Pool({ connectionString, max });
  const db = drizzle(pool, { schema });
  return { close: () => pool.end(), db, pool };
}

/**
 * Run `fn` in a transaction scoped to `scope`. Nothing outside the scope is
 * visible or writable inside it; a query that forgets the scope sees nothing.
 */
export function withScope<T>(
  db: Database,
  scope: Scope,
  fn: (tx: ScopedTransaction) => Promise<T>
): Promise<T> {
  return db.transaction(async (tx) => {
    // set_config with is_local = true is SET LOCAL with bound parameters.
    await tx.execute(
      sql`SELECT set_config('app.organization_id', ${scope.organizationId}, true),
                 set_config('app.user_id', ${scope.userId}, true)`
    );
    return fn(tx);
  });
}
