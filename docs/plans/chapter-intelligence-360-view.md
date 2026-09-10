# Chapter intelligence and default creation

Status: implemented; combined release and live qualification in progress, 2026-09-10.
Rajesh requested automatic editorial
intelligence, chapter creation without writing a prompt, and the pending
Chapter-Llama integration. This plan precedes implementation. The four-minute
recording is a regression source, not a held-out quality benchmark.

## Observed problem and outcome

The current pipeline produced playable chapters but retained an unfinished
question at an artificially cropped source end. Human feedback and an
operator-specified sentence partition supplied the correction. Seventeen media
checks per chapter verified construction, not editorial completeness. The
text-only verifier did not receive explicit compiled boundary context initially,
and its editorial findings did not trigger a correction.

The intended flow is one click: identify logical topics, preserve complete
spoken thoughts and question/answer context, explicitly account for justified
drops, assess the compiled edit, automatically repair grounded defects, and
render the resulting chapters for human acceptance. Custom instructions remain
optional. Human approval is never fabricated by an automatic repair.

## Default chapter creation

- Add one server-owned versioned generic brief. The normal Create chapters
  button requires no typing. An existing registry Button reveals optional
  instructions; Button, Label, Textarea and Input remain the UI primitives.
- The default asks for meaningful topic changes without a fixed chapter count,
  source language, context and complete thoughts, grounded informative titles,
  and explicit justified drops. It explicitly addresses incomplete source
  openings/endings without inventing missing content. It contains no source IDs
  or desired timestamps.
- Resolve the default consistently in start and pending-status actions. Freeze
  the default version in a new browser intent before dispatch. Missing or new
  versioned blank instructions select the default; custom bytes are preserved.
  Preserve the identity of old unversioned pending requests, including explicit
  empty strings. Reject unknown default versions rather than silently changing
  an intent after a deployment.
- Keep the configured budget prefilled and editable, existing request UUIDs,
  concurrent-start conflicts, cancellation, retry and source/run history.
- Verify default and custom paths in backend identity tests and the production
  image Playwright journey, including reload/pending re-entry.

## Editorial program

- Inspection found an avoidable mandatory hierarchy: even a full transcript that fits the
  qualified proposer was split into 80-sentence summary windows. New-policy planning first
  admits the complete transcript, counting the compact output wrapper and optional topic-hint
  bytes against the existing route limits. If it does not fit, keep the bounded hierarchy.
  This compares full source context with lossy extra summary calls; fewer calls is structural,
  while any quality benefit still requires measurement. Old policies retain their windowing.

- Preserve existing prompts, model schemas, saved artifacts and old workflows.
  A new policy is frozen when a run is first created; old runs retain legacy
  behavior even when a new continuation workflow resumes them. A workflow patch
  selects new activity sequences. Additive persistence must not change old
  request/config hashes. Replay retained real histories before release.
  Store the policy in the existing immutable route-snapshot wrapper beside
  pinned source/transcript identities, atomically with first run creation.
  Missing policy means legacy; do not add a default to the public run config
  or migrate old request hashes. This avoids a new DDL column for existing
  immutable metadata. An internal versioned start request supplies the policy.
- Add a versioned private editorial assessment: typed defect codes, affected
  section/boundary IDs, supporting source sentence/word IDs, explanation and
  repairable versus instruction-required disposition. Validate all identities
  and affected ranges against the immutable evidence and compiled edit before
  accepting a finding. No model-authored milliseconds.
- Give the critic the actual compiled edit, original brief, source edges,
  nearby lexical timing/confidence, candidate reasons and speech evidence.
  Include complete global topic context when it fits; explicitly bound larger
  requests without dropping source coverage or pretending partial inspection is
  complete. Text assessment must not claim listening or watching.
- Specify an editorial rubric covering coherent topics, complete openings and
  endings, setup/question retention, unsupported titles and cut-risk evidence.
  A keep-all instruction conflicting with a truncated source is an explicit
  instruction conflict, not permission to silently drop content.
- Run an independent editorial assessment after compilation and before
  committing/rendering the initial revision. Repair validated defects through
  the existing metered model activity and finite planning-repair accounting,
  then compile and assess again. Preserve raw proposals and findings. Keep the
  eligible generator distinct from the critic; unnecessary family rotation
  must not consume the only independent verifier.
- Restrict a semantic replacement to its grounded affected scope. Compare
  candidates and refuse unchanged or repeated proposals before buying another
  iteration. Timing corrections select addressable candidates with deterministic
  compiler constraints; repeating an identical semantic proposal is not a
  timing repair. Unknown external outcomes stop dispatch and retain exposure.
