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

**2026-09-05 — Icons stay Hugeicons; transcription is WhisperX on Modal from S2 (corrected
2026-09-06 after re-research).** On reviewing the
tech-stack research (`docs/tech-stack.md`), Rajesh made two slot decisions. (1) **Hugeicons** stays
because the icons look better; the cost is accepted: shadcn's Base UI registry output imports Lucide,
so each vendored component is swapped with the official migration tool. (2) **WhisperX** (large-v3,
wav2vec2 alignment, pyannote diarization) running on **Modal** is the transcription engine from the
first S2 run, behind the provider seam with a deterministic mock. Basis: the independent March 2026
benchmark on podcasts and interviews put WhisperX ahead of both hosted providers on word error and
diarization; Modal lists the L4 at about $0.80/hour (2026-09-06), and the audio-hours-per-GPU-hour
figure has no primary source, so the first staging run is the benchmark. Consequences: the sprint
plan's S12 is a calibration round (model size, VAD, alignment,
diarization settings against the sentence grid), not an ownership A/B; a hosted adapter (AssemblyAI
Universal-3.5 Pro is the candidate) is built only as fallback if the bar is missed; PRD §6's
Deepgram-primary line is retired at its next revision. whisperx 3.8.6 (2026-05-25, BSD-2) requires
pyannote-audio 4.x and defaults to `pyannote/speaker-diarization-community-1` (gated on Hugging Face,
CC-BY-4.0, commercial use allowed with attribution); issue #1406 is a dead `use_auth_token` keyword on
the Whisper model loader, inert under whisperx's `huggingface-hub<1` pin; no pin and no patch are
needed. The model gateway is still decided by the S3 transport probe; its
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
3. **The uploader is Uppy 6 over the app's own control calls (Rajesh, 2026-09-07).** Uppy 6.0
   (2026-08-26) rewrote `@uppy/aws-s3` to send Create, ListParts, Complete, and Abort from the browser
   on presigned URLs. The S1 build rejected it on the claim that R2 cannot serve those; a probe from
   the staging pipeline container on 2026-09-07 disproved it (all four presigned calls succeed on R2
   and on Garage; R2's documented exclusion is HTML-form POST policies, a different mechanism), and
   Rajesh chose Uppy, which had worked well in the legacy. The shape: Uppy's Dashboard (inline; core,
   aws-s3, dashboard, react pinned 6.0.0 exact) owns the browser half. `POST /api/uploads` still
   creates the source and upload rows and the store's multipart upload, by fingerprint (project,
   name, size, lastModified) with the adoption grace window (`UPLOAD_ADOPT_GRACE_SECONDS`, 60 s) and
   a 409 countdown. The browser injects `{key, uploadId}` as the plugin's own resume state
   (`s3Multipart`, the field Golden Retriever persists and `S3Uploader` reads), so Uppy lists parts
   and continues rather than creating an upload of its own. `signRequest` is one route that signs
   only UploadPart (the liveness touch) and ListParts (never liveness). Complete is the app's own
   idempotent route handed to Uppy as the URL: storage first (HeadObject, else the store's part list
   and a server-side Complete), one conditional row transition, ledger, ingest start, an S3-shaped
   XML answer. Abort is never signed: a user's Cancel goes through the DELETE route from the
   file-removed handler, and an unmount is refused, so in-app navigation keeps every part for the
   re-pick. Part size is a deterministic function of file size capped at 1000 parts (Uppy reads one
   ListParts page on resume). No Golden Retriever: its blob store caps at 10 MiB, so a master is
   always re-picked, which the fingerprint already covers in any browser.
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
   Modal's L4 lists at about $0.80/hour (2026-09-06); the ladder runs there as a hybrid, CPU decode
   and scale with NVENC for the video rungs, because NVENC cannot decode ProRes masters, and the
   function publishes the ladder to R2 itself, since the publish was 21 of the VPS's 67 minutes.
10. **Garage CORS on the dev bucket allows any origin.** Garage echoes a matching rule's whole origin
   list in `access-control-allow-origin`, and browsers reject a comma-joined list (the first upload
   attempt failed on exactly that); the gate then serves the page from `127.0.0.1` on a random port,
   which no fixed origin list covers. Part PUTs carry no credentials, so `*` is valid, and the
   bucket is local with dev-only keys. Applied by a compose one-shot (`garage-cors`); R2 gets the one
   real origin (runbook §1).

