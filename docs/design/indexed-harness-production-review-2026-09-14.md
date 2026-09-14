# Indexed harness production review

Reviewed: 2026-09-14. Code: `2140fbd` on `feat/indexed-editorial-evidence`.
Scope: source-index construction/reuse, tools, checkpoint reconstruction, V3–V7 orchestration,
stage admission, route/accounting limits, recovery, compilation/render gates, staging config and
tests. This is a review, not a rollout.

## Verdict

The implementation is not ready to claim reliable unattended two- or four-hour processing.
There are reproducible functional defects in evidence delivery and review admission, in addition
to deliberate stop conditions. The previous green repository gate remains valid test evidence;
it did not establish that a real agent receives useful tool results or reads enough source to
substantiate a clean internal-structure verdict.

The web still selects V3. V4–V7 are registered separate workflows available through the controlled
operator, not the default action. Exact-request pre-flight is optional: the September 13 decision
removed mandatory qualification manifests. Live tool-loop and editorial evaluation remain missing
release evidence, rather than an existing runtime gate.

## Actionable findings

### F1 — P1: Compaction removes tool observations before the model can use them

`source_progress.py:118–184` projects successful tool results into arguments, node IDs, sentence
IDs, media-event IDs and pagination flags. Only transcript sentences retain their contents.
`compact_source_messages` replaces all prior messages with the original prompt and that projection
(`source_progress.py:252–300`). Production installs this as `ProcessHistory` (`models.py:1505`).

- Browse loses previews, keywords, sentence ranges, timestamps and child counts.
- Search loses those fields and relevance scores. Ranked node IDs cannot tell the model which
  sentence range to give `read_source`; there is no read-by-node-ID tool.
- Candidate inspection loses internal-region descriptors and selected subranges.
- Media evidence loses sensor values, leaving event IDs without their measurements.
- Assistant hypotheses and working notes are discarded; no bounded editorial working-state field
  exists.

Reproduction using the actual browse function and compactor: the returned section contained 14
descriptive fields; its continuation contained neither the preview nor first sentence ID. The
model sees only `section-0001` and audit facts. This is model-boundary information loss.

Fix: retain a bounded model-visible working set of navigation/search/candidate/media observations,
including exact read handles and identities. Separate it from the audit trail. Test the actual
next request for all five tools, pagination and errors, not only history size and result IDs.

### F2 — P1: A clean candidate review does not require reading its internal speech

`save_source_review_shard` derives required reads from the work item and returned review
(`topic_selection_activities.py:1944–1949`). A local candidate work item carries IDs, not its full
extent or required transition reads. `validate_reviewer_inspection` requires navigation pages,
not exact reads across their boundaries (`editorial_evidence.py:339–402`). A select decision can
cite one small span.

Reproduction: extended a fixture candidate to 80 sentences; returned select citing its first
sentence; browsed, searched, inspected all three candidate regions and read only that first
sentence. Source inspection, required-read admission, reviewer inspection and pure shard admission
all passed. Checkpoint persistence would faithfully prove that one read; it adds no missing
internal-coverage requirement.

The previous statement that every candidate receives an explicit internal-structure decision is
too strong. The schema still uses a general selection decision and findings; internal structure
is a prompt obligation without separate typed decomposition or mandatory internal reading.

Fix: explicit bounded candidate-internal tasks, grounded subtopic observations, continuation and
completion records, and deterministically required exact evidence before a clean verdict.
Evidence delivered must remain distinct from measured comprehension.

### F3 — P1: Item-count batches do not bound required final evidence

All cited sentences must remain in the final checkpoint, limited to 320 sentences and 128 Ki
characters (`source_progress.py:38–42`; `source_index.py:803–827`). Author batches use 12
opportunities; review uses candidate/opportunity counts; repair uses findings/resources. None
partitions by the union of exact evidence needed by the final output.

Reproduction: read 321 sentences completely through five valid pages. The checkpoint retained 320;
admission of the whole span refused its evicted first sentence. Rereading evicts another sentence.
No tool sequence can satisfy that answer. The character ceiling can cause the same impossibility
below 320 sentences. One long candidate or several modest candidates can trigger it.

Cold review also remains one full-candidate prompt (`topic_selection.py:437–472`) with no candidate
continuation. Both input and generated output still have route limits.

Fix: deterministic evidence-size planning, bounded grounded subdecisions and an evidence-bound
aggregate verdict. Preserve semantic coupling when subdividing. Raising one constant is insufficient.

### F4 — P1 for rollout: Committed dispatch settings cannot execute the scale design

`apps/pipeline/harness/staging.json:9` sets `maxDispatches=64`, alongside $20 maximum run budget,
three repairs and route concurrency two. These are committed settings, not a fresh observation
of the running service; each actual frozen run snapshot is authoritative.