- Publish only the final automatically assessed candidate as initial review
  revision; automatic work does not impersonate a human mutation. If bounded
  repair cannot resolve a finding, retain a concrete diagnostic and a
  reviewable result where safe. No technical failure becomes an editorial pass.
- Give post-render verification the same compiled context and retain its
  modality and findings. A new post-render content revision, if required,
  must use an owned system CAS rather than forged user review events.
- Source edges receive risk assessment too. Independent speech evidence must
  be bound to the selected media timeline and exact input bytes; unknown
  coverage remains unknown. Reuse the existing pinned Silero/PCM implementation
  where applicable rather than inventing a punctuation or energy heuristic.

Implementation evidence: on the retained 43:56 source, the same qualified proposer admits the
complete 579-sentence/6,203-word transcript in an 87,718-byte compact request. Planning changes
from eight summary windows to one direct request, eliminating eight summary calls for that input.
This is an offline admission measurement with zero provider calls, not a chapter-quality score.
Four retained production histories (77, 191, 361 and 217 events) replayed successfully. Real
Postgres/Temporal tests cover saved-response verification and reuse, scoped pre-render repair
publication, and rejection of tampered verdicts, families, edit/render identities and reports.

## Chapter-Llama

Integrate the official ASR-only Llama-3.1-8B plus Chapter-Llama LoRA as a runnable
candidate, retaining the current compiler and evaluation grid. It supplies
topic suggestions; it does not replace speech-cut assessment. Compare it with
the existing SaT/MiniLM/KernelCPD candidate and gateway planner before promotion.

Use the pinned official code/model identities and direct Transformers/PEFT
loader, excluding the upstream training stack. Prefetch base, tokenizer and
adapter snapshots; runtime never resolves a mutable model revision or silently
truncates over-context input. Preserve raw timestamp/title predictions and
their deterministic mapping to addressable sentences, model identity, tokens,
elapsed time and failure outcome. Reject malformed, duplicate, unordered and
out-of-range predictions instead of silently cleaning them.

Add an explicit segmenter/evaluation choice with recorded fixtures and a real
inference path. For remote GPU audition, use an isolated Modal app, authenticated
transport and immutable admission/checkpoint identity before inference. Never
hide a remote model call inside retriable synchronous evidence construction.
Connect any production candidate consumption through a durable metered
activity and immutable artifact lineage. No default routing promotion is
inferred from successful loading or transport.

Probe existing Hugging Face access before claiming a blocker. The base model is
gated; a license/account action cannot be bypassed. Start with a measured GPU
profile sufficient for the unquantized model and compare cheaper configurations
only after the first recorded run. Keep model weights/CUDA out of the CPU worker.

## Verification and release

The existing gateway qualifier was found to be fixed to the older summary/proposal/v1-verdict
suite. Add an explicit editorial suite for the exact v2 assessment and v1 repair request shapes,
preserving legacy suite identities, request journals, retained responses, cost reconciliation
and halt-on-unknown behavior. Run synthetic qualification across the three frozen route families
before claiming the new request shapes are qualified. Monkeypatching the older stage registry
would bypass the qualifier's identity checks and is not acceptable evidence.

The first live editorial suite exposed genuine reference-contract misuse: one assessment
attached a source-end fragment to both chapters, another mixed a candidate ID into compiled
boundary IDs, and a structural repair supplied an invalid source-edge self-transition. Add
explicit compiled-boundary ownership and allowed-reference guidance to the generic request,
require the specific source-fragment code for an incomplete kept source edge, and make clear
that sentence-range repairs normally leave timing overrides empty. Keep validators strict;
never coerce these responses into success. Version the changed prompts and preserve read-only
reconciliation of retained v1 qualification journals. Repeat the exact new request shapes
across the three families before the default-brief real-video run. This is contract qualification,
not a seat-quality audition or permission to promote a vendor.

The v2 follow-up completed six calls with valid strict schemas and grounded references. GLM
identified the invented tail as repairable, Kimi identified it but requested instructions, and
DeepSeek missed it. All three independent repair requests removed only the invented fragment.
The two suites cost $0.20216064 reported by the gateway ($0.202165 after per-call microdollar
rounding), with no retries or unresolved outcomes. These results qualify the request contracts
and expose a semantic miss; they do not establish general editorial accuracy.

The first real default-brief workflow completed its paid stages and rendered revision one,
but portable evaluation export rejected the retained v2 `editorial` field because its typed
verification body still accepted only the legacy projection. Extend that reader with the
typed optional editorial payload and validate its consistency with the compatibility verdict;
preserve the old serialized shape when the field is absent. Add a regression using an actual
v2 verification artifact and a legacy roundtrip. Recover the completed run's existing artifacts
without rerunning its paid workflow. A review status alone is not a quality acceptance.

