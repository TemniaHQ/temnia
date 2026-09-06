/**
 * The rows that exist before identity does: the Temnia organization and user
 * the scope resolver returns until S24, and the probe organization the
 * isolation suite reads against. Idempotent; runs after every migration so a
 * fresh database (the gate's disposable one, a rebuilt staging) is usable.
 *
 * With FORCE RLS even the owner is subject to the policies, so each
 * organization is written inside its own scoped transaction.
 */
import {
  PROBE_ORGANIZATION_ID,
  type Scope,
  SEEDED_SCOPE,
} from "@temnia/contracts";
import { type Database, withScope } from "./client.ts";
import { member, organization, user } from "./schema/index.ts";

export const PROBE_SCOPE: Scope = {
  organizationId: PROBE_ORGANIZATION_ID,
  userId: "0192e8a0-0000-7000-8000-000000000004",
};

interface SeedOrganization {
  email: string;
  name: string;
  scope: Scope;
  slug: string;
  userName: string;
}

export const SEEDS: SeedOrganization[] = [
  {
    email: "rajesh@temnia.com",
    name: "Temnia",
    scope: SEEDED_SCOPE,
    slug: "temnia",
    userName: "Rajesh Pattanaik",
  },
  {
    email: "probe@example.invalid",
    name: "Probe (isolation suite)",
    scope: PROBE_SCOPE,
    slug: "probe",
    userName: "Probe User",
  },
];

export async function seed(db: Database): Promise<void> {
  for (const entry of SEEDS) {
    // biome-ignore lint/performance/noAwaitInLoops: each organization needs its own scoped transaction
    await withScope(db, entry.scope, async (tx) => {
      await tx
        .insert(organization)
        .values({
          id: entry.scope.organizationId,
          name: entry.name,
          slug: entry.slug,
        })
        .onConflictDoNothing();
      await tx
        .insert(user)
        .values({
          email: entry.email,
          id: entry.scope.userId,
          name: entry.userName,
        })
        .onConflictDoNothing();
      await tx
        .insert(member)
        .values({
          organizationId: entry.scope.organizationId,
          role: "owner",
          userId: entry.scope.userId,
        })
        .onConflictDoNothing();
    });
  }
}
