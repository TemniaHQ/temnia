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
Scaffold consequences: the pipeline joins Turborepo through a `package.json` shim whose scripts call
`uv run` (native uv workspaces arrive when `experimentalPythonWorkspaces` leaves canary; corrected at S0,
see `docs/tech-stack.md` §0 row 13); `@temporalio/client` is listed in Next.js `serverExternalPackages`.

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

**2026-09-06 — Infrastructure lives on `temnia.dev`; the product lives on `temnia.com`.** Rajesh
registered `temnia.dev` as the infra domain: `dokploy.temnia.dev`, `staging.temnia.dev`, and
`temporal.temnia.dev`, all behind the Cloudflare Tunnel and Cloudflare Access. `temnia.com` carries
only product and marketing hostnames and takes design partners' custom domains through Cloudflare for
SaaS at S27. Why separate: certificate transparency logs publish every hostname a certificate is issued
for, so infra names on the product domain advertise the control plane; cookies scoped to the product's
registrable domain cannot leak between staging and production; and the product zone stays clean for
custom hostnames. The staging VPS is reinstalled from scratch for this (runbook `docs/runbooks/staging.md`).

**2026-09-06 — S1 build decisions (each researched fresh; the comparison is in the PR that
adopted it, and the per-slot record in `docs/tech-stack.md` §14).**
1. **Forced RLS is completed by `packages/db/scripts/generate.ts`, not by drizzle-kit.** drizzle-kit
   0.31 emits the policy and `ENABLE ROW LEVEL SECURITY` from `pgPolicy`, but has no notion of
   `FORCE` (without which the table owner bypasses every policy) or of grants. The wrapper appends
   both for every table a new migration creates, so the tenancy rule still holds mechanically; the
   isolation suite is what catches a wrapper that was skipped. Policies read
   `NULLIF(current_setting('app.organization_id', true), '')::uuid`: a pooled connection reports `''`,
   not NULL, once any earlier transaction has set the GUC, and `''::uuid` is an error, not a fail-closed
   NULL.
2. **Identity tables follow Better Auth 1.7.3, keyed by Drizzle property, not by column name.** The
   adapter resolves columns by the Drizzle property key, so keys are Better Auth's (`emailVerified`,
   `createdAt`) and columns are this repository's snake_case. Two deliberate departures from the CLI's
   output, reconciled by hand at S24 because the CLI overwrites rather than merges: `timestamptz` and
   `uuidv7()` defaults. `user` is scoped through `member`; `organization` by its own id. The pipeline
   role carries one declared cross-tenant read, `organization_enumerable_by_pipeline` (ids only), so
   the reaper can enumerate organizations and then scope into each.
3. **The uploader is in-house, not Uppy.** Uppy 6.0 (2026-08-26) rewrote `@uppy/aws-s3` to send
   Create, ListParts, Complete, and Abort from the browser on presigned URLs; R2 supports presigned
   GET/PUT/HEAD/DELETE only, so that design cannot run on the deployed store. Temnia's browser half
   only PUTs file slices to server-signed part URLs; every control call is a route handler, which is
   also what makes resume server-side: the fingerprint (project, name, size, lastModified) finds the
   active upload from any browser, ListParts says what the store holds, and a grace window
   (`UPLOAD_ADOPT_GRACE_SECONDS`, 60 s) stops two writers interleaving. Listing parts is never a
   liveness signal; signing is. Part size is a deterministic function of file size.
4. **Player: Video.js v10's React skin over its hls.js media element, with peaks.js 4.** The first
   S1 build used hls.js on a plain video element (v10 is still beta.32 with breaking changes between
   betas). Rajesh reversed that the same day because he likes Video.js's UI, the same kind of call as
   Hugeicons. Two facts from the swap: the media host exposes the hls.js instance as `engine`, so the
   S8 scrubber's `createIFramePlayer()` is reachable after all (the earlier objection was wrong);
   and the legacy's passive-adapter trouble is avoided by initialising peaks only after
   `loadedmetadata` (also bbc/peaks.js#574). `@videojs/react` is pinned to the exact beta; a bump is
   a deliberate change, re-verified by the Playwright playback and waveform assertions.
5. **ffmpeg 8.1.2 is the exact release, copied from `mwader/static-ffmpeg:8.1.2` by image digest.**
   No tarball mirror to maintain (the legacy's pin 404'd when BtbN pruned a dated build); PyAV 18.1's
   wheels bundle the same 8.1.2. Re-examined at S3 against 9.0.x as planned.
6. **The ladder is fMP4 with a separate intra-only I-frame rendition.** ffmpeg's `iframes_only` flag
   cannot produce a companion playlist over the ladder's own segments (verified on 8.1.2: it writes
   whole-segment byte ranges, and with `single_file` every offset is `@0`), so the same pass writes a
   360p one-frame-per-GOP rendition as a single file with byte ranges, and Python adds the
   `#EXT-X-I-FRAMES-ONLY` and master entries. Every playlist is duration-verified against the probe
   before upload (tolerance `max(12 s, 1%)`); the master is read from local disk, which removes the
   network cause of the legacy's 58% truncation, and the assertion stays.