The saved short-run audit found a further concrete weakness: the final critic invented a
continuous speech interval across a cut that actually lies in a 2.852-second detector gap,
while a different shared cut really crosses measured speech. The repair request also consumed
72,637 input tokens for only four minutes. Move timing arithmetic into deterministic per-cut
facts (exact chosen time, intersecting word/speech intervals, local uncertainty, and the measured
gap). Global detector disagreement is not proof of risk at every cut. Give models these facts
and validate new timing-uncertainty findings against local evidence; retain the old reference-only
validation mode when reading historical accepted artifacts, including the recorded false claim.

Compact the model context without dropping global source meaning: retain every sentence's text,
identity, time and endpoint anchors, detailed words near cuts/findings, and original proposal
fields needed for exact preservation. Assessments do not choose candidate IDs and need no full
candidate catalogue. Repairs receive local candidate options only for explicit timing repair;
structural sentence-range changes use the compiler. Explain that a measured-risk internal cut
can be removed by a coherent regrouping/merge that preserves content, rather than merely moved
within the same speech interval. Keep real unknown evidence and brief conflicts reviewable.

Version these prompts as v3 and retain reconciliation of both earlier qualification generations.
Measure payload/token reduction on the saved short run, test the real clear-tail/risky-internal-cut
facts, missing detector/uncertain words, exact endpoint behavior, unchanged historical artifact
reading, full global text coverage and candidate locality. Qualify the new request bytes, then
use a fresh default-brief run; preserve the completed first run, its files and all five expenses.

Independent review of v3 found two concrete follow-ups before paid qualification. First, timing
options must be judged after the compiler's exact frame/sample quantization, retaining the raw
candidate ID separately; a raw gap can round into speech. Test raw-safe/quantized-unsafe rejection
on the retained evidence and a small rational-grid fixture.
V3 repair validation also requires every explicit candidate choice to appear in the supplied
local timing options; a real but unshown candidate does not gain authority through its ID.
Keep that stricter rule opt-in for older retained repair responses, selected by the accepted
prompt generation, and test an omitted candidate and an empty supplied list.

Second, freeze the selected editorial prompt version in the internal `VerificationPlan` returned
by preparation. Workflow model dispatch uses that returned version; old editorial plans without
the field resolve to the v2 prompt generation used by the completed prototype workflow, while
legacy non-editorial verification keeps its existing version. Both assessment and repair, and
all post-render review paths, use this dispatch identity. Finalizers validate the exact accepted
response and its supported prompt generation before applying generation-specific grounding:
v3 requires local timing evidence, earlier recorded generations retain their original reference
rules. Do not reinterpret historical model judgments or change their hashes. Verify completed
v2 history replay, saved-response finalization/reuse, current v3 strict refusal and foreign-version
rejection. This is an internal durable compatibility seam, not a public request/config change.

The live v3 repair probe exposed a conflicting instruction emphasis: DeepSeek correctly removed
the final fragment but added endpoint quotes to an unaffected section whose original quote array
was empty. The scope validator refused the response. Clarify the repair prompt so newly selected
endpoint anchors apply only to changed sections; unaffected fields, including empty quote arrays,
must be copied verbatim. Retain the validator and raw rejected response. Advance only the changed
repair prompt to v4; assessment remains v3. Qualification generation 4 records that exact pair and
keeps read-only reconciliation of generations 1–3. The durable version resolver accepts the new
repair identity with the existing strict local-evidence/supplied-option rules. Requalify the full
current request pair before the fresh default-brief workflow; this changes no source-specific cuts.

Qualification and audition gates remain distinct. Generation 4 fixed DeepSeek's repair, and the
selected GLM critic identified the fragment. GLM's separate repair probe instead omitted the
explicit drop section; that non-selected role remains a recorded grounding failure. Do not keep
changing a shared prompt until every model passes every role on one fixture. For the fresh
audition, require the complete six-call native-schema/privacy/route/cost record with no unknown
outcomes, and grounded successes for the exact selected repair and critic routes. Preserve the
whole report's real failed/passed statuses and all non-selected-role failures; do not call this a
six-of-six grounding pass or promote those roles. Production source/repair validators remain
unchanged and continue to reject invalid actual outputs. A real-video result is the next quality
measurement, not a consequence of a transport-only status.

### Real-run follow-up: preserve existing risk without authorizing a new risky cut