**2026-09-07 — The substrate is designed from the Python ecosystem; the legacy rules are a scored
baseline.** Slice C ported the legacy TypeScript grid function for function, which reproduced a set
of regexes and thresholds in the language that exists to avoid them. Rajesh's correction: research
the ecosystem and build what it says. The substrate is now three layers behind a `Segmenter`
protocol (`apps/pipeline/src/temnia_pipeline/substrate/`), all sharing one output shape and one
renderer.
1. **Sentences: Segment-any-Text** (`wtpsplit` 2.2.1, MIT), which predicts boundaries from the text
   and was evaluated on ASR transcripts with the punctuation and casing stripped. This removes the
   dependence on Whisper's full stops that PRD §8 itself flags as the grid's weakness. Words map
   back by character offset, so every sentence keeps exact `startMs` and `endMs`. `sat-3l-sm` runs
   on the worker's CPU; `sat-12l` on the Modal GPU is a name, not a code change. SaT's own paragraph
   mode is a selectable candidate and **not** the default: measured on the fixtures it returns one
   paragraph per sentence, so paragraphs come from the legacy rule (2500 ms, 120 words, speaker
   change) over SaT's sentences.
2. **Chapter candidates: change-point detection over sentence embeddings**
   (`sentence-transformers` 6.0.1 with all-MiniLM-L6-v2, `ruptures` 1.1.10 `KernelCPD` with an RBF
   kernel), the Embed-KCPD line of work, unsupervised. Granularity is a parameter
   (`target_per_hour`, default 6) and the count is solved exactly rather than approached by
   bisection. Each candidate carries a score, so S4 reads a ranked list instead of guessing from the
   raw text. Chapter-Llama is a later challenger to score, not a default. Kernel CPD is quadratic in
   sentences: 2,500 is seconds, and the ceiling is 10,000 with a clear error.
3. **The legacy rules stay**, as `LegacyRulesSegmenter`, still byte-parity tested against the frozen
   oracle in `tools/legacy-reference/`. That is what makes them a trustworthy baseline rather than a
   memory.
4. **The metrics are the field's, not plain F1.** `evals/segmentation.py` reports Pk, WindowDiff and
   GHD (`nltk` 3.10; `segeval` is unmaintained since 2013), window-tolerant precision and recall
   reported separately as purity and coverage with their F1, boundary density per hour on both
   sides, and tIoU-F1 over 0.5 to 0.95 for segments. "When F1 Fails" (arXiv 2512.17083) shows
   boundary F1 tracks boundary density more than boundary quality, which is why density and the
   unaveraged pair are always printed. Every row is scored on one unit grid, the source's word start
   times, because sentences differ per segmenter and rows must be comparable. Without gold the
   runner still reports density and pairwise agreement.
5. **The exit test changes.** "Byte-identical to the legacy on three recorded sources" becomes
   `temnia-eval segment` on those sources with every segmenter scored, and the plan recording which
   segmenter S4 starts on and what number would make us switch. The legacy scorer snapshots keep
   their bit-identical test: they are the record of what the legacy measured, and the M1 baseline
   column.
6. **Models are baked into the pipeline image** (`TEMNIA_MODELS_DIR`, `HF_HOME`, `HF_HUB_OFFLINE=1`)
   so the worker never downloads inside an activity; `torch` is pinned to the CPU wheels on linux,
   which is the difference between a 3 GB image and a 6 GB one.

**2026-09-07 — The editing harness is a typed, durable program on Temporal with PydanticAI, not a
cast of agents (Rajesh).** Research basis: the 2025 multi-agent failure taxonomy (specification,
inter-agent misalignment, verification), compute-controlled results where a single capable model
matches multi-agent setups, and the domain systems that work (EditDuet's editor-critic loop and
artefact judge at 80.6%, Crayotter's traceable artefacts, PODTILE's global context, Chapter-Llama's
fine-tuned proposer). Five typed stages (brief, propose in ids, cut in parallel with backstops,
verify code-then-family, explain); idempotency-keyed activities for zero repeated calls; no default
vendor and open-weight models first-class in every seat's pool, all seats through the gateway
(Vercel AI Gateway leading, probe-confirmed); per-seat offline prompt optimisation with DSPy/GEPA
against the C2 metrics; failover halt and per-run budget as pure code. Consequence: sprint-plan S3
rewritten; tech-stack rows for framework, optimisation, gateway, roster, evals, tracing updated; the
legacy's Director/Reconciler/Cutter/Publisher/Verifier/Reviewer names retire. What would change it:
a measured win for a persona-style loop on the three fresh sources at equal cost.

