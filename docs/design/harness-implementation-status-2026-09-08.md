# Temnia architecture and original-review status — 2026-09-08

**Delivery state:** PR #23 was merged as `555eb24`, the paired protocol-4 rollout completed, and a 151-minute recording passed one end-to-end transcription run in 11 minutes. [Measured evidence and limits](s2-staging-qualification-2026-09-08.md). PR #24 implements the [replacement design](../pipeline-architecture.md) on a separate branch. The complete recorded-provider chapter browser journey and three structural transcript journeys pass against real local workflows, storage and media. The corrected live checkpointed GPU path passed both short recovery and a 151-minute source. Exact-commit delivery is recorded by the PR's local receipt and GitHub provenance check; the runtime changes have not been deployed to shared staging.

The [checkpointed speech qualification](checkpointed-speech-qualification-2026-09-09.md) records the live builds, preserved failure, recovery proof, long-run timing/memory observations and limits. The new long path took 18m4.81s, slower than the earlier 11-minute combined run; independent recovery has not established a cost or latency win.

## PR #24 implementation progress

This is the current branch assessment, separate from the PR #23 baseline audit below. A focused
test is evidence for its named mechanism, not the whole product. The exact-SHA gate and required GitHub check establish delivery for the final commit.

| Original gap | Current branch implementation | Verification and remaining qualification |
| --- | --- | --- |
| I07 — recognition completeness | Independent hash-pinned Silero ONNX evidence, interval-union comparison, explicit unknown/disagreement results | Real 40-second and 151-minute detector runs completed. The long run found 8.87% unmatched detector speech against old raw recognition spans; this requires listening and is not confirmed word omission. |
| I17 — persisted evidence | Immutable evidence and dependencies, transcript identity/revision/hash, stable word lineage, source-byte fingerprint and exact track clocks | Shared strict contracts, actual scoped persistence/corruption tests and compiler tests pass. The complete browser journey reaches accepted export and a second run on the same source. Two scoped runs reuse one evidence artifact/storage entry; changed bytes, stable metadata and dependencies still conflict. |
| I18 — language and scale | Universal SaT evidence, preserved detected-language context, whole-sentence context windows, explicit size bounds | Non-English preservation and real Temporal multi-window hierarchy tests pass. The hierarchy has an eight-level bound and preserves exact original sentence ranges. This does not qualify every language or make an indivisible overlarge sentence processable. |
| I24/I25 — transcript editing | Phrase search; delete/insert/split/merge; revisioned speaker identity and labels; explicit speaker merge; historical captions; undo; drafts keyed by word identity | Pure/action and real PostgreSQL checks pass. All three structural browser journeys passed across the initial run and focused regression rerun: complete structural/speaker/history controls, two conflicting tabs and explicit deleted-target retargeting, plus deleting all 93 words and recovering from an empty revision. Production-image coverage is recorded by the exact-commit gate. |
| I28 — GPU memory/recovery | Separate recognition/alignment/diarization functions, single-use containers, reusable checkpoints, explicit OOM batch downgrade and partial telemetry | Controlled checkpoint/OOM/cancellation tests pass. A real CPU crash probe reproduced provider container restart despite retries=0 and verified admission refusal before repeated inference. The corrected live short test recovered an injected lost result with three physical dispatches before/after; the 151-minute run completed three isolated GPU stages without OOM. Sampled device peaks were recorded, but process peaks and live OOM recovery remain unqualified. |
| I31 — attempts, costs and progress | Physical-attempt ledger, atomic reservations/dispatch fence, known/unknown costs, failed/cancelled attempt records, remote IDs and sampled telemetry | Real PostgreSQL races and response-publication recovery pass. Real Temporal cancellation waits for both speech activities to finish cleanup before terminal settlement. Proven undispatched, lost acknowledgement and known-handle cancellation paths have separate tests. The nine live GPU attempts include the initial known failure and retain 11,625,003 micros of unknown-cost exposure. Invoice costs and invisible provider startup/restart costs remain unknown without billing evidence. |
| Missing chapter compiler | ID-grounded exact-cover proposals, joint monotonic boundary selection, rational media grid and shared boundaries, pure review mutations | Nineteen focused compiler tests pass, including post-quantization lexical and detector safety, alternative safe cuts and explicit review reasons. Candidate weights remain provisional. |
| Missing durable model transport | Pinned PydanticAI/Temporal request guard, full response artifacts before validation, qualified immutable route pools, independent verifier policy, bounded reservations and cassettes | Real plugin/replay probe and scoped model-persistence tests pass. No live gateway credential is configured, so transport/privacy qualification and model auditions have not run. |
| Missing rendered review/export | Real H264/AAC chapter renderer, captions, content-keyed reuse, strict decode and track-clock checks; revisioned browser controls | Actual NTSC/VFR, signed-start, delayed-audio, rotation, odd-size, corruption and process-cleanup tests pass. The complete chapter browser journey passes, including accepted export, retained output after edits/cancellation, approval without further model calls and a second run. Retrying an actual failed export also reached ready on revision 13 with dispatch count unchanged at nine. Production-image coverage is recorded by the exact-commit gate. |
| I16 — quality and economics | Scoped `export-bundle` and artifact-based reporting CLIs, human boundary tasks and scoped cost reconciliation; exact artifact/revision/check validation and complete attempt facts | Exporting an actual browser run captured ten synthetic attempts, current revision 14 and accepted revision 13 separately. A pre-evidence failed run reports an unmeasured source fingerprint. Human boundary labels, correction time, held-out recordings, actual invoices and matched-budget model comparisons remain unmeasured. |

