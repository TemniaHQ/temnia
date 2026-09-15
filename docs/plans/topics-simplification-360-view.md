# Topics pipeline: simplify to production, frontier models first

Date: 2026-09-15. Author: Fable, for Rajesh's decision. Supersedes the vendor and ledger
sections of [topics-production-360-view.md](topics-production-360-view.md); keeps its window,
decision and admission design.

## 0. What Rajesh asked and what this answers

Simplify the pipeline as far as it goes, make it production ready, and make it scale: no
recording length the harness refuses, and no length at which it starts inventing. Build the
source index, the mapping and the loop the way production harnesses do. Frontier models first;
cheap models later, once the pipeline is proven. OpenRouter can go if it is what drags us away
from production; a better gateway is acceptable if one exists without the same failures.

The answer in one paragraph: keep Temporal and PydanticAI, keep the `standalone-topics/8`
shape (index, inline windows per decision, claim admission, complete-with-gaps), and delete
everything that exists to serve either the model audition or OpenRouter's billing. Call the
frontier vendors directly through PydanticAI's own adapters, price from a table with usage
read from the response, pace from the vendors' rate-limit headers, and cache the shared
source window. Add a hierarchical author for very long sources and a faithfulness evaluation
on held-out recordings. The harness shrinks by roughly half and loses every stop that is not
money, an invalid source, a vendor refusing on every route, or cancellation.

## 1. Why the runs failed, in the order that matters

1. **The route pool.** Three open-weight routes through OpenRouter (Kimi K3 at Fireworks, Gemini
   3.8 Flash at Vertex, DeepSeek V4 Pro at Fireworks) were chosen by the vendor rules of
   2026-09-07 (no default vendor, open weight first-class, zero data retention, strict JSON
   schema). Those rules excluded the models that qualified in the 2026-08-26 audition (Opus kept
   both clip passes; GPT-5.6 Terra was the standing challenger; Gemini, Qwen, GLM and MiniMax
   did not qualify) and routed every call through two hops whose capacity is shared with every
   other OpenRouter customer. OpenRouter's own documentation says the 429s on paid models are
   the upstream provider's, not OpenRouter's; that is exactly the point: the quota is not ours.
2. **The gateway's billing model.** OpenRouter reports the exact charge only through a second
   lookup, minutes later. That one fact is why the ledger has receipts, reconciliation,
   unknown-outcome fences, a CLI, and the reaper sweep from #52. A direct vendor returns usage
   in the response and publishes a price table: cost is known when the call ends.
3. **The ledger's audit rules in production.** "Never replay an unknown request" and
   "reserve the worst case before dispatch" were right for the audition, where a replayed call
   would corrupt a cost record. In a product run they turn an eight-cent dropped stream into a
   stopped run. Claude Code, Codex and every video product retry at the transport, hold the
   money aside, count usage, and never stop for accounting.

The frameworks are not on this list. Temporal gives durable fan-out, retries, heartbeats and
resume, which is the shape a long editorial run needs. PydanticAI gives typed outputs and
already ships direct adapters for Anthropic, OpenAI, Google and the others. LangGraph and
Google ADK would arrive with the same providers and the same ledger; their checkpointing is a
weaker copy of what Temporal does, and they are built for chat agents with tools, not for a
batch of bounded editorial decisions over windows.

## 2. Decisions for Rajesh

| # | Decision | Recommendation | Why |
| --- | --- | --- | --- |
| D1 | Provider path | **Direct vendor APIs** through PydanticAI's `AnthropicModel`, `OpenAIResponsesModel`, `GoogleModel`. No gateway on the primary path. | Usage in the response, our own quota, official retries, prompt caching, structured outputs, zero-data-retention on API terms. Three keys instead of one is the whole cost. |
| D1b | If one key must stay | **Vercel AI Gateway with BYOK** for the open-weight lane only. | Zero markup on tokens and on BYOK, usage returned per response, per-request ZDR routing. The harness already speaks it. Not OpenRouter: its 429s are upstream quota we cannot see, and its charge needs a lookup. |
| D2 | Seats, frontier first | Author and repair: **Claude Opus 5**. Reviewer (cold, local, omission, pairs): **GPT-5.6 Terra**, family independent of the author. Inventory: **Claude Sonnet 5**. Fallbacks in each pool: GPT-5.6 Sol for the author, Gemini 3.8 Flash direct for review. | Straight from the audition evidence: Opus was the only model top-tier in both passes; Terra had the best drop taxonomy and unique finds at a tenth of the price; Sonnet is the competent high-volume workhorse. Gemini's earlier DNQ was a schema issue since cured. |
| D3 | Ledger | **Allowance and usage, not audit.** Reserve from the price table at the seat's typical answer, settle from response usage, retry with the reservation held, no unknown-outcome fence, exact reconciliation only where a vendor offers a usage endpoint, as a report. | Removes the whole stop class from the last two runs and about a third of the harness runtime. |
| D4 | Deletion | Delete V3 to V7, qualification, experiment, cassette qualification paths, OpenRouter transport, receipts, reaper reconciliation. | §5 lists the files and the line counts. About half the harness. |
| D5 | Scale | Hierarchical author above a section budget, continue-as-new per stage, adaptive pacing from rate-limit headers, prompt cache for the shared window. | §4. These are the four things that separate "works on Karma" from "works on ten hours". |
| D6 | Quality gate | A faithfulness and coverage evaluation on three held-out recordings before cheap models are auditioned again. | The pipeline is proven when it holds on sources it has never seen, not when Karma passes. |

