/**
 * Emits JSON Schema for every contract into ./schemas:
 *   - one file per contract, for any consumer that wants a single shape;
 *   - contracts.json, one document whose $defs hold every contract with $ref
 *     between them, which the Python pipeline turns into one pydantic module.
 * `--check` fails when the files on disk differ from what the Zod schemas
 * produce, which is how the gate catches drift.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import "../src/index.ts";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "schemas");
const check = process.argv.includes("--check");

const { schemas } = z.toJSONSchema(z.globalRegistry, {
  io: "output",
  target: "draft-2020-12",
  uri: (id) => `#/$defs/${id}`,
});
const names = Object.keys(schemas).sort();
if (names.length === 0) {
  throw new Error("no contracts carry a .meta({ id }) registration");
}

mkdirSync(outDir, { recursive: true });
let drift = 0;

function emit(file: string, document: unknown): void {
  const rendered = `${JSON.stringify(document, null, 2)}\n`;
  const path = join(outDir, file);
  if (check) {
    let current = "";
    try {
      current = readFileSync(path, "utf8");
    } catch {
      current = "";
    }
    if (current !== rendered) {
      drift += 1;
      process.stderr.write(
        `schemas/${file} is stale; run: pnpm --filter @temnia/contracts schemas\n`
      );
    }
    return;
  }
  writeFileSync(path, rendered);
  process.stdout.write(`wrote schemas/${file}\n`);
}

/**
 * Zod emits both `format: "uuid"` and a `pattern` for z.uuid(). pydantic types a
 * uuid-format string as UUID and refuses a regex constraint on it, so the format
 * alone carries the rule on the Python side.
 */
function dropUuidPattern(node: unknown): unknown {
  if (Array.isArray(node)) {
    return node.map(dropUuidPattern);
  }
  if (node && typeof node === "object") {
    const { pattern, ...rest } = node as Record<string, unknown>;
    const record: Record<string, unknown> =
      rest.format === "uuid" ? rest : { pattern, ...rest };
    for (const key of Object.keys(record)) {
      record[key] = dropUuidPattern(record[key]);
    }
    return record;
  }
  return node;
}

/**
 * Zod renders `.nullable()` as `anyOf: [X, { type: "null" }]`. datamodel-codegen
 * turns a constrained X inside that union into a named root model
 * (`Width`, `Fps`), which pyright then refuses to accept a plain int for.
 * `type: [X.type, "null"]` with X's constraints inline is the same schema
 * and generates `Annotated[int | None, Field(...)]`.
 */
function collapseNullable(node: unknown): unknown {
  if (Array.isArray(node)) {
    return node.map(collapseNullable);
  }
  if (node && typeof node === "object") {
    const record = { ...(node as Record<string, unknown>) };
    const { anyOf } = record;
    if (Array.isArray(anyOf) && anyOf.length === 2) {
      const [first, second] = anyOf as Record<string, unknown>[];
      const nullIndex = [first, second].findIndex((m) => m?.type === "null");
      const other = nullIndex === 0 ? second : first;
      if (
        nullIndex !== -1 &&
        other &&
        typeof other.type === "string" &&
        !other.anyOf
      ) {
        const { anyOf: _drop, ...rest } = record;
        return collapseNullable({
          ...rest,
          ...other,
          type: [other.type, "null"],
        });
      }
    }
    for (const key of Object.keys(record)) {
      record[key] = collapseNullable(record[key]);
    }
    return record;
  }
  return node;
}

const defs: Record<string, unknown> = {};
for (const name of names) {
  const { $schema: _omit, ...schema } = collapseNullable(
    dropUuidPattern(schemas[name])
  ) as Record<string, unknown>;
  defs[name] = schema;
}

function referenceSegments(ref: string): string[] {
  if (!ref.startsWith("#/")) {
    throw new Error(`unsupported nonlocal JSON Schema reference: ${ref}`);
  }
  const segments = decodeURIComponent(ref.slice(1))
    .split("/")
    .slice(1)
    .map((segment) => segment.replaceAll("~1", "/").replaceAll("~0", "~"));
  const [namespace, definition] = segments;
  if (namespace !== "$defs" || !definition) {
    throw new Error(`invalid JSON Schema definition reference: ${ref}`);
  }
  return segments;
}

function definitionName(ref: string): string {
  const segments = referenceSegments(ref);
  const [, definition] = segments;
  if (!definition) {
    throw new Error(`invalid JSON Schema definition reference: ${ref}`);
  }
  let value: unknown = { $defs: defs };
  for (const segment of segments) {
    if (
      !(value && typeof value === "object" && Object.hasOwn(value, segment))
    ) {
      throw new Error(`JSON Schema reference does not resolve: ${ref}`);
    }
    value = (value as Record<string, unknown>)[segment];
  }
  return definition;
}

function references(node: unknown): Set<string> {
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
        found.add(definitionName(child));
      } else {
        visit(child);
      }
    }
  }
  visit(node);
  return found;
}

function reachableDefinitions(root: string): Record<string, unknown> {
  const visited = new Set<string>();
  const pending = [root];
  while (pending.length > 0) {
    const name = pending.pop();
    if (name === undefined || visited.has(name)) {
      continue;
    }
    const schema = defs[name];
    if (schema === undefined) {
      throw new Error(
        `JSON Schema definition ${name} is referenced but absent`
      );
    }
    visited.add(name);
    pending.push(...references(schema));
  }
  return Object.fromEntries(
    [...visited].sort().map((name) => [name, defs[name]])
  );
}

for (const name of names) {
  emit(`${name}.json`, {
    $defs: reachableDefinitions(name),
    $id: `${name}.json`,
    $ref: `#/$defs/${name}`,
    $schema: "https://json-schema.org/draft/2020-12/schema",
  });
}
emit("contracts.json", {
  $defs: defs,
  $id: "contracts.json",
  $schema: "https://json-schema.org/draft/2020-12/schema",
});

if (drift > 0) {
  process.exit(1);
}
