# Temnia Harness: Model Selection and Editorial Quality Evaluation

**Date:** September 11, 2026  
**Status:** Proposed engineering and evaluation plan; no new model benchmark has been executed.  
**Audience:** Temnia engineering, editorial evaluators, and coding agents.  
**Suggested repository path:** `docs/plans/harness-model-selection-and-evaluation-plan.md`

## 1. Decision and product objective

**Establish a quality-first model configuration before adding substantial new editorial orchestration or optimizing inference cost.** Compare the existing Temnia configuration with capable GPT, Claude, Gemini, Kimi, DeepSeek, and GLM configurations on Temnia's real task. Choose models by measured results, not provider nationality, reputation, or a general-purpose leaderboard.

The target outcome is:

> Find worthwhile discussions inside a long podcast, retain what each discussion needs to make sense independently, preserve its meaning and natural completion, and produce videos an editor would consider publishing as separate YouTube uploads.

This is not merely chapter navigation, timestamp extraction, valid JSON generation, short-form virality prediction, or correct FFmpeg execution. A coherent excerpt is not necessarily an interesting video. An interesting excerpt is not acceptable when it distorts the speaker's meaning.

A more capable model is a plausible way to improve discovery, context selection, completion, audience judgment, and repair. **No Temnia-specific comparison currently establishes the size of that improvement or a winning model.** The immediate recommendation is to measure the opportunity, not declare that GPT or Claude necessarily beats Kimi, DeepSeek, or GLM.

OpenAI explicitly recommends establishing task accuracy before optimizing cost and latency. Anthropic describes capability-first selection for complex, nuanced tasks, alongside efficiency-first selection for other workloads. This plan chooses capability-first because of Temnia's stated product priority. [S1][S2]

## 2. Scope and relationship to the existing engineering plan

Use this document alongside `harness-editorial-quality-engineering-plan.md`. It changes the implementation sequence: repair correctness blockers, establish a strong-model reference, then add editorial mechanisms where measured failures remain.

This document is not a new repository audit. Previously discussed source locations are integration search targets, not newly verified findings against the latest checkout. Before implementation, resolve actual types, functions, workflows, and tests in the current repository.

All configuration layouts, records, metrics, and work packages below are **proposed Temnia designs**, unless explicitly attributed to an official provider source. They are not claims that corresponding modules or APIs already exist. The document does not apply code patches, authorize paid inference, or report fabricated evaluation results.

Retain these product constraints:

- Preserve natural, contiguous conversations for this feature; do not quietly introduce stitched montages or generated dialogue.
- Permit justified overlap and unused source material. The portfolio need not partition the entire episode.
- Do not force an output count, short duration, sensational hook, or artificial conclusion.
- Keep source-bound selection, deterministic speech-safe cuts, versioned artifacts, and human editorial decisions.

## 3. Where model capability can help—and where it cannot

| Failure or decision | Model experiment | Required harness responsibility |
|---|---|---|
| Misses a worthwhile story or explanation present in the source | Compare discovery quality | Expose the source and measure missed opportunities |
| Starts with an unresolved “that” or “as I said” | Compare opening and dependency judgment | Preserve stable sentence IDs and permit grounded extension |
| Ends before the answer or qualification is complete | Compare local and source-aware review | Make the necessary source context accessible |
| Selects a technically coherent but low-value exchange | Compare audience-specific value judgment | Share one audience rubric across stages |
| Expands a segment until it becomes unfocused | Compare completeness-versus-focus judgment | Support rejection, alternative selection, or scoped restructuring |
| Proposed interval is correct but compiler cannot find an available safe cut | Not an author-model problem | Fix feasible-cut discovery |
| Relevant content was removed during preprocessing | No model can inspect absent evidence | Fix evidence access and provenance |
| Reviewer evaluates the wrong audience | A stronger model may still follow the wrong brief | Freeze and share the intended rubric |
| A necessary merge is not expressible | Reasoning does not create an authorized operation | Add a validated compound-repair contract |
| Request times out or its charge is unknown | Operational result, not proof of weak editorial reasoning | Preserve receipts and reconcile without blind redispatch |

