# Topic generation on staging

How the one staging worker is configured so that **Find topic videos** on
staging.temnia.dev runs the current standalone-topic program. Decision record: the
2026-09-13 entries in `AGENTS.md`; plan:
[topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).
Dokploy reload mechanics are in [staging.md](staging.md) §4b.

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

## The route snapshot

The file is committed: [`infra/harness/topic-routes-staging-0df7f78d.json`](../../infra/harness/topic-routes-staging-0df7f78d.json),
snapshot ID `0df7f78dc6dccf978d976ee7e4d94d672d304b65f8d75db9bee6906b0008886c`. It is the
r25 experiment catalogue without Astra, `gateway-transport/2` through OpenRouter:

| Seat | Order |
| --- | --- |
| `propose` (author, repair) | Kimi K3/Fireworks, DeepSeek V4 Pro 0813/Fireworks, Gemini 3.8 Flash/Vertex |
| `verify` (inventory, cold, source review) | Gemini 3.8 Flash/Vertex, Kimi K3/Fireworks, DeepSeek V4 Pro 0813/Fireworks |
| `summary` (unused by topics, required by the schema) | as `propose` |

The worker refuses to boot if the file's content does not hash to the ID in
`HARNESS_ROUTE_SNAPSHOT_ID`. A reorder or a new route is a new file with a new ID, never an
edit; build it with the pipeline's `RouteSnapshot` model (`computed_id()`) and commit it
next to this one.

## Environment

On `pipeline` (secrets stay here):

| Variable | Value |
| --- | --- |
| `HARNESS_ENABLED` | `1` |
| `HARNESS_BACKEND` | `gateway` |
| `HARNESS_GATEWAY` | `openrouter` |
| `OPENROUTER_API_KEY` | the staging key (already on the service) |
| `HARNESS_ROUTE_SNAPSHOT_PATH` | `/etc/temnia/topic-routes.json` |
| `HARNESS_ROUTE_SNAPSHOT_ID` | `0df7f78dc6dccf978d976ee7e4d94d672d304b65f8d75db9bee6906b0008886c` |
| `HARNESS_MAX_OUTPUT_TOKENS` | `65536` |
| `HARNESS_MAX_DISPATCHES` | `64` |
| `HARNESS_MAX_REPAIRS` | `3` |
| `HARNESS_MAX_RUN_BUDGET_MICROS` | `20000000` |
| `HARNESS_MAX_RENDER_CONCURRENCY` | `2` |
| `HARNESS_TOPIC_SHOT_DETECTOR` | `scdet` |
| `HARNESS_TOPIC_SELECTION_*`, `HARNESS_CHAPTER_LLAMA_CONFIG_JSON` | delete if present |

On `web` (non-secret only):

| Variable | Value |
| --- | --- |
| `HARNESS_ENABLED` | `1` |
| `HARNESS_BACKEND` | `gateway` |
| `HARNESS_ROUTE_SNAPSHOT_ID` | the same ID |
| `HARNESS_MAX_OUTPUT_TOKENS` | `65536` |
| `HARNESS_MAX_DISPATCHES` | `64` |
| `HARNESS_MAX_REPAIRS` | `3` |
| `HARNESS_MAX_RUN_BUDGET_MICROS` | `20000000` |
| `HARNESS_MAX_RENDER_CONCURRENCY` | `2` |
| `HARNESS_EVIDENCE_WINDOW_SENTENCES` | `80` |
| `HARNESS_CHAPTERS_ENABLED` | delete if present |

The run config the web sends must equal the worker's; a mismatch ends the run with a message
that says so. These values were checked on 13 September: the worker's `validate_boot` passes
with this file and this table (the run-config ceiling was raised to 65,536 for it).

## Rollout, step by step

Do this once, after PR #41 is merged and both services have deployed the merge commit
(Dokploy → each application → Deployments shows the merge SHA green).

1. **Nothing in flight.** Temporal UI (`temporal.temnia.dev`): no running workflow on
   `temnia-pipeline`. Deploying or reloading the worker under a model call makes that run
   `outcome_unknown`.
2. **Copy the snapshot to the VPS** (from the merged checkout, as your SSH user):

   ```bash
   scp infra/harness/topic-routes-staging-0df7f78d.json vps:/tmp/topic-routes-0df7f78d.json
   ```

   then on the VPS:

   ```bash
   sudo install -d -o root -g root -m 0755 /var/lib/temnia/harness && sudo install -o root -g root -m 0444 /tmp/topic-routes-0df7f78d.json /var/lib/temnia/harness/topic-routes-0df7f78d.json && ls -l /var/lib/temnia/harness/
   ```

3. **Mount it on `pipeline`.** Dokploy → `pipeline` → Advanced → Mounts → Add: type
   *Bind*, host path `/var/lib/temnia/harness/topic-routes-0df7f78d.json`, mount path
   `/etc/temnia/topic-routes.json`. Leave existing mounts alone.
4. **Set the `pipeline` environment** (Dokploy → `pipeline` → Environment): every row of the
   `pipeline` table above; keep all unrelated entries (database, storage, Temporal, Modal,
   the OpenRouter key). Save.
5. **Reload `pipeline`** (Dokploy → `pipeline` → General → Reload, not Deploy). Then read
   the new task's log: it must print the snapshot ID `0df7f78d…`, no error, and the Temporal
   UI must show pollers on `temnia-pipeline` and `temnia-pipeline-control`. If the log
   says the ID does not match, the file or the ID is wrong; fix and reload.
6. **Set the `web` environment** (the `web` table above), Save, Reload `web`. Open
   `https://staging.temnia.dev/api/health`, then a source that is Ready: the Topics tab shows
   **Find topic videos** enabled. If it says the harness is disabled, the web env or its
   reload did not take; check the container's environment, not the saved form.
7. **First run: Karma.** Leave the instructions box empty (the single default brief applies),
   press the button, watch the run card. Expect `needs_review`, nine to eleven videos, about
   $1, and the run's `route_snapshot` naming the author and reviewer above. Review every
   video: play it, accept or correct, export. Note settled cost and each full-source call's
   duration.
8. **Second run: World Order (151 min).** Same, and record each full-source call's duration
   against the payload-scaled deadline.
9. Record the snapshot ID, both runs' IDs, cost and durations in the day log.

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

Never deploy or reload `pipeline` while a topic run is active.
