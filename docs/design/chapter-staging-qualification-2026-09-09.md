# Chapter staging qualification — 2026-09-09

**Status:** the live harness is enabled on staging. Both services passed configuration and boot
verification. The first real-source chapter run has not started: the authenticated browser is
currently inaccessible because the operator's Mac is locked. No chapter inference expense has
been incurred by this enablement. Render, review and export qualification remain pending.

## Deployment and route evidence

PR #26 merged as `da54adc3993de3c6c885c345709fcad8894a5b03`. Its pipeline deployment completed with
the same gateway and prompt hashes used by the successful live transport checks. The web's code
did not change in that PR and remained on its verified PR #24 image.

The three route proofs in the [gateway qualification record](gateway-staging-qualification-2026-09-09.md)
were rechecked against their retained response hashes and assembled into snapshot
`08bf9b1b9a07c909dbf98362a5660a7358795a198afdd0c5ea05be749aa64f84`.
Its file SHA-256 is `3514fda12b7a096c9ecba8e8820b100983fed106ea65de00954a935349291c7e`.
The PR #26 image running before the configuration rebuild validated it in a separate container
without network access. The final rebuilt image validated the same snapshot again at worker boot.

This experiment starts summaries/proposals with DeepSeek V4 Flash on DeepInfra and independent
verification with Qwen 3.8 27B on Alibaba. Qwen is the first proposal repair candidate; Kimi K3 on
Alibaba then remains available as an independent verifier. Every seat contains all three
qualified families, all owner-verified open-weight releases. Caching stays off. This provisional
order starts with the cheapest transport-qualified generator; it is not a human quality audition
result, a general vendor default, or evidence that these models produce the best boundaries.

The route file contains no credential. Dokploy binds a root-owned `0444` copy into the worker;
the running UID-10001 worker can read it but its write attempt raises `PermissionError`. The
standard Dokploy v0.30.5 mount API cannot express a Docker read-only bind. The gateway key remains
only on the worker and is absent from web.

## Long-source admission

The chosen existing recording is 9,060,473 ms long, 1.8 GB, 1080p25 H.264 with stereo AAC. Its
current ready transcript contains 20,587 words, 241 provider utterances and two speakers. No new
upload, transcription retry, GPU invocation or source correction was needed. The authenticated
browser loaded the current transcript and played source video before the Mac locked.

The exact baked `sat-3l-sm` model produced **1,989 sentences and 331 paragraphs**. A second cold
process produced the same counts. One measured call took **61.167 seconds wall time** and
338.212 CPU-seconds on the staging VPS. This is a single performance sample, not a guarantee.
The production activity runs segmentation in a thread while heartbeating independently.

At 80 sentences per window, all prompts fit without shrinking: **25 windows**, between 11,871 and
14,450 bytes, totaling 320,321 bytes. Their exact conservative reservations total $0.078883.
A no-compression projection of the summaries fits the global proposal request. Actual model
summaries have not been produced and can still require bounded reduction steps.

The full-source verifier baseline is 296,656 bytes. Qwen fits it with a $0.177000 reservation;
after a Qwen repair, Kimi's baseline reservation alone is $1.037424. This exposed a flaw in the
initial $1 operator smoke plan before any dispatch. The final run configuration is:

| Setting | Value |
| --- | ---: |
| Maximum run budget | $3.00 |
| Maximum physical model dispatches | 32 |
| Maximum semantic repairs | 1 |
| Maximum output tokens per call | 8,192 |
| Initial evidence window | 80 sentences |
| Concurrent renders | 2 |

The strongest planned repair path reserves **$2.310384**: the 25 measured initial summaries,
four maximum-size reduction requests, a maximum-size initial proposal, one maximum-size Qwen
repair and one maximum-size Kimi verifier. The $3 cap leaves $0.689616 headroom. These are
conservative admission bounds, not measured charges. A deeper hierarchy can still reach the
32-dispatch limit; that is a visible bounded stop. The budget will not be raised after this
qualification run is admitted.

Scoped reads found no active ingest/transcription work and no existing chapter runs before
rollout. Private evidence retains source/transcript identities, exact word and window facts,
helper hashes, configuration backups and deployment receipts.

## Runtime verification and deployment correction

The worker's code deployment produced image
`sha256:8ad93e30f87c70693af2ec087d6e9a87e821f6052cb1a95b235c3f8a824f4550`.
It captured the earlier $2 environment when its build started, despite a later saved change to $3 during
the build. Inspecting the actual task caught that mismatch before web was enabled.

The installed Dokploy implementation and a live probe then established that its supported
`application.reload` applies saved environment and mounts without rebuilding. The worker
replacement started at 11:55:02 UTC with the same image and the final $3 cap; its source hashes,
snapshot, file permissions and Temporal startup were verified. Web reloaded at 11:56:32 UTC,
retaining image `sha256:a83cec69c3e53ca247456e5d8ade316cd9f76d0917cb99ea36dd81c938b74e94`.
Its matching harness settings, migrations/seed and HTTP-200 health response passed. The temporary
uncached-build setting was restored. The runbook's image-changing-only claim is corrected with
this observed configuration-only path.

The read-only Temporal check found one workflow poller and one activity poller on the main
pipeline queue, plus one activity poller on its control queue. A scoped source query still
reported no chapter run. The private observer was exercised against this running worker before
being retained for the live test.

## Remaining live checks

Unlocking the Mac restores access to the authenticated staging browser. Start exactly one run
on the selected source with the frozen $3 budget, retain its request/run identity, and stop on
unknown outcome, budget pause or technical failure. Inspect the actual rendered files, captions,
cut previews and acceptance/export flow. A successful transport check or healthy boot does not
establish editorial quality, clean render output or end-to-end completion. Human boundary labels,
listening quality and model auditions remain separate measurements.