The latest focused harness/evaluation batch passed 102 tests, including actual PostgreSQL
roles/races and Temporal workflows. The web tranche separately passed 170 unit tests; the
database isolation suite passed 41. These counts are not a combined final-suite total. The
[probe record](harness-foundation-probes-2026-09-08.json) preserves real service/detector evidence;
the [360-degree plan](../plans/chapter-workflow-followup-360-view.md) records the remaining work.

The corrected Python preflight passed **624 tests, with 11 skips**; a separate explicit model-loading
run then passed **all 11 model tests**. Shared contracts passed **27 tests**. The four browser
journeys passed together in **2.1 minutes** against the development server and a frozen worker
copy. These results do not replace the exact-commit production-image gate.

The first structural browser run caught two real regressions: undo changed default speaker
labels in captions, and successful structural edits retained unchanged word selections as
false unsaved drafts. Both were fixed. The complete failed journey then passed in 9.1 seconds,
including byte-identical original captions after undo. NLTK was also removed after preserving
its three used metric conventions against 18,228 oracle comparisons, all exact matches; the
attributed adaptation contains no model/file APIs.

The first recorded chapter run reached review with three checked renders and an explicit drop.
Browser integration found missing check dependencies, an accepted-output section without an
accepted revision, and shared player controls; those paths were corrected. A subsequent runtime
audit distinguished tested helpers from actual workflow paths, finding missing terminal-failure
and budget-pause transitions, cancellation during pending source preparation, and all-drop export
and resume handling. Actual Temporal/PostgreSQL regressions now cover those transitions; the
complete browser journey separately exercises correction, export and recovery. Long-source GPU
qualification passed as recorded above; production-image delivery is established by the exact-SHA gate. A tested helper alone
does not count as end-to-end completion.

## Historical PR #23 audit

The initial runtime-code audit below examined PR #23, `fix/s2-review-findings`, at commit `7135852`. No runtime code changed during that earlier validation tranche. The implementation work described above followed it in PR #24.

**PR #23 conclusion: the fourteen second-review fixes were implemented, but the complete original review and proposed architecture were not.** Much of the architecture existed in documents only. The original hardening plan explicitly deferred I16, I17, I24, I25, I28, I31 and language routing in I18. Sprint placement was an organizational choice, not a reason to call those items fixed.

## Baseline implementation versus architecture (PR #23)

