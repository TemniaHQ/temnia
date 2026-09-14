# Pipeline architecture review, 2026-09-14

A principal-architect read of the whole pipeline as it stands on `main` at `b5433fd` (PR #45
merged), written the evening after the first staging run that survived every failure mode we had
seen this week. It says what is right, what is wrong, how bad each wrong thing is, and what to do
in what order. The companion plan for the editorial loop is
[harness-loop-redesign-360-view.md](../plans/harness-loop-redesign-360-view.md).

## 1. Provenance and limits

Read: every module under `apps/pipeline/src/temnia_pipeline` (6.7k lines outside the harness,
20.9k in it, 2.8k media), the contracts package, the Drizzle schema and migrations, the web
server actions and harness libraries, the Dokploy creation script, both GitHub workflows,
`docs/tech-stack.md`, the 2026-09-07 harness review, and every decision in `AGENTS.md`.

Ran and observed today: run `f7396b5e` on Karma end to end (evidence, a rate-limited first
call, a seat fallback, parallel cold reviews, an admitted repair, re-review, nine CPU renders),
the bundle exports of runs `a562673b` and `f7396b5e`, the model responses of both from the
object store, the Dokploy deployments of #44 and #45, and the worker boot and activity logs.

Not done: load testing, a second source, a source longer than 44 minutes, a security review of
the web tier, and re-verification of every open item recorded on 2026-09-12. Items from that
review that I did re-check today are marked as such.

## 2. The system in one page

| Component | What it is | Where |
| --- | --- | --- |
| Web | Next.js 16, server actions start and describe Temporal workflows, the topic panel reads the run's artifacts from Postgres and refreshes on a button | `apps/web` (91 TS files) |
| Contracts | Zod schemas are the single source; `contracts.py` is generated and drift-checked | `packages/contracts` (2.0k lines), `contracts.py` (1.5k) |
| Database | Drizzle owns DDL and row-level security; 18 tables; the pipeline role is DML-only inside org-scoped transactions | `packages/db`, 6 migrations |
| Durable runtime | Self-hosted Temporal on the VPS, one Python worker process with two task queues (main: 2 concurrent activities, control: 4), a reaper schedule | `worker.py`, `workflows.py` |
| Ingest lane | `IngestWorkflow`: claim, probe, transcode (Modal), derive, measure sensors, finalize; child `TranscribeWorkflow` with checkpointed speech (Modal `temnia-speech/2`) | `ingest.py`, `workflows.py`, `speech/` |
| Editorial lane | `TopicSelectionWorkflow` (`standalone-topics/3`): evidence, rubric, inventory, author, cold reviews, source review, bounded repair, compile, render, checks | `harness/topic_selection_workflow.py` + 40 harness modules |
| Model transport | OpenRouter through an auditable gateway seam; seat pools with three families; per-route gate; receipts and reconciliation | `harness/gateway.py`, `models.py`, `routes.py`, `ledger.py` |
| Money | Reservations before dispatch, settlement per attempt, unknown-outcome fencing, automatic receipt reconciliation, run budgets | `harness/ledger.py` (1.6k), `runs.py` |
| GPU | Modal app `temnia-media` (ladder, transcription, `render_sections`), deployed from `main` by GitHub Actions | `modal_app.py`, `render_remote.py` |
| Storage | R2 on staging, Garage locally, through obstore; every artifact content-addressed with size and hash verified on read | `storage.py`, `harness/artifacts.py` |
| Delivery | Local exact-commit gate (install, lint, types, 1.2k Python tests, 198 web, 41 db, 33 contracts, both images, 18 browser journeys) attested by a GitHub status; Dokploy deploys both images on push to `main` | `scripts/local-ci.mjs`, `.github/workflows` |

## 3. What is right

These are the load-bearing decisions, and today's run is the evidence that they hold.

- **The program is typed and durable.** Every stage is an activity with a contract on both
  sides; the workflow owns every paid retry; a stopped run resumes from retained artifacts under a
  new execution. Today: a rate-limited first call, two 429s on the author seat, one failed cold
  review, and the run finished with nine videos for $0.72.
- **Money is a first-class type.** Reservation before dispatch, settlement per attempt, receipts
  fetched from the gateway, unknown outcomes fenced and reconciled. We can say to the cent what a
  run cost and what a failure did not cost. Most agent stacks cannot.
- **Authority is a validator, not a prompt.** A model proposes; code decides whether the proposal
  had the right to change what it changed. This is why a wrong repair costs a re-run and never a
  wrong video.