**2026-09-08 — The S3 harness adopts the review's evidence layer and edit compiler; five typed
artefacts, not five calls (Rajesh, after the 2026-09-07 review).** The review in
`docs/design/harness-review-and-architecture-2026-09-07.md` (32 findings on the S2 code, 19
independently rechecked, none overturned) proposed an architecture that is the 2026-09-07 design
with two things made explicit: a versioned source-evidence record under everything (words in
lexical order with their timing and its uncertainty, overlapping speaker intervals, speech and
non-speech regions, shots), and a deterministic edit compiler that chooses neighbouring boundaries
jointly from addressable candidates, the model proposing semantic spans and code picking the cut.
Both are adopted: the compiler is the generalisation of the legacy's cutting room and answers the
wrong-boundaries failure that sank M1. The one conflict, "drop the fixed five stages", is resolved
by keeping the five typed artefacts as the contract (evidence, plan, edit specification, checks,
review) and letting the number of model calls a stage makes be an implementation choice measured per
lane; a stage may be one call, several, or none. Deferred, each with its reason: Remotion and the
TypeScript composition worker (no lane before S9 needs them, and the licence is a cost);
OpenTimelineIO interchange (nobody has asked for it); budget reservations beyond the pre-call check
(there is no parallel dispatch yet); the twelve-lane edit specification (version one carries what
chapters and moments need). Pulled forward: the annotated corpus, acceptable boundary windows on the
three recordings, is the S4 entry gate, because the compiler's objective cannot be calibrated
without it and the legacy's cutting room was never proven on a fresh source. Speech engines are
re-auditioned only after slice D gives a measured WhisperX baseline. The spec is
`docs/plans/s3-harness-spec.md`; the batch that fixed the review's defects is
`docs/plans/s2-hardening-360-view.md`. Two rules that batch added to the pipeline: **a write a
retry can repeat carries the identity of the run that made it and is fenced on it** (`run_id` on the
transcript row, an idempotency key on the ledger, a per-attempt object key on a correction), and
**a transport failure is never reported as a failed computation** (`Unreachable` is its own status
in both Modal adapters, and no runner spawns on it). The PRD's "a retry re-pays zero tokens" (§1.5 principle 6,
§8 chassis, §23 fair billing) is qualified in the same PR to committed results, with unresolved spend
bounded and reported, because the crash window between a provider's answer and Temnia's commit cannot
be closed by a checkpoint.

**2026-09-08 — S2 follow-up: outcomes carry their origin, models their loaded identity.**
The second review's fourteen remaining defects are tracked in
`docs/plans/s2-hardening-followup-360-view.md`. Modal media protocol **4** returns an explicit
success/failure envelope: a remote `OSError` or `TimeoutError` is a computation failure, while
transport uncertainty retains the call handle. The old exception-class heuristic could not
distinguish them because Modal rethrows serialized remote exceptions as their original classes.
This is an incompatible paired rollout: drain version-3 work before replacing the Modal app and
workers; the staging runbook gives the sequence. A separate smoke CLI resolves the deployed app
and environment and checks its source/config fingerprint; importing a local Modal entry point
does not prove the deployed app works. Fingerprints identify build inputs, not immutable upstream
wheel or GPU model bytes.

SaT weights, its separately loaded XLM-R tokenizer, and MiniLM now load explicit local commit
snapshots. Setup fetches those exact revisions and verifies their required files; runtime does
not resolve `main`. `HF_HOME` takes precedence over `TEMNIA_MODELS_DIR` in both setup and runtime,
and all loaders receive absolute paths from that one root. Provenance is captured with the loaded
model, not read later from a mutable cache reference. Other audition models require a prefetched
`repo@<full commit>` snapshot; the default fetch script intentionally downloads only the pinned
default files. The gate performs this setup explicitly before its offline model tests.

Transcript edits retain the revision of their loaded bytes, and asynchronous saves retain their
own draft identity. A retry reserves only the observed row using a temporary `dispatch:<uuid>`
run id; the worker replaces it when claiming. Unknown Temporal status never authorizes resetting
the row. Corrections and machine finalization take the same transcript row lock before reconciling
storage usage. HLS reuse verifies named objects, sizes, and playlist references; retained extra
objects are included in recorded storage usage rather than removed during a possibly concurrent retry.

