# Third-party notices

## NLTK segmentation metrics

`src/temnia_pipeline/evals/nltk_metrics.py` adapts the `pk`, `windowdiff`, and
`ghd` algorithms from `nltk/metrics/segmentation.py` in NLTK 3.10.3.

- Copyright (C) 2001-2026 NLTK Project.
- Authors named by the source: Edward Loper, Steven Bird, and David Doukhan.
- Source file SHA-256: `6455fea9e1880aee0951c3c76927d0307d2ea48f110ccaaee2a7e81382d5d18e`.
- License: Apache License 2.0, reproduced in `LICENSES/NLTK-3.10.3.txt`.

Temnia changed the adaptation to validate a strict binary-string surface, use
explicit bounded windows, maintain rolling boundary counts, and retain only two
rows of the GHD dynamic program. It does not include NLTK package initialization,
model/data loading, downloaders, classifiers, or other NLTK APIs.
