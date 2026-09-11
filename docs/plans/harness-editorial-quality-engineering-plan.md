# Temnia Harness: Standalone Podcast Editorial Quality
## Source-grounded engineering review and implementation plan

**Prepared:** September 11, 2026  
**Status:** Implementation specification, not an implemented patch or a measured quality claim.  
**Suggested repository destination:** `docs/plans/harness-editorial-quality-engineering-plan.md`  
**Source:** The complete Temnia archive supplied in this conversation.  
**Companion evidence:** [Source excerpts and original line numbers](temnia-harness-editorial-quality-evidence.html)  
**Archive SHA-256:** `3abe749a1d9ec556d43693f322226a444fffcc6cea444ef5a25a16a278b7a387`

---

## 1. The outcome we are engineering

> Given a long podcast and an optional audience/focus brief, discover worthwhile discussions, select their natural self-contained extents, and produce faithful, interesting videos that an editor would be comfortable considering for independent YouTube publication.

This is an **editorial selection and completion problem**, not merely topic segmentation, scene detection, clipping, or export reliability.

A strong output must answer six questions:

1. **Why watch?** It offers a concrete reason for the intended viewer to spend time on this discussion.
2. **Can a new viewer enter?** Its selected speech establishes necessary people, concepts, questions, and references without requiring the original episode.
3. **Does it develop?** It provides a meaningful explanation, argument, story, experience, or exchange rather than just announcing a subject.
4. **Does it finish?** It reaches an appropriate stopping point, including any necessary answer, conclusion, qualification, correction, or acknowledged uncertainty.
5. **Is it faithful?** The extraction does not change the source's consequential meaning or misrepresent the speakers.
6. **Does the actual video work?** Its rendered beginning, ending, audio, and relevant visuals support the editorial judgment.

At the episode level, also ask: **Did we find the worthwhile opportunities, and are the selected videos meaningfully distinct?** Excellent individual outputs do not establish that important material was not missed.

### What “perfect” means operationally

Treat the target as a continuously tested quality contract: strong selection, complete discussions, preserved meaning, clean playback, honest uncertainty, and little avoidable human repair. Do not substitute an attractive architecture or a model's self-awarded score for these outcomes.

This plan does not promise audience metrics such as views, click-through rate, or retention. Those are not established by static code review or model judgment. “Publishable” here means editorially suitable for the stated task, not automatic authorization to publish.

### Binding constraints

Keep independent **contiguous** source intervals in their original order. Overlapping setup or completion is allowed. Unused source material is allowed. The source determines useful duration and output count: no forced shorts, fixed chapter lengths, or quotas. A long complete discussion and a short complete discussion can both be valid. An honest unresolved debate can be complete without a definitive answer.

Do not manufacture dramatic hooks, narration, certainty, or dialogue. Do not make montage editing, reordering, thumbnails, publishing automation, a new orchestration framework, or a topic-graph database prerequisites for this feature.

Existing configured execution/spend limits remain enforced; they are operational constraints, not editorial selection rules. Budget exhaustion must not be relabeled as “no useful topics.”

## 2. What this review establishes—and what it does not

The archive contains 693 files. This pass rechecked the relevant source, contracts, existing editorial plan, tests, and retained trial journal. It did not modify the repository, run paid models, retrieve private source recordings, or evaluate new rendered outputs. Source references and hashes are provided in the companion evidence index.

The repository already has a focused plan: `docs/plans/standalone-topic-intelligence.md`. It anticipates several of the recommendations below, including opportunity coverage, separate judgments, full-recording evaluation, and preserving natural extents. **The next work is to implement and demonstrate missing behavior—not rewrite that thesis with different terminology.** [E08]

The earlier broad engineering audit remains useful separately. This document takes priority for the **editorial-quality workstream**, not for unrelated security or operational release decisions.

### Verified design strengths

| Existing behavior | Evidence | Preserve it because |
|---|---|---|
| The brief explicitly requests interesting, independently understandable topic videos. | `topic_editorial.py:35–56` [E01] | The product objective is already correct; a generic “be more interesting” prompt is not the main missing feature. |
| The cold judge receives only selected speech/title, not source-wide context or author rationale. | `topic_editorial.py:176–202` [E02] | It can expose missing setup that a source-aware reader may unconsciously supply. |
| The source judge checks omitted qualifications, corrections, setup, and duplication. | `topic_editorial.py:205–229` [E02] | Local intelligibility alone does not establish source fidelity. |
| Candidates include core, context, completion, and consequential follow-up spans. | `packages/contracts/src/topics.ts:19–36` [E04] | Semantic obligations already have a source-linked representation. |
| Publication intervals are independent; each reuses an existing exact-cover execution internally. | `topics.ts`; `topic_compiler.py:172–209` [E04, E10] | Portfolio overlap is compatible with safe per-video execution. Do not weaken the legacy compiler. |
| Review judgments require reasons and valid cited spans; missing observations do not authorize arbitrary rewrites. | `topic_editorial.py:244–273, 329–385` [E03] | These prevent fabricated evidence and destructive “repair everything” behavior. |
| The workflow reserves a nonauthoring reviewer family and retains proposal/review lineage. | `topic_editorial.py:68–76`; `topic_activities.py:310–393` [E01, E06] | Keep attribution and accounting. Different families are a design control, not proof of independent errors. |
| The existing topical path optionally consumes Chapter-Llama hints, then runs author/reviews/compile/render. | `topic_workflow.py:292–305, 310–519` [E07] | Integration scaffolding exists. Another framework is not needed to test editorial improvements. |

### The actual quality gaps

**Viewer value is less explicit than completeness.** Current review criteria are `intelligibleBeginning`, `coherentTopic`, `completeDiscussion`, `titleFaithful`, `faithfulMeaning`, `completeContext`, and `distinctPurpose`. Usefulness is not absent from the prompts, but there is no equally explicit account of the intended viewer's reason to watch and the delivered value. [E02, E05]

**Audience is not shared consistently.** The author receives `run.brief`; cold and source prompt calls do not. The cold prompt assumes an interested general viewer. Specialist content can therefore be authored and judged against different audience assumptions. [E02, E06]

**Supplied candidates receive review; missing opportunities do not have an equivalent required assessment.** A good selected subset is not evidence of comprehensive discovery. [E02, E05, E07]

