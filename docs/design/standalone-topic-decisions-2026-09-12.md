# Standalone topic decisions (12 September 2026)

Working decisions from the harness review. Each section is a dated call with
its evidence and limits. This file will accumulate further decisions; it is
not an editorial acceptance result.

Audience: Rajesh and anyone changing topic evidence, routing, or the
standalone-topic program. Keep the existing runtime, ledger, source clock,
rendering and revision foundations unless a section explicitly changes one.

## 1. Shot detector: stick to FFmpeg `scdet`

This section supersedes the 10 September instruction to use PySceneDetect
AdaptiveDetector as the topic trial default. It does not change ingest’s
existing `scdet` producer, Silero speech coverage, or the standalone-topic
editorial program.

### Decision

**New topic runs use FFmpeg `scdet`.** AdaptiveDetector is not the detector to
keep without a quality trial. It remains an explicit, frozen comparison when
someone later has a reason to re-open the choice (handheld or continuously
moving picture, or listening that shows `scdet` inventing edges inside a moving
shot). Do not silently swap detectors on a refetch; a run keeps the detector
saved in its snapshot.

Neither detector establishes that a discussion is complete. A camera switch may
sit between a question and its answer. A topic may change with no visual cut.
Semantic membership and speech-safe compile stay authoritative. A shot may be
preferred only among already-legal, speech-free instants.

### Why `scdet` without a further trial

The 10 September AdaptiveDetector default was a requested trial order, not a
measured ranking. Rajesh asked which detector to keep if that trial is not
going to run. The answer is `scdet`.

On the complete verified Karma master, production decode paths, sequential on
one Mac ([implementation record](standalone-topic-implementation-2026-09-10.md)):

| Signal | AdaptiveDetector | FFmpeg `scdet` |
| --- | --- | --- |
| Selected cuts | 428 | 409 |
| Exact agreement | 403 cuts match | 403 cuts match |
| Wall time | 211.526 s | 15.617 s |
| Scene or editorial gold | none | none |

That is agreement and cost, not a scene-quality ranking. For a typical locked-off
multi-cam podcast, both detectors are looking for hard camera switches. They
already agree on almost every cut on the one real source. AdaptiveDetector’s
documented strength is a rolling local baseline that reduces false hard-cuts
during *within-shot motion* (handheld, pans, moving subjects). Official
default-setting hard-cut F1 varies by footage (Planet Earth 91.59, AutoShot
73.86, ClipShots 55.75) and still does not measure standalone-video acceptance.
That motion-robustness case is not the current editorial target.

`scdet` is already in the execution image, already used at ingest, and is about
thirteen times faster on this source. AdaptiveDetector adds `scenedetect-headless`
and a second decode for no demonstrated standalone-topic gain. The leftover
AdaptiveDetector-only cuts were never labeled; they are not a reason to keep the
slower default.

### What this does not decide

- No model, provider, or editorial-program winner.
- No change to physical compile rules, word membership, or unknown-speech review.
- No claim that `scdet` scores are calibrated chapter-quality probabilities.
- Historical topic snapshots without `topicShotDetector` remain `scdet`.
  Snapshots that already froze `pyscenedetect-adaptive` keep that identity.
- The matched semantic-span detector comparison in
  [standalone-topic-intelligence.md](../plans/standalone-topic-intelligence.md)
  remains available if a later source class needs it. It is not a prerequisite
  for this default.

### Implementation consequence

`HARNESS_TOPIC_SHOT_DETECTOR` for new topic work is `scdet`. AdaptiveDetector
stays installed and selectable as `pyscenedetect-adaptive` for an explicit
comparison. Unavailable detection and measured-empty detection stay distinct.
Code that still defaults new topic runs to AdaptiveDetector should follow this
record when that default is changed; this document alone does not deploy the
flag.

## 2. Qualify `standalone-topics/2` on staging before enabling it

> **Superseded on 13 September 2026.** The qualification manifest is no longer a gate
> and the per-version flags are removed; admission is in-run and the ledger protects
> spend. See the 2026-09-13 AGENTS.md entry and
> [topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).
> The procedure below is kept as the historical record of the v2 admission design.

v2 (`TopicSelectionWorkflow`) is implemented and is the topic-panel default.
It is not live. New v2 intents stay refused until exact four-schema gateway
qualification is bound to the **current** production route snapshot and both
staging services are reloaded with the enable flag. This section is the
staging procedure for that admission gate. The suite contract remains
[topic-selection-qualification.md](../runbooks/topic-selection-qualification.md).
Reload, mounts and in-flight checks remain
[staging.md](../runbooks/staging.md) §4b. Chapter-schema qualification remains
[chapter-harness.md](../runbooks/chapter-harness.md); those receipts do not
qualify v2.

### Decision

**Do not set `HARNESS_TOPIC_SELECTION_ENABLED=1` on staging web or worker until
the steps below complete and the worker has booted against the bound
manifest.** Enabling the flag without a matching
`HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH` is a failed worker boot on the
gateway backend, not a silent fallback to v1. Leave the flag off to keep
existing v1 topic runs reviewable.

Passing this procedure proves only that named snapshot routes can return the
four exact v2 native schemas, under the same prompt, privacy, routing and
output-token settings the worker will send. It does not prove long-source
context capacity, cold comprehension, missed-opportunity recall, or that a
rendered video is publishable. A later full-source v2 run is a separate
editorial experiment.

### What must be qualified

v2 adds four request shapes. A successful chapter or `standalone-topics/1`
receipt does not admit any of them:

