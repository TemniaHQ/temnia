# Compact proposal qualification — September 10, 2026

PR #29 merged as `0f6d43cffe37350d2d4f30effcc1072c5da6bc32`. Both staging services
were verified on that revision before new inference. The first compact transport batch
passed for DeepSeek/DeepInfra and Kimi/Alibaba and failed for Qwen/Alibaba. A separately
bounded replacement batch passed for GLM/Baseten. The new three-family snapshot was
rolled out to both services, and one separately budgeted long-source run was admitted
through the authenticated staging browser. It stopped at the fifteenth summary because
the returned ranges did not exactly cover that source window. The compact global proposal
and rendering are still unqualified on this recording.

The [360-degree plan](../plans/chapter-compact-staging-360-view.md) records admission,
failure handling, replacement qualification, configuration rollout and source testing.
Times below are UTC; the working date is September 10 in Asia/Kolkata.

## First compact batch

The isolated container used the exact deployed Python image, with the matching merged
operator CLI mounted read-only. Its source inputs were authored synthetic evidence.
No shared worker code was patched. The credential stayed inside server-side process
environments. Native strict JSON, `store=false`, per-request ZDR, exact provider pinning,
disabled SDK retries and the 8,192-token output limit remained in force.

From 18:43:10 to 18:47:26 UTC, seven calls were dispatched. The two remaining Qwen
calls were skipped after its first failure. All seven responses were saved before
validation, and every generation had a matching reported non-BYOK charge. The batch
returned failure because one candidate failed; it was not a nine-call pass.

| Route | Summary | Compact proposal | Verifier | Reported micros, rounded per call |
| --- | --- | --- | --- | ---: |
| DeepSeek-V4-Flash / DeepInfra | Pass | Pass | Pass | 148 |
| Qwen3.8-27B / Alibaba | Fail | Skipped | Skipped | 4,248 |
| Kimi K3 / Alibaba | Pass | Pass | Pass | 33,963 |

Total reported charges were **38,359 micros ($0.038359)**. Conservative admission was
509,157 micros against the 800,000-micro qualifier envelope. No call in this batch has an
unknown result or unresolved charge. The old source run's separate unknown attempt remains
unchanged at 20,236 micros reported and 109,644 reserved.

## Qwen failure

The response ended normally, with a one-element JSON array in its text part. That element
was structurally identical to the requested `HierarchicalSummaryV1` JSON Schema, whose
canonical SHA-256 is `80fab9c00519001b33de511e8f390a5d9e66ea69d1ee3a76e98a9fa012f0b926`.
It was a schema description, not summary data. Strict validation correctly refused it.

A network-free reconstruction through the pinned SDK produced the same schema hash.
DeepSeek and Kimi received the same summary message bytes and schema with the same
controls and passed. The earlier Qwen success used a different prompt identity and
cannot qualify the current three-stage path.

This identifies a failure of the exact Qwen/Alibaba route. The retained normalized SDK
response cannot distinguish the model, Alibaba's adapter or Vercel's adapter as its cause.
It does not establish a failure rate or prove the skipped proposal/verifier stages fail.
No paid retry, parser weakening, schema repair or promotion from the older proof followed.

## Replacement batch