The fresh default run found a repairable source tail and returned a valid tail-only repair, but
compilation refused `constrained candidate intersects grounded speech`. The existing 126.600-second
cut intersects detector interval 124.322–126.910 seconds. Retaining that untouched cut was incorrectly
treated as requesting a new safe timing override. No repair was applied; the four-call run and its
original outputs remain recorded, not rewritten as a success.

Separate two compiler inputs: new explicit candidate choices retain all current local/speech checks;
preserved cuts are derived from an accepted prior proposal/edit pair and keep their exact rational
time and review reasons. Only a sentence transition present in both proposals can be preserved,
and an explicit override cannot claim preservation. Validate prior source/evidence/section lineage,
candidate identity and source grid; retain risk flags and require the joint monotonic/exact-cover
path. This permits a partial editorial repair while the next critic still sees unresolved risk.
It does not declare the old cut safe, remove content or choose new source-specific timestamps.

The activity loads the prior proposal/edit through scoped, hash-checked artifact reads, validates
their linked identities and includes both immutable references in the new proposal/edit operation
fingerprints and dependencies. The new compiler mode has its own version. Optional internal request
fields are omitted when absent, preserving old wire/artifact identities. A new Temporal patch freezes
whether repair compilation uses this preservation policy; old saved repair requests keep the old
policy, including their original successful results and known refusals. No public schema or model
prompt changes are needed, so the selected-role request qualification remains applicable.

Regression cases: valid tail repair with an untouched risky cut; exact retained time/reasons;
new risky override still refused; unrelated/stale/foreign prior artifacts; wrong transition, candidate
or evidence identity; impossible joint path; source-edge protection; unchanged historical compilation
and replay. Validate the failed real repair offline against retained inputs, then use a fresh default
workflow and keep the four-call failed-to-repair attempt/costs intact. Do not manually apply its cut
list to the live run. The longer audition remains held until the short automatic path is demonstrated.

Cases: incomplete source opening/end, complete final sentence retained, internal
cut with unchanged semantic ranges, brief conflict, unknown/missing speech
evidence, empty/audio-only/non-English/long input, foreign finding IDs,
unrelated changes, no-op/oscillating repairs, duplicate/cancelled/restarted
activities, stale ownership, budget exhaustion and unresolved provider outcome.
Verify unchanged-media reuse, explicit drops and their required human acceptance.

Use recorded contracts in CI, actual old-history replay, focused DB/Temporal
tests, UI e2e, then the full exact-commit local gate and verified PR workflow.
No open related PR existed at planning time; Rajesh merges. Additive database
changes deploy through the web migration release phase before dependent worker
code. Preserve older staging runs and their expenses; do not retry unknown work.

Live qualification must use the default brief on the known bad excerpt without
operator-provided cut IDs, then representative long material. Record first-pass
and repaired quality separately, automatic repairs, missed broken speech,
review time and model/GPU/render/storage costs where measured. No claim of M1
completion until the three-source human acceptance gate is actually measured.
The fresh v3 short/long auditions allow three automatic repairs and a $10 per-run budget,
using existing supported configuration so one provisional repair does not stop an otherwise
recoverable result. Rajesh authorized increasing limits while reaching a working result.
No session-wide spending stop is introduced; cycle detection and unresolved-outcome accounting
remain active. Report the changed allowance when comparing the earlier one-repair run.

## Research basis and alternatives

- Compared one-pass richer prompting with a separate grounded critic/repair
  program. The observed silent incomplete ending justifies explicit independent
  assessment; measure its additional calls rather than assuming it is cheaper.
- Compared repairing before rendering with repeated post-render human edits.
  Pre-render repair reuses existing revision-zero ownership and avoids encoding
  a known-bad candidate. It does not prove audiovisual quality.
- [Temporal patching and replay](https://docs.temporal.io/develop/python/workflows/versioning)
  and [PydanticAI Temporal durability](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/)
  were rechecked September 10; existing pinned runtime remains.
- [Official Chapter-Llama code](https://github.com/lucas-ventura/chapter-llama/tree/d19a77efcf583f63771de052267fdeea016510e9),
  [model card](https://huggingface.co/lucas-ventura/chapter-llama),
  [CVPR paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Ventura_Chapter-Llama_Efficient_Chaptering_in_Hour-Long_Videos_with_LLMs_CVPR_2025_paper.pdf),
  [PEFT loader](https://huggingface.co/docs/peft/main/en/package_reference/peft_model).
  The paper's aggregate score is not an ASR-only Temnia result. No legacy
  implementation is being ported; its wrong-boundary failure motivates measuring
  actual editorial acceptance instead of infrastructure completion.