| Stage | Seat | Schema |
| --- | --- | --- |
| `topic_author` | `propose` | `topic-selection-draft/2` |
| `topic_cold` | `verify` | `topic-selection-cold/2` |
| `topic_source` | `verify` | `topic-selection-portfolio/2` |
| `topic_patch` | `propose` | `topic-selection-patch/2` |

Every route that can occupy author/repair (`propose`) or cold/source (`verify`)
on the frozen snapshot needs a passed, settled call for each stage of that
seat. The suite still dispatches **four calls per catalogue candidate**.
Include both the intended author families and the reserved non-authoring
reviewer. Three chapter-qualified families are not automatically four
v2-qualified seat combinations.

Use the snapshot already mounted at `/etc/temnia/harness-routes.json` unless a
deliberate new snapshot is being rolled with this work. Binding a manifest to
a different snapshot than the worker will load refuses boot. Changing a route,
prompt, schema, output cap, reasoning setting or provider pin after the suite
invalidates the manifest; run a new create-only session.

### Staging steps

Operator limits (`--max-exposure-micros`, `--max-dispatches`,
`--max-output-tokens`) are declared for this session. Do not inherit the
historical chapter-suite ceilings. `--max-output-tokens` must equal the
worker’s `HARNESS_MAX_OUTPUT_TOKENS` (staging default 8192 unless that cap is
being changed in the same reviewed reload). Budget four calls per candidate
plus any still-unresolved prior gateway exposure. The qualifier lives in the
checkout and is **not** in the worker image; run it from a matching reviewed
tree with the pipeline gateway key in the process environment only.

1. **Check work in flight.** Do not reload the worker under an active chapter,
   topic, ingest, transcription or qualification run. Retain the previous
   Dokploy environment and mounts privately. Keep `AI_GATEWAY_API_KEY` on the
   pipeline only.
2. **Confirm the chapter harness is already live.** Staging needs
   `HARNESS_ENABLED=1`, `HARNESS_BACKEND=gateway`, matching
   `HARNESS_ROUTE_SNAPSHOT_ID` on web and worker, and
   `HARNESS_ROUTE_SNAPSHOT_PATH=/etc/temnia/harness-routes.json` on the worker.
   Confirm paid Pro/Enterprise ZDR entitlement the same way as
   [chapter-harness.md](../runbooks/chapter-harness.md). A credits read is not
   that proof. Leave `HARNESS_TOPIC_SELECTION_ENABLED` at `0` during
   preparation.
3. **Read the live catalogue.** Build a metadata-only `candidates.json`
   (version 1) whose `gatewayModel`, `provider`, `family`, context, max output,
   `zdrClaim: true`, prices, and optional `reasoningEffort` / `serviceTier`
   match the frozen snapshot exactly. Date the catalogue observation. No
   credentials. An old model name or a chapter-suite candidate file is not
   sufficient if any of those fields drifted.
4. **Create a new private session directory.** Journal, receipts, report and
   bind output are create-only. A new directory is a new experiment; it does
   not reset unresolved exposure on an earlier journal.
5. **Dispatch the topic-selection suite** from `apps/pipeline`:

   ```sh
   uv run --frozen python scripts/qualify_harness_gateway.py run \
     --suite topic-selection \
     --candidates /private/tmp/topic-qualification/candidates.json \
     --journal /private/tmp/topic-qualification/journal.json \
     --receipts /private/tmp/topic-qualification/receipts \
     --report /private/tmp/topic-qualification/report.json \
     --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
     --max-dispatches "$REVIEWED_DISPATCHES" \
     --max-output-tokens "$QUALIFIED_OUTPUT_TOKENS"
   ```

   The fixture source is synthetic and small. Do not point this command at
   Karma or any customer transcript. Do not restart the process to evade an
   unknown outcome. If a call is `outcome_unknown`, reconcile that journal
   (lookups only) and keep the reservation:

   ```sh
   uv run --frozen python scripts/qualify_harness_gateway.py reconcile \
     --journal /private/tmp/topic-qualification/journal.json \
     --journal-sha256 EXACT_RETAINED_JOURNAL_SHA256 \
     --report /private/tmp/topic-qualification/reconciliation.json
   ```

6. **Bind without another model call.** The report must be `completed` and
   `passed`, suite `topic-selection`, every required seat/stage settled with a
   generation id and reported cost:

   ```sh
   uv run --frozen python scripts/qualify_harness_gateway.py bind-topics \
     --snapshot /etc/temnia/harness-routes.json \
     --reports /private/tmp/topic-qualification/report.json \
     --max-output-tokens "$QUALIFIED_OUTPUT_TOKENS" \
     --output /private/tmp/topic-qualification/admission.json
   ```

   Copy the admission file and the referenced report/receipt bytes onto the
   staging host at stable paths the worker can read after reload. A bind that
   cannot open those files at their recorded paths will fail validation.
7. **Install the manifest like the route snapshot.** Content-addressed,
   root-owned `0444` file in a root-owned directory. Bind it with Dokploy
   `mounts.create` (`serviceType: "application"`, `type: "bind"`). This Dokploy
   version has no read-only flag; protection is file mode, not a Docker `:ro`
   suffix. Preserve existing mounts, including
   `/etc/temnia/harness-routes.json`. Suggested mount:
   `/etc/temnia/topic-selection-qualification.json`. Confirm the UID-10001
   worker can read it and that a write attempt raises `PermissionError`.