The vendor rule of 2026-09-07 is reversed for the primary tier. It stays as the second phase:
once the pipeline is proven on frontier models, open-weight models are auditioned per seat on
cost-per-correct against the frontier baseline, with the same evaluation.

## 3. The stack after the change

```
Next.js panel ── start / retry / cancel ──► Temporal ──► pipeline worker
                                                          │
   source index (sections, regions, sentences, vectors)   │  one activity per decision
   inline windows per decision ◄──────────────────────────┤  PydanticAI agent, typed output
   claim admission (cited sentences ⊆ window ∪ reads)     │  BudgetedModel: reserve → call → settle from usage
   complete-with-gaps assembly                            │  vendor adapters: Anthropic, OpenAI, Google
   compile → render (Modal GPU) → panel                   │  pacing from rate-limit headers, shared cooldown
                                                          │  Logfire: one trace per run, one span per call
```

What stays exactly as it is: the source index (`source_index.py`), the windows and prompts
(`topic_windows.py`), the decision activity and its ladder (`topic_decisions.py`), the V8
workflow (`topic_windows_workflow.py`), claims admission, the compiler and renderer, the run
row and the panel.

What changes:

- **Routes.** A `RouteEntry` names a vendor (`anthropic`, `openai`, `google`) and a model id;
  `GatewayTransportPolicy` and `provider_accounting_name` go. The model factory builds the
  vendor adapter with the vendor's SDK client and its retry policy off (ours is the retry).
  Eligibility keeps `strict_json_schema`; `zero_data_retention` becomes a statement about the
  account, recorded once, not a per-endpoint flag.
- **Cost.** `RoutePrices` stays (input, output, cache read, cache write). `BudgetedModel`
  reserves `prices × typical tokens for the seat` and settles `prices × usage from the
  response`, including cache reads. No lookups, no receipts.
- **Retries.** Transport errors, 429, 5xx, overloaded and dropped streams: the reservation is
  held, the attempt is retried on the same route after the vendor's `retry-after` or the
  ladder, then on the next route, with the worker-wide cooldown from #51. A retry never waits
  for a receipt. The only ledger fences left are the allowance and cancellation.
- **Pacing.** The worker reads `anthropic-ratelimit-*` and `x-ratelimit-*` response headers
  and sets each route's in-flight gate and pause from the remaining tokens and the reset
  time, instead of a fixed gate. This is what Claude Code does and it is why it rarely 429s.
- **Caching.** The section window and the rubric are marked cacheable (Anthropic
  `cache_control`; OpenAI and Google cache automatically on a stable prefix). Inventory,
  local review and omission calls on the same section then pay a tenth for the shared prefix,
  and cache reads do not count against Anthropic's input-token limit.
- **Tracing.** Logfire's Temporal plugin on the client and PydanticAI instrumentation: one
  trace per run, one span per decision and per model call, with tokens and cost. Rajesh reads
  a run there, not in screenshots.

## 4. Scale: index, map, loop

The V8 shape is already index, map, verify, loop. What is missing for "any length" is bounds
on the two places that grow with the whole source rather than with a section, and a guard on
Temporal history.

- **Index** (unchanged): sections of at most eight regions, regions of at most 32 sentences or
  8,000 characters. A ten-hour recording is about 25 sections and 190 regions.
- **Map** (unchanged): inventory per section, cold review per candidate, omission scan per
  region, relationship pairs. Prompt size is bounded by construction at 128,000 characters and
  asserted on the four-hour fixture. Calls grow linearly with duration; prompt size does not.
