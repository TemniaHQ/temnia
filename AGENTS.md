# Temnia — repo rules

## Decisions

**2026-09-14 — Indexed source evidence and tools are a foundation for the editorial roles.**
Rajesh's direction after reviewing PR #48: repeatedly supplying a whole transcript to an author
or source reviewer, with intermittent success on 44-minute Karma, is not a demonstrated solution
for two- or four-hour recordings. Develop a reusable source index and tool-based evidence access
for discovery, authoring and source review together. Treat this as a foundational design change,
superseding PR #48's late author-only W4 placement and deferred source-review conversion. The
initial design is [indexed-editorial-evidence-2026-09-14.md](docs/design/indexed-editorial-evidence-2026-09-14.md):
bounded chronological coverage plus targeted lexical/semantic retrieval and exact source reads;
summaries and relationship links are hypotheses, and coverage records do not prove comprehension.
Cold review remains isolated to the selected speech. Bounded working context, source-bound
identities, independent judgments, repair authority and the run-level unknown-expense fence
remain part of the design. Internal discussion structure must be observable; an index alone
does not prove the compound-candidate problem solved. The first vertical slice is implemented on
`feat/indexed-editorial-evidence`: a source-bound `topic-source-index/2` episode → section → region
hierarchy, bounded chronological browse, BM25/MiniLM leaf search and exact sentence reads replace
transcript bodies in the inventory, author and source-review prompts. Every indexed answer must
retain a complete root-then-sections browse/search/read trace and must have read every sentence in
every span it returns. Code re-derives the hierarchy's exact ownership, deterministic descriptors
and bottom-up vectors. Each tool continuation is a separately accounted model request. Inventory
and author requests admit only the exact three source tools; source review adds mandatory paginated
`inspect_candidate` and optional measured `read_media_evidence` under immutable selection/evidence
authority, with exact checkpoint and admission replay. The gateway admits only the complete
role-specific three- or five-tool set. A 2,400-sentence synthetic four-hour source produces 10
sections and 75 leaves and proves bounded initial prompt shape and pagination, not editorial
quality. New indexed calls rebuild every continuation from the unchanged prompt plus one bounded
`topic-agent-checkpoint/1`; old assistant/tool messages are removed. The checkpoint retains progress
facts without source text and a bounded LRU set of exact sentences, is published before dispatch in a
run/stage/role/index-bound parent chain, and is loaded unchanged after a known provider failure so a
route retry or fallback does not repeat settled discovery. Final `topic-source-inspection/2`
admission requires every cited sentence to remain in the response's exact checkpoint dependency.
Contract and limits: [indexed-agent-checkpoints-2026-09-14.md](docs/design/indexed-agent-checkpoints-2026-09-14.md).
Reviewer tool contract:
[candidate-media-evidence-tools-2026-09-14.md](docs/design/candidate-media-evidence-tools-2026-09-14.md).
The index now has a policy-neutral producer identity bound to exact accepted evidence, source,
transcript revision, immutable encoder, partition constants and relevant runtime versions. A later
run in the same organization/source scope can reuse it only after exact reference, lineage, stored
hash and full structural validation; each run publishes `topic-source-index-use/1` with its observed
build/reuse result. Contract:
[source-index-reuse-2026-09-14.md](docs/design/source-index-reuse-2026-09-14.md). Bounded portfolio
reconciliation now starts with a separately versioned bounded opportunity inventory. In
`standalone-topics/4` / `TopicSelectionWorkflowV4`, code derives one immutable work item per source
section, assigns an opportunity to the section containing its earliest core sentence, permits exact
setup/completion reads across the edge, and runs at most three section calls concurrently. Every
settled call becomes an admitted `topic-opportunity-inventory-shard/1` or an exact rejection. Only
the complete ordered shard set can produce `topic-opportunity-inventory-manifest/1`; otherwise the
author is not called and the run ends visibly with its partial shards and checkpoints retained. V3
history and its whole-source inventory stay unchanged. The V4 experiment and pre-flight paths are
implemented, but the web keeps starting V3 until the exact V4 request suite is qualified and real
44-minute/two-hour/four-hour editorial evidence is reviewed. Contract:
[bounded-opportunity-inventory-2026-09-14.md](docs/design/bounded-opportunity-inventory-2026-09-14.md).
Author packaging and whole-portfolio source review are still unbounded final-answer stages, so
their decomposition is next. No real provider, latency, recovery or quality result follows, and no
new model arm or paid run is authorized by this record.

**2026-09-14 — A stopped run resumes under any build that speaks its editorial programme;
the build that resumed it is recorded; a refused retry is reported to the reader.** Rajesh
pressed Retry on the run that #46 fixed and it failed in three seconds: the run's frozen
evaluation programme includes `implementationSha256`, a digest of every Python file in the
pipeline, so the deploy that fixed the defect made the run non-resumable, and the refusal never
reached the panel because it happened before the run was claimed. Now a product run (no explicit
programme on the request) is compared on `editorial_identity`: policy, programme version and the
five stages' prompt-template and schema hashes; the resuming build is appended to
`route_snapshot.resumedImplementations`; changed prompts or schemas still refuse, with a sentence
that says to start a new run. An experiment claim (explicit programme) stays strict, build
included. The web retry action waits up to eight seconds for an early failure and shows its
innermost sentence as "Retry refused: ..."; a run still going after the window is a started retry.

**2026-09-14 — The Modal render function imports only light modules, the release smoke proves
the render path, and a function that cannot start falls back to the CPU.** The first GPU render
after #45 failed on the card with `No module named 'psycopg'`: `render_sections` imported a
harness module that imports the database driver, the deploy smoke exercised only speech, and the
activity retried three times before the run failed at $1.35 spent. Now: the render job contracts
live in `render_contracts.py` and the timeline identity in `media/timeline_identity.py`, both
pydantic-and-stdlib only; `RENDER_MODULES` names what the function imports at call time and a test
loads that list with `psycopg`, `temporalio`, `av` and `torch` made unimportable; `render_probe`
imports the same list on the card and encodes one second through `h264_nvenc`, and the smoke calls
it before the speech sample, so a broken render deploy fails the deploy; and a call that fails
with an import error is treated like an absent function: the worker renders that run on its CPU
and says why, rather than retrying. Log: `docs/log/2026-09-14.md`.