Reproduction: the 2,400-sentence four-hour fixture with an empty portfolio produces 75 omission
review work items. Those alone need at least 75 responses, before mandatory tool rounds, ten
inventory sections, authoring, cold reviews or repair. Every model round consumes a dispatch.

Fix: estimate mandatory work before spending, expose projected/consumed work and remaining
capacity, choose explicit measured run settings and support durable unfinished-work continuation.
Operational envelopes must not become output count/duration targets.

### F5 — P1: Reviewer route selection excludes only the current author

V5/V7 retain all author/repair families, but `prepare_topic_selection_call` calls `editorial_routes`
with only current indices (`topic_selection_activities.py:938–942`). That helper excludes only the
current author's family (`topic_editorial.py:54–75`). Source-shard admission later rejects families
present in the complete contributor set (`topic_selection_activities.py:1951–1957`).

Concrete committed-snapshot example: Kimi and DeepSeek contribute author shards, making DeepSeek
the current author. At reviewer fallback index 1 the helper selects Kimi, which already authored
part of the selection. A paid source review is rejected after dispatch. Cold-review admission does
not perform the same explicit all-author-family check.

Fix: filter against every contributing generator family before dispatch, preserve per-observation
provenance and revalidate cold-cache independence when exclusions change. Exhaustion needs an
explicit pre-dispatch result.

### F6 — P2: Review planning refusals become generic failures

Review planning refuses more than 16 inspection candidates or 48 contextual opportunities
(`topic_source_review.py:267–359`). Its workflow call (`topic_selection_workflow.py:1283–1290`)
does not catch `validation_refusal`. The outer workflow marks an activity failure; its generic
formatter loses the detailed reason. V7 repair planning does retain an adjudication diagnostic.

Fix: typed stopped outcomes with the offending work item and required action for deterministic
planning/assembly refusals. Apply consistent handling to inventory and author boundaries too.
Keep infrastructure failures distinct.

### F7 — P2: An implicit SDK limit precedes the documented checkpoint limits

Installed PydanticAI defaults `UsageLimits.request_limit` to 50. `Agent.run` applies that default
when none is supplied; `run_seat` supplies none. Request 51 raises `UsageLimitExceeded`, even
though the checkpoint allows 256 successful tool calls. These are different counters.

`execution_limit` recognizes budget, dispatch, context and source-progress limits, not
`UsageLimitExceeded` (`topic_selection_workflow.py:180–190`); the stop becomes a generic failure.
Route retries start another agent invocation, so this is not a durable whole-work-item limit.

Fix: explicitly freeze the model-round policy, account for continuation/recovery, classify
exhaustion and document it. Do not simply disable all liveness controls.

## Remaining capabilities and unverified risks

- **Inventory:** admission requires browse plus reads of returned spans, not reading all original
  speech. Empty inventory can pass that access check. V6 omission scans require every sentence
  in their leaf, but downstream review can be prevented by F1/F4. No recall/comprehension result
  follows from navigation coverage.
- **Cross-item reconciliation:** author groups are section/12-opportunity slices, not semantic
  components. Later review/repair may reconcile ownership, but there is no bounded episode-level
  opportunity/relationship reconciliation protocol. Semantic abstracts, recurrence, question/answer,
  correction and continuation links remain future work; descriptors are extracts and keywords.
- **Oversize continuation:** long candidate, dense omission context, coupled repair or oversized
  sentence can refuse with no automatic subdivision/adjudication continuation.
- **Recovery scope:** durable activity replay and known-provider checkpoint recovery exist. A
  completed limit-stopped run is not a suspended agent. Revision-zero retries reconstruct work and
  reuse settled responses; a cached schema-invalid answer is not thereby corrected. With a compiled
  revision, `program` resumes rendering directly (`topic_selection_workflow.py:728–739`), not
  incomplete editorial shards. `needs_review` with a revision cannot use planning retry. A durable
  editorial resume/adjudication operation and UX are still needed.
- **Workflow scale:** aggregate plans, drafts, assessments/manifests and shard-reference lists
  cross Temporal boundaries. All shard coroutines are created behind semaphores at once. No
  `continue_as_new`/child-workflow partition or measured long-history rollover was found. Pure
  fixture planning does not measure history size, replay memory, payload admission or recovery.
- **Concurrent failure drain:** inventory/author/source/repair use `asyncio.gather` without
  `return_exceptions=True`. Unexpected errors propagate without an explicit stage-level drain of
  siblings. Existing expense fences remain; interrupted production-workflow tests must prove how
  already-dispatched siblings settle and expose retained shards when the workflow exits.
- **Backend retrieval work:** each tool reloads the whole index; each search rebuilds BM25 corpus
  statistics. Bounded response size does not establish bounded backend cost or acceptable latency.
