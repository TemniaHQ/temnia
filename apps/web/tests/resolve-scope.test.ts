import { ScopeSchema, SEEDED_SCOPE } from "@temnia/contracts";
import { describe, expect, it } from "vitest";
import { resolveScope } from "@/lib/scope/resolve-scope";

describe("resolveScope", () => {
  it("returns the seeded Temnia scope until identity lands", () => {
    expect(resolveScope()).toEqual(SEEDED_SCOPE);
  });

  it("returns a scope that validates against the shared contract", () => {
    expect(ScopeSchema.safeParse(resolveScope()).success).toBe(true);
  });
});
