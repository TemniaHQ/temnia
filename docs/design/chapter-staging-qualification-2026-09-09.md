# Chapter staging qualification — 2026-09-09

**Status:** PR #28 is merged and deployed. The same run has passed summary grounding using its
25 saved responses, with six explicit source-excerpt fallbacks for ten misplaced quote anchors.
The global proposal exhausted its output-token limit with incomplete JSON; the one repair then
hit the 300-second request timeout. The run is fenced as `outcome_unknown`, with $0.020236 reported
and $0.109644 still reserved. Render, review and export remain unqualified. The source, run and
original raw responses are retained; model quality is still unmeasured.

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


## PR #27 deployment and same-run continuation

The final PR #27 commit `0786e76` passed its exact-commit gate at 13:28:28 UTC, including all
1,087 application tests, both Docker images and 18 production-image browser tests. Two earlier
attempts had failed at npm tarball downloads; bounded Docker install concurrency and fetch time
resolved the observed build failure. The pipeline image also fetched and loaded its pinned models.
The required GitHub check passed before Rajesh merged `0c37a9c` at 13:59:49 UTC.

Both running services were verified before retry. The web replacement started at 14:01:16 UTC,
and the pipeline replacement at 14:04:49 UTC. The four changed pipeline file hashes match the
merge, the worker reports ready, and web health is HTTP 200. Both retain the admitted gateway
snapshot, $3 maximum, 32 dispatches and one repair; recorded mode is disabled, the gateway key
remains worker-only and the existing Modal speech configuration is unchanged. The pre-resume
private receipt SHA-256 is `f3bca24f9574051d48c9aab4d2c597f028aca6c81c3b483f5abdf6abe0f8784e`.

A single Retry through the authenticated browser resumed the same run at 14:10:41 UTC. The UI
confirmed its durable command result; the database retained the original run, source and ledger
with seven dispatches, 4,196 spent micros and zero reserved exposure. The child execution moved
past evidence preparation and the seven prior summaries into previously unattempted windows.
After the child stopped, all original seven operation, attempt, artifact and dependency tuples
matched the pre-resume receipt. Each operation still has its single original physical attempt;
the additional 18 dispatches belong to previously unattempted windows.

This continuation also exposed stale current-error text during a running retry and a budget
field initialized independently of the selected run. The
[recovery-state plan](../plans/chapter-resume-states-360-view.md) addresses both. An initially
stale accessibility option did not reproduce when the native selector was opened, so its query
is unchanged.

At **14:28:26 UTC**, after every summary had returned, the global planning activity rejected
`summary quote anchor lies outside its source unit`. The first violation was in window 14:
two visible quote anchors belonged to sentence 1179, just before the unit's range 1180–1182
(these are zero-based diagnostic indices). Inspection of all 25 responses found **10 misplaced
anchors across five windows**. All were copied from visible input anchors; none were invented.
Every unit and window sentence range still passed ordered exact coverage. This is a separate
semantic citation failure, not a recurrence of the cosmetic summary-label defect.

All 25 known responses remain accepted, with **15,614 spent microdollars**, zero reserved,
25 physical dispatches and zero semantic repairs. Revision remains zero. No proposal, chapter
render, verifier or export was dispatched. The complete resumed history has 191 events; the
private capture SHA-256 is `ccf9447d92c3909b98cd7e81789f07bfede88e5a11fd3155ea530a433afbbd83`.
The 25 summaries contain 201 units. Six units need the proposed fallback; 195 retain their
original prose. Their full source excerpts range from 68 to 1,057 characters. A read-only
simulation using each range's first and last word anchors changes the complete hierarchy prompt
from 154,506 to 152,956 UTF-8 bytes, within the 512 KiB payload ceiling. These are sizing
measurements, not a successful production implementation test or model-quality score.
The private failed-run evaluation bundle SHA-256 is
`d3f6886038dc4ba6d428a55b045402a6d88a79cf4632fc392bc90c9e8af0c3f2`;
its report remains incomplete, with no accepted-chapter or human-label denominator.
The all-response grounding analysis SHA-256 is
`46ceced4a0efc76e6d14bb96a2f67f169c287444a8401a93a1e25d81fbd6c32c`.

The [grounding plan](../plans/chapter-summary-grounding-360-view.md) moves validation after each
summary and replaces an eligible invalid unit's whole generated text with an exact source
excerpt, with immutable provenance and a visible reviewer notice. It keeps source coverage,
non-prompt/foreign ID refusals, model budgets and the original raw response identities intact.
No second Retry, new run, budget increase or source/GPU change was made after this failure.

The follow-up's focused checks pass: 12 grounding/persistence tests, four Temporal hierarchy
tests, 20 export/evaluation tests and 218 web tests (three existing skips). Python and web static
checks are clean. Both retained histories replay against the new worker, as does a newly recorded
history containing the validation patch. The real Temporal/Postgres test runs the same summary
in two executions while retaining one provider call, one model attempt and one grounding report;
its raw response, accounting and repair count remain unchanged. These local checks establish the
recovery mechanics, not the untested live proposal, render or editorial result.