- **Editorial/audio proof:** no new labeled calibration/recall set, listening judge or real indexed
  44-minute/two-hour/four-hour provider/editorial result. Sensor evidence is not playback. Retrieval
  relevance, multilingual robustness and cost/latency remain unmeasured. Safe cuts do not prove
  topic ownership or publication quality.
- **Product:** web still selects V3, and complete V7 progress, pending-work, adjudication and
  recovery flows are not implemented in the product surface.
- **Render versus approval:** `require_complete_review=True` in `selection_candidates_for_render`
  means source-selected and without required findings; it does not check assessment completeness
  or presence of a passing cold review. Missing/unknown cold review adds reasons, not a blocking
  finding. Such a candidate can still render for human review while the run remains `needs_review`.
  Do not describe rendering as proof that every review passed.

## Refusal and termination matrix

This groups boundary conditions by cause. Integrity checks have many distinct messages: a changed
ID, hash, revision, scope, order or dependency at any boundary belongs to the identity rows below.
Paging is a normal continuation; refusal, unavailable evidence and terminal failure are different.

| Boundary | Trigger | Consequence / recovery |
| --- | --- | --- |
| Startup | Disabled harness; invalid config; missing credential/model asset; invalid route/schema/privacy/transport setting or no eligible seat | No valid dispatch; fix configuration/assets. |
| Run identity | Foreign scope/source, changed pins or immutable intent, changed policy/prompts/schemas, experiment build mismatch, competing execution owner | Creation/resume refused; corrected identity or new run required. |
| Run state | Cancelled/stale/incompatible terminal state, unresolved paid exposure | New work/command refused; cancellation stops execution, unknown exposure requires reconciliation. |
| Evidence | >250,000 words or >10,000 sentences; inconsistent layers/timing/master; storage, disk or sensor failure | Evidence fails before index. Empty lexical transcript instead ends `needs_review` without edit. |
| Index | Sentence >64,000 characters; invalid hierarchy/ownership/vectors/encoder; corrupt/foreign/stale claimed cache artifact or producer/dependency mismatch | Refuse index. Valid cache miss builds; corruption is not silently trusted. |
| Request admission | Serialized request >512 KiB; estimated input + reserved output exceeds route context; incompatible output allowance | No dispatch; no automatic evidence subdivision. Estimate is ceil(bytes/2) + protocol overhead, not exact tokenization. |
| Accounting | Cannot reserve next request within remaining budget; frozen dispatch count exhausted; unresolved prior request/receipt | No new dispatch. Bounded-stage handling can end incomplete/`needs_review`; uncaught budget becomes `budget_paused`, dispatch `failed`, uncertainty `outcome_unknown`. |
| Permanent provider refusal | Parameter/privacy/policy/provider/account rejection, including HTTP 402 | Known failure; no blind replay. Correct account or frozen request/route as appropriate. |
| Transient provider failure | Conclusively known rejection/failure, or lost stream with settled charge | Two attempts per route, at least 20 s backoff plus provider advice, then next route; at most four route positions. Exhaustion fails; settled charges remain. |
| Unknown provider outcome | Interrupted/nonterminal stream, timeout/cancellation without conclusive accounting, missing/invalid generation or receipt identity | Reservation retained; `outcome_unknown` fence and reconciliation attempt. Timer does not authorize replay. |
| Activity deadline | Evidence 6 h; index 30 min; most plan/save/assembly/tools 2 min; compile 5 min; render 12 h; relevant heartbeat expiry | Activity fails or paid outcome becomes uncertain. Most non-model activities get three attempts; model/tool framework attempts are one. |
| Model deadline | Idle timeout; aggregate route timeout × ceil(payload/128 KiB), minimum one unit; activity timeout aggregate +60 s, capped at 45 min (600 s without frozen transport) | Accounting distinguishes retryable known failure from unknown fence. Not a universal fixed 540 s ceiling. |
| SDK limit | 50 model requests per agent invocation | Next request raises `UsageLimitExceeded`; currently generic failure. |
| Checkpoint limits | >256 successful tool calls; >8,192 accumulated result IDs; >384 KiB checkpoint; third identical successful result | `SourceProgressLimitExceeded`, incomplete stage. Identical-call guard can also stop useful rereads after eviction. |
| Retained evidence | Final cited sentence absent from final checkpoint; at most 320 sentences/128 Ki characters retained | Answer rejected; a larger required union cannot pass by rereading. |
| Tool arguments | Unknown/unauthorized parent/candidate/sentence, reversed extent, invalid cursor, invalid page limit/query | Tool/validation error. `retries=0`; no general corrective tool-error conversation. |
| Tool page limits | Browse/inspect ≤16 nodes; search ≤12 results, query 1–512 characters; read ≤80 sentences/64,000 characters; media ≤80 events | Valid larger ranges paginate; out-of-range limit arguments refuse. |
| Investigation admission | Missing/out-of-order browse/inspection, no hybrid search, missing cited/omission-leaf exact reads, altered results or missing exact final checkpoint dependency | Retain rejection; shard not admitted. Repeated completed browse/inspection can also fail. |
| Typed output | Invalid/incomplete schema/JSON, output truncation, missing/duplicate/foreign required items, invalid spans/claims | Paid response retained; rejected shard or unavailable judgment. No general schema-correction retry. |
| Inventory | Namespace/earliest-core ownership violation; missing/invalid/foreign/reordered section shard | No manifest, no author; `needs_review` at revision zero with partial evidence retained. |
| Author | Changed assigned inventory/set/order, foreign candidate namespace, invalid annotations/selection, incomplete shard set | No author manifest or review; twelve opportunities is not an evidence/output-size guarantee. |
| Independent reviewer | No eligible independent route, route exhaustion, reviewer contributed authoring | Review fails/rejects; mixed-family exclusion currently occurs too late. |
| Source plan | >48 context opportunities; >16 inspection candidates | Planning refuses without adaptive continuation; currently generic failure. Nominal batches: four candidate decisions, twelve opportunities, two pairs, one omission leaf. |
| Source answer | Wrong decisions/pairs; omission outside owned leaf; finding exceeds local authority; invalid overlap/handoff recommendation | Rejected shard; no complete manifest and no partial repair authority. |
| Repair plan | Connected component >12 findings, 8 candidates, 24 opportunities or 8 source sections; no valid required authority | Explicit adjudication stop; retain prior selection. |
| Repair request/patch | Prompt >256 KiB; operation count outside 1–16; missing findings, wrong namespace, unsupported mutation/source, foreign mapping, invalid merge/split/drop/overlap/handoff | Reject component. Prompt-size failure during call preparation currently becomes generic failure rather than plan adjudication. |
| Assembly | Missing/foreign/reordered/changed shard, actual write collision or invalid aggregate patch | No component applied. Complete valid disjoint aggregate applies once and gets fresh review. |
| Editorial loop | Complete assessment or no actionable finding; source review unavailable; max repairs reached; repeated semantic selection; no complete patch | Editorial loop ends. Exhaustion/repetition is not success. Committed staging has three repairs. |
| Render eligibility | No admitted source portfolio, source decision not select, or required finding affects candidate | Candidate excluded by select-only gate; no whole-episode fallback. Missing/unknown cold review alone does not block a review render. |
| Physical compilation | No grounded safe grid boundary, speech crossing/word-membership change, invalid pause ownership, invalid/out-of-extent source evidence or stale derived boundary data | Candidate refused; others may compile. Structural portfolio corruption can fail compilation globally. |
| Rendering | Stale revision/master/hash, storage/download/subprocess/GPU/CPU failure, expense fence, cancellation/deadline | Stop/fail render; exact accepted artifacts may be reused. GPU fallback retains existing conclusive-outcome rules. |
| Acceptance | No videos, technical checks fail, or human acceptance unavailable/declined/stale | `needs_review`. Normal successful rendering also finishes `needs_review`; publication is not automatic. |
| Retry | `needs_review` with current edit/revision; changed program/config; unresolved exposure; cancelled/ready state | Planning retry refused. Failed run with revision resumes render, not editorial work. Raising budget does not change frozen dispatch/repair limits. |

