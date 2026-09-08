# Checkpointed speech qualification — 2026-09-09

PR #24, isolated Modal staging application `temnia-speech`, protocol `temnia-speech/1`.
Shared staging and its existing `temnia-media` application were not replaced. The tests use a
dedicated database, Temporal namespace and owned R2 prefixes. Results below are live observations,
separate from the recorded-provider chapter browser tests.

## Initial short run: known failure preserved

The committed smoke input is **8.824 seconds**, **61,192 bytes**, SHA-256
`715fc82696abe02c22894319daa53aeb73074edf0dd75d5700d62ffcf966f23b`.
It is distinct from the 40.116-second recorded chapter fixture.

Build `1fa3a9eeab64c3997765183b45f1d3b4e55385c756ea222679f858fe44259206`
accepted recognition and alignment, then diarization failed because the isolated container had not
explicitly imported `whisperx.diarize`. The pinned release's package initializer does not expose
that submodule; its own CLI imports `DiarizationPipeline` explicitly. The correction follows that
API and has a regression using a package root without the attribute.
[WhisperX v3.8.6 initializer](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/__init__.py),
[CLI implementation](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/transcribe.py).

| Stage | Result | Function seconds | Observed device memory peak, bytes |
| --- | --- | ---: | ---: |
| Recognition | Accepted checkpoint | 29.606 | 4,636,803,072 |
| Alignment | Accepted checkpoint | 25.929 | 851,443,712 |
| Diarization | Known failure before inference | 7.502 | 3,145,728 |

All three attempts were terminal: two succeeded and one `failed_known`. The run was `failed`,
with three dispatches and **3,875,001 micro-dollars of retained unknown-cost exposure**. No invoice
charge was available. No normalized transcript or completed recovery claim exists for this run.
The independent detector, recognition and alignment artifact bodies were copied privately and all
three accepted hashes and sizes were independently verified before cleanup. Alignment names the
exact recognition artifact as a dependency.

The telemetry reports NVIDIA L4, one-second sampling and unavailable process GPU memory/kernel
RSS high-water measurements. Sampled device peaks are not a continuous memory trace. Function
elapsed time excludes provider allocation before user code and is not billed-container lifetime.

An earlier setup attempt stopped before any GPU dispatch: the qualification helper used a directory
prefix listing to check one exact uploaded object. The helper now uses exact object HEAD and checks
its size; this correction does not change the production upload path.

## Corrected qualification budget

The failed run remains counted. The corrected short run is capped at three dispatches and
3,875,001 micros; the long run at four dispatches and 5,166,668 micros. Together with the failed
run, terminal retained exposure plus future ceilings is at most **12,916,670 micros** and ten
physical dispatches, within the original 13,000,000-micro-dollar test ceiling. These are configured
exposure limits, not a guaranteed provider invoice ceiling. Stage/startup deadlines remain 3600/120
seconds. Unknown execution halts further work. The long run has room for one known OOM downgrade.

## Corrected short run: recovery passed

Build `9c4ab9a9f0eaaa123d068f15e9f3677dd1a2a20ef56c4ef379f3f6e0e3489f36`
completed all three stages under a fresh source identity. After all checkpoints had been accepted,
the driver deliberately lost the activity result. Activity attempts **[1, 2]** observed physical
dispatch counts **[3, 3]**. Recovery reused the accepted stage artifacts and made no additional
GPU invocation. All three original attempts remained `succeeded`; the run reached `ready`.

| Stage | Function seconds | Observed device memory peak, bytes | Samples |
| --- | ---: | ---: | ---: |
| Recognition | 26.531 | 4,368,367,616 | 19 |
| Alignment | 20.267 | 851,443,712 | 14 |
| Diarization | 25.666 | 511,705,088 | 7 |

The normalized result contains **27 words**, **one speaker**, language `en`, with the first word
starting at **31 ms** and the final word ending at **8,797 ms**. Both committed expected words,
`chapters` and `smoke`, occur. Its 2,804-byte body has SHA-256
`40b599e6bbf34cf3b3eb0631784b35a87a51263b6ee2f674d80c9408e5144821`.
The normalized transcript and four accepted artifacts (three stages plus independent coverage)
were independently checked against their stored hashes and sizes. Alignment and diarization retain
the exact preceding stage dependencies.