**2026-09-14 — GitHub Actions in this repository: GitHub-owned or verified actions only,
every `uses` pinned to a commit SHA.** The first `modal-deploy` run failed at startup because
the repository policy (`allowed_actions: selected`, `sha_pinning_required: true`) refused
`actions/checkout@v4` and a third-party setup action. Pin `actions/checkout` to its commit
SHA with the tag in a comment, and install tools from their registries at pinned versions
(uv from PyPI) instead of through actions.

**2026-09-14 — Every deployable deploys from a push to `main`; nothing ships by hand.** Rajesh,
handed a `modal deploy` command in a runbook: "deployments should be automatic when code
merges to main until and unless a manual deployment is required." Dokploy already deployed
the two images from main; the Modal media app was the exception, and
`.github/workflows/modal-deploy.yml` removes it (deploy on push to main touching the pipeline
package, then the GPU smoke, environment `staging`). Two rules follow. A PR that adds a
deployable adds its automation in the same PR. Configuration that depends on a deploy must
degrade gracefully until the deploy lands: the deployment file now says `render: modal /
h264_nvenc`, and a worker that finds `render_sections` absent renders that revision on its
own CPU with libx264 and logs why, instead of a runbook asking for ordering by hand. The only
acceptable manual step is a one-time secret or account action, named once with the reason it
cannot be automated: here, the `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` repository secrets.

**2026-09-13 — Equivalent extent repairs preserve authority; settled refusals get bounded
correction.** Karma run `a562673b-6723-47b8-a425-476f061f44dd` withheld four candidates after
one `replace_candidate` changed both edges and annotations but kept title/purpose. The
patch prompt suggested that combination while validation required a title/purpose change.
Patch prompt `/10` distinguishes the operations. The existing in-memory normalizer treats
this exact extent-only effect as `replace_extent`, preserving raw paid output and every
finding/source/physical/atomic check. A typed, settled patch rejected by validation now
uses the remaining shared repair allowance with the rejection artifact and diagnostic
bound into a distinct request; the unchanged selection is not reviewed again first.
Rejected-output hashes may themselves be wrong: authority comes from the rejection's
actual request dependencies. Admitted corrections clear rejection context and receive
fresh review. Unknown outcomes remain fenced. This repairs admission and bounded
correction, not the editorial criteria or fresh-execution retry accounting. The original
four-operation patch passes offline without changing its raw operation labels; its
editorial adequacy remains unmeasured. Plan: `docs/plans/topic-repair-recovery.md`.
By the same rule a title-only `replace_candidate` is the equivalent `retitle`, and a title
changes only under an unsupported-title finding whichever label carries it. A purpose-only or
annotation-only correction is a `replace_candidate` under that axis's finding (prompt `/11`), so
no grounded correction is unrepresentable; only prose-only changes are refused.
**2026-09-13 — Topic videos render on the Modal GPU; the worker verifies and publishes.**
Second part of `docs/plans/media-placement-360-view.md`. `render_sections` in the media app
renders every missing section of a revision from one download of the master with the
worker's own `render_chapter` command and `h264_nvenc` (constant quality 18, preset p5,
high profile); the worker's `_render_missing_sections_remotely` builds the job, spawns or
reattaches by the call id carried on the activity heartbeat, verifies every output's size in
the store and its hash after download, and the per-section loop publishes the file exactly as
a local render. `ChapterRenderConfig` carries the encoder, so GPU and CPU renders are
different media artifacts. Smart-cut (copying the master's GOPs and re-encoding only the
edges) is deferred: mixing parameter sets inside one MP4 is not something browser decoders
promise to play, and the panel plays these files in the browser. The deployment file's
`render.backend`/`render.encoder` selects the path; staging stays `local`/`libx264` until the
media app is deployed with the new function, then one commit switches it. A local backend
refuses the GPU encoder at boot.

**2026-09-13 — Source sensors are measured at ingest, once; a topic run downloads nothing
until it renders.** Rajesh asked which steps belong on the GPU and which on the CPU; the plan
is `docs/plans/media-placement-360-view.md`. The first run on every source used to pay the
master download, `scdet` over the whole master and Silero VAD inside the topic run, on the
VPS, before the first model call. Now `measure_source_sensors` runs in the ingest workflow
after `derive_source`, while the master is still in the source work directory, and publishes
three records bound to the master's object identity (ETag or version ID, key, size):
`source-timeline/1` (the master's sha256 and exact stream facts), the shot record and the
speech record. `build_chapter_evidence` heads the object, finds the timeline record, takes
the hash and timeline from it, finds the two sensor records by their bindings and assembles
evidence with no download; a source without records (ingested before this, or a sensor that
failed at ingest) falls back to the download path unchanged. Two identity rules changed to
make this hold: the shot record's fingerprint no longer includes the ffmpeg binary hash (it
stays in the body as provenance, so a deploy with a new ffmpeg does not send every source
back through a decode), and speech is measured without a transcript (the duration equality
check moved to the projection onto a transcript, where it belongs). A sensor that fails at
ingest never fails the ingest. Rendering still fetches the master; moving that encode to the
Modal GPU is the plan's second part.

**2026-09-13 — A transient provider failure never ends a run; it retries, falls back, and the
run can always be resumed.** Rajesh, after three staging runs (one success, a 402, a 429 that
discarded $0.38 of work): "With such inconsistency how can we even launch our product to the
public?" The audit is `docs/design/harness-robustness-2026-09-13.md`. Rules from it: an HTTP
status before any response (408, 425, 429, every 5xx) is a transient failure with a known
zero cost and a released reservation, retried once on the route after the longer of 20 s and
the provider's Retry-After, then on the next qualified route in the seat pool (`author_index`,
`verifier_index` on the selection context, sticky for the run, reviewer pool filtered by the
author's family at every position); only when every route has failed does the run end, naming
them all. The worker admits provider calls per route (`limits.maxInFlightPerRoute`,
`limits.minDispatchIntervalSeconds` in the deployment file; staging 2 and 1 s), taken before
the dispatch is committed, because providers throttle the account, not the run. Cold reviews
fan out concurrently within that bound. An unconfirmed outcome is reconciled from gateway
receipts in the workflow's failure path; a run in `pending`, `failed` or `budget_paused` can be
taken over by a new execution and the web's **Retry this run** does exactly that, replaying
settled responses by request identity. A stale server action after a deploy is explained as
such. Model activities still never retry at the Temporal level: every retry is a paid decision
the workflow owns.