Model selection and harness design interact. Avoid explaining every failure as “the model is weak” or every failure as “we need another agent.” Classify the failure using the exact evidence and decision that produced it.

## 4. Candidate models: a dated shortlist, not a ranking

The following names and identifiers were checked against official documentation on September 11, 2026. Documentation confirms catalog entries, not account access, gateway support, immutable deployment versions, or podcast-editing quality. Recheck before execution and record the actual response metadata.

| Family | Candidate documented at verification | Intended experiment | Reference |
|---|---|---|---|
| OpenAI | GPT-6 Astra, `gpt-6-astra` | Transcript author and editorial reviewers | [S3] |
| Anthropic | Claude Opus 5, `claude-opus-5` | Transcript author and reviewers | [S4] |
| Anthropic | Claude Fable 5.1, `claude-fable-5-1` | Additional capability test if Opus results leave consequential failures | [S2][S4] |
| Google | Gemini 3.8 Flash, `gemini-3.8-flash` | Transcript comparison, then a separate actual-media experiment | [S5] |
| Google | Gemini 3.1 Pro Preview, `gemini-3.1-pro-preview` | Alternative Gemini configuration; qualify preview behavior separately | [S5] |
| Moonshot/Kimi | Kimi K3, `kimi-k3` | Contemporary comparison alongside the unchanged historical baseline | [S6] |
| DeepSeek | `deepseek-v4-pro`; optionally `deepseek-flash` | Reasoning-model comparison, not an assumed inferior tier | [S7] |
| Z.ai | GLM-5.3 family | Contemporary GLM comparison; resolve the actual endpoint through its current guide and account | [S8] |
| Existing Temnia route | Exact current model, route, reasoning settings, and prompts | Mandatory control condition | Freeze from the running configuration |

Do not silently replace the existing baseline with a newer model from the same family. Keep both experiments identifiable. Do not assume a “Flash” model is weaker than an older “Pro,” that a longer context guarantees better dependency tracking, or that provider marketing claims establish editorial superiority.

### First comparison to run

Start with the unchanged baseline, GPT-6 Astra, and Claude Opus 5. Add Gemini's text-only condition next. Include contemporary Kimi, DeepSeek, and GLM alternatives as budget allows; they remain eligible to win.

Do not run every possible author–reviewer permutation immediately. Screen authors and reviewers independently, then evaluate a small number of promising complete configurations.

## 5. Assign models to editorial roles, not the whole application

Separate the model assignment for each decision role. The same model may occupy several roles in isolated requests; role separation does not require a new agent framework.

| Role | Allowed evidence | Required decision |
|---|---|---|
| Opportunity discovery and authoring | Original transcript, speaking turns, sentence references, audience rubric; source map if used | Identify valuable discussions and propose complete source-bound extents |
| Cold-viewer review | Only candidate content, candidate title, and audience rubric | Decide whether the candidate establishes its own context and delivers value |
| Source-aware review | Candidate plus the required original-source evidence | Check fidelity, setup, qualifications, completion, and portfolio relationships |
| Repair | Grounded findings, allowed source evidence, authorized affected candidates | Apply bounded changes without degrading unrelated good work |
| Actual-media review | Actual candidate playback; neighboring source only in a separately identified source-aware pass | Detect delivered audio/visual editorial defects |

For the first complete premium configuration, test Claude author/GPT reviewers and the reverse after screening. These are experimental assignments, not claims about each provider's creative or analytical personality.

A fresh context is necessary for cold review. A different provider family is an optional experimental factor; it does not guarantee independent errors. A reviewer must not receive the author's persuasive rationale before deciding whether the candidate itself works.

