# OpenRouter transport and topic-model audition

September 11, 2026. This continues the [model audition](topic-model-evaluation-2026-09-11.md)
after Rajesh merged PR #37 and configured the staging OpenRouter key. The
[implementation and experiment plan](../plans/openrouter-model-evaluation.md)
records the alternatives, controls and subsequent declared conditions. The target
remains useful standalone discussion selection, with independent comprehension,
source fidelity and actual playback. Transport qualification alone is not that
measurement.

## Outcome

Three exact routes qualified, then three full-source configurations ran through
the production workflow on the same 43:56 Karma recording. OpenRouter enabled
completed authoring and review, but did not eliminate operational failures or
establish better chapter selection.

| Author / reviewer | Full-source result | Settled model expense |
| --- | --- | --- |
| Astra / Gemini | Author stream reported a network error; no selection or renders; original unknown fence retained | $0; separate matching receipt reports zero, original estimated exposure $5.009620 |
| Kimi / Gemini | Eleven candidates and eleven review videos; patch rejected; final `needs_review` | $1.027687, fourteen calls |
| Gemini / Kimi | Four initial candidates; repair terminated at output length; workflow failed before compilation or rendering | $0.619309, seven calls |

The strongest findings concern discovery, judgment and repair. Gemini omitted a
developed prayer discussion until the source critic found it, and left the earlier
action/stillness answer without an opportunity. Kimi represented more source
discussions but split some setup and qualifications. Both configurations selected
the same wealth story with an unfinished ending. Source and individual critics
disagreed on concrete context requirements. Kimi's actual repair exposed a mismatch
between the opportunity fields the prompt presents and those the validator allows
to change. Gemini's larger real repair exhausted its declared output allowance
despite passing small native-stage qualification.

These are source-grounded development diagnoses, not human precision/recall scores.
Both native comparisons retain missing human metrics and report no winner. The
eleven videos are available for review; technical media checks do not establish
publication readiness. All trial workers have stopped without replaying unknowns.

## What changed

The existing paid activity now supports an explicitly configured OpenRouter
transport. It consumes streaming internally, persists an observed generation ID
before content when available, drains the response and retains its original SDK
representation. Complete but truncated or invalid output remains a paid failure;
incomplete output retains an unknown fence. Existing operation ownership, immutable
artifacts, finite Temporal workflows, source/compiler/media boundaries and reviewer
family separation remain the foundations.

Frozen routes carry gateway, mode, deadlines and accounting provider. Transport
version 2 also carries the endpoint's output-token parameter and a separately
declared canonical accounting model. Request aliases remain request identities.
Exact native requests, full response bytes and original generation-accounting
receipts are bound by qualification `/4`; earlier proof formats retain their
interpretation. Credentials are selected by gateway and remain process-only.
Gateway/mode/deadlines enter shared evaluation factors; provider-specific encoding
and model/accounting identity stay in complete author/reviewer route factors.

The change adds no dependency, database migration, durable runtime, model prompt,
native editorial schema, detector or user-interface flow. The four existing v2
editorial stages remain the same programme. Response caching and silent provider
fallback are disabled in these trials.

## First declared cohort: endpoint admission

The first condition used Astra/Azure, Opus/Amazon Bedrock, Gemini/Vertex global and
Kimi/Fireworks, each at high effort and 32,768 output tokens. Streaming had a
300-second idle timeout and 540-second aggregate deadline inside the existing
600-second model activity. There were 16 planned requests and $19.151153 of
conservative admission exposure; this was not a charge or editorial count limit.

The running PR #37 pipeline key was verified by presence in saved configuration,
desired service configuration and the actual running container. The isolated
image preserved all 112 installed distributions and the PR #37 base filesystem
layers. Its 161 Python files and four prompt/native-schema records matched the
actual local production image. First-condition implementation fingerprint:
`ed627ea062fd8e8e436934ce4b3729a98a76faf5d58397e7a1ebdf3b9b7d14b4`.
Server image: `sha256:d1ad9352f94de0b86bfb06eee0eae655f4a4cb0adb964306f5b45db9b48c4a54`.

