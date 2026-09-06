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

const defs: Record<string, unknown> = {};
for (const name of names) {
  const { $schema: _omit, ...schema } = dropUuidPattern(
    schemas[name]
  ) as Record<string, unknown>;
  defs[name] = schema;
  emit(`${name}.json`, {
    $defs: defs,
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