**Cold-review instruction:** assess what the video establishes for the specified viewer. Do not fill missing context using private familiarity with the episode or general model knowledge. The title must be faithful and must not be used to excuse a materially incomplete opening.

## 6. Shared editorial rubric

Freeze a versioned rubric before comparing models. Provide the same intended audience, language, assumed expertise, purpose, and style preferences to author, reviewers, and repair.

Assess seven dimensions separately:

| Dimension | Editorial question |
|---|---|
| Audience value | What concrete question, explanation, story, experience, or perspective makes this worth watching for this audience? |
| Independent comprehension | Are necessary subjects, referents, and premises established within the selected content? |
| Development and focus | Does the interval develop a coherent discussion without excessive unrelated material? |
| Completion | Does the answer, story, argument, or purposeful open question reach a natural stopping point? |
| Fidelity | Are consequential qualifications, corrections, and speaker intent preserved? |
| Title-content fit | Does the title make a promise that the selected content honestly fulfills? |
| Delivered viewing quality | Do the actual beginning, ending, audio, and relevant visuals work? |

A provisional four-band scale can support editor calibration: `0 = fails`, `1 = substantial repair`, `2 = minor repair`, `3 = publish-ready for the brief`. Use concrete reference examples to align raters. These bands are not calibrated probabilities or predictions of views.

A major fidelity or independence failure cannot be averaged away by a high interest rating. Allow `unknown` when required evidence is unavailable. Do not equate a model's confidence with correctness.

Avoid compulsory novelty or controversy. A clear beginner explanation can be valuable even when experts know the material. A thoughtful segment can be long. “Complete” does not mean forcing certainty into an honestly unresolved discussion.

## 7. Controlled author experiment

### Freeze the evidence and execution conditions

For each episode, freeze the source checksum, selected streams, transcript revision, sentence IDs, speaking turns, audience rubric, prompt template, output schema, repair policy, and compiler/render configuration. Do not regenerate ASR per author.

Run two distinct comparisons when needed:

**Model-swap comparison:** preserve semantic instructions, evidence access, and output contract. Permit only documented provider syntax and adapter differences. This estimates improvement from a practical model substitution.

**Best-qualified-configuration comparison:** give each candidate an explicitly bounded development tuning opportunity. Freeze provider-specific prompts and reasoning settings before the held-out evaluation. This estimates the best deployable configuration, not the isolated model effect.

Report them separately. Do not silently give one provider a better prompt and describe the result as a pure model benchmark.

### Match information, not token counts

Use adequate supported reasoning and output limits. Tokenizers and reasoning controls differ; identical numeric budgets are not necessarily equivalent. Anthropic explicitly treats effort as a capability/cost/latency lever. [S2]

Preflight the entire request, including schema, rubric, and output reserve. On a smaller context limit, mark the direct-full-source condition unsupported rather than silently truncate. A hierarchical alternative is a separate configuration with complete source coverage and traceable references.

### Human review of author outputs

Review source-bound proposals independently of model-written justifications. For a small development set, use five varied full episodes as a starting diagnostic sample—not statistical proof. Include the languages, accents, code-switching, expertise levels, lengths, and formats intended for launch.

Use separate held-out recordings to confirm improvements. Keep all clips from the same recording in the same split; avoid near-duplicate material across development and held-out sets. Repeat a subset of runs to observe variability instead of relying on one favorable response.

## 8. Reviewer calibration experiment

Give reviewers the same fixed set of good and flawed candidates. Include missing questions, unresolved referents, delayed qualifications, abrupt conclusions, excessive unrelated context, duplicate cores, misleading titles, specialist content, and deliberately good controls.

Measure both false approvals and false rejections. A judge that rejects everything is not useful; a judge that accepts attractive prose is not reliable.

Blind provider identity, randomize order, and counterbalance pairwise presentation. Ask for short evidence-grounded rationales, not hidden chain-of-thought. Model-judge bias and the need to check agreement with human labels are documented evaluation concerns. [S9]

