# Chapter proposal output and recovery — 2026-09-09

## Observed problem and intended behavior

After PR #28, the 151-minute staging run reused all 25 summaries and passed grounding. The
DeepSeek global proposal then exhausted 8,192 output tokens: 26,780 text bytes contained 19
complete sections and a partial twentieth, covering only 1,280 of 1,989 sentences. There were
1,053 quote IDs and 11,337 whitespace bytes. The Qwen repair repeated the prompt and limit
without the failure diagnostic, then reached the 300-second read timeout without a response or
generation handle. The old run remains fenced, with $0.020236 reported and $0.109644 reserved.

The new path asks for compact editorial decisions, derives internal section labels in code,
preserves exact source coverage and stores useful failure evidence before a bounded repair.
No chapter count or duration is imposed. An incomplete response at the output limit stays invalid;
code never completes a JSON fragment, invents the missing source partition or increases a budget.

## Decisions and alternatives

Use a new, internal native-output proposal schema. Each section retains inclusive source sentence
endpoints, keep/drop kind, title, reason and at most two supplied quote anchors. Titles are bounded
to 160 characters, reasons to 320, and the overall summary to 1,024. Section count has no editorial
quota. Section IDs are deterministic functions of ordinal and source endpoints, derived after
validation. The existing public `ChapterProposal` and all stored version-one artifacts remain
readable. The new typed output converts explicitly to that canonical contract.

Compared with merely increasing the token cap, this removes repetition from every request and
does not change the frozen route's advertised output capacity or spend ceiling. Compared with
silently trimming model prose or accepting a partial document, it validates the complete answer.
A substantially smaller boundary-only wire format and paginated planning remain candidates if
this bounded format still fails representative sources; neither is declared necessary without
measurement. Compact formatting is a prompt instruction, not a guarantee of provider behavior.

This uses PydanticAI's existing [native structured output](https://pydantic.dev/docs/ai/core-concepts/output/#native-output)
and local Pydantic validation; it introduces no new framework or provider. The installed SDK APIs
were checked at PydanticAI 2.40.0 and OpenAI Python 3.8.0. The internal wire schema is Python-owned;
the shared canonical TypeScript contract and its generated Python model are unchanged.

The proposal prompt requests compact JSON, short text and representative anchors, using exactly
the existing source evidence. A separate repair prompt includes a bounded, content-free diagnostic
and the rejected response's immutable identity, while asking for a complete replacement. It never
embeds a raw invalid response as trusted instructions. Repair context and cost are estimated after
adding feedback, before a repair is claimed or dispatched. Known provider rejection remains a
separate failover; ambiguous transport failure never enters semantic repair.

## Inputs, edges and provenance

- Empty, malformed, truncated, overlong, duplicate, foreign-ID, misordered and incomplete-cover
  proposals refuse. Labels belong to code; titles, reasons, endpoints, kinds and anchors are never
  silently edited. The existing deterministic compiler remains the coverage and timing gate.
- Preserve every paid raw response and its accounting first. A scoped diagnostic activity reads
  the exact accepted model operation for the run/stage, verifies its response identity and
  dependencies, and distinguishes output-limit, JSON/schema and compiler refusals. Missing or
  ambiguous operation identity refuses rather than selecting an arbitrary response.
  Reconstruct the operation's input/configuration hashes from its original request fields and
  retained request hash; require the exact evidence, grounding and repair dependency references.
- Diagnostic artifacts contain finish reason, usage counts, immutable references and bounded
  known schema field paths/error codes, never model text, input values or arbitrary exception
  bodies. They depend on the original response, evidence and explicit request inputs. Repair
  operations depend on the diagnostic and rejected response as well as their original
  evidence/grounding inputs.
- Portable evaluation bundles include optional diagnostic bodies and their verified immutable
  dependency facts, including failures before revision one. Old bundles remain readable. These
  report execution/schema outcomes only and never count a repaired result as first-pass success.
- Duplicate activity execution produces the same immutable diagnostic and no provider call.
  A refusal on the last allowed repair still retains its diagnostic and a useful final reason.
- The new schema/prompt/operation stages and workflow patch are versioned. Existing histories
  replay through the old agent and old request shape; summary prompts, schemas and operation
  identities do not change. New proposal work never aliases an old paid response to a new schema.

## Time, failure and operations

