# Topic generation on staging

How **Find topic videos** on staging.temnia.dev runs the standalone-topic program with
nothing to configure on the box. Decision record: the 2026-09-13 entries in `AGENTS.md`;
plan: [topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).

## What runs

`standalone-topics/8` on `TopicSelectionWorkflowV8`, queue `temnia-pipeline`, the same worker
that ingests and transcribes. Decision record: the 2026-09-15 entry in `AGENTS.md`; plan:
[topics-production-360-view.md](../plans/topics-production-360-view.md). No chapter lane
exists. No qualification manifest or version gate exists: the button starts the program on
the frozen route snapshot, and every stop is a typed reason on the run row.

The program builds the source index once (episode → sections of at most eight regions →
regions of at most 32 sentences), then runs one bounded decision per planned item, each as
one Temporal activity: an inventory per section with that section's speech inline; author
packaging per section batch of at most twelve opportunities; a cold review per candidate;
bounded source review items per section (candidate batches, opportunity batches, one omission
scan per region, overlap and handoff pairs); and connected-component repairs, up to the repair
allowance. A decision that cannot be admitted after one correction becomes a coverage gap: the
run records it, continues around it, and names it in the finish message. The run stops only for
money (the allowance), a provider that refuses or fails on every eligible route, an unconfirmed
paid outcome, an invalid source, or cancellation.

The panel offers three inputs before **Find topic videos**: optional instructions, the run
allowance in dollars (default from the deployment file, at most its maximum), and the author
and reviewer models chosen from the snapshot's pools ("Server default" keeps the snapshot's
own order; the reviewer must be a different family from the author, which the worker enforces
by reserving the reviewer before the first author call). The run row shows the pre-spend
projection (calls and dollars, from the actual section prompts) once planning has run, and a
paused run can have its allowance raised from the panel and then be retried.

## The roster (frontier vendors, direct, since 15 September)

Decision record: the 2026-09-15 entries in `AGENTS.md`; design:
[topics-vendors-and-usage-2026-09-15.md](../design/topics-vendors-and-usage-2026-09-15.md).
The seats come from the 2026-08-26 audition evidence and are re-auditioned per seat once the
pipeline is proven on three held-out recordings.

| Seat | Order |
| --- | --- |
| `propose` (author, repair) | Claude Opus 5 (high), then GPT-5.6 Sol (high) |
| `verify` (cold, local, omission, pairs) | GPT-5.6 Terra (high), then Gemini 3.8 Flash (medium) |
| `inventory` (per-section inventory) | Claude Sonnet 5 (medium), then Gemini 3.8 Flash (medium) |

Every route calls its vendor directly through PydanticAI's adapter with the SDK's retries off;
there is no gateway on the path. The reviewer is chosen excluding the author's family, so Terra
or Gemini reviews whatever Opus or Sol authored. Prices in the snapshot are the vendors'
published rates on 2026-09-15 (cache reads and writes included where the vendor charges them);
each call settles from the usage in its response, so a run's spend is exact when the call ends.
Anthropic routes send the automatic cache breakpoint, so a decision's later rounds pay a tenth
for the prefix. The worker paces each route from the vendor's rate-limit headers and honours
`retry-after` on the same ladder as before; a dropped stream settles at its estimate and is
retried, and no run is ever fenced on an unconfirmed outcome.

## Where the configuration lives

