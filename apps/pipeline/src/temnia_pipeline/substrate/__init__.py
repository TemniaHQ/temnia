"""The cut-point substrate: the addressable timeline every clip pass selects from.

Three layers over the word timeline — sentences, paragraphs, and ranked
boundary candidates — with stable ids and exact millisecond times, produced by
one of several segmenters behind the :class:`~.protocol.Segmenter` seam and
printed by one renderer.

M1 failed in the legacy at 18% acceptance on chapter boundaries and the
diagnosis was addressing: the model could not name the instant it meant. So the
shapes here are built for addressing first (`model.py`), the implementations
compete on the metrics in `temnia_pipeline.evals.segmentation`, and none of
them is privileged:

- `legacy_rules.py` — the ported TypeScript rules, the baseline to beat. The
  port itself is `grid.py` and `paragraphs.py`, still byte-parity tested
  against the frozen oracle in `tools/legacy-reference/` by
  `tests/test_substrate_parity.py`, so the baseline is trustworthy.
- `sat.py` — Segment-any-Text sentences and paragraphs, which read the text
  rather than Whisper's punctuation.
- `changepoint.py` — kernel change-point detection over sentence embeddings,
  the ranked topic candidates.

The legacy renderings in `grid.py` are frozen with the port; the renderer for
these layers is `render.py`, and it is what S4's prompts quote.
"""
