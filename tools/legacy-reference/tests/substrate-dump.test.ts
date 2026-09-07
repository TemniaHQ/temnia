import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, expect, it } from "vitest";
import {
  dumpAll,
  FIXTURE_DIR,
  fixtureNames,
  shotTimesMs,
} from "../dump-substrate.ts";

// Keeps the committed renderings honest against the frozen oracle they were
// dumped from: an edit to a fixture, to the oracle, or to the dump itself
// fails here until `pnpm substrate:dump` is re-run. The Python port is then
// asserted against the same bytes by
// apps/pipeline/tests/test_substrate_parity.py, so this test and that one
// together are the substrate parity gate. (The legacy's
// tests/eval-parity-snapshot.test.ts did the same job for the scorers.)

const OUT = mkdtempSync(join(tmpdir(), "substrate-dump-"));

afterAll(() => {
  rmSync(OUT, { force: true, recursive: true });
});

const OUTPUTS = [".coarse.txt", ".fine.txt", ".grid.json", ".paragraphs.json"];

it("re-dumps every fixture byte for byte", () => {
  const names = fixtureNames(FIXTURE_DIR);
  expect(names).toEqual([
    "empty",
    "signal-boost-snippet",
    "speech-40s",
    "synthetic-edges",
    "two-topics",
  ]);

  expect(dumpAll(FIXTURE_DIR, OUT)).toEqual(names);
  for (const name of names) {
    for (const suffix of OUTPUTS) {
      const file = `${name}${suffix}`;
      expect(
        readFileSync(join(OUT, file), "utf8"),
        `stale ${file} — run pnpm substrate:dump`
      ).toBe(readFileSync(join(FIXTURE_DIR, file), "utf8"));
    }
  }
});

it("leaves no committed rendering without a fixture", () => {
  const names = new Set(fixtureNames(FIXTURE_DIR));
  for (const file of readdirSync(FIXTURE_DIR)) {
    const suffix = OUTPUTS.find((candidate) => file.endsWith(candidate));
    if (!suffix) {
      continue;
    }
    const base = file.slice(0, -suffix.length);
    // `<name>.layers.coarse.txt` and `<name>.layers.fine.txt` are the Python
    // renderer's goldens, not this oracle's. They end in the same two suffixes
    // and are checked by apps/pipeline/scripts/dump_layers.py --check.
    if (base.endsWith(".layers")) {
      continue;
    }
    expect(names, `${file} has no transcript fixture`).toContain(base);
  }
});

it("counts only shots at or above the file's decision threshold", () => {
  // The rule the fine rendering's ·cut glyph depends on, and the one the
  // Python port mirrors: score >= decision_threshold, seconds rounded the way
  // Math.round rounds.
  expect(
    shotTimesMs({
      decision_threshold: 10,
      shots: [
        { score: 9.99, t: 1 },
        { score: 10, t: 2.0005 },
        { score: 31.4, t: 21.7 },
      ],
    })
  ).toEqual([2001, 21_700]);
});
