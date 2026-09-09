import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  HelloInputSchema,
  HelloOutputSchema,
  ScopeSchema,
  SEEDED_SCOPE,
  SeededScopeSchema,
} from "../src/index.ts";

const schemasDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "schemas"
);

function referenceSegments(ref: string): string[] {
  expect(ref.startsWith("#/"), `unsupported schema reference ${ref}`).toBe(
    true
  );
  const segments = decodeURIComponent(ref.slice(1))
    .split("/")
    .slice(1)
    .map((segment) => segment.replaceAll("~1", "/").replaceAll("~0", "~"));
  expect(segments[0], `invalid schema reference ${ref}`).toBe("$defs");
  expect(segments[1], `invalid schema reference ${ref}`).toBeTruthy();
  return segments;
}

function referencedDefinition(
  ref: string,
  document: Record<string, unknown>
): string {
  const segments = referenceSegments(ref);
  const [, definition] = segments;
  if (!definition) {
    throw new Error(`invalid schema reference ${ref}`);
  }
  let value: unknown = document;
  for (const segment of segments) {
    expect(
      value && typeof value === "object" && Object.hasOwn(value, segment),
      `unresolved schema reference ${ref}`
    ).toBe(true);
    value = (value as Record<string, unknown>)[segment];
  }
  return definition;
}

function references(
  node: unknown,
  document: Record<string, unknown>
): Set<string> {
  const found = new Set<string>();
  function visit(value: unknown): void {
    if (Array.isArray(value)) {
      for (const child of value) {
        visit(child);
      }
      return;
    }
    if (!(value && typeof value === "object")) {
      return;
    }
    for (const [key, child] of Object.entries(value)) {
      if (key === "$ref" && typeof child === "string") {
        found.add(referencedDefinition(child, document));
      } else {
        visit(child);
      }
    }
  }
  visit(node);
  return found;
}

function reachable(
  root: string,
  document: { $defs: Record<string, unknown> }
): Set<string> {
  const visited = new Set<string>();
  const pending = [root];
  while (pending.length > 0) {
    const name = pending.pop();
    if (name === undefined || visited.has(name)) {
      continue;
    }
    expect(document.$defs, `missing definition ${name}`).toHaveProperty(name);
    visited.add(name);
    pending.push(...references(document.$defs[name], document));
  }
  return visited;
}

describe("contracts", () => {
  it("accepts the seeded scope", () => {
    expect(ScopeSchema.parse(SEEDED_SCOPE)).toEqual(SEEDED_SCOPE);
    expect(SeededScopeSchema.parse({})).toEqual(SEEDED_SCOPE);
    expect(
      SeededScopeSchema.safeParse({
        ...SEEDED_SCOPE,
        organizationId: "0192e8a0-0000-7000-8000-000000000003",
      }).success
    ).toBe(false);
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

  it("emits standalone schemas with every and only reachable definition", () => {
    const files = readdirSync(schemasDir)
      .filter((name) => name.endsWith(".json"))
      .sort();
    for (const file of files) {
      const document = JSON.parse(
        readFileSync(join(schemasDir, file), "utf8")
      ) as {
        $defs: Record<string, unknown>;
        $ref?: string;
      };
      const roots =
        file === "contracts.json"
          ? Object.keys(document.$defs)
          : [referencedDefinition(document.$ref ?? "", document)];
      const expected = new Set<string>();
      for (const root of roots) {
        for (const name of reachable(root, document)) {
          expected.add(name);
        }
      }
      expect(Object.keys(document.$defs).sort(), file).toEqual(
        [...expected].sort()
      );
    }
  });
});