**2026-09-13 — A physical-only repair is judged by its effect, not its operation label.** The
first staging run (`fc2000e9`, 15 calls, $1.05, no technical failure) produced 11 candidates
and 8 required findings on 7 of them; Kimi's repair addressed all 8 in six `replace_extent`
operations and the harness refused it because one operation, a one-sentence opening
extension for a physical-only finding, was not spelled `extend_start`, while the repair prompt
says `replace_extent` "may move either or both edges … including extension". The atomic rule
then discarded the other five; four videos rendered instead of eleven. The validator now names
the extended edge from the replacement's effect (exactly one edge, moved outward) and applies
the same physical-only checks as before; any other shape is still refused. The stop reason
carries the refusal diagnostic so the panel says why; withheld candidates are listed with
their findings. Two smaller corrections from the same day: HTTP 402 is reported as gateway
credits or a key limit (the earlier text sent the reader to the route snapshot), and a
conclusive HTTP rejection settles at a known zero cost so the run's reservation is released.
The atomic-repair rule stays; whether partial acceptance should replace it is decided on the
re-run, not before.

**2026-09-13 — Merge, deploy, click: the images carry the harness configuration.** Rajesh,
shown the eleven-step rollout (root-owned file on the VPS, Dokploy bind mount, two
environment tables, ordered reloads): "Why do I need to do all of these circus to be able
to run the pipeline?" He is right; the guarantees were sound and their delivery was design
debt. The guarantees stay: the route snapshot is an immutable content-addressed file, the
worker refuses to boot on a mismatch, the web sends exactly the worker's run config. The
delivery is now one committed file per deployment, `apps/pipeline/harness/staging.json`
(`harness-config/1`, a shared contract), next to its snapshot; both Dockerfiles copy the
directory to `/app/harness/` and bake `HARNESS_CONFIG_PATH` to it. With that variable set,
every other `HARNESS_*` environment entry is ignored and the worker's boot log names the
ignored entries, so stale Dokploy values cannot disagree with the file; empty means the
environment is the configuration (the gate, local development, the experiment operator,
which pins its own snapshot). The only value left on the box is the `OPENROUTER_API_KEY`
secret. `tests/test_harness_config_file.py` boots every committed configuration in the
gate, which is where the two blockers found by hand today (three-family seat pools; a
32,768 run-config ceiling under a 65,536 setting) now fail. Changing the roster or a limit
is a PR. Production gets its own file and one Dokploy variable pointing at it.