Use separate judgments for cold comprehension and source fidelity. An editor who has already watched the entire source is no longer a clean cold-viewer evaluator for that episode; use another editor or explicitly label the limitation.

Review quality must be calibrated before using model scores to choose the winning author. Otherwise, the selected author may merely match the judge's preferences.

## 9. Complete-workflow experiment

Take the strongest author/reviewer combinations through the whole production path:

```text
Frozen evidence and rubric
          ↓
Author proposal
          ↓
Cold-viewer and source-aware assessment
          ↓
Grounded repair, when authorized
          ↓
Deterministic compilation and rendering
          ↓
Actual-media technical checks
          ↓
Blind independent-viewer assessment
          ↓
Source-aware editorial assessment
```

Retain the initial proposal, every assessment, rejected patches, accepted revisions, exported media, and final human decisions. Measure improvement across repair: how often it fixes the reported issue, introduces another issue, or wrongly changes an unaffected candidate.

Test previously identified failure cases, including an opening whose referent is outside the interval. A valid author response alone is not a completed editorial demonstration.

Keep the compiler and rendering path identical across model conditions. Report a model's inability to provide admissible source references as a contract outcome; distinguish it from a compiler failure on an otherwise valid selection.

## 10. Primary metrics and anti-gaming rules

| Metric | Definition and safeguard |
|---|---|
| Publish-ready precision | Final proposed videos accepted without editorial repair / final proposed videos evaluated. Count technical failures in the end-to-end denominator; report playable-output-only quality separately. |
| Useful-topic recall | Independent worthwhile source opportunities represented by at least one suitable output / independently labeled opportunities. Do not use source-minutes coverage. |
| Cold-comprehension defect rate | Candidates with missing necessary setup or unresolved references / candidates evaluated. |
| Completion defect rate | Candidates with incomplete answers, stories, or consequential endings / candidates evaluated. |
| Severe fidelity defects | Report count and rate separately; do not hide them inside a composite score. |
| Reviewer false-pass rate | Human-invalid candidates approved by a reviewer / human-invalid candidates judged. |
| Reviewer false-reject rate | Human-valid candidates rejected by a reviewer / human-valid candidates judged. |
| Repair success and regression | Grounded failures fixed and unaffected candidates degraded, reported separately. |
| Human editing effort | Active correction time and operations needed per accepted video; separate evaluation overhead. |
| Cost and latency | All paid calls, repairs, media analysis, and failed attempts per source and accepted output, plus completion rate. |

Label worthwhile source opportunities independently before comparing portfolios. Allow more than one legitimate segmentation: labels should capture viewer purpose, core evidence, and required dependencies, not one exact timestamp pair. Credit an opportunity only when an independently usable output meaningfully represents it. Do not credit a full-episode export for every topic merely because it contains their words.

A zero-output run must not receive perfect precision. Report it as no accepted yield, with appropriate recall and completion outcomes. An episode with no worthwhile opportunities is a legitimate abstention case; mark recall not applicable rather than invent a denominator.

Report episode-level results as well as aggregates. Candidates from one episode are correlated; use episode-level paired comparisons and uncertainty estimates rather than treating every cut as an independent trial. A five-episode diagnostic is not proof of universal superiority.

Define the promotion policy before viewing held-out results. Reject apparent gains caused by excessive omission, much longer unfocused videos, hidden fallback models, or increased fidelity defects. When uncertainty is large, retain the result as inconclusive rather than declare a winner.

## 11. Transcript reasoning versus actual-media understanding

Run these as separate ablations:

| Condition | Information supplied | Question |
|---|---|---|
| Text-only | Identical transcript and references | Which configuration makes better semantic editorial decisions? |
| Text plus media | Same evidence plus actual audio/video | Does delivery and visual evidence improve assessment? |
| Full-candidate versus edge inspection | Complete playback or restricted beginning/ending | What quality is lost when inspection is narrowed? |

