# The frozen legacy oracle

`lib/` is a frozen copy of the cut-point substrate from
`Mitosia/mitosia-legacy` at commit `b642b774b48ad45910acb168d8dad86796da7e64`,
taken on 2026-09-07. It is **never imported by an app or a package**. It exists
for one job: to regenerate the parity fixtures under
`apps/pipeline/tests/fixtures/substrate/`, which the Python port in
`apps/pipeline/src/temnia_pipeline/substrate/` is asserted against byte for
byte.

| File | Came from | Edits |
|---|---|---|
| `lib/intelligence/grid.ts` | `lib/intelligence/grid.ts` | import paths |
| `lib/intelligence/moments.ts` | `lib/intelligence/moments.ts` | import path; trimmed to the closure `grid.ts` needs plus the word-timeline helpers the scorers read (the file's own header lists what was cut and why) |
| `lib/transcription/paragraphs.ts` | `lib/transcription/paragraphs.ts` | import path |
| `lib/transcription/types.ts` | `lib/transcription/types.ts` | none |
| `lib/ai/evals/scorers.ts` | `lib/ai/evals/scorers.ts` | import paths; two capability output types declared locally instead of imported from the Zod capability module |
| `lib/intelligence/review-metrics.ts` | `lib/intelligence/review-metrics.ts` | none |

`grounding.ts` is not here: trimming `moments.ts` removed its only importers.

The scorers and the review metrics are here for the record, beside the code
their Python port was made from. Nothing regenerates from them: the scorer
parity snapshots under `apps/pipeline/tests/parity/` were dumped by the legacy's
own `pnpm eval:parity` and are replayed by `uv run temnia-eval verify`.

## Rules

- Do not edit `lib/` to make something else pass. A behaviour question is
  settled by what this code does, not by what the port does. If the legacy
  behaviour is wrong, the fix is a decision in `AGENTS.md`, a change to the
  Python, and a re-dump with the reason in the commit.
- `lib/` is exempt from Biome (`biome.jsonc`); the formatting is the legacy's.
  Everything else in this directory is ours and is linted.
- The directory is type-checked (`pnpm --filter @temnia/legacy-reference
  typecheck`), so a drift in `tsconfig.base.json` shows up here rather than in a
  dump six months later.

## The dump

```sh
pnpm substrate:dump          # rewrite every rendering from the fixtures
```

For each `<name>.transcript.json` in `apps/pipeline/tests/fixtures/substrate/`
(a `TranscriptV1`) with an optional `<name>.shots.json` (the ingest's shot
grid), `dump-substrate.ts` writes `<name>.coarse.txt`, `<name>.fine.txt`,
`<name>.grid.json`, and `<name>.paragraphs.json`. `tests/substrate-dump.test.ts`
re-dumps to a temporary directory and asserts byte equality with the committed
files, so a fixture or an oracle edit without a re-dump fails the gate.

`convert-signal-boost.ts` is the one-off adapter that turned the legacy eval
fixture `evals/fixtures/signal-boost-snippet.json` into a `TranscriptV1`. It is
kept so the conversion is reproducible, not because it runs again.
