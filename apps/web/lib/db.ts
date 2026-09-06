import type { Scope } from "@temnia/contracts";
import {
  createDatabase,
  type DatabaseHandle,
  type ScopedTransaction,
  withScope,
} from "@temnia/db";
import { resolveScope } from "@/lib/scope/resolve-scope";

let handle: DatabaseHandle | undefined;

/** One pool per server process, connected as the app role. */
function database(): DatabaseHandle {
  if (!handle) {
    const url = process.env.DATABASE_URL;
    if (!url) {
      throw new Error("DATABASE_URL is required");
    }
    handle = createDatabase(url);
  }
  return handle;
}

/**
 * The only way the web app touches tenant data: a transaction scoped by the
 * resolver. Callers never pass an organization id.
 */
export function scoped<T>(
  fn: (tx: ScopedTransaction, scope: Scope) => Promise<T>
): Promise<T> {
  const scope = resolveScope();
  return withScope(database().db, scope, (tx) => fn(tx, scope));
}
