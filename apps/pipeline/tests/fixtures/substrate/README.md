# Substrate parity fixtures

Each source here is one `<name>.transcript.json` (a `TranscriptV1`) plus an
optional `<name>.shots.json` (the shot grid `derive.write_shots` writes to
`shots/shots.json`). Everything else is generated:

| File | What it is |
|---|---|
| `<name>.coarse.txt` | `renderCoarse(grid)`, verbatim, no trailing newline |
| `<name>.fine.txt` | `renderFine(grid, 0, last, shotTimesMs)`, verbatim, no trailing newline |
| `<name>.grid.json` | the sentences and paragraphs of `buildCutGrid` |
| `<name>.paragraphs.json` | the display paragraphs of `buildParagraphs` |

The generator is the frozen TypeScript oracle in `tools/legacy-reference/`, a
copy of the legacy substrate at commit `b642b77`. The Python port in
`src/temnia_pipeline/substrate/` is asserted against these bytes by
`tests/test_substrate_parity.py`, and `tools/legacy-reference/tests/
substrate-dump.test.ts` asserts that the bytes are what a fresh dump produces.
So the gate fails from either side: a port that drifts from the oracle, and an
oracle or fixture edited without a re-dump.

The renderings carry no trailing newline on purpose: the comparison is against
exactly what the function returns, and `empty` therefore has two zero-byte
files.

## Adding a recorded source

1. Drop `<name>.transcript.json` and `<name>.shots.json` in here, taken from a
   staging run (the transcript is `transcript/rev-1.json` under the source
   prefix; the shot grid is `shots/shots.json`).
2. `pnpm substrate:dump`
3. Commit the two inputs and the four outputs. The parity test picks the source
   up by filename; nothing is registered anywhere.

The three real recorded sources are S2 slice D's: they need a staging
transcription run and Rajesh's confirmation that the recordings may live in the
repository (S2 plan §10.4). Until then the gate runs on the four below, which is
why the chain is parameterised over the directory rather than over a list.

## The sources

| Source | What it is |
|---|---|
| `speech-40s` | The normaliser's output for `apps/web/e2e/fixtures/speech-40s.mp4`: two machine voices, seven turns, real pauses. Its `shots.json` is what the ingest actually produced for that file (a `testsrc` picture has no cuts, so the list is honestly empty). |
| `signal-boost-snippet` | The legacy eval fixture `evals/fixtures/signal-boost-snippet.json`, converted by `tools/legacy-reference/convert-signal-boost.ts`. Carries one word of crosstalk, so it covers a sentence that tiles across two speakers. |
| `synthetic-edges` | Hand-built to exercise every rule at once (below). |
| `empty` | A ready transcript with no words: empty grids, not an error. |

`synthetic-edges` covers, in order: a one-word sentence; a pause of 690 ms (just
under the 700 ms glyph) and one of 701 ms (just over); a sentence whose speaker
changes half way through, so its grid speaker is its first word's and the next
sentence does not open a turn; a 2600 ms gap that breaks a paragraph on silence
alone; a paragraph that reaches exactly 120 words and breaks on the cap; a stamp
that rolls past a minute; a turn with no speaker (`S?`); a non-numeric speaker id
(`Sguest`); a word with `startMs == endMs`; and a final sentence with no terminal
punctuation. Its `shots.json` carries a cut 300 ms before a sentence start (snaps,
`·cut`), one exactly on a start (snaps), one 501 ms away (does not), one nowhere
near a start, and one 200 ms from a start whose score is under the file's
decision threshold (dropped before the rendering ever sees it).
