/**
 * One-off: the legacy eval fixture `evals/fixtures/signal-boost-snippet.json`
 * as a `TranscriptV1`.
 *
 * Kept so the conversion is reproducible, not because it runs again. The legacy
 * fixture's transcript is the pre-`timing` word shape with no utterances and no
 * provider, so the adapter marks every word `aligned` (the legacy had no
 * interpolation), keeps the confidence it was given, derives the utterances
 * from runs of one speaker, and records the provider as the legacy fixture it
 * came from.
 *
 * One word of the fixture is crosstalk: the answer's first word starts 200 ms
 * before the question's last one. `TranscriptV1` promises ascending starts to
 * everything that reads a transcript, so the words are sorted by
 * `(startMs, endMs)` here exactly as `normalize.py` sorts them at the provider
 * boundary. The times are not touched; the overlap survives as a sentence that
 * tiles across the two speakers, which is what the substrate really does with
 * crosstalk and is the reason this fixture is worth keeping.
 *
 *   node --experimental-strip-types convert-signal-boost.ts \
 *     <path to mitosia-legacy>/evals/fixtures/signal-boost-snippet.json
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { FIXTURE_DIR } from "./dump-substrate.ts";

const LEGACY_COMMIT = "b642b77";

interface LegacyWord {
  confidence: number | null;
  endMs: number;
  speaker: string | null;
  startMs: number;
  text: string;
}

interface LegacyFixture {
  transcript: {
    durationMs: number;
    language: string | null;
    words: LegacyWord[];
  };
}

const [, , source] = process.argv;
if (!source) {
  throw new Error(
    "usage: convert-signal-boost.ts <mitosia-legacy>/evals/fixtures/signal-boost-snippet.json"
  );
}

const fixture = JSON.parse(readFileSync(source, "utf8")) as LegacyFixture;
const { transcript } = fixture;

const words = transcript.words
  .map((word) => ({
    confidence: word.confidence,
    endMs: word.endMs,
    speaker: word.speaker,
    startMs: word.startMs,
    text: word.text,
    timing: "aligned",
  }))
  .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);

const utterances: { endMs: number; speaker: string | null; startMs: number }[] =
  [];
for (const word of words) {
  const open = utterances.at(-1);
  if (open && open.speaker === word.speaker) {
    open.endMs = word.endMs;
  } else {
    utterances.push({
      endMs: word.endMs,
      speaker: word.speaker,
      startMs: word.startMs,
    });
  }
}

const speakers = [
  ...new Set(
    words
      .map((word) => word.speaker)
      .filter((speaker): speaker is string => speaker !== null)
  ),
];

const out = {
  durationMs: transcript.durationMs,
  language: transcript.language ?? "en",
  provider: {
    model: "signal-boost-snippet",
    name: "mitosia-legacy-fixture",
    version: LEGACY_COMMIT,
  },
  speakers,
  utterances,
  version: 1,
  words,
};

const path = join(FIXTURE_DIR, "signal-boost-snippet.transcript.json");
writeFileSync(path, `${JSON.stringify(out, null, 2)}\n`);
process.stdout.write(`wrote ${path} (${words.length} words)\n`);
