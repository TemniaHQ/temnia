# Chapter recovery state follow-up — 2026-09-09

The first same-run retry on the PR27 staging worker progressed beyond the seven retained
summaries. The UI nevertheless kept the old terminal error beside a running status and offered
$1.00 as the new maximum for a $3.00 run. The selector's initially stale accessibility option
was a browser observation artifact: opening the native menu showed the correct running status.
Do not change the run query for that observation.

## Current error versus retained history

Clear the current error atomically when an allowed retry moves failed/budget-paused work to
pending. Do the same when an allowed budget increase resumes a budget-paused run, while keeping
a failed run's error if only its budget increases without retrying. Clear any remaining current
error when a pending execution successfully claims running, so a command queued before this fix
cannot carry a stale failure into the new worker. Preserve every refusal fence, run/operation
identity, reservation, accepted artifact and review event; Temporal history retains the actual
prior failure. This is scoped DML, with no migration or workflow command changes.

Verify on Postgres that applied retry clears the current error, duplicates remain idempotent,
refused/unknown-outcome commands preserve state and error, a budget-paused resume clears its
current error, and the execution claim returns running with no stale error. Historical review
results must remain readable. Add a browser assertion for an actual retry path if the existing
recorded fixture journey can exercise it without inventing a new production failure mode.

## Budget drafts belong to the selected run

Initialize an existing run's budget field from that run's actual maximum, with exact decimal
representation supported by the existing parser. New-run input keeps its independent initial
value. Selecting another run must show that run's budget; polling the same run must not overwrite
a draft the user has typed. Switching between the new-run form and an existing run must not
transfer one form's budget into the other. Keep server/worker caps and explicit Raise budget
submission unchanged. Do not auto-increase, clamp, or submit a budget during initialization.

Use the existing components and state helpers. Verify initial existing-run display, same-run
polling with an edited draft, selection changes, and new-run/existing-run transitions through
Playwright. Cover an externally changed budget without overwriting a dirty draft if the current
refresh path can encounter it. No dependency or UI primitive is introduced.

## Delivery and live evidence

Implement on the new follow-up branch after PR27, while staging continues on its verified merge.
Never hot-patch or restart the in-flight worker. Record source/run/route/deployment identity,
original seven attempt/artifact comparisons, new known charges and any unresolved exposure.
Live technical checks and operational smoke acceptance are not independent human quality labels.
Preserve any later failure before applying a recovery command. Complete the exact-commit local
release gate and required GitHub check for the resulting PR; Rajesh owns merging.