Gemini's official video guide describes joint audio/visual processing and default static visual sampling at one frame per second. It warns that rapid motion or quick changes may lose detail. Native video input therefore does not establish frame-accurate observation. [S10]

Freeze media preprocessing, sample rate, resolution, processing mode, and inspected intervals. Verify that the selected provider route actually forwards the required modalities. Do not infer that an image-capable model can hear audio or directly inspect a video file.

A media reviewer may identify clipped delivery, missing demonstrations, awkward endings, or overlapping speech. Require observed evidence and approximate location as a diagnostic. Resolve final cuts through authoritative timestamps and existing safety constraints—not model-invented timecodes.

Start with full-candidate playback in the evaluation set. Boundary-only inspection cannot establish interest or coherence throughout the middle. Broader context access must not contaminate the independent-viewer pass.

## 12. Provider and configuration engineering

### 12.1 Configuration identity

Represent a tested model configuration as more than a display name. Record:

```text
provider and gateway route
requested model identifier
resolved model/deployment metadata, when supplied
adapter and SDK versions
actual supported request parameters
reasoning settings and output limits
prompt, schema, and rubric hashes
source/transcript revisions
modalities and preprocessing specification
allowed tool set and source-access policy
budget, retry, correction, and fallback policy
```

A provider alias may resolve differently over time. Pin a deployment snapshot when supported; otherwise retain response metadata and the evaluation date and rerun canaries. A configuration is reproducible only to the extent the underlying service exposes version control.

### 12.2 Proposed profile example

This is a conceptual Temnia configuration, **not** an existing config file or drop-in provider request. Implement validation and map logical profiles to tested native request parameters.

```yaml
schema_version: 1
profile_id: editorial-quality-reference-v1
status: proposed_unqualified

shared:
  audience_rubric_revision: editorial-audience-v1
  evidence_mode: frozen_transcript
  external_web_access: false
  silent_model_fallback: false
  automatic_publishing: false

seats:
  author:
    provider: anthropic
    requested_model: claude-opus-5
    request_profile_ref: opus-author-qualified-v1
  cold_reviewer:
    provider: openai
    requested_model: gpt-6-astra
    request_profile_ref: astra-cold-qualified-v1
    evidence_scope: candidate_only
  source_reviewer:
    provider: openai
    requested_model: gpt-6-astra
    request_profile_ref: astra-source-qualified-v1
    evidence_scope: source_bound
  repair:
    provider: anthropic
    requested_model: claude-opus-5
    request_profile_ref: opus-repair-qualified-v1

media_experiment:
  enabled: false
  provider: google
  requested_model: gemini-3.8-flash
  request_profile_ref: gemini-media-qualified-v1

budget:
  authorization_ref: null
  max_total_usd: null
  block_paid_dispatch_without_authorization: true
```

Null authorization/budget and unresolved request profiles must fail preflight for a paid run. Do not silently apply a default credential, spending limit, or reasoning setting.

### 12.3 Adapter contract

Preserve existing accounting and orchestration boundaries. The adapter should normalize successful structured responses, refusals, unsupported inputs, truncation, malformed output, timeout, cancellation, and unknown outcomes without pretending they are equivalent.

Strictly validate the same logical chapter contract after every provider response. Native JSON support is not a substitute for validating existing sentence IDs, ordering, allowed operations, and complete output. A syntactic/schema correction and an editorial revision must remain separately counted events.

Capture actual usage and receipts where available. Do not store or depend on hidden model reasoning. Store editorial evidence and observable outcomes. Keep credentials out of artifacts and logs.

### 12.4 Experimental isolation and caching

Use the complete decision identity for response cache reuse. A result from a different author, reasoning setting, rubric, evidence revision, or modality condition is not the same experiment.

Physical media can still be reused when the approved interval and render specification are unchanged. Model changes should invalidate the relevant editorial decisions, not arbitrarily force re-encoding identical media.

