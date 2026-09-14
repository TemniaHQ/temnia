# Indexed harness recovery and bounded review — 2026-09-14

Status: implementation and verification in progress. This is not a production acceptance record.
It follows the [implementation review](indexed-harness-production-review-2026-09-14.md).

## User direction

Rajesh authorized changing the implementation to make technical failures recoverable and to attempt
long recordings rather than refuse them at arbitrary source-size thresholds. This authorizes the
reliability work here. It does not turn unknown paid requests into safe retries or establish that a
model has understood a discussion. Duration should increase work units and total work, not the size
of each model prompt. There is no promise of infinite resources or guaranteed editorial success.

## Evidence coverage does not mean a full-transcript prompt

A review can require reading a candidate's complete selected speech over several bounded tool calls.
Each continuation holds recent excerpts, compact observations and working notes. The durable
checkpoint records which exact sentences reached prior requests. Earlier excerpts can be read again.
The complete episode is not copied into each candidate review. Local source judgments require their
assigned candidates' internal speech; omission and relationship assignments require their own source
windows and cited evidence, not every related candidate's entire speech.

This distinction repairs the previous final-checkpoint paradox: requiring 321 cited sentences while
allowing only 320 retained sentences could never succeed. Cumulative delivered evidence is now valid.
An excerpt fetched and evicted before any model request does not count. A coverage check establishes
delivery, not comprehension, internal topic decomposition or a human publication score.

## Implemented changes

- Checkpoint `/2` retains bounded tool observations, including navigation ranges, descriptors,
  search results, candidate regions and measured media values. It retains at most 8,000 characters
  of assistant working notes and 96 KiB of observations, alongside 320 recent sentences / 128 Ki
  characters. The existing 384 KiB whole-checkpoint bound remains. Notes and observations are
  explicitly untrusted data and hypotheses.
- Inspection `/3` carries cumulative delivered sentence IDs. Admission binds this claim to the
  exact durable checkpoint dependency of the settled response. The access audit and currently
  retained excerpts are distinct. Legacy inspection `/1` and `/2` retain their validation meanings.
- Exact repeated successful tool results are deduplicated in the audit. Re-reading evicted speech
  counts as useful work. Two stalled rounds produce guidance; six yield the work item for recovery.
  Tool argument errors become corrective `ModelRetry` observations. The SDK allows three tool/output
  corrections; the explicit per-invocation request limit is 256 rather than the implicit 50.
- Inventory, author, bounded source review, V7 cold review and bounded repair get up to three
  admission attempts. Each settled rejection is retained, each correction has a separate stage
  identity, and the next prompt includes its diagnostic. Feedback precedes source JSON. Budget and
  dispatch exhaustion do not trigger more paid requests. Unknown outcomes propagate to the existing
  run fence without replay.
- Parallel work drains its dispatched siblings before propagating an exception. Successful shards
  and their financial outcomes can settle rather than being abandoned at the first sibling failure.
- V7 cold prompt `/4` contains the rubric, title, candidate ID and exact selected sentence interval.
  Its sole tool is `read_source`, restricted to that interval. Complete selected-speech delivery and
  source-grounded judgments are admitted in a durable cold-observation activity. Missing reads or
  invalid judgments receive the same correction loop. V7 optional qualification now binds this
  read-only toolset and compact-history processor.
- Cold observations carry the exact reviewer context, including fallback route and correction
  attempt. Revision changes clear the cold cache so changed author provenance cannot retain an
  observation from a now-ineligible family. Review routing excludes every contributing author family.
- Response admission chooses the most recent matching settled response in deterministic artifact
  creation order. Earlier schema-invalid outputs and identical later corrected outputs no longer
  make the final receipt ambiguous. Every match still requires the original run, route, prompt,
  schema, dependency and request/config hashes.
- `maxDispatches` is nullable in the shared contract. Null removes the run-wide call-count ceiling;
  an explicit positive operator ceiling still applies. Budget, reservations and unknown exposure
  remain enforced. Staging configuration uses null. The experiment worker serializes an absent
  ceiling as an empty override, so it does not inherit a parent's arbitrary count.
- Evidence assembly no longer refuses solely at 10,000 sentences or 250,000 words. Transcript/layer
  identity and complete word ownership checks remain. Source data is not truncated.

## Runtime decision

Keep Temporal and PydanticAI. The failures above are in Temnia's evidence projection, admission and
work-item recovery, not evidence of an unavailable framework capability. Current primary guidance
supports replacing history with `ProcessHistory`, distinct tool/output retries, and Temporal-backed
execution. Replacing the SDK would still require these same domain controls and new transport tests.
References: [message history](https://pydantic.dev/docs/ai/core-concepts/message-history/),
[retries](https://pydantic.dev/docs/ai/core-concepts/retries/) and
[Temporal integration](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/).

## Remaining work and release boundaries

This revision must not be called an unlimited-duration production harness. The web still starts V3;
V7 remains the controlled workflow until product routing, retained-run access and retry routing move
together. Already running histories must drain or remain on their original worker build: changing
checkpoint/retry semantics is not a promise of replay compatibility with old in-flight histories.

Still open: adaptive subdivision when source-review context is too dense; oversized connected repair
components; a sentence larger than one read page; semantic reconciliation of section-local inventory;
bounded aggregate workflow history; editorial continuation after a published revision; explicit
candidate-internal decomposition; and real long-source provider, failure-injection and human
editorial measurements. The existing finite checkpoint bounds can still exhaust an unusually large
single assignment. Corrective attempts alone do not solve an assignment that must be subdivided.

Provider receipts, source identity, tool authority, independent review and safe physical cuts remain
necessary. Their failure must produce an actionable retained state, not be silently treated as a
passing video. These are different from arbitrary duration or call-count denials.

## Verification

Focused implementation checks passed before the final added regressions: 97 tests, including source
progress, reviewer tools, workflow correction paths, exact qualification request construction,
receipt admission and a 10,001-sentence evidence assembly. Final added tests exercise cold paging
across 400 sentences with 320 active, bounded cold prompts, model-visible candidate/media observations
and nullable dispatch contracts. Full gate results will be recorded separately after execution.
No live provider call, deployment or human acceptance result is claimed here.
