# Checkpointed speech performance qualification — 2026-09-09

**Outcome: all twelve planned cases completed; no new configuration selected.** This experiment compares progress publishing, CPU allocation and
stage scheduling on one 151-minute recording. It is a performance screen, not a transcription-quality
benchmark. The exact-output acceptance rule was not met, so it does not select a new production
configuration. Protocol 2 remains explicitly configured; shared staging was not changed.

## What changed

PR #24 moves progress-store network writes out of inference callbacks into a bounded, coalesced
publisher. It binds explicit CPU limits and frozen model assets to each run and adds parallel raw
speaker detection alongside recognition → alignment. A deterministic CPU operation joins the two
accepted inputs. Individual GPU checkpoints, create-only execution admission, physical-attempt
accounting and cancellation drainage remain intact. There is no fourth GPU call for assignment.

The earlier [protocol-1 run](checkpointed-speech-qualification-2026-09-09.md) used 1,084.810 workflow
seconds and 1,021.261 function seconds; the older combined GPU run used 660.264 and 635.220 seconds.
Different CPU limits and model provenance prevent attributing that earlier difference to isolation.
Both used GPUs. This experiment makes those factors explicit instead of assuming a cause.

## Controlled comparison

| Variant | Progress publishing | CPU request/limit per call | Scheduling | Peak concurrent L4s |
| --- | --- | ---: | --- | ---: |
| A | Synchronous control | 4 / 4 | Serial | 1 |
| B | Background, coalesced | 4 / 4 | Serial | 1 |
| C | Background, coalesced | 8 / 8 | Serial | 1 |
| D | Background, coalesced | 8 / 8 | Recognition → alignment alongside speaker turns | 2 |

Every physical stage uses one L4 and 16 GiB of host memory in a single-use container. D uses two
L4s concurrently and three invocations in total; it reuses C's deployed application. Co-location and
GPU snapshots were not tested. The order was short A/B/C/D, long A/B/C/D, then long D/C/B/A.
Short cases exercise recovery and are excluded from long-source latency comparisons.

All long cases use 9,060.473 seconds of audio, 110,024,458 bytes, SHA-256
`85615a2b6e04512cb333b80c4af6080a0f2017b355146b289303ed57bacc0c91`.
The short fixture is 40.116 seconds, 795,054 bytes, SHA-256
`31ae0e36255c429b9842b7fdc2cfe2edec9034096ac1b188be943173381a57af`.
Repeated processing of one source is not evidence across eight independent recordings.

| Case | Workflow seconds | Summed function seconds | Resource estimate (USD) | Output group |
| --- | ---: | ---: | ---: | --- |
| A1 | Excluded (retry) | 892.008 | $0.276452 | X |
| B1 | 776.556 | 719.077 | $0.222858 | X |
| C1 | 777.203 | 688.369 | $0.249411 | X |
| D1 | 523.518 | 824.702 | $0.298807 | Y |
| A2 | 1,077.178 | 1,012.410 | $0.313767 | Y |
| B2 | 819.884 | 756.952 | $0.234596 | X |
| C2 | 762.104 | 695.022 | $0.251821 | Y |
| D2 | 533.682 | 828.541 | $0.300198 | Y |

The [machine-readable results](checkpointed-speech-optimization-2026-09-09.json) retain exact
identities, exclusions, output hashes, per-stage telemetry and all paired comparisons.

Workflow wall time includes orchestration after source preparation. Function time sums the three
GPU function envelopes, so parallel work can reduce wall time while using more total resource time.
Neither includes every billable provider allocation or startup interval. Source upload/preparation
also makes case elapsed time longer than workflow wall time. No estimated overhead is subtracted.

Background publishing is the promising cost-conscious change: B2 reduced the one clean A comparison
from 1,077.178 to 819.884 seconds (**23.89%**) and the resource estimate by **25.23%**. Its first-block
function reduction was 19.39%, but A1 has no eligible wall comparison. These are one-source screening
results with the output qualification below, not a general speedup guarantee.

Doubling CPU allocation from B to C did not meet the intended tradeoff: workflow wall time changed
by +0.08% and −7.05%, while the resource estimates increased 11.91% and 7.34%. Parallel D reached
523.518 and 533.682 seconds, or about **8.8 minutes for 151 minutes of audio**. Compared with B,
that is 32.58% and 34.91% less wall time but 34.08% and 27.96% higher resource estimates. Against C,
D saved 32.64% and 29.97% wall time while raising estimates 19.81% and 19.21%. It is a speed/cost
tradeoff, not the lowest-cost arrangement.

## Output compatibility and selection

The eight outputs form two internally identical groups: **X = A1/B1/B2/C1**, with 20,587 words;
**Y = A2/C2/D1/D2**, with 20,588. Both contain English, two speakers, a first word at 91 ms and
last word ending at 9,044,040 ms. Exact stage-payload comparisons and normalized transcript hashes
are retained; raw recording text remains private.

