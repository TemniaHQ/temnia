"""The cut-point substrate: the addressable timeline every clip pass selects from.

A port, function for function, of the legacy TypeScript
(`lib/intelligence/grid.ts`, the grid's half of `lib/intelligence/moments.ts`,
and `lib/transcription/paragraphs.ts`), frozen for reference under
`tools/legacy-reference/`. The renderings are what a model reads, so their
strings must be byte-identical to the oracle's: the committed dumps under
`tests/fixtures/substrate/` are the assertion, and `tests/
test_substrate_parity.py` is the gate. "Close" fails, the same bar the eval
scorers are held to (legacy DECISIONS #13 and #14).
"""
