# Third-party notices

## WhisperX 3.8.6

The CPU speaker-assignment implementation in `temnia_pipeline.speech.assignment`
is adapted from WhisperX 3.8.6 `whisperx/diarize.py`.

Copyright (c) 2023, Max Bain

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

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

## Chapter-Llama ASR prompt and optional model adapter

The ASR task prompt and Llama input wrapper in `chapter_llama/inference.py` are
adapted from Lucas Ventura et al.'s Chapter-Llama, commit
`d19a77efcf583f63771de052267fdeea016510e9`:
<https://github.com/lucas-ventura/chapter-llama>. The MIT license is preserved in
`LICENSES/Chapter-Llama-MIT.txt`. Please cite Ventura, Yang, Schmid and Varol,
*Chapter-Llama: Efficient Chaptering in Hour-Long Videos with LLMs*, CVPR 2025.

The optional ASR-10k adapter is supplied by the authors at
<https://huggingface.co/lucas-ventura/chapter-llama>. Its model card declares MIT;
its Llama-3.1-8B-Instruct base has the separate Llama 3.1 Community License and
Acceptable Use Policy. Setup retrieves both notices beside the base weights.
**Built with Llama.** No Llama weights are bundled into the CPU pipeline package.