**The repair policy protects good candidates but lacks an explicit compound editorial transaction.** A merge involving a failed candidate and a previously passed neighbor needs authorized scope, not permission to rewrite every candidate. [E03]

**Actual media remains a distinct observation.** Current automated editorial prompts explicitly disclaim listening or visual inspection. Technical qualification and text review do not substitute for judging delivered content. [E02]

### The most useful retained regression

The September 11 journal records nine proposed topic selections. Its independent text inspection identifies one candidate opening with “For that” and “how do you develop that?” while its necessary explanation is in the preceding topic. It also identifies a weaker ending for another candidate and two overreaching internal explanations. The journal explicitly records **zero subsequent automatic critic calls, editorial repairs, renders, or human acceptances** for that response. [E09]

This is a development case, not a quality rate, a model ranking, or proof that the full harness would fail to repair it. Its raw transcript-bearing artifacts are recorded as outside Git. Obtain them from the owner's retained run using authorized access; do not invent the missing fixture or derive a supposed gold cut from the journal alone.

**First question to answer:** Can the current complete harness detect and repair that missing setup, without source-specific coaching in the default prompt, and produce an independently watchable video?

## 3. Keep one typed editorial program

Use the existing durable workflow and model/accounting layer. The following are responsibilities and artifacts, **not a requirement for one new agent, service, or paid call per box**.

```text
Frozen source evidence + frozen audience/editorial rubric
                         |
          Full-source or source-linked episode context
                         |
                 Topic opportunities
                         |
          Candidate extents and dependency review
                         |
        Cold comprehension/value + source fidelity
                         |
             Bounded, evidence-authorized repair
                         |
        Portfolio selection + missed-opportunity audit
                         |
              Deterministic physical compilation
                         |
                 Render and inspect
                         |
            Human publication-candidate decision
                         |
              Episode-level quality evaluation
```

A missing-opportunity finding can return to candidate authoring. A physical failure first returns to deterministic boundary analysis. A changed semantic extent returns to affected editorial reviews. A title-only change returns to title/brief-sensitive review, not unnecessary media rendering.

No stage may assert a modality it did not observe. A valid sentence ID proves evidence availability, not that a semantic interpretation is correct.

## 4. Establish one audience and editorial rubric

### The change

Create a small frozen rubric that captures the intended viewer and task, and supply the appropriate parts to the author and every reviewer. Use existing defaults when no instructions are supplied; the generic button must work without prompt engineering by the user.

Proposed fields—not existing contracts:

| Field | Purpose |
|---|---|
| `audienceDescription` | Who the independent video is for. |
| `assumedDomainKnowledge` | General subject competence, not knowledge of this episode. |
| `viewerGoals` | What the viewer wants to understand, solve, experience, or consider. |
| `languagePolicy` | Expected language and treatment of code-switching/subtitles. |
| `focus` | Optional preferred subjects; not permission to misrepresent the source. |
| `exclusions` | Explicit user exclusions, kept distinct from model disinterest. |
| `editorialMode` | Initially `standalone-contiguous-discussion`. |
| `sourceFidelityRequired` | A non-negotiable invariant. |
| `rubricVersion` | Frozen policy identity used in prompts, caches, and evaluation. |

Start with deterministic defaults plus explicit user refinements. Do not require another LLM to rewrite every simple brief. When structured interpretation is necessary, retain the original request and the interpreted rubric separately.

### Protect the cold reviewer

Provide audience knowledge, not episode answers. “Senior Android engineers” is valid audience context. “The speaker was referring to the previous prayerfulness explanation” would leak the missing setup and invalidate the cold test.

Keep outside sentences, episode maps, planner rationale, dependency annotations, and other candidates out of the cold input. Do not let a title repair an incomprehensible opening. Optionally test a speech-only first impression before title judgment, but qualify that as a changed evaluator rather than silently altering the baseline.

### Code changes

Update `cold_prompt`, `source_prompt`, and their calls in `TopicActivities.prepare`. Include rubric identity in persisted dependencies and the exact review cache identity. `cold_review_key` currently reflects candidate ID, extent, and title, with other identity bound elsewhere; the new rubric must be part of the effective identity at both lookup and retained-response validation. [E01, E02, E06]

If the rubric changes, invalidate the relevant judgments and downstream acceptance assumptions. The source transcript itself need not be regenerated.

**Acceptance:** all seats see the same audience requirements; no source context leaks into cold review; changing an audience cannot reuse an old judgment; a title does not rescue a dangling referent.

## 5. Make viewer value an explicit, evidence-backed judgment

Do not replace the existing safeguards with one “engagement score.” A fascinating but misleading excerpt is not acceptable, and an independently complete explanation need not be sensational.

### Proposed review dimensions

Keep the existing semantic criteria. Add a small cold-input-compatible value assessment:

| Dimension | Question | Evidence expected |
|---|---|---|
| `viewerReasonToWatch` | What makes this discussion worth the specified viewer's attention? | Selected sentences establishing the problem, tension, experience, idea, or question. |
| `deliveredValue` | What meaningful answer, insight, experience, resolution, or articulated uncertainty is delivered? | Selected sentences developing and completing that value. |
| `openingEffectiveness` | Does the natural opening establish the discussion without unnecessary unrelated runway? | Opening speech plus the selected topic's development. |
| `focusedDevelopment` | Is the material substantively connected to its purpose rather than mostly filler or unrelated conversation? | Development spans and any problematic detours. |

The cold reviewer should briefly reconstruct the viewer purpose and takeaway **from the selected content**, before judging them. It must not copy the author's claimed purpose as evidence.

Avoid forcing all sources into question-and-answer form. Stories, explanations, debates, personal experiences, and unresolved but well-developed questions can deliver different kinds of value.

### Judgments and severity

Use grounded decisions with `pass`, `fail`, and `unknown`, plus a specific reason and evidence spans. Keep fatal meaning/completeness failures separate from softer comparative preferences such as a more efficient opening. Do not make every preference a hard rejection.

For portfolio ranking, a small anchored ordinal judgment—such as `strong`, `adequate`, `weak`, `unknown`—can be useful after calibration. It is not a probability of success. Define examples for each category with editors before using it for automatic filtering.