The observed alternate result differs by three lexical substitutions and one insertion after case
and punctuation normalization, plus seven punctuation/case-only replacements. Every recognition
segment retains its original interval; the differences begin in recognition. The independent
speaker-turn payload is identical: 1,670 intervals. No paired shared word changes speaker. Nine
paired word timings differ, confined to changed phrases; five shifts are 20 ms, three are 220–280 ms,
and one redistributes a boundary by 1,523 ms within the changed phrase. All other paired times match.
The inserted word changes the speaker-sequence hash without changing the diarization partition.

C1 and C2 use the same serial topology, application, resource profile, model/configuration and audio
bytes but fall into different output groups. The variation therefore occurs without switching to
parallel scheduling. Small recognition log-probability differences accompany it; their lower-level
cause is unproven. The earlier uncontrolled protocol-1 output matches the alternate group, which is
additional compatibility evidence rather than a controlled comparison. Listening or labels are
needed to determine which wording/timing is more accurate; token equality is not an accuracy score.

The rules were recorded at 05:37:56 UTC, before B1 completed. They require all eight terminal reports,
the seven clean wall measurements plus A1's reviewed exclusion, exact language/tokens/timing/speaker
partition, at least 10% wall reduction in each eligible pair, and no more than 10% estimated-resource
increase. Moving from four to eight CPUs must break even on the resource estimate. The omitted B→D
comparison was added at 05:54:37 UTC before B1 completed; thresholds did not change. A-based wall
comparisons have only the clean second block and remain provisional even if their numbers pass.

The global exact-output rule failed. No comparison can pass that declared selection rule, including
a candidate that happens to match its own paired baseline. It is not relaxed after seeing results.
The timings remain descriptive evidence. A future selection experiment should prospectively define
acceptable transcription/timing differences with listening or labels, then test additional sources.
No billed-cost winner exists without sufficiently attributable provider billing.

## Frozen identities

- GPU deployment source-build identity: `2ec251d208c3361b34f86ad1bc0aed86f605f212bdc3d13ab4bc1512abb88b01`, from `6f16b9e830e7fc0afa9e7f806966cc2128d00716`.
- Initial corrected worker: `0c64ba401a45f860ae83f0b00911091a7f3177fe2f8387ec352dd3d9d6fb5813`, used for recovered short A, short B/C/D and long A1.
- Heartbeat-repaired worker: `9f972e6f0e282e4ff504323dd3dacdfb97cc744dc24b42ff35dfc82c19943b22`, frozen for B1 onward and qualified on `aba6d93da5c691657969e2508864d326263ac96d`.
- Deployment manifest: `09d308e6488dfafcce6e7265be67c96b6b2783e5d0eef292f56065fa2e1eab7a` (canonical SHA-256).
- Frozen model volume: 23 files, 6,624,978,799 bytes, manifest `c8323dcc132fe24d12d0c856e1fa9610f41af6391679d1cdc2999ebdfe99dea4`.
- Image-bundled model assets: `b67cbb5bb2c5f22ff98be5504b1b309861ea54d59cf970457cc0cf586d249be9`.

GPU applications and model assets were never redeployed between cases. Wall pairs require matching
worker builds; A1 fails that comparison as well as its retry exclusion. Original GPU telemetry remains
comparable under the unchanged GPU source-build/model identities. This asset set qualifies the English
recording only. Later PR #24 accepted-download UI and route-capacity boot checks were implemented in
a separate checkout; they did not change the running benchmark worker or its reported fingerprint.

## Failures exposed and recovery retained

The first short run accepted all three GPU results, then CPU assignment failed because the Drizzle
artifact enum lacked `speech_assignment`. The failure report also queried the wrong dependency
column. Migration 0004 and the query correction were exercised against real PostgreSQL. The original
failed run, three attempts and 1,062,501 micros of unresolved exposure remain preserved.

Recovery preflight then caught a selector assumption: independent speech coverage legitimately shares
the `speech_checkpoint` kind. The selector now follows the exact three GPU operation result IDs and
validates their identities/dependencies instead of counting every source artifact of that kind.
Create-only continuation/start-intent records, exact journal hashes, local/database leases and
latest-execution checks prevent a lost start response from reopening the recovery exception.

The separate CPU-only recovery reached ready in 12.274 seconds with zero GPU attempts, expense or
new exposure; its one-micro budget and refusing GPU client made a cache miss fail. The original failed
run/accounting was unchanged. All four short cases have identical 93-word/two-speaker output. B/C/D
each recovered a deliberately lost activity result with observed dispatch counts `[3, 3]`.

Long A1 completed three GPU calls totaling 892.008 seconds, then Temporal history recorded a
heartbeat timeout for the enclosing activity. Its retry reused accepted work and completed in 3.203
seconds. The benchmark stopped as intended; the 989.952-second workflow wall time is excluded.
Reconstructing the exact accepted CPU assignment took a median 0.207 seconds and was byte-identical.
The repair supplies one owned Temporal-only heartbeat throughout preparation, storage waits and
post-GPU publication, independent of diagnostic progress. It retains the ten-second timeout and
cancellation cleanup. Bounded completed-case acknowledgment preserved the failed-validation evidence
and continued only the original remaining cases, without rerunning A1 or increasing the budget.