Keep SDK retries disabled, one durable dispatch per attempt, the existing 300-second HTTP request
limit, ten-minute model activity, 30-second heartbeat and configured repair/dispatch/budget limits.
Diagnostic I/O has a two-minute activity deadline and uses the existing bounded artifact reader.
Its retries only repeat deterministic validation/publication. No provider call occurs in the
workflow, web process or diagnostic activity. The worker keeps the gateway credential.

Do not add streaming or attribution headers as an unqualified patch. Installed SDKs can stream,
but early generation-ID persistence needs the entire stream's durable reservation, cancellation,
response and settlement lifecycle. Gateway tags are attribution, not documented idempotency or
proof of an exact response. That path needs its own tests and saved transport qualification.
Vercel documents early generation IDs for streamed responses in its
[generation lookup](https://vercel.com/docs/ai-gateway/observability-and-spend/custom-reporting#generation-lookup)
guidance, while [ZDR](https://vercel.com/docs/ai-gateway/security-and-compliance/zdr) deletes prompts
and outputs after completion. Neither attribution nor request logs can be assumed to restore a
missing response. The current PR makes no unsupported inference-idempotency claim.

The current unknown attempt has no recoverable output. Its reservation and state remain intact.
Cost settlement alone cannot recover execution. A separately recorded new run is new work and
does not reuse model operations across run IDs. Any later staging continuation retains old
exposure and stays within the existing $3 session ceiling: at this checkpoint a new run can have
at most 2,870,120 microdollars, before subtracting any additional admitted qualification expense.
No new paid request is made by this code-change PR.

## User states and tenancy

The chapter panel explains an unknown result and retained possible charge beside the existing
disabled Retry/cancel/budget controls. Starting a new run explains that it sends new requests under
a separate run budget while the old charge remains unresolved. Existing results and run history
stay selectable. No operator settlement or pretend-cancellation button is added.

All reads and writes use the existing seeded scope resolver, source/run ownership and forced RLS.
No DDL, role, secret, route, output-cap, GPU or shared-staging configuration change is required.

## Verification and rollout

Use generated, non-private fixtures for overlong quote arrays, malformed/truncated JSON, source
coverage errors, repeated diagnostics and distinct repair requests. Prove the new wire format
converts without modifying semantic fields and derives stable labels. Verify output-limit
diagnostics from the retained response privately without committing source content. Test repair
feedback, context-overflow-before-claim, family exclusion, one-repair stopping and the unknown
fence through actual Temporal tests. Replay the retained 77-, 191- and 361-event histories.

Exercise scoped diagnostic artifact/lineage validation against Postgres, and prove no second
model dispatch or model-attempt accounting mutation during inspection. Diagnostic storage still
uses the normal artifact metering path. Browser tests cover unknown-state
explanations, disabled actions and the new-run warning; existing chapter journeys still exercise
the canonical edit contract. The exact-commit full gate, both deployment images, production
Playwright and required GitHub check are the delivery gate. Rajesh merges the new PR.

After merge, verify actual deployment identities before a separately journaled live continuation.
Passing local tests does not establish full-source completion, rendering, listening, acceptance,
cost per correct or a winning model. Those remain separate qualification results.

The existing gateway qualification CLI gains an explicit `--proposal-wire compact` choice. The
default remains canonical, including historical journal/request identities. A compact session
freezes the wire choice in its limits, journal and report; its proposal uses the production compact
prompt, native output schema and explicit canonical conversion before checking the authored
evidence. Schema, prompt and request fingerprints distinguish the two formats. Resuming a journal
with a different wire refuses before any dispatch. This adds no stage, automatic live probe or
provider retry; existing dispatch, exposure, privacy and unknown-outcome fences still apply.
Tests cover old journals, changed-wire resume refusal and compact requests across three candidate
families with fake transport. A fresh compact journal is required for the post-merge live probe;
an earlier canonical receipt never qualifies this new wire format.

## Lessons carried forward

- Legacy failure amplification: replace an identical retry with one bounded, diagnosed repair.
- Legacy silent truncation: require complete output; preserve and report the incomplete result.
- Existing summary recovery: keep paid bytes and stage identities immutable, with explicit derived
  artifacts and no fabricated model success.
- Current unknown result: preserve exposure and distinguish fresh work from recovery; elapsed
  time, a charge alone and an attribution tag are insufficient execution proof.