8. **Reload the worker first.** Save environment with every unrelated entry
   preserved. Set on **pipeline only**:
   - `HARNESS_TOPIC_SELECTION_ENABLED=1`
   - `HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH` = the mounted manifest path
   - `HARNESS_TOPIC_SHOT_DETECTOR=scdet` (section 1; do not re-introduce
     AdaptiveDetector as a side effect of this enablement)
   Then `application.reload`. Verify a new running task, same image, container
   environment, successful boot, Temporal connection, and that boot loaded
   both the route snapshot and the topic manifest. A missing path, snapshot
   mismatch, stale schema hash, unresolved cost or seat without a passed
   receipt is a refused boot — keep the previous task.
9. **Reload web second.** Set only the nonsecret
   `HARNESS_TOPIC_SELECTION_ENABLED=1`. Do not put the gateway key, snapshot
   path or qualification path on web. Reload, then check `/api/health` and
   that **Find topic videos** with the default program
   (`standalone-topics/2`) no longer returns the qualification-refusal
   message. Existing v1 runs stay visible.
10. **Record identities, not a quality win.** Keep snapshot id, manifest hash,
    report hashes, generation ids, settled micros, output-token setting,
    detector value and the two reload task identities. Do not start a
    full-source v2 run as part of this enablement unless that experiment is
    separately authorized. Schema success on the synthetic fixture is not
    standalone-video acceptance.

### What this does not decide

- No author, reviewer, provider or programme winner.
- No change to v1 replay, human review of existing topic runs, or the
  independent-span compile/render path.
- No proof that Kimi/Alibaba (or any other pairing) can finish a 579-sentence
  source. The September 11 Vercel timeouts were full-source transport
  failures; this suite will not reproduce them.
- No obligation to raise `HARNESS_MAX_OUTPUT_TOKENS` or `HARNESS_MAX_REPAIRS`.
  If a later full-source v2 run needs different caps, requalify at those
  exact settings.
- AdaptiveDetector remains an explicit comparison only, per section 1.

### Implementation consequence

Until these steps are done, staging behaves as today: the topic panel may
offer v2, and `startTopicRun` refuses it with the deployment-qualification
message. After they are done, a new default-button run starts
`TopicSelectionWorkflow` and consumes the reserved reviewer for v2 cold,
source and patch seats. Treat the first such customer-source run as measured
work, not as a continuation of this admission procedure.

## 3. Stage review of the topic program (evidence → author → review → repair → compile)

Read-only review of the actual code on 12 September, first against the
`feat/topic-selection-quality` tree (v1/v2) and then re-checked against `main`
after PR #39 merged `standalone-topics/3`
([production quality v3](standalone-topic-production-quality-v3-2026-09-12.md)).
Each finding names the code it was read from. **Status** is the state on `main`
at commit `88be5fb`: *Open*, *Partly addressed*, or *Closed*.

### Decision

**The remaining blockers are editorial operations and calibration, not
orchestration or transport.** PR #39 fixed the control failures the Karma runs
exposed (author packaging its own inventory, rationale leaking into the source
critic, a repair vocabulary that could not trim, one repair round, no
publication gate). The items below stay on the list until a measurement closes
them. None of them implies a runtime, ledger or media change.

### 3a. Evidence (`activities.py` `_build_chapter_evidence_locked`, `evidence.py`, `topic_feasible.py`)

| # | Finding | Status | Note |
| --- | --- | --- | --- |
| E1 | Evidence carries no topical structure: sentences plus legal cut instants only. KernelCPD change-points exist in `evals` and are not run in production; `is_question` is computed by SaT and dropped before the prompt; speakers are raw diarization labels with no host/guest role. | Partly addressed | v3 adds a model-side inventory call (reviewer family). No code-side source map exists. |
| E2 | `pauses` are gaps between aligned words, not measured silence (Karma: 6,202 of them). Kind `pause` carries score 1.0 and the best compiler preference (−100), so an arbitrary inter-word gap outranks a real turn or shot. No `silencedetect`/RMS/prosody is measured. | Open | `compiler.py` `_candidate_cost` unchanged. |
| E3 | Silero speech is a hard veto at every candidate (`_boundary_constraints`, `_layer_choices`). Back-channels and VAD padding make many sentence transitions cut-free, and the only permitted response is a semantic extent change. | Open | Both v3 Karma runs still produced "physical edge" findings that were repaired by moving sentences. |
| E4 | Admission counts **1 byte = 1 token** (`routes.py` `estimate_cost`, `input_tokens = payload_bytes + 8192`). Karma's ~90 KB is admitted as ~100k tokens against 25k real. A two-hour source (~240 KB) is refused on every 128k/200k route and marginal at 262k. The topic lane has **no hierarchy** fallback; the chapter lane has eight levels. | Open | Unchanged on `main` (`routes.py:270`). v3 adds a third full-source call (inventory), so the ceiling now binds three times per run. |
| E5 | v2/v3 feasible grid (`topic-feasible-grid/2`) is the only genuinely new physical candidate source; it is correct and reproducible. | Closed | Keep. |

### 3b. Author (`topic_editorial.py`, `topic_selection.py`, `topic_compiler.py` `_validate_candidate`)