**2026-09-08 — Chapter implementation: immutable revisions, physical attempts and finite review workflows.**
PR #24 acts on the accepted review plan; the design and comparisons are in
`docs/pipeline-architecture.md`. A finite ChapterRunWorkflow produces reviewable outputs; a
finite ChapterReviewWorkflow per mutation UUID applies a revision compare-and-swap and stores
its replayable outcome. Compared with a long-open review workflow, this keeps immutable database
revisions authoritative across deployments and browser re-entry. Eight scoped tables separate
artifacts/dependencies, runs, operations, attempts, reservations, revisions and review events.
Atomic run-row reservations replace the earlier pre-call-only budget check; unknown outcomes
retain exposure, known failures retain expenses, and actual charges above estimates are recorded
before further dispatch is refused. App reads harness history; pipeline owns writes. Source
history restricts deletion, with a source deletion fence before any object removal.

PydanticAI's TemporalDurability capability remains the model integration, pinned at 2.40.0
(the September 8 release is too new under the age rule). Paid dispatch guards execute inside
its model activity, with provider and validation retries disabled; pinned-API replay tests must
prove this placement. A qualified immutable route snapshot selects seats without a default
vendor. Earlier prose said a gateway transport probe was confirmed; no saved probe artifact or
configured credential was found in the inspected worktrees, shell or running staging services
on September 8. Live qualification remains pending evidence; synthetic cassettes prove wiring.

Speech recovery uses an additive temnia-speech app, protocol temnia-speech/1, with separate
recognize/align/diarize checkpoints while protocol 4 drains. Single-use stage processes initially
fence native GPU memory lifetime; compared with a checkpointed reused monolith this trades extra
downloads/cold loads for attributable stage recovery. Batch repair is bounded to explicit
recognition OOM, 16 -> 8 -> 4, never an uncertain remote outcome. Independent Silero v6.2.1 ONNX
coverage runs on CPU with ONNX Runtime 1.29.0 and pinned, hash-verified offline weights; direct
ONNX avoids the Python package's unnecessary conflicting audio dependency. Detector agreement
is not transcription accuracy. These are implementation decisions, not claims of completed live
qualification. The sprint plan and the older slice specification defer to this record.

**2026-09-08 — Chapter time uses a source-relative output grid; source offset is a mapping, not a frame phase.** The compiler quantizes shared internal boundaries on a canonical frame/sample grid from source-relative zero. An absolute container start timestamp does not establish where decoded input frames lie. Rendering preserves selected-track offsets, handles VFR with explicit codec/frame tolerance, and checks actual artifacts. The first chapter renderer retains ffmpeg 8.1.2 after comparing current 9.0.1: its required accurate seek/timestamp/filter capabilities already exist on the matched CLI/PyAV line, while an upgrade requires chapter plus existing ladder/NVENC regression evidence. This does not assert a third-party incompatibility. Evidence: `docs/pipeline-architecture.md` and the official release catalogue inspected September 8.

**2026-09-08 — GPU execution admission must survive provider container restarts.** A real CPU-only Modal fault probe showed two containers executing one submitted call despite `retries=0`. The second was stopped before inference by a create-only R2 claim. The checkpointed speech app adopts that guard: reuse an exact completed checkpoint, otherwise acquire one immutable admission claim before model work; an existing incomplete claim means unknown execution and no automatic retry. Keeping unguarded execution was rejected by the probe; changing compute providers is deferred to measured qualification. Ledger attempts count admitted invocations, not invisible provider container starts, and a reservation is not a guaranteed invoice ceiling. CPU and memory belong in the dated estimate alongside the GPU. Record and limits: `docs/design/harness-foundation-probes-2026-09-08.json`, `docs/pipeline-architecture.md`.

**2026-09-08 — Short chapter controls have dedicated activity capacity in the same process.**
The two media activity slots can be occupied for minutes or hours. A real Temporal probe held
both and showed that a shared-queue control waited, while a second activity queue completed
the control before either media slot was released. The Python process now serves short scoped
chapter commands on `<configured pipeline queue>-control` with four slots; workflows and heavy
activities keep the existing queue and two slots. Both workers start, fail and drain together,
with the same namespace and no extra routing environment variable. This remains one durable
runtime. Raising every activity's concurrency was rejected because it also raises CPU/disk
pressure; local activities were compared against the current Temporal guide and rejected for
these database commands because they add marker/replay semantics without useful savings.
Only compact reads and transactions belong on control; object I/O, models and renders remain
on the pipeline queue. The probe proves scheduling independence, not a production latency SLA
or unlimited control capacity. Record: `docs/pipeline-architecture.md` and the PR #24 probe record.

