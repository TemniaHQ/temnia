"""The eval runner, the deterministic scorers, and the review metrics.

Ported as behaviour from the legacy `lib/ai/evals/scorers.ts`,
`lib/intelligence/review-metrics.ts`, and the report semantics of its
`scripts/run-evals.ts`, by way of the legacy pipeline's A1 phase. The
TypeScript is frozen for reference under `tools/legacy-reference/lib/`.

Cross-language parity is enforced by the snapshots under `tests/parity/`, which
were dumped from the legacy's own mock-mode eval flow and are replayed here by
`temnia-eval verify` and by pytest. Nothing in this repository regenerates them:
the flow that produced them does not exist here yet, and the snapshots are the
record of what the TypeScript answered. A lane that produces fresh scorer inputs
arrives with the harness; the thresholds and the report format are already the
production ones.

The bar is legacy DECISIONS #13 and #14: scores bit-identical, issue strings
byte-identical. "Close" fails.
"""
