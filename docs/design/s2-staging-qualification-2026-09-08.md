# S2 staging qualification — 2026-09-08

**Result: PR #23 is merged and deployed; the long-source transcription completed successfully.** This is an engineering qualification of one recording, not a claim that S2 quality, recovery, cost accounting or the chapter editing harness is complete. The [follow-up plan](../plans/chapter-workflow-followup-360-view.md) covers the remaining work.

## Release and scope

Rajesh explicitly requested merging and testing directly on staging. [PR #23](https://github.com/TemniaHQ/temnia/pull/23) was squash-merged at 12:08:10 UTC as `555eb248bb309855279e3f722c676eb0a43dabcd`. The exact reviewed head had passed the local gate and required GitHub check. Squash was used because the branch rules reject merge commits.

The paired rollout paused automatic deployments, stopped new web submissions, and verified no running Temporal workflows, saved active GPU calls, Modal backlog/running inputs or recently active uploads before stopping the old worker. The actual Modal app was deployed at protocol 4, its deployed smoke passed, and matching web/worker images started. Both applications' automatic deployments were restored and verified. Staging remains running.

Verification checked the new containers rather than a queued deployment response: web boot applied migrations and seeded rows, `/api/health` returned 200, and the pipeline connected to its expected namespace/queue with Modal transcription and transcoding enabled. All 58 packaged Python/runtime manifest files matched merged main. The pipeline role could read the new transcript run-ownership and ledger idempotency columns. No runtime code changed in this validation session.

The browser reached the deployed app through a temporary localhost-only SSH tunnel to the private container. This tested the deployed Next.js actions and scoped media proxy; it did not test the public Cloudflare Access login or public-origin upload CORS. An existing completed transcript was preserved as a baseline. One other already-ingested source had no transcript; its real UI Retry action started this qualification. No source re-upload or new HLS encode was performed.

## Actual deployed smoke

| Measurement | Observed |
|---|---|
| App / environment / protocol | `temnia-media` / `staging` / `4` |
| Helper and GPU source/config build | `3f2c496c9aa90a16ed74f4eb35bd38ebe4513eac7b17e8b3a3eeb6f33bd9ed55` |
| Fixture | 8.824-second spoken test sample |
| Words / speakers / language | 27 / 1 / English |
| Missing expected / interpolated words | 0 / 0 |
| GPU-function elapsed / helper elapsed | 29.576 s / 37.4 s |

The smoke helper deleted its temporary storage prefix. The actual media app remains deployed. Function elapsed time is not a provider invoice or full billed container lifetime.

## Long-source result

| Measurement | Observed |
|---|---|
| Source ID | `01a07b1d-3b3b-727d-9456-824effb25346` |
| Source duration / original master size | 9,060.473 s (151 min) / 1,911,080,861 bytes (1.91 GB) |
| Workflow ID | `transcribe-01a07b1d-3b3b-727d-9456-824effb25346` |
| Workflow run ID | `01a080fe-4389-7613-bc28-3ba5ae4dcb01` |
| Saved Modal call | `fc-01M20FWJ252T1KWHG1G9JG842W` |
| Terminal states | Temporal `COMPLETED`; transcript `ready` |
| Claimed attempts / observed transcription activity attempts | 1 / 1 |
| End-to-end workflow elapsed | 660.3 s (11 min; about 13.7 times faster than source playback) |
| GPU-function elapsed / GPU | 635.22 s / L4 |
| Canonical words / stored word count | 20,587 / 20,587 |
| Speakers / utterances / language | 2 / 241 / English |
| Interpolated words / malformed timing checks | 0 / 0 |
| Zero-duration / unassigned-speaker words | 0 / 0 |
| Alignment scores below 0.5 | 2,609 (uncalibrated diagnostic, not word-error rate) |
| Accepted revision / canonical JSON bytes | 1 / 2,117,635 |
| First word start / last word end | 91 ms / 9,044,040 ms |
| Unrecognized tail / largest internal uncovered gap | 16,433 ms / 5,686 ms |

The same saved remote call remained associated with the activity throughout the observation. It progressed through recognition, alignment, speaker assignment and writing with fresh heartbeats, without an observed activity retry or OOM. Timing checks covered negative values, end before start, out-of-order word starts and ends outside the source envelope. They do not measure alignment accuracy against audio.

The scoped ledger contained one `transcription_seconds` entry, quantity 9,060, with idempotency key `transcription:01a07b1d-3b3b-727d-9456-824effb25346:1` and reported GPU time 635.22 s. It also contained one transcript `storage_bytes` entry of 2,117,635. This establishes successful finalization for this run, not complete provider-expense or failed-attempt accounting.

Unrecognized gaps and the 16.433-second tail require independent speech evidence or listening before classifying them as silence or omission. Two detected speakers do not establish correct word-level attribution. No human reference transcript, diarization gold, peak-memory instrumentation or provider bill was obtained.

## Browser checks and findings

The completed baseline transcript loaded, displayed two speakers, found two occurrences of a single-word query, navigated to the second match, sought from 0:00 to 0:08 when a word was clicked, and played decoded video. Its speaker dialog was dismissed without saving. Browser errors and console output were empty.

The newly generated transcript appeared automatically when processing completed. It also found the two single-word matches and sought the video to 8.193 seconds from the first match, with no browser errors or console messages. No transcript edit, deletion, speaker save or duplicate Retry was submitted during these checks.

**New finding:** progress displayed 0% during recognition, alignment and diarization, then 100% while writing. The stages changed and heartbeats remained fresh, but the displayed percentage did not describe completed work. The follow-up must show an indeterminate stage/elapsed state or measured window/batch progress; a live poller must not imply live computation.

**Still unqualified:** independent speech completeness; broad language/input support; consecutive warm-run memory behavior; OOM/cancellation/crash recovery; failed and uncertain attempt cost; actual invoice reconciliation; tens-of-gigabytes upload/transcode performance; phrase search/structural correction; chapter editing and held-out human quality. The long test used the existing audio extract, so it does not qualify a new ingest or NVENC run.

## Evidence handling and next work

The repository records metadata and conclusions, not the recording or transcript text. Full operation logs and browser screenshots were retained in the local staging QA evidence directory for inspection. A separate read-only verifier pinned the exact Temporal run and passed all 18 consistency checks against the completed activity call ID, workflow result, fresh scoped rows, strict transcript contract, object bytes and ledger. The canonical revision SHA-256 is `c4222ccd028e5be24270c8411baaba589000bf997578161b322b674c8e1b51be`. Its metadata-only output is retained in [the verification record](s2-staging-verification-2026-09-08.json). The temporary browsers, observer and SSH tunnel were closed after verification.

Continue on the new follow-up PR, not merged #23: close the remaining S2 reliability findings; implement versioned evidence/edit contracts and atomic operation accounting; deliver planning, joint boundary compilation, rendering, checks and human correction; then compare editorial quality and total cost on held-out recordings. These are planned work and must remain marked incomplete until their acceptance evidence exists.
