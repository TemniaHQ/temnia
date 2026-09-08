# S2 hardening follow-up: complete the second review's fixes

Written 2026-09-08 before implementation, for the existing PR #23 and branch
`fix/s2-review-findings`, starting at `55af59b`. Scope is the fourteen residual
findings from the second review. The original hardening plan remains the baseline;
this plan specifies the additional failure cases and their verification.

## Changes and verification

| Area | Intended behavior | Proof |
| --- | --- | --- |
| Missing word times | Fill only within the applicable segment's known evidence; preserve lexical order and uncertainty | A 10–12 s unaligned segment between distant aligned words stays in 10–12 s; adjacent unaligned segments and partial anchors cannot blend across known boundaries |
| Raw word fields | Reject malformed word fields instead of coercing null/objects into words or silently dropping speech | Null/non-string/missing word fields fail; nonempty speech with an empty alignment list is preserved as uncertain text; explicit no-speech stays valid |
| Reader revision identity | Displayed content and mutations use the same loaded revision | Delay the new revision fetch after a turn merge; an edit cannot target a different turn by submitting old indices with a new revision |
| Remote execution outcomes | Remote function failure is distinct from poll timeout and client transport failure | SDK-serialized remote OSError/TimeoutError are terminal execution outcomes; a genuine lost status request retains its handle and does not duplicate work |
| Stale retrying state | Stale/missing liveness is considered before retry-stage wording | A 24-hour-old retrying row has a recovery action; null heartbeat ages from a reliable row timestamp |
| Retry ownership | Unknown Temporal status does not authorize replacement; database transition is conditional on the observed run/status | Concurrent Retry requests cannot reset a newly claimed row; control-plane uncertainty preserves active state; dispatch failure remains recoverable |
| Draft/save ownership | A save result cannot clear or replace another edit's draft | Delay save A and edit B; successful, rejected, and failed A responses preserve B; virtualization preserves the current draft |
| Retry errors | Network/server-action errors remain actionable in the panel | Rejected and uncertain launches show a recoverable message without losing the existing surface |
| Storage ledger | Finalizer and corrections reconcile one consistent revision/usage snapshot | Coordinate two real database transactions around a correction; stored-byte totals equal the ledger, including duplicate finalization |
| Deployed smoke | The smoke calls the exact deployed app/environment and verifies its build/protocol identity | A stale/wrong deployment cannot pass by running module-local ephemeral functions; runbook states deployment order |
| Gate cleanup | Remove only abandoned image tags; preserve shared images used by live gates | Same image ID with live and abandoned tags removes only the abandoned tag; no live Docker resources used in the unit test |
| Ladder artifacts | Validate the named artifact set, sizes, and required playlist references; generation/retry behavior handles stale objects explicitly | A missing segment masked by an equal-size extra object is rejected; missing init/rendition and changed sizes fail; valid retries recover |
| Model identity | Fetch/load/check/report one immutable snapshot from one effective cache | Different HF_HOME/TEMNIA_MODELS_DIR cannot validate the wrong cache; explicit revision arguments, loaded identity, and duplicate-pin consistency are tested |
| Segmenter parameters | Reject nonpositive min_sentences before expensive work | Zero/negative/fractional values fail without embedding; valid values still work |

## Inputs, scale, and time

Cover empty and malformed provider output, explicit music/no-speech, partially timed words,
overlapping segments/speakers, long pauses, two simultaneous edits/retries, delayed responses,
cancelled saves, duplicate activity delivery, and worker restarts. Preserve original timing
evidence where it is valid; inferred timing remains visibly uncertain.

The target remains a multi-hour source with tens of thousands of words and thousands of HLS
objects. Artifact validation must paginate, remain bounded, and heartbeat during potentially
long work. Model snapshots are fetched at build/setup time and loaded offline in workers.
Database locks cover only the necessary reconciliation/publication transaction, never a paid
model call. Lock acquisition and stalled workflow recovery must not create infinite waits.

Remote result framing is a protocol change if wire shapes change: bump the compatibility
identifier, document deploy-before-worker order, and fail clearly on an incompatible deployment.
Distinguish provider application failures from transport uncertainty without guessing from the
exception's message. A lost submission acknowledgment remains explicit uncertainty unless the
provider offers a verified reconciliation mechanism; do not claim exactly-once paid computation.

## User states and operations

Keep loaded revisions usable while a refresh is pending, but bind edit identity to those bytes.
Retain drafts on save failure and conflicts; never let an older response own a newer editor.
Explain that a workflow's status cannot be checked when Temporal is unreachable, and offer a
safe later retry. Failed/absent dispatch and no-audio states retain their distinct messages.

Keep organization scope and forced RLS on every access. No organization ID is accepted from
the user. Artifact keys stay inside the source's prefix. Cleanup acts only on explicitly owned
temporary files, rejected candidates, and abandoned gate tags. No source originals are removed.

Use the existing PR worktree and preserve other checkout changes. No merge is performed.
The real GPU smoke is a post-deployment verification step; its result is reported only if run
against the correct deployed build. Local deterministic tests cannot establish its live result.

## Delivery

Run focused Python/web tests and the new concurrency/browser regressions, then inspect the
combined diff. Update the decision record where protocol/cache/publication behavior changes,
the review disposition, runbook, and dated session log with actual results. Commit on the same
branch and run `pnpm push:verified`, which owns the exact-SHA local gate, push, attestation,
and GitHub required check. Never bypass a failing gate or fabricate a receipt. If environmental
issues block a check, resolve them within scope or report the precise remaining limitation.

The legacy lessons applied here are whole-artifact validation after apparent success,
idempotency across commit/acknowledgment windows, and keeping transport uncertainty separate
from failed work. The regression tests exercise each rather than inheriting a literal assertion.
