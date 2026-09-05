import { type Scope, SEEDED_SCOPE } from "@temnia/contracts";

/**
 * The one scope resolver.
 *
 * Every access path that touches organization-owned data takes its
 * organization id from here, never from input. Until identity lands (S24)
 * it returns the seeded Temnia organization and user; the seam is this one
 * function, so Better Auth replaces the body without touching a caller.
 */
export function resolveScope(): Scope {
  return SEEDED_SCOPE;
}
