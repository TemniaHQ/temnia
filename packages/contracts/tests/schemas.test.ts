import { describe, expect, it } from "vitest";
import {
  HelloInputSchema,
  HelloOutputSchema,
  ScopeSchema,
  SEEDED_SCOPE,
} from "../src/index.ts";

describe("contracts", () => {
  it("accepts the seeded scope", () => {
    expect(ScopeSchema.parse(SEEDED_SCOPE)).toEqual(SEEDED_SCOPE);
  });

  it("rejects an organization id that is not a uuid", () => {
    expect(
      ScopeSchema.safeParse({ ...SEEDED_SCOPE, organizationId: "temnia" })
        .success
    ).toBe(false);
  });

  it("requires the hello output to name the python worker", () => {
    const result = HelloOutputSchema.safeParse({
      greeting: "hi",
      organizationId: SEEDED_SCOPE.organizationId,
      workerHost: "x",
      workerLanguage: "typescript",
    });
    expect(result.success).toBe(false);
  });

  it("bounds the greeting name", () => {
    expect(
      HelloInputSchema.safeParse({ name: "", scope: SEEDED_SCOPE }).success
    ).toBe(false);
  });
});