- **Evidence is measured once, at ingest, bound to the object's identity.** A run downloads
  nothing until it renders (from #44). The first run on an old source still pays the sensors once.
- **Provider neutrality is real.** Three families per seat, fallback with the provider's pause,
  a per-route admission gate. Kimi to DeepSeek happened today without anyone noticing.
- **Artifacts are immutable and dependency-linked.** Raw model output is never edited; a
  rejection binds to the exact inputs it rejected; exports are reproducible.
- **Tenancy is in the database from day one.** Row-level security with org-scoped transactions,
  including the reaper's declared cross-org policy. A plain connection reads nothing, which is
  the correct failure.
- **The delivery gate attests the exact commit.** No push without a receipt for that SHA; the
  provenance check is the only cloud CI and it is enough for one contributor.
- **Decisions are written down the day they are made.** Sixty entries in `AGENTS.md`; every
  reversal cites the earlier entry.

## 4. Findings, ranked

Severity is product impact first, operational risk second. Each finding names its evidence and
its remedy. Findings 1 to 3 are the subject of the companion plan.

### F1. No seat judges a clip's edges against the source (high)

Video 1 of today's run opens at 3:09 on "But I would love to know", after the host has spent
thirty seconds stating the premise the guest then corrects. The inventory chose the tightest
intelligible start, the author kept it, the cold reviewer cannot see earlier speech, and the
source reviewer only compares adjacent candidates with each other. Nothing asks whether the
answer responds to something the clip left out. Remedy: plan W1.

### F2. Repair is one atomic patch per iteration (high)

Both refused repairs this week discarded every good operation with the bad one. #42 and #45
narrowed the causes, and #45 added a bounded correction, but the shape remains: one model, one
patch, all or nothing, three iterations. Remedy: plan W3, one patch per finding group.

### F3. Prompt text and validators are two sources of truth (high, re-checked today)

Both refusals this week were the prompt describing an operation one way and the validator
another. The 2026-09-12 review noted prompts are user-turn rule lists with no `instructions=`
and no `.describe()` on contract fields; both are still true today (zero occurrences of either).
Remedy: describe every operation kind and every finding kind on the contract, generate the
prompt's operation section from those descriptions, and add a test that every refusal message a
validator can emit is covered by a rule the prompt states. This is cheap and closes a class of
bug.

### F4. Long sources are out of reach (high for a coverage product)

Admission is byte-as-token with no topic hierarchy; a two-hour source is refused; the full-source
review is one call with a 540 s transport ceiling that two routes already hit on a 44-minute
source (recorded 2026-09-12, not re-measured). Temnia's positioning is the whole episode. Remedy:
plan W4 changes the author's input from the full transcript to an index plus tools, which is the
structural fix; the source review needs a windowed form for the same reason.

### F5. One box runs everything (high operational)

Web, worker, Temporal server and its Postgres, and the application Postgres share one VPS. The
worker allows two concurrent activities on the main queue, so a long render or evidence build
delays every other run. A host fault takes the control plane down with the workers. Acceptable
for staging with one user; not for launch. Remedy: before launch, Temporal Cloud Essentials (the
M3 evaluation already recorded) or a second box for the Temporal stack; a worker per lane
(ingest, editorial, control) with its own concurrency; renders moved to Modal with #45's merge (the deploy workflow's first successful run was today).

### F6. No tracing, no metrics, one log stream (medium operational)

Langfuse over OpenTelemetry was decided at S3 and is not wired (no reference in the pipeline).
Stage timing is recoverable only from a bundle export; the worker log is `httpx` lines; the
render fallback logs once per candidate. Today I had to describe the workflow from inside the
container to know what it was doing. Remedy: emit one structured event per stage transition and
per attempt settlement (already known to the ledger) and ship them; wire the PydanticAI
instrumentation to the chosen sink; log the fallback once per run.

### F7. Latency is dominated by serial model calls (medium)

Thirty-four minutes from click to videos today: about three of evidence, about five of render,
and the rest model calls in sequence, including one author call that took nearly ten minutes on
the fallback route. Cold reviews already fan out. Remedy: W3 fans out repair; W4 shortens the
author's input; a per-seat latency budget that moves to the next route when a call exceeds it
(the transport ceiling exists, at 540 s, and is too long for an author seat with a cheaper
alternative).

### F8. The gateway is a single point of failure (medium)

Every seat reaches its provider through OpenRouter. The 2026-09-11 decision calls OpenRouter an
audition transport; it is now the production transport by default. Vercel AI Gateway was the
implemented candidate. Remedy: keep the seam, qualify the second gateway on the same snapshot,
and let a route name its gateway so a pool can span both.

### F9. Human judgment is collected and discarded (medium)

Every accept and reject in the panel carries a reason and lands in a table. Nothing reads it.
There is no calibration set, so a prompt change is judged by one run and one pair of eyes.
Remedy: plan W2.

### F10. The gate runs on one laptop (medium)

The exact-commit gate is thorough and slow (about 35 minutes, Docker-heavy) and it is the only
place the tests run. It refuses a receipt if HEAD moves during the run, which cost one cycle
today. Remedy: run the same script in GitHub Actions on `main` after merge, nightly, so a broken
main is noticed without a laptop; keep the local gate as the push guard.

### F11. Web state is pulled, not pushed (low)

The panel refreshes on a button and shows "Waiting for the durable run record" after the record
exists. Fine for one reviewer; wrong for a product where a run is half an hour long. Remedy: a
run-progress query (stage, calls, spend) served from the run row on an interval, and a stage
timeline in the panel; no new infrastructure.

### F12. Naming and module debt (low)

Chapters are gone, and the topic path still speaks of them: `ChapterRunInput`,
`claim_chapter_repair`, `accept_initial_chapter_revision`, `chapter_revision`. Four harness
modules exceed 1,200 lines; `topic_selection.py` holds prompts, validators and normalizers in
one file. Remedy: rename at the next contract change; split prompts, validators and normalizers
into three modules with no behaviour change, behind the existing tests.

### F13. Configuration lives in two images (low)

The deployment file is copied into both images; the web reads it for its schema and the worker
for its behaviour. #45 made the web rebuild when it changes. The remaining risk is a config that
one image accepts and the other refuses. Remedy: the web should read the effective configuration
from the worker (a query on the run or a health endpoint), not from its own copy.

### F14. Rendering is dispatched per video, and the master is downloaded twice (medium)

`render_topic_revision` loops over the revision's videos and calls the render path once per
video, so a run with nine videos makes nine Modal calls, each downloading the 121 MB master and
paying its own cold start; the 09:44 run logged the CPU fallback nine times for the same reason.
Before any of that, the worker downloads the master to the VPS to re-verify its hash, timeline
and fingerprint, although the ingest-time `source-timeline/1` record and an object head already
prove the same identity, which is exactly how the evidence stage avoids the download. Remedy:
one render job per revision carrying every missing section (the job contract already allows it),
and identity from the ingest record and the object head, with the download only for the CPU
fallback.

### F15. Physical boundary constraints that the repair loop cannot resolve (medium)

The compiler emits `physical_boundary_constraint` when no cut on the output grid preserves the
selected words without crossing aligned or detected speech. It is a required finding, so the
candidate is withheld, and the only remedy is a model repair that widens the edge, competing for
the three-repair allowance with editorial findings. In the 11:00 run two candidates were withheld
on this alone after the allowance was spent. The correct edge is computable: the nearest grounded
cut outside the selected words. Remedy: resolve physical-only findings in code before any model
repair, as a deterministic edge extension within the candidate's authority, and reserve model
repairs for editorial findings. Plan W3 groups findings by candidate; physical-only groups should
never reach a model.

## 5. Things I looked for and did not find wrong

Secrets are not in images or logs; the Dokploy API output is never printed by our tooling.
Artifact reads verify size and hash. The reaper is storage-first and idempotent. Migrations are
few and forward-only. The route snapshot is content-addressed and frozen per run. The
`local-ci` provenance check rejects a status not backed by a receipt. The three Modal apps are
all live (media, speech v2, and the speech benchmark tooling has tests and a script).

## 6. Verdict

The foundation is right and it is the part that is hard to get right later: a durable typed
program, money as a type, authority in code, immutable evidence, tenancy in the database. What is
wrong is concentrated in the editorial loop's judgment and shape (F1 to F4, F9) and in the
single-box operations (F5, F6). None of the frameworks discussed this week address the first
group; the second is money and a week of work.

Order: F3 and F1 first (both are small, both change what the loop optimises for), W2 alongside
(so the next change is measured), then F2 and F4 through W3 and W4, with F15 folded into W3 and
F14 as its own small change, then F6 and F5 before the first outside user. F8, F10 to F13 are
hygiene to schedule, not to block on.