**2026-09-13 — Staging roster settled on technical reliability; chapters are gone; the
harness is the only goal until results are satisfactory.** Rajesh: Temnia is not live, existing
chapter and topic runs need no compatibility, chapters are gone and only standalone topic videos
remain, work is done without delegation, and the model selection is settled on what completes
without technical failure. Evidence from the full-source Karma runs: Kimi K3/Fireworks completed
every author and repair call in r11, r15, r16, r18 and r20 (zero transport failures in the
author seat); Gemini 3.8 Flash/Vertex at medium effort completed inventory, cold and source
review in r11, r18, r20, r22 and r25 (37 settled calls in the r20/r25 bundles alone) and failed
only by an upstream rate limit delivered inside an HTTP 200 stream (r13 cold review, r23 as
author) and by exhausting a 32,768 output allowance on reasoning (first v3 source review, a v2
repair); DeepSeek V4 Pro 0813/Fireworks completed authoring in r24 and authoring plus repair in
r25; Kimi as full-source reviewer crossed the 540 s deadline once in one attempt (r24); Astra
failed two full-source author calls on the deadline. Settled roster: verify pool Gemini 3.8
Flash first, propose pool Kimi K3 first and DeepSeek V4 Pro second, Astra excluded, all through
OpenRouter with `gateway-transport/2`; every pool lists all three routes because a production
seat pool needs three model families. The file is committed as
`infra/harness/topic-routes-staging-0df7f78d.json` (ID `0df7f78d…`), and the run-config
ceiling `maxOutputTokens` was raised from 32,768 to 65,536 so the worker boots on it. This is a reliability selection recorded with its
evidence, not an editorial audition; the no-default-vendor rule's audition is still owed on a
calibration set. Two technical fixes make the roster hold: a lost stream whose generation
receipt reports a settled charge is now a known failure (`TransientProviderFailure`) that the
workflow retries twice with 30 s and 90 s backoff before ending the run with route, stage and
cost named, while a pending receipt keeps the unknown fence; and staging runs with
`HARNESS_MAX_OUTPUT_TOKENS=65536` (Gemini's route maximum, clamped per route) and
`HARNESS_MAX_DISPATCHES=64`. The chapter lane (navigation partitions, `chapter-editorial/1`,
Chapter-Llama) is deleted on 13 September: the web tab, panel, actions, API route and journey
(5,393 lines) and the pipeline program (workflows, hierarchical summaries, proposal diagnostics,
editorial verify/repair, Chapter-Llama, chapter evaluation bundle and report, legacy and editorial
pre-flight suites; 94 files, 24,540 lines). What the topic lane used moved: run failure messages
to `harness/run_failures.py`, shared evaluation wire models to `evals/common.py`, the synthetic
pre-flight transcript to `harness/qualification_fixture.py`. Editorial policy is topic-only and a
replay under another program generation is refused. The `chapter_*` names that remain
(`ChapterRunInput`, `chapter_revision`, `start_chapter_run`, the per-video `chapter-edit/1`
execution) are the shared run and render machinery the topic lane is built on; renaming them is
churn for later. The v1/v2 topic programs went the same evening (84 files, 6,658 lines removed,
1,361 added): one workflow type `TopicSelectionWorkflow`, one policy literal
`standalone-topics/3`, one patch and one source-review schema (`TopicSelectionPatchV3`,
`TopicPortfolioReviewV4`), no `topic-assessment/1` reader, no chapter summary-grounding view,
and one recorded fixture `topic.synthetic.json` that carries the five schemas as a discovery
case (empty inventory and author, a required omission finding, one grounded repair, then cold
and source review of the recovered treatment); settings refuse a recorded fixture that lacks
any topic stage output. `qualify_harness_gateway.py` keeps only the `topic-selection-v3`
suite. Prompts were read in full on 13 September: they are user-turn
rule lists with no system instructions, negative-heavy, example-free, repeating the no-count rule
across seats, carrying jargon and ruff line-wrap artifacts, and each incident added a paragraph;
the decisions doc §4 findings stand. The rewrite is measured against the first staging run as
the baseline, not before it.

**2026-09-13 — Topic generation on staging is one button, one program, one worker; the
qualification manifest is no longer a gate.** Rajesh could not start the current program on
staging: the default button refused for want of a per-version flag on two processes and a
bound five-stage qualification manifest that every prompt or schema bump invalidated, and
the routes that finished full-source runs were not in the worker's snapshot. New topic runs
start only `standalone-topics/3` (its DB literal and workflow type name are unchanged); `/1`
and `/2` were deleted the same day (entry above); Temnia is not live, so no run of either needs
reading. The manifest, `bind-topics`, the manifest
formats and the `HARNESS_TOPIC_SELECTION_*` flags and paths are removed. Admission is in-run:
the first settled call on a snapshot, route, stage and program identity is the proof; a
provider refusal ends the run `failed` with a message naming route, stage and HTTP status and
the charge retained; `outcome_unknown` now carries a message. The ledger's reservation,
receipt and unknown fences are unchanged; `qualify_harness_gateway.py run` remains an
optional pre-flight that binds nothing. Chapters (navigation partitions) and topics
(standalone videos) are distinct lanes since 10 September; only topics are the product now,
so the chapter lane is parked on staging with `HARNESS_CHAPTERS_ENABLED=0` (creation refused,
existing runs reviewable) and the one worker carries the OpenRouter topic snapshot. OpenRouter
is the gateway because the Vercel full-source calls timed out repeatedly. The single default
brief is the Python `EDITORIAL_BRIEF`; the web omits `brief` when the box is empty and the
worker freezes the effective text on the run; every topic run carries its program manifest;
the shot detector default is `scdet` in code. Worker boot checks every pool route against the
effective topic output ceiling; chapter starts keep their strict run-start check. Long sources:
admission is `admission/2`, input tokens = ceil(bytes / 2) + protocol overhead (the old 1:1 rule
refused a 2.5-hour source on every 256k route; the 512 KiB payload cap stays; each run records its
admission version in the frozen snapshot); a route's frozen aggregate deadline is the unit per
128 KiB of payload, the model activity's start-to-close is the effective deadline plus 60 s, and
payloads up to 128 KiB keep exactly the frozen values so recorded runs are unchanged. The per-call
activity option reaches pydantic-ai through a subclass of its private durable model operation on
the pinned version; an upgrade that renames it fails at import and the gate, never silently. The staging
roster (Kimi K3/Fireworks author first, DeepSeek V4 Pro/Fireworks second, Gemini 3.8
Flash/Vertex reviewer first; Astra excluded) is the r25 snapshot reordered and remains
provisional under the no-default-vendor rule: it finishes runs, it has not won. Later the same
day the chapter lane was deleted outright (web and pipeline, see the entry above), so the
`HARNESS_CHAPTERS_ENABLED` flag described here no longer exists. Acceptance for
the first staging run is sentence-complete cuts with the pause owned by the preceding video;
topic-ownership defects are corrected by hand and the compound-candidate gap is the next
program problem. Overlap between standalone videos is allowed for setup context both videos
need, never for core, and any overlap longer than a short premise is an ownership question
the reviewer must answer. This supersedes the 2026-09-11 four-schema qualification
requirement and the decisions doc §2 procedure. Plan:
`docs/plans/topic-generation-staging-360-view.md`.

**2026-09-12 — Human playback accepts sentence-complete Karma cuts; trailing pauses and
semantic handoffs are the remaining release refinements.** Rajesh reviewed the nine admitted r11
videos and found every video technically complete with no broken sentence at either edge. Do not
erase that measured result by continuing to describe the harness as unable to produce independent
videos. Two narrower findings remain. First, an inter-utterance pause belongs to the speaker and
video that precede it; `topic-compiler/3` selects the latest safe source-grid instant before the
next speech, preserving all speech and limiting any unavoidable 25 fps opening residue to less than
one frame. Second, when one selected topic begins inside a neighbouring video's tail, move that
premise out of the earlier video and into the later video so the complete discussion has one clear
owner. This is semantic extent misallocation, not blanket duplicate suppression. Shared context is
still allowed when both independent videos need it. The first follow-up Karma run proved that prompt
language alone is insufficient: the author produced the correct later start but retained eleven
mantra-transition sentences in the prayer video, and the source reviewer silently accepted both.
V3 therefore supplies every exact candidate overlap and requires one typed classification for each;
omission is invalid, and misallocated or duplicate core must carry a required two-candidate finding.
Historical V2 review remains on its original schema. The r15 author assigned prayer through s239 and
mantra from s240,
which is the intended ownership. Its repair exposed a separate edit-language gap: one candidate
needed an extent and title correction, but two operations on the same candidate are forbidden. V3
now has one `replace_candidate` operation for that coupled correction. It retains the candidate ID,
stays inside finding-authorized source, and requires independent findings for every changed content,
title or purpose axis. Requalify the exact OpenRouter patch request and validate on Karma before
calling these refinements production-ready. The r16 reviewer later treated prayer-completion speech
as necessary shared context and repair chased anaphoric openings backward until mantra began at s232,
overlapping prayer through s247. This is invalid ownership. `necessary_shared_context` is admissible
only when the exact overlap lies in both candidates' `requiredContextSpans`; core or completion has
one owner. At a handoff, author and repair must trim connective runway to the first self-contained
new-topic premise when extending backward would annex a completed neighbouring discussion. The r18
run exposed the corresponding non-overlap loophole. It oscillated between s248 (a dependent “And…”
opening) and s251 (missing the self-contained online-sadhana setup), converged at s249 after three
repairs, but still assigned the preceding sadhana transition through s247 to the prayer video and
declared the portfolio complete. V3 now supplies every adjacent non-overlapping candidate pair with
fixed left/right context windows. The independent source reviewer must classify every handoff and,
for misallocation, provide the exact final left `lastSentenceId` and right `firstSentenceId` plus one
required two-candidate finding. The patch must implement both reviewed edges exactly in one atomic
transaction. This is `topic-selection-portfolio/4`, source prompt `/8` and patch prompt `/9`; the
historical overlap-only V3 response remains readable. The exact OpenRouter qualification passed all
five stages on Astra, Gemini and Kimi after one separately retained Gemini author HTTP 429 was
replaced by a successful call. The accepted qualification evidence cost $0.640177. The subsequent
r20 Kimi-author/Gemini-reviewer Karma run spent $0.907369 across 19 settled calls with no reservation
and rendered seven videos, but failed Rajesh's ownership criterion: its final author selection merged
prayer and mantra into one s202–s312 candidate, and the final reviewer explicitly accepted that
compound span as one distinct topic. The adjacent-handoff control works only when the author has
already represented both discussions as candidates; it cannot adjudicate a missing internal split.
Treat r20 as a model-configuration failure, not acceptance of the program or a reason to add
Karma-specific logic. A controlled r22 comparison changed only the author from Kimi K3 to Astra,
with Gemini review and every other source, prompt, schema, detector, compiler and execution factor
fixed. Its inventory settled for $0.057453, but the Astra author stream exceeded the frozen
540-second aggregate deadline without a complete response or generation handle. The run is
`outcome_unknown` with its $5.271530 reservation retained and must not be replayed. This is Astra's
second full-Karma transport failure despite passing short qualification calls, so it is unavailable
for this workload. A declared r23 configuration comparison swapped the roles to Gemini author and
Kimi inventory/review. Its Kimi inventory settled for $0.235307, then Gemini returned HTTP 200 and
an upstream rate-limit error before a complete response. Run
`6f156f15-b07a-5f00-a3ce-cfc0412d4d3b` is `outcome_unknown` with its $0.213370 reservation retained;
do not replay it. A separately qualified DeepSeek V4 Pro 0813/Fireworks challenger then authored
with Kimi inventory/review. Its exact five-stage qualification cost $0.144163. Full Karma run r24
settled 22 calls for $1.235900: inventory, a 20-candidate author proposal and every cold review. The
proposal improved representation by separating prayer at s223–s240, but overlapped its following
sadhana candidate at s238–s267 and divided the connected mantra discussion among that candidate, a
spiritual-window-shopping candidate at s268–s292 and karmic-collision effects at s293–s312. It did
not yet meet the required single-owner discussion. Kimi's full-source review then crossed the frozen
540-second deadline without a complete response or handle. Run
`02ad8512-6cca-5248-b845-1aa59a5380ae` is `outcome_unknown` with its $1.015747 reservation retained;
no patch, final review or render followed and the request must not be replayed. A final controlled
r25 pairing keeps DeepSeek authoring and changes only the reviewer route to Gemini, whose prior
full-source Karma review completed. R25 settled 13 calls for $0.430738 with no reservation and
rendered all nine proposed videos, but also failed the human criterion. DeepSeek merged prayer,
continuous sadhana, unguided mantras and karmic effects into one s223–s308 candidate. Gemini's
source review explicitly called that compound span complete and distinct, found no internal split,
and classified every external handoff as clean. A repair for an unrelated opening finding was
invalid and retained no changed selection. The run ended `needs_review`. No tested configuration is
an editorial winner. The measured remaining representation gap is internal topic structure inside
one candidate; adjacent overlap and handoff judgments do not observe it. Do not start more model
arms or add source-specific rules to hide that program limitation.

**2026-09-12 — New topic runs stick to FFmpeg `scdet`; AdaptiveDetector is not the default without a trial.**
Rajesh asked which scene detector to keep if the requested AdaptiveDetector trial
is not going to run. Keep `scdet`. On the verified Karma master the two detectors
agreed on 403 cuts (428 adaptive vs 409 `scdet`) while AdaptiveDetector took
211.5 s against 15.6 s; that is agreement and cost, not editorial ranking.
AdaptiveDetector’s motion-robust baseline matters for handheld or continuously
moving picture, not a typical locked-off podcast camera switch. Neither detector
establishes discussion completion. Record:
[standalone-topic-decisions-2026-09-12.md](docs/design/standalone-topic-decisions-2026-09-12.md) §1.
This supersedes the 10 September “use AdaptiveDetector for the next topic trial”
default. Historical snapshots keep their frozen detector. AdaptiveDetector remains
an explicit comparison. Do not broaden this into detector shopping or an
orchestration change. This entry is not yet applied in code. As of PR #40 new runs
take `harness/settings.py` (`HARNESS_TOPIC_SHOT_DETECTOR`, default
`pyscenedetect-adaptive`) and `topic_experiment.py` defaults to the same; the `scdet` in
`runtime_types.py` and `runs.py` is only the fallback for snapshots recorded before the
field existed. The v3 record and the 12 September runs above record AdaptiveDetector. The
next settings PR changes the code default and the staging variable to `scdet`; until then
state the detector explicitly per run. Do not leave both defaults standing.

**2026-09-12 — Topic blockers are editorial operations and calibration; no agent framework.**
A read-only review of the evidence, author, review, repair and compile stages, re-checked
against `main` after PR #39 (`standalone-topics/3`) and again after PR #40, is recorded in
[standalone-topic-decisions-2026-09-12.md](docs/design/standalone-topic-decisions-2026-09-12.md)
§3–§9 with per-finding status. #39 closed the control failures (inventory-first by the reviewer
family, rationale-free source critic, `replace_extent`, three re-reviewed repairs, a select-only
render gate). Still open: byte-as-token admission with no topic hierarchy (a two-hour source is
refused), out-of-extent spans refused instead of unioned, word-gap "pauses" and a binary Silero
veto in the compiler, no critic calibration set, no listening judge, and prompts that are user-turn
rule lists with no `instructions=` and no `.describe()` on contract fields. Rajesh asked whether
LangChain/LangGraph/Google ADK should supply checkpoints, resumes or agent loops: no. Their
Temporal plugins disable their own persistence and add notation only; Temnia's guards live inside
the PydanticAI model activity. The 2026-09-07 typed-program decision stands (§5). Rajesh's first
playback review of the nine admitted r11 Karma videos (13 September, §7) is the measured result
recorded in the entry above: every cut physically complete, no sentence broken at either edge, two
defects. Their root causes are code-side, and #40 answered both in part. The trailing pause was
split because `topic-compiler/2` targeted the word-gap midpoint at every transition on a
first/mid/last grid; `topic-compiler/3` now gives the whole pause to the preceding video, which
closes the ending. The opening now starts at the latest safe instant before the first selected
word with no lead-in; whether a 200–300 ms lead-in sounds better is a listening preference under
C2, not a defect, and no acoustic silence is measured yet (E2). Two videos shared core content
(mantra, videos 4 and 5) because core overlap was never computed; `topic-selection-portfolio/4`
now hands every exact overlap and adjacent handoff to the source reviewer with a required typed
classification. The classification is still the model's: the deterministic core∩core finding
proposed in §7b was not adopted, and r20/r25 showed the loophole neither can see, a single compound
candidate with no overlap to classify. That internal-structure gap is the program's open problem,
as the entry above states; give it a program answer before starting more model arms (§9).
One informal review is not a labeled set. KernelCPD change-points are built but unreachable from
production (`segmenter: Literal["sat"]`); run them as scored hints in a code-side source map,
never as compiler candidates, measured on the calibration set first (§8). No author or reviewer
seat is finalized. Transport ceiling is now a selection criterion in its own right: Astra failed
two full-Karma author calls and Kimi one full-source review inside the frozen 540 s, so those
routes cannot be auditioned on longer sources at all (§9).

