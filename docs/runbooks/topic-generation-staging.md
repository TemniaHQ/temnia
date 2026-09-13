# Topic generation on staging

How **Find topic videos** on staging.temnia.dev runs the standalone-topic program with
nothing to configure on the box. Decision record: the 2026-09-13 entries in `AGENTS.md`;
plan: [topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).

## What runs

`standalone-topics/3` on `TopicSelectionWorkflow`, queue `temnia-pipeline`, the same worker
that ingests and transcribes. No chapter lane exists. No qualification manifest exists; a
route that refuses a request ends the run with a message naming the route, stage and HTTP
status.

## The roster (settled 13 September on technical reliability)

Author and repair: Kimi K3/Fireworks (five completed full-source runs, no transport failure
in that seat); alternate author: DeepSeek V4 Pro 0813/Fireworks (two completed). Inventory,
cold and source review: Gemini 3.8 Flash/Vertex at medium effort (the only reviewer that has
finished this call shape on Karma, five times). Astra is excluded. Gemini's two known failure
modes are handled: an upstream rate limit inside a stream is settled against the receipt and
retried twice, and the output allowance is Gemini's route maximum, 65,536. This is a
reliability selection, not an editorial winner.

A production seat pool must name at least three model families, so every pool lists all
three routes; only the order differs. The reviewer is chosen from the verify pool excluding
the author's family, so Gemini reviews whatever Kimi or DeepSeek authored.

## Where the configuration lives

One committed file per deployment is the whole harness configuration:
[`apps/pipeline/harness/staging.json`](../../apps/pipeline/harness/staging.json)
(`harness-config/1`), next to its route snapshot
[`topic-routes-staging-0df7f78d.json`](../../apps/pipeline/harness/topic-routes-staging-0df7f78d.json)
(ID `0df7f78dc6dccf978d976ee7e4d94d672d304b65f8d75db9bee6906b0008886c`). Both images copy
that directory to `/app/harness/` and bake `HARNESS_CONFIG_PATH=/app/harness/staging.json`,
so the worker and the web read the same file from the same commit: the run config the web
sends equals the worker's by construction. The only value that stays in Dokploy is the
secret `OPENROUTER_API_KEY` on `pipeline`, which is already there.

While `HARNESS_CONFIG_PATH` is set, every other `HARNESS_*` entry in a service's environment
is ignored and the worker's boot log lists the ignored names; stale entries from earlier
enablements are harmless. An empty `HARNESS_CONFIG_PATH` (the gate, local development, the
experiment operator) means the environment is the configuration, as before.

The snapshot is the r25 experiment catalogue without Astra, `gateway-transport/2` through
OpenRouter. A production seat pool must name at least three model families, so every pool
lists all three routes; only the order differs, and the reviewer is chosen excluding the
author's family:

| Seat | Order |
| --- | --- |
| `propose` (author, repair) | Kimi K3/Fireworks, DeepSeek V4 Pro 0813/Fireworks, Gemini 3.8 Flash/Vertex |
| `verify` (inventory, cold, source review) | Gemini 3.8 Flash/Vertex, Kimi K3/Fireworks, DeepSeek V4 Pro 0813/Fireworks |
| `summary` (unused by topics, required by the schema) | as `propose` |

Limits in the file: output 65,536 (Gemini's route maximum; the config ceiling was raised to
it), 64 dispatches, 3 repairs, $20 run budget, render concurrency 2, evidence window 80
sentences, `scdet` shot detector.

`tests/test_harness_config_file.py` boots every committed configuration in the gate: the
snapshot ID, the three-family rule and the output ceilings are checked before a merge, not
on the box.

## Merge, deploy, click

1. Merge the PR. Dokploy builds and deploys both images from `main` (each application's
   Deployments tab shows the merge SHA green). Do not deploy while a topic run is active;
   a model call in flight becomes `outcome_unknown`.
2. Read the `pipeline` boot log: one line
   `harness enabled from /app/harness/staging.json: backend gateway, gateway openrouter, route snapshot 0df7f78d…`,
   then the Temporal pollers on `temnia-pipeline` and `temnia-pipeline-control`.
3. Open a Ready source on staging.temnia.dev, Topics tab, **Find topic videos**. Leave the
   instructions box empty (the single default brief applies) and press it.

First run: Karma. Expect `needs_review`, nine to eleven videos, about $1, the run's
`route_snapshot` naming the author and reviewer above. Play every video, accept or correct,
export. Second run: World Order (151 min); note each full-source call's duration against the
payload-scaled deadline. Record the run IDs, cost and durations in the day log.

## Changing the roster or a limit

Edit `apps/pipeline/harness/staging.json` in a PR. A different roster is a new snapshot
file with a new ID (build it with the pipeline's `RouteSnapshot` model and `computed_id()`;
never edit an existing snapshot) and the file's `routeSnapshot` block points at it. The gate
refuses a configuration that does not boot. Merge, deploy; nothing to set on the box.
Production gets its own file and sets `HARNESS_CONFIG_PATH` to it in Dokploy, once.

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

Never deploy `pipeline` while a topic run is active.
