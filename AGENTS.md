# Temnia — repo rules

## Decisions

**2026-09-03 — Legacy docs are inspiration, not inheritance.** `docs/prd.md` is the
only document carried over from `Mitosia/mitosia-legacy` and is the product's
source of truth for *what* it does. The legacy `tech-stack.md`, `sprint-plan.md`,
`pipeline-architecture.md`, `pipeline-implementation-plan.md`,
`clip-cut-architecture.md`, `episode-to-clips.md`, `clipping-landscape.md`, and
`editor-study.md` are deliberately **not** copied here. The new tech stack, sprint
plan, and pipeline architecture get decided fresh for the monorepo, consulting the
legacy files at `../mitosia-legacy/docs/` for reference when a specific question
comes up. The PRD's header links to those siblings; they will not resolve locally
until the corresponding new document is written.

**2026-09-03 — Build in public is part of the workflow.** Temnia (Mitosia until the 2026-09-03 rename) is rebuilt in public
with a daily post on X and LinkedIn. The strategy is `docs/build-in-public.md`. Every
working session ends by writing `docs/log/YYYY-MM-DD.md` (done / how / went wrong /
numbers / tomorrow / X draft / LinkedIn draft) from what actually happened — real
numbers, dated, no embellishment. Claude drafts; Rajesh edits and posts. The log is
committed with the day's work and is the source of truth for the post.

**2026-09-03 — The product is Temnia, not Mitosia.** The IP India search found an identical
`Mitosia` mark in class 42 (appl. 7804137, filed 20/06/2026, prior use claimed from
10/10/2024) held by a design studio; we were the junior party on every axis and chose to
rename rather than fight. **Temnia** (Greek *temnō*, "I cut" — the root of *atom* and
*epitome*) cleared IP India phonetic and Start-With in classes 42 and 9, USPTO, and WIPO the
same day. Record: `docs/naming.md`, `docs/trademark-filing.md` §8. Use "Temnia" in all new
docs, code, and posts; "Mitosia" survives only in historical records and the legacy repo name.
The GitHub org/repo were recreated under the Temnia name on 2026-09-03; the local directory
and Claude memory key may still say `mitosia` — treat that as a pending mechanical task, not
a naming ambiguity.

**2026-09-04 — Positioning leads with the coverage lane, not moments.** Temnia's public story is
cutting the whole long-form source into correct chapters and segments (the exact-cover coverage
lane, PRD §9), not mining it for short highlight clips. Competitors are content with imperfect
moment clips because a highlight is forgiving; a chapter partition is not (every second in exactly
one chapter or a deliberate drop, one cut point per shared silence, a wrong boundary breaks both
neighbours), and that strictness is the reason the harness design exists. Moments remain a lane and
are described as what hangs off the chapter spine. Rajesh's call during the Day 0 draft review.
Every post, the landing page, and the pitch shape in `docs/build-in-public.md` §6b follow it. PRD
§1.2 still lists the coverage lane last in the clipping-tools bullet and should lead with it at the
next PRD revision.

**2026-09-05 — Legacy is reference only; every build decision gets fresh research.** The archived
`Mitosia/mitosia-legacy` codebase and its docs (`../mitosia-legacy/`) are a record of what was tried
and what it cost, not a default to inherit. When building anything here — choosing a library, a
service, a runtime, a schema shape, a convention, or porting a behaviour — do the research as if the
legacy choice did not exist: read the current documentation and versions, look at the alternatives
that exist today, and decide on today's merits. Then consult the legacy for its *lessons* (incidents,
measured numbers, options rejected and why) and carry those forward as evidence, not as the answer. A
legacy pick may well win again; it wins because the fresh comparison said so, and the doc or PR that
adopts it states what was compared. "The legacy did it this way" is never sufficient justification on
its own, and a legacy decision recorded with a reason still gets re-examined, because the reason may
no longer hold (versions moved, the product changed, the constraint disappeared). This strengthens
the 2026-09-03 "inspiration, not inheritance" rule from docs to every decision. Rajesh's rule,
2026-09-05, after the first tech-stack proposal carried legacy picks forward by default.

**2026-09-05 — Sequence: the editing core first, then the SaaS stitching; identity deferred behind a
stubbed scope.** `docs/sprint-plan.md` is the sequence record. Rajesh's direction: build every editing
feature (both clip lanes, the studio, rendering, every harness lane in PRD §10, source groups) and
dogfood it before any multi-tenant SaaS surface (identity, hierarchy, approvals, portal, publishing,
billing). Gates: M0 walking skeleton (after S2), M1 chapter cut (S5), M2 editing core complete (S20),
M3 private alpha (S27), M4 beta (S31), M5 GA (S37). Identity (Better Auth) lands at S24. Until then
every table is organization-scoped with forced RLS from its first migration, the scope resolver is
one function returning a seeded Temnia organization and user, staging sits behind Cloudflare Access,
and `organization` and `user` are created at S1 in Better Auth's column shape so S24 is additive
(verified 2026-09-05 against the current docs: the CLI diffs an existing database and both tables
accept custom fields). Any access path that does not go through the resolver is a bug. Identity can
be pulled forward any time a second person needs an account; the seam is one function.