Do not impose “hook within 15 seconds,” a minimum number of insights, forced conflict, or universal duration penalties. Those are different product choices and can damage natural long-form discussion.

### Titles

Generate or refine a clear, source-faithful title after the discussion's extent is established. Recheck any title change against the selected content and audience. A strong title expresses the actual promise; it must not invent a conclusion, attribute one speaker's idea to another, or conceal absent setup.

A content pass and a title pass remain separate. Title alternatives should not create additional videos with the same core discussion.

**Acceptance:** distinguish a complete but low-value exchange from a compelling complete discussion; accept a valuable non-dramatic explanation; accept completed uncertainty; reject unsupported title claims; preserve audience-specific relevance.

## 6. Discover opportunities separately from approving outputs

### A lightweight opportunity inventory

Introduce a source-linked inventory of worthwhile discussions. It can be emitted by the existing author initially; it does not need a new service. For each opportunity, retain its core evidence, potential viewer purpose, known dependencies, and disposition.

Proposed contract shape:

```text
TopicOpportunity
  id
  evidenceSha256
  rubricSha256
  coreSpans[]
  viewerPurpose
  valueEvidenceSpans[]
  dependencyHints[]
  candidateIds[]
  disposition
  dispositionReason
```

Useful dispositions include `selected`, `missing_candidate`, `duplicate_core`, `not_useful_for_audience`, `not_contiguously_extractable`, `needs_evidence`, and `deferred_execution_limit`.

A missing response or exhausted budget is not evidence of low value. Every disposition must preserve that distinction.

### Independently look for omissions

An inventory created by the author is still a hypothesis. Add an explicit source-context task to identify strong unrepresented opportunities. Reuse the source-review call where its capacity and schema allow; measure a separate blind discovery pass only when necessary.

A review that only sees the author's labels can inherit the author's blind spots. The omission check must have access to original source content and be allowed to propose an opportunity absent from the supplied inventory.

Source-time gaps are useful places to look, not a definition of missing value. A missed discussion can sit inside a selected long interval but lack its own independently useful treatment. Conversely, unused filler is not a missed opportunity.

### Portfolio selection

Choose among complete, faithful candidates for audience value and meaningful distinctness. Shared setup is allowed; repeating the same core under multiple titles is not extra value.

Do not use interval overlap, title similarity, or a repeated broad subject as automatic proof of duplication. Two conversations about pricing might deliver genuinely different answers. Preserve justified differences and record why.

Permit a modest internal pool of alternatives when useful, bounded by execution capacity. The number of internal hypotheses is not a required number of exports. Avoid promising exhaustive discovery from one model's statement that it “read everything.”

**Acceptance:** deliberately remove a worthwhile candidate; the source-level audit identifies the missing opportunity. Leave greetings unused; they are not demanded as outputs. Reuse a question across genuinely different discussions; that alone is not flagged as duplication.

## 7. Improve selection of the complete natural extent

The source contract already has dependency-role spans. Extend their use; do not discard them for generic summaries.

### Make consequential dependencies inspectable

For difficult cases, record simple typed relationships between source spans:

```text
question -> answer
referent -> antecedent
claim -> qualification/correction/rebuttal
setup -> example -> explanation
story setup -> consequential event -> resolution
visual reference -> required visible evidence
```

These can be JSON records. No graph database or exhaustive relationship extraction is required. Each claim needs source evidence and a reason it is necessary to the candidate's meaning.

### Evaluate small competing extents

When a boundary is questionable, consider source-grounded alternatives such as the current extent, the extent including its question, and the extent including necessary earlier explanation or later qualification. Generate only alternatives justified by the observed problem; do not enumerate all possible interval pairs.

For each alternative ask:

- Does a cold viewer understand the opening?
- Does the discussion complete faithfully?
- Does the full contiguous interval still offer one coherent viewer experience?
- Is any additional material actually needed, rather than merely related?

Choose the smallest **coherent and faithful** extent that preserves the discussion, not mechanically the shortest interval. Sometimes a longer variant is better. Sometimes no contiguous interval is worth exporting.

### Avoid the expansion trap

Including every remotely related later sentence can make a segment technically comprehensive but editorially unfocused. After adding setup or a follow-up, recheck the whole selected interval for value and coherence.

When the only faithful extraction includes extensive unrelated material, record `not_contiguously_extractable` or send it to human review. Do not silently introduce stitching or fabricate a closing line.

### Evidence reliability

A faithful transcript extraction can still misrepresent the audio if transcription missed a negation, attributed a sentence to the wrong speaker, or misunderstood a consequential name or number. Add targeted audio verification for suspected meaning-changing errors and uncertain boundaries, rather than blindly retranscribing every candidate.

Transcription confidence is a triage signal, not proof of correctness. A corrected transcript must create new evidence identity and invalidate affected judgments. The editorial model must never silently rewrite the authoritative transcript to make its selection pass.

**Acceptance:** include a required question, retain a later correction, preserve acknowledged uncertainty, refuse a sprawling noncoherent interval, and handle suspected transcript errors without invented speech.

## 8. Repair decisions without losing good work

### Use structured findings

Extend review findings with stable identifiers and explicit affected candidates/spans. Distinguish at least:

```text
missing_setup
missing_qualification
unfinished_answer_or_story
unsupported_title
weak_viewer_value
unfocused_extent
duplicate_core
missed_opportunity
transcript_uncertainty
physical_boundary_constraint
```

A reviewer must state what is wrong and cite evidence. It may suggest a repair, but deterministic admission still controls permitted source references and scope.

### Evidence-authorized patch operations

Add a typed patch envelope with:

```text
baseProposalSha256
rubricSha256
evidenceSha256
findingIds[]
affectedCandidateIds[]
operation
replacementCandidates[]
reason
```

Support `extend_start`, `extend_end`, `retitle`, `merge`, `split`, `drop`, and `add_opportunity` as explicit operations, with source-sentence extents rather than model-selected milliseconds. The exact operation encoding can be simplified during implementation; enforce its invariants.

A compound merge may legitimately affect a previously passed neighbor. Require explicit authority for the affected set and invalidate the appropriate reviews. Do not remove `validate_preserved_candidates` and permit a complete unconstrained rewrite.