| # | Finding | Status | Note |
| --- | --- | --- | --- |
| A1 | One call did inventory, selection, extents and span proof. | Partly addressed | v3 splits inventory (reviewer family, `opportunity_inventory_prompt`) from packaging; the author still fixes extents, spans and dispositions in one full-source call. |
| A2 | A cited span outside `[first,last]` is a **refusal** ("required evidence lies outside the video") while the brief says to expand. DeepSeek's out-of-extent completion spans were the correct editorial signal and killed the run. | Open | `validate_topic_proposal` unchanged. Recommended: treat the union of cited spans as the extent, deterministically. |
| A3 | `startMs`/`endMs` per sentence are in a call whose output has no times (`sentence_rows`). Cost and length bias for no use. | Open | |
| A4 | Output allowance: the only complete Karma proposals used 19k–32k output tokens, mostly hidden reasoning. Default `HARNESS_MAX_OUTPUT_TOKENS` is still 8192; the v3 runs used 32,768 and the source reviewer exhausted it twice on ~31,450 reasoning tokens. | Partly addressed | `topic-selection-source/4` removed duplicated candidate-local review to shrink output; no per-stage reasoning-effort control exists. |
| A5 | v1 `reason` is mandatory, excluded from the semantic key, yet must be byte-identical on protected candidates in repair (`validate_preserved_candidates`). | Open (v1 only) | v2/v3 semantic keys exclude prose; v1 remains a comparison program. |
| A6 | Overlap between videos is permitted, never elicited. | Partly addressed | Inventory prompt asks to "record overlapping alternatives"; the compiler still cannot request overlap to resolve a shared hard edge. |
| A7 | Author `purpose`/`reason` were sent to the source critic. | Closed | v3 `independent_projection` strips `reason` and `dispositionReason`. |

### 3c. Reviews (`topic_editorial.py` `cold_prompt`/`source_prompt`/`ground_review`, `topic_selection.py` `assess_selection`, `topic_activities.py` `save_assessment`)

| # | Finding | Status | Note |
| --- | --- | --- | --- |
| R1 | Seven criteria all `pass` or nothing is `passed`; text judges are told to answer `unknown` on audio-dependent questions, so the bar is structurally unmeetable. | Partly addressed | v3 gate is "source review says `select` and no `required` finding"; unknowns touching no candidate no longer block. Unknowns that touch a candidate still do. |
| R2 | A grounding error (one citation outside the clip) deletes the whole review; the candidate becomes `needs_review` with no judgment. | Open | `assess_selection`: "Cold review … is unavailable". Recommended: demote the criterion to `unknown`, keep the rest. |
| R3 | Cold judge gets no pre-roll, no speaker roles, no times; it cannot test "does the first referent resolve" the way a viewer does. | Open | |
| R4 | Source reviewer is overloaded: full source + per-opportunity status + missing opportunities + selection + findings in one call. | Partly addressed | `/4` trimmed the candidate-local criteria; the first v3 run still exhausted 32k twice on this call. |
| R5 | No critic calibration set; false-pass rates by defect type are unknown. The `unfocused_extent → required` promotion in v3 was derived from one Karma case. | Open | Build the set from Karma's known defects (claim/correction split, "For that" opening, omitted examples, duplicate core, shared 7/8 edge, wealth tail s349–s356). |
| R6 | Nobody hears anything before or after render; technical checks are container checks. | Open | |
| R7 | Verifier is a reserved family slot, not an auditioned judge. | Open | |

### 3d. Repair (`topic_workflow.py`, `topic_selection_workflow.py`, `topic_selection.py` `_patch_authority`/`apply_selection_patch`)

| # | Finding | Status | Note |
| --- | --- | --- | --- |
| P1 | `HARNESS_MAX_REPAIRS=1` for the whole run, shared between admission corrections and editorial repair. | Partly addressed | Default is now 3; still one counter (`run.repair_count`) for both loops. |
| P2 | v1 repair regenerates the whole proposal and proves byte identity on the rest. | Open (v1 only) | v2/v3 use atomic patches. |
| P3 | The common fix ("trim this tail", "move both edges") was not expressible. | Closed | v3 `replace_extent`; one operation may cite several findings. |
| P4 | Repair saw the full source and full opportunity records while most fields were immutable; Kimi's patch rewrote protected evidence and was refused. | Closed | v3 `selection_patch_prompt_v3`: immutable definitions, editable mappings, and only finding-authorized spans ±8 sentences. |
| P5 | No local Cutter step reads ±N sentences around each edge during construction. | Open | The ±8 projection exists only in repair. |
| P6 | Repaired candidates were not re-reviewed. | Closed | Loop re-runs cold (cache keyed on id/title/first/last/rubric) and a fresh source review after every accepted patch. |
| P7 | An invalid or truncated repair could leave a run with no output or a stale one. | Closed | v3 retains the last assessed selection and the gate renders only selected, finding-free candidates. |

### 3e. Compile (`topic_compiler.py`, `compiler.py`)

| # | Finding | Status | Note |
| --- | --- | --- | --- |
| C1 | Exactness, reproducibility, provenance and refusal transparency are good. | Closed | Keep. |
| C2 | Cost optimises displacement from the word-gap midpoint (weight 100/ms); no lead-in/lead-out preference (start after previous breath, end after terminal fall plus room tone). | Open | |
| C3 | Physical refusal is binary. A word-free instant inside Silero padding cannot be cut with a short fade under review; the whole video is refused and a semantic move is forced. | Open | Recommended tiers: clean cut / fade-under-review / refuse only when a word would be cut. |
| C4 | Word-gap `pause` outranks `turn` and `shot`. | Open | Same root as E2. |

### What this does not decide

- No model, provider or program winner. The v3 Karma run (nine admitted videos,
  $0.652119) is the first complete automatic output and has **no** human playback
  judgment.
- No change to the four-schema/five-schema qualification contracts.
- No new duration, count or spend caps.

## 4. Prompt review

