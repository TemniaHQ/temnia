# Substrate parity fixtures

Each source here is one `<name>.transcript.json` (a `TranscriptV1`) plus an
optional `<name>.shots.json` (the shot grid `derive.write_shots` writes to
`shots/shots.json`) and an optional `<name>.gold.json` (reference boundaries).
Everything else is generated:

| File | What it is |
|---|---|
| `<name>.coarse.txt` | `renderCoarse(grid)`, verbatim, no trailing newline |
| `<name>.fine.txt` | `renderFine(grid, 0, last, shotTimesMs)`, verbatim, no trailing newline |
| `<name>.grid.json` | the sentences and paragraphs of `buildCutGrid` |
| `<name>.paragraphs.json` | the display paragraphs of `buildParagraphs` |
| `<name>.layers.coarse.txt` | `render_coarse` over the `legacy` segmenter's layers |
| `<name>.layers.fine.txt` | `render_fine` over the same, with the shot times |

The first four come from the frozen TypeScript oracle in
`tools/legacy-reference/`; the two `layers` files come from
`apps/pipeline/scripts/dump_layers.py` (`pnpm --filter @temnia/pipeline layers`,
and `--check` for the comparison, which `tests/test_substrate_render.py` also
makes). Only the `legacy` segmenter is dumped, because it is the one that needs
no model and is therefore deterministic on any machine; SaT and change-point
renderings move with their model versions and are asserted by properties.

The oracle is a copy of the legacy substrate at commit `b642b77`. The Python port in
`src/temnia_pipeline/substrate/` is asserted against these bytes by
`tests/test_substrate_parity.py`, and `tools/legacy-reference/tests/
substrate-dump.test.ts` asserts that the bytes are what a fresh dump produces.
So the gate fails from either side: a port that drifts from the oracle, and an
oracle or fixture edited without a re-dump.

The renderings carry no trailing newline on purpose: the comparison is against
exactly what the function returns, and `empty` therefore has four zero-byte
files.

## Adding a recorded source

1. Drop `<name>.transcript.json` and `<name>.shots.json` in here, taken from a
   staging run (the transcript is `transcript/rev-1.json` under the source
   prefix; the shot grid is `shots/shots.json`).
2. `pnpm substrate:dump` and `pnpm --filter @temnia/pipeline layers`
3. Commit the inputs and the six outputs. The parity test picks the source
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
| `two-topics` | Hand-written for the change-point layer: 80 sentences over about 12 minutes, 40 on coaching a rugby forward pack and 40 on compound interest, host and guest. It has a `gold.json` because the one true boundary is known by construction. |
| `empty` | A ready transcript with no words: empty grids, not an error. |

`two-topics` is written so the boundary is not free. The gap at the join is
1950 ms, under the legacy rule's 2500 ms paragraph gap, so the pause rule does
not find it; the speaker changes there, but the speaker changes at forty other
places too, so a rule that breaks on every turn scores no better at that one.
A segmenter that finds it has read the words.

## Gold

`<name>.gold.json` is `{"boundariesMs": [...], "chapters": [{"startMs",
"endMs", "title"?}]}`, both optional. `temnia-eval segment --gold` scores every
segmenter against it, and without one the runner still reports density and
inter-segmenter agreement. Three sources of gold are planned, in order of
arrival: creator chapters on the recorded episodes if they have them, a manual
annotation in the cutting room once S5 exists, and then every accepted or
nudged boundary from the cutting room. `two-topics` has gold because it was
constructed around one boundary; nothing here is a substitute for a real one.

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