**2026-09-08 — Keep the three segmentation metrics, remove the NLTK runtime dependency.**
The application used only Pk, WindowDiff and GHD. The current upstream advisory
GHSA-8mgp-746c-j5xp lists NLTK through 3.10.3 with no patched release; inspection did not
find the vulnerable model-path APIs on Temnia's execution path. Rather than carry the whole
package or change the measured metric conventions, PR #24 narrowly adapts the three pure
algorithms with Apache-2.0 attribution, original source hash and license in
`apps/pipeline/THIRD_PARTY_NOTICES.md`. An oracle captured from installed NLTK 3.10.3
before removal covers 18,228 exhaustive and seeded comparisons, including asymmetric GHD
costs, with maximum difference 0.0. Incremental window counts and two-row GHD bound temporary
memory. No downloader, initializer, model loader or file APIs are copied. The frozen dependency
tree contains neither NLTK nor its sole-use defusedxml dependency. This replaces the 2026-09-07
choice of the NLTK package, preserving its metric conventions and the frozen legacy baseline.

**2026-09-08 — Human chapter approval closes editorial review; metadata-only decisions reuse checks.**
The recorded end-to-end journey reached revision 13 with every section explicitly accepted, but
the model's repeated `needs_review` verdict still prevented export. Accepting four sections also
made four further verifier calls. The model identifies editorial concerns; a reasoned human
acknowledgement resolves them. Export therefore requires every section accepted and every required
technical check present exactly once with no failures; warnings and the earlier model verdict remain
visible evidence. Technical failures
cannot be overridden. Acceptance/rejection changes review metadata only and reuses verified media
and checks through new revision-bound descriptors with immutable predecessor dependencies; it does
not call a model or decode the unchanged files again. Content changes still trigger the relevant
render/check/editorial path. A failed final export can resume from the accepted revision. Compared
with making a model pass mandatory after human review, this avoids a review loop the user cannot
close and repeated provider work that does not assess a changed edit. The browser fixture keeps its
model verdict at `needs_review` to prove this distinction.