All twelve model-facing prompts were read: topic v1 (`EDITORIAL_BRIEF`,
`cold_prompt`, `source_prompt`), v2/v3 (`selection_prompt`,
`opportunity_inventory_prompt`, `selection_cold_prompt`,
`selection_source_prompt`, `selection_patch_prompt`, `selection_patch_prompt_v3`),
chapter (`prompts/chapter.py`, `EDITORIAL_RUBRIC`, repair prompt) and the two
default briefs in `apps/web/lib/harness`.

### Findings

| # | Finding | Status on `main` |
| --- | --- | --- |
| S1 | **There are no system prompts.** Every `Agent` in `models.py` `_agent` is built without `instructions`; instruction text and the untrusted transcript travel in one user turn, instruction first, data last, with no task restatement after the data. | Open |
| S2 | Contract fields carry **no `.describe()`** (only `hello.ts`, `ingest.ts`, `scope.ts`, `transcript.ts` have any). `coreSpans`, `completionSpans`, `disposition`, `severity` are defined only by prompt prose. | Open |
| S3 | Rule lists, not procedures: 22–40 equal-weight imperatives per prompt, no order of operations. | Open; v3 prompts add paragraphs in the same style. |
| S4 | Negative-heavy (11 "do not/never" to 4 definitions in `EDITORIAL_BRIEF`). | Open |
| S5 | Prompt-as-changelog: `EDITORIAL_RUBRIC` and the chapter repair prompt encode live incidents as sentences instead of schema constraints or code checks. `/4` adds "Complete the JSON instead of narrating" after the 32k exhaustion. | Open |
| S6 | Internal jargon leaks (`physicalBoundaryIssues`, "source-admission diagnostics", "compound merge/split", `cutFacts`). | Open |
| S7 | Criteria have names but no operational tests (unresolved referent in sentence 1, reply to an unheard question, next source sentence begins "But…"). | Open |
| S8 | No examples anywhere. | Open |
| S9 | Redundant restatements of "no duration/count" across author, cold and source prompts. | Open |
| S10 | Line-wrap artifacts from ruff inside triple-quoted strings ("these\npreferences", "EVEN IF THE PROPOSAL\nIS EMPTY", "Preserve every other candidate\nexactly"). | Open (still present in `selection_cold_prompt`, `selection_source_prompt`, v1 repair block) |
| S11 | `unknown` is over-encouraged for text judges. | Partly addressed (see R1) |
| S12 | `NativeOutput(strict=True)` leaves non-reasoning models no scratch space; reasoning models spend it hidden (31k of 32k on the v3 source review). | Open |
| S13 | Source critic received author rationale. | Closed (v3 projection) |

### Decision

Prompt work is the cheapest open item and needs no contract change: stable role,
shared definitions and the data-is-not-instructions rule move into
`instructions=`; every contract field gets a `.describe()`; each prompt is
rewritten as purpose → numbered procedure → per-criterion tests → one example →
what to do when unsure, at roughly a third of current length; every rule code can
check leaves the prompt. Bump each prompt version constant and requalify. A prompt
rewrite is not editorial acceptance; it is measured on the calibration set (R5).

## 5. Agentic frameworks: no role

Rajesh asked whether LangChain, LangGraph or Google ADK should supply checkpoints,
resumes or multi-agent loops. Checked on 12 September: both LangGraph and ADK now
ship official Temporal plugins (`temporalio.contrib.langgraph`, Public Preview;
`temporalio.contrib.google_adk_agents`) in which the framework's own persistence
is switched off (`InMemorySaver`, in-memory session) and Temporal history supplies
durability. On Temporal they contribute notation only. Temnia already has durable
execution (Temporal), checkpointed *money* (receipts, reservations, immutable
artifacts), resume paths and explicit typed loops. Neither plugin knows the
dispatch guards inside the PydanticAI model activity (reservation, receipt,
route snapshot, reserved verifier family); adopting one means a second model-call
abstraction and re-implementing those guards.

**Decision: no framework.** The 2026-09-07 "typed durable program, not a cast of
agents" call stands; its original reason (a second durable runtime beside
Temporal) no longer applies, its conclusion does. Revisit only on measured
evidence that an open-ended tool-using editor beats the typed stages at equal
cost. Rajesh accepted ("leave it").

## 6. Re-validation after PR #39 (`main` at `88be5fb`)

### What #39 made more robust

- **Inventory before packaging**, by the reviewer family, with code enforcing
  that the author retains every inventory ID and evidence field
  (`validate_selection_against_inventory`). Omissions and selection decisions are
  now distinguishable.
- **Independent source critic**: `reason`/`dispositionReason` removed from its
  input; candidate-local criteria removed from its output (`/4`).
- **Repair that can actually repair**: `replace_extent`, multi-finding ops,
  immutable-vs-editable opportunity projection, ±8-sentence authorized source
  rows only. This directly fixes Kimi's refused patch and shrinks the repair
  prompt and its injection surface.
- **Three repair rounds**, fresh cold and source review after each accepted
  patch, stop on repeated semantic key or invalid patch with the prior
  assessment retained.
- **Publication gate**: render only candidates the complete source review marked
  `select` with no `required` finding; a truncated source review now yields
  nothing rather than a publishable-looking batch.
- **Per-generation flags and qualification** (`standalone-topics/3`,
  `topic-selection-qualification/5`), OpenRouter transport with early
  generation-ID capture, unknown outcomes fenced (the second v3 run:
  `outcome_unknown`, $0.586809 settled, $0.137916 exposure retained).
