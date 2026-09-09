# Chapter staging qualification — 2026-09-09

**Status:** the live harness is enabled on staging. Its first real-source run failed in planning
after seven summary calls and before publishing an edit. The seven charges were reconciled to
**$0.004196** in the integer-microdollar ledger, with zero remaining reserved exposure. The run
and its raw responses are retained. PR #27 addresses the observed failures; render, review and
export qualification remain pending.

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

The chosen existing recording is 9,060,473 ms long, 1,911,080,861 bytes, 1080p25 H.264 with stereo AAC. Its
current ready transcript contains 20,587 words, 241 provider utterances and two speakers. No new
upload, transcription retry, GPU invocation or source correction was needed. The authenticated
browser loaded the current transcript, played source video and created the chapter run.

The exact baked `sat-3l-sm` model produced **1,989 sentences and 331 paragraphs**. A second cold
process produced the same counts. One measured call took **61.167 seconds wall time** and
338.212 CPU-seconds on the staging VPS. This is a single performance sample, not a guarantee.
Segmentation runs in a thread. The first live run exposed a separate missing heartbeat while
waiting for a source-download chunk, described below.

At 80 sentences per window, all prompts fit without shrinking: **25 windows**, between 11,871 and
14,450 bytes, totaling 320,321 bytes. Estimating those prompt strings gives $0.078883. These
preflight estimates omit the serialized model request envelope; the production ledger measures
that envelope and reserves again before each dispatch. A no-compression projection of the
summaries fits the global proposal request. Actual summaries can require bounded reduction.

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

The preflight repair scenario totals **$2.310384**: the 25 measured initial summary prompt estimates,
four maximum-size reduction requests, a maximum-size initial proposal, one maximum-size Qwen
repair and one maximum-size Kimi verifier. Its nominal headroom is $0.689616 before the initial
request envelopes. This is a planning estimate, not a guarantee that every repair path fits.
The actual serialized requests face the unchanged $3 reservation gate and 32-dispatch limit;
either can produce a visible bounded stop. No post-admission budget increase was made.

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

## First real-source result

The browser created exactly one run at **12:07:23 UTC**. Its identity is retained privately;
the run used the admitted source, transcript revision, route snapshot and $3 configuration.
The transcript's stored revision metadata has no scalar SHA, so evidence preparation verified
the actual 2,117,635 bytes and computed their SHA-256. A diagnostic helper initially confused
the digest of the complete pinned-transcript descriptor with that byte digest; comparing the
underlying fields resolved the discrepancy without changing the source or admission.

Evidence preparation's first attempt timed out while waiting for the next 8 MiB object-store
chunk. Progress heartbeats were sent only when chunks arrived. The second attempt completed
at **12:10:54 UTC**. This exposed a liveness gap during quiet I/O, rather than a GPU failure.

Seven summary activities then returned known responses, with no model-activity retries. At
**12:14:17 UTC**, local validation rejected the seventh response: all five summary units had
empty `id` labels. The workflow recorded a planning failure, revision zero, and cleaned its
source cache. No chapter was rendered, accepted or exported. The raw response remains immutable.
Temnia should assign these internal summary labels deterministically; source sentence IDs,
word anchors, text and exact coverage remain strictly validated.

Immediate generation lookups left all seven charges unresolved and retained **$0.024046** in
reservations. The original usage-observation fields were not preserved before reconciliation;
the precise initial lookup failure is therefore unknown. The new runtime records a sanitized
error class and polls only explicitly pending receipts. Later read-only lookups verified every generation/model/provider identity. The
shipped reconciler applied 574, 588, 691, 726, 531, 461 and 625 microdollars respectively, totaling
**4,196 microdollars**. The exact gateway receipts total **$0.00419354**; each charge is rounded
upward to integer microdollars for the ledger. All seven reservations are settled, dispatch count remains seven, and the run remains
failed. A second reconciliation found no unresolved rows. This used no new inference.

The private reconciliation receipt's SHA-256 is
`b4ca92c8da085910e215ceab8aff8af5e41c24c9be5d3c368858d0d7ee2e0a4a`.
The retained deployment/configuration receipt's SHA-256 is
`429c07c4c6fd9fdb6aa915ec7dc0eceb04dc82e7e3d9fb5a248b74f058d9423e`.

## Fixes and remaining live checks

The [failure-fix plan](../plans/chapter-live-failures-360-view.md) covers deterministic summary
labels at the activity boundary, bounded polling of delayed cost receipts, evidence heartbeats
during quiet I/O, and two misleading UI messages. Committed responses must remain reusable;
changing request identity or hiding source-reference errors would defeat that recovery.

All seven retained summaries pass both current exact-cover validation and the stricter check
that quotes use only visible prompt anchors, after correcting only the seventh summary's labels.
The private failed-run evaluation bundle contains the evidence and seven attempts; its SHA-256 is
`f53d93a9af89f97d04f7c4b6b3f1781162c1156ccad45c19a1b4005b7c0d5c4c`.
The complete 77-event Temporal history is retained for offline replay, SHA-256
`732b97d7c5d19ab7725bf92fdafdc10cf7cffea38c9fce4f993c368b840b5b44`.
It replays successfully against the changed worker code. A separate real Temporal/Postgres
regression proves that two executions reuse one operation and one physical model attempt,
while the stored blank-label response remains unchanged. The model transport suite passes
19 tests; cost polling/qualification/CLI passes 34; evidence liveness and workflow checks pass
22. Web tests pass 214 with three existing skips, and web typechecking plus Python static checks
pass. The final exact-commit release receipt and production browser gate belong to PR #27.

After the fix is merged and deployed, recover the same run and verify no repeated dispatch for
its seven committed responses. Then inspect rendered files, captions, cut previews and the
acceptance/export flow. This failed run does not establish editorial quality or end-to-end
completion. Human boundary labels, listening quality and model auditions remain separate work.