For splits, require independently meaningful children—not two pieces whose connection only works when played consecutively. For a new opportunity, assign new stable identity and retain its discovery evidence. For a merge, preserve parent lineage and identify the superseded candidates.

### Invalid responses and convergence

Aggregate available source-reference diagnostics so one model response can correct several invalid fields. Apply valid patches atomically. A malformed replacement is retained as a failed attempt, not promoted over the last valid proposal. Do not silently drop invalid candidates and claim the source was fully handled.

Separate source-admission correction, judgment-schema correction, semantic repair, deterministic boundary repair, and uncertain-provider reconciliation. These can share the existing accounting system but must not be confused as the same editorial operation.

Track repeated semantic proposals and unresolved findings. Stop when findings are resolved, no material improvement is available, an operation is unauthorized, required evidence is unavailable, or the configured execution limit is reached. Preserve a best-known valid, transparently reviewed state. More iterations are not automatically better.

Unknown review is neither a passing judgment nor permission for an unrelated semantic rewrite. Do not replay a paid request with an unknown outcome as though it were an ordinary content correction.

### Review invalidation

| Change | Required invalidation |
|---|---|
| Start/end sentence extent | Candidate cold/value/source judgments; dependency assessment; compilation; rendered review; human acceptance of old media. |
| Title only | Title-sensitive and effective cold-input judgment; human acceptance of changed packaging. Media bytes need not change. |
| Audience/rubric | Audience-dependent judgments and portfolio decisions; not inherently source transcription. |
| Add/drop/merge/split | Affected candidate judgments and portfolio coverage/distinctness. Unchanged clip-local reviews can survive when exact inputs match. |
| Correct transcript/source evidence | Every derived judgment using changed evidence; explicit new identity, not mutation of old artifacts. |
| Human accept/reject only | Decision/export manifest, not unchanged media analysis. |

**Acceptance:** repair the missing-context case; permit an authorized merge; preserve unaffected candidates; reject an unauthorized patch; retain the prior valid proposal after malformed output; terminate a repeated nonimproving loop without claiming success.

## 9. Physical editing must support—not redefine—the discussion

Preserve the existing source clock, selected tracks, rational time, and compiler semantics. Do not normalize every source to CFR as a side effect of this plan. New logic must fit Temnia's existing output-grid and source timestamp mapping.

The observed candidate builder samples pause midpoints, sentence/turn starts, and other supplied candidates. `_boundary_constraints` filters those candidates rather than deriving every feasible interval. A sparse inventory can therefore miss a feasible safe cut. This is a quality-enabling correction when it prevents a good selection from reaching the viewer. [E10, E11]

### Required algorithmic behavior

For the selected opening/ending:

1. Derive the legal lexical membership window from the selected and excluded words.
2. Intersect that window with permitted acoustic regions using the evidence state and all relevant observed speech.
3. Determine whether representable output-grid instants exist under the established source mapping.
4. Construct a bounded grounded candidate set from those feasible regions; include useful interior candidates rather than only a midpoint or endpoint.
5. Rank visual/acoustic preferences among valid candidates, then run the existing final compiler checks.

An interval containing no safe representable instant is different from an incomplete candidate inventory. Unknown speech evidence must remain unknown, not become silence. Preserve configurable uncertainty treatment and review escalation instead of pretending a detected gap proves perceptual quality.

Keep exact rational arithmetic. Do not round an instant out of its safe region. A word-midpoint survival check is not enough: retain the complete intended word membership. Test actual output-grid conventions, source offsets, overlapping speakers, and fractional frame rates.

Run deterministic refinement before asking the model to expand an already complete topic. Physical-only repairs must not remove selected speech or change the viewer purpose to evade a compiler constraint.

PySceneDetect provides existing visual transitions as optional preferences; Chapter-Llama provides navigation/topic hypotheses. Neither can certify conversational completion. Their contribution should be measured separately from changes in semantic selection. [E01, E07, E11]

**Acceptance:** find a safe grid cut inside a valid silence region when midpoint/start samples fail; continue refusing continuous speech without a safe cut; never move a semantic boundary merely to follow a camera change.

## 10. Judge the actual export

### Maintain four separate records

1. Deterministic source/compile validity.
2. Transcript-based editorial assessment.
3. Rendered-media observation.
4. Human publication-candidate decision.

For each rendered video, inspect its opening and closing speaking turns with enough adjacent original media to diagnose a boundary problem. Inspect interior passages when meaning depends on visuals, prosody, attribution, or suspected transcription errors.

Check whether the opening sounds complete, the end preserves the final thought/reaction when needed, cross-talk is handled acceptably, a referenced demonstration is visible, and captions represent the selected speech. These are separate from file decodability and duration.

A model without actual audio/video input cannot perform this review. When the configured observer lacks a modality, retain the limitation and route it to human inspection. Do not turn a text-only model's assertion into media approval.

Boundary previews are useful for defect detection but do not establish that the full video is compelling. During qualification, independent viewers should watch complete outputs. Use sampled or risk-triggered media review for later operation only after measuring its missed-defect rate.

Human approval remains bound to the exact video/title/rubric revision inspected. This work does not authorize automatic publishing.

### Minimal editor controls

Provide source-sentence-bound opening/ending adjustment, title correction, merge/split where authorized, adding a missed discussion, and accept/reject with a reason. Show selected speech and adjacent context. These controls are part of collecting useful quality feedback, not a request to redesign the whole UI.

The current topic command accepts only accept/reject/cancel. Add a separate versioned edit command rather than smuggling new behavior through fields that current admission explicitly rejects. [E12]

Record how much human repair was needed. A video accepted after a major manual recut is not evidence of excellent unassisted selection.

## 11. Support long podcasts without losing the episode

Retain the direct full-source author/reviewer path as a baseline where it fits the qualified model and works well. Do not make hierarchy mandatory merely because the recording is long.

For larger inputs or measured missed-dependency failures, implement a source-linked global/local path:

```text
authoritative sentences and speaking turns
 -> episode index with original spans
 -> topic opportunities
 -> local original-text examination
 -> source-wide search for consequential setup/follow-ups
 -> portfolio reconciliation and omission audit
```

Use summaries as retrieval indexes, never the sole proof of an opening or ending. Window using actual model input limits with headroom for instructions and output. Preserve original IDs and offsets. Overlap at natural turns and reconcile window-owned results; a local window start is not a new source-level topic by definition.

