# Complete standalone-topic selection and qualification

September 11, 2026. Implementation plan following Rajesh's review of the supplied
editorial-quality engineering and model-selection/evaluation plans. Rajesh confirmed
that both missed worthwhile discussions and weak selected discussions are the highest
priority, and authorized this work. This document records the complete change boundary
before implementation; it is not an editorial acceptance result.

## Problem and outcome

The current author already receives all original source sentences and an appropriate
standalone-video objective. Its only durable editorial output is its selected candidates.
Critics assess that set, and the loop can finish when those candidates pass. Source review
cannot return a structured missing opportunity, value has no separate assessment, and
human review records decisions without structured corrections. Therefore a complete,
faithful subset can still miss the best discussions or include weak choices.

Complete the decision cycle: shared audience -> source-linked opportunities -> candidate
treatments -> isolated value/comprehension and source/portfolio assessment -> authorized
portfolio correction -> physical production -> exact-revision human decisions/corrections
-> independent selection evaluation. Preserve one Temporal runtime and the existing
evidence, ledger, source clock, rendering, storage, tenancy and revision foundations.

## Versioning and ownership

- Add `standalone-topics/2` and `TopicSelectionWorkflow`; retain the v1 workflow, agents,
  prompts, schemas, request identities and artifacts unchanged.
- Separate supported topic-policy family membership from exact generation admission.
  Both generations remain visible/reviewable/exportable in topic views and excluded from
  navigation chapter actions. A request key cannot cross generations.
- Preserve `TopicEditSpec`, renders and export formats where physical semantics are
  unchanged. V2 discovery and assessment artifacts have new identities; dispatch their
  readers by the frozen policy, never label a v2 paid answer as v1.
- Keep Zod as owner of shared schemas; regenerate Python. New native-output agents have
  distinct names and disjoint workflow registration. Existing model/accounting adapters
  remain the only paid path. Exact request-shape qualification is required for new schemas.
- Preserve old pending browser intents. New intent identity includes its policy/workflow;
  v1 retains its exact old memo calculation. Deployment qualification controls enabling
  the new path; no old route probe is evidence for a new request shape.

## Editorial contract

The shared v2 contracts live in `packages/contracts/src/topic-selection.ts`.

1. `TopicEditorialRubric`: version, audience description, assumed domain knowledge,
   viewer goals, language policy, focus, exclusions and original instructions. Initial
   defaults are deterministic (interested audience, no prior episode knowledge, preserve
   original language and meaning). Explicit free-form refinements remain verbatim and
   reach every seat; do not pretend a model inferred structured audience facts. Persist
   the rubric and hash before editorial calls. Cold review sees this rubric, not source
   answers, author reasons, outside sentences, or other candidates.
2. `TopicOpportunity`: stable ID, viewer purpose, core/value evidence, dependency spans,
   candidate IDs, and a reasoned disposition (`proposed`, `not_useful_for_audience`,
   `not_contiguously_extractable`, `needs_evidence`). These are source-grounded hypotheses,
   not human labels. Unfinished execution is recorded by code, never disguised as low value.
3. `TopicSelectionDraft`: existing `TopicProposal` plus the opportunity inventory. Every
   proposed candidate must represent a named opportunity; references and memberships are
   grounded. Unselected opportunities remain inspectable. No output count/duration quota.
4. `TopicValueReview`: reconstruct viewer purpose and takeaway from selected speech;
   separately assess reason to watch, delivered value, opening effectiveness and focused
   development using cited pass/fail/unknown judgments. Add this to v2 cold review without
   changing v1. Opening/focus preferences are distinguishable from fatal fidelity or
   comprehension failures and do not become universal duration/hook rules.
5. `TopicPortfolioReview`: source judgments for every current candidate; reasoned
   selection dispositions (`select`, `decline`, `unresolved`); opportunity representation
   judgments; original-source-grounded missing opportunities; and structured findings.
   Run even for an empty author selection on nonempty evidence. Missing-opportunity
   findings are permitted to introduce new opportunity IDs, not foreign source IDs.
6. `TopicSelectionFinding`: stable identity, kind, severity, affected candidate/opportunity
   IDs, evidence spans and reason. Source findings can authorize a compound affected set;
   cold findings cannot invent outside evidence. Code verifies references and authority,
   not the semantic truth of a model's interpretation.
7. `TopicSelectionPatch`: exact base selection/evidence/rubric identities, finding IDs,
   and atomic operations (`extend_start`, `extend_end`, `retitle`, `merge`, `split`, `drop`,
   `add_opportunity`). Operations name affected candidates and full replacements plus
   opportunity updates. Changes outside the grounded affected set refuse. No passed
   neighbour may be modified without explicit compound authority. New candidate identity
   and parent lineage are retained. Rejection of a patch preserves the prior valid state.