**2026-09-05 — Stack: one durable runtime, media in Python, one schema owner.** Rajesh accepted the
three structural decisions the monorepo forces, argued from the same day's research (session record
in `docs/log/2026-09-05.md`; the full per-slot rationale lands in `docs/tech-stack.md` at S0).
1. **Temporal is the only durable runtime.** Workers are polyglot by task queue: Python for everything
   media and editorial; a TypeScript worker arrives at S9 for Remotion renders and later for publishing
   adapters. Next.js is a Temporal client (start, query, signal) and makes no model calls of its own.
   Trigger.dev, Mastra, and the AI SDK do not come across. Basis: Worker Versioning went GA in March
   2026 and answers the legacy's open worry (its DECISIONS entry 6) about deploys during days-long
   review waits; task-queue fairness with per-key rate limits went GA in May 2026 and covers per-client
   queues; async Python activities heartbeat while awaiting a subprocess. Runner-up was Hatchet.
   Trigger.dev runs Python only as scripts inside TypeScript tasks and its self-host drops checkpointed
   waits. Self-hosted through M3 on a pinned server version upgraded sequentially on a schedule;
   Temporal Cloud Essentials is evaluated at M3.
2. **Media and ingest are Python.** Every ffmpeg string lives in the pipeline package's render and
   sensor wrappers, beside the harness sensors, shot and peaks generation, and Modal GPU calls. PyAV 18
   wheels bundle the ffmpeg 8.1.2 libraries; the CLI still comes from the mirrored static build. ffmpeg
   9.0 shipped on 2026-08-04, so the 8.1 pin is re-examined in the pipeline architecture document at
   S3, not inherited.
3. **Drizzle owns all DDL, RLS, and the isolation suite; there is no second migration tool.** Policies
   are declared on the table with `pgPolicy`, so drizzle-kit emits the policy and forced RLS in the
   migration that creates the table. Python is DML-only: its models are derived from the migrated
   database (sqlacodegen 4.x is the researched default) as a Turborepo task, and CI fails on drift.
   Write boundaries are role grants for the app role and the pipeline role, not a tool boundary.
   Drizzle stays on the 0.45 line until Better Auth's adapter supports 1.0 (issues open 2026-09-05).
Scaffold consequences: the pipeline is a uv workspace member behind Turborepo's
`experimentalPythonWorkspaces` flag rather than a package.json shim; `@temporalio/client` is listed in
Next.js `serverExternalPackages`.

**2026-09-05 — Icons stay Hugeicons; transcription is WhisperX on Modal from S2.** On reviewing the
tech-stack research (`docs/tech-stack.md`), Rajesh made two slot decisions. (1) **Hugeicons** stays
because the icons look better; the cost is accepted: shadcn's Base UI registry output imports Lucide,
so each vendored component is swapped with the official migration tool. (2) **WhisperX** (large-v3,
wav2vec2 alignment, pyannote diarization) running on **Modal** is the transcription engine from the
first S2 run, behind the provider seam with a deterministic mock. Basis: the independent March 2026
benchmark on podcasts and interviews put WhisperX ahead of both hosted providers on word error and
diarization; an L4 handles large-v3 with alignment and diarization at roughly twenty audio-hours per
GPU-hour. Consequences: the sprint plan's S12 is a calibration round (model size, VAD, alignment,
diarization settings against the sentence grid), not an ownership A/B; a hosted adapter (AssemblyAI
Universal-3.5 Pro is the candidate) is built only as fallback if the bar is missed; PRD §6's
Deepgram-primary line is retired at its next revision. Known trap: whisperx 3.8.5 wheels still carry a
`use_auth_token` path that breaks against pyannote 4.x (m-bain/whisperX#1406), so pin pyannote 3.x or
patch until a fixed wheel ships. The model gateway is still decided by the S3 transport probe; its
catalogue coverage for Kimi K3, GLM, Nano Banana, and the Seedance, Veo, Kling, and Wan video models is
recorded in `docs/tech-stack.md` §9.

**2026-09-05 — No default model vendor.** Rajesh: Temnia does not stick to Claude models. Every seat's
pool is drawn from at least three model families, probed for transport at S3 (ZDR, strict schema, full
request shape) and auditioned per seat at S4 on cost-per-correct with reasoning tokens included; the
verifier seat is always a different family from the generator, so production runs at least two
vendors by construction. No model or vendor is a default in config or in docs; a first slot is won by
audition and changed only by audition. The gateway's live models endpoint is the roster; the family
list in `docs/tech-stack.md` §9 is the starting roster on the research date, never a routing table.
Claude Fable 5.1 is excluded by policy, not preference, because it is not served under zero data
retention. The legacy's August audition result is evidence about a field that has moved and is re-run.

**2026-09-05 — `docs/tech-stack.md` v1.0 is the system-design record.** Accepted by Rajesh after every
slot was researched under the same day's rule. Together with `docs/prd.md` (what) and
`docs/sprint-plan.md` (sequence) it is one of the three documents a session reads before
architectural work. Every slot in it is the default for its sprint until one of its §13 revisit
triggers fires or the sprint's build list re-researches it; a change is made in the PR that acts on
it and states what was compared. Decisions already carrying their own dated entries above (runtime,
media language, schema owner, icons, transcription, no default vendor) are not re-argued through it.