Record which original ranges were available/examined through each operation and which remain unresolved. An input coverage manifest establishes supplied evidence, not proof that a model understood every sentence.

Before reviewing a candidate pool, estimate/reserve capacity for the intended complete review and a bounded repair path using existing admission controls. When capacity is insufficient, explicitly defer work or request the configured operational choice; do not promote the easiest reviewed subset as complete discovery.

Changing the model, hierarchy, discovery task, and detector simultaneously prevents useful attribution. Introduce one change per comparison when diagnosing quality.

**Acceptance:** recover a discussion across processing windows; retain a distant consequential qualification; do not truncate the final source portion silently; report deferred/unreviewed opportunities separately from rejected content.

## 12. Cross-language contracts and integration map

The repository declares Zod as the schema owner and emits JSON Schema/generated Python models. Keep this arrangement. Do not hand-edit generated `apps/pipeline/src/temnia_pipeline/contracts.py`. [E13]

The following names are **proposed new artifacts**, not claims that they exist today:

| Proposed artifact | Minimum role |
|---|---|
| `TopicEditorialRubric` | Frozen audience, task, language, invariants, original brief identity. |
| `TopicOpportunityInventory` | Source-linked opportunities and truthful dispositions. |
| `TopicValueAssessment` | Viewer reason, delivered value, opening/focus judgment, evidence and uncertainty. |
| `TopicDependencyAssessment` | Consequential source relationships and unresolved obligations. |
| `TopicPortfolioAssessment` | Representation of opportunities, redundant cores, comparative selection rationale. |
| `TopicRepairPatch` | Exact base identity, authorized changes, findings, replacements. |
| `TopicRenderedEditorialReview` | Actual observed asset identity, modality, source/clip time evidence, findings. |
| `TopicQualityEvaluation` | Dataset/source split, run revisions, human judgments, costs, outcomes, metrics. |

Start with sidecar artifacts when possible; do not add every field to the initial model-output schema. Shared identity should include source/evidence, rubric, policy/prompt/schema versions, candidate extent/title where visible, route/model identity, and exact input artifacts. Reuse the existing artifact and paid-response provenance machinery.

Version public changes deliberately. For example, a new `standalone-topics/2` policy may select extended review contracts while `/1` retains old readers and behavior. This is a proposed naming direction: confirm the existing version registry and replay strategy before choosing final identifiers. Never mutate persisted `/1` meaning in place.

| Current seam | Change responsibility |
|---|---|
| `packages/contracts/src/topics.ts` | Preserve inclusive source spans and independent candidate semantics; add versioned candidate/patch structures only as needed. |
| `packages/contracts/src/topic-runtime.ts` | Extend or version review/assessment contracts, keeping existing artifacts readable. |
| `harness/topic_editorial.py` | Shared rubric, value judgment, opportunity/source review, structured repair instructions, grounding, cache identity. |
| `harness/topic_activities.py` | Load/persist new artifacts; bind exact input identities; aggregate diagnostics; retain valid state on rejected patches. |
| `harness/topic_workflow.py` | Wire the additional decisions into the existing durable program, preserve accounting, bounded repair, and truthful partial outcomes. |
| `harness/topic_compiler.py` and `harness/evidence.py` | Feasible safe-cut discovery without weakening membership or timing semantics. |
| `harness/topic_review.py` and `apps/web/app/actions/topics.ts` | Separate versioned human editorial patches from acceptance commands. |
| `harness/topic_render.py` | Attach actual-media review to the exact rendered revision; reuse unchanged assets. |
| `evals/` | Add standalone-topic evaluation alongside navigation metrics, not as an incompatible replacement. |

Use the existing module boundaries where feasible. A small new `evals/topics.py` or a pure patch-validator module may be appropriate; the plan does not require a new file per conceptual artifact.

## 13. Evaluate publication quality, not merely schema validity

### Three distinct test layers

**Deterministic contract tests** establish identity, source membership, patch scope, cut safety, review invalidation, and accounting behavior. Mocked model output is appropriate here.

**Editorial diagnostic tests** exercise real models with both sound and deliberately defective selections. Mocked judges cannot establish that actual models understand a missing answer.

**Full-recording evaluations** run the complete current program, render candidates, and collect blinded human decisions. These establish whether the product delivers useful uploads.

A passing contract test is not a passing editorial evaluation. Keep counts and conclusions separate.

### Build the evaluation set

Use the retained Karma case for development and regression, not a blind generalization claim. Select fresh full recordings before further prompt tuning. Include relevant languages, code-switching, specialist and general audiences, interviews, explanations, debates, stories, overlapping speech, long-distance qualifications, and visually dependent passages.

Split by recording; avoid tuning and evaluating on adjacent excerpts from the same episode. Where practical, hold out shows/speakers too. Choose an initial cohort that can actually receive full human review; a small diagnostic cohort is not evidence of broad reliability. Expand the held-out set as the product claims broaden.

Keep source recordings and private transcripts in authorized artifact storage. Commit only permitted fixtures and metadata. The trial journal is not a replacement for the exact raw evidence.

### Human labels

A source-aware editor identifies worthwhile opportunities and mandatory setup/core/completion spans independently of the model's proposed list. Permit alternative legitimate selections and acceptable boundary windows; there is no requirement for one exact timestamp or one exact title.

Separate judgments of the final candidate:

- A cold viewer, representative of the rubric, sees the video and title without the original episode, model/provider identity, or author rationale.
- A source-aware editor checks fidelity, dependencies, missed opportunities, and duplication against the original recording.

Use independent labels for at least a meaningful subset and retain disagreement rather than forcing a single unexplained gold answer. An opportunity inventory produced by another model is a diagnostic aid, not human ground truth.

### Essential metrics

