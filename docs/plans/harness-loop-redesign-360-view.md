# Harness loop redesign: judge edges against the source, repair in parallel, author with tools

2026-09-14. Rajesh, after watching video 1 of run `f7396b5e` open thirty seconds late: "can we
design a better looping agent?" This is the plan. It builds on the runtime we have (Temporal,
PydanticAI, the ledger, the validators); it adopts no agent framework, per the 2026-09-12
decision, for the reasons restated in §8. The architecture review that motivates it is
[pipeline-architecture-review-2026-09-14.md](../design/pipeline-architecture-review-2026-09-14.md).

**Direction update, 2026-09-14, after review of PR #48.** Rajesh identified indexed source
evidence and agent tools as the foundation needed for two- and four-hour recordings. The
author-only W4 scope, its placement after W3, and postponing source-review conversion are
superseded by [Indexed evidence for the editorial harness](../design/indexed-editorial-evidence-2026-09-14.md).
That design covers discovery, authoring and source review together, with bounded working
context, explicit coverage and exact source access. The workstreams below remain the original
PR #48 proposal for reference; their day estimates, fixed round-cap suggestion and cost/quality
predictions are not an approved implementation schedule or measured results. Resolve the
review's accounting, repair-conflict and calibration-identity findings in the detailed design.

## 1. Problem

The loop is technically robust as of #43 and #45 and editorially blind in three places.

1. Nobody with the whole transcript judges where a clip opens or closes. Video 1 dropped the
   host's premise (2:41 to 3:07) that the guest's answer corrects, and every seat passed it.
