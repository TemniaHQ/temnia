# S3 slice A — the harness chassis: implementation spec

Written 2026-09-07 with the harness design (the sprint-plan S3 row and the AGENTS.md decision of
that date carry the design); amended 2026-09-08 after the S2 review (§ Amendments below, and the
AGENTS.md decision of 2026-09-08). Branch: a new branch off main (`feat/s3-harness`). Inputs: the S2
substrate (`temnia_pipeline.substrate`, `temnia_pipeline.evals`) and the hardened S2 transcript
path (`docs/plans/s2-hardening-360-view.md`). Rules: AGENTS.md, including "no default vendor",
"the PRD is not a hard requirement", and "design from the Python ecosystem; where a library does it
better than this spec assumed, take it and say so".

## Goal of slice A

A run of the chapter lane executes end to end on the recorded provider's transcript with test
models, with every property the design promises already true: typed stages, idempotency-keyed
calls with a checkpoint table, seats with pools and the failover-halt rule, a per-run budget
checked before every call, zero repeated calls after a kill, cassettes in CI, tracing wired, and
the substrate persisted as a versioned evidence artefact. No real model is called in CI. The
transport probe is a command Rajesh runs once the gateway key exists; its report seats the roster.

## Decisions (do not re-open)

- **Package** `apps/pipeline/src/temnia_pipeline/harness/` with `seats.py`, `budget.py`,
  `calls.py`, `models.py`, `validators.py`, `registry.py`, `evidence.py`, `compiler.py`,
  `stages/{brief,propose,cut,verify,explain}.py`, `workflow.py`, `cli.py`. Contracts for the
  evidence record, the plan, the edit specification, and the stage outputs live in
  `packages/contracts/src/harness.ts` (Zod) and are regenerated into `contracts.py` as before, so
  the cutting room (S5) reads the same shapes.
- **Tables** (Drizzle, forced RLS, isolation probes): `harness_run` (organization, source,
  transcript revision, evidence version, lane `chapters | moments`, status `running | ready |
  partial | failed`, budget and spent in micro-dollars, seat snapshot jsonb, program versions
  jsonb, error, timestamps) and `llm_call` (organization, run, seat, stage, idempotency key,
  models attempted jsonb, model used, family, request hash, response storage key, input, output,
  cache-read and cache-write tokens, cost micro-dollars, latency ms, status `ok | failed |
  replayed`, error, created). Response bodies go to storage under the source prefix
  (`runs/{run}/calls/{key}.json`), never into Postgres. `usage_kind` gains `ai_tokens` with the
  cost in `detail`. Every row a retry can repeat carries the run's identity and is fenced on it
  (the S2 hardening rule).
- **Idempotency.** `calls.py` computes the key as sha256 of (transcript revision, evidence version,
  stage, program version, seat model, params, input hash). The model-call activity looks the key up
  first; an `ok` row returns the stored response with status `replayed` recorded on the run, and
  no model is called. A call whose response was received but whose row was not committed is an
  `outcome_unknown` row settled on the next attempt, never a silent second dispatch. That is the
  chaos test's mechanism; the test below proves it.
- **PydanticAI** for every model call: one `Agent` per stage with `output_type` set to the stage's
  Pydantic model (structured output; the JSON schema is also validated by our code after the
  call), instructions loaded from `registry.py` (`prompts/{stage}/{version}.md`, with an optional
  per-seat-model variant `{version}.{model-slug}.json` in the shape DSPy saves, for S4's
  optimisation to fill), `instrument=True` with `include_content=False` outside tests. Wrap each
  agent with `TemporalDurability` so requests are activities with the activity config from
  `workflow.py`. Provider: `models.py` builds an OpenAI-compatible model against
  `GATEWAY_BASE_URL` with `GATEWAY_API_KEY` for any seat model string of the form
  `gateway:<vendor>/<model>`; a `test:` prefix returns `TestModel`; `cassette:` wraps a real model
  with the recorder below. Provider options for zero-data-retention and caching are passed through
  as settings, never relied on. Disable the SDK's own transport retries; Temporal owns retries.