**2026-09-11 — OpenRouter is an explicit model-audition transport.** Rajesh merged
PR #37 and configured the staging pipeline key. The
[transport plan](docs/plans/openrouter-model-evaluation.md) qualifies new requests
through OpenRouter while preserving the existing editorial programme and paid-call
ledger. Routes freeze gateway, streaming mode, idle/aggregate deadlines and accounting
provider identity; legacy routes and receipts keep their original interpretation.
The first condition uses high effort, 32,768 output tokens, 300-second idle and
540-second aggregate deadlines. Internal streaming records an observed generation ID
before content when available, drains the complete response, and keeps incomplete
outcomes fenced. Actual provider receipts determine expense. No unknown request is
replayed by switching gateways, and no model or gateway winner is inferred from
qualification.

The initial four routes are Astra/Azure, Opus/Amazon Bedrock, Gemini/Google Vertex
global and Kimi/Fireworks. This is a declared comparison, not a production default.
Provider changes from Vercel and base-slug regional routing remain explicit factors;
the Vercel Opus output restriction is not assumed for OpenRouter/Bedrock. Whole-source
development runs follow successful exact qualification. Human opportunity and
publication acceptance measurements remain necessary.

The first OpenRouter cohort stopped at five requests: four Astra parameter-admission
404s and one complete Opus/Bedrock response whose canonical accounting model differed
from the request alias; its prose also failed the required JSON contract. Preserve
that original report and expense fence. `gateway-transport/2` binds endpoint-specific
output-token spelling and catalogue-bound canonical accounting model separately from
the request alias. The second declared cohort substitutes Opus/Vertex global and
tests its actual schema capability, retaining the catalogue's support uncertainty.
Neither an accounting correction nor a catalogue flag can turn the Bedrock prose
into qualifying output. Details and source/image verification are in the transport plan.