8. `TopicSelectionAssessment`: exact source/rubric/draft and response lineage, complete
   cold/source observations, findings, opportunity/selection dispositions, and execution
   completeness. Missing reviews remain unknown. Terminal unresolved work remains visible.

Inventory and candidate construction may share the initial author call. Existing source
review is extended for omission and portfolio decisions. A separate blind discovery call,
routine alternative search, detailed relationship graph, hierarchy and multimodal inference
are later measured variants, not prerequisites or new framework requirements.

## Workflow, recovery and physical production

- Freeze evidence/rubric/seats, author a selection, validate all source references, review
  every candidate cold, and review the source/portfolio even when there are zero candidates.
- Reserve the independent reviewer family before authoring. Authoring and repair cannot
  consume it. The complete request identity includes program/prompt/schema/model settings,
  rubric and exact evidence/artifact dependencies, including at retained-answer admission.
- Construct findings only from grounded observations and deterministic physical evidence.
  Opportunity omissions, declined valuable treatments and authorized compound changes can
  reenter authoring through a patch. Re-review changed inputs and portfolio decisions.
- Stop when the known opportunities have reasoned dispositions and no actionable finding
  remains, a semantic proposal repeats without improvement, required evidence is unavailable,
  or existing configured execution guards stop work. Distinguish complete abstention,
  unresolved selection, failed admission and execution-limited work. Add no arbitrary
  experimentation-spend cap. Preserve unknown-outcome fences and all charges.
- Schema/source admission correction is distinct from editorial repair. Retain malformed
  responses and aggregate source diagnostics. Invalid patches do not overwrite the last
  valid assessment or prevent reviewable prior work from being retained.
- Compile selected and unresolved reviewable candidates; preserve explicit declined
  candidates and their reasons in editorial artifacts. Decline is not human rejection.
  Human acceptance remains authoritative over editorial concerns, subject to technical checks.
- Where sparse boundary candidates block otherwise valid content, deterministically derive
  feasible rational output-grid cuts from lexical membership windows intersected with
  permitted acoustic regions. Version derivation and reproduce it during validation; never
  silently alter frozen evidence or weaken word membership. V1 remains reproducible.
  No feasible instant is distinct from unknown acoustic evidence or an incomplete inventory.
- Reuse the existing source cache lease and owned heartbeats through preparation, render,
  checking and publication. No provider redispatch follows a transport-unknown result.

## Human correction and feedback

Add a separate `TopicEditorialPatchInput` and finite `TopicEditorialPatchWorkflow`, with
`action: topic_edit`, version, scope/source/run/mutation identity, base revision/edit/evidence
hashes, reason, optional measured active correction time, and atomic operations. Human
operations are `adjust_extent`, `retitle`, `add`, `drop`, `merge`, `split`. Add only this enum
value through Drizzle; reuse existing run locks, revision CAS and replayable command outcomes.

Replacement candidates remain source-sentence-bound. The editor explicitly supplies core
and completion spans when adding/splitting or removing previously annotated content; do not
invent completion annotations from the final sentence. Existing valid annotations may remain
as hypotheses. Human-origin proposals are published separately from paid model responses.
Changed extents/titles lose their old applicable judgments and human acceptance; portfolio
changes invalidate portfolio review. Unaffected exact clip-local observations can survive.

The focused UI supplies verified source sentences, selected and adjacent context, title and
extent controls, add/merge/split/drop, reason and accept/reject. Compose existing shadcn/Base UI
primitives. Persist pending mutation identity, retain a draft on CAS conflict and never silently
rebase it. Source context is loaded through the existing scope resolver and artifact checks.
Title-only changes reuse identical encoded bytes; all reused checks/descriptors bind to the
new revision correctly. Automatic analysis never becomes human acceptance.

## Evaluation and model selection

Implement offline `temnia-eval topics validate/report/compare` and scoped read-only
`temnia-harness export-topic-bundle` using existing artifact/expense conventions. Keep old
chapter evaluation unchanged. Records include frozen source/rubric/program/model identities,
independent source-level opportunity labels, exact output judgments, semantic opportunity
matching, candidate trajectories, repair/regression events, actual-media inspection, human
corrections and all paid attempts/unknown expenses.

Report separately: editorial precision, worthwhile-opportunity recall, successful delivery
and source completion, invalid among approvals, invalid approved among human-invalid cases,
false rejection, repair success/regression, severe fidelity defects, redundant core, human
effort, actual total expense and cost per accepted distinct video. Every rate has explicit
counts and undefined/missing-data handling. Attribute source opportunity loss to discovery,
selection, repair, compilation, rendering or human rejection only with corresponding evidence.
No output is not perfect precision; no worthwhile opportunities makes recall inapplicable.