- **Seats** (`seats.py`): a `Seat` has a name, a role (`proposer | cutter | verifier | explainer |
  judge`), and an ordered pool of `SeatModel(model, family, params)`. The roster file
  `harness/roster.py` starts with placeholders in three families for every seat and a comment that
  the transport probe and the S4 audition fill it; in dev and CI every pool is `test:` models. Rules
  as pure code, unit tested: the verifier's and judge's family is excluded from the proposer's
  family for the run; failover walks the pool in order; after two independent models fail the same
  named local validation the run halts (`status = failed`, reason recorded) and nothing else is
  dispatched; dispatch, repair, and revision ceilings per run.
- **Budget** (`budget.py`): per-run budget = `HARNESS_BUDGET_MICROS_PER_SOURCE_HOUR` times the
  source's hours; a price table per seat model (micro-dollars per million input, output, cache
  read, cache write) in `roster.py`, updated by the probe; the estimated cost of the next call
  (prompt token count from the evidence rendering size plus the stage's output allowance) is
  checked before dispatch; a breach completes the run with partials (`status = partial`) and
  records which stage stopped. Actual cost from usage is written per call and summed on the run.
- **Evidence** (`evidence.py`): the substrate is built once per (transcript revision, segmenter
  configuration, model revisions) and persisted as an immutable artefact under the source prefix
  (`evidence/{version}.json`) with a row in `harness_run`'s `evidence version`; every downstream
  artefact names the version it consumed. Version one carries the S2 layers: words in lexical order
  with timing and its flag, sentences, paragraphs, turns, ranked change-point candidates, and the
  model revisions and parameters that produced them. Speech activity and shot boundaries join as
  further layers when the cut stage needs them (S4), keyed the same way.
- **Stages** (`stages/`): each is `async def run(ctx: StageContext, input) -> output` with a typed
  input and output; the LLM call is the only side effect, and a stage may make one call, several,
  or none. `brief` reads the coarse rendering (cached prefix candidate) and returns topics, arcs,
  speakers. `propose` returns chapters as an exact cover in sentence ids with a reason each, given
  the brief and the evidence's ranked candidates as hints; `validators.exact_cover` repairs trivial
  gaps and rejects overlaps. `cut` is the joint boundary compiler (`compiler.py`): every proposed
  boundary has a window of candidate cut points from the evidence (sentence starts, turn starts,
  pause midpoints, later shots), each carrying its semantic fit (the model's per-window answer on a
  fine rendering, one call per window on a cheaper seat), its acoustic clearance, and the lane's
  constraints; a transparent dynamic-programming selection over the monotonic candidate graph
  chooses neighbouring boundaries together, and the C2 backstops (two-turn lead-in, lead-out trim,
  pause air, shot snap) are terms of that objective rather than a pass after it. Where no candidate
  in a window is safe, the compiler widens the window or marks the boundary for review; it never
  manufactures a time. `verify` runs the code validators first (exact cover, duration bands as
  flags, verbatim grounding of every quoted line, count outliers as flags), then the judge seat with
  a 0-1-2 rubric and one fix from a fixed vocabulary, at most one repair round; at S4 the judge
  receives the rendered artefact, not only the plan. `explain` returns a title and one grounded
  quote per chapter, checked verbatim against the sentence text.
- **Workflow** (`workflow.py`): `ChapterRunWorkflow(run request)`: claim run → evidence (build or
  reuse the persisted version) → brief → propose → cut (fan-out activities, concurrency cap from
  settings) → compile → verify → explain → persist the plan (storage JSON + `harness_run` ready)
  → ledger. Failure classes mirror ingest: transport errors retry under Temporal; validation and
  budget outcomes are terminal decisions of the router, never retried. The run heartbeats per
  stage; timeouts per activity like ingest's.
- **Cassettes** (`models.py` `CassetteModel`): wraps any PydanticAI model; keyed by the request
  hash; `record` mode writes `tests/cassettes/{stage}/{hash}.json`, `replay` mode reads and fails
  loudly on a miss. CI runs replay only. Until a gateway key exists, the committed "cassettes" for
  the recorded transcript are produced by `FunctionModel` stand-ins that derive plausible outputs
  from the evidence (chapters at the change-point candidates, cuts at the candidate sentence,
  verdicts of 2, titles from the first sentence), marked as synthetic in a top-level field.
- **Transport probe** (`cli.py` `temnia-harness probe`): for every roster model, the full request
  shape (system, a 40k-token cached prefix from a real fixture rendering, JSON-schema output, ZDR
  option), twice, asserting schema-valid output, and recording latency, tokens, cost, and whether
  the second call hit the cache; writes `docs/audition/probe-<date>.md`. Needs `GATEWAY_*` env.
- **Tracing**: PydanticAI's OpenTelemetry instrumentation with the OTLP exporter from env
  (`OTEL_EXPORTER_OTLP_ENDPOINT`); no Langfuse deployment in this slice (an ops item with its own
  measurement), but the spans must carry run id, seat, stage, model, and cost as attributes and no
  content in production.
- **CLI** `temnia-harness run <source-id> [--lane chapters] [--segmenter changepoint]` starts the
  workflow; `temnia-harness show <run-id>` prints the plan and the call ledger. The web trigger
  arrives with S5.
- **Docs**: `docs/pipeline-architecture.md` written from the design and the amendments (the S3
  deliverable); runbook env lines; `.env.example`.

## Tests

- Router rules, budget rules, idempotency key stability, the `outcome_unknown` settlement,
  cassette record and replay, validators (exact cover, grounding, bands), the compiler (a boundary
  with no safe candidate is widened then flagged; neighbours move together; the objective is a
  pure function with a hand-computed example), each stage with `TestModel` and with the stand-ins.
- The chaos test with Temporal's test environment: start a run with the stand-ins, kill the worker
  after `cut` has completed half its windows, restart, and assert the run finishes with the number
  of model calls equal to the number of distinct keys and every replay marked `replayed`.
- Evidence: the same transcript revision and configuration produce the same version; a correction
  produces a new one; a run names the version it consumed.
- Isolation probes for both tables; schema contract test updated.
- Same tools and rules as S2: pytest, ruff, pyright strict, contract drift checks, lint,
  typecheck; small commits with the trailer; the gate on the final SHA.

## Amendments (2026-09-08, after the S2 review)

What changed against the 2026-09-07 spec, and why; the AGENTS.md decision of the same date is the
record.

1. **Evidence is a persisted, versioned artefact** (review finding I17). The substrate was a library
   output consumed in-process; the harness's idempotency keys and the cutting room's stable ids
   both need it to exist under a version. `evidence.py` and the `evidence version` column above.
2. **The cut stage is a joint compiler, not per-clip trimming** (review §5.3). The 2026-09-07 spec
   answered "delimits badly" with per-boundary model calls followed by code backstops. Independent
   per-boundary answers cannot keep a partition consistent; the compiler chooses neighbouring
   boundaries together and the backstops are objective terms. The model's per-window answer stays,
   as one input.
3. **Five artefacts, calls measured** (review §8). The stage contracts are unchanged; the
   assumption that each stage is one model call is dropped. A stage that needs no call (an
   `explain` derived from stored decisions) makes none.
4. **Outcome uncertainty is a state** (review §7.4). A response received but not committed is
   `outcome_unknown`, settled before any redispatch; "zero repeated calls" is claimed for committed
   results, and unresolved spend is bounded and reported.
5. **The annotated corpus gates S4.** Acceptable boundary windows on the three recordings exist
   before the compiler's objective is tuned; the sprint-plan S4 row carries the gate.
6. **Deferred, with reasons**: Remotion and the TypeScript composition worker (S9, when a lane needs
   graphics; licence cost); OpenTimelineIO (no request); budget reservations beyond the pre-call
   check (no parallel dispatch in slice A); the twelve-lane specification fields (version one
   carries chapters and moments); speech-engine re-audition (after slice D's measured WhisperX
   baseline); phrase search and delete/split/merge edits (product work on the transcript tab,
   scheduled with the cutting room).