Disable silent provider fallback in comparative runs. An explicit fallback creates a separately identified mixed configuration. A timeout remains an operational outcome; report both conditional editorial quality and unconditional successful-output yield.

## 13. Integration work packages

| Package | Implementation | Acceptance condition |
|---|---|---|
| M1: Freeze the control | Serialize existing route, model, evidence, prompts, and policies | Baseline is reproducible and cannot be silently upgraded |
| M2: Qualify role-based routes | Extend the existing model adapter/profile layer | Every seat records resolved settings, usage, and typed outcomes |
| M3: Share rubric and preserve isolation | Wire one audience definition to all seats; candidate-only cold context | Audience reaches every reviewer; hidden source does not reach cold review |
| M4: Build paired author and fixed-candidate reviewer runners | Persist manifests and stage outputs | One intended factor changes at a time; no silent truncation or fallback |
| M5: Complete editorial runs | Execute critic, repair, compile, render, and human evaluation | Results include actual videos and all failed/abstained cases |
| M6: Add media ablation | Qualify actual-media input with fixed preprocessing | Text-only and media-aided results remain distinguishable |
| M7: Promote and optimize | Version the selected profile; retain rollback and canaries | Promotion is backed by held-out evidence, not one persuasive example |

Integration search targets from the earlier review are `topic_editorial.py`, `topic_activities.py`, `topic_workflow.py`, `topic_runtime.py`, the cross-language topic contracts, and the existing budgeted model gateway. Inspect their current interfaces rather than invent replacement infrastructure or rely on old line numbers.

Do not couple this work to a broad authentication, billing, database, or UI rewrite. Only modify those boundaries when required to make the experiment or review decision correct and observable.

## 14. Minimum acceptance cases

| Case | Expected behavior |
|---|---|
| Specialist audience | Author and judges use the same expertise assumptions |
| Missing referent | Cold judge flags absent setup rather than inferring it from external knowledge |
| Qualified claim | Source judge preserves a meaning-changing qualification or rejects the misleading extent |
| Good long discussion | No arbitrary length rule rejects an otherwise publishable video |
| Uninteresting but complete exchange | Completeness passes while audience value is independently questioned |
| Good candidate control | Reviewer does not reject simply to appear conservative |
| Valid source IDs, invalid speech cut | Compiler failure is attributed correctly, without automatically blaming the author |
| One unsafe repair | Rejected patch does not erase the prior valid proposal |
| No worthwhile topic | Explicit abstention, not filler outputs |
| Oversized request | Explicit unsupported condition or separately evaluated complete-coverage path |
| Provider timeout | Unknown outcome recorded and reconciled, not silently redispatched |
| Partial structured response | Truncation is not accepted as a complete portfolio |
| Changed rubric/model settings | Editorial cache identity changes |
| Same media specification | Existing verified bytes can be reused with newly bound review decisions |
| Text-only reviewer | Cannot claim to have heard tone or observed video |
| Multimodal comparison | Records which media intervals were actually made available |
| Transcript instruction injection | Spoken instructions inside the podcast are treated as source content, not tool or publication authority |
| Unauthorized evaluation spend | Paid dispatch is blocked before a request is sent |

## 15. Cost optimization after quality is demonstrated

The first goal is a qualified quality reference, not an unlimited-budget system. Establish explicit development and per-run spend limits before inference.

Compare total cost per usable result, not just price per token:

```text
Total observed processing cost =
    all author and reviewer calls
  + admission corrections and editorial repairs
  + media analysis and rendering
  + billable failed attempts

Processing cost per accepted video =
    total observed processing cost / accepted video count
```

Report a zero accepted count as no accepted yield, not zero cost. Keep human correction time as a separate reported quantity; combine it with processing cost only using an explicit assumed labor rate.

Once quality is established, test lower-cost replacements one role at a time. Good candidates include nonbinding metadata extraction or well-calibrated review tasks. Cheap discovery must not become an irreversible filter that prevents a stronger model from finding missed topics. Retain access to original evidence and monitor recall.

