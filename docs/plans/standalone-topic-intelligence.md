# Focused plan: standalone topic intelligence

September 10, 2026. Proposed implementation sequence following the
[research and code audit](../design/standalone-topic-intelligence-2026-09-10.md).
The user-approved output goal and permission to reuse context are binding. The implementation
choices below are recommendations to test, not a claim that the architecture has already shipped.

## Product contract

The generic action finds **interesting, independently publishable topic videos** in the uploaded
recording. Each candidate must communicate a coherent purpose and complete its discussion with
enough source context for a new viewer. A question, correction, qualification or story conclusion
that is necessary to preserve meaning belongs in the output. A topic can finish in acknowledged
uncertainty; the system must not manufacture certainty or a sensational hook.

User instructions are optional refinements of audience, topic or treatment. The default does this
editorial work without a custom prompt. The source determines useful count and duration. Context
reuse is permitted; arbitrary duration, output-count or spend caps are not added by this plan.
Actual cost, latency, unresolved outcomes and redundant content remain visible. Unknown provider
outcomes are not retried blindly; accounting correctness is separate from editorial restrictions.

First implementation: independent **contiguous** source intervals in original order. Gaps between
videos and overlapping context across videos are valid. This answers the current scope without
adding montage, reordering, generated narration or a new graph runtime. If a valuable topic cannot
be made coherent as a contiguous interval without unrelated material, record that limitation for
the review instead of claiming a complete result.

## 1. Freeze evidence and judgments before changing the harness

Preserve the current Karma run, original transcript/evidence hashes, prompts, route snapshot,
proposal attempts and output media. Do not reset that run or reinterpret its stored revision.
The [case record](../design/standalone-topic-karma-case-2026-09-10.json) is diagnostic evidence,
not a production edit or human gold. It must never become a source-specific hint in the default
generator prompt.

Add an evaluation record that separates:

- A candidate's source extents and its mandatory setup, core discussion and completion evidence.
- Cold-viewer comprehension, source faithfulness, heard edge quality, title accuracy and topic value.
- Raw proposal, automatic revisions and human decisions, including the actual boundary edit required.
- Set-level missed worthwhile topics and redundant core ideas, distinct from permitted setup reuse.
- Actual model/provider/prompt/schema identities, calls, spend, latency and human review time.

Do not require one exact gold cut. Human annotations can name acceptable boundary windows and
mandatory spans, with disagreement retained. Build the first diagnostic examples from the known
Karma claim/correction split, the earlier unfinished excerpt, and examples that are short but
complete. Mark assistant-created examples as development labels until a human reviews them.

Choose fresh full recordings before prompt optimization. Split at source level; never put adjacent
clips from one episode into both tuning and acceptance sets. Karma is excluded from the blind
generalization claim. Source availability and human labels are prerequisites for that claim, not
reasons to block the first diagnostic implementation.

## 2. Make a comparable editorial execution possible

Reserve a qualified nonauthoring family for review when the run is admitted. Author retries may
use qualified author routes, but cannot consume the reserved reviewer. If no eligible reviewer
exists, retain any candidates as unverified and state that reason; do not mark them publication-ready.
The same route policy must apply to both comparison variants, with every author and critic identity
recorded. Do not silently reclassify previous failed authors as independent critics.

Use sentence-ID spans for semantic decisions. Derive redundant display quotes and media times in
code. Where an evidence field expresses a semantic obligation, validate it against the source and
candidate rather than inventing it after the fact. A malformed reference produces a precise diagnostic
with the offending field; it does not authorize silent semantic repair. This small interface change
belongs in the experimental contract, with the historical baseline retained separately.

Relevant existing seams: `harness/prompts/chapter.py`, `proposal_diagnostics.py`,
`editorial_activities.py`, `editorial_workflow.py`, `routes.py` and the qualification/eval runner.
Confirm current names and call paths when implementing; the research identifies responsibilities,
not a mandate to create one file or model call per step.

## 3. Implement the smallest independent-topic program