- **Measured**: first complete automatic run, ten renders, nine admitted after the
  `unfocused_extent` rule, $0.652119, all media decode-verified.

### What #39 did not change (still open, by section 3/4 number)

E1–E4, A2–A4, A6, R2–R7, P1 (shared counter), P5, C2–C4, S1–S12.

### New concerns introduced or exposed

1. **Detector inconsistency.** §1 of this document says new topic runs use
   `scdet`; `settings.py` on `main` still defaults `pyscenedetect-adaptive` and
   the v3 record says v3 "continues to use the requested AdaptiveDetector
   trial". Either §1 is applied in a deploy (`HARNESS_TOPIC_SHOT_DETECTOR=scdet`
   and the code default) or §1 is withdrawn. Do not leave both. Unchanged after
   PR #40 (`settings.py:89`).
2. **Reasoning eats the output allowance.** The source reviewer twice used
   ~31,450 of 32,768 tokens on reasoning. `effective_topic_output_tokens` is
   `min(requested, route max)`; there is no per-stage reasoning-effort setting
   and no separation of reasoning from answer budget. A two-hour source will hit
   this before it hits E4.
3. **Three full-source calls per iteration** (inventory, author, source review)
   plus per-candidate cold reviews. Cost scaled to $0.65 on 44 minutes; with E4
   unfixed a two-hour source is refused, and with E4 fixed it costs roughly three
   times that per iteration. Acceptable, but record it before the first long run.
4. **The gate trades yield for safety.** With `require_complete_review`, any
   schema-invalid or exhausted source review renders zero videos. That is the
   right default until R5 exists, and it means the run's success now depends on
   one large call finishing (concern 2).
5. **n = 1 rules.** `unfocused_extent` promotion came from one wealth tail.
   Record such rules as hypotheses until the calibration set (R5) confirms them.
6. **Inventory reuses `TopicSelectionDraft` with zero candidates** and a mandated
   summary text. It works, but the schema tells the model nothing about the
   inventory task (S2); a dedicated inventory schema with descriptions would be
   cheaper and clearer.
7. **Human playback judgment is still zero.** Nine technically valid videos and
   contact sheets are not acceptance. The v3 record says so; this section
   repeats it so the next session does not read "nine admitted" as a result.

### Decision

Treat `main` as the baseline for the next measured step. Order of work that
follows from this review: (1) R5 calibration set from Karma's known defects;
(2) prompt/system-instruction/`.describe()` rewrite (§4) measured on (1);
(3) A2 span-union and P5 local edge inspection during construction; (4) E4
tokenizer-based admission plus a topic hierarchy so long sources are admitted at
all; (5) E2/C2/C3 acoustic edge evidence and fade tier; (6) R6 post-render edge
listening judge. Resolve the detector inconsistency (concern 1) in whichever PR
touches settings first.

## 7. First playback review of the r11 videos (13 September)