## Next implementation order

1. Fix tool observation delivery and next-request tests (F1).
2. Add typed internal review and evidence-sized subdecisions/continuation (F2/F3).
3. Enforce all-family independence before dispatch and on cached observations (F5).
4. Explicitly own limits/work estimates (F4/F7), consistent stopped outcomes (F6), and editorial
   resume/adjudication.
5. Test concurrent interruption, real Temporal history/payload growth, replay and recovery through
   the production operator with deterministic transport.
6. Integrate the intended product program and progress UX; then run declared exact live-tool
   requests and controlled real-source evaluation with human opportunity and playback labels.

## Review evidence

- Prior exact-commit gate: `e04e56d`, `2026-09-14T16:12:26.345Z`, retained in the V7 design record;
  not rerun for this review.
- Existing focused tests: 28 passed in 2.89 s across source progress, source review, repair,
  reviewer evidence, inventory and author packaging.
- Pure local reproductions: missing browse content; 80-sentence candidate admitted after one read;
  321 read sentences reduced to 320 and refused; 75 omission calls versus 64 dispatches; prior-author
  reviewer selection after fallback using the committed snapshot. Fixture encoders, no live provider.
- Installed SDK defaults inspected directly. No application fix, paid call, media generation,
  deployment, staging mutation or model audition was performed.