Version the new policy and artifacts explicitly. Keep `ChapterProposal` and `ChapterEditSpec`
valid for their historical partition lane. A new topic plan references the same source/evidence
identity but owns an independent first/last sentence extent per candidate and independent compiled
in/out boundaries. It does not weaken old validators to make old malformed partitions valid.

Suggested candidate fields are `id`, `purpose`, `firstSentenceId`, `lastSentenceId`, `coreSpans`,
`requiredContextSpans`, `completionSpans`, `meaningChangingFollowups`, `title` and a grounded reason
for inclusion or refusal. These are conceptual fields to simplify during implementation. Avoid
forcing every discussion into a question/answer template. Code owns time, source membership and
render provenance; model evidence claims remain reviewable.

The minimum sequence is:

1. Read the full transcript and produce a compact source-specific topic inventory with original
   IDs. On longer inputs, preserve global context through the existing hierarchy and retrieve
   original text before finalizing a candidate; summaries alone cannot certify an edge.
2. Propose worthwhile topic spans. Include necessary context; do not export greetings, connective
   remarks or an outro merely because every second needs an owner.
3. Inspect each candidate's opening, ending and surrounding exchange. Resolve backward references,
   unanswered questions, supporting examples and corrections. Merge, expand, reuse context or
   decline as the source warrants. Record a reason for every changed semantic extent.
4. Review cold comprehension from the **actual selected content**, without the source map or the
   generator's rationale. Review source-context faithfulness separately. A good title cannot cure
   speech that starts with an unintelligible referent.
5. Select among complete, faithful candidates for value and distinctness. Required setup may repeat;
   the same core insight repackaged several ways is counted as redundancy.
6. Compile independent physical edges, render, and verify the resulting artifacts. Any movement
   that changes semantic membership invalidates the affected review and requires reassessment.

These are responsibilities in one typed durable program. Shared global work is reused. Independent
candidate work can use existing activity concurrency once its accounting is correct. Do not create
new agent personas, a database of topic graphs or a retrieval service for this first comparison.

The new contract crosses the TypeScript/Python seam, so `packages/contracts` remains its schema
owner and generated Python models remain generated. Source use accounting derives the union of
used intervals, unused intervals and overlap duration. It is separate from individual render plans.
All artifacts retain organization, source, evidence hash, policy version, attempt and revision.

Compatibility and operations remain concrete: old revisions render and replay under old contracts;
new run starts choose a frozen policy; cached model responses are reused only for matching inputs;
uncertain paid outcomes retain their fences. No deployment or gate restart is performed while a
paid diagnostic run is active. This plan changes no credentials or staging configuration by itself.

## 4. Compare causes in a controlled order

The saved Karma run is baseline **H**, historical and unverified editorially. The actual legacy
behavior is baseline **L**, with its code/model/defaults recorded. Historical notes without raw
outputs cannot be scored as though they were a fresh run. Do not force retired legacy models back
into service; if exact execution is unavailable, label the substituted behavior and limitation.

Run the first matched comparison only after both variants have a working independent reviewer:

| Variant | Change | Question answered |
| --- | --- | --- |
| P: standalone prompt, partition output | Explicit standalone instructions, current partition contract | What standalone quality is achievable under the partition constraint? |
| I: independent spans | Same editorial instructions, author/provider, evidence and reviewer; permit independent spans, omissions and overlap | Does removing the representation constraint improve the useful output? |

Keep the compact source inventory, purpose/context/completion evidence fields, quote simplification
and review procedure common to both arms. Any unavoidable schema wording differs only to describe
the permitted output. Record initial proposals and final
reviewed outputs separately. P versus I isolates the contract only to the extent the remaining
inputs and program are equal. H versus either variant is an end-to-end improvement comparison,
not a pure prompt ablation. In particular H had no reviewer and cannot be used to estimate reviewer
accuracy. To attribute a gain specifically to new prompts, add a matched old-prompt control with
the same working reviewer; do not infer that contribution from H alone.