All three charges remain unknown; three active reservations total **3,875,001 micros**. The detector
reported `clear`, which means agreement under provisional thresholds, not a human accuracy score.
The function times total **72.463 seconds**, excluding provider work outside the measured functions.
No live OOM occurred in this short run. No checkpoint from the failed build was reused.

## Long run: completed with review evidence

The corrected build processed the existing **9,060.473-second** audio (**110,024,458 bytes**,
SHA-256 `85615a2b6e04512cb333b80c4af6080a0f2017b355146b289303ed57bacc0c91`)
in **18 minutes 4.81 seconds of workflow wall time**. Recognition, alignment and diarization each
completed once; all three checkpoints were accepted and the run reached `ready`. No OOM,
batch downgrade, fourth dispatch or unknown execution occurred. This run did not inject a lost
activity result; the separate short run establishes that recovery mechanism.

| Stage | Function seconds | Observed device memory peak, bytes | Samples |
| --- | ---: | ---: | ---: |
| Recognition | 311.129 | 10,391,388,160 | 282 |
| Alignment | 230.317 | 3,146,776,576 | 200 |
| Diarization | 479.815 | 2,363,490,304 | 420 |

Measured function time totals **1,021.261 seconds**. Process RSS high-water and per-process GPU
peaks are unavailable; these are sampled device peaks, not proof of a universal memory envelope.
The three calls retain **3,875,001 micros** in active unknown-cost reservations. Across the failed
short, successful short and long runs, all **nine physical attempts** remain counted, with
**11,625,003 micros** of unresolved cost exposure. Recorded spend is zero because no charge has
been reconciled; it must not be interpreted as free compute.

The normalized transcript contains **20,588 words**, **two speakers**, language `en`, first word
start **91 ms**, final word end **9,044,040 ms**. Its **2,117,717-byte** body has SHA-256
`41a1cd415e0f8044f12c0769cc4cd0846ed7bbef0026c75fc16b3a8f403282a5`.
Independent verification found zero invalid/zero-duration timings, non-aligned words, missing
speakers, missing/nonfinite confidence values or decreasing word starts. All four accepted artifact
bodies and the normalized transcript matched their recorded hashes and sizes. Stage checkpoints
bind the exact source/build and alignment/diarization name their actual predecessor artifacts.

The independent detector reports **`needs_review`**, retaining provisional thresholds. Of
7,372.712 seconds of detected speech, 653.819 seconds are unmatched by recognition spans
(about 8.87%); the largest unmatched interval is 2.119 seconds and 0.434 seconds follows the final
word. That result requests listening review. It does not establish missing words, and the usable
transcript remains available with the warning.

Compared with the earlier combined protocol-4 run, the new transcript has one additional word
and the same speaker count/final endpoint. Sequence comparison matches 20,581 case-folded tokens,
with seven inserted and six deleted tokens; matching-token speaker IDs agree and median/P95
endpoint differences are zero. Neither output is human gold. The earlier run completed in
660.264 seconds with 635.22 measured function seconds; this isolated-stage run was slower.
The implemented tradeoff is independent model lifetimes, durable stage recovery and attempt
visibility. It has not won a cost or latency comparison. Cold starts, model loading and provider
variation are included differently across observations; repeated controlled runs and invoices
are needed to attribute that difference.

## Scope and retained evidence

Metadata-only results are in the [machine-readable record](checkpointed-speech-qualification-2026-09-09.json).
Full checkpoints, normalized transcripts, provider handles, manifests and attempt reports were
preserved privately with mode 0600 before teardown. The additive application was stopped and
reported zero tasks. Cleanup deleted **28 objects across four owned R2 prefixes**, verified those
prefixes empty, dropped the dedicated database, deleted the dedicated namespace and removed the
temporary credential files. Shared staging and the old application remain unchanged.

These tests establish this source's long-duration execution and the short recovery path. Live OOM
recovery, a broad memory envelope, human transcription/speaker/language accuracy, actual invoice
costs and live editorial-model auditions remain unqualified. Controlled tests cover OOM downgrade
and cancellation; they do not replace those live measurements. PR delivery additionally requires
the exact-commit local receipt and GitHub provenance check, linked from PR #24.