GLM-5.3-Flash/Baseten was selected for one bounded replacement probe after comparing current
provider metadata, context capacity and price. Its listed one-million-token context provides
room for the retained long prompt under the harness's conservative byte accounting.
[Z.ai's owner record](https://autoclaw.z.ai/blog/model/glm-5.3-flash/) identifies its weights
and MIT license. This is a provisional experiment route, not an audition winner.

GPT-OSS-120B/Baseten was considered before the long-context check. Its
[official model documentation](https://developers.openai.com/api/docs/models/gpt-oss-120b)
lists 131,072 context, which is below the old repair's 170,136 estimated input tokens under
the current byte accounting, before reserving output. No GPT-OSS inference was purchased.
This is a limitation of Temnia's conservative admission for this source, not a measured
tokenizer result or a general claim that GPT-OSS cannot handle the underlying transcript.

The exact replacement requests reserved 17,448 micros: summary 5,787, compact proposal
5,936 and verifier 5,725. They ran on the same deployed image with unchanged controls and
passed all three stages. Each saved generation matched GLM/Baseten and reported its cost;
no cache tokens were reported. The batch completed at 19:03:58 UTC with charges of
123, 136 and 132 micros respectively: **391 micros total**.

Across both compact batches, ten dispatches reported **38,750 micros ($0.038750)**.
Cumulative conservative admission was **526,605 micros**, within the original 800,000-micro
qualifier envelope. The replacement was not a retry of Qwen, and neither successful route
from the first batch was called again. No unknown execution or cost was introduced.

The replacement journal/report SHA-256 is
`05bf90ddaac5829f6d30663011ffe68d38f9191901c2875e823d299ea606a456`.
Its candidate file binds the fresh gateway endpoint response
`93df6e2e27b0024e8eee88cde24370890f4e2cf0e2f5faf1134161d911dc5d3f`.
The owned container was removed only after terminal evidence capture.

## Evidence retained

All seven response SHA-256 values and byte counts matched the journal and terminal
container manifest in an independent offline review. Proofs for the two passed routes
bind the compact schema and prompt, request hashes, response hashes and sizes, exact
generation identity, usage and cost facts. They do not establish editorial quality,
maximum-context behavior, provider-internal retention or a model winner.

| Artifact | SHA-256 |
| --- | --- |
| First compact journal/report | `059dcf315235614da7ddb6bc1957c204bf7e822fc3c2f791bdbe8b16c16614f7` |
| DeepSeek compact route proof | `8130155465e893f5e988784b3aa2c9a69259d5883a34677e9f694595c42f4ae6` |
| Kimi/Alibaba compact route proof | `c4d5056657e084ae2b106e01d6f72fdf46b42caaa72b067d98553c83b49b638b` |
| Qwen failed response | `89afe7536143395f7ff7dcfad8764416b9272c73da3ed763f42ed18a84512dad` |
| Offline proof review | `b2079baf5552680bf611c9cf2bc4248efc1105a28bfb213addfdd36bf686d8ec` |

Raw receipts and manifests remain private. The owned qualifier container was removed after
terminal evidence capture. Existing source artifacts, the old route snapshot and the old run
were preserved.

## Browser check

After refreshing the existing authenticated browser, the merged chapter panel explained
the unknown provider result and retained charge. Retry, Cancel and Raise budget were
disabled. New run displayed the separate paid-work explanation; cancelling that form
returned to the old run without submitting anything. This verifies those visible states,
not chapter listening, editorial acceptance or export.

## Configuration rollout and source admission

Snapshot `e35aa3c9556f6b1e48923c895ef0bce98f831701eae729d07f727b8e07dcb01b`
passed production validation and an independent proof review. Its file SHA-256 is
`110ec4bfa35dd92cc4ff8f5ad698a1eb0f327cd2c73610f2719da98ccaae6d35`.
Summary/proposal order is DeepSeek/DeepInfra, GLM/Baseten, Kimi/Alibaba; verifier order
is GLM/Baseten, Kimi/Alibaba, DeepSeek/DeepInfra, subject to family exclusion. Caching
remains disabled. This is a provisional staging route, not a model audition result.

The configuration rollout completed at **19:25:32 UTC**. Both services retained their
exact PR #29 images and all existing caps. Actual Swarm and container environments
matched; the worker booted, validated the route and could not write its root-owned file.
The web emitted its migration/seed marker and returned health 200. The gateway key
remains worker-only. The old route file and bind mount were preserved. Scoped ledger
and Temporal checks found no competing work and no change to the old unknown run.
The private rollout receipt SHA-256 is
`f2b9065ab14cb2e05cbe51eec9de8454cc242e0a4668538ff17d1facfdf8cb80`.

At **19:26:18 UTC**, one browser submission created run
`a0f1913d-31ff-46b5-b470-7c4458f71b18` for the same 151-minute source, using the original
editorial brief and a **2,000,000-micro ($2) cap**. The panel acknowledged the durable run
and showed the previous unknown run separately. Its initial evidence activity was observed
with download progress; no second submission was made. A bounded read-only observer owns
the follow-through and retained the final state without retrying or cancelling it.

A separate scoped admission check verified the exact brief, complete saved configuration,
budget, route content and source/transcript revision. The old transcript row's metadata has
no content hash, so the run's optional creation-time transcript SHA remains null. Its attached
immutable evidence artifact independently records the actual transcript SHA
`c4222ccd028e5be24270c8411baaba589000bf997578161b322b674c8e1b51be`, matching the retained
input before model work. The evidence artifact is the same accepted source-scoped artifact
as before. This verifies the input used without claiming that the original run envelope
contains the missing digest. The consolidated verification receipt SHA-256 is
`87928fa2872f30be7f5ab4bbb85da6a5fcbf3c62ada65b939005b18fbb187a72`.

## Performance limits identified during the run

The new run downloaded the 1.9 GB master again. Code review confirms that verified local
source paths, leases and expiration are keyed by run ID. A distinct run therefore repeats
the source download/hash, timeline probe, transcript read/hash and segmentation before
immutable evidence deduplication. The final artifact identity and storage accounting are
shared when the bytes match; this does not currently reuse the producer work. The existing
cache regression covers repeated access within one run. The configuration reload itself
does not require the transfer because the work volume persists.

Summary requests are sequential. This test measures the current implementation; bounded
parallel summary execution and safe reuse of source evidence are separate optimization
candidates. Neither changes the need to validate each response, reserve concurrent costs
atomically, preserve source identity, and retain partial work after failure.

Source download I/O has a 180-second quiet bound with progress and a two-second heartbeat
supervisor. The transcript read does not have that same quiet bound; its outer activity
timeout remains six hours. Segmentation runs in a thread, so cancelling the coroutine
cannot forcibly stop already-running native work. These are code-review limitations;
neither was observed to stall this staging run.

## Long-source result and next fix

The workflow completed at **19:41:15 UTC**, in `needs_review` with revision zero, after
15 summary calls. Every call had a retained response and reported charge: **9,397 micros
($0.009397)** total, no active reservation, no repair and no unknown outcome. The first 14
grounding reports contain 119 normalized passages: 112 first-pass reference-valid passages
and seven source-excerpt fallbacks correcting nine misplaced anchors. The fifteenth response
ended normally with 1,269 output tokens; the failure was interval coverage, not truncation.

The five ordered ranges cover 76 of the window's 80 sentences. They omit `s001152` and
`s001183` through `s001185`; there are no foreign endpoints, hidden quote anchors, reversed
ranges or overlaps. The complete original window is 4,756 characters, within the existing
20,000-character summary-unit bound. Its exact joined-text SHA-256 is
`a25a7f48abbbb640a4077365f263011ace1c1e9f85f70c035eacbca923ebff23`.

The observer retained the completed 217-event history, whose canonical SHA-256 is
`5cd67b3f7828ed2fb6f2c05d648dd1c6cb58bd1e503fd688b3cd55e4f65ba158`.
The production evaluation exporter verified the scoped artifacts and lineage. Its content-free
analysis SHA-256 is `930953b5bf47b81ce02228d6a6e2a3407efc4f79151ba17dcf437b38ee841440`.
All original-run operation/attempt tuples remained unchanged. Qualification and this new
source run together reported **48,147 micros**; the old source run's separate exposure remains.

No global proposal, chapter edit, render descriptor or independent editorial verdict was
produced. Full coverage, boundary quality, review, export and cost per accepted chapter are
unmeasured. The UI correctly displayed the coverage refusal, but only offered starting a new
run: Retry was disabled for planning-only `needs_review`. This prevents reuse of known paid
work after a deterministic recovery improvement.

The [summary coverage recovery plan](../plans/chapter-summary-coverage-recovery-360-view.md)
adds a versioned complete-source fallback for eligible first-level coverage errors and a
guarded same-run Retry at revision zero. Existing valid reports and all 15 paid responses
remain immutable. A new verified PR and staging deployment are required before continuation;
the stopped run has not been mutated to bypass its current controls.

## Recovery evidence before deployment

The replacement policy was applied offline to the exact retained failing response and accepted
evidence. It preserved all 80 source sentences as one 4,756-byte unit, with immutable endpoint
anchors and a diagnostic recording five original units, 76 covered sentences and four gaps.
No provider call was made. The content-free proof SHA-256 is
`b18379bb6e3407ae990279d4bb70449c2c503caf81e4d8e3aeff6f7de3255919`.

A single scoped read captured the 14 accepted report bodies and 15 raw responses without
changing staging. All 14 v1 reports round-trip through the new typed reader with their exact
bytes, sizes, hashes, fingerprints and metadata shape preserved. Recomputing each report from
its actual response, evidence and exact prompt also reproduced its normalized summary and
quote-fallback records. The content-free compatibility and recomputation proof hashes are
`ea5d9abe2d3880e24985a252edb8ee556f91f4c1e9a92461a49e96318b446856` and
`361018c395150ee8b122ed56e50ac0b6ce07e6e238e567b7d483c5f0c2f2cbd1`.
Raw source prose and responses remain private and are not repository fixtures.

The production Temporal replayer accepted the retained 77-, 191-, 361- and 217-event histories
with the recovery code. The workflow scheduling and model request shapes are unchanged;
the fix changes validation and admission for a later retry. These results establish recovery
compatibility, not completion of the remaining summaries, chapter rendering or editorial review.

The real Postgres/Temporal persistence regression uses synthetic source content and 15 saved
model responses. After a production planning-refusal transition and same-run Retry, the model
adapter reuses all 15 responses while a provider-dispatch trap observes zero calls. The first
14 report tuples and all attempt/reservation tuples, including distinct nonzero known charges,
remain unchanged. Only the failed window receives a new v2 report, and global proposal
preparation accepts the resulting mixed-policy lineage. This regression is part of the full gate.