The cohort stopped on its fifth request at 17:45:18 UTC:

| Route/stage | Observed outcome | Interpretation |
| --- | --- | --- |
| Astra/Azure, all four stages | Four complete HTTP 404 parameter-admission rejections; no generation ID | No inference output or timeout was observed. The captured endpoint advertises `max_completion_tokens`, but these requests used `max_tokens`. |
| Opus/Bedrock author | Complete streamed response; matching generation/provider but canonical accounting model differed from the request alias | The original journal stopped with `identity_failed`. Independently replaying its text through the production contract rejects prose as invalid JSON. |
| Opus remaining stages, all Gemini/Kimi stages | Eleven unstarted slots | No results, failures or expenses are invented for these slots. |

Opus's early generation handle was durably observed after 1.925 seconds; its complete
response was saved after 20.014 seconds and the identity failure recorded after
30.389 seconds. Four accounting lookups returned 404 before a fifth returned the
receipt. This demonstrates early identity capture on one small synthetic request;
it does not establish full-source reliability or gateway superiority.

The receipt reports **$0.040225**, non-BYOK, 1,490 prompt tokens and 1,311 completion
tokens, including 363 reasoning tokens. The original journal retains **zero settled
micros** and **992,173 micros of estimated exposure** because its declared accounting
identity did not match. Those are distinct facts, not a zero-cost claim. The original
journal/report is unchanged at SHA-256
`e940c6b3c7621acc9225f55f9f7b6849e09eb53a1225e99732891d377effbf52`.
All ten response/error/accounting references were independently rehashed.

## What the live result changed