The second cohort completed sixteen requests: Astra, Gemini and Kimi passed all four
native stages, while Opus/Vertex returned four conclusive parameter-admission 404s.
It settled $0.588704 with no new unknowns. A separately declared full-Karma development
comparison uses Astra and Kimi authors with fixed Gemini review; both role pools retain
all three qualified families. The unavailable Opus design and all original failures
remain recorded. Qualification is still separate from full-source editorial acceptance.

The full-source runs are now retained: Astra's author stream failed with an early
handle and an unknown fence; Kimi/Gemini produced eleven review videos after a
rejected repair; a separately declared Gemini/Kimi configuration retained four
initial candidates but failed on a length-truncated repair before rendering. No
editorial winner follows. The [evidence record](docs/design/openrouter-topic-model-evaluation-2026-09-11.md)
documents observed omission/completion problems, conflicting critic judgments, the
repair prompt/schema/validator mismatch and absent direct trimming. These are
measured execution outcomes and source-grounded diagnostic hypotheses, not human
publication scores. All experiment workers stopped; no unknown request was replayed.

**2026-09-11 — Model auditions freeze the programme and use the production workflow.**
Rajesh requested model evaluation after merging PR #36 (`a96b571`). The
[model evaluation plan](docs/plans/topic-model-evaluation.md) keeps the observed staging
configuration, new quality-first request settings, and programme changes as separate
conditions. Qualification checks actual route/prompt/native-schema/settings requests;
it does not establish editorial ability. The controlled operator freezes source and
transcript pins, rubric, intended stage roster, configuration and stable run identities,
then starts the existing `TopicSelectionWorkflow` on isolated arm queues. Conditional
repair is an observed outcome under that same programme. Historical missing programme
identity remains unknown rather than reconstructed from today's constants.

Human source opportunities and cold/source/playback judgments determine selection and
publication acceptance. Karma and the 151-minute World Order recording are development
sources, not fresh holdouts. No model winner follows from transport success or an
uncalibrated model critic. Keep unknown paid outcomes fenced, record all costs, and keep
the ordinary staging configuration separate from declared experiment workers. Current
evidence and limitations are in
[the audition record](docs/design/topic-model-evaluation-2026-09-11.md).

