# Follow-up plan: complete and qualify the chapter workflow

2026-09-08. **Status: planned, not implemented.** This follow-up PR initially records staging evidence and this implementation plan. It does not claim the editing architecture is built, or that the component proposals below are accepted decisions. No runtime code is changed by this document.

## Current evidence

PR23 is merged; main is `555eb24`. The actual `temnia-media` app and staging worker now use protocol 4. Its deployed speech smoke passed: 27 words, 29.576 seconds inside the GPU function, and 37.4 seconds wall time. Function elapsed time is not an invoice.

The existing source is **9,060.473 seconds (about 151 minutes), 1.91 GB**, not tens of gigabytes. Its first attempt completed recognition, alignment and diarization under saved call `fc-01M20FWJ252T1KWHG1G9JG842W`: 20,587 words, two speakers, zero interpolated/malformed timings and 660.264 seconds end to end. The pinned-result consistency audit passed all 18 checks. See the [staging report](../design/s2-staging-qualification-2026-09-08.md) for measurements and limits. This source can establish long-duration behavior; it cannot establish tens-of-gigabytes ingest performance.

During recognition, alignment and diarization, displayed progress remained at 0%; writing eventually showed 100%. This is an observability finding, not proof of stalled computation. The successful short smoke does not establish the memory envelope, full cost, chapter quality or completed editing harness.

The fourteen second-review fixes have landed. Original gaps remain: incomplete-recognition detection (part of I07), editorial evaluation (I16), persisted evidence (I17), language/scale recovery (part of I18), phrase search/correction semantics (I24/I25), memory qualification (I28), and attempt accounting/compute progress (I31).

## 1. Complete S2 reliability and qualification

Preserve recognition, alignment and diarization as independently reusable checkpoints. A failed speaker stage must reuse completed recognition. Release completed-stage models or isolate their processes; measure whole-device/process memory as well as Torch counters so CTranslate2 allocations are included. Record effective batching and add a bounded, metered OOM reduction rather than repeating unchanged work.

Add independent speech-activity evidence and compare it with recognition coverage. Empty or prematurely ending recognition over detected speech becomes `needs review`, not asserted silence. Calibrate against music, noise, breaths, overlap and genuine silence; VAD itself is uncertain evidence.

Separate poller heartbeat, remote-stage activity and measurable completed work. Replace the observed persistent 0% with completed audio-window/batch progress where available; otherwise show the stage and elapsed time without invented percentages. Bound stages and retain explicit cancellation/unknown states.

Complete phrase search across words/punctuation, transcript delete/insert/split/merge with lineage and timing flags, and versioned speaker identity/undo. Transcript deletion must not implicitly delete media. Add language/token-limit routing, long-sentence handling and a bounded recovery path beyond KernelCPD's ceiling; unsupported inputs must fail clearly before expensive work.

Verify a complete long source, consecutive warm runs, failure after recognition, cancellation and retry. Record peak memory, all attempts, retained storage, stage durations and actual supported limits. Add cases for omissions, code-switching, token overflow and corrected captions. Investigate any additional staging findings before declaring this tranche complete.

## 2. Build versioned evidence, edit contracts and safe operation accounting

Persist immutable evidence keyed by source/transcript/speaker revision and model/configuration hashes: lexical word IDs, timing uncertainty, sentences, turns/overlap, speech activity, pauses, chapter candidates, shots and media timebase mappings. Concurrent builders publish one accepted version. Corrections create new versions; dependency records support selective invalidation while preserving historical results.

Define a canonical chapter edit contract containing ordered keeps, explicit drops, shared boundary references, source/output mappings, captions, provenance and review state. Keep counts/durations as steerable preferences rather than quotas. Preserve original source timebases; use rational time for compilation and explicit frame/sample quantization.

Use one operation/attempt model for **GPU, CPU, LLM and render work**, separate from customer usage. An attempt records physical dispatch identity, remote handle, resource/model route, inputs/results, state and estimated/measured/reconciled cost. Successful-result metering alone misses failed compute.

Atomically reserve bounded exposure before parallel dispatch. Settle idempotently against usage; retain exposure for `outcome_unknown` and cancellation awaiting confirmation. Checkpoint publication precedes accepting the database pointer. Reuse committed results; do not promise zero paid compute across a lost provider acknowledgement. Disable hidden retry layers; every repair/failover requires its own bounded attempt.

