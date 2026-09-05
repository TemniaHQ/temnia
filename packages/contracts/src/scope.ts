import { z } from "zod";

/** Where a request acts. Every organization-owned row is written under one of these. */
export const ScopeSchema = z
  .object({
    organizationId: z
      .uuid()
      .describe("The organization every row is scoped to."),
    userId: z.uuid().describe("The acting user inside that organization."),
  })
  .meta({ id: "Scope", title: "Scope" });

export type Scope = z.infer<typeof ScopeSchema>;

/**
 * The organization and user seeded at S1 and returned by the scope resolver until
 * identity lands at S24. Fixed ids so fixtures, seeds, and probes agree across both
 * languages.
 */
export const SEEDED_SCOPE: Scope = {
  organizationId: "0192e8a0-0000-7000-8000-000000000001",
  userId: "0192e8a0-0000-7000-8000-000000000002",
};

/**
 * A second seeded organization that owns nothing the first may see. The isolation
 * suite probes every table against this pair.
 */
export const PROBE_ORGANIZATION_ID = "0192e8a0-0000-7000-8000-000000000003";