- **Author, hierarchical.** Today one author sees the whole inventory. Above a budget of 48
  opportunities or 12 sections, the planner groups sections into runs of at most 12, authors
  each group with its own window and inventory, then a merge author sees only the candidates
  (not the source) and resolves overlaps and hand-offs across group edges with `read_source`
  for the edge sentences. Depth is at most two for anything under twenty hours; the merge
  step is the same call at every level. Group edges are ownership, never a cut, exactly as
  section edges are today.
- **Review, unchanged in shape.** Cold review, omission scan and pairs are already per
  candidate, per region and per pair. Local review is per section.
- **Repair, bounded.** At most three repair iterations, each over the findings' evidence with
  eight sentences of context, as today.
- **Loop control.** Each stage's decisions fan out three at a time (raised per route from the
  pacing headers). The workflow holds only plans, references and gaps, and continues-as-new
  after every stage, so history stays under a few thousand events at any length.
- **No hallucination by construction.** The model chooses sentence ids; timestamps and
  boundaries come from the evidence, never from the model. A cited sentence outside the
  window and the reads is a rejected answer, corrected once, then a recorded gap. That rule
  is the anti-hallucination mechanism and it does not weaken with length because every
  decision sees only its window. What §6 adds is the measurement: a claim-level faithfulness
  score and a coverage score on recordings the prompts were never tuned on.

Anthropic's Citations feature would ground the author's summaries to character ranges
natively; it is an optimisation to audition after the baseline, not a dependency, because the
claim admission already enforces the same property on sentence ids.

## 5. Deletion list

Line counts from the worktree today; the harness is 35,214 lines.