Correct the current spec's contradictions: parallel cuts require reservations, and `outcome_unknown` must exist in the proposed schema. Add forced RLS, scoped references and additive migrations through Drizzle. Test two connections competing for the final budget, duplicate settlement, every dispatch/publication crash window, stale workers and source deletion. Use no database transaction across external I/O.

## 3. Deliver the complete chapter editing slice

Implement typed model operations on Temporal with PydanticAI, a frozen auditioned route configuration, local validation, independent-family verification and bounded repair. Start with a combined global brief/proposal plus sparse visual evidence; investigate uncertain boundaries locally. Compare full-context and hierarchical approaches at equal cost instead of prescribing a fixed number of calls.

Compile neighboring chapter boundaries jointly using semantic fit, speech/pause/overlap and shot evidence. Enforce exact source coverage through keeps and explicit drops, with one shared boundary between neighbors. If no safe candidate exists, widen within limits or require review; never manufacture timing certainty.

Render source-aspect chapters and sidecar captions from the original master using that same edit map. Check full decode, duration, A/V synchronization, captions and boundary audio/images. Judge the rendered evidence with a qualified independent route; text-only review must not be presented as audiovisual verification.

Ship the review surface with the backend: contextual boundary audition, accept/reject with reason, shared-boundary nudge, restore drop, merge, undo and export accepted files plus `chapters.json`. Preserve accepted media during recomputation. Revision CAS and mutation identities protect simultaneous edits and lost acknowledgements; only affected outputs re-render/recheck.

This is the minimum useful vertical slice. Moments packaging, multicam, reframe, dubbing, general montage, templates and SaaS surfaces remain wider lane work. Extend contracts deliberately when those consumers arrive.

## 4. Establish honest editorial and economic evaluation

Use a private manifest of recordings, content hashes, permissions, language/type and source-level evaluation split. Reuse the existing master for engineering measurement. Select separate calibration and held-out recordings for editorial testing; do not tune and evaluate on snippets of the same episode.

Annotate acceptable semantic boundary windows, acoustically safe windows, alternate valid groupings, necessary context and severe omissions. Evaluate sentences, turns, proposals and final cuts separately. Retain corrected Pk/WindowDiff/matching diagnostics, but measure human correction minutes, unchanged/minor-change acceptance, severe context loss, technical failures, total cost per accepted source-hour and tail latency.

Compare full-context planning, global/local refinement and independent proposals under matched total budgets including visual inputs, reasoning and failed attempts. Apply the provisional 70% chapter acceptance target with explicit denominators/per-source results; do not claim universal quality from three recordings. Synthetic cassettes prove wiring, not editorial correctness. Human decisions are needed for human acceptance claims.

## Delivery, component checks and genuine dependencies

Implement sequential foundations—contracts/accounting, S2 evidence, planning/compiler, render/review—then qualify the complete path. Test concurrent inputs, empty/huge sources, cancellation, recovery, budget exhaustion and every UI state. Run scoped isolation tests, production-image Playwright and the exact-SHA local gate before verified delivery. Keep old workflow/protocol handlers compatible while active work drains; fail visibly on invalid runtime configuration.

Research supports examining [Pydantic Temporal integration](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/), [Silero ONNX](https://github.com/snakers4/silero-vad) and [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/). These remain implementation proposals: verify pinned APIs, request-guard placement and replay before adoption. Gateway budgets are [soft caps](https://vercel.com/academy/ai-gateway/set-a-budget), so they cannot replace reservations. Probe actual [Modal billing access](https://modal.com/docs/guide/billing) before assuming account-tier limitations; label unreconciled costs honestly.

Modal/HF/R2 setup already works and does not require renewed credentials. Gateway probe status is inconsistent across existing documents; inspect available credentials and saved evidence before asking for anything. A genuinely missing gateway key blocks live audition only. Inventory existing recordings before requesting more; keep transcripts private unless publication is authorized. Choose a finite cap within the authorized qualification scope, and obtain a further budget decision only if necessary. Build review tooling before requesting human annotations. Record remaining blockers explicitly; do not relabel a recorded-provider-only path as a completed chapter feature.