Qualification binding may use unchanged terminal halted reports for completely settled
required stages of unrelated routes. Any selected model/provider with an unsettled call
in any referenced report remains excluded; original report hashes and unknown expenses
remain intact. Do not relabel a halted cohort, subset its journal or replay an unknown
request. The observed high/32,768 cohort admitted only two author families; its planned
full-workflow matrix stays unavailable under the three-family policy. A separate
high/8,192 condition also proved unavailable: Gemini source review exhausted its output.
The next declared reference uses explicit route ceilings: Astra/Kimi author and Gemini
review at high/32,768, Opus author/repair at high/8,192. V2 derives actual output from the
minimum of the run and immutable route ceilings and binds those exact settings through
qualification, requests, expenses and evaluation. Older workflows retain strict global
ceiling admission. Astra/Kimi can be a controlled model swap; the Opus comparison changes
both model and output configuration. Earlier failed conditions remain evidence. These
ceilings describe model requests, not useful video count or duration.

The first full-source author trial ended at the client's 300-second read timeout,
with no generation handle or returned selection. Rajesh then reported better
OpenRouter reliability in his experience. New Vercel experiment starts are held for
that assessment. The observed timeout does not establish which upstream layer was
slow. Any OpenRouter comparison must bind gateway/provider/request-policy identity,
qualify its own strict requests and retain expense/unknown-outcome fences. Streaming
is a separate configuration factor. No gateway or model winner has been established.

**2026-09-11 — Standalone selection quality has a versioned decision and evaluation contract.**
Rajesh prioritized both missed worthwhile discussions and weak selected discussions, and authorized
[the complete selection-quality programme](docs/plans/topic-selection-quality.md) after review of
the two supplied engineering plans. `standalone-topics/2` retains source-linked opportunities,
one frozen audience rubric, isolated cold value/comprehension judgments, source-wide omission and
portfolio review, and finding-scoped atomic add/merge/split/extent/title/drop repair. A partial
repair retains every known opportunity; physical-only authority cannot rewrite semantic annotations.
Human corrections are source-bound immutable revisions and invalidate changed-content acceptance.
V1 remains an explicit comparison and its histories/artifacts remain supported.

New v2 gateway calls require exact four-schema/prompt/settings qualification; the web and worker
rollout flag defaults off. Synthetic qualification and production-image mechanics do not establish
editorial improvement. The standalone evaluator separates editorial value/recall, physical delivery,
and media-inspected acceptance; whole-source independent human labels remain necessary. No model,
provider or detector winner is selected by this implementation. See the
[qualification](docs/runbooks/topic-selection-qualification.md) and
[evaluation](docs/runbooks/topic-quality-evaluation.md) runbooks. Keep the existing runtime,
source/expense fences and media foundations; do not broaden this into unrelated hardening.

