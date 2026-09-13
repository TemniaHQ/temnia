# Harness robustness: every run finishes or says exactly why

2026-09-13. Three staging runs in one afternoon: one succeeded, one died on an HTTP 402, one
died on an HTTP 429 after $0.38 of good work. Rajesh: "With such inconsistency how can we even
launch our product to the public?" This is the audit of the run path from the button to the
rendered videos, what each finding cost, and the design that replaces it.

## Findings

| # | Where | What the code did | What it cost |
| --- | --- | --- | --- |
| 1 | `models.py` HTTP branch | Every 4xx except 408 was "conclusive": the run ended. 5xx before any response was "unknown": the run parked for a human. | The 429 run: $0.38 discarded, no retry, wrong advice ("change the route snapshot"). |
| 2 | `editorial_routes` | Each seat always used the first route of its pool. The two other qualified routes in every pool were never used. | A single throttled provider ends the run although two alternatives are configured. |
| 3 | `run_seat` | Fixed 30 s / 90 s backoff, same route only; the provider's Retry-After was read by nobody. | Retries into the same rate limit. |
| 4 | worker | No admission control per route: any number of runs could dispatch to one provider at once. | Rate limits are provoked by our own concurrency the moment two sources run. |
| 5 | `review_selection` | Cold reviews sequential, eleven calls one after another. | Ten minutes of wall clock for work that is independent per candidate. |
| 6 | `TopicRunWorkflow.run` | An unconfirmed outcome parked the run until a human ran `reconcile-cost` on the VPS. | A deploy or a lost stream during World Order means a CLI session before anything moves. |
| 7 | web | No way to resume a stopped run; the DB already supported it for `pending`. | Every failure means a new run and paying again for the inventory and author calls. |
| 8 | web | A deploy under an open tab surfaces a Next.js internals message; a failed run kept "dispatched, waiting" text and a stale reservation. | Rajesh read "What the hell went wrong with the new deployment?" from a page that was merely old. |

Not a finding: model activities retry at most once at the Temporal level on purpose; the
workflow owns retries because every retry is a paid decision with ledger consequences.

## Design

**Classify by what the provider did, not by the status range.** A status before any response
means no generation exists: 408, 425, 429 and every 5xx are `TransientProviderFailure`
with a known zero cost (reservation released), the same class as a lost stream whose receipt
settled. 400, 401, 403, 404, 422 remain conclusive. 402 is conclusive and names the account.
The Retry-After header, when present, rides on the failure sentence.

**Fall back through the seat pool.** `SelectionContext` carries `author_index` and
`verifier_index`. A seat call is retried once on its route after a pause (the fixed 20 s or
the provider's Retry-After, whichever is longer), then the seat moves to the next qualified
route in its pool and stays there for the rest of the run. The reviewer pool is filtered by
the author's family at every position, so independence holds after a fallback. When every
route has failed, the run ends with all of them named and the last failure quoted.

**Admit per route in the worker.** `ModelRuntime.route_gate(route_id)` bounds in-flight
calls per route (`limits.maxInFlightPerRoute`, staging 2) and spaces dispatches
(`limits.minDispatchIntervalSeconds`, staging 1). The gate is taken before the dispatch is
committed, so a queued request is never counted as sent. It is per process because the
provider throttles the account, not the run.

**Fan out the independent work.** Cold reviews run concurrently, bounded by
`COLD_REVIEW_FAN_OUT` in the workflow and by the route gate in the worker. Results are
processed in candidate order, so the assessment is deterministic.

**Reconcile automatically.** When a run stops on an unconfirmed outcome, the workflow's
failure path asks the gateway for the receipts of every unknown attempt and settles them; if
none remains unknown the run becomes `failed` with a message that says it is retryable.
A pending receipt keeps the fence, as before.

**Resume, don't restart.** A new Temporal execution may take over a run in `pending`,
`failed` or `budget_paused`; `outcome_unknown`, `cancelled` and `ready` refuse. The web's
**Retry this run** starts `TopicSelectionWorkflow` under `topic-selection/<run>/retry-<uuid>`
with the run's frozen intent; the worker replays settled model responses by request
identity and pays only for what never completed.

**Say the right thing.** A stale server action after a deploy reads "Temnia was updated
while this page was open. Reload the page"; the panel already polls every five seconds.

## What stays open

- Repair per finding group in parallel (Rajesh's question of 13 September) is the next
  program change and builds on the same seat runner.
- Retry-After is honoured as a delay; a provider that answers with a date is treated as
  giving no advice.
- The gate is per worker process. A second worker doubles the admitted rate; the limit is a
  deployment fact to revisit when a second worker exists.