A1's synchronous inference callbacks made 1,903 successful network writes and occupied 221.389
seconds. This is included in function/inference time, not additional elapsed time or a speedup that
can simply be subtracted. Coalesced callback, publisher and phase totals are recorded per case.
Sampled device memory/utilization and Torch allocations are available; cgroup CPU/throttling,
per-process GPU memory and process peak RSS remain unavailable. Successful completion without OOM
on this source is not a general memory bound or a live OOM-recovery qualification.

## Expense and owned-resource cleanup

The completed journal contains exactly **36 physical calls and 13,770,018 micros of reserved
exposure**, within the original 14,000,000-micro limit. All calls reached successful terminal states.
Every per-attempt actual cost remains unknown; reservations remain in the exported accounting.
The earlier nine protocol-1 attempts retain their separate 11,625,003 micros of unresolved exposure.

The read-only provider billing export for complete hourly intervals from 00:00 through 07:00 UTC
reports **$2.10079169** across the owned apps: A $0.36514462, B $0.51064727, shared C/D $1.22273983,
and preparation/probes $0.00225997. This is reported usage before credits/reservations, not a final
invoice. It excludes the final A2 work after 07:00 and cannot separate C/D or exact attempts, so it
does not settle ledger costs or establish a billed-cost winner. [Modal billing reports](https://modal.com/docs/guide/billing).

Configured rates are 1,250,000 micros/hour for four CPUs and 1,450,000 for eight. The dated resource
floors used in the estimates are 1,115,712 and 1,304,352 respectively. Eight CPUs therefore need about
14.46% less summed function time to break even at those floors. These estimates are not invoices.
The experiment allowed 900 seconds per stage plus 120 seconds startup, disabled provider retries and
OOM fallback, and admitted no extra repetition. Production configuration retains 3,600 + 120 seconds
and coalesced progress. [Modal pricing](https://modal.com/pricing),
[resource allocation](https://modal.com/docs/guide/resources).

The final private evidence archive contains 159 hashed files covering 36 terminal provider calls,
13 Temporal executions across twelve workflow IDs, all scoped database rows and 132 source-prefix
objects. Every object was hashed; non-media bodies were preserved, and source media was verified
against the two retained frozen local inputs. Archive manifest SHA-256:
`004dccb3b4df3723de6921e49e5058826ba22489108c2e38d0a4096ee3cce953`.

After rechecking unchanged evidence under the experiment leases, cleanup stopped the three exact
GPU apps and verified zero tasks, removed the experiment's frozen volume and 36 exact progress keys,
deleted/verified empty all twelve owned prefixes (132 objects), and removed/verified absent the
dedicated Temporal namespace and PostgreSQL database. The shared progress dictionary and shared
staging were preserved. Temporary operator/storage credential files were removed; frozen inputs,
full evidence and unknown-cost accounting remain private. Cleanup receipt SHA-256:
`2e2a4d89f093cf75dd6cc9ddbf3487445550e3715acffd7438dc56ac11cd20a5`.

The first read-only export refused because its private helper read a stage from `harness_attempt`
instead of the linked `harness_operation`. The corrected check follows the exact operation relation,
retains all other identity checks, and passed seven private regressions. The partial export was
preserved and no cleanup occurred before the later complete evidence proof.

## Release verification and remaining qualification

The benchmark's final frozen worker passed the full release gate on `aba6d93` at 05:42:34 UTC:
743 Python tests without skips, 187 web tests, 41 database tests, 28 contract tests, all 16 build/lint/
typecheck/test tasks, both Linux deployment images and 18 production browser journeys.

That followed an honestly retained failed gate: new Temporal tests had not closed their database
pool before function-scoped event loops ended. The exact ordered modules reproduced one failure and
13 fixture errors; a test-local `finally` teardown then passed all 16 tests. Production pool code and
the worker/GPU fingerprint were unchanged. The heartbeat regressions use real Temporal/PostgreSQL
with a twelve-second quiet post-GPU delay, prove one activity attempt, and verify cancellation keeps
heartbeating through cleanup without publishing a late assignment.

The final integration additionally includes actual accepted video/caption downloads and required-route
capacity checks at worker boot. Before the combined release gate, the download validator passed
23 focused web tests plus formatting/typechecking; the route-capacity module passed nine Python
tests plus Ruff/Pyright. Independent review corrected raw validation errors reaching the UI and
made the stale-response browser assertion wait for its exact response completion. The manual setup
also clears an inherited single-file transcript fixture override. The final combined commit requires
its own exact-SHA receipt and GitHub provenance check; those delivery results are recorded on PR #24,
not inferred from the earlier benchmark-worker gate.

The [manual walkthrough](../runbooks/chapter-harness-manual-testing.md) covers the recorded chapter
journey and actual accepted-file downloads. Live chapter gateway transport/privacy, model auditions,
human boundary/transcript labels, correction time, held-out language/source accuracy and attributable
actual costs remain separate, explicitly unqualified work. No result here claims zero bugs, universal
speech completeness or a production cost winner.