**2026-09-10 — The immediate editorial target is independently publishable topic videos
(Rajesh's clarification).** By chapters, Rajesh means interesting standalone discussions extracted
from a long video for YouTube or Facebook. Necessary surrounding context may appear in more than
one video: standalone quality takes priority. A generic action must do this without a custom
prompt; content determines useful count and duration. This supersedes the September 4 exact-cover
requirement for these exported videos, not the need for source grounding and accountable source
use. Navigation partitions and independent topic videos are distinct outputs.

The research in `docs/design/standalone-topic-intelligence-2026-09-10.md` found a real objective and
representation mismatch, missing/reduced legacy editorial operations, and a full Karma run whose
proposal repairs left no independent reviewer. The historical 18% legacy result predates its later
Reconciler and Publisher and must not be treated as their acceptance measurement. Technical checks
and schema qualification are not publication-readiness evidence. The research did not identify a
winning model or establish that competitors solved unattended extraction.

`docs/plans/standalone-topic-intelligence.md` defines the focused implementation and measured
comparisons: independent contiguous spans, completion and faithfulness judgments, an available
independent reviewer, and acceptance on fresh full sources. The initial program, generic action,
independent renderer and human review are now implemented on the PR branch, recorded in
`docs/design/standalone-topic-implementation-2026-09-10.md`; real editorial acceptance remains
unmeasured. Rajesh requested that the harness try PySceneDetect AdaptiveDetector before deciding
between scene detectors. Use it for the new topic-policy trial, retain FFmpeg as an explicit
comparison, and freeze detector identity in run configuration and evidence. Package tests and
detector agreement do not establish superiority. Keep the existing runtime/evidence/ledger/media foundations; do not
broaden into an orchestration rewrite. Incidental findings are recorded in
`docs/plans/editorial-research-deferred-backlog.md` and stay deferred unless they prevent a valid
editorial experiment. Rajesh explicitly asked to stop the minor-bug fix loop and target harness
intelligence. Do not add arbitrary duration, output-count or experimentation-spend caps; continue
to record costs and preserve unknown-outcome fences.

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

**2026-09-09 — A chapter run's error is current state; prior failures remain in durable history.**
The first staging retry after PR #27 displayed a previous terminal error beside a running status.
Permitted retry and budget-paused resume clear that current error atomically, and a successful
pending execution claim clears stale pre-deploy state too. Refused commands and a budget increase
that does not resume a failed run preserve its error. Temporal and review events remain the
history. Budget drafts belong to the selected run and are not overwritten by status polling.
The observed cases and verification plan are in `docs/plans/chapter-resume-states-360-view.md`.

**2026-09-09 — Invalid summary citations can fall back to exact source excerpts.** The resumed
151-minute run reused its seven original responses and completed 25 summary windows, then rejected
10 prompt-visible quote anchors across five windows because they belonged to other units. Validate
each response before starting the next summary. Only after proving the complete sentence partition,
an otherwise real visible anchor attached to the wrong unit permits replacing that entire unit's
generated prose and citations with its exact source excerpt. Foreign IDs, inferred anchors and
structural coverage errors remain refusals. Preserve the raw response, publish explicit immutable
fallback provenance, expose it to the reviewer and retain it for model evaluation. This adds no
provider call and consumes no semantic model-repair slot. Compared with repeated paid summaries,
a larger repair call and silent citation reassignment, it preserves grounded content at lower
execution risk. Prompt/context bounds remain enforced. The model-response normalizer above stays
cosmetic; this separate derived artifact does not rewrite the model's answer. Plan and compatibility
tests: `docs/plans/chapter-summary-grounding-360-view.md`.

**2026-09-09 — Proposal output is bounded independently of source context.** After summary
grounding recovered all 25 saved responses, the live global proposal exhausted 8,192 output
tokens before covering the whole source. Exhaustive quote arrays and pretty-print whitespace
dominated its output; the repair repeated the request without feedback and timed out. Use a
versioned compact internal proposal schema with at most two representative anchors per section
and bounded editorial text. Derive section labels in code, while leaving chapter count to the
content and keeping the canonical edit contract compatible. Preserve the paid raw response and
an immutable, content-free diagnostic; bind the one independent-family repair to that evidence
and estimate its complete prompt before dispatch. Old Temporal histories keep their original
agent and request shape. Raising caps, completing partial JSON and silently trimming output were
rejected because they hide the failure rather than reduce avoidable work. Unknown transport
outcomes retain their reservation; a new run is separately budgeted work, never a settlement or
reuse of a missing response. Plan: `docs/plans/chapter-proposal-output-360-view.md`.

**2026-09-10 — Route qualification follows the exact request shape and real-source admission.**
The compact-wire requalification passed DeepSeek/DeepInfra and Kimi/Alibaba, but Qwen/Alibaba
returned the requested summary schema inside an array instead of summary data. The validator
correctly refused it; the normalized receipt cannot distinguish the model, provider adapter or
gateway adapter as the cause. Its earlier success does not qualify the new three-stage path.
A separately bounded GLM-5.3-Flash/Baseten probe passed all three stages and is the replacement
for the provisional staging pool, deployed with matching verified web/worker configuration.
GPT-OSS/Baseten was
considered but rejected before paid qualification: its 131,072 context does not fit the measured
179,187-unit compact request under the harness's byte-conservative admission. Qwen/Parasail and
GLM/DeepInfra were compared on current context, price and provider observations. No tokenizer
limit, editorial winner or production default is inferred. Every new snapshot binds the fresh
compact proof, preserves three families and independent verification, and leaves saved runs
unchanged. Record: `docs/design/chapter-compact-staging-qualification-2026-09-10.md`.

**2026-09-10 — A known summary coverage refusal can recover from complete source evidence.**
The fresh long-source run stopped at its fifteenth summary: the model's ranges did not exactly
cover the window, while all 15 responses and charges were retained. For first-level windows only,
after strict identity, input ownership and prompt checks, discard the entire invalid partition
and substitute the complete source window with an explicit v2 diagnostic and source provenance.
Existing valid/quote-fallback v1 reports remain byte-identical; foreign, unshown, malformed,
oversized and higher-level inputs still refuse. Known planning-only refusals may retry at revision
zero without outstanding/unknown attempts, reusing their paid responses. This is compared against
resubmission, silent endpoint adjustment, starting over and a new summary wire in
`docs/plans/chapter-summary-coverage-recovery-360-view.md`. It does not select a model winner or
waive independent verification and human acceptance.

**2026-09-10 — Chapter render liveness covers preparation, checks and publication.**
A pre-resume review found that only fresh encoding reported heartbeats, while full decode,
cached-source hashing and artifact I/O could exceed the render activity's 30-second heartbeat
deadline. This was found before the long staging run reached rendering. Extend the existing
owned speech/evidence heartbeat supervisor across the whole render operation and its cleanup;
retain the current hard deadlines, revision fences, cache lease, artifact identities and retry
policy. Workspace deletion stays inside the acquired lease, so a cancelled waiter cannot
delete the active attempt's files. Progress-only callbacks and a larger heartbeat timeout
leave quiet phases uncovered; new durable render stages are unnecessary for this correction.
A heartbeat proves activity
liveness, not media progress or quality. Plan: `docs/plans/chapter-render-liveness-360-view.md`.

**2026-09-10 — Chapter creation has a server-owned default brief and an automatic editorial program.**
Rajesh rejected operator-specified cut lists as evidence of harness intelligence. New runs freeze
`chapter-editorial/1` in the existing immutable route-snapshot wrapper; absent means legacy, even
after a new continuation workflow. The primary Create chapters action requires no prompt; custom
instructions remain optional and pending browser intents retain their published default version.
An independent critic assesses the actual compiled edit before rendering. Findings name affected
sections, boundaries and supporting source words; code validates ownership before a scoped repair
uses sentence IDs or existing candidate IDs. Repeated candidates and changes to unaffected cuts
refuse, and unresolved provider outcomes retain exposure. Automatic work creates no human approval.
Source-relative Silero evidence is measured from the verified master using an explicit selected-track
timestamp mapping; old extracted-audio metadata cannot establish that mapping. Missing measurements
remain unknown. Per-cut timing arithmetic belongs to code. Global disagreement against aligned word
intervals includes inter-word gaps and cannot establish missing transcription or risk at every cut;
models receive compact global text and explicit local facts. Historical judgments, including false
claims, remain intact for evaluation. Timing options are checked after frame/sample quantization;
new repairs can select only options actually supplied. Prepared editorial plans freeze their prompt
version, and saved responses finalize using that generation's grounding rules, so a prompt update
cannot invalidate a committed response. Existing cuts and new timing overrides are separate compiler
inputs: retaining an accepted risky cut must not block an independent repair, and must retain that
cut's exact time and review flags. Scoped prior-artifact lineage and a Temporal patch fence this
preservation policy; it cannot authorize a new risky cut. Chapter-Llama is an explicit pinned
ASR-only base-plus-LoRA candidate, with separate durable Modal admission, attempt accounting and evaluation; loading is not
model qualification.
The staging HF account was granted access to the gated Llama 3.1 base during implementation;
real inference and editorial acceptance remain separate measurements.
Plan and comparisons: `docs/plans/chapter-intelligence-360-view.md`.

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
