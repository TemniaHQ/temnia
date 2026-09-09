# Silero VAD attribution

The direct ONNX runner and timestamp post-processing in this directory are
derived from Silero VAD v6.2.1 at commit
`7e30209a3e901f9842f81b225f3e93d8199902b1`, specifically
`src/silero_vad/utils_vad.py`. Temnia uses the repository's
`silero_vad_16k_op15.onnx` asset. Upstream:
<https://github.com/snakers4/silero-vad/tree/7e30209a3e901f9842f81b225f3e93d8199902b1>.

Silero VAD is copyright Silero Team and contributors and licensed under the
MIT License:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

# WhisperX speaker assignment attribution

`assignment.py` adapts the interval overlap, dominant-speaker, nearest-turn,
and stable tie semantics of `assign_word_speakers` from WhisperX v3.8.6,
specifically `whisperx/diarize.py`. Upstream:
<https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/diarize.py>.

WhisperX is copyright Max Bain and contributors and licensed under the
BSD 2-Clause License. The complete license is included in the pipeline's
third-party notices.