| Metric | Definition and interpretation |
|---|---|
| Unassisted publish-ready precision | Human-accepted without editorial changes / all candidates presented as ready for review. Report title-only and boundary/content fixes separately. |
| Useful-opportunity recall | Independently labeled worthwhile opportunities represented by at least one acceptable output / all such opportunities. Match by purpose and mandatory content, not exact timestamps. |
| Coverage completion | Fraction of required source analysis/review actually completed, with explicit pending/failure counts. Not the fraction of source duration exported. |
| Semantic false-pass rate | Human-confirmed incompleteness or distortion among model-approved candidates. Break down missing setup, qualification, ending, and attribution. |
| False rejection rate | Human-acceptable candidates wrongly discarded by the harness; use an adjudicated sample of rejects. |
| Repair success and regression | Grounded failures genuinely resolved, and previously satisfactory properties damaged, per attempted repair. |
| Rendered defect rate | Heard/seen boundary or content defects among inspected actual exports. |
| Redundant-core rate | Outputs judged to repeat the same main value unnecessarily; exclude justified context reuse. |
| Human effort | Active editorial work per accepted video and per episode, with source review effort tracked separately. |
| Resource efficiency | Actual total spend, failed-attempt cost, latency, and media processing per acceptable output/episode. Unknown charges stay unknown. |

When no candidate is produced, precision is undefined rather than 100%. Recall may be zero when useful opportunities exist. When the source genuinely contains no useful standalone discussion, recall is not applicable; evaluate the refusal against human judgment. Unreviewed outputs remain unreviewed, not inferred failures or successes.

One long output can represent several human topics only when editors agree it is a coherent useful video; coverage alone cannot justify a whole-episode fallback. Use portfolio value and human acceptance to prevent metric gaming.

Report episode-level results, denominators, uncertainty, and disagreement. Clips from one episode are correlated; do not present many clips from a single source as equivalent to many independent recordings.

### Promotion rule

Before evaluating a changed variant, write down the primary improvement being tested and the non-regression requirements. Promote only with measured improvement in useful selection, publishability, or reduced editing effort, without a material fidelity/completeness regression.

Do not invent a universal 90% “perfect” threshold from this audit. Use an initial real baseline to set explicit product gates, sample sizes, and acceptable uncertainty before examining holdout results. Any critical meaning distortion found in the release cohort requires adjudication and a regression case before promotion; zero observed incidents is still not a zero-risk guarantee.

Human confirmation remains the final publication-candidate decision while reviewer calibration is immature.

## 14. Concrete regression cases

These are acceptance specifications. Except where explicitly attributed to the retained journal, the cases are proposed fixtures—not claims about observed production failures.

| ID | Case | Required behavior |
|---|---|---|
| Q01 | Recording begins a selected clip with “For that” but necessary antecedent is outside. | Cold review fails setup; grounded repair includes a coherent antecedent or declines the candidate. Title does not cure it. |
| Q02 | Answer is interesting but the host's question establishes what it answers. | Include the question when necessary; do not always include every preceding question by rule. |
| Q03 | A later qualification changes an earlier apparently complete claim. | Source review finds it; select a faithful coherent extent or decline. |
| Q04 | Speaker finishes by explicitly acknowledging uncertainty. | Treat as potentially complete; do not fabricate a definitive conclusion. |
| Q05 | One complete but routine low-value exchange and one worthwhile discussion. | Distinguish audience value without confusing low value with syntactic incompleteness. |
| Q06 | Technical content for specialists versus the same content for beginners. | All seats use the correct frozen rubric; no cross-rubric review reuse. |
| Q07 | Strong title paired with an incomplete opening. | Content still fails; attractive packaging does not supply absent speech. |
| Q08 | Two candidates share setup but develop different useful cores. | Preserve justified overlap; do not deduplicate by time alone. |
| Q09 | Different titles around the same central discussion. | Flag redundant core; do not count as extra opportunity coverage. |
| Q10 | Valuable discussion intentionally removed from supplied candidates. | Source-level omission audit identifies it with original evidence. |
| Q11 | Necessary context plus a distant correction encloses extensive unrelated material. | Reassess coherence; do not claim expansion alone proves publishability. |
| Q12 | Short complete discussion and long complete discussion. | Judge both without arbitrary duration rejection. |
| Q13 | Failed candidate requires merging with a passed neighbor. | Scoped authority allows the merge; unrelated candidates remain identical. |
| Q14 | Model repair contains foreign sentence IDs. | Aggregate diagnostics, reject patch atomically, preserve prior valid state. |
| Q15 | Review is unknown because required media is unavailable. | No false pass and no unrelated rewrite authorization. |
| Q16 | Feasible safe grid instant exists but sampled candidate points miss it. | Deterministic solver finds a grounded cut without semantic expansion. |
| Q17 | Continuous overlapping speech with no feasible safe cut. | Do not invent silence; retain the physical failure and its review implications. |
| Q18 | Transcript-perfect boundary audibly clips speech in the export. | Actual-media observation catches it; text review alone does not certify it. |
| Q19 | A selected passage depends on a visible demonstration. | Verify actual visuals or escalate unknown; no text-only claim of visual completeness. |
| Q20 | Discussion crosses a long-source processing window. | Reconcile original source extents; no artificial chapter at the window edge. |
| Q21 | Transcript omits a consequential “not” or misattributes a claim. | Targeted source-audio check and versioned evidence correction; never silent semantic rewriting. |
| Q22 | No worthwhile discussion exists, versus execution budget runs out. | Distinct outcomes; neither case manufactures content nor masks unfinished discovery. |
| Q23 | Title-only edit or unchanged candidate after a portfolio patch. | Correct minimal invalidation; no stale judgment or unnecessary media processing. |
| Q24 | Adversarial instruction appears inside source speech. | Treat as source data; do not obey it or alter workflow authority. |

For Q01, use the actual retained raw case only after retrieving it. A synthetic antecedent example is useful for development but is not evidence that the real recording was repaired.

## 15. Implementation order: deliver behavior, not another architecture cycle

### EQ-00 — Establish the baseline and recover the known editorial audition

**Change:** Add an evaluation record for the current complete pipeline and retrieve authorized exact artifacts for the retained missing-setup case. Pin prompt/schema/policy, rubric/defaults, evidence, model route, receipts, and raw output. Do not restart unknown paid operations.

**Deliverable:** A current-workflow diagnostic through reviewer, source assessment, bounded repair, compilation, render, and human inspection—or a precise stage-specific blocker with retained artifacts.

**Done when:** The outcome says whether the defect was detected, repaired, and resolved in the actual video. “Nine schema-valid candidates” is not the exit condition. If retained raw inputs are not yet available, prepare the evaluator and synthetic regression fixtures and proceed with deterministic EQ-01 work; keep the real-case qualification explicitly incomplete rather than blocking all engineering or inventing its result.