7. **Python talks to Postgres through psycopg 3 with hand-written SQL; no model generator yet.** The
   pipeline's DML is six statements. `tests/test_schema_contract.py` asserts every column and enum it
   touches against the migrated catalogue, which is the drift check the 2026-09-05 decision asked for.
   sqlacodegen (4.0.4, SQLAlchemy 2) is the graduation path when the surface grows.
8. **Migrations are the release phase in `apps/web/instrumentation.ts`.** The server applies pending
   migrations and the seed before its first request when `MIGRATE_DATABASE_URL` is set, and refuses to
   boot in production without it; a failure exits non-zero and Dokploy keeps the previous container.
   Nothing runs at build time.
9. **The HLS ladder moves to Modal at the start of S2, with WhisperX.** Measured on the staging VPS
   during the S1 exit run (2026-09-06): a 2:31 1080p25 master laddered at about 2.9x realtime with all
   eight vCPUs saturated, about 52 minutes for the ladder. Rajesh's call: S1 closes on this box as the
   exit test is written; the transcode activity then calls a Modal function (NVENC, the same ffmpeg
   command) once the Modal account exists for transcription, so both land on one deployment. The
   `veryfast` top-rung preset was offered as a stopgap and declined in favour of the one move.
10. **Garage CORS on the dev bucket allows any origin.** Garage echoes a matching rule's whole origin
   list in `access-control-allow-origin`, and browsers reject a comma-joined list (the first upload
   attempt failed on exactly that); the gate then serves the page from `127.0.0.1` on a random port,
   which no fixed origin list covers. Part PUTs carry no credentials, so `*` is valid, and the
   bucket is local with dev-only keys. Applied by a compose one-shot (`garage-cors`); R2 gets the one
   real origin (runbook §1).

## Working rules (S0, 2026-09-06)

Read `docs/prd.md` (what), `docs/sprint-plan.md` (sequence), and `docs/tech-stack.md` (system design)
before architectural work. Record durable decisions in the Decisions section above, in the same turn
they are made.

### The 360-degree view comes before the code (Rajesh, 2026-09-06)

Every change, whatever it touches (a page, a route, a workflow, a script, an env block, a runbook
line), is planned in full before the first line is written, and the plan is in the PR description.
The S1 bugs that reached Rajesh on staging were all the same shape: the happy path worked and an
edge he met within minutes was unplanned, with the information to foresee it already in the session.
The view covers:

- **Inputs and edges**: empty, huge, duplicate, concurrent, re-entered, cancelled mid-way.
- **Scale**: production numbers (a two-hour, tens-of-gigabytes master), on the real store and the
  real network, not loopback.
- **Failure and time**: per step, its expected duration, the timeout that bounds it, the liveness
  signal inside it, and what a retry does with partial work (reuse, resume, or restart).
- **User states**: every state the surface can be in, including waiting, refused, and failed, with
  the words the user reads and what they can do from there. A raw server message is never a state.
- **Operations**: env, hostnames, roles, volumes, disk, what a deploy or restart does to work in
  flight, and how a wrong value shows up (loud at boot, never as a silent queue).
- **Tenancy and security**: which scope every read and write runs under.
- **Verification**: which test proves each row above; Playwright for every interactive surface;
  the sprint's scale run on staging before the sprint is called done.
- **Legacy lessons**: every item in a legacy report is ticked in the PR description as ported,
  replaced by something better, or dropped with a reason. Reading a lesson is not applying it.

### Git workflow

- **Never commit or push directly to `main`.** Every change lands through a pull request:
  branch → commit → `pnpm pr:verified -- <gh pr create args>` → the required `checks` job green.
  This holds for docs and one-line fixes. The agent finishing a change owns the local gate and the
  attestation and never hands those steps back. For an existing PR use `pnpm push:verified`; raw
  `git push`, `--no-verify`, or a hand-made status are not delivery workflows.
