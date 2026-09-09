# Chapter summary grounding — 2026-09-09

The same staging run retained all 25 paid summary responses and then failed before proposal:
10 quote anchors across five windows belonged to neighboring sentences. All
window and unit sentence ranges form an exact cover; all offending anchors were visible in the
input. The raw response and expense records must remain unchanged. The window count is derived
from this source's 1,989 sentences and the configured 80-sentence limit, not a fixed 25.

## Decision and alternatives

Validate each summary immediately after its model activity. Where its sentence partition is
valid but its quote anchors are not, replace the entire affected unit's generated prose and
anchors with an exact source-text excerpt over that same verified range. Derive excerpt anchors
from the evidence's first and last word IDs. Preserve every unaffected unit. Record the rejected
anchors, original response identity and replacement in an immutable grounding report, and show
the use of source excerpts in the chapter UI. This is an extractive fallback, not proof that the
model's original summary was correct, nor a semantic model repair.

We compared resubmitting five windows, one larger model repair, silently trimming/reassigning
anchors, and source excerpts. Resubmission spends again and needs durable repair-round semantics;
a larger repair adds context risk; changing anchors while retaining their generated claims hides
the failure. Source excerpts are grounded by construction and require no additional model call.
The existing independent verifier and human review remain necessary for the chapter decisions.

## Inputs and boundaries

Source and evidence identities, response shape, ordered sentence ranges and exact coverage are
strictly validated before fallback. Foreign sentence IDs, gaps, overlaps, wrong windows, missing
source words or mismatched evidence fail visibly; they are never repaired into a plausible cut.
Quote IDs must be present in the actual prompt and owned by the declared unit. Fallback is
eligible only when a real, prompt-visible anchor belongs to another unit in the same window.
Foreign, inferred or non-prompt quote IDs remain a visible refusal. Never retain the ungrounded
prose while changing only its citation.

Excerpt text is joined from complete source sentences in order, without translation or
truncation. It must fit the existing 20,000-character unit bound. Oversize or empty excerpts
produce a finite review refusal, not a larger limit or silent omission. The ordinary context
estimator and maximum hierarchy depth still bound subsequent requests. Reductions validate
against their actual supplied anchors and preserve lineage back to original evidence.

## Persistence, retry and workflow compatibility

Do not change original summary prompts, model output schema, program version or logical operation
inputs. Previously accepted responses remain cached. Add a versioned Temporal patch around the
new post-response validation activity; histories without that marker retain the old command path.
Replay both retained staging histories offline before delivery.

The activity checks the run's pinned evidence and exactly one accepted model response for its
stage. Its immutable `checks` artifact uses a distinct `chapter-summary-grounding/1` format,
identifies run, window and hierarchy level, and depends on evidence, original response and any
prior-level grounding inputs. Its fingerprint includes the validation policy version and exact
inputs. Duplicate execution reuses the same artifact; publication failures retry without another
provider call. Downstream model operations explicitly depend on the grounding artifacts they
consume. Existing per-run budget, dispatch and model-repair limits remain unchanged.

## Failure, user state and operations

Validation has a two-minute activity deadline and ordinary bounded activity retries for storage
failures. Semantic refusal is returned as a value and moves planning to needs-review, preventing
further model dispatch. Cancellation, unknown outcomes and budget fences retain their current
behavior. No migration, dependency, credential, staging environment or model route changes.

The UI reports how many generated summary units were replaced with source excerpts, including
while planning is active or refused. It does not display a successful-grounding claim for rejected
model output. Reports remain inspectable through scoped artifact reads and portable evaluation
evidence; model audition numbers must distinguish fallback from valid first-pass output.

## Verification and delivery

Use content-free fixtures for neighboring, foreign and inferred anchors; preserve valid units
and original provider bytes; prove exact excerpt text and source ownership. Cover malformed
partitions, empty/oversized excerpts, reductions, repeated validation and cross-source evidence.
A real Temporal/Postgres test must show validation before the next summary, refusal stops paid
work, cached-response reuse, artifact dependencies and unchanged money/repair counters. Replay the
77-event original and 191-event resumed staging histories. Evaluate all 25 retained responses
offline with no provider calls and report fallback counts and resulting context size.

The existing recovery-error and budget-draft fixes stay in the same follow-up PR. Finish the
exact-commit local gate, both production images, browser assertions and GitHub required check.
Rajesh merges. Only then verify the deployed image and recover the same staging run; do not claim
render/export or editorial qualification from this planning-only result.