| Area | Implemented | Still design-only, deferred or missing |
|---|---|---|
| S2 correctness | Normalization and interpolation, lexical preservation, overlap spans, scorer repairs, immutable substrate model loading, correction/retry races, storage accounting, Modal protocol/smoke, HLS inventory and gate cleanup | The fourteen follow-up fixes are enumerated in [the reconciliation](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/design/harness-review-and-architecture-2026-09-07.md#L498); partial original findings below still matter. |
| Source evidence | Transcript revisions, timing flags, substrate library and separate shot/thumbnail artifacts | No persisted unified evidence artifact or dependency/invalidation graph. Construction is used by [the evaluation report](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/evals/segment_report.py#L157). [The S3 spec](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L69) adopts persisted evidence. Source timebases/track mappings and separate ASR/alignment/diarization checkpoints are not implemented or fully covered by that first version. |
| Edit compiler | None | Joint boundary selection, shared chapter boundaries, safe-cut widening and review are [adopted in the spec](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L76). No compiler, canonical edit contracts or chapter workflow exists. |
| PydanticAI and durable model calls | Temporal/Pydantic media workflows | PydanticAI, the harness package, model-call tables, cassettes and harness CLI are absent. They are [planned](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L22), not part of [current dependencies/scripts](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/pyproject.toml#L6). |
| Planning and routing | Substrate candidates for a future planner | Full-context/hierarchical comparisons, independent-family verification, auditioned seat pools, bounded escalation/failover, prompt optimization and gateway qualification remain design work. Five artifacts with a variable number of model calls is adopted; no measured architecture comparison exists. [Seat design](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L55) |
| Budgets and attempt accounting | Accepted transcription usage is idempotent; known Modal handles are reused | No harness budget enforcement, atomic exposure reservation, model-attempt ledger or unknown-outcome settlement. Failed/cancelled GPU compute remains unmetered. [Current successful finalization](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/transcription/activities.py#L265) |
| Quality evaluation | Corrected Pk/WindowDiff and maximum-cardinality matching; density and truncation diagnostics | Human boundary windows, separate task evaluation, held-out sources, cost/accepted source-hour, correction minutes and model/architecture auditions remain missing. [Fixture limits](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/tests/fixtures/substrate/README.md#L45) |
| Transcript/studio editing | Durable drafts, conflict handling, recovery and timing labels | Phrase search, delete/insert/split/merge, versioned speaker identity, undo and boundary-review studio remain deferred. [Per-word search](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/web/lib/transcript/paragraphs.ts#L139), [edit contract](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/web/lib/transcript/edits.ts#L17) |
| Rendering/all lanes | Source preparation, preview media and transcription | Artifact-based editorial judging, the canonical multi-lane edit specification, selective regeneration and preview/export equivalence are not implemented. Wider lane fields, Remotion/TS rendering and OTIO are explicitly narrowed or deferred. [Amendments](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L156) |

## Original findings that must not be reported as fully closed

| Finding | Current status and evidence |
|---|---|
| I07 — invalid/truncated speech | Malformed output and missing alignment are fixed. Incomplete recognition versus genuine silence remains unchecked: [the normalizer still assumes an early ending means silence](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/transcription/normalize.py#L18). Independent speech-coverage evidence is absent. |
| I16 — production-quality evidence | Deferred. Small/synthetic fixtures do not establish editorial quality; annotated recordings and fair per-task evaluation remain required. |
| I17 — persisted substrate | Deferred; library/eval output is not a persisted product evidence version. |
| I18 — language/scale | Partly fixed: ceiling checked before embeddings and truncation reported. No language routing, long-sentence recovery or implemented hierarchical fallback. [The current refusal](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/substrate/changepoint.py#L226) recommends caller-side splitting/hierarchical processing. |
| I24/I25 — search/edit semantics | Deferred. Phrase matching still fails across words; the edit contract supports replacement and turn reassignment, not deletion/split/merge or versioned speaker identity. |
| I28 — memory envelope | Unqualified robustness risk, not a reproduced OOM. ASR stays referenced as alignment/diarization models load; batching is fixed and there are no intermediate reusable checkpoints or controlled OOM downgrade. [Model lifetimes](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/modal_app.py#L482) |
| I29 — real speech path | Mechanism implemented; short smoke, paired staging rollout and one 151-minute transcription now passed. Broader input/quality and failure-recovery qualification remain separate. See the linked staging report. |
| I31 — attempt cost and actual compute liveness | Still open. GPU timing is returned only on success. Temporal may retry `transcribe_source` under the same claimed attempt; failed compute is absent from successful finalization. Terminal failure updates state but adds no cost record. [Workflow](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/workflows.py#L254), [failure path](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/apps/pipeline/src/temnia_pipeline/transcription/activities.py#L289) |

The explicit deferral record is [the original hardening plan](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s2-hardening-360-view.md#L50). Known-handle reuse does not close an unknown provider submission acknowledgement; HLS presence/size and playlist hashes do not establish integrity against same-size binary media corruption. Those limits are already [documented](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/design/harness-review-and-architecture-2026-09-07.md#L515).

## Remaining specification inconsistencies

1. **Parallel calls versus budget reservations.** The workflow [fans out cut activities](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L94), but [defers atomic reservations because it claims no parallel dispatch](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L156). A pre-call check alone allows concurrent calls to spend the same remaining balance. Specify atomic bounded reservations or remove parallel dispatch from this version.
2. **Uncertainty state missing from the proposed schema.** `llm_call.status` lists only `ok | failed | replayed`, while the next paragraph promises `outcome_unknown`. [Schema and promise](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/plans/s3-harness-spec.md#L31). Define durable dispatch/settlement states consistently, and qualify the goal's unconditional zero-repeat claim to committed results.
3. **GPU accounting lacks an adequate destination.** I31 is deferred to the harness call ledger, but that ledger is specified for LLM requests/tokens. It does not define failed Modal resource accounting, reconciliation or remote compute progress. Add a provider-attempt record or generalize the operation ledger explicitly.

The audited review header's blanket statement that [“the defects are fixed”](https://github.com/TemniaHQ/temnia/blob/713585241af6e8dc55eafff27fde8f4d8e53a6bc/docs/design/harness-review-and-architecture-2026-09-07.md#L5) overstated the narrower implementation. This documentation follow-up corrects that header to distinguish fixes, partial mitigations and deferrals.

## Initial temporary-app smoke (before the later staging rollout)

The standalone smoke passed against temporary deployed app `temnia-media-smoke-7135852` in environment `staging`, exact source build `3f2c496c9aa90a16ed74f4eb35bd38ebe4513eac7b17e8b3a3eeb6f33bd9ed55`.

- Nine-second speech sample: 27 words, language `en`, one speaker, zero interpolated words.
- All expected words present.
- `gpuSeconds`: 29.2; measured wall time: 35.9 seconds.
- At this initial smoke, existing `temnia-media` remained protocol 3. The later staging report records its protocol-4 rollout.

This demonstrates the temporary deployed build's real short speech path. It does not establish a production rollout, compatibility of old work with protocol 4, long-source throughput, peak VRAM, failed-attempt economics or the completed editing harness.

The helper verified its own build and the actual GPU build, then the client checked the deployed identity again. Its storage-prefix deletion completed before it returned. The temporary app was stopped after the successful call; the original app was not redeployed. The remote log included a TorchCodec decoder warning; the current in-memory audio path completed successfully, so this does not qualify TorchCodec file decoding.

Returned smoke report:

```json
{
  "app": "temnia-media-smoke-7135852",
  "environment": "staging",
  "identity": {
    "protocol": "4",
    "build": "3f2c496c9aa90a16ed74f4eb35bd38ebe4513eac7b17e8b3a3eeb6f33bd9ed55"
  },
  "gpuBuild": "3f2c496c9aa90a16ed74f4eb35bd38ebe4513eac7b17e8b3a3eeb6f33bd9ed55",
  "words": 27,
  "language": "en",
  "speakers": [
    "0"
  ],
  "interpolated": 0,
  "gpuSeconds": 29.2,
  "wallSeconds": 35.9,
  "missing": [],
  "text": "Temnia cuts long recordings into chapters. This is the release smoke test for the transcription path. It should take only a few seconds on the graphics card."
}
```

`gpuSeconds` measures elapsed time inside the GPU function; it is not a retrieved Modal invoice or full billed-container lifetime. `wallSeconds` measures the deployed smoke helper after its initial setup. Neither number establishes cost per audio-hour.

## Completion order

The [follow-up plan](../plans/chapter-workflow-followup-360-view.md) starts with remaining S2 qualification and reliability, then builds the shared foundation and chapter path. Within that work:

1. Resolve the dispatch/budget/unknown-outcome schema contradictions and define a generalized provider-attempt ledger.
2. Persist versioned source evidence and canonical edit contracts, including uncertainty and correction dependencies.
3. Implement one complete chapter path: plan, jointly compile boundaries, render, verify and correct.
4. Qualify recognition completeness, language routing, long-source memory/recovery and economics on annotated recordings; add the remaining transcript editing tools.
5. Extend the proven evidence, compiler and review operations across the other editing lanes.

These are unfinished product and architecture work, independent of sprint labels. A passed S2 smoke does not close them.
