/**
 * Dumps the frozen oracle's renderings for every substrate fixture.
 *
 * For each `<name>.transcript.json` (a `TranscriptV1`) in
 * `apps/pipeline/tests/fixtures/substrate/`, with an optional
 * `<name>.shots.json` (the shot grid the ingest writes), this writes four
 * files beside it:
 *
 *   `<name>.coarse.txt`      `renderCoarse(grid)`, verbatim and with no
 *                            trailing newline, so a byte comparison is against
 *                            exactly what the function returns
 *   `<name>.fine.txt`        `renderFine(grid, 0, last, shotTimesMs)`, likewise
 *   `<name>.grid.json`       the sentences and paragraphs of `buildCutGrid`
 *   `<name>.paragraphs.json` the display paragraphs of `buildParagraphs`
 *
 * The Python port in `apps/pipeline/src/temnia_pipeline/substrate/` is asserted
 * against these bytes by `apps/pipeline/tests/test_substrate_parity.py`, and
 * `tests/substrate-dump.test.ts` asserts that they are what a fresh dump
 * produces. Adding a recorded source is dropping two files in and re-running:
 *
 *   pnpm substrate:dump
 */
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildCutGrid,
  renderCoarse,
  renderFine,
} from "./lib/intelligence/grid.ts";
import { buildParagraphs } from "./lib/transcription/paragraphs.ts";
import type {
  TranscriptData,
  TranscriptWord,
} from "./lib/transcription/types.ts";

const here = dirname(fileURLToPath(import.meta.url));

export const FIXTURE_DIR = join(
  here,
  "..",
  "..",
  "apps",
  "pipeline",
  "tests",
  "fixtures",
  "substrate"
);

/**
 * `TranscriptV1` from `@temnia/contracts`, as much of it as the oracle reads.
 * Declared here rather than imported: nothing in this directory may depend on a
 * workspace package, or the oracle stops being frozen.
 */
interface TranscriptV1 {
  durationMs: number;
  language: string;
  utterances: { endMs: number; speaker: string | null; startMs: number }[];
  words: {
    confidence: number | null;
    endMs: number;
    speaker: string | null;
    startMs: number;
    text: string;
    timing: string;
  }[];
}

/** The shot grid `derive.write_shots` writes to `shots/shots.json`. */
interface ShotGrid {
  decision_threshold: number;
  shots: { score: number; t: number }[];
}

/**
 * The shot times a boundary may snap onto: the candidates scoring at or above
 * the file's own decision threshold, in integer milliseconds. The seconds are
 * rounded the way `Math.round` does, which is what the Python port mirrors.
 */
export function shotTimesMs(grid: ShotGrid): number[] {
  return grid.shots
    .filter((shot) => shot.score >= grid.decision_threshold)
    .map((shot) => Math.round(shot.t * 1000));
}

/** `TranscriptV1` words carry a `timing` the legacy shape has no field for. */
function legacyWords(transcript: TranscriptV1): TranscriptWord[] {
  return transcript.words.map((word) => ({
    confidence: word.confidence,
    endMs: word.endMs,
    speaker: word.speaker,
    startMs: word.startMs,
    text: word.text,
  }));
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

/** Two-space indent, keys in the order written below, one trailing newline. */
function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

export function fixtureNames(fixtureDir: string): string[] {
  return readdirSync(fixtureDir)
    .filter((file) => file.endsWith(".transcript.json"))
    .map((file) => file.slice(0, -".transcript.json".length))
    .sort();
}

export function dumpFixture(
  name: string,
  fixtureDir: string,
  outDir: string
): void {
  const transcript = readJson<TranscriptV1>(
    join(fixtureDir, `${name}.transcript.json`)
  );
  const words = legacyWords(transcript);
  let shots: number[] = [];
  try {
    shots = shotTimesMs(
      readJson<ShotGrid>(join(fixtureDir, `${name}.shots.json`))
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw error;
    }
  }

  const grid = buildCutGrid(words);
  writeFileSync(join(outDir, `${name}.coarse.txt`), renderCoarse(grid));
  writeFileSync(
    join(outDir, `${name}.fine.txt`),
    renderFine(grid, 0, grid.sentences.length - 1, shots)
  );
  writeJson(join(outDir, `${name}.grid.json`), {
    paragraphs: grid.paragraphs.map((paragraph) => ({
      endMs: paragraph.endMs,
      endSentence: paragraph.endSentence,
      id: paragraph.id,
      speaker: paragraph.speaker,
      startMs: paragraph.startMs,
      startSentence: paragraph.startSentence,
    })),
    sentences: grid.sentences.map((sentence) => ({
      endMs: sentence.endMs,
      endsTurn: sentence.endsTurn,
      endWord: sentence.endWord,
      id: sentence.id,
      opensTurn: sentence.opensTurn,
      pauseAfterMs: sentence.pauseAfterMs,
      question: sentence.question,
      speaker: sentence.speaker,
      startMs: sentence.startMs,
      startWord: sentence.startWord,
      text: sentence.text,
    })),
  });

  const data: TranscriptData = {
    durationMs: transcript.durationMs,
    language: transcript.language,
    utterances: transcript.utterances,
    version: 1,
    words,
  };
  writeJson(
    join(outDir, `${name}.paragraphs.json`),
    buildParagraphs(data).map((paragraph) => ({
      endMs: paragraph.endMs,
      speaker: paragraph.speaker,
      startMs: paragraph.startMs,
      wordCount: paragraph.words.length,
      wordOffset: paragraph.wordOffset,
    }))
  );
}

export function dumpAll(fixtureDir: string, outDir: string): string[] {
  const names = fixtureNames(fixtureDir);
  for (const name of names) {
    dumpFixture(name, fixtureDir, outDir);
  }
  return names;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const names = dumpAll(FIXTURE_DIR, FIXTURE_DIR);
  if (names.length === 0) {
    throw new Error(`no *.transcript.json under ${FIXTURE_DIR}`);
  }
  process.stdout.write(`dumped ${names.length}: ${names.join(", ")}\n`);
}