### EQ-01 — Shared rubric and exact identity

**Change:** Implement section 4 across contracts, prompt inputs, persisted provenance, cold cache lookup, and retained-response validation.

**Tests:** Q06, Q07, Q15, Q23, Q24 plus current cold-isolation tests.

**Done when:** Audience is consistent without leaking source answers. Old artifacts remain readable.

### EQ-02 — Value judgment using the existing review seats

**Change:** Add evidence-backed viewer purpose/delivered value and opening/focus assessment. Preserve existing hard completeness/fidelity checks. Prefer extending existing model calls over creating new agents.

**Tests:** Q04–Q07, Q11–Q12; real-model paired examples with human labels.

**Done when:** The added assessment helps distinguish acceptable-but-weak selections from strong candidates without rejecting valuable natural explanations or uncertainty. Report extra cost and false rejections.

### EQ-03 — Safe-cut completeness and robust patch recovery

**Change:** Correct feasible physical-candidate discovery when it blocks valid content; aggregate source diagnostics; preserve the last valid proposal. Keep physical and semantic repair types separate.

**Tests:** Q14–Q17, existing rational-time/source-offset/compiler tests.

**Done when:** Valid extents are not needlessly expanded because the candidate inventory missed a safe instant. Invalid repairs do not erase useful work.

### EQ-04 — Explicit opportunities and portfolio accountability

**Change:** Emit source-linked opportunities and add a required missing-opportunity/duplicate-core assessment using the existing source-review seam where possible.

**Tests:** Q08–Q10, Q22; full-source human-labeled opportunities.

**Done when:** Missed useful discussions are visible and recoverable. Unused filler does not become a mandatory export.

### EQ-05 — Dependency-aware alternatives and compound repair

**Change:** Add bounded alternative extents for grounded failures, inspect consequential relationships, and implement authorized merge/split/add/drop patches with precise invalidation.

**Tests:** Q01–Q04, Q11, Q13–Q15, Q23.

**Done when:** The system can recover a coherent standalone discussion without indiscriminate source expansion or destruction of unaffected candidates.

### EQ-06 — Render-bound review and minimal human editing

**Change:** Bind media observations and human adjustments to exact revisions. Add source-sentence-based edits using a separate versioned command. Collect edit effort and reasons.

**Tests:** Q18–Q19, Q21, Q23, stale-revision tests.

**Done when:** An actual clipped opening or missing visible context cannot be hidden by a text pass; users can fix nearly correct outputs without reauthoring everything.

### EQ-07 — Long-source path, only as needed and measured

**Change:** Preserve the direct baseline; add source-linked episode indexing and local original-text review where capacity or real misses justify it. Preflight complete review workloads.

**Tests:** Q03, Q20, Q22; untouched long recordings.

**Done when:** Whole-source processing and distant dependencies work without hidden truncation or window-induced topic splits. Report comparison to the direct path.

### EQ-08 — Held-out qualification and promotion

**Change:** Run the full comparison on untouched complete podcasts with the generic button, consistent route policies, actual exports, blinded viewers, and source-aware editors.

**Done when:** Predeclared quality gates are met with denominators and uncertainty reported. Keep unsuccessful results. Do not promote a variant because it fixes only the familiar development recording.

These increments may be combined into sensible pull requests. They are not a mandate to build every proposed artifact before testing the first improvement. EQ-00 starts now; general infrastructure refactoring remains outside this workstream unless it blocks a valid editorial experiment or corrupts the content being judged.

## 16. Controlled experiments that answer the right questions

| Experiment | Hold fixed | What it can establish |
|---|---|---|
| Shared-rubric review versus current review | Candidate extents, source, model route, other instructions. | Whether inconsistent audience assumptions cause false judgments. |
| Explicit value assessment versus current criteria | Candidate set and audience. | Whether better selection appeal is recognized without worse false rejection. |
| Opportunity inventory/omission audit versus author-only selection | Source, audience, comparable models; report additional calls. | Whether useful-topic recall improves. |
| Dependency/alternative-extent repair versus current repair | Initial proposals and grounded failures. | Whether actual completeness/fidelity recovery improves. |
| Direct versus hierarchical source reasoning | Full recording, rubric, comparable model settings. | Whether the hierarchy improves coverage or capacity without missing context. |
| Chapter-Llama hints versus no hints | Existing author/reviewer program, evidence, rubric. | Whether hints add useful opportunities or reduce total cost; not whether their chapter starts look neat. |
| PySceneDetect versus alternate/no visual preferences | Accepted semantic spans, source, output profile, acoustic evidence. | Whether physical choices improve heard/seen boundaries, without conflating semantic changes. |

Maintain two experimental modes: **fixed-candidate** evaluation to isolate reviewer/repair effects, and **end-to-end** evaluation to assess discovery and final portfolio quality. A win in one does not automatically imply a win in the other.

Do not convert provider transport success into an editorial-quality win. A cached author response is useful for matched diagnostics but must retain its original identity; do not relabel it as a fresh production inference by a new route.

## 17. Agent handoff and repository commands

### Instructions for the implementing coding agent

Work from the existing standalone-topic program, not the legacy navigation partition lane. Read the existing focused plan and current implementation before modifying files. Verify the current branch against this archive's references; line numbers below describe the supplied snapshot, not future HEAD.

Begin with EQ-00 and EQ-01. Add contract and behavior tests before changing semantics. Implement the smallest change that answers the observed quality problem. Preserve unknown-outcome accounting, reserved review identity, source membership, revision binding, and old contract readers. Never compensate for an invalid model output by guessing source IDs or silently rewriting source content.

New schemas belong in `packages/contracts`; regenerate Python rather than editing generated models. Wire the changed contract through prompt creation, activity admission, workflow control, persistence, cache keys, UI state, and evaluation. A prompt-only change is incomplete when the old schema cannot express the new decision.

No new model provider, paid evaluation, production deployment, persistent data mutation, or automatic publication is authorized merely by this document. Use the owner's existing authorized environments and spending configuration. Missing recordings, receipts, or credentials must be reported as specific missing inputs, not fabricated.