If I improves completeness and useful topic yield, then audition author capability under I across
at least three eligible model families, preserving the critic and evidence. Include capable current
routes, not only the cheapest transport-qualified ones. Read the live gateway roster, qualify
strict output/privacy/actual request shape, then compare editorial quality. No vendor is inherited
as winner. Three author families with one fixed nonauthoring critic require at least four qualified
families in the experiment pool; the current three-family snapshot is insufficient. Qualify that
pool before starting this matrix rather than quietly switching critics between rows. Test critic
capability separately, using fixed candidate inputs. Chapter-Llama is a
later proposal-input ablation on the same completion program; its integration is not a substitute
for that program or its quality gate.

Freeze each comparison's instructions before dispatch. Run the planned alternatives, review the
result, and record what changed before starting another experiment. There is no arbitrary paid-call
ceiling here. Persistence means answering the measured failure, not silently cycling prompts until
one pleasant sample appears. Repetition to assess variance is recorded and never cherry-picked.

## 5. Acceptance and measurements

For a video presented as ready, require zero **observed** critical failures: missing necessary
orientation, unfinished chosen discussion, omitted meaning-changing correction, misleading
packaging or audibly broken edge. “Zero observed” on a small set is not a population guarantee.
Unknown audio/visual evidence is reported as unknown, not passed from text.

Primary output: human-accepted distinct worthwhile topics with **no content-boundary editing**.
Also report the existing M1 category of acceptance with minor adjustment; do not substitute the
looser category for autonomous success. Retain the existing 70% M1 bar as an initial project gate,
not a definition of production quality. Its claim now requires standalone-topic judgments on fresh
sources. Report counts and source-level variation alongside the rate; a small sample is not a
reliable generalization estimate.

Publish one comparison table per source with:

- Proposed and accepted distinct topic counts; critical failures by reason; missed useful topics.
- Redundant core discussions, permitted reused context and unused source duration.
- Boundary-edit time and total reviewer time; cosmetic edits separately.
- All provider calls, repairs, reasoning tokens, known cost and unresolved exposure.
- Elapsed time after ingest and cost per accepted useful video, including rejected work.

The winner must improve useful accepted output without hiding failure by returning fewer or much
broader videos. A near-full-source video is judged for whether it remains one coherent worthwhile
topic; a zero-output run cannot claim perfect precision. Human clip-only review precedes source
review so comprehension is not contaminated by context from the original episode. Keep model,
variant and generated rationale hidden during that first judgment.

Automated critic calibration includes complete and deliberately incomplete variants: missing
question when required, omitted correction, dangling referent, unresolved story and a short but
complete discussion. Report false passes, false rejections and disagreements by failure type.
Different model families and a high aggregate agreement are insufficient substitutes.

## 6. Delivery order and exit conditions

**First deliverable:** the minimal independent-span contract, versioned generic policy, always
provisioned reviewer, case evaluator and real Karma comparison outputs. Prove source grounding,
permitted overlap, independent edges, review invalidation after a changed span and old-artifact
compatibility with focused tests. Test model judgment with actual outputs rather than asserting a
hand-authored expected merge over synthetic sentences. The ordinary repository gate still applies.

**Second deliverable:** the matched model/Chapter-Llama comparisons only where the first result
leaves their contribution uncertain. Keep unsuccessful outputs and their costs. A formatting fix
does not count as an editorial win.

**Promotion deliverable:** unassisted generic-button runs on untouched full recordings, exported
media and blinded human decisions demonstrating the intended quality. The review surface must
show candidate, unverified, needs-review and human-accepted states truthfully, and display exactly
the revision whose media was inspected. No automatic publishing is authorized by this work.

If P and I both fail the same discourse dependency, inspect candidate completion and critic false
passes before changing acoustic thresholds. If a different model improves I under matched inputs,
record model capability as a contributor. If legacy still wins, preserve that outcome and inspect
the missing editorial operation. If fresh sources regress, do not promote based on Karma.

The [deferred backlog](editorial-research-deferred-backlog.md) is deliberately outside this work
unless a defect prevents a valid comparison or corrupts the source/output being judged.