2. A repair is one atomic patch from one model; one refused operation discards all of them.
   Bounded correction (#45) retries, it does not decompose.
3. The author reads the full transcript in one prompt and writes once. It cannot look anything
   up, and the prompt's size is why long sources are refused and why one route took ten minutes.

Two enabling gaps sit under them: human accept and reject reasons are stored and never used, so
no change is measured; and prompts and validators describe operations independently, which
produced both refusals this week.

## 2. Goals and non-goals

Goals: a clip's edges are judged against the source by a seat that can see it; a repair with one
bad operation still lands its good ones; the author can investigate before it writes; every
prompt change is scored against human decisions; one description of each operation feeds both
the prompt and the validator.

Non-goals: a new orchestration runtime; new model vendors (the roster question is an experiment,
§7); changing the compiler, the render path or the panel's review actions; solving the
compound-candidate problem recorded on 2026-09-12 (it gets easier after W4, it is not in scope).

## 3. Workstreams

Each workstream is one PR with its own gate, its own decision entry and one staging run on Karma
compared with run `f7396b5e` as the baseline. Order: W0 (small), W1, W2, W3, W4, W5.

### W0. One description per operation and finding (one day)

**Design.** `.describe()` on every operation kind and finding kind in
`packages/contracts/src/topic-selection.ts`; the generated `contracts.py` carries them as field
docs. `selection_patch_prompt_v3` renders its operation section from those descriptions instead
of prose. A unit test enumerates every `_refuse(...)` message in `topic_selection.py` and asserts
the prompt states the rule it enforces (a table of message to required phrase, so a new refusal
without a prompt rule fails the test).

**Also in W0** (from the prompt review): instructions and data split with one shared contract,
the `userInstructions` field-name fix, wrapped-line cleanup, `endMs` dropped from author and
source payloads, local ids for the cold clip.

**Edges.** Descriptions are data, so the prompt version bumps when they change; the test pins
the mapping, not wording. **Tests.** The enumeration test; the existing prompt-content tests move
to the descriptions. **Legacy.** Removes the hand-written operation paragraph.

### W1. Edges judged against the source (one day, one staging run)

**Design.** The source review contract (`topic-selection-portfolio/4` to `/5`) gains one entry
per candidate:

```
edges: {
  opening: { status: pass | missing_premise | connective_opening | excess_runway,
             evidenceSpans, recommendedFirstSentenceId | null, reason },
  closing: { status: pass | unfinished | excess_tail,
             evidenceSpans, recommendedLastSentenceId | null, reason }
}
```

The prompt asks, per candidate: read the speech before the opening (bounded: the evidence window
already supplied, typically sixty seconds); does the selected answer respond to a question,
claim, premise or misconception stated there? If so the clip must include it, and the reviewer
names the first sentence of that premise. The same for the closing: does the speaker later
qualify or overturn what the clip ends on?

Code maps statuses to the existing finding kinds and authority: `missing_premise` to a required
`missing_setup`, `unfinished` to `unfinished_discussion`, `excess_runway` and `excess_tail` to
`unfocused_extent`, `connective_opening` to `missing_setup` unless the following sentence is the
first self-contained statement (the existing rule, now applied at an absolute opening too). A
recommendation is admissible only inside the candidate's authorized window and never inside
another candidate's core; otherwise it degrades to a finding without a recommendation.

The inventory prompt gains the same clause: required context includes the asker's stated premise
when the answer addresses it. The prompt review adds to W1: `dependencies[]` and
`conversationalForms` on candidates, `acceptanceCondition` and `omittedSpans` on findings, a
`changed_attribution` finding kind, title support in the source review, the pack's probes as
criterion descriptions, and two or three held-out examples per role
([prompt-review-2026-09-14.md](../design/prompt-review-2026-09-14.md) §5). The cold rubric gains: an opening on a dependent connective fails
intelligibleBeginning unless the sentence is self-contained.

**Edges.** A premise that belongs to the previous candidate's core is a handoff, not a missing
premise; the handoff judgment already exists and wins. A premise longer than the window is
reported as a finding with no recommendation and the repair extends within authority. Sources
with a cold open (no question) produce `pass`.

**Scale.** No new calls; the source review response grows by two small objects per candidate.

**Failure and accounting.** Unchanged; the source seat already has fallback and settlement.

**UX.** The panel's "Editorial evidence and concerns" shows the edge judgments; nothing else.

**Tests.** A synthetic evidence fixture reproducing Karma 2:41 to 3:22: the review must emit
`missing_premise` with `s000036`, the repair path must extend the opening, the compiled edit must
start there. A negative fixture where the earlier speech is another candidate's core: handoff,
not premise. Recorded-fixture replay unchanged for candidates with `pass`.

**Staging proof.** Karma re-run: video 1 opens at 2:41; the other eight unchanged or improved;
cost within $0.10 of baseline.

### W2. Human decisions become the calibration set (one day)

**Design.** A `temnia-harness export-decisions --source <id>` command joins
`chapter_review_event` rows to the candidate identity (evidence sha, first and last sentence,
title hash) and writes a labelled set. A scorer in the experiment operator reports, per run:
agreement of the source review's select/withhold with human accept/reject; opening and closing
agreement within a sentence tolerance; and per finding kind, how often a human rejected a
candidate the reviewers passed. Every W1 to W5 PR reports these numbers for its staging run.

**Edges.** Rejections with no reason count as labels of lower weight. Decisions on an older
evidence revision are matched by sentence identity, not by artifact. **Tenancy.** The export is
scoped like the bundle export. **Tests.** Scorer unit tests on a hand-built set; the export on the
recorded fixture. **Legacy.** None; the table exists.

### W3. Repair per finding group, in parallel (two days, one staging run)

**Design.** Required findings are grouped by connectivity: two candidates joined by a handoff or
overlap finding are one group; otherwise one group per candidate. The workflow fans out one patch
request per group (`asyncio.gather` over activities, bounded by the route gate), each prompted
with only its group's candidates, findings and authorized source rows. Each group's patch is
validated and admitted on its own with today's rules; an admitted group becomes part of one new
selection revision; a refused group takes the #45 bounded correction independently. Only changed
candidates are re-reviewed (already keyed by candidate hash). `maxRepairs` counts iterations, so
the allowance is unchanged in money terms except that parallel groups reserve together.

**Contracts.** A `SelectionRepairPlan` (groups with candidate and finding ids) recorded as an
artifact; `TopicSelectionPatchV3` unchanged per group; the merged revision records which group
produced each operation.

**Edges.** A group's patch touching a candidate outside its group is refused by the existing
affected-set rule. Two groups that both want the same sentence cannot exist (connectivity puts
them in one group). An empty group (findings with no candidate) is an opportunity group and uses
`add_opportunity` alone.

**Scale.** Nine candidates with findings on four of them produce at most four small calls in
parallel instead of one large one; wall time drops, tokens drop, cost roughly flat.

**Failure and accounting.** The budget check reserves the sum before fan-out; one group's
transient failure retries and falls back alone; an unknown outcome fences only that group; the run
stops on the first conclusive account failure as today. Request identity includes the group, so
replay is per group.

**UX.** The run summary lists admitted and refused groups with their diagnostics instead of one
sentence.

**Tests.** Two-group fixture where one is refused: the other lands, the refused one is corrected
next iteration; identical-selection detection per group; per-group cost settlement; replay by
request identity; a workflow test with a fake that 429s one group.

### W4. An author that investigates (two to three days, one staging run)

**Design.** The author and patch agents get deterministic, free tools over the evidence already
loaded in the activity: `read_sentences(first, last)`, `find_text(query)`, `speech_gaps(first,
last)`, `shot_boundaries(first, last)`, `neighbors(candidate_id)`. The prompt shrinks to the
rubric, the inventory and a sentence index (id, start, first words); the transcript is read
through tools. PydanticAI's Temporal integration runs each model round trip as an activity, so a
tool loop is durable and each round trip is an attempt in the ledger; a seat becomes a sequence of
attempts with a round cap (eight) and a shared budget.

**Edges.** A tool call outside the authorized window returns nothing with a reason, never
speech. A model that never calls a tool still gets the index and can write; quality is measured,
not assumed. The recorded fixture gains tool transcripts so replay stays deterministic.

**Scale.** This is the structural answer to long sources: the first request carries an index,
not a transcript, and admission stops being byte-as-token. The source review gets the same shape
later.

**Failure and accounting.** More round trips means more exposure to 429s; the route gate and the
per-route pacing already exist. The ledger's "one attempt per seat" becomes "n attempts per seat
call" with one operation id; settlement per attempt is unchanged.

**UX.** The bundle export includes tool transcripts for the "why did it cut here" question.

**Tests.** Fake model with scripted tool calls; window enforcement; round cap; replay
determinism; a workflow test where a mid-loop attempt 429s and the loop continues on the fallback
route with prior tool results intact.

### W5. Adjudication instead of a stop (one day)

**Design.** When cold and source reviews disagree on a candidate, or the repair allowance ends
with required findings, an adjudicator seat from the third family sees both reviews, the
candidate and its window, and returns select or withhold with a finding or a clearance. Bounded
to one call per disputed candidate per run; feeds the existing select-only gate.

**Edges.** The adjudicator cannot author; a clearance without evidence spans is refused.
**Accounting.** One more seat pool in the deployment file; same fallback and settlement.
**Tests.** Disagreement fixtures both ways; exhaustion with an adjudicator clearance renders,
without one withholds.

### Two code-side items found on 2026-09-14

Neither touches a prompt or a reviewer; both are in the review as F14 and F15.

**R1. One render job per revision, identity without a download (half a day).** The render
activity builds one `RenderJob` with every missing section of the revision and spawns once; the
card downloads the master once and encodes the sections in parallel as the function already
does. The worker verifies the master's identity from the ingest-time `source-timeline/1` record
and an object head, the same way the evidence stage does, and downloads only for the CPU
fallback. Heartbeat carries one call id. Tests: a three-video revision spawns one job; a resumed
activity reattaches to that one call; identity mismatch refuses before any spawn.

**R2. Physical-only findings resolved in code (one day, rides with W3).** Before any model
repair, each `physical_boundary_constraint` finding is resolved deterministically: extend the
affected edge to the nearest grounded cut outside the selected words, within the candidate's
authority and never into another candidate's core; record it as a physical-only operation with
the same finding citation the model would have used. Findings that cannot be resolved this way
(no grounded cut within the authorized window) stay required and are reported as such. Tests:
the 11:00 run's two withheld candidates resolve without a model call; a candidate hemmed in by
another's core stays withheld with the reason.

## 4. Sequencing and effort

| Step | Days | Staging run | Decision entry |
| --- | --- | --- | --- |
| W0 descriptions, composition split, the prompt-rule test | 1 | no | yes |
| W1 edges against the source | 1 | yes | yes |
| W2 calibration export and scorer | 1 | scores W1's run | yes |
| W3 repair per group | 2 | yes | yes |
| W4 author with tools | 2 to 3 | yes | yes |
| W5 adjudication | 1 | yes | yes |
| R1 one render job per revision | 0.5 | yes | yes |
| R2 physical-only findings in code (with W3) | 1 | with W3 | yes |

Days are my working days, each ending with a gate-green push. Every PR after W2 reports the
calibration scores of its staging run against the baseline.

## 5. Cost and time per run, expected

Baseline today: 24 calls, $0.72, 34 minutes on Karma. After W1: same calls, same cost, video 1
correct. After W3: fewer tokens in repair, minutes shorter. After W4: the author's first request
drops from 33k input tokens to a few thousand plus tool rounds; total tokens roughly flat on a
44-minute source and far lower on a two-hour one, which today is refused.

## 6. Operations, tenancy, legacy

No new services, queues or images. Every new artifact is scoped like the existing ones. Chapter
names on the topic path are renamed in W3's contract change (review F12). The recorded fixture
`tests/fixtures/harness/topic.synthetic.json` is regenerated once per workstream that changes a
request shape, by the existing recorder.

## 7. Open decisions for Rajesh

1. **Roster experiment.** Gemini as author with a different-family reviewer, through the
   experiment operator, on Karma, before any roster change. Not a code change; one run.
2. **W1 recommendation window.** Sixty seconds before the opening is my proposal; the evidence
   window already supplies it. A longer window costs tokens on every source review.
3. **W4 round cap.** Eight tool rounds is a guess; the staging run will say.
4. **Whether W5 is worth a fourth seat pool** or whether exhaustion should simply withhold. I
   lean to building it after W3 shows how often disagreement survives.

## 8. Why not LangGraph or ADK, restated

They provide graph orchestration, checkpointed state, a loop node with a cap, human interrupts
and evaluation tooling. Temporal already gives durability at the activity level, which is where
renders, GPU jobs and paid calls live; their persistence is graph-state persistence and their
Temporal plugins disable it. They have no notion of money, receipts or authority. Adopting one
means running it inside a single activity (nothing gained) or replacing Temporal (the media lanes
and the audit trail lost). Everything in §3 is expressible in the workflow and PydanticAI we
have; if a graph notation helps readability inside the workflow, `pydantic-graph` is the same
authors and the same runtime.
