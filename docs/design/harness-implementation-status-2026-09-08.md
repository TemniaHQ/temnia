# Temnia architecture and original-review status — 2026-09-08

**Staging update:** PR #23 was subsequently merged as `555eb24`, the paired protocol-4 rollout completed, and a 151-minute recording passed one end-to-end transcription run in 11 minutes. [Measured evidence and limits](s2-staging-qualification-2026-09-08.md), [new-PR implementation plan](../plans/chapter-workflow-followup-360-view.md). The architecture gaps below remain open.

Initial runtime-code audit of PR #23, `fix/s2-review-findings`, at commit `7135852`. This report records the completed live GPU check and separates implemented fixes from unfinished architecture. No runtime code changed during this validation session.

**Conclusion: the fourteen second-review fixes are implemented, but the complete original review and proposed architecture are not.** Much of the architecture is adopted in documents only. The original hardening plan explicitly defers I16, I17, I24, I25, I28, I31 and language routing in I18. Sprint placement is an organizational choice, not a reason to call those items fixed.

## Implementation versus architecture

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