Comparisons freeze their intended changed factors. Separate fixed-program model swaps from
best-qualified configuration comparisons and fixed-candidate reviewer calibration. Include
valid reviewer controls. Hold out complete recordings and identify overlapping-viewer exposure.
Baseline and every finalist must complete the same production path before a production winner
can be claimed. Provider documentation, schema tests and synthetic fixtures are not that result.

The retained external Karma author response is not a settled Temnia database operation.
Support a hash-verified retained-author diagnostic record/continuation manifest that preserves
its original HTTP/model/provider/request/receipt identity and historical cost; later reviewers
are separately identified work. Never inject it as a different route's cached response or count
it as a fresh author call. Paid execution uses qualified existing adapters and authorization;
preparation/reporting does not dispatch inference. No arbitrary model/vendor default is added.

## Edges, operation and validation matrix

| Concern | Required behavior and evidence |
| --- | --- |
| Empty transcript/empty author output | No ungrounded inference on empty transcript; independent source challenge for empty proposal; explicit abstention versus incomplete discovery tests. |
| Long/multilingual/oversized input | Preserve every original sentence and language; byte/context/output admission before dispatch; unsupported is explicit, no truncation; fresh full-recording qualification remains required. |
| Repeated/foreign/inconsistent references | Aggregate diagnostics, atomic refusal, exact prior state preserved; core/context/completion and opportunity membership tests. |
| Cold isolation/audience change | No source answer leakage; same rubric to all seats; rubric change invalidates cache/admission; exact-input tests. |
| Portfolio repair | Omission add, justified overlap, duplicate core, compound merge/split, unchanged candidates, invalid patch, repeated no-progress and unknown-only finding tests. |
| Concurrent/reentered/stale human edit | Full command identity, same-mutation replay, revision/hash conflict, cross-source refusal, draft retention; real scoped DB transaction tests and browser journey. |
| Cancellation/timeout/worker restart | Existing reservation and unknown-outcome fences; no repeated inference; finite command outcomes and registration/replay compatibility tests. |
| Physical safe cuts | Fractional grid, source offsets, overlapping speech, interior feasible instant and genuinely infeasible interval; old compiler validation unchanged. |
| Render/accept/export | Actual independent files, checks bound to current revision, unchanged byte reuse, invalidation of old acceptance; production-image browser journey. |
| Evaluation | Exact denominator/unknown tests, held-out/source pairing, no label leakage, stage-loss evidence, original retained receipt identity and no inference from report commands. |
| Delivery | Regenerate/check contracts; focused relevant tests; full local gate for clean commit; verified PR with this plan, exact results and remaining live qualification limits. No direct main commit/push or automatic merge. |

## Implementation ownership and order

1. Freeze this complete design and shared interfaces; copy the two supplied source plans into
   repository documentation as proposed supporting inputs, not executable authority.
2. Implement shared contracts and pure editorial grounding/patch logic; in parallel implement
   versioned workflow/integration, human correction surface, and standalone evaluation against
   those interfaces. Coordinate schema generation and uv use serially.
3. Integrate exact receipt/qualification paths, physical feasibility support and compatibility.
4. Independently review the connected implementation and exercise full production-shape tests.
5. Produce the concrete live comparison manifests and run authorized eligible evaluations;
   report missing qualification/access/human labels honestly rather than manufacture acceptance.

Incidental unrelated defects remain in the deferred backlog. Implementation completion and
real editorial acceptance are separate recorded outcomes throughout this work.

## Implementation decisions confirmed during integration

- The persisted v2 feasible boundary ID records its canonical rational instant. This is necessary
  for a valid audio sample inside a narrow safe interval that integer milliseconds cannot express.
  `timeMs` remains an approximation; deterministic validation reproduces every derived candidate.
  The shared compiler filters eligible choices without deleting candidates from immutable evidence.
- Exact gateway admission is a manifest over retained report/response byte identities, the frozen
  route snapshot and the run's effective output setting. It checks the actual strict native schema
  against the pinned SDK's production schema conversion. It does not alter old route snapshots.
  The new suite uses operator-declared execution limits without importing the historical suite's
  fixed experiment ceilings. Unknown outcomes retain the existing reconciliation fences.
- A substantive repair incorporates every newly discovered opportunity into the next inventory,
  including omissions it did not yet treat. A no-op preserves the current draft and its assessment.
  Physical-only extensions preserve semantic annotations. These are selection-integrity fixes,
  covered by focused adversarial tests.
- Editorial observation and publishable-media acceptance have different denominators. A faithful,
  useful treatment can pass editorial assessment while rendering or playback remains incomplete;
  that cannot qualify unattended publication. The evaluator records both scopes explicitly.
- The local production-image journey exercises v1 and v2 through generation, actual independent
  media, human acceptance, title correction, invalidated acceptance, reacceptance and exact export.
  Its model judgments are explicitly synthetic. The retained Karma diagnostic makes no inference,
  creates no substitute database operation and leaves downstream quality unmeasured.