The current production prompt validator and grounding functions also pass against all 25 retained
Temporal responses offline: 201 units, 195 unchanged, six source-excerpt fallbacks across five
windows, and 10 rejected anchors. The resulting global proposal prompt is 152,956 UTF-8 bytes.
All three frozen proposal routes admit it under the current context estimator; their prompt-based
reservations are 14,367, 105,150 and 606,324 microdollars respectively. The dispatched request still
includes its serialized envelope and must pass the ordinary reservation gate. No provider call
was made for this check.

The private offline receipt SHA-256 is
`5a8603b6bda931a495287e1878c3dee27d6732096c2da783bb144bfc8797b7ce`.
It records that 21 retained Temporal response envelopes match the artifact-body hash exactly;
four differ in non-output serialization. Per-window summary/violation facts and the original
seven operation, attempt, artifact and dependency hashes match the retained capture. This offline
result is not a claim that all response envelopes are byte-identical, or that the live run has
completed.

## PR #28 deployment and live grounding recovery

PR #28's exact commit `ed6e6b6` passed the release gate at 15:51:04 UTC: 819 Python, 221 web,
41 database, 28 contract and three legacy tests (1,112 total), both deployment images and all
18 production-image browser tests. The required GitHub check passed. Rajesh merged it as
`ef2a6fd` at 16:03:07 UTC.

The new web container started at 16:03:49 UTC and the new worker at 16:06:32 UTC. Both deployment
records identify the merge and report done. All nine changed pipeline files match the merge
byte-for-byte; web's deployed bundles contain the new UI copy. Startup, migration/seed and web
health checks passed with zero container restarts. Both services retain the admitted route
snapshot, $3 ceiling, 32 dispatches, one repair, 8,192 output-token limit, 80-sentence windows and
render concurrency two. The gateway key remains worker-only. The private deployment receipt
SHA-256 is `364a63fb9d334184c49dc97d5325dedff3beb527452fd38c37f62c99671ab930`.

At 16:59:03 UTC, the scoped pre-retry capture confirmed the original failed run at revision zero,
25 dispatches, $0.015614 spent, zero reserved and zero repairs. All 25 operation, attempt,
response-artifact and dependency tuples were retained for exact comparison. No active run
conflicted with the retry. The private baseline receipt SHA-256 is
`2de158ca3e6e7d2627d9cec951a46a4455f64b55008adb7ca2256e9a38b525be`.

One browser Retry resumed that same run at 17:00:05 UTC. The UI confirmed its durable result,
cleared the old failure text and showed the run's actual $3 maximum. The source cache contains
the complete 1,911,080,861-byte master and its identity sidecar. The worker has 356,533,506,048
free bytes, exceeding the 6,806,984,407-byte conservative all-keep render preflight.

The resumed workflow reached `validate_chapter_summary` on activity attempt one. All 25 windows
passed the new grounding path without another summary dispatch or model repair. The live UI
reports the same six fallback passages and ten misplaced references found offline and exposes
their immutable reports. At 17:03:46 UTC, dispatch 26 started `proposal:global`, reserving 15,086
microdollars for its complete serialized request. That reservation is exposure, not a reported
charge. This checkpoint establishes summary recovery only; later stages need their own evidence.

The DeepSeek proposal returned at 17:07:07 UTC with a reported $0.004622 charge and a durable
response artifact. Its text is 26,780 bytes and the provider finish reason is `length`: exactly
8,192 output tokens left an incomplete JSON document. Strict local parsing refused that output.
The workflow's generic schema-error branch consumed its one repair and sent the same prompt and
output cap to Qwen, without communicating the truncation or other validation feedback.

The Qwen repair ran from 17:07:07.886578 to 17:12:07.929161 UTC, then failed through
`ReadTimeout → APITimeoutError → ModelAPIError → OutcomeUnknown`. It has neither a durable
response artifact nor a provider generation handle. The final scoped capture records 27
dispatches, one repair, revision zero, $0.020236 reported and $0.109644 unresolved exposure.
A timeout does not establish whether the provider processed or charged the request. The reservation
remains in place; no further Retry, cancellation, budget change or provider call followed it.

The 361-event execution history and portable evaluation bundle are retained privately. All 25
original summary operation, attempt, response-artifact and dependency tuples match their baseline
hashes exactly. The bundle verifies 25 grounding reports: 195 first-pass reference-valid units and
six extractive fallbacks, across five reports, for ten rejected anchors. It reports incomplete cost
and no model winner. There is no chapter edit, render, verifier, accepted revision or export, and
no human listening or editorial labeling was performed. The private combined-capture SHA-256 is
`5d45ff0da1742aa437a6094199d426d919125599cec5b0db503a88d3883b6309`.