The first adapter generalized one output-token spelling across OpenRouter. The
captured endpoint records contradict that assumption: Azure advertises
`max_completion_tokens`; the three other chosen providers advertise `max_tokens`.
Current [OpenRouter chat documentation](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
defines both fields, while provider support remains endpoint-specific. The adapter
now freezes that choice per route instead of assuming another universal spelling.
The earlier retained Kimi/Fireworks full-source probe corroborates this distinction:
its `max_completion_tokens` request was rejected and its separate `max_tokens`
condition returned a proposal. It is historical v1 evidence, not qualification for
the new editorial contracts.

The first accounting check assumed the billing model would echo the request alias.
Opus's actual response and request use `anthropic/claude-opus-5`, while routing and
accounting identify `anthropic/claude-opus-5-20260723`, already present in the frozen
catalogue. All four public alias lookups returned 200 with their canonical slug;
canonical-slug model lookups returned 404. Consequently the transport retains the
request alias and separately binds the exact catalogue accounting identity. It does
not substitute a canonical slug into requests or learn an alias after dispatch.

Opus's invalid JSON remains a separate route-output failure. The exact request
contained the expected native strict schema. OpenRouter's
[structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs)
describes varying endpoint enforcement. The receipt has no transformed upstream
body, so the specific cause is unknown. No accounting adjustment can make that prose
pass. The next declared provider condition is Opus/Vertex global; its missing
`structured_outputs` advertisement remains recorded and actual qualification must
decide eligibility.

## Second declared cohort: qualified routes

The second condition completed all sixteen requests at 18:16:13 UTC. It used the
explicit endpoint token parameter and canonical accounting identity, with the same
four semantic prompts/native schemas, high effort, 32,768 output and 300/540-second
streaming deadlines. Opus used Vertex global as a separately declared provider
condition; the original Bedrock failure remains intact.

| Route | Author | Cold review | Source review | Patch |
| --- | --- | --- | --- | --- |
| Astra / Azure | Pass | Pass | Pass | Pass |
| Opus / Vertex global | HTTP 404 | HTTP 404 | HTTP 404 | HTTP 404 |
| Gemini / Vertex global | Pass | Pass | Pass | Pass |
| Kimi / Fireworks | Pass | Pass | Pass | Pass |

All twelve passes settled, totaling **588,704 micros ($0.588704)**. There were no
unknown outcomes in this second cohort. Each Opus response states that no endpoint
can handle the requested parameters; the response does not isolate which parameter
caused rejection. This is request incompatibility, not a timeout or editorial score.
The process exited with code 1 because some candidates failed, while the journal
correctly records a completed cohort. A successful synthetic stage means strict
structure and source/repair-authority validation passed; it does not mean a good
standalone topic was selected from a real recording.

The unchanged terminal report/journal SHA-256 is
`275e7908a6ce7267f68db60f22a40fd17dc1b1f77d17232bfd34e5c04a332096`.
All 72 referenced response, error and accounting artifacts were rehashed; all twelve
outputs passed independent replay through the production validators, and their raw
receipts reproduced the journal's exact normalized costs. Route totals were Astra
$0.320688, Gemini $0.186665 and Kimi $0.081351. The first cohort's separate $0.040225
raw observation and original identity-failed reservation remain distinct from these
settled amounts.

Current implementation fingerprint:
`2f1121dc2477407a29d26124cab4b7fe777de678d62d87b568e1149bb93ab92c`.
Actual local production image:
`sha256:74c18bf0212d0f312d9d7161a4f25807106ff4bc6430da61127ca659dc45c596`.
Actual staging trial image:
`sha256:6ed240dca24c74f93d67f7754cb281b773a9da5b6a5c3420a0ce80a0f943e410`.
The staging derivative retained the PR #37 base filesystem and all 112 installed
distributions; all 161 Python files and complete programme records matched the
local production image. The second candidate-file SHA-256 is
`156d430dd352dacc42ba1a4b26de5a53c8a5d46cb191fcabfc09effd7cb8b2b3`.

These results make the original Astra/Kimi/Opus author comparison unavailable.
The plan declares a separate two-arm Astra/Kimi comparison with fixed Gemini review,
and qualified Astra/Kimi/Gemini pools. Three qualified families per pool remain a
requirement; three evaluated author arms are not. Both arms use the same derived
44,503,040-micro run allowance, preserving the existing dispatch/repair configuration.
It reflects the newly frozen provider-price envelope, not expected expenditure.

## Full-Karma experiment preparation

Both complete original qualification reports and all 82 receipt aliases were
retained, then both `/4` arm bindings passed inside the exact trial image with
networking disabled. The packet SHA-256 is
`7832f1f0cb5c227efd441dd8e481df1998e9e81277a57d061c42fe26c102ca08`.
The production operator prepared experiment
`80fb3012-22ff-4ed8-9ed0-2d4c52accfcb`, with immutable prepared-file SHA-256
`fd1742adb4b47c00528c8c139c6b9295743e3baa43bf423ebb9f66b539c45818`.

The full Karma source remains 43:56, source SHA-256
`dbac1e49ef528b4991a0e1d0568dc83fad7c35cffd114f445325c77fb0e3e3b9`.
The transcript's existing optional database hash is null. A separate scoped read
verified its actual 634,934 bytes against
`cf46fa227ab75cdf008d3666d02f67e94b574966dd9d118f09972092986399e4`,
bound to the new prepared identity; original metadata and prepared bytes were not
rewritten. Each start repeats that byte check, and the resulting source evidence
must agree afterward. The generic brief and rubric remain frozen; neither arm
receives the assistant's independent source inventory.

| Arm | Run ID | Reserved independent reviewer |
| --- | --- | --- |
| Astra / Azure | `b8a22df8-c82e-5c4b-a01a-019318a15a30` | Gemini / Vertex global |
| Kimi / Fireworks | `4e3479df-c430-5f83-9dfa-0df4097240c1` | Gemini / Vertex global |

Preparations and subsequent execution records live under the private durable path
`/var/lib/temnia/experiments/karma-openrouter-two-arm-20260911`. Old Vercel run IDs,
unknowns and unstarted preparations remain separate.

### Astra full-source outcome

Astra's author request was dispatched at 18:33:21.281 UTC. HTTP 200 opened at
18:33:23.976, and the early generation ID
`gen-1789151601-R9DDjQxMtwzwx6KzNh0R` was retained. During stream consumption the
installed OpenAI SDK raised `APIError: Network connection lost.` The retained
exception points to the SDK's branch for an error field in an SSE event. This is
a reported stream error; it does not identify which network hop failed.

The attempt became `outcome_unknown` at 18:36:34.375, **193.094 seconds** after
dispatch. Neither the 300-second idle timeout nor the 540-second aggregate deadline
caused this termination. The workflow failed with no complete author response,
selection, review, repair or rendered portfolio. Its **5,009,620-micro reservation**
remains active, and its original ledger has zero settled micros; that is not proof
of zero charge. No inference is repeated to recover this result.

Persisted evidence exactly matches the previous full-Karma evidence at
`38da4d18000440a2c9db9a0864866f1c19c34c82b0c9864fb23e5e46c8617219`.
Source and transcript identities agree. The failure demonstrates that the new
transport preserves a generation handle on a real incomplete full-source request;
it does not establish editorial ability or reliable completion. Kimi's separately
prepared arm is still a distinct planned observation, not an Astra retry.

A single separate accounting GET at 18:40:52.899 UTC returned HTTP 200 and passed
the production generation/canonical-model/provider checks. OpenRouter reports
**zero total and upstream cost**, 20,829 input tokens and 204 output tokens, all
204 recorded as reasoning. The receipt has no finish reason, and `cancelled=false`
does not establish completion. Its 1,389 original bytes hash to
`aac832511268d4484217ab757d35e3157c42f5ef939c45e05d43f3a8764bc7c9`.
This is an observed zero charge, separate from the unchanged original ledger and
unknown-response fence. No inference or ledger reconciliation was performed.

The production terminal export passed validation; its bundle SHA-256 is
`8a80a30c2bca0e2f5194e404221daf9fb3550588df06ca3b79de8f4e20277483`.
The unfilled human-label template and unlabelled evaluation report remain explicit.
The owned worker stopped gracefully at 18:39:35.770 after the terminal/no-pending-work
checks. Its container, generation handle, evidence and original reservation remain
retained.

### Kimi's initial selection and independent inspection

Kimi's author request completed in **222.512 seconds**, settling **247,932 micros**.
It produced eleven initial video candidates. The immutable running export has
bundle SHA-256 `b8bb47211fb9d2177e8b84c86a287567994f5f95ea7b3fc7bf32995154bd03a6`;
its selection record hashes to
`8ee7e8a0ba1410031dd5401293b27cfb7dc94de06e59dcc5ecf7891f7195799c`.
This captures the initial proposal before any repair, not the final portfolio.
All eleven initial individual-review requests subsequently completed and settled;
successful request completion does not establish that their judgments are correct.

An assistant read the entire source and froze an independent opportunity inventory
at 17:58:20 UTC, before either new full-source output. Its freeze SHA-256 is
`f753dd81dc944af727f2a99d24c766bfda8c25cc3b5d4bb0ce9a4ff1256f9f24`.
It records alternative topic groupings, not a required output count or human gold.
Separately, initial candidate text was inspected before reading model critic
opinions. That source-familiar, text-only inspection was frozen at
`9943cf3d7460d2c8db787beb35608956722b73ae183085385366b092a0b9283a`.
No inventory, diagnosis or specific source hint enters production prompts.

The initial inspection found concrete hypotheses to test against review and repair:
one candidate starts with a dependent sentence about something becoming part of
one's breath, while its prayerful-attitude antecedent remains in the previous
video; a warning about choosing spiritual practices is separated from a later
positive qualification; and another candidate ends on a fatalistic premise before
the ensuing discussion develops its answer. These are grounded assistant diagnoses,
not publication scores. Final selection, model detection and correction must be
examined separately. Rendered audio/video has not been reviewed at this stage.

The first committed assessment is retained in bundle
`815bec08fbe974e447e6c70f3ea08a88196d9eb12e5a6502023fc1b6ab664fbd`,
assessment `188be6e6fef9b35769303640667346cdc8037767eb4d8f977c36631a1739c81f`.
Gemini's individual reviews independently flagged the orphaned V5 opening and the
unfinished V7 ending. The assessment produced three findings: required missing
setup and a preference for a better opening on V5, plus required completion on V7.
These findings, rather than the assistant's notes, authorize the scoped repair.

The separate Gemini source/portfolio review selected all eleven candidates and
passed every candidate's context, distinct-purpose and faithfulness checks. It
called V5 and V7 complete despite the individual reader's contrary findings. It
also did not flag the warning/positive-qualification split across V5/V6. This is a
specific observed gap in source review on this selection. The full-source prompt
exposes the author's proposal and rationale; whether that exposure causes the
agreement is a hypothesis requiring a controlled comparison, not an established
explanation. A different model family alone does not establish independent judgment
or adequate critic sensitivity. The combined assessment still preserved the local
reader's required failures and initiated repair.

Inspection of the unchanged repair implementation identifies a separate constraint:
`_validate_operation_shape` permits `extend_start` and `extend_end`, but rejects
shortening either edge. Other operations are retitle, merge, split, drop and add
opportunity; there is no direct one-candidate trim or general extent replacement.
The assessment authorizes changes to V5/O5 and V7/O7 only. A tighter V7 ending before
its new fatalism setup, or a V5 opening after the orphaned prayer continuation,
therefore cannot be represented as a direct trim. Splitting requires multiple new
candidates, and merging with V4/V6/V8 requires findings naming those candidates.
This is an observed limit of the frozen repair contract, distinct from model
selection quality. The actual patch response and its validator outcome must show
what the model does within that limit; this evaluation does not alter it mid-run.

### Kimi's rejected repair and terminal delivery

The repair request completed in **290.424 seconds** and settled **380,265 micros**.
It requested a V5 start extension from `s000239` to `s000226` and a V7 end extension
from `s000348` to `s000357`, preserving candidate IDs, titles, purposes and unrelated
videos. It also changed O5's required-context and value-evidence spans. The atomic
validator rejected the first operation with
`repair cannot rewrite opportunity evidence to evade a finding`; neither candidate
extension was applied. This was not a trim rejection.

The patch response hashes to
`83c4d8fb7704a0b54b4e111823f22546441e20264e199a14d733d919717256c7`,
and the retained rejection to
`da04ca906e3f673f15f49539ba8de4321cf89dedfc9b6f9ac1df3de5258d8ce2`.
The final assessment preserves the original selection and required findings. No
further model reviews occurred because no changed selection was admitted.

This exposes a concrete implementation mismatch: `selection_patch_prompt` asks for
updated opportunities, and its schema exposes full opportunity objects, while the
validator permits changes only to `candidateIds`, `disposition` and
`dispositionReason`. The prompt does not enumerate that restriction. A pure offline
replay reproduced the rejection. A diagnostic copy restoring the original
opportunity definitions while retaining the proposed candidate extensions passed
the validator. That copy was not persisted, dispatched or treated as a model result.
The refusal is therefore not evidence solely of model inability. A future contract
change should make its editable projection explicit and preserve original evidence,
rather than remove protection to accept this response.

There is also an independent editorial problem in the attempted V7 repair:
`s000358`, immediately after its proposed endpoint, explicitly qualifies the
preceding affirmation of virtuous action. The repair would still omit that
qualification. Passing deterministic admission would not prove this edit faithful.
This is a source-familiar assistant diagnosis, separate from the actual refusal.

Kimi's workflow completed in **`needs_review`**, retaining the eleven original
candidates. It settled **1,027,687 micros ($1.027687)** across fourteen model calls,
with no unknown cost or active reservation in this run. The compiler and renderer
produced eleven video references, totaling 309,293,430 media bytes. Each retained
render reports all seventeen technical checks passing, including full decode,
duration, stream properties and caption bounds. These checks do not judge the
standalone discussion or establish playback acceptance; no human accepted a video.

Terminal bundle SHA-256:
`ec9a6c127791e44609916ca81f9b110e6f2b3a29c2d4566625fd62f4400e2e88`.
Final edit:
`c4f6bad69bd6d95aea2bbff4d75334fd24fdf1db8235465f5666749bd30a9761`.
Final render descriptor:
`bb30c2fc433225f3800416986957b68ae3cc3b32d376b50eaadad5816025598f`.
The production export validated; human labels remain unfilled. The owned worker
stopped after terminal/no-pending-work checks. Runtime observations retain the early
generation handles even though the existing evaluation-attempt projection exposes
`remoteHandle=null`; that export limitation is not evidence of a lost runtime handle.

All twenty-two compiled edges retain speech-coverage uncertainty requiring review.
The physical cuts add no source sentence outside the selected semantic spans. In
particular, V5's padding does not bring back its prior prayer antecedent and V7's
padding does not include the following answer. The deterministic compiler cannot
repair the semantic omissions merely by finding a cleaner nearby cut.

### Original native comparison

The unchanged native `model_swap` report hashes to
`0a6d8b2c9d48fb5b727820e2117a8110aa8278b50d99a5f95e7f004ecf901921`.
It reports `comparable=false`, `productionQualificationReady=false` and no winner.
Both human primary metrics are unmeasured. Astra also has no observed reviewer or
compiler, and its failed-before-selection export uses a fallback rubric identity
that differs from the actual rubric retained by Kimi. Those native missing/mismatch
reasons were preserved, not filled from the intended configuration to force a pass.
Source/transcript, declared programme/prompt/schema and execution settings match.
Operational delivery is observed; a comparative editorial score is not.

### Additional declared Gemini-author/Kimi-reviewer configuration

After Astra's operational failure, one additional configuration was declared before
inspecting Kimi's candidate content. It swaps both roles: Gemini authors and Kimi
independently reviews. It uses the same source, generic brief, rubric, programme,
high/32,768 profile, streaming deadlines and AdaptiveDetector, and rebinds the same
twelve successful native-stage proofs against both complete qualification reports.
No new qualification inference or source-specific prompt amendment is introduced.

This is a separate one-arm experiment, not an Astra retry or a replacement for the
original two-arm record. Its **71,755,776-micro** allowance is the frozen maximum
of one Gemini call and thirty-one Kimi calls under the existing 32-dispatch
programme; the two-author-call mixture is lower. This is admission exposure, not
predicted expenditure. It runs sequentially after the original Kimi worker stops.

The later native comparison declares `mode=configuration` and allows the author,
reviewer and execution identities to change; the execution difference is the
price-derived allowance. The original Astra/Kimi `model_swap` comparison remains
intact. Both retain normal human editorial precision and opportunity recall as
primary metrics. Missing human labels remain unmeasured, and neither comparison
can identify an editorial winner from these runs alone.

Its production preparation created experiment
`a5176076-1418-4755-b142-1ad99399243c`, run
`7ff8f391-55ea-5a24-8808-336cb54f32be`, with snapshot
`8c09dd86db9020126d799fca9fe1f7885da352d57bdecfa132987ee3dec77f64`.
Packet SHA-256:
`1d1e4d50f07ab6547aec33e69874db9568d479c58aada2fca8788ba592d28409`.
Prepared-file SHA-256:
`39a0c368b600d9e3599cb09595245211af6dd833f44956426dcaa2cc2472dae8`.
The ordinary `/4` binder and fresh transcript-byte proof passed. The image and
programme remain identical to the first two arms.

### Gemini selection and Kimi review

Gemini completed authoring in **166.460 seconds** for **132,166 micros**, proposing
four candidates and six opportunities. Its initial selection hashes to
`bcad2f8cf1d4df139aa5a0e07263fe1896f5d7cdcacabe20ddd434863c909c63`.
The four selected discussions were a broad karma primer (`s000036`–`s000201`),
wealth/bad karma (`s000313`–`s000348`), rock bottom (`s000391`–`s000447`) and the
fixed-deposit analogy (`s000448`–`s000507`). The wealth candidate has exactly Kimi's
original speech extent. The broader primer retains follow-ups that Kimi separated;
its sustained focus is an editorial tradeoff, not a failure inferred from duration.

Content-only diagnoses were frozen before reading this configuration's critic
opinions. The reviewers already knew the source and Kimi output; this is explicitly
not a blind comparison. Four outputs alone is not evidence of either good
selectivity or low recall. The actual opportunity inventory distinguishes three
different states:

- Prayer/checklist material has no separate authored opportunity; its main exchange
  is absent from the author's evidence spans. The earlier action/stillness/offering
  answer at `s000349`–`s000389` has no authored opportunity evidence at all.
- Practice/mantra choice is discovered but rejected as `not_contiguously_extractable`.
  Its record cites context, core, follow-up and completion. The claim that an
  intervening conversational digression prevents a coherent longer treatment needs
  editorial adjudication; it is not a mechanical impossibility.
- The book-origin story is discovered and declined for audience value. Its narrower
  appeal was already a caveat in the independent source inventory; it is not an
  automatically required output.

All four Kimi individual reviews and its source review settled. The first assessment
hashes to `4aa895073f7c951359df684f06011dbeac2f287fd54f51587c5b3ea9597e88e0`.
The individual wealth review again catches the unfinished ending. The source
review discovers a new prayer/perfection opportunity, a real contribution beyond
the author's inventory, but leaves the action/stillness omission unresolved and
agrees with the mantra exclusion. In that agreement it treats the later positive
qualification as part of a digression; the frozen independent reading identified
that qualification as relevant to the warning's meaning.

The critics also make conflicting context judgments. Individual review passes the
wealth opening, while source review demands the affluent-friend setup over seven
minutes earlier, across the intervening prayer and practice discussions. Both
independent content inspections found a clear self-contained paradox and concrete
story at the selected opening. Conversely, individual review fails the rock-bottom
opening for residual percentage/show framing while source review finds its explicit
question sufficient. These are calibration questions, not adjudicated false-positive
rates. One individual-review takeaway also recommends keeping only positive people
close although the selected answer challenges that premise; critic summaries must
not substitute for source text.

The newly discovered prayer opportunity labels `s000239`–`s000240` as completion,
but `s000240` asks another awareness question answered afterward. Discovery has not
yet produced a faithful replacement. Its evidence annotations need the same
scrutiny as author annotations. The source review declines wealth, leaving three
recommendations in the evaluator's projection, while the immutable initial
selection still contains all four candidates.

### Gemini repair length termination and final comparison

The seventh request, Gemini's scoped repair, returned a retained response with
**`finish_reason=length`**, including the provider-native finish reason. It records
**32,754 output tokens, including 31,454 reasoning tokens**, against the frozen
32,768 allowance. The paid activity settled **159,216 micros** before its stream
normalization guard raised
`UnexpectedModelBehavior: streamed model response did not finish successfully`.
This was a complete but truncated paid response, not a timeout or unknown expense.
No patch was admitted or applied; no compiler or renderer ran. It is not evidence
that the patch validator rejected a submitted edit.

The visible incomplete JSON proposes dropping wealth: it adopts the source critic's
distant-setup demand, then says that adding all intervening discussion would destroy
coherence. It starts adding the discovered prayer opportunity, but stops before
defining a replacement candidate. This traces a questionable dependency claim into
a proposed exclusion; it does not establish an applied drop or the missing patch's
eventual extents. The prefix also mixes raw and aggregated finding IDs, a latent
authority-interface concern. Neither that ambiguity nor the repair vocabulary is
proved to have caused the observed length termination.

The run ended **`failed`**, with all seven calls settled at **619,309 micros
($0.619309)** and no active reservation. It preserves the original four-candidate
selection and first assessment. Its terminal bundle hashes to
`f4729850479a487a6cdb5f8aa0b8e992ec540b240ec70044ecf9c24d97cfb054`.
The original response and finish metadata remain private retained artifacts. The
native export, unfilled-label template and unlabelled report validated. The owned
worker stopped at 19:35:48.747 UTC after terminal/no-pending-work checks.

The declared native configuration comparison hashes to
`ecc110425caefac23e28d547a54c3409d213a6136dfbcb90142b24c2daa6346c`.
It reports `comparable=false`, `productionQualificationReady=false`, `winner=null`:
Gemini never reached a compiler observation and both human primary metrics remain
unmeasured. Source, transcript, rubric, programme, prompts, schemas and evidence
configuration match. Both role identities change as declared; the only execution
difference is the price-derived admission allowance. Gemini's delivery yield remains
unmeasured because rendering did not run, while the factual delivered count is zero.
Neither missing factor nor metric was rewritten to manufacture a comparison pass.

## What to test next for production quality

The next programme should address these related intelligence gaps as one measured
change, keeping this programme as the baseline:

1. **Find and preserve worthwhile discussions.** Independently map viewer purposes
   and developed answers before comparing the author's selected portfolio. Evaluate
   never-discovered opportunities separately from discovered-but-declined ones.
   Review claimed non-extractability against a complete contiguous treatment and
   distinguish necessary context from merely earlier related discussion. Test
   whether withholding author rationale during an initial source pass improves
   omission detection; this trial does not establish anchoring as the cause.
2. **Calibrate comprehension, completion and faithfulness judgments.** Adjudicate
   critics against human source and cold-viewer judgments, including valid excerpts
   as well as deliberately incomplete controls. Test preservation of qualifications
   and disagreement, and whether proposed completion spans actually finish the
   answer. A model-family difference alone did not ensure reliable criticism here.
3. **Make useful repairs expressible and their authority clear.** Expose only
   editable opportunity fields, or separately version an evidence correction with
   retained lineage. Support a scoped replacement of both extent edges, including
   contraction, and reassess changed content. Today's one-edge/one-touch rule also
   makes simultaneous opening and ending repairs awkward. These interface limits
   are distinct from Kimi's observed immutable-field rejection and Gemini's length
   termination; do not weaken evidence protection to make either result pass.
4. **Audition roles on representative full repair and review inputs.** Tiny exact
   schema probes establish transport admission, not completion at real task size.
   Compare declared reasoning/output configurations per role with costs, complete
   valid responses, repair improvement and regression measured. Raising every
   ceiling would be an untested configuration change. Explicitly assess usable
   reviewable output after known repair failures: the rejected Kimi patch retained
   rendered originals, whereas Gemini's truncation stopped before any rendering.
   Establish the editorial win
   with independent human labels and fresh full sources before promotion.

These are a focused follow-up design, not implemented improvements or permission
to tune against Karma and call the result general. Keep the source inventory and
critic diagnoses out of production trial prompts. No arbitrary duration or output
count is needed; judge useful discussions and faithful completion.

## Final accounting scope

The two OpenRouter qualification cohorts and three full-source runs contain
**43 physical model requests**: 21 qualification requests and 22 workflow requests.
Original journals and ledgers report **2,235,700 settled micros ($2.235700)**.
Two original unresolved calls retain **6,001,793 micros ($6.001793) of estimated
exposure**: the first cohort's identity-failed Opus call and Astra's full-source
stream error. The separately retained raw observations are $0.040225 for Opus and
$0 for Astra; they are not added to settled totals or used to clear those fences.
Older Vercel expenses are outside this OpenRouter scope. These are model expenses,
not a complete infrastructure/rendering bill or a measured cost per accepted video.

## Implementation validation

The full clean-commit `pnpm ci:local` gate passed for implementation checkpoint
`e7cf00b60ca4e5b74a08aff263f63ba915afa424` at 19:17:43.687 UTC on September 11.
It passed all sixteen package tasks, **1,590 Python tests** with one optional
detector-agreement test skipped, both production image builds and **twenty browser
tests**. This includes the new OpenRouter wire, accounting, early-handle/unknown
ledger and qualification-binding cases. Final evidence-document additions require
their own exact-commit receipt before the verified PR is delivered.

## Retained evidence and acceptance limits

First-cohort originals remain on staging at
`/var/lib/temnia/qualification/topic-selection-openrouter-20260911-high32768`.
Private local reports, immutable original receipts, catalogue snapshots, image
proofs and the model-identity matrix are under
`/private/tmp/temnia-openrouter-evaluation-20260911/`. Transcript-bearing material,
provider reasoning and credentials are not committed. The ordinary staging worker
configuration and historical Vercel unknown attempts were not changed.

The first cohort qualified no editorial roles and launched no full-source workflow.
The second qualifies three routes for the four native stages. No human opportunity
labels, publication-quality scores, reviewer calibration or model/gateway promotion
follows from qualification. Karma remains exposed development material.
Independent human opportunities and cold/source/playback judgments are still needed
for the actual selection-quality decision.