One committed file per deployment is the whole harness configuration:
[`apps/pipeline/harness/staging.json`](../../apps/pipeline/harness/staging.json)
(`harness-config/1`, `gateway: direct`), next to its route snapshot
[`topic-routes-staging-0a40fd58.json`](../../apps/pipeline/harness/topic-routes-staging-0a40fd58.json)
(ID `0a40fd581f1f966cfb05842d171192331b224a8eda59fd264f64d2246e7262b2`). Both images copy
that directory to `/app/harness/` and bake `HARNESS_CONFIG_PATH=/app/harness/staging.json`,
so the worker and the web read the same file from the same commit: the run config the web
sends equals the worker's by construction. What stays in Dokploy, on `pipeline`, set once:
the three vendor keys `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `GEMINI_API_KEY`, and
optionally `LOGFIRE_TOKEN` for tracing. A missing key does not stop the worker (it also
ingests and transcribes): the boot log names the variable, the routes at that vendor are
skipped, and a run that has no route left stops with the variable in its sentence.
`OPENROUTER_API_KEY` is no longer read by this configuration.

While `HARNESS_CONFIG_PATH` is set, every other `HARNESS_*` entry in a service's environment
is ignored and the worker's boot log lists the ignored names; stale entries from earlier
enablements are harmless. An empty `HARNESS_CONFIG_PATH` (the gate, local development, the
experiment operator) means the environment is the configuration, as before.

To change the roster without rebuilding images, an operator can mount a separate
configuration file and its immutable, content-addressed route snapshot into both services,
set `HARNESS_CONFIG_PATH` to that file, and use the supported
[Dokploy configuration reload](staging.md#4b-apply-runtime-configuration-without-a-code-change).
The worker reads its snapshot at boot and the web caches configuration, so reload both
services and verify their effective snapshot IDs before starting a run. Other `HARNESS_*`
overrides remain ignored while a config file is selected. This is a process restart using
the same images, not a hot model switch; wait for active topic runs to finish first.
New runs use the new roster. Existing runs retain their frozen snapshots, and a retry
whose old run config differs from current worker settings can be refused. The ordinary
committed-file workflow below remains the default; no mounts or settings are changed by
this documentation.

Limits in the file: output 65,536 (the config ceiling; every route allows more), no dispatch
ceiling, 3 repairs, $20 default allowance and $100 maximum, render concurrency 2, evidence
window 80 sentences, `scdet` shot detector, at most 3 calls in flight per route with 0.5 s
between dispatches (the worker's admission per route; the vendors' headers pace the rest).

`tests/test_harness_config_file.py` boots every committed configuration in the gate: the
snapshot ID, the two-family rule per pool, the inventory seat and the output ceilings are
checked before a merge, not on the box. A Karma run is the test of the keys: a missing or
wrong key stops the run with the variable in its sentence, and a model the vendor does not
recognise shifts the seat to its fallback. Optional, when a number is wanted without a run
(the answering model, the usage, the settled micros and the account's rate-limit tier per
route), from inside the pipeline container where the keys already are:

```bash
ssh temnia-vps 'docker exec $(docker ps -q -f name=temnia-staging-pipeline) temnia-harness vendors probe /app/harness/topic-routes-staging-0a40fd58.json'
```

## Merge, deploy, click

1. Merge the PR. Dokploy builds and deploys both images from `main` (each application's
   Deployments tab shows the merge SHA green). Do not deploy while a topic run is active;
   a model call in flight is settled at its estimate by the next execution and dispatched again
   (then **Retry this run** if the run had stopped).
2. Read the `pipeline` boot log: one line
   `harness enabled from /app/harness/staging.json: backend gateway, gateway direct, route snapshot 0a40fd58…`
   (and no `is not set` warning above it),
   then the Temporal pollers on `temnia-pipeline` and `temnia-pipeline-control`.
3. Open a Ready source on staging.temnia.dev, Topics tab. Leave the instructions box empty
   (the single default brief applies), keep or change the allowance and models, and press
   **Find topic videos**. The run row shows the projection after planning.

First run: Karma. Expect `needs_review`, nine to eleven videos, about $4 to $7 on the
frontier seats, the run's `route_snapshot` naming the author, reviewer and inventory routes
above. Play every video, accept or correct,
export. Second run: World Order (151 min); note each full-source call's duration against the
payload-scaled deadline. Record the run IDs, cost and durations in the day log.

## Rendering on the GPU

The media app on Modal carries `render_sections` and deploys from `main` through the
`modal-deploy` GitHub Actions workflow (deploy, then the GPU smoke), the same way Dokploy
deploys the two images. The deployment file already says `render: modal / h264_nvenc`.
Until the app deploy has landed, a worker that finds the function absent renders that
revision on its own CPU with libx264 and logs why; the next run after the deploy uses the
card. A run stopped on such a defect is retried after the deploy that fixes it: **Retry this run**
resumes it under the new build, which is recorded on the run; only a change to the prompts or
schemas makes an old run non-resumable, and the panel says so. The same fallback applies when the deployed function cannot start (an import error
in the image): the run finishes on the CPU, the worker log names the error, and the deploy
smoke's `render_probe` step exists to catch that before any run does. GPU and CPU renders are different media artifacts, so nothing is reused across the
encoders. A run's GPU render shows `render-remote` in the activity heartbeat with the Modal
call id; a worker restart reattaches to that call rather than rendering twice.

One-time, not per change: the repository secrets `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`
hold the `temnia-pipeline-staging` token (staging.md §2c). Without them the workflow stops
at its first step and says so.

## Changing the roster or a limit

Edit `apps/pipeline/harness/staging.json` in a PR. A different roster is a new snapshot
file with a new ID (build it with the pipeline's `RouteSnapshot` model and `computed_id()`;
never edit an existing snapshot) and the file's `routeSnapshot` block points at it. The gate
refuses a configuration that does not boot. Merge, deploy; nothing to set on the box.
Production gets its own file and sets `HARNESS_CONFIG_PATH` to it in Dokploy, once.

Two limits are the user's, not the file's: `limits.maxRunBudgetMicros` is the most a run may
be given (staging: $100) and `limits.defaultRunBudgetMicros` is what the allowance box
proposes (staging: $20). `maxDispatches` is null: the allowance is the only ceiling. Without a
deployment file, the web reads `HARNESS_DEFAULT_RUN_BUDGET_MICROS` and lists models from
`HARNESS_ROUTE_SNAPSHOT_PATH`; an unreadable snapshot leaves the model selects hidden and the
run on the snapshot's own order.

## What a run does before its first model call

Nothing heavy. Ingest measured the master's timeline, shot boundaries and speech coverage
once (`measure_source_sensors`, the `sensors` stage in the source's progress), so a topic run
heads the object, reads those records and assembles evidence in seconds. A source ingested
before 13 September has no records: its first run downloads the master and measures, as
before, and caches the result for later runs. Rendering still fetches the master.

## What each stop means

A throttled route (HTTP 429) pauses every dispatch on that route in the worker for the
provider's Retry-After (20 s without one), so sibling decisions wait instead of burning their own
attempts. An answer cut off at its output allowance is retried up to twice with the allowance
doubled; routes at high reasoning effort start with twice the base allowance because thinking
tokens count against it.

| Run status and message | Cause | Do |
| --- | --- | --- |
| `needs_review`, videos present | the cycle completed or hit a limit named in the message | review, accept, correct |
| `failed`, "Route X rejected the … request (HTTP …)" | the route does not accept this request shape | change the snapshot, new run |
| `budget_paused` | the next call would exceed the run allowance | raise the allowance in the panel, then **Retry this run**; admitted decisions are reused |
| `needs_review`, message names coverage gaps | one or more bounded decisions produced no admitted answer after a correction; the rest of the run continued | review; the gap's retained answers and diagnostics are on the run's artifacts |
| `failed`, "No route is eligible for one editorial seat …" | the chosen author and reviewer are the same family, or the snapshot has no independent reviewer | choose different models, new run |
| `failed`, "Every eligible … route failed transiently for … across 2 rounds of the pool" | throttling or dropped streams on every eligible route through a 20/40/80/160 s ladder per route, a five-minute pool pause, and the ladder again | wait, then **Retry this run**; admitted decisions are reused, only the unfinished ones are paid again |
| `outcome_unknown` | a provider call ended without a confirmed outcome and the gateway receipt is still pending | nothing: the decision asks the gateway for the receipt on a 30 s to 5 min ladder for 17.5 minutes and resumes on its own; a reported charge is settled, a generation the gateway has no record of ten minutes later is released at zero; if the run's execution has already ended, the reaper settles it every 15 minutes and leaves it `failed` with "was reconciled from the gateway receipt" for **Retry this run** |
| `failed`, "Every qualified … route failed transiently for the … call (…); last: …" | every route in that seat's pool was throttled or down, each retried after a pause | wait, then **Retry this run**; the retained work is reused |
| `failed`, "… HTTP 402 … insufficient credits or … spending limit" | the gateway account or key | top up or raise the limit, then **Retry this run** |
| `failed`, "… was reconciled from the gateway receipt. Retry this run …" | a call ended without a confirmed outcome and its receipt has since settled | **Retry this run** |
| `cancelled` | you cancelled | — |

A run that stops on a known failure keeps its identity and artifacts: **Retry this run**
(shown on `failed` and `budget_paused` runs) starts a new execution that replays every
settled model response and pays only for what never completed. A tab left open across a
deploy says "Temnia was updated while this page was open. Reload the page"; that is the
page, not the run.

Never deploy `pipeline` while a topic run is active.