Rajesh watched the nine admitted videos from run
`0c3a9707-30b9-558f-b0e3-1098bd1c04e2` (PR #39 code, Karma 43:56). This is the
first human playback judgment of any automatic topic output. It is one reviewer
on one source, not a labeled set; it supersedes §6 concern 7 ("human playback
judgment is still zero") and does not close R5.

### Result

**Physical cuts pass.** Every video plays as a complete video: no sentence is
broken at either edge. This confirms C1 (the compiler's exactness and speech
safety) and shows that on this locked-off two-person source the Silero veto (E3)
cost nothing visible; E3 remains open for sources with cross-talk.

Two defects remain, one physical, one editorial.

### 7a. The trailing pause is split between two videos

**Observed.** When a video ends on Sakala Maa's last sentence and her pause, the
pause is not held to its end inside that video; a few hundred milliseconds of it
land at the start of the next video, which therefore opens on dead air.

**Root cause (code, not model).** `compile_chapters` sets
`desired_ms = (previous_sentence.endMs + next_sentence.startMs) // 2` for every
transition and `_candidate_cost` charges 100 per millisecond of displacement from
that midpoint. The v2/v3 feasible grid (`topic_feasible.py` `_derive`) offers
exactly three instants per speech-free region: first, middle, last. The middle
instant has zero displacement and wins at both the ending of video N and the
opening of video N+1, so each gets half the pause. The objective knows a
transition; it does not know which side is an ending and which an opening.

**Fix.** Make the topic objective edge-role-aware. For an **ending**, the target
is the latest safe instant that still leaves a guard before the next speech:
`min(gap_end − guard, last_selected_word_end + lead_out)`. For an **opening**,
the target is a short lead-in before the first selected word:
`max(gap_start + guard, first_selected_word_start − lead_in)`. Because videos
may overlap (§3 A6, the product contract), the same pause can belong wholly to
video N's ending and partly to N+1's lead-in; no partition is violated. The grid
must offer those targets as real candidates (`topic-feasible-grid/3`: add the
target instants, quantized, beside first/mid/last), otherwise the DP cannot
choose them. Starting values to tune by listening, not to freeze: `lead_out`
500–800 ms, `lead_in` 200–300 ms, `guard` ≥ 120 ms and never inside Silero
speech. Silence measured from audio (E2) would let `lead_out` end on room tone
rather than on a word-gap estimate. This supersedes the generic wording of C2
with a concrete defect and a concrete objective.

**Status after PR #40 (merged 13 September 00:35): closed for the ending, open
as a preference for the opening.** `topic-compiler/3`
(`_pause_ownership_constraints`) gives every inter-utterance pause to the speech
that precedes it by selecting the *latest* admissible grid instant at both the
ending and the opening. The ending now holds the whole pause. The opening starts
at the last safe instant before the first selected word, so the residue is under
one 25 fps frame and there is no lead-in at all. Whether a 200–300 ms lead-in
sounds better than an immediate onset is a listening preference, not a defect,
and stays open under C2. The `lead_out`/`guard` values above are superseded by
"latest safe instant"; no acoustic silence is measured yet (E2).

### 7b. Two videos share core content

**Observed.** Video 5 is about the mantra and karmic collision; video 4 already
covers the mantra discussion toward its end. A viewer watching both hears the
same core twice.

**Root cause.** Three things allow it. (1) Every prompt permits "shared setup or
completion context" and forbids "repeated core value", but nothing states which
sentences are which; the author never declares an overlap and its purpose. (2)
`distinctPurpose`/`duplicate_core` is judged by the source critic reading
prose; the arithmetic (candidate 4's extent ∩ candidate 5's core spans) is
never computed and handed to it. The first r11 review did catch one containment
(the 10:45 treatment containing the sadhana treatment), so the critic can see
this class; it missed the 4/5 case, so a model-only check is not reliable. (3)
The inventory maps opportunities to candidates but does not require that one
opportunity's core appear as core in at most one video.

**Fix.** Make overlap a computed fact. Code derives, for every candidate pair,
the shared sentence range and whether it intersects either side's `coreSpans`.
Core-in-core intersection, or one candidate's extent containing another's core,
becomes a deterministic `duplicate_core` finding (required) before any model
review; setup/completion reuse remains allowed and is shown to the critic as
"shared s0401–s0412: completion for 4, setup for 5". The author declares each
overlap as `setup` or `completion` reuse per shared range; an undeclared core
overlap is an admission diagnostic. Add the 4/5 mantra pair to the calibration
set (R5) beside the wealth tail.

**Status after PR #40: partly addressed; the remaining gap is inside one
candidate.** Code now computes every exact candidate overlap
(`candidate_overlap_rows`) and every adjacent non-overlapping handoff with fixed
context windows (`candidate_handoff_rows`) and hands them to the source
reviewer, which must return one typed classification per row
(`necessary_shared_context`, `misallocated_topic_extent`, `duplicate_core`,
`clean_handoff`, `unresolved`); omission is invalid, `necessary_shared_context`
is admissible only when the overlap lies in both candidates'
`requiredContextSpans`, and misallocation requires exact replacement edges plus
a required two-candidate finding. `replace_candidate` lets one operation fix
extent and title together. This is `topic-selection-portfolio/4`, source prompt
`/8`, patch prompt `/9`. The classification is still the model's, not
arithmetic: the deterministic core∩core finding recommended above was not
adopted. Runs r20 and r25 then showed the loophole this cannot see: when the
author merges prayer, sadhana and mantra into **one** compound candidate
(s202–s312, s223–s308), there is no overlap or handoff to classify and the
reviewer accepted the span as one distinct topic. Internal topic structure
inside a single candidate is the measured open gap (recorded in `main`'s
AGENTS entry). Do not answer it with Karma-specific rules.

### What this changes in §3 and §6

- C2 → concrete (7a). C1 confirmed by playback on one source.
- New portfolio finding **A8**: core overlap between videos is not computed or
  declared. Open.
- §6 concern 7 is superseded: one informal playback review exists. The labeled
  set and the listening judge (R5, R6) remain open.

### What this does not decide

- No lead-in/lead-out values; they are tuned by listening on more than one
  source.
- No claim that the remaining eight videos are publishable beyond Rajesh's
  informal judgment.
- No change to the compile contract for chapter partitions (exact cover), where a
  shared cut is by design.

## 8. Change-point evidence (KernelCPD) is built, not run; run it as hints

Rajesh asked what "KernelCPD embeddings" are and why production does not use
them. This section records the answer and turns §3 finding E1 into a concrete
step.

### What it is

`substrate/changepoint.py` (2026-09-07). Every SaT sentence is embedded with
`sentence-transformers/all-MiniLM-L6-v2` (baked into the image) and normalised;
the ordered vectors are a signal; `ruptures.KernelCPD` with an RBF kernel (cost
is a function of cosine distance) solves the exact dynamic program
`predict(n_bkps=k)` for the best split into k+1 pieces. This is the unsupervised
Embed-KCPD line of topic segmentation. `k` comes from `target_per_hour`
(default 6); the penalty curve `candidates_at(penalty)` is also exposed. Each
boundary is a scored `BoundaryCandidate`. Quadratic in sentences: 2,500
sentences is a 50 MB Gram matrix and seconds of CPU; refuses above 10,000.

### Why production does not run it

- **The contract forbids it.** `BuildEvidenceRequest.segmenter` is
  `Literal["sat"]` (`harness/runtime_types.py:137`). `make_segmenter("sat")`
  yields SaT sentences and legacy paragraph-rule candidates. The change-point
  segmenter is reachable only from `temnia-eval segment`
  (`changepoint:target_per_hour=…`). `build_evidence` already maps a `"topic"`
  layer candidate to a `sentence` boundary with reason `topic_score:<value>`;
  the path exists and is never fed.
- **The harness design moved past it.** The substrate decision expected S4 to
  read a ranked candidate list. The 09-07/09-08 harness decision made the model
  propose semantic spans over the full sentence list with code choosing the
  physical cut. Nobody wired the ranked list into a prompt, and the compiler
  needs speech-free instants, which KCPD does not provide.
- **Never measured against gold.** The annotated corpus named as the S4 entry
  gate does not exist. Shipping an unmeasured signal into evidence would repeat
  the pattern the repo rules forbid.
- **Its objective is the partition lane's.** `target_per_hour` asks "split this
  hour into about six pieces"; standalone extraction asks "which discussions are
  complete and worth publishing", and the 09-10 decision forbids implied counts.
  A change-point says the conversation moved, not that what preceded it is a
  finished discussion.

### Decision

**Run it as scored hints, not as the answer.** Add change-points (from the
penalty curve, not a fixed `target_per_hour`, so no count is implied) to the
code-side source map recommended under E1, beside speaker roles, question
sentences and turn boundaries, and expose them through the existing
`optionalNavigationHypotheses` slot to the inventory and author calls and as
citeable facts to the critics. Widen the `segmenter` literal or add a separate
evidence field; do not let hints enter the boundary-candidate list the compiler
selects from. Measure on the calibration set (R5) before trusting any hint.
Cost is a few CPU seconds inside the existing evidence activity for a two-hour
source.

### What this does not decide

- No claim that KCPD boundaries are correct on any source; density and
  agreement are the only numbers that exist.
- No change to SaT as the sentence layer or to the legacy rule as the scored
  baseline.

## 9. Author and reviewer seats are not finalized

Rajesh noted that the production author and reviewer models could not have been
finalized yet. Correct, by rule (2026-09-05: a seat is won by audition on
cost-per-correct with reasoning tokens included, the verifier is a different
family, nothing is a default) and by the evidence.

### Where it stands

- Every evaluation ends "no winner": the 11 September native audition, the
  OpenRouter audition (Astra/Gemini transport failure; Kimi/Gemini finished with a
  rejected repair; Gemini/Kimi truncated), and the v3 record.
- Exactly one configuration has produced a complete, gated, rendered output:
  **Kimi K3 author/patcher with Gemini 3.8 Flash inventory/reviewer through
  OpenRouter**, on one 44-minute source, once (`0c3a9707…`); its repeat
  (`7ae3b259…`) died on an upstream rate limit inside an HTTP 200 stream. That
  is an existence proof for the program, not a ranking.
- The staging snapshot pool is still the chapter-qualified trio
  (DeepSeek/DeepInfra, Kimi/Alibaba, GLM-5.3-Flash/Baseten); none has a v3
  five-schema receipt.

### Why it cannot be finalized yet

1. No labeled set. R5 and whole-source human labels do not exist; §7 is one
   reviewer on one source.
2. "Correct" is undefined per seat: author = recall of worthwhile discussions
   and extent quality; cold judge = false-pass rate on known defects; source
   critic = catching containment and missing opportunities. No rate has been
   measured for any model.
3. The program moved under the models (v1 → v2 → v3 in three days). Kimi failed
   v2's repair and passed v3's; auditioning on a moving contract measures the
   contract.
4. Transport confounds: the same family behaves differently by provider
   (Qwen/Alibaba array wrapping, Gemini/Vertex integer enums, Azure
   `max_completion_tokens`); reasoning-token consumption is a route property as
   much as a model property (§6 concern 2).
5. ZDR and per-request privacy are checked per route, not per family.

### What finalizes a seat

Freeze the program (v3 or its successor after §7), build R5, then for each seat
run every candidate family on at least three fresh full sources with the other
seat held fixed, score against the labels, divide by settled cost with reasoning
included, and record the comparison in the PR that writes the snapshot. Until
then every document and post says: the program works; the roster is provisional.

### Interim rule

Kimi/Gemini will drift into a de facto default by repetition because it is the
only pair with a finished run. The snapshot guards configuration; nothing guards
habit. While Karma reruns tune §7, alternate at least the reviewer family across
runs so the audition does not start from zero on the others. Record the pair
used in each run's evidence, as the experiment manifest already requires.

### After PR #40 (runs r15–r25, recorded in `main`'s AGENTS entry)

The alternation happened. Results, none of which is a winner:

| Run | Author / reviewer | Outcome |
| --- | --- | --- |
| r20 | Kimi K3 / Gemini 3.8 Flash | $0.907369, 19 calls, seven renders; author merged prayer and mantra into one s202–s312 candidate and the reviewer accepted it. Failed the ownership criterion. |
| r22 | Astra / Gemini | Author stream exceeded the frozen 540 s deadline with no handle; `outcome_unknown`, $5.271530 reservation retained. Astra's second full-Karma transport failure after passing short qualification: **unavailable for this workload**. |
| r23 | Gemini / Kimi | Gemini returned HTTP 200 with an upstream rate-limit error; `outcome_unknown`, $0.213370 retained. |
| r24 | DeepSeek V4 Pro 0813 (Fireworks) / Kimi | 20-candidate proposal separated prayer but divided mantra across three candidates; Kimi's full-source review crossed the deadline; `outcome_unknown`, $1.015747 retained. |
| r25 | DeepSeek / Gemini | $0.430738, 13 calls, nine renders; author merged prayer, sadhana, mantra and karmic effects into one s223–s308 candidate; Gemini called it complete and distinct. `needs_review`. |

Two consequences for this section. First, the transport ceiling is now a
selection criterion in its own right: a route that cannot finish a 44-minute
full-source call inside 540 s (Astra twice, Kimi review once) cannot be
auditioned on longer sources at all, and E4/§6 concern 2 bind before any quality
question. Second, the recurring failure is the same across authors and both
reviewers (compound spans accepted as one topic), which is evidence about the
program's blind spot, not about a model. Do not start more arms until the
internal-structure gap in §7b has a program answer.