- **Merging is Rajesh's call** (2026-09-06). A PR stays open while a discussion or a changeset is
  being iterated on; follow-up edits go to the same branch with `pnpm push:verified`, not to a new
  PR. Rajesh merges when he is satisfied. The agent does not merge, and does not open a second PR
  for a small follow-up to work that is still under review. (Reversed the earlier rule under which
  the agent merged its own PRs; PRs #4 to #9 were docs follow-ups that should have been one.)
- Branch names: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- **Validation is local while Temnia has one committer** (tech-stack §2; reverse this before adding
  a collaborator). `pnpm ci:local` attests a clean commit only: frozen install, Ultracite, `uv sync`,
  both contract drift checks, compose up, drizzle migrate into a disposable database, `turbo run
  build lint typecheck test`, both Docker images, and a Playwright run in which the web image's
  server action completes a workflow on a worker from the pipeline image. It writes a receipt for
  the exact SHA under `.git/local-ci/`; the pre-push hook refuses any other SHA and any push to
  `main`. `pnpm push:verified` publishes the `local-ci` commit status the GitHub provenance job
  (`.github/workflows/ci.yml`) requires. A commit without an exact-SHA status cannot be merged.
  The gate's containers run in their own Temporal namespace (`temnia-gate-<stamp>`, created and
  deleted by the script), so a developer's `pnpm worker` on `default` never picks up the gate's
  ingest activities, and the gate never picks up the developer's.
- `production` does not exist yet. When it does (M3), it is promoted only by fast-forward from `main`.

### Environments

- `main` = staging, auto-deployed by Dokploy per target with watch paths. The topology, the one-time
  Cloudflare Tunnel and Access setup, and the deploy verification steps are in
  `docs/runbooks/staging.md`. Staging is reachable only through Cloudflare Access; the origin
  publishes no ports.
- Runtime env is set in Dokploy and reaches a container only through an image-changing deploy;
  a container that then exits non-zero is rolled back with its old env. Verify against the running
  service (`docker service inspect … ContainerSpec.Env`), never the API response. Read the dead
  container's log before anything else.
- The VPS SSH port is rate-limited to six new connections per thirty seconds. Never poll over SSH.
- No infra hostname, panel, or console appears in a post or screenshot (`docs/build-in-public.md` §8).

### Local development

- `pnpm services` starts Postgres 18 + pgvector (**56432**), Garage (**56900** S3, **56903** admin,
  bucket `temnia-media`), Temporal (**56233**), and the Temporal UI (**56080**). It is `docker compose
  up -d --wait` restricted to the long-running services, because compose's `--wait` treats the finished
  one-shot schema and namespace jobs as failures and exits 1.
  The 56xxx block is deliberate: 5432, 5433, 55433, 5549x, and 543xx belong to other stacks on the
  development machine. `next dev` uses **3000**.
- `pnpm dev` runs the web app; `pnpm --filter @temnia/pipeline worker` runs the Python worker. The
  home page's hello workflow needs both plus compose; an upload needs `apps/web/.env.local` (copy
  `.env.example`) and the worker finds `ffmpeg` on PATH (the image carries the pinned 8.1.2).
  `node scripts/upload-master.mjs <file> --project <id>` pushes a file through the real upload API and
  follows the ingest, which is how the sprint's scale run is done without a browser.
- Toolchain is pinned in the repo: `packageManager` pnpm 12.3.4 (pnpm self-switches), Node 24 via
  `devEngines.runtime` (pnpm downloads it; `pnpm exec node` is v24 whatever the host has), Python
  3.13 via uv (`apps/pipeline/.python-version`). Nothing else needs a version manager.
- Dependency installs run only allow-listed build scripts (`allowBuilds` in `pnpm-workspace.yaml`)
  and only versions published more than 24 hours ago (`minimumReleaseAge`). A brand-new release
  is pinned to its previous version, not exempted.
- `.env` files are gitignored; `apps/web/.env.example` documents the shape. Nothing secret is
  committed except the dev-only Garage and Postgres credentials in `compose.yaml` and
  `infra/dev/`, which never leave the laptop.

### Tenancy and RLS

- Every organization-owned table carries `organization_id`, is declared in `packages/db/src/schema`
  with `pgPolicy` for the isolation predicate, and gets forced RLS in the migration that creates it.
  `packages/db/tests/isolation.test.ts` asserts those catalogue facts for every table in `public`,
  probes every table as the app role under two seeded organizations (a row written under A is
  invisible, unwritable, and undeletable under B; a row claiming A's scope cannot be inserted under
  B; an unscoped transaction sees nothing), and fails on any table without a probe.
- The organization id always comes from `resolveScope()` (`apps/web/lib/scope/resolve-scope.ts`),
  never from input. Until S24 it returns the seeded Temnia organization and user from
  `@temnia/contracts`. Any access path that does not go through the resolver is a bug.
