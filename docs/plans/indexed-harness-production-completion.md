# Finish indexed topic production

Status: paused at Rajesh's explicit 2026-09-15 stopping boundary: finish the current batch,
get the pipeline tests green, commit, and stop. Owner: the current harness session, without
delegation. The broader production request remains unfinished; this batch is not a release claim.
The [batch record](../design/indexed-harness-recovery-2026-09-15.md) distinguishes implemented
recovery from the remaining work below.

## Required outcome

A valid long source creates bounded, resumable work. Ordinary tool mistakes, settled invalid
answers, transient provider failure and an oversized initial assignment get a recovery attempt.
Source identity, safe speech-preserving cuts, independent review and unresolved paid execution
remain explicit safeguards. No unlimited input, cost, latency or editorial-success promise follows.

## Completion ledger

| Area | Starting state | Required completion |
| --- | --- | --- |
| Tool observations | Fixed in b488871 | Keep regression proof at actual model boundary |
| Coverage after eviction | Fixed in b488871 | Add bounded treatment of unusually long individual sentences/candidates |
| Technical correction | Three attempts; SDK corrections | Carry progress, expose actionable exhaustion, test sibling drain |
| Dense source planning | Refuses >16 candidates/>48 opportunities | Adapt assignments and page structured context without losing authority |
| Connected repair | Refuses large components | Stage bounded operations while validating the final coupled transaction |
| Reviewer independence | All contributors excluded | Reserve a reviewer before parallel authors; remap fallback positions safely |
| Editorial resume | Partial artifacts retained; compiled revision resumes render | Resume the unfinished editorial phase with admitted work reused |
| Workflow history | All model rounds in parent | Bound history using Temporal children and continuation with immutable checkpoints |
| Candidate structure/reconciliation | Prompt obligations and local inventories | Source-linked internal observations and cross-section reconciliation |
| Product integration | Web starts V3 | Current indexed program from start, retry, queries and recorded image journey |
| Release evidence | Local gate passed fb67422 | Fault-injected long-source runs, real provider exercise, documented editorial/playback limitations |

## Batch boundary, 2026-09-15

This batch adds paged structured reviewer context, segmented inspection audits, exact character
fragments for oversized sentences, per-decision child continuation, admitted-decision reuse,
editorial progress across review renders, reviewer-family reservation and V7 product wiring.
Incomplete connected-repair staging was removed before closing the batch. Its existing authority,
component and operation limits remain. Typed internal-topic subdecisions, cross-section
reconciliation, parent-history continuation, actual Temporal continuation fault tests, the V7
image/browser journey and real long-source editorial/playback evaluation are still outstanding.
Do not interpret pipeline test success as completion of those items.

## Implementation order

1. Make structured review planning adapt rather than throw; turn remaining technical argument
   choreography errors into corrective observations. Add counterexamples before moving on.
2. Add durable editorial progress and bounded workflow execution; ensure retry resumes editorial
   work even when an earlier review render exists. Preserve settled request identity and unknown
   expense fences.
3. Complete oversized source/candidate and atomic repair handling, with typed internal evidence and
   cross-section reconciliation. Do not replace a context limit with unbounded metadata in prompts.
4. Integrate the current program into the one product action and its complete image-level journey.
5. Exercise long sources and failures, run the exact-commit gate, document release evidence and
   remaining user-dependent publication measurements. Do not label synthetic success human acceptance.

## Runtime research

Retain Temporal as the durable runtime and PydanticAI for the request/tool loop. Primary references
reviewed in this session: [Temporal Python SDK](https://github.com/temporalio/sdk-python),
[Continue-as-New](https://github.com/temporalio/documentation/blob/main/docs/develop/python/workflows/continue-as-new.mdx),
[PydanticAI history](https://pydantic.dev/docs/ai/core-concepts/message-history/), and
[Temporal integration](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/).
In particular, child workflows isolate histories; Continue-as-New bounds a continuing workflow;
large dependency/history objects remain the application's responsibility. Tools cannot use local
mutable state as durable workflow state. This design therefore uses immutable artifact identities
and explicit progress transitions, not a second persistence framework.