| Delete | Lines | Reason |
| --- | --- | --- |
| `topic_selection_workflow.py`, `topic_selection_activities.py`, `topic_selection.py`, `topic_source_review.py`, `topic_review.py`, `topic_editorial.py` (V3 to V7 parts), `topic_patch*.py`, `topic_experiment.py` | ~10,500 | Superseded programs and the experiment driver. Their recorded fixtures go with them. |
| `qualification.py`, `qualification_topic_selection.py`, `qualification_fixture.py`, `scripts/qualify_*` | ~2,600 | Audition-era route qualification against OpenRouter endpoints. |
| `gateway.py` (OpenRouter and Vercel transports, receipts), `gateway_policy.py`, `receipts.py`, `cli.py` reconciliation, `reconcile_*` activities, the reaper sweep | ~1,700 | Replaced by vendor adapters and usage-from-response. |
| `ledger.py` fences and receipt paths (`outcome_unknown` states, `reconcile_cost`, `find_recoverable_attempt`'s unknown branches, `cancel_requested` reconciliation) | ~700 of 1,629 | The audit rules. Reservation, settle, allowance and cancellation stay. |
| `cassettes.py` synthetic paths used only by qualification | ~300 | The recorded fixture for the browser journey stays. |
| Web: V3 to V7 run listing, experiment pages, qualification readouts | ~1,500 | One program, one panel. |
| Docs: design records for V3 to V7 stay as history; the runbook and AGENTS rules for them go | | |

Kept and simplified: `models.py` (BudgetedModel without receipts, about half its size),
`runs.py` (no unknown-outcome status), `activities.py`, `artifacts.py`, `evidence.py`,
`source_index*.py`, `topic_windows*.py`, `topic_decisions.py`, `topic_compiler.py`,
`topic_render.py`, `topic_repair.py`, `topic_inventory.py`, `topic_author_packaging.py`,
`rendering.py`, `compiler.py`, `evals/`.

Net: roughly 17,000 lines removed, 1,500 added. The migration keeps the run table: existing
rows in `outcome_unknown` are moved to `failed` with the reconciled message by the migration.

## 6. 360 view

- **Edges.** A source under one section (a five-minute clip): one inventory call, the author
  sees everything, no merge. A source over twenty hours: two merge levels; the planner refuses
  nothing and the projection says the cost. A section with no words: recorded gap. A vendor
  with no eligible route for a seat: typed stop naming the seat. A run whose author and
  reviewer are the same family: refused in the panel before any row exists, as today.
- **Scale.** Ten hours: about 25 inventory calls, 3 group authors and 1 merge, ~60 cold
  reviews, ~190 omission scans, ~30 pairs, 3 repairs: ~320 calls. At the seat prices in D2
  and the V8 prompt sizes, with caching on the shared prefix: about $60 to $90. Four hours:
  $25 to $40. Karma: $4 to $7. These are the projection's inputs; the run row shows the exact
  figure before spend. Cheap-model phase target after proof: a third of these.
- **Failure.** Transport failure: retry, then next route, then a typed stop naming both routes
  and the vendor's status. Allowance: pause, raise, retry, as today. Worker death: Temporal
  retries the activity; settled responses are reused by request hash; a reservation with no
  usage settles at the estimate on retry and is corrected if the vendor's usage endpoint
  reports otherwise. Vendor outage: the fallback route in the pool; both down is the stop.
- **UX states.** Unchanged panel: allowance, author and reviewer selects now listing the
  frontier pools, projection line, running with calls and spend, `needs_review` with videos and
  gaps, `budget_paused` with raise, `failed` with the typed sentence and Retry, `cancelled`.
  `outcome_unknown` disappears from the status enum.
- **Ops.** Three vendor keys on the pipeline service, set once in Dokploy. Logfire token on
  both services. Rate-limit tier: Anthropic Build tier or above for the author seat (Tier 1's
  20,000 input tokens per minute would throttle three 12,000-token prompts); the spend to
  reach it is a few days of staging runs, and cache reads do not count. Deploy on merge as
  today; the route snapshot file names the vendor models and prices and is validated at boot.
- **Tenancy.** Unchanged: every row scoped by organisation, keys are the platform's, spend is
  metered per run and per organisation on the run row.
- **Tests.** The existing decision, window and workflow suites keep passing with the vendor
  adapters faked at the model layer (PydanticAI's `FunctionModel`, as today). New: usage
  settlement from a response, pacing from headers, hierarchical author on a synthetic
  40-section source, continue-as-new after each stage, the faithfulness scorer on the Karma
  fixture. The gate stays exact-commit with both images and the browser journey.
- **Evaluation (D6).** Three held-out recordings Rajesh picks (one under an hour, one two-hour,
  one four-hour or longer). For each run: claim faithfulness (every cited sentence in its
  window, already enforced, reported as a rate), coverage (fraction of regions with at least
  one admitted opportunity), duplicate rate across group edges, and Rajesh's editorial verdict
  per video on a fixed rubric. The pipeline is production ready when all three runs finish
  without a code-caused stop and the editorial verdict is acceptable on the long two.
- **Legacy.** Nothing inherited: the seat choices come from the audition evidence and are
  re-auditioned in the cheap-model phase; the vendor rule is recorded as reversed in AGENTS.md
  with the reason.

## 7. Delivery order

Each step is one PR, gate green, deploys on merge, and leaves staging runnable.

1. **Vendors and usage** (2 days). Direct adapters, price table, settle from usage, retries
   with the reservation held, pacing from headers, cache markers, Logfire. Route snapshot with
   the D2 seats. The unknown-outcome fence is removed from the V8 path. Karma runs end to end
   on frontier models. This is the run Rajesh wants to see first.
2. **Deletion** (1 day). Everything in §5. Migration for the status enum. Runbook and AGENTS
   rewritten for one program.
3. **Scale** (2 days). Hierarchical author, continue-as-new per stage, the synthetic
   40-section test. Two-hour and four-hour staging runs.
4. **Evaluation** (1 day plus the runs). Faithfulness and coverage scorers in `evals/`, the
   three held-out runs, the verdict recorded. Then the cheap-model audition opens against this
   baseline.

## 8. Sources consulted on 2026-09-15

- OpenRouter: paid-model 429s originate upstream; rate limits inherited from providers
  (OpenRouter help centre; Requesty and NanoGPT write-ups).
- Vercel AI Gateway: zero markup on tokens and BYOK, per-request ZDR free, team ZDR $0.10 per
  1,000 successful requests charged only on responses that return usage (Vercel docs).
- Anthropic: Opus 5 $5/$25, Sonnet 5 $2/$10 per million tokens; cache reads at a tenth; API
  rate limits per organisation by tier (Start, Build, Scale), token bucket, cache reads exempt
  from input limits; ZDR by approval; Citations API generally available.
- OpenAI: GPT-5.6 Sol $5/$30, Terra $2/$12, Luna $0.20/$1.20; 1.05M context; structured
  outputs; ZDR for the API by approval.
- Google: Gemini 3.8 Flash $0.75/$3.75, 1M context, no retention on Vertex.
- PydanticAI 2.40 ships `anthropic`, `openai`, `google` adapters (the `anthropic` and
  `google` extras are not yet installed in the worktree); the Logfire plugin instruments
  Temporal and PydanticAI together.
