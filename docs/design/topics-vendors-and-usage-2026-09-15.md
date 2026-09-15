# Topics PR 1: vendors and usage

Date: 2026-09-15. Author: Fable. Implements step 1 of
[topics-simplification-360-view.md](../plans/topics-simplification-360-view.md) (D1, D2, D3 and
the D5 pacing and caching items). Steps 2 to 4 (deletion, scale, evaluation) follow in their own
PRs; nothing here is deleted that step 2 owns.

## What changes

The `standalone-topics/8` decision path calls the frontier vendors directly through PydanticAI's
own adapters, prices every call from the route's price table with the usage the vendor returns
in the response, retries transport failures with the money held at the estimate, paces from the
vendors' rate-limit headers, caches the shared prompt prefix on Anthropic, and traces to Logfire
when a token is present. The OpenRouter and Vercel transports, receipts, reconciliation and the
unknown-outcome fence remain in the tree for the legacy programs and are removed by step 2; a
vendor route never produces an unknown outcome.

### Routes

`RouteEntry` gains `vendor: anthropic | openai | google | None` and `account: VendorAccountTerms |
None`. A vendor route names the vendor, the vendor's model id in `gateway_model` (the field keeps
its name until step 2 renames the schema), the account's retention terms recorded once, and no
transport, accounting name or accounting model. `eligibility` is required on legacy routes and
absent on vendor routes: strict JSON schema output is a property of the adapter, not of a probe.
`cache_enabled` on a vendor route needs no qualified probe.

A production seat pool now needs two model families, not three, and no open-weight candidate: the
2026-09-07 vendor rule is reversed for the primary tier (AGENTS.md, 2026-09-15). Independence is
still enforced where it matters, at selection: the reviewer's family is excluded from the author's.

Seats: `propose` (author and repair), `verify` (cold, local, omission, pairs), and a new optional
`inventory` seat; a snapshot without one inventories with the verify pool, as before, so the
synthetic gate fixtures and the legacy programs are untouched. `summary` is no longer required.

The staging snapshot is `topic-routes-staging-<id>.json` with the D2 seats: Opus 5 then Sol for
`propose`; Terra then Gemini 3.8 Flash for `verify`; Sonnet 5 then Gemini 3.8 Flash for
`inventory`. Prices are the vendors' published rates on 2026-09-15 (Anthropic pricing page,
OpenAI model pages, Gemini API pricing page), in micros per million tokens, including cache read
and cache write where the vendor charges them. Max output is each vendor's documented limit.

### Vendors

`harness/vendors.py` builds the adapter for a route from process-only keys (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`), with the SDK's own retries off, one httpx client per call
with the idle timeout, and an aggregate deadline scaled by payload size exactly as the gateway
transport did. Settings the harness injects: `max_tokens` (the decision's allowance), `thinking`
from the route's `reasoning_effort` (Anthropic adaptive thinking and effort, OpenAI reasoning
effort, Gemini thinking level), `service_tier` where the route names one, and `anthropic_cache`
on Anthropic routes so the server places the cache breakpoint automatically: a decision's second
and later rounds pay a tenth for the prefix, and sibling decisions on the same section share it
when their prompts share a prefix long enough to cache.

Rate-limit headers (`anthropic-ratelimit-*`, `x-ratelimit-*`) are read from every response by an
httpx hook and handed to the route's gate: when the remaining tokens fall under the low-water mark
or the remaining requests reach zero, the gate holds every dispatch on that route until the
vendor's reset time. A 429 or 5xx carries `retry-after` on the exception's headers and the
decision's ladder honours it, as before. Anthropic's spend-cap 429 (no `retry-after`,
`enforced_spend_limit_reached`) is a conclusive rejection, not a transient one.

### Ledger

For a vendor route `BudgetedModel` reserves at the route's prices for the admitted payload and
half the requested output allowance (the typical answer; the full allowance is still checked
against the context window), settles from the response's usage (uncached input, cache reads, cache
writes and output each at their price), and never leaves an attempt in `outcome_unknown`:

- an HTTP error before a response settles at zero, as before;
- a dropped stream, a timeout, a cancellation or an unexpected error after dispatch settles the
  attempt as a known failure at its estimate, with `settlement: estimate` in the usage detail,
  and the decision retries on its ladder;
- a crash-recovered attempt (dispatching or running with no durable response, from a dead
  execution) is abandoned at its estimate by `ledger.abandon_attempt` and a fresh attempt is made,
  rather than fencing the run;
- a successful response without usage (an adapter that could not read it) settles at the
  estimate and says so in the detail.

Legacy gateway routes keep every existing path, including the fence and the receipt wait.

### Configuration

`harness-config/1` accepts `gateway: direct`. With it the worker reads the three vendor keys from
the environment. A missing key does not stop the worker (it also serves ingest and transcription):
boot logs the missing variable, and a run whose seat reaches that vendor stops with a typed
sentence naming the variable. The staging file switches to `direct`; the keys are set once in
Dokploy on the `pipeline` service.

Logfire: `LOGFIRE_TOKEN` on the pipeline service turns on tracing of every PydanticAI call and
Temporal activity (one trace per run). Without the token nothing is exported.

`temnia-harness vendors probe` sends one strict-schema request per route of a snapshot with the
process keys and prints the model id the vendor answered with, the usage, the settled micros and
the rate-limit headers. Rajesh runs it once after setting the keys.

## 360

- **Edges.** A snapshot mixing vendor and gateway routes is refused at load. A vendor route with
  an eligibility probe, a transport or an accounting name is refused. A route whose vendor key is
  absent is skipped by selection like a family exclusion, so the seat's next route is tried and the
  stop names the key only when no route remains. Usage with zero tokens on a success settles at
  the estimate and records it. A `finish_reason` of `length` on a vendor response is the same
  truncation the decision already doubles the allowance for. Cache reads at a route with no cache
  price are charged at the input price.
- **Scale.** Nothing here grows with source length. The pacing gate is per route per worker; three
  decisions in flight per stage at the Build tier's five million input tokens per minute never
  pause; at the Start tier's two million they pause only on the reset the header names.
- **Failure.** Transport: retry on the ladder, then the next route, then a typed stop that names
  both routes; every attempt is settled, nothing is fenced. Vendor outage: the fallback route.
  Both vendors down: the stop. Worker death mid-call: the next execution abandons the stale
  attempt at its estimate and dispatches again; the paid response of the dead execution, if it
  arrives, is discarded (double spend bounded to one call).
- **UX states.** Unchanged panel. The model selects list the vendor pools by model id and vendor.
  A missing key surfaces as a `failed` run with the variable named in the sentence.
- **Ops.** Three keys and, optionally, a Logfire token on `pipeline`, set once. The Anthropic
  account should be at the Build tier before long sources; the probe prints the tier's limits from
  the headers. The vendor accounts' retention terms are recorded in the snapshot.
- **Tenancy.** Unchanged; spend is metered per run and per organisation from the settled usage.
- **Tests.** Route validation, vendor adapter construction and setting injection without network,
  usage settlement arithmetic, header pacing, the BudgetedModel vendor path against the database
  (success from usage, dropped stream at the estimate with no fence, crash recovery by
  abandonment), the inventory seat, the direct configuration boot, the committed staging file.
- **Legacy.** The legacy gateway path is exercised by its existing tests and is deleted in step 2.
