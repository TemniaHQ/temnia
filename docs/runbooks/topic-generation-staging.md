# Topic generation on staging

How the one staging worker is configured so that **Find topic videos** on
staging.temnia.dev runs the current standalone-topic program. Decision record: the
2026-09-13 entry in `AGENTS.md`; plan:
[topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).
Reload mechanics are in [staging.md](staging.md) §4b.

## What runs

`standalone-topics/3` on `TopicSelectionWorkflowV3`, queue `temnia-pipeline`, the same
worker that ingests and transcribes. The chapter lane no longer exists in the code. No
qualification manifest exists; a route that refuses a request ends the run with a
message naming the route, stage and HTTP status.

## The roster (settled 13 September on technical reliability)

Author and repair: Kimi K3/Fireworks (five completed full-source runs, no transport failure in
that seat); alternate author: DeepSeek V4 Pro 0813/Fireworks (two completed). Inventory, cold
and source review: Gemini 3.8 Flash/Vertex at medium effort (the only reviewer that has finished
this call shape on Karma, five times). Astra is excluded. Gemini's two known failure modes are
handled: an upstream rate limit inside a stream is settled against the receipt and retried
twice, and the output allowance is Gemini's route maximum, 65,536. This is a reliability
selection, not an editorial winner.

## The route snapshot

One immutable file, root-owned `0444`, bind-mounted into the pipeline container at
`/etc/temnia/topic-routes.json`, its ID in `HARNESS_ROUTE_SNAPSHOT_ID` on both `pipeline`
and `web`. Content for the first rollout: the r25 experiment routes (recoverable from
`configuration.routeSnapshot` inside the exported r25 bundle, snapshot `38d30301…`)
without Astra, `gateway-transport/2` through OpenRouter. Seat order: verify pool
Gemini 3.8 Flash/Vertex first, propose pool Kimi K3/Fireworks first then DeepSeek V4 Pro
0813/Fireworks. The roster is provisional; a reorder is a new file with a new ID, never an
edit. Record the ID in the day log.

## Environment

On `pipeline` (secrets stay here):

| Variable | Value |
| --- | --- |
| `HARNESS_ENABLED` | `1` |
| `HARNESS_BACKEND` | `gateway` |
| `HARNESS_GATEWAY` | `openrouter` |
| `OPENROUTER_API_KEY` | the staging key |
| `HARNESS_ROUTE_SNAPSHOT_PATH` | `/etc/temnia/topic-routes.json` |
| `HARNESS_ROUTE_SNAPSHOT_ID` | the file's ID |
| `HARNESS_MAX_OUTPUT_TOKENS` | `65536` |
| `HARNESS_MAX_DISPATCHES` | `64` |
| `HARNESS_MAX_REPAIRS` | `3` |
| `HARNESS_MAX_RUN_BUDGET_MICROS` | `20000000` |
| `HARNESS_MAX_RENDER_CONCURRENCY` | `2` |
| `HARNESS_TOPIC_SHOT_DETECTOR` | `scdet` |
| `HARNESS_TOPIC_SELECTION_*` | removed if present |

On `web` (non-secret only): `HARNESS_ENABLED=1`, `HARNESS_BACKEND=gateway`, the same
`HARNESS_ROUTE_SNAPSHOT_ID`, the same limits (`HARNESS_MAX_OUTPUT_TOKENS`,
`HARNESS_MAX_DISPATCHES`, `HARNESS_MAX_REPAIRS`, `HARNESS_MAX_RUN_BUDGET_MICROS`,
`HARNESS_MAX_RENDER_CONCURRENCY`, `HARNESS_EVIDENCE_WINDOW_SENTENCES`). Remove any
`HARNESS_CHAPTERS_ENABLED` or `HARNESS_CHAPTER_LLAMA_CONFIG_JSON` entries. The run
config the web sends must equal the worker's; a mismatch ends the run with a message
that says so.

## Rollout

1. Nothing in flight on `pipeline`.
2. Install the snapshot file and mount.
3. Set the `pipeline` values, reload, read the boot log: the new snapshot ID, no errors,
   pollers on `temnia-pipeline` and `temnia-pipeline-control` in the Temporal UI.
4. Set the `web` values, reload, `/api/health`; the button is enabled on a ready source.
5. First run: Karma. Expect `needs_review`, nine to eleven renders, about $1, the frozen
   author and reviewer visible in the run's `route_snapshot`. Review every video.
6. Second run: World Order (151 min). Record settled cost and each full-source call's
   duration against the payload-scaled deadline.

## What each stop means

| Run status and message | Cause | Do |
| --- | --- | --- |
| `needs_review`, videos present | the cycle completed or hit a limit named in the message | review, accept, correct |
| `failed`, "Route X rejected the … request (HTTP …)" | the route does not accept this request shape | change the snapshot, new run |
| `failed`, "… exceeds the context window of route X" | source too large for that route | a larger-window route, or wait for the topic hierarchy |
| `budget_paused` | the next call would exceed the run budget | cancel; new run with a larger budget |
| `outcome_unknown` | a provider call ended without a confirmed outcome; the reservation is retained | reconcile read-only; never replay |
| `failed`, "… ended without a response; its charge … is settled … Three attempts ended the same way" | the route kept failing inside its stream after two automatic retries | wait and start a new run, or change the route |
| `cancelled` | you cancelled | — |

Never deploy `pipeline` while a topic run is active: a model call in flight becomes
`outcome_unknown`.