For each increment, report changed behavior, exact source files, deterministic tests run, real-model/media tests run, remaining limitations, and the evidence for promotion. Explicitly separate mocked tests from actual editorial evaluation.

### Existing commands verified in this snapshot

Run from the repository root in the project's configured environment:

```bash
# After changing Zod contracts:
pnpm --filter @temnia/contracts schemas
pnpm --filter @temnia/pipeline contracts

# Check schema/model drift:
pnpm --filter @temnia/contracts schemas:check
pnpm --filter @temnia/pipeline contracts:check

# Existing deterministic topical tests (not live quality evaluation):
cd apps/pipeline
uv run --frozen pytest \
  tests/test_topic_editorial.py \
  tests/test_topic_compiler.py \
  tests/test_topic_proposal_recovery.py \
  tests/test_topic_workflow.py \
  tests/test_topic_review.py
cd ../..

# Normal repository validation; inspect its environment/resource requirements first:
pnpm ci:local
```

These commands are read from existing package scripts. They were not executed as part of producing this plan. The pipeline specifies Python `>=3.13,<3.14`; do not import the earlier generic guide's Python 3.12 environment into this repository. [E13]

There is no claim here that a new `temnia-eval topics` subcommand already exists. Implement and document an actual evaluator entry point before using one in agent instructions or acceptance records.

## 18. Definition of done for this workstream

A completed implementation must demonstrate all of the following on its declared evaluation cohort:

- The generic workflow finds worthwhile independent discussions without hand-crafted episode-specific instructions.
- Reviewers use the same audience and distinguish understandable content from content with meaningful viewer value.
- Required questions, antecedents, answers, qualifications, and conclusions are retained or the candidate is honestly declined.
- The output portfolio has measured opportunity coverage and justified distinctness, not merely a set of individually valid clips.
- Grounded defects can be repaired without unauthorized rewrites, invented evidence, or loss of prior valid work.
- Actual exported videos—not just transcripts—receive the appropriate listening/viewing judgment.
- Human edits and acceptance bind to the exact source, evidence, title, rubric, and media revision evaluated.
- Performance is reported with denominators, failures, unknowns, human editing effort, and actual cost; no fabricated quality score or presumed audience success.

**The target is an editor that makes good choices and corrects its mistakes—not a larger collection of agents that can explain why a technically valid clip might be good.**

---

## Appendix A. Source evidence index

Line ranges below refer to the supplied archive. The optional companion HTML contains the cited excerpts and full-file SHA-256 values. The source filenames and locations here are sufficient to navigate inside the repository even without that HTML.

| Ref | Repository path and snapshot lines | Supports |
|---|---|---|
| E01 | `apps/pipeline/src/temnia_pipeline/harness/topic_editorial.py:30–173` | Program IDs, goal, review-family reservation, cold cache content, full-source author prompt, repair instructions. |
| E02 | `apps/pipeline/src/temnia_pipeline/harness/topic_editorial.py:176–241` | Cold isolation/general audience, source-review scope, current criteria, transcript-only modality. |
| E03 | `apps/pipeline/src/temnia_pipeline/harness/topic_editorial.py:244–385` | Evidence grounding, assessment scope, unknown/failure distinction, candidate protection and physical-only repair. |
| E04 | `packages/contracts/src/topics.ts:1–121` | Existing candidate span fields and independent compiled portfolio. |
| E05 | `packages/contracts/src/topic-runtime.ts:9–93` | Review/assessment schema and exact criteria. |
| E06 | `apps/pipeline/src/temnia_pipeline/harness/topic_activities.py:218–393, 447–525` | Prompt calls, audience routing gap, exact paid-response binding, initial-versus-repair admission. |
| E07 | `apps/pipeline/src/temnia_pipeline/harness/topic_workflow.py:242–519` | Actual pipeline sequence, optional hints, repair loop, per-candidate cold review, source review and compilation. |
| E08 | `docs/plans/standalone-topic-intelligence.md`; `docs/plans/editorial-research-deferred-backlog.md` | Existing product constraints, intended evaluation, and intentionally deferred broad engineering work. |
| E09 | `docs/log/2026-09-11.md:216–246` | Retained author-only trial, missing antecedent, weak ending, absent downstream qualification, private artifact limitation. |
| E10 | `apps/pipeline/src/temnia_pipeline/harness/topic_compiler.py:75–270` | Source extent validation, exact membership windows, per-video execution and candidate filtering. |
| E11 | `apps/pipeline/src/temnia_pipeline/harness/evidence.py:355–444` | Pause, sentence, speaker, layer and visual candidate generation. |
| E12 | `apps/pipeline/src/temnia_pipeline/harness/topic_review.py:72–122` | Current human command admission and decision-only mutation. |
| E13 | `packages/contracts/package.json`; `apps/pipeline/package.json`; `apps/pipeline/pyproject.toml:1–63`; root `package.json` | Schema owner/generation, existing validation commands, repository environment. |
| E14 | `apps/pipeline/tests/test_topic_editorial.py:115–358`; `apps/pipeline/tests/test_topic_compiler.py:74–275` | Existing tests to extend rather than replace; test presence is not an execution result. |

## Appendix B. Initial evaluation record outline

This is a proposed outline, not a supported existing wire contract. Nullable measurements remain null until actually observed; no scores are prefilled.

```json
{
  "format": "proposed-topic-quality-evaluation/1",
  "datasetSplit": "development",
  "sourceEvidenceSha256": null,
  "rubricSha256": null,
  "programVersion": null,
  "routeSnapshotSha256": null,
  "initialProposalArtifact": null,
  "finalProposalArtifact": null,
  "renderedArtifacts": [],
  "opportunityLabels": [],
  "candidateJudgments": [],
  "repairEvents": [],
  "executionCompletion": "not_run",
  "unknownProviderOutcomes": [],
  "measurements": {
    "publishReadyAccepted": null,
    "publishReadyReviewed": null,
    "worthwhileOpportunitiesRepresented": null,
    "worthwhileOpportunitiesLabeled": null,
    "semanticFalsePasses": null,
    "renderedDefects": null,
    "humanEditingSeconds": null,
    "settledCostMicros": null
  }
}
```

Store per-candidate decisions and their source evidence alongside aggregates. The record must never imply that absent labels, unreviewed media, or unfinished provider outcomes were successful evaluations.
