# Topic repair admission and correction

## Problem and scope

Karma run `a562673b-6723-47b8-a425-476f061f44dd` proposed nine candidates and
withheld four. Its complete, settled repair addressed all four, but an extent-only
change used `replace_candidate`. The prompt suggested that label for extent plus
annotation changes, while the validator required a title or purpose change too.
Atomic refusal discarded the complete patch and stopped after one of three allowed
repairs. Offline replay against deployment-verified code admitted all four operations
after changing only that label to `replace_extent`.

Fix admission and recovery, without changing reviewer criteria, source authority,
physical safety, the model roster, or staging data. The rejected repair is not evidence
of editorial success: fresh review remains mandatory after every admitted edit.

## Decisions and alternatives

1. Treat a `replace_candidate` with unchanged title/purpose and a changed edge as
   the equivalent `replace_extent` in the existing in-memory normalization pass.
   Keep raw provider output/rejection records immutable. All existing ID, finding,
   source-window, physical-only and atomic checks then apply to the effective operation.
   Correct the prompt to explicitly distinguish extent/annotation changes from coupled
   title/purpose changes, and bump the patch prompt version. Annotation-only, no-op,
   foreign-ID and unauthorized title/purpose edits must not gain this equivalence.
   Merely adding another prompt warning leaves the measured label failure possible;
   broadly relaxing finding authority would admit unrelated edits.
2. Retry a settled, typed patch rejected by validation within the existing shared
   `maxRepairs` allowance. Retain its rejection artifact, feed the rejected patch and
   exact diagnostic into the next patch request, and bind that artifact as a dependency.
   Correction retains the same selection and assessment; it increments iteration and
   gets a distinct paid-call identity. Do not buy another source review of unchanged
   content. Once a corrected patch is admitted, re-review changed content normally.
3. Validate rejection artifact dependencies against the current source, rubric,
   selection, assessment and settled response before exposing it to a corrective
   request. Incorrect hashes in the rejected model output are themselves correctable;
   provenance comes from the request's retained artifacts. Clear correction context
   after admission. Unknown paid outcomes still stop; schema/transport failures retain
   their existing behavior. Exhausted correction allowance retains the last assessed
   selection and reports the final rejection diagnostic. A repeated valid semantic
   selection still stops through the existing no-progress rule.

## Inputs, execution and user states

- Multiple operations remain atomic; a malformed or unauthorized operation cannot
  partially publish its siblings. Empty/foreign/multiply-targeted operations keep
  their existing refusal rules. Repeated invalid repairs stop at `maxRepairs`.
- Correction adds no new unbounded loop, durable runtime, timeout, storage backend or
  source-wide payload. The rejected patch is included with the already-scoped source
  rows; existing prompt admission and output limits still apply to long sources.
- Cancellation, budget limits and unknown outcomes retain existing accounting fences.
  No blind model replay or manual replay of the inspected run is part of this change.
- Existing scoped artifact reads/publication retain organization/source identity;
  no new unscoped database or storage access is introduced.
- Review videos remain gated by current required findings. A successful correction
  clears its temporary rejection context; exhaustion names the refusal and leaves
  original assessments available. No UI component or new approval state is needed.
- No deployment/configuration change is bundled. Current code has no requirement to
  replay an older topic program generation; program identity freezes the new prompt.

## Verification and delivery

Synthetic regressions reproduce the actual two-edge extent-plus-annotation mistake,
retain raw input immutability, and prove title/purpose authority, physical-only limits,
and atomic refusal still hold. Connected workflow tests cover rejection → correction →
fresh review, no source-review duplication before correction, exhausted allowance,
distinct correction identities/dependencies, stale rejection refusal, and unknown
outcomes. Re-run the retained private patch offline without modifying its operation.

Run focused pytest, formatting and typing, then the repository's full exact-commit
local gate and verified PR workflow. Record actual checks and limitations in the
session log and PR; model or human editorial acceptance is not inferred from tests.
