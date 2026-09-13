# Topic generation in staging: one button, one program, no manifest gate

13 September 2026. Reviewed with Rajesh the same day (grilling rounds 1 and 2); the
decisions below are the settled ones and implementation started on PR #41's branch.
Settled on 13 September: the chapter lane is parked on staging (one worker, one
snapshot, one gateway), the v1/v2 topic programs are hidden now and deleted in the next
PR, the qualification manifest gate is deleted, OpenRouter is the gateway. Written from
a read of `main` at `5c63091` (PR #40), the
[standalone topic decisions](../design/standalone-topic-decisions-2026-09-12.md), the
[v3 production record](../design/standalone-topic-production-quality-v3-2026-09-12.md),
the [qualification runbook](../runbooks/topic-selection-qualification.md), the
12 September log and the repo rules.

## 0. Outcome

After this work, on staging.temnia.dev, a source with a ready transcript has one control,
**Find topic videos**, with optional instructions. It starts the current program on the
routes that have completed full-source runs, and the run ends either with reviewable
rendered videos or with a sentence that names the cause and what to do next. No
qualification manifest, no per-version flag, no program dropdown, and no second lane
competing for the worker: chapter creation is paused on staging until it is wanted again.

The program it runs is the one r18/r20/r25 ran: reviewer-family inventory, author,
per-candidate cold review, independent source review with computed overlaps and
handoffs (`topic-selection-portfolio/4`), grounded repair up to three times with fresh
reviews, the select-only publication gate, `topic-compiler/3` pause ownership, render.

What this does **not** deliver: the compound-candidate blind spot measured in r20 and
r25 (author and reviewer both call a prayer-plus-mantra span one topic). That is a
program gap with no answer yet (§6). Physical cuts are the measured, sentence-complete
kind Rajesh reviewed on r11, with the trailing pause now owned by the preceding video.

## 1. Why the button refuses today

Every line below is from the code on `main`.

| Layer | Check | Effect on a staging user |
| --- | --- | --- |
| Web `startTopicRun` (`apps/web/app/actions/topics.ts`) | Default program is `standalone-topics/3`; refused unless the web process has `HARNESS_TOPIC_SELECTION_V3_ENABLED=1` | "The new selection program awaits deployment qualification." The only startable option is the v1 program from the dropdown. |
| Worker `runs.start_or_refetch_run` | The same flag, read from the worker's own env; also `request.config` must equal the worker's `allowed_config()` byte for byte | If web and worker disagree, the run starts, then ends `failed` with "The chapter workflow stopped after a known activity failure." |
| Worker boot `HarnessSettings.validate_boot` | With the flag on and `HARNESS_BACKEND=gateway`, a `topic-selection-qualification/5` manifest at `HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH` must bind the worker's exact snapshot | A missing, stale or mismatched manifest refuses the whole worker, not one run. |
| Manifest validation (`qualification_topic_selection.py`) | Every route in the `propose` and `verify` pools needs a settled paid call for all five stages at today's prompt SHA, output-contract SHA, native-schema SHA, transport, reasoning and service tier; every report and receipt must exist at its recorded absolute path with identical bytes | Any prompt or schema bump silently drops coverage; the next reload fails boot. PRs #36, #39 and #40 each bumped prompts or schemas within three days. Pools need three families, a run uses two, so each cohort pays for routes the run never selects. |
| Per call `prepare_topic_selection_call` | Re-reads and re-hashes the manifest, every report and every receipt before every stage, once per candidate for cold reviews | Cost in I/O only, but it is the same failure surface again mid-run. |
| Staging snapshot | The mounted snapshot is the chapter-qualified Vercel trio (DeepSeek/DeepInfra, Kimi/Alibaba, GLM/Baseten). None has a five-stage receipt. Every finished v3 run used OpenRouter routes (Kimi K3/Fireworks, Gemini 3.8 Flash/Vertex, DeepSeek V4 Pro/Fireworks) on an experiment worker with its own pinned snapshot and queue | Even with a manifest, the ordinary worker cannot use the routes that work: one snapshot and one gateway per worker, shared with the chapter lane. |
| UI | Program dropdown with three generations; availability messages say "Chapter editing" on the Topics tab | The user chooses between programs that are refused, and reads chapter wording for topic failures. |

Two silent differences between a web run and the measured runs, invisible in any
version string: the default brief on the web (`topic-defaults.ts`) is a different
string from the `EDITORIAL_BRIEF` every r-run used, and it is hashed into every prompt;
and web runs carry no program manifest, so a web bundle cannot prove which prompt bytes
produced it.

The code path itself is already unified: a web run gets `TopicSelectionWorkflowV3`,
portfolio/4 and compiler/3 with no pinning. The v1/v2/v3 feel is the flags, the manifest
and the dropdown.

## 2. Decisions

### D1. One program, one button

- The topics panel loses the program selector. **Find topic videos** starts the current
  program. Instructions stay optional.
- `standalone-topics/1` and `/2` become read-only generations: the web never starts
  them, their agents, prompts and compile entry points leave the new-run path, and their
  readers (assessment and rejection union types, `TopicSourceReview`, the
  `compilerVersion` accept set, the `topic-grid` fallback in `candidate_time`, the
  compiler re-selection in `topic_patch_review.py`) move into one module whose name says
  it is for reading and correcting existing runs.
- `TopicRunWorkflow.program()` and the four v1-only activities, `TOPIC_AGENTS`,
  `TOPIC_SELECTION_AGENTS`, the `/2` prompt constants, `TopicPortfolioReviewV3` in every
  producer union, and the stale `topic-rubric/1` string in the bundle exporter are
  deleted. `compile_topics` and `compile_topics_v2` stay only where a human correction of
  an existing run needs its originating compiler.
- Registered workflow type names and the DB policy literal `standalone-topics/3` do not
  change: they are in Temporal histories and run rows. `TopicRunWorkflow` and
  `TopicSelectionWorkflow` stay registered until a staging query shows no non-terminal
  run with policy `/1` or `/2`; that deletion is a follow-up commit, not this PR.
- `require_source_candidate_reviews = program_version != V3`, which today carries four
  meanings (candidate judgments in source review, overlaps required, handoffs required,
  `unfocused_extent` promotion), collapses into four unconditional behaviours.
- Removed env: `HARNESS_TOPIC_SELECTION_ENABLED`, `HARNESS_TOPIC_SELECTION_V3_ENABLED`,
  `HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH`, `HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH`
  on web and worker, and their use in `scripts/local-ci.mjs`.

### D2. Qualification is evidence, not a gate

The manifest proves one thing: a route once returned each strict schema on a five-sentence
fixture with settled cost. It did not predict the failures that mattered (Astra passed
short calls and failed two full-source runs; Gemini's repair truncated at length; Kimi's
review crossed the deadline). The paid-call ledger, not the manifest, protects the money:
reservation before dispatch, receipt after, unknown-outcome fence when neither arrives.

- Delete `validate_topic_selection_qualification` from boot and from
  `prepare_topic_selection_call`; delete `bind-topics`, manifest formats `/2` to `/5`, and
  their tests. Keep `qualify_harness_gateway.py run` and `reconcile` as an optional
  pre-flight for auditioning a new route before a full-source run; its output binds
  nothing and no worker reads it.
- Admission happens in the run. The first call on a (snapshot, route, stage, program
  hashes) tuple is the proof. A parameter-admission 404, a strict-schema refusal or an
  invalid response at any stage ends the run with a typed stop reason,
  `route_admission_failed`, naming route, stage and code, with the settled cost retained.
  The inventory call is first and cheapest (observed $0.06 to $0.24), so a bad reviewer
  route costs that much; a bad author route costs inventory plus author. Identical
  settled responses are reusable by the existing response-reuse identity when the
  snapshot is corrected and a new run started; verify this reuse crosses run IDs before
  claiming it in the UI.
- Every attempt already records prompt, schema and native-schema hashes with its
  response artifact; a read-only report, `temnia-harness route-evidence --snapshot`, lists
  settled calls per route and stage for a program. That is the qualification record from
  now on.
- Boot keeps: snapshot immutability and ID match, gateway credential for the snapshot's
  gateway, every pool route fitting `effective_topic_output_tokens` with context headroom
  (the current v3-only asymmetry in `settings.py` goes away with the flags), seats present.
- The experiment operator (`topic_experiment.py`) drops its manifest pin and keeps its
  snapshot and program-hash pins, so controlled comparisons still freeze what matters.

Supersedes: the 2026-09-11 rule "New v2 gateway calls require exact four-schema/prompt/
settings qualification; the web and worker rollout flag defaults off", the decisions
doc §2 staging procedure, and the runbook sentence that the worker requires a
qualification path. The 2026-09-05 no-default-vendor rule is untouched: the snapshot
still selects seats, and the roster below is provisional.

### D3. One worker, one snapshot: the chapter lane is parked on staging

Chapters (a navigation partition of the whole episode, exact cover) and topics
(independent standalone videos) are two editorial contracts that were built side by side
when Rajesh clarified the target on 10 September. Only topics are the product now. One
worker holds one snapshot and one gateway, and both lanes read the same seat pools, so
running both would need a second worker or lane-scoped seats. Rajesh chose to park
chapters instead: the one staging worker gets the OpenRouter topic snapshot; chapter
creation is refused on the web with `HARNESS_CHAPTERS_ENABLED=0` ("Chapter creation is
paused on this server. Existing chapter runs remain reviewable."); chapter code, runs,
review and export stay in place. No new service, no new queue.

Alternatives not taken: a second Dokploy worker on a `temnia-topics` queue (the shape of
the experiment runs; correct if both lanes must stay live), lane-scoped seats with
per-route gateway credentials (two contract changes), moving the chapter trio to
OpenRouter (a requalification nobody needs now).

### D4. Web and worker config stay as they are

With one worker, the web already carries the worker's limits and snapshot ID for the
chapter lane, and the same values serve topics. The run config equality check stays.
The per-version topic flags go (D1); no new flag replaces them. Worker boot remains the
gate: wrong snapshot, missing key or a route below the output ceiling refuses boot,
loudly.

### D5. Staging roster and settings (provisional, not a winner)

The topic snapshot is the last experiment snapshot (the r25 preparation) minus nothing:
Kimi K3/Fireworks, DeepSeek V4 Pro 0813/Fireworks and Gemini 3.8 Flash/Vertex, all
`gateway-transport/2` through OpenRouter. Astra is excluded (two full-source transport
failures). The snapshot file is not in the repo; it is copied from the experiment host's
prepared manifest and its ID recorded in the runbook. Seat order is an operational
declaration recorded in every run's `route_snapshot`; `editorial_routes` picks the first
eligible verifier, then the first author of another family. To honour the §9 interim
rule (alternate the reviewer family), a later snapshot reorders the verify pool; that is a
new snapshot ID, never an edit.

| Setting on the staging `pipeline` service (and matching non-secret values on `web`) | Value | Why |
| --- | --- | --- |
| `HARNESS_GATEWAY` / `OPENROUTER_API_KEY` | `openrouter` / the key already on staging | Every finished v3 run; the Vercel full-source attempts timed out on 11 September |
| `HARNESS_ROUTE_SNAPSHOT_PATH` / `_ID` | `/etc/temnia/topic-routes.json`, root-owned `0444` | Same handling as the chapter snapshot |
| `HARNESS_TOPIC_SHOT_DETECTOR` | `scdet` | Decisions doc §1; the code default changes to match in this PR |
| `HARNESS_MAX_OUTPUT_TOKENS` | `32768` | Every v3 run; clamped per route by `effective_topic_output_tokens` |
| `HARNESS_MAX_DISPATCHES` | `64` | r24 proposed 20 candidates; 20 cold reviews plus three repair rounds exceeds 32 |
| `HARNESS_MAX_REPAIRS` | `3` | Program contract |
| `HARNESS_MAX_RUN_BUDGET_MICROS` | `20000000` | Karma runs settled $0.43 to $1.49; a 2.5-hour source is about 3.4 times the input per full-source call |
| `HARNESS_MAX_RENDER_CONCURRENCY` | `2` | Unchanged |
| `HARNESS_CHAPTER_LLAMA_CONFIG_JSON` | unset | The experiment path forbids it; production matches |
| `HARNESS_CHAPTERS_ENABLED` (web) | `0` | Chapter lane parked (D3) |
| `TRANSCODE_BACKEND` / `TRANSCRIPTION_PROVIDER` | unchanged (`modal`) | The same worker still ingests and transcribes |

### D6. Long sources are admitted and given time

Two frozen constants refuse or kill any source much longer than Karma before a model
sees it.

- Admission counts one byte as one token (`routes.py` `estimate_cost`). Karma's 90 KB is
  admitted as ~100k tokens against ~25k real. A 2.5-hour source (~240 KB) plus 32k output
  exceeds a 256k route. Change: a declared floor of 2.0 bytes per token
  (`input_tokens = ceil(payload_bytes / 2) + 8192`), conservative for Latin, Devanagari
  and CJK text, recorded as `admission/2` in the run config so reservation arithmetic
  is reproducible per run. The 512 KiB payload cap stays; beyond it the topic hierarchy
  question (E4) remains open and the run refuses with a message that says so.
- Deadlines are frozen at 300 s idle / 540 s aggregate per route and capped at 540 s in
  `GatewayTransportPolicy`; the model activity is 10 minutes. Karma's full-source calls
  took 166 to 290 s; linear scaling puts a 2.5-hour source at 560 to 990 s, over the cap.
  Change: the frozen aggregate deadline is the unit per 128 KB of payload, effective
  deadline `total × max(1, ceil(payload_bytes / 131072))`, activity start-to-close the
  effective deadline plus 60 s, hard ceiling 45 minutes. Karma keeps exactly 540 s, so
  the r-runs stay comparable. This is declared, not measured; the first World Order run
  measures it.
- Known and not fixed here: a reasoning model can spend the whole 32,768 allowance on
  hidden reasoning (observed 31,450 of 32,768 on the source review). The run then ends
  with the last valid selection and the gate withholds; the message says which call
  truncated. Per-stage reasoning effort is a route property and therefore a new snapshot.

### D7. One brief, one manifest

- `EDITORIAL_BRIEF` in `topic_editorial.py` is the only default. The web sends `null`
  when the instructions box is empty; `resolveTopicBrief` and `DEFAULT_TOPIC_BRIEF` go.
  A web run's rubric hash then equals the r-runs'.
- `start_chapter_run` attaches `current_program(policy)` (moved out of
  `topic_experiment.py` into a shared module) to every topic run at no model cost, so a
  staging bundle binds prompt-template and native-schema bytes the way experiment
  bundles do.

## 3. The 360 view

### Inputs and edges

| Case | Behaviour after this plan | Where |
| --- | --- | --- |
| Empty transcript | `needs_review`, "The accepted transcript has no words to ground topic discovery." No render | existing |
| Source too large for a route | Refused before dispatch at every stage, including the first author call (today the author call escapes the handler and reads as a generic failure); message names route, payload size and the route's context | D6 + `topic_selection_workflow.py` author path |
| Duplicate click | Existing request-key idempotency with `USE_EXISTING` | existing |
| Second run on the same source while one is running | Allowed today; the panel's run list shows both; the source cache lease serialises master downloads | verify on staging |
| Transcript re-accepted after a run | Panel warning stays; the run's pinned transcript is unchanged | existing |
| Source pending deletion | Refused before start | existing |
| Cancel mid model call | The in-flight call's reservation is fenced as unknown; nothing retries | existing ledger |
| Worker restart mid-run | Temporal retries the activity; idempotency keys prevent a second paid call; render resumes under the lease | existing |
| Route rejects a request | `route_admission_failed`, run ends with cost retained, message names route and stage | D2 |

### Scale

A 2.5-hour source: three full-source calls per iteration (inventory, author, source
review) plus one cold review per candidate; with three repairs, roughly 3 to 4 times
Karma's $0.65 to $1.49 per iteration, so a $20 budget can end `budget_paused` on a
long, heavily repaired run. Evidence build downloads the master (tens of GB) to the
topic worker's work volume; two lanes on one host means two cached masters. Check the
volume size before the first long run. `scdet` on Karma took 15.6 s; expect about a
minute on 2.5 hours. Rendering is CPU ffmpeg on the worker; measure the r11 render time
from its artifacts before promising a duration.

### Failure and time

| Step | Expected duration | Bound | Liveness | Retry |
| --- | --- | --- | --- | --- |
| Evidence (download, scdet, SaT, Silero, feasible grid) | minutes to tens of minutes on a long master | 6 h activity, 30 s heartbeat | heartbeat supervisor | reuse persisted evidence |
| Inventory, author, source review | 3 to 10 min each on long sources | D6 effective deadline, activity = deadline + 60 s | streaming chunks | never; unknown fence |
| Cold review per candidate | under a minute | same | same | never |
| Repair rounds | up to 3 | `HARNESS_MAX_REPAIRS` | — | — |
| Render | to measure | 12 h activity, 30 s heartbeat | heartbeat across decode, encode, checks | resume under lease |
| Deploy of `pipeline` during a run | container stops mid-activity | — | — | model call in flight becomes `outcome_unknown`; never deploy under a running topic run (existing rule) |

### User states

Every state has words and an action. A raw status string is not a state.

| State | Trigger | The user reads | Can do |
| --- | --- | --- | --- |
| Not available | `HARNESS_TOPICS_ENABLED` unset on web | "Topic videos are not enabled on this server." | nothing |
| Not ready | source or transcript not ready | "A ready transcript is required before topic discovery." | wait for ingest |
| Ready | otherwise | Button enabled; instructions optional | start |
| Queued, no worker | started, task queue has no pollers (`describeTaskQueue` from the web's Temporal client) | "Waiting for the topic worker." After two minutes: "No topic worker is running." | cancel |
| Running | activities progressing | Stage, dispatches, spend, and the author and reviewer routes this run froze | cancel |
| Videos ready for review | `needs_review`, complete | Video cards as today, plus which candidates the gate withheld and why | accept, reject, correct |
| Stopped with a limit | `needs_review`, `execution_limited` | The limit in words: repairs, dispatches, budget, a call that truncated, a route that refused | review what rendered; new run |
| Route refused | `route_admission_failed` | "Route X did not accept the inventory request (HTTP 404: parameter …). The snapshot needs a different route." | new run after the operator changes the snapshot |
| Source too large | admission refusal | "This source (240 KB of transcript) exceeds route X's window. Routes with a larger window: …" | none until the roster changes |
| Budget paused | `BudgetExceeded` | Existing message plus what was spent and what the next call would cost | cancel; new run with a larger budget |
| Outcome unknown | provider call without a confirmed outcome | "A provider call ended without a confirmed outcome. $X is reserved until reconciliation. Nothing is retried." (today this state has no message at all) | wait for reconciliation; cancel |
| Failed | any other error | The cause, never "stopped after a known activity failure" | new run |
| Cancelled / Accepted | user actions | as today | — |

### Operations

- Existing `pipeline` service: mount the topic snapshot beside the chapter one, set the
  D5 values, reload, then verify: running task, container env, boot log with the new
  snapshot ID, Temporal pollers on `temnia-pipeline` and `temnia-pipeline-control`.
- Web: matching `HARNESS_ROUTE_SNAPSHOT_ID` and limits, `HARNESS_CHAPTERS_ENABLED=0`,
  reload, `/api/health`, the button enabled on a ready source, chapter creation refused
  with the paused message.
- Wrong values are loud at boot (missing key, snapshot ID mismatch, route below the
  output ceiling), never a silent queue. A missing worker is visible in the panel.
- Disk: one work volume; check free space before the first long run.
- Cost of shipping this plan: no model calls. First staging runs: about $1 on Karma,
  $3 to $8 on World Order, declared before starting.

### Tenancy and security

Unchanged. Run rows, artifacts and media stay organization-scoped through `db.scoped()`
and the media proxy; the worker connects as `temnia_pipeline`; snapshot files are
platform configuration, not tenant data, and carry no secrets. The OpenRouter key lives
on `pipeline` only, never on `web`.

### Verification

- Python: delete the manifest tests; add tests for `route_admission_failed` at each
  stage with cost retained; author-stage `ContextWindowExceeded` handled like later
  stages; `admission/2` arithmetic; effective deadline and activity timeout per payload
  size; worker-owned config frozen in the run row and read by the workflow; multi-queue
  worker construction; reaper ensured on the pipeline queue only; `current_program`
  attached to web-started runs.
- Contracts: `brief` optional on `ChapterRunInput`, both drift checks.
- Web unit: the panel without a program selector; every state in the table rendered
  from fixture runs with its exact words.
- Playwright: the one-button journey on the production image through the gate's recorded
  worker; the chapter journey unchanged with the flag at its default.
- Gate: `pnpm ci:local` on the exact commit; `local-ci.mjs` no longer sets the removed
  flags.
- Staging: Karma first (expect `needs_review`, nine to eleven renders, about $1, the
  frozen routes visible on the run); then World Order (151 min) to measure D6, cost and
  render time. Record run IDs, settled cost, snapshot ID and detector in the log.

### Legacy checklist

| Item | Disposition |
| --- | --- |
| v1 lane (`standalone-topics/1`) | Hidden from new runs now; code deleted in the next PR after a staging query shows no non-terminal run |
| v2 lane (`standalone-topics/2`) | Hidden now; v3 contains it; deleted next PR; readers kept |
| Chapter lane | Parked on staging with one web flag; code, runs, review and export untouched |
| Qualification manifest gate | Replaced by in-run admission plus the ledger; identity hashes still recorded per attempt |
| `bind-topics`, manifest formats `/2` to `/5` | Deleted; `run` and `reconcile` kept as optional pre-flight |
| Per-version enable flags on web and worker | Dropped; worker boot is the gate |
| Web/worker config equality | Kept; one worker serves both values |
| Program dropdown | Dropped |
| Detector double default | Resolved: `scdet` in code and env |
| Two default briefs | Resolved: one in Python |
| Missing program manifest on web runs | Resolved |
| Decisions doc §2 (v2 staging qualification procedure) | Superseded by this plan |
| Qualification runbook | Rewritten as the optional route pre-flight |
| Experiment operator | Kept; manifest pin removed, snapshot and program pins kept |

## 4. Change list, one PR, in commit order

| # | Commit | Size |
| --- | --- | --- |
| 1 | docs: this plan, the AGENTS.md decision entry (§7), runbook rewrite, decisions doc §2 marked superseded, staging runbook for topic generation | S |
| 2 | contracts: `brief` optional on `ChapterRunInput`; regenerated schemas | S |
| 3 | harness: remove the manifest gate from boot and prepare; delete `bind-topics` and formats; specific stop messages including `outcome_unknown`; `scdet` default; single brief; program manifest on every topic run | M |
| 4 | web: one button, no program selector, no per-version flags; topic wording; `HARNESS_CHAPTERS_ENABLED` parks chapter creation; Playwright updated | M |
| 5 | harness: `admission/2` bytes-per-token floor; payload-scaled deadlines and activity timeout | M |
| 6 | local gate: flag cleanup | S |

Follow-up PR after merge: delete the v1/v2 producers once the staging query shows no
non-terminal run with policy `/1` or `/2`; then the program answer to the compound
candidate (§6).

## 5. Rollout on staging (Rajesh, Dokploy UI)

1. Check nothing is in flight on `pipeline` (chapter, topic, ingest, transcription).
2. Build the topic route snapshot: the r25 routes (recoverable from
   `configuration.routeSnapshot` in `/private/tmp/karma-r25-final-bundle.json`, snapshot
   `38d30301…`) minus Astra, verify pool Gemini first, propose pool Kimi then DeepSeek.
   The new file has a new ID. Copy it to the host as `/etc/temnia/topic-routes.json`,
   root-owned `0444`, and add the bind mount.
3. On `pipeline`: `HARNESS_GATEWAY=openrouter`, `HARNESS_ROUTE_SNAPSHOT_PATH` and `_ID`
   to the new file, `HARNESS_MAX_OUTPUT_TOKENS=32768`, `HARNESS_MAX_DISPATCHES=64`,
   `HARNESS_MAX_RUN_BUDGET_MICROS=20000000`, `HARNESS_TOPIC_SHOT_DETECTOR=scdet`; remove
   the four `HARNESS_TOPIC_SELECTION_*` entries if present. Reload. Read the boot log.
4. On `web`: the same `HARNESS_ROUTE_SNAPSHOT_ID` and limits, `HARNESS_CHAPTERS_ENABLED=0`.
   Reload, check `/api/health`.
5. Run Karma. Record run ID, routes, cost, render count, and review the videos.
6. Run World Order. Record the same plus the measured full-source call durations against
   D6.

## 6. What this does not decide, and what comes next

- No author or reviewer winner. The roster is the last experiment snapshot because it
  finished runs, not because it scored.
- No editorial acceptance. The compound-candidate gap (decisions doc §7b, §9) stands: a
  span both models call one topic has no seam for the handoff contract to see. The
  program answer to design next, before any further model arm: a code-side source map
  (speaker turns, question sentences, KernelCPD change points from the penalty curve, §8)
  supplied to the inventory as navigation hypotheses and to the source reviewer as
  citeable seam facts, with a required per-candidate seam judgment for every strong
  change point inside a candidate. Measured on the R5 calibration set first.
- Then, in the order the decisions doc §6 already set: R5 calibration set, the prompt
  and `.describe()` rewrite, A2 span union and P5 local edge inspection, acoustic edge
  evidence, the listening judge.
- The chapter lane is untouched.

## 7. Decision record (added to AGENTS.md on 13 September)

See the 2026-09-13 entry in `AGENTS.md`. It supersedes the 2026-09-11 four-schema
qualification requirement and the decisions doc §2 procedure.