- The app connects as `temnia_app` and the pipeline as `temnia_pipeline`: no superuser, no
  `BYPASSRLS`, owns nothing. Migrations run as the owner through `MIGRATE_DATABASE_URL` only.
- Drizzle is the only DDL owner. `pnpm --filter @temnia/db db:generate` authors a migration,
  `db:migrate` applies it (advisory-locked, idempotent), and `generate` appends the `FORCE ROW LEVEL
  SECURITY` and role grants drizzle-kit does not emit (decision 1, 2026-09-06). No `drizzle-kit push`
  against a shared database. Python never declares tables; its DML runs through psycopg 3 inside
  `db.scoped()` and `apps/pipeline/tests/test_schema_contract.py` fails the gate on drift.

### Cross-language contracts

- `packages/contracts` (Zod 4) is the single source of truth for anything that crosses the
  TypeScript–Python seam. `pnpm --filter @temnia/contracts schemas` emits JSON Schema;
  `pnpm --filter @temnia/pipeline contracts` generates `apps/pipeline/src/temnia_pipeline/contracts.py`
  (pydantic v2). Both `:check` variants run in the gate; a stale file fails it. Never hand-edit the
  generated module. Task-queue and workflow names live in `packages/contracts/src/temporal.ts` and
  must match the Python `@workflow.defn(name=…)` and worker settings.

### Python pipeline

- One uv project at `apps/pipeline`, driven from Turborepo through its `package.json` shim until
  `experimentalPythonWorkspaces` ships stable. Every command is `uv run --frozen …`; `uv lock` is
  run on purpose, never implicitly. ruff `select = ["ALL"]`, pyright strict, pytest with
  pytest-asyncio; the Temporal time-skipping test server backs workflow tests.
- Never run two `uv` commands on the same project concurrently; they race on the environment. If
  the project directory moves, delete `.venv` and sync again (script shebangs embed the old path).
- Every ffmpeg and ffprobe string lives under `src/temnia_pipeline/media/`. A workflow module may not
  import the database or storage clients: the workflow sandbox re-imports it and refuses them
  (`ReaperWorkflow` lives in `workflows.py`, its activity in `reaper.py`).
- Temporal workflows import the contract models at runtime inside
  `workflow.unsafe.imports_passed_through()`; the SDK resolves run-method type hints to
  deserialise payloads, so `TYPE_CHECKING`-only imports break at runtime.

### UI (hard rule)

- **Never build UI components from scratch.** Search the shadcn registry first and add the official
  component or block (`pnpm dlx shadcn@4.21.0 add <name> -c apps/web`), then compose. Hand-rolling
  layout, navigation, or form primitives the registry provides is not allowed; genuinely novel
  domain UI (timeline, cutting bench) is built from registry primitives and the exception is named
  in the PR.
- The registry is configured for **Base UI** (`style: base-nova`) and **Hugeicons**
  (`iconLibrary: hugeicons` in `apps/web/components.json`), so added components already import
  `@hugeicons/react`; no Lucide swap is needed. Composition is via the `render` prop, not
  `asChild`. Keep a block's structural wrappers: Base UI parts are context-coupled and a dropped
  wrapper crashes only when the menu opens.
- `apps/web/components/ui/**` is vendored registry output: lint-exempt, regenerated by the CLI,
  never hand-maintained.
- Every interactive surface (menu, dialog, popover, form) gets a Playwright test in `apps/web/e2e`
  that opens it and asserts the outcome; a manual sweep is not sufficient.

### Conventions

- Formatting and linting is Ultracite on Biome: `pnpm check`, `pnpm fix`. Package-level `lint`
  scripts run `biome check .` and pick up the root `biome.jsonc`.
- Next.js is a Temporal client only (start, query, signal); it runs no workflow code and makes no
  model calls. `@temporalio/client` stays in `serverExternalPackages`.
- Next 16.3 type-checks with the project-local TypeScript 7 CLI during `next build`; package
  `typecheck` scripts run `tsc --noEmit` (TS 7 native).
- Deployed images never bake env or run migrations at build time. The web image applies migrations
  and the seed at boot from `instrumentation.ts` when `MIGRATE_DATABASE_URL` is set (required in
  production).
- Media reaches the browser only through `/api/media/[...key]`, which answers only inside the
  caller's organization prefix (404 otherwise, never 403). No presigned read URL reaches a client.
- The browser uploads parts straight to storage on server-signed URLs; every multipart control call
  is a route handler under `/api/uploads`. The reaper (`ReaperWorkflow`, a Temporal schedule every
  15 minutes) aborts uploads idle for 24 hours, storage first.