Evaluate effort reduction, caching, narrower context retrieval, and selective escalation as separate experiments. A cheaper configuration that meets the product threshold can be preferable to a more expensive one. Conversely, lower token rates can be poor value when they create more repairs or rejected videos. These are product economics to measure, not assumed provider rankings.

## 16. Promotion record and definition of done

Produce one model decision record containing the tested source IDs and splits, frozen manifests, author and reviewer results, final media, human labels, failures, costs, uncertainty, and the recommended profile with its limitations.

The work is complete when:

1. At least the unchanged baseline and two capable alternatives have been evaluated under controlled conditions.
2. Reviewer calibration includes valid controls and source-grounded defects.
3. At least one promising configuration completes authoring through actual playback and human assessment.
4. Useful-topic recall, fidelity, independent comprehension, and editing effort are reported—not merely schema success.
5. Results and costs include failures, abstentions, and incomplete runs.
6. The promoted configuration has a versioned identity, explicit operating budget, rollback path, and regression set.

This establishes evidence for a particular workload and configuration, not perfection for every recording. Keep a failure taxonomy and expand the evaluation set using genuine user corrections without contaminating the held-out set.

## 17. Instructions for a coding agent implementing this plan

Read the current editorial-quality plan and inspect the actual harness before editing. Preserve existing Temporal/workflow semantics, source references, budget accounting, approval requirements, and deterministic rendering constraints.

Implement the smallest experiment-enabling changes first. Add deterministic adapter/contract tests without paid calls. Provide a diff, executed test results, remaining integration limitations, and the exact paid evaluation commands to be run only under explicit authorization. Do not invent benchmark results or claim model superiority from provider documentation.

Do not add agents solely to make the architecture look more sophisticated. When a stronger configuration already fixes an editorial failure, record that outcome before introducing another stage. When the failure is caused by absent evidence or an inexpressible operation, fix the harness instead of repeatedly changing models.

**Final engineering decision:** find the strongest practical editorial configuration, demonstrate the quality of its exported videos, and then reduce cost without losing that quality.

## 18. Official sources and verification notes

Sources below support provider availability, documented capabilities, and general evaluation guidance. They do **not** establish Temnia-specific model rankings. Verified September 11, 2026; catalogs and provider behavior may change.

- **[S1] OpenAI — Model selection.** Accuracy-first selection followed by cost/latency optimization. https://developers.openai.com/api/docs/guides/model-selection
- **[S2] Anthropic — Choosing the right model.** Capability-first and efficiency-first approaches, effort tuning, and application-specific comparisons. https://platform.claude.com/docs/en/about-claude/models/choosing-a-model
- **[S3] OpenAI — Models.** Catalog and documented model identifiers. https://developers.openai.com/api/docs/models
- **[S4] Anthropic — Models overview.** Current model lineup and Claude API identifiers. https://platform.claude.com/docs/en/models/overview
- **[S5] Google — Gemini API models.** Current catalog, model endpoints, and stable/preview labels. https://ai.google.dev/gemini-api/docs/models
- **[S6] Kimi — Quickstart.** Current Kimi K3 recommendation and identifier. https://platform.kimi.ai/docs/overview
- **[S7] DeepSeek — Models and pricing.** Current model identifiers, versions, and reasoning-mode support. https://api-docs.deepseek.com/quick_start/pricing/
- **[S8] Z.ai — Overview.** Current GLM model catalog. Provider comparison claims on the page are not adopted as Temnia evidence. https://docs.z.ai/guides/overview/overview
- **[S9] OpenAI — Evaluation best practices.** Task-specific evaluation, human calibration, and model-judge bias. https://developers.openai.com/api/docs/guides/evaluation-best-practices
- **[S10] Google — Video understanding.** Audio/visual evidence, processing choices, and sampling limitations. https://ai.google.dev/gemini-api/docs/video-understanding
