# Topics pipeline to production: bounded windows, one activity per decision, no self-inflicted stops

15 September 2026. Implemented the same day on `feat/topics-production` (PR #49); §7 records what each step delivered. Written after the review of `feat/indexed-editorial-evidence` at `cd48459`
(Codex's last commit before Rajesh stopped it) and two measurements on the 2,400-sentence
four-hour fixture. Branch: `feat/topics-production`, which starts from that commit and already
carries two corrections: the model-side toolset validator now admits every registered role
(`471cbb5`), and the web is back on `standalone-topics/3` until the new program passes its
physical journey (`9e11f7d`). Prior records this builds on: the
[indexed evidence design](../design/indexed-editorial-evidence-2026-09-14.md), the
[production review](../design/indexed-harness-production-review-2026-09-14.md), the
[recovery batch](../design/indexed-harness-recovery-2026-09-15.md) and Codex's paused
[completion ledger](indexed-harness-production-completion.md).

## 0. Outcome

A user uploads a 44-minute to four-hour recording, presses **Find topic videos**, and the run
ends in one of exactly two ways: reviewable rendered videos with every unresolved finding
attached, or a stop whose reason is on a closed list of genuine causes (money, provider,
source, cancellation, human decision). Cost and wall time are projected before the first paid
call and shown in the panel. Retry resumes from admitted work. Editorial quality is measured
separately against human labels; it is not part of this bar.

Two invariants make that concrete, and both get a test:

1. **Every model call is bounded by construction.** No prompt over 64 KB of text, no more than
   eight model rounds per decision, and total tokens for a run grow linearly with duration. A
   limit that can still be hit at runtime is a planning bug.
2. **The pipeline never stops itself.** Every terminal reason belongs to the genuine list in §5.
   Any other exception reaching the run is a test failure.

## 1. Where the branch stands

What Codex's batch got right and this plan keeps: the source index (`topic-source-index/2`),
the ledger's reserve/dispatch/settle path, immutable artifacts with dependency chains, the
gateway toolset policy, reviewer reservation before authoring, editorial progress separate from
render, `needs_review` retry, and the `failure_cause` unwrapping across child boundaries.

What the review measured, on the four-hour fixture, V3 whole-source inventory:

| Quantity | Value |
| --- | --- |
| Checkpoint prompt text the model reads on the last round | 200,780 bytes |
| Of which audit data the model cannot use (`calls`, `observed_sentence_ids`) | 111,806 bytes |
| Serialized request (checkpoint appears twice: prompt text and `metadata`) | 437,594 bytes |
| Sum of prompt text over the 42 rounds of one inventory call | ~4.7 MB |
| The whole transcript, sent once, in the pre-index design | ~0.3 MB |

The root cause is coverage by tool loop: the model browses and reads its way across the source,
every round re-sends the growing checkpoint, and every step is proved by exact path admission.
That produces the cost above, the strict admission that rejects real-model behaviour, the
Temporal history growth Codex answered with child workflows and 32-request continuation, and
most of the stop conditions in §5. The fix is to stop using tools for coverage.

## 2. Design: windows, not loops

The index already partitions a source deterministically: a region is at most 32 sentences or
8,000 characters, a section at most eight regions. A section is therefore at most 64,000
characters, in practice about 25,000 for spoken transcripts (the fixture's sentences average
100 characters, real sentence IDs are seven bytes). That fits inline in one prompt at roughly
7,000 tokens. Every coverage role gets its window inline and answers in one call. Tools remain
only where a decision genuinely needs something outside its window.

| Role | Window in the prompt | Tools | Rounds |
| --- | --- | --- | --- |
| Inventory | One section's sentences, plus the last region of the previous section and the first of the next as marked context | none | 1 |
| Author | One section group's sentences (the sections owning its assigned opportunities), plus the inventory items | `search_source`, `read_source` for a dependency outside the group | ≤ 4 |
| Cold review | The candidate's selected speech | none | 1 |
| Local source review | The section's sentences plus its assigned candidates and opportunities | `read_source` outside the section | ≤ 4 |
| Omission scan | One region's sentences plus every intersecting candidate and opportunity | none | 1 |
| Overlap and handoff | The two candidates' speech plus the shared span | none | 1 |
| Repair | Every finding's evidence spans with eight sentences of context each, plus the affected candidates | `search_source`, `read_source` | ≤ 8 |

Rules that follow:

- **Section boundaries are work ownership, never a cut.** An author may extend a candidate
  across the group edge through `read_source`; the edge sentences are already inline.
- **Candidate length is editorial, not a paging problem.** A candidate over 320 sentences (about
  25 minutes of speech) is a `unfocused_extent` finding raised by the planner before cold review,
  not a checkpoint eviction. The cold prompt is one call.
- **Structured context is inline, not paged.** A local review batch that would exceed 16
  candidates or 48 opportunities is split by the planner into two batches over the same section;
  the sixth tool (`read_editorial_context`) and its cursor format are removed.
- **Checkpoints carry only what the model needs.** For the roles that keep tools, the
  continuation holds the original prompt, retained excerpts, observations and notes. The audit
  (`calls`, observed IDs, segments) lives in the artifact and is never sent to the model or
  serialized into the request twice. The request payload used for cost, timeout and the 512 KiB
  cap is the wire payload.
- **Sections should follow the discussion, not a fixed count.** Second-order improvement: cut
  sections at changepoints from the existing substrate embeddings instead of every eight regions,
  bounded by the same 64,000-character ceiling. This is quality work and not required for §0.

Projected work for a four-hour source (10 sections, 75 regions, about 20 candidates):

| Stage | Calls | Prompt per call | Route | Estimated cost |
| --- | --- | --- | --- | --- |
| Inventory | 10 | ~30 KB | verify seat (Gemini Flash) | $0.10 |
| Author | ≤ 10 | ~40 KB + tool reads | propose seat (Kimi K3) | $3–5 with high reasoning |
| Cold review | ~20 | ~10 KB | verify seat | $0.10 |
| Source review | ~5 local + 75 omission + ~10 pairs | ~30 KB / ~10 KB / ~8 KB | verify seat | $0.60 |
| Repair, per iteration | ≤ 5 | ~40 KB | propose seat | $2–3 |
| Whole run, three repairs | ~140 | | | $12–18 |

Against today's measured 4.7 MB for one V3 inventory call alone, this is a 20 to 30 times
reduction in tokens for the same coverage. The estimate is verified in §7; the plan does not
depend on the exact figures, only on the linear shape.

## 3. Execution model: one decision, one activity

Replace per-round model activities with one activity per decision:

- The activity builds the prompt, runs the agent (`agent.run`, with the same `BudgetedModel`
  reserve/dispatch/settle path inside each model request), persists the checkpoint chain to the
  artifact store between rounds exactly as today, and returns one shard reference or one typed
  rejection. It heartbeats every round. Timeout is derived from the projected rounds and the
  route's transport deadline.
- The parent workflow holds only plans, shard references, manifests and progress. It
  continues-as-new after each accepted revision. A four-hour run's history is then tens of
  kilobytes per decision, and the child-workflow-per-decision with 32-request continuation,
  `audit_segment` chains and `topic-editorial-work/1` caching are removed.
- Recovery: a retried activity loads the latest persisted checkpoint for its stage and
  continues; a settled model response is reused by request hash as today; an unknown paid outcome
  keeps its fence. Nothing here changes the ledger.
- PydanticAI's Temporal capability already passes through when not inside a workflow, so the
  same agent definitions run inside the activity unchanged. The `PayloadScaledDurability`
  wrapper and `TOPIC_SELECTION_*_AGENTS` registration go away with the per-round activities.

## 4. Admission: claims, not paths

Admission answers one question: does the answer cite only speech that was delivered to the
model, and did the call stay inside its authority?

- Delivered speech is the inline window plus every `read_source` page in the checkpoint chain.
  Cited spans must be a subset. Missing spans are named exactly in the diagnostic.
- Tool arguments are validated after pydantic coercion. The order of navigation, repeated calls,
  a different page size and an unused search are not grounds for rejection.
- Namespace, ownership and schema rules stay as they are; they are claim rules.
- Every manifest is complete-with-gaps. A section whose decision failed twice becomes a
  `coverage_gap` finding on the manifest; the run continues with the admitted shards, the
  reviewer sees the gap, and the panel shows it. Nine good sections are never discarded for one.

## 5. Stops and recovery

Genuine terminal reasons, the only ones allowed: `budget_exhausted` (projection or spend),
`provider_refused` (401, 402, policy, account), `outcome_unknown` (paid request without a
conclusive receipt; settled from the gateway receipt by the worker, then resumed or retryable), `source_invalid` (evidence mismatch, no words),
`cancelled`, and `needs_review` (the normal end: videos or findings waiting for a person).

Everything else is handled, in this order:

| Condition | Handling |
| --- | --- |
| Schema-invalid or ungrounded answer | One correction on the same route carrying the prior checkpoint and the exact diagnostic; a second failure is a `coverage_gap`, never a run stop |
| Deterministic limit (prompt size, rounds, context) | Cannot occur after planning; if it does, it is a planning bug surfaced as `coverage_gap` with the item, and a test failure in CI |
| Transient provider failure | Two attempts on the route with backoff, then the next eligible route; exhaustion of the pool is `provider_refused` with the route list |
| Reviewer pool | Reserved before any author call; a snapshot that cannot provide one refuses at start, before money |
| Repair loop end (three repairs, repeated selection, nothing actionable) | Ends the loop, never the run; the best admitted revision renders with findings attached |
| Route-specific rejection of a valid request (context, parameter) | Next eligible route once; then `coverage_gap` |

The budget is projected from the plan (calls × prompt bytes × route prices plus reserved
output per role) and compared to the run's allowance before the first paid call. The allowance
is derived from source duration (a per-hour figure Rajesh sets), not a fixed $20. Reserved
output tokens are per role (inventory 8k, author 16k, cold 4k, review 8k, repair 16k), not
65,536 for every request; the transport deadline is derived from reserved output, not payload
bytes.

## 6. 360 view

**Edges.** Empty section (no speech): inventory returns an empty shard, valid. Single-sentence
source: one section, one region. A sentence longer than a window (transcripts occasionally
produce one): the index splits it into fragments at build time with stable IDs, as Codex's
fragment reader does, and the window carries the fragments. A candidate spanning three sections:
authored from the group, reviewed by the local batch of its first section, inspected inline.
Two candidates sharing speech: the overlap pair call. Non-Latin transcripts: windows are sized
in bytes, not characters, and the token estimate uses a per-script bytes-per-token table
measured once per route in the pre-flight, replacing the fixed `bytes / 2`.

**Scale.** Ten-hour source: 25 sections, 190 regions, about 350 calls, about $40; the plan still
fits, history still flat. Concurrency: fan-out three per stage as today, `maxInFlightPerRoute`
two; the projection includes wall time from rounds × observed route latency.

**Failure.** Worker killed mid-decision: the activity retries, loads the checkpoint, continues.
Worker killed between stages: the parent replays from history, shard references intact.
Provider 429 storm: backoff then route fallback, then a genuine stop with retained shards.
Budget exhausted at decision 90 of 140: `budget_exhausted` with 89 admitted shards; raising the
allowance and pressing Retry resumes at decision 90. Temporal history over limit: cannot happen
by construction; a test asserts history size on the four-hour fixture with fault injection.

**UX states.** Projected cost and time shown before start with a confirm when the projection
exceeds the allowance. During the run: decisions done over total per stage, spend against
projection, current stage. On stop: the typed reason and the one action that changes it (raise
allowance, fix snapshot, wait for reconciliation, review findings). Coverage gaps appear as
findings in the review panel with the section they cover.

**Ops.** No new secrets. Route snapshot unchanged. Staging config gains `budgetMicrosPerHour`
and drops `maxDispatches`. Deploy from main as today. Old in-flight histories drain on the
current worker build before the per-round activities are removed; the workflow types V4 to V7
are registered until then and deleted with them.

**Tenancy.** Unchanged: every artifact, ledger row and run remains scoped by organization and
source; index reuse stays within scope.

**Tests.** (a) Plan-time fit proof on the four-hour fixture: for every planned decision, prompt
bytes + largest tool page + reserved output is under the route context and under 64 KB of
prompt text. (b) Typed-stop suite: fault-injected runs (worker kill, 429, 5xx, schema-invalid
answer, budget exhaustion, reviewer pool exhaustion) must all end in a §5 reason; anything else
fails. (c) Physical-path suite: every role's real agent through `BudgetedModel.request` with a
synthetic transport, not a monkeypatched agent. (d) Admission replay: the recorded Karma traces
through the new admission with zero false rejections. (e) History-size assertion on a real
Temporal test server. (f) Projection accuracy: projected versus settled cost within 30% on the
three reference runs.

**Legacy checklist.** Delete, not hide, once the new program passes §7: V3 to V6 workflow types,
the whole-source prompts, `read_editorial_context` and its contracts, `audit_segment`,
`TopicEditorialWorkWorkflow`, `topic-editorial-work/1`, inspection formats 1 to 4 (one new
claim-based format replaces them), the qualification suites for V3 to V6. Keep the index,
fragments, ledger, artifacts, gateway policy, reviewer reservation and editorial progress.

## 7. Delivery order

Each step is its own commit series on this branch with the exit criterion met before the next;
one PR, Rajesh merges.

1. **Fit proof and windows for the single-call roles** (inventory, cold, omission, pairs):
   inline windows, one-call prompts, the fit-proof test. Exit: (a) passes on the fixture; the
   four roles pass the physical-path suite.
2. **Author and local review with bounded tools, claim-based admission, gap-tolerant manifests.**
   Exit: (c) and (d) pass; the 80-sentence-candidate and 321-sentence reproductions from the
   review are admitted correctly.
3. **One activity per decision, flat parent, continue-as-new per revision.** Exit: (e) under
   10 MB on the four-hour fixture with worker kills; per-round activities and children removed.
4. **Projection, per-hour allowance, per-role reserved output, typed-stop suite.** Exit: (b)
   and (f) pass; the panel shows projection and typed stops.
5. **Product integration and deletion.** Web starts the new program; Retry resumes at the
   decision level; Playwright covers start, mid-run kill, retry and a budget stop; legacy
   programs deleted. Exit: the exact-commit gate, both images, all browser tests.
6. **Release evidence on real sources.** Karma (44 minutes), a two-hour and a four-hour
   recording on staging with the fault injections of (b), recording cost, wall time, history
   size, admission rejection rate per role and a human boundary verdict against the M1 rubric.
   Exit: every stop typed, cost within projection, and the boundary verdict recorded. Only then
   does the decision entry say production ready, and only for the measured durations.

## 8. Open for Rajesh

- The per-hour allowance (the projection above suggests $4–5 per source hour with Kimi as
  author; Gemini Flash as author would be about a fifth of that, at unmeasured quality).
- Whether the author seat stays on Kimi K3 at high reasoning effort for the reference runs, or
  the run is repeated across the propose pool as an audition.
- The candidate length ceiling that turns into a finding (320 sentences proposed).
