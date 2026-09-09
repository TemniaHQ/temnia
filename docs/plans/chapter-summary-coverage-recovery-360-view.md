# Recover a refused summary window without repeating paid work

Planned September 10, 2026 after the PR #29 staging continuation. Run
`a0f1913d-31ff-46b5-b470-7c4458f71b18` stopped before proposal at summary window 15 because
the model's sentence ranges did not form an exact cover. All 15 responses and charges are
durable: 9,397 micros spent, zero reserved, zero repairs. Fourteen grounding reports are
accepted. Its 217-event workflow completed; no edit, render or independent verdict exists.

## Decision and alternatives

Add a narrow, versioned whole-window source fallback for first-level summary coverage
errors, and let known planning refusals resume the same run at revision zero. Keep the model
wire, prompts, routes and logical operation identities unchanged, so all 15 paid responses
remain reusable. Preserve the first 14 accepted v1 grounding reports exactly.

The model's invalid partition is discarded as a whole. Code substitutes one unit containing
the complete original source window in order, with its actual endpoint sentence and word
IDs. This does not reinterpret the model's prose or invent chapter boundaries. A distinct
immutable diagnostic and UI warning expose the failed coverage and the source fallback.
Independent editorial verification and human review remain required.

Compared alternatives: repeating the window spends again and needs separate repair accounting;
adjusting only its endpoints falsely reattributes the existing prose; starting a new run repeats
accepted paid work; an end-boundary-only summary wire can prevent some interval errors but
changes request identity and requires fresh qualification. The source fallback addresses this
incident without any of those changes. Parallel summaries and cross-run source caching are
separate performance work and are excluded from this fix.

## Eligibility and limits

All existing source/evidence/response hashes, operation ownership, exact prompt reconstruction,
schema and input dependency checks run before recovery. Every model range endpoint must belong
to the actual source window, ranges must be individually forward, and every quote must be a
real anchor visible in that exact prompt. Foreign, out-of-window, unshown, malformed, missing
or unconfirmed data remains a refusal. Recovery applies only when these checks pass and the
remaining problem is gaps, overlap or ordering between otherwise valid ranges.

Recovery is first-level only. It discards all generated units in the affected window and joins
every complete source sentence with a space. No truncation, translation, sentence omission or
generated prose is retained. Empty text, missing source word anchors, or more than the existing
20,000-character unit cap remains a finite refusal. Higher-level reduction failures remain
refusals. The existing serialized-request/context limits, hierarchy depth, budget, dispatch and
repair caps still gate all subsequent model calls.

## Immutable compatibility and audit

Keep valid and quote-fallback results on `summary-grounding-v1`, with identical bytes, metadata,
fingerprints and dependencies. Only the whole-window recovery produces `summary-grounding-v2`.
Use an explicit typed diagnostic containing a stable reason, original unit count and coverage
counts, plus explicit whole-window fallback provenance. The report retains the exact raw
response, evidence, window, source-summary hash and replacement.

Readers and portable exports must accept mixed v1/v2 lineage and reproduce each report using
its recorded policy. Fingerprints bind the report's actual policy, not the current policy
constant. Old v1 bodies retain their original shape; no new default field is inserted into
their serialized form. Preserve duplicate-stage refusal: recovering the failed window must
not create a second report for any of the 14 already accepted windows. Evaluation distinguishes
whole-window coverage recovery from quote-anchor fallback and first-pass valid output.

No model activity input or workflow scheduling shape changes. Recorded completed validation
results continue replaying as recorded; add a Temporal patch only if implementation changes
workflow commands. Replay the retained 77-, 191-, 361- and new 217-event histories.

## Resume and user states

Enable the existing Retry command for `needs_review` only when revision is zero and no edit
exists. The backend must also refuse outstanding reservations or nonterminal/unknown physical
attempts. Keep source readiness/deletion, mutation identity, base revision and workflow ownership
checks. Under the same transaction, move this planning-only retry to pending/evidence and clear
its prior error; a resumed workflow claims ownership normally. Completed revisions, accepted
exports and unknown runs retain their current controls. Stale or duplicate commands cannot
start additional work.

Explain that Retry revalidates saved results and continues unfinished planning; it does not
guarantee that an unchanged refusal will disappear. Keep New run available with its separate
paid-work warning where applicable. Show a specific whole-window coverage warning, rather
than claiming there were zero mismatched quote references. The old unknown run remains
unchanged at 20,236 micros spent and 109,644 reserved.

## Verification and rollout

Pure tests cover gaps, overlaps, ordering, missing tail, exact text/anchors, unchanged valid
units, foreign/unshown inputs, oversize/empty source, and higher-level refusal. Persistence and
portable evaluation tests cover mixed policies, old bytes/hashes, repeated acceptance, altered
diagnostics, response/dependency tampering, and separate fallback metrics.

A real Postgres/Temporal continuation must reuse retained model responses, preserve accepted
v1 report identities and money/dispatch/repair counts through the recovered window, then
continue only the unperformed work. Test concurrent/duplicate/stale Retry, unknown attempts,
positive revisions and clean error clearing. Playwright covers the actual retry control,
visible warning and unchanged unknown-run controls in production images.

Apply the new policy offline to the retained failing response and evidence; record eligibility,
exact excerpt size, hashes and context admission without any provider call. Run the full
exact-commit gate and open one verified PR containing this plan and actual results. Rajesh
merges. After merge, verify staging images/config and resume the same $2 run once through the
authenticated browser. Preserve all existing budget and exposure; do not start another source
run or alter the old unknown attempt. Rendering and editorial quality remain unqualified until
the resumed run provides that evidence.