**2026-09-08 — Large local source caches require an activity lease and bounded eviction.**
The chapter evidence pass retains the verified master for rendering. A parent-directory mtime
alone cannot protect a cache reopened by another worker, and lazy cleanup never runs on an idle
worker. Use a stable per-run advisory lock outside the disposable directory, held for the full
activity, with nonblocking eviction, terminal cleanup, expiry and disk-pressure cleanup. This
was compared with mtime-only eviction and process-local locks; neither protects concurrent worker
processes. The choice targets the existing Unix worker's local volume. Remote filesystem locking
is not qualified by this decision. Fresh sources: Python 3.13's
[fcntl reference](https://docs.python.org/3.13/library/fcntl.html) and the
[Linux flock manual](https://man7.org/linux/man-pages/man2/flock.2.html), inspected September 8;
the implementation must prove contention and cleanup behavior with real file locks.

**2026-09-08 — Build-in-public strategy is revised from research, with cadence treated as a trial
(Rajesh's clarification: the earlier posting habits were unresearched).** The operating playbook is
`docs/build-in-public/TEMNIA_BUILD_IN_PUBLIC_GUIDE.md`; its companion owns founder/product account
roles, and `RESEARCH_REVIEW_2026-09-08.md` records primary-platform checks and the limits/inconsistency
in the observational frequency evidence. Initial target: one founder X daily, three product X
weekly and five personal LinkedIn weekly; an extra founder X needs an independent supported
takeaway. Begin with a 30-minute daily editorial/community budget, review workload after 14 days
and response after 28; these are trial choices, not proven growth optima. The supplied 2+1 X daily
allocation is a possible expansion, not a mandatory quota. This supersedes the September 6–7
mandatory Day N opener, single-post cap, combined daily recap, fixed point formatting and ban on
customer framing: lead with a useful hook; Day N is optional; one original post has one takeaway.
Coverage/chapters still lead the product story, with moments drawn from that structure. Preserve
`docs/log/YYYY-MM-DD.md` as the facts/drafts/publication history; every session logs its work, while
social drafts follow cadence and evidence rather than creating another pack per session. Coding
agents assist; Rajesh edits and posts. No scheduler, account change, publication or content-system
scaffolding is implied by the guides. This also replaces the September 3 daily-LinkedIn cadence
and the earlier per-session draft obligation; every session still records its work.
`docs/build-in-public.md` remains the navigation entry point.

**2026-09-08 — Founder X length follows Rajesh's Premium+ preference.** Rajesh reconfirmed that
@RajeshBuilds has Premium+ and welcomes modestly longer posts when the explanation benefits,
without long, tedious copy. Do not impose a 280-character cap on founder drafts. Roughly 50–100
words is a flexible editorial starting range, not a quota or a claim about engagement; shorter
posts remain valid. Keep one clear takeaway and use extra space for evidence or explanation.
@TemniaHQ's entitlement remains unknown, so standard-length product drafts remain the fallback.
This changes length guidance, not the account cadence. Guide §8 records the current preference.

**2026-09-09 — Evidence identity is source-scoped; runs own references to it.** The complete
chapter browser journey exposed a second-run collision: identical source/transcript/config evidence
had the same producer fingerprint but different consumer `runId` metadata. New evidence omits that
transient metadata; each scoped `harness_run.evidence_artifact_id` records consumption. Duplicate
acceptance tolerates only that legacy evidence key while preserving exact bytes, stable metadata,
transcript identity and dependency checks. Adding run ID to the fingerprint was rejected because it
would turn identical evidence into duplicated artifacts and storage metering. The actual PostgreSQL
regression proves two runs share one artifact/storage entry, and changed content or lineage still
conflicts. The complete browser journey passes through the second run.

**2026-09-09 — Deletion distinguishes proven probe failure from uncertain media writers.**
The production-image resume test exposed a blanket `FAILED` workflow fence: even an unreadable
file rejected before external media dispatch could never be deleted. Keep the source-row fence
and retained-history refusal. Any failed-run exception also requires `duration_ms IS NULL` under
that lock: successful probe persistence precedes every transcode schedule, and retry never clears
it. Bounded, complete history for the exact run must prove either `claim_source → probe_source →
fail_source` with the known non-retryable probe failure, or a claim completed with `false` followed
by non-retryable `NotClaimable`, with no writer-capable activity or child/external dispatch. The
claim-only case handles a retry arriving after deletion was fenced; retry metadata updates cannot
mutate a fenced source.
Missing, oversized, truncated or ambiguous history and other terminal outcomes remain fenced.
Allowing every failed workflow was rejected because a lost remote acknowledgement can leave a
writer running. The existing resumed-upload deletion assertion remains the acceptance test.

**2026-09-09 — Speech performance changes preserve physical-attempt recovery.** The first
151-minute checkpointed run was slower than the old combined GPU run; neither observation proves
a GPU or billing disadvantage. Protocol `temnia-speech/2` adds an independently admitted raw
speaker-turn stage alongside recognition → alignment, followed by a deterministic CPU assignment
artifact with both input dependencies. Protocol 1 retains its original interpretation and app
until drained. Progress callbacks perform local coalescing; a separate bounded publisher owns
network writes. Resource profiles and exact offline model identities travel with deployment,
plan, admission and artifacts. Four versus eight capped CPU cores and serial versus parallel
scheduling are measured on one frozen source/model set before selecting a default. Single-use
containers and create-only admission remain; co-location and snapshots are later candidates.
Atomic batch reservation precedes fan-out, cancellation drains siblings, and unknown outcomes or
charges retain their exposure. Faster wall time alone is not a cost win. The comparison plan and
limits are `docs/plans/speech-optimization-360-view.md`. The first short comparison exposed a
missing `speech_assignment` database enum value after all GPU checkpoints were accepted; Drizzle
adds that artifact kind. Recovery retains the failed run and its costs and reuses accepted evidence
in a separate run whose budget cannot admit inference. A benchmark continuation preserves its
original journal and cap and records GPU and worker builds separately. Actual assignment and report
SQL are exercised against migrated PostgreSQL.

**2026-09-09 — Speech liveness covers the whole activity, including checkpoint publication.**
The first long protocol-2 comparison completed three GPU calls and a CPU assignment, but Temporal
reported a heartbeat timeout and retried the activity. The retry reused all three results. The CPU
join itself measured 0.207 seconds on the preserved actual input; asynchronous storage and ledger
work after polling also need heartbeats. One owned Temporal-only heartbeat spans the whole activity,
independent of diagnostic database progress. Its lifecycle follows success, failure and cancellation;
the ten-second timeout and provider cleanup remain. Increasing the timeout or heartbeating only at
assignment entry leaves the underlying gap. The original long-A wall timing is excluded, while its
original complete GPU telemetry remains usable. A narrowly validated completed-case acknowledgment
retains the failed-validation record and exposure without rerunning that source. Remaining fixed
cases use the corrected worker build and unchanged GPU/model builds; block-one comparisons requiring
the excluded A wall time remain unavailable. No additional repetition or budget is inferred.

**2026-09-09 — Accepted exports must offer the actual media, with their revision intact.** Manual
handoff review found that “Export accepted files” opened only the internal JSON manifest. Keep that
manifest as the durable export contract and present chapter video/caption downloads from its exact
accepted edit, including after later edits or cancellation. Same-origin downloads through the existing
scoped streaming proxy avoid buffering multi-hour media into a browser or server ZIP. Validate the
accepted source/run/revision/hash, complete keep set and required checks before exposing links; titles
come from the accepted edit, never the current draft. Missing/corrupt evidence gets an explicit read
retry. The manifest remains separately downloadable; no new rendering or model call is needed.
The plan is `docs/plans/chapter-downloads-360-view.md`; the local product pass is documented in
`docs/runbooks/chapter-harness-manual-testing.md`.

**2026-09-09 — Every required-seat route must fit the worker's configured output capacity at boot.**
A valid snapshot alone did not ensure that every proposal, summary and verifier candidate could
honor `HARNESS_MAX_OUTPUT_TOKENS`. Reject an enabled worker before it polls if any referenced route
has a lower maximum or lacks context beyond the output cap plus the shared protocol allowance.
This includes later failover candidates. Keep dynamic prompt-size checks; do not silently clamp
the configured cap or remove a candidate from an immutable snapshot. Disabled workers are unchanged.
Plan: `docs/plans/harness-route-capacity-360-view.md`.


**2026-09-09 — The controlled speech screen selects no new configuration.** The twelve-case,
36-call fixed experiment completed on one 151-minute English recording. Coalesced serial progress
reduced the one clean synchronous wall comparison by 23.89%; parallel speaker detection reached
about 8.8 minutes but raised estimated resources versus coalesced serial. The exact-output gate
failed: two recognized-text families occurred, including different outputs across repeated identical
serial configuration. Do not relax that predeclared rule or promote a default from these numbers.
Future selection needs prospective quality tolerances/listening, additional sources and attributable
billing. The report, exact exclusions and identities are in
`docs/design/checkpointed-speech-optimization-2026-09-09.md`; shared staging is unchanged.

**2026-09-09 — Gateway authentication and catalogue metadata do not establish live eligibility.**
The first staging request with mandatory per-request ZDR was rejected because Vercel Pro Trial
does not permit it; the key itself authenticated successfully. Vercel requires an active paid Pro
or Enterprise plan for this control, independently of gateway credits. Keep chapter planning
disabled until the account entitlement and exact provider/schema/identity/cost path are qualified.
Do not weaken privacy to pass a smoke. Probe candidates remain separate from production snapshots;
the reserved `qualification-unproven:` route prefix cannot enter a saved snapshot. The bounded
qualifier preserves admission, response and cost evidence without retrying inference or claiming
editorial quality. Paid Pro subsequently cleared the entitlement; DeepSeek/DeepInfra, Qwen/Alibaba
and Kimi/Alibaba passed all three schemas. The live failures also require integer-safe Decimal
settlement and prompt-v3 copy-only word anchors before rollout. Gemini/Vertex's integer-enum
rejection and Kimi/Wafer's schema failure exclude those exact pairings, not their whole families.
No editorial winner is selected from transport success. Record:
`docs/design/gateway-staging-qualification-2026-09-09.md`.

**2026-09-09 — Dokploy environment-only changes can use its supported reload.** The earlier
image-changing-only claim was too broad. The installed v0.30.5 implementation was inspected and
then probed on idle staging services: `application.reload` creates a new Swarm task with the saved
environment and mounts while retaining the image. Both the pipeline and web booted with the new
harness settings; actual container environments, file identity, worker readiness and web health
were checked. A build captures configuration when processing starts, so saving a new value during that build
does not update its eventual task. Use a Git deployment for code changes and a verified reload for
configuration-only changes; preserve the previous configuration and check work in flight first.
The standard mount API has no read-only flag in this version. The nonsecret route snapshot uses a
content-addressed, root-owned `0444` file; the UID-10001 worker's write attempt is refused. This is
file permission enforcement, not a Docker read-only mount. Procedure: `docs/runbooks/staging.md`.

**2026-09-09 — Internal summary labels belong to code; source anchors remain model constraints.**
The first 151-minute live chapter run stopped after seven summaries because the seventh returned
five empty unit labels. These labels identify summary units, not source evidence. Derive them
deterministically inside the model activity after preserving the raw response, for both fresh
and reused results. Keep the request schema/fingerprint and immutable receipt unchanged, so
recovery does not purchase the same seven results again. Do not normalize sentence endpoints,
quote anchors or editorial content. Compared with another paid repair or a wider source schema,
this removes a model responsibility that has no editorial value. The same run exposed missing
heartbeats during a quiet source download and delayed generation accounting; supervise I/O
liveness with separate finite stall deadlines and poll known generation receipts for a bounded
period. Unknown charges retain reservations. Plan: `docs/plans/chapter-live-failures-360-view.md`;
measured outcome: `docs/design/chapter-staging-qualification-2026-09-09.md`.

**2026-09-09 — Web image dependency downloads have bounded network settings.** Two unchanged
release gates passed application tests then failed fetching large npm tarballs in the cold Docker
install stage. That stage uses eight concurrent requests and a 180-second fetch deadline, keeping
the frozen lockfile, integrity checks and default two retries. Offline install cannot populate
an empty store; a persistent cache mount is a separate optimization. This is a build-only override,
not a global pnpm setting. Evidence and comparison: `docs/plans/chapter-live-failures-360-view.md`.

## Working rules (S0, 2026-09-06)

Read `docs/prd.md` (what), `docs/sprint-plan.md` (sequence), and `docs/tech-stack.md` (system design)
before architectural work. Record durable decisions in the Decisions section above, in the same turn
they are made.

### Sessions orchestrate (Rajesh, 2026-09-06)

The top model in a session plans the work, writes the briefs, reads the agents' reports, and writes
the synthesis itself: the 360-degree view, the design, every decision, and the final word on a
review. Research, extraction, exploration, and implementation against a written spec go to cheaper
agents: Opus for implementation and multi-source synthesis, Sonnet for research and mechanical work.
The finalization of a document or a decision is never delegated. Agents report to short files in the
session scratchpad, not raw dumps; independent agents run in parallel. Delegation never lowers the
verification bar: the agent that finishes a change runs the gate and the orchestrator checks the
evidence.

### The PRD is not a hard requirement (Rajesh, 2026-09-07)

The PRD and the sprint plan describe intent and were written from the legacy TypeScript product.
They are not specifications to reproduce. Every pipeline component is designed from the Python
ecosystem first, the legacy is behaviour evidence and a baseline to beat, and where research shows
a better design the better design is built, the deviation is recorded in the Decisions section, and
the PRD or sprint-plan row is updated in the same PR. An exit test derived from the legacy is
replaced by a scored comparison in which the legacy behaviour is one candidate.

No pipeline design names a default vendor. Designs and cost estimates speak of seats and show
several families side by side, open-weight ones included; the audition decides, and its result is
recorded here.

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

### A third-party limit is probed before it decides anything (2026-09-07)

When research says a service cannot do something and that claim picks a design, the claim is
verified against the real service (one presigned request, one API call, one query) before it is
recorded here. The S1 uploader decision carried "R2 does not support presigned multipart control
calls" for a day, into this file, the tech-stack record, the log, and a post draft; Rajesh asked for
a re-check before posting and a five-line probe against the staging bucket disproved it. A wrong
limit that survives into a decision record is worse than no research.

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
- Runtime env is set in Dokploy and applied by a code deployment or an explicit configuration
  reload. Saving alone does not change a running task; a build retains the configuration it
  captured at its start. Verify the actual running service and container environments, image,
  and boot health, never only the API response. Read a failed task's log before anything else;
  do not assume automatic rollback. Restore and reload the backed-up configuration explicitly
  when recovery requires it.
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
