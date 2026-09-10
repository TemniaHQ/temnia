# Chapter render liveness — 360-degree view

Written before implementation, 2026-09-10. PR #30 is merged and deployed. The
same stopped long-source run remains untouched while this defect is addressed.

## Problem and scope

`render_chapter_revision` has a 30-second Temporal heartbeat timeout. Its encoder
reports progress, but source hashing, artifact upload/download and the strict
full decode can await work without any heartbeat. Full decode alone permits one
hour. Another concurrent encoder can conceal the gap until the last section or
an artifact-reuse attempt. This is a code-confirmed defect, not a newly observed
staging render failure: that run has not reached rendering.

Use the existing owned `run_with_activity_heartbeat` supervisor around the entire
render activity operation, including initial scoped lookup, cache lease,
render/check/publication and cleanup. Keep heartbeat lifetime tied to that owned
operation: success, failure and repeated cancellation drain its children before
returning. The heartbeat describes activity liveness, not evidence of media
progress. Existing encoder progress, revision fences, resource checks and
subprocess cleanup remain.

Compared: passing progress only into strict decode would miss storage and cached
hashing; increasing the heartbeat timeout would retain quiet cancellation gaps;
splitting the render into new durable stages would change history and delivery
scope. Reuse the already tested speech/evidence supervisor. The current
[Temporal Python SDK](https://github.com/temporalio/sdk-python#heartbeating-and-cancellation)
documents that asynchronous activities receive task cancellation and need
heartbeats for non-local cancellation delivery. This extends the September 9
whole-activity liveness decision to the renderer; it selects no new runtime,
provider, model, codec or dependency.

## Inputs, concurrency and recovery

- Exercise quiet preparation and the last section's check/publication tail even
  when no encoder emits progress. Empty/all-drop inputs still follow their
  existing checks and return behavior.
- Keep the per-run filesystem lease, revision-specific workspace, two-section
  semaphore and sibling TaskGroup. Owned asynchronous children and ffmpeg cleanup
  must drain before lease release or workspace cleanup. A concurrent attempt
  waits under heartbeat supervision.
  Root review also found cleanup outside the acquired lease: a cancelled waiter
  could delete the same revision's active workspace. Move workspace cleanup
  inside the acquired lease and test a cancelled waiter against a real held lock
  and an existing owner's file. Keep failure expiry under its existing lock guard.
- On failure or cancellation, retain immutable accepted artifacts and preserve
  retry fingerprints, scoped operation records and source-cache expiry behavior.
  Mark the operation successful only after cleanup and lease release succeed;
  a context-exit failure must still re-arm cache expiry.
  No request, contract, workflow command sequence or model identity changes.
- Heartbeat transport failure must cancel and drain the render operation. No
  success may escape if cancellation arrives during cleanup.

Existing native `asyncio.to_thread` file hashing is read-only and can finish
after its awaiting coroutine is cancelled; draining an asyncio task does not
stop that native thread. The cancelled coroutine cannot proceed to publication
or database acceptance. This patch does not change that existing behavior or
claim to stop every native file read before lease release.

## Scale, failure and time

The staging master is 1,911,080,861 bytes, 9,060.473 seconds, 1080p25 with stereo
audio. An all-kept edit requires 6,806,984,407 free scratch bytes under the
existing estimate; this is not a worst-case CRF output-size guarantee. Keep the
512 MiB encoder disk floor and source-download quiet deadline.

Do not change the 30-second heartbeat deadline, five-hour activity limit,
four-hour per-encode limit, one-hour strict-decode limit, 60-second ffprobe limit
or three-attempt retry policy. Storage retains its existing client deadlines;
the whole activity is bounded by its five-hour Temporal deadline. A timer is
not a new storage stall detector, and healthy heartbeat alone is not progress
or completion. This patch closes false liveness failures without claiming
better render throughput or broader media-quality checks.

## User states, operations and scope

No UI or environment change is required. The existing running, failed, cancelled
and review states remain, as do authenticated browser Retry and the scoped
control workflow. Every read/write continues through the existing scope and
revision checks. A deployment uses the verified PR pipeline; it changes neither
the route snapshot nor the current $2 run cap. The older unknown-outcome run and
its reserved charge remain untouched.

Legacy lessons applied: whole-activity liveness from the measured speech retry
is extended here; immutable artifacts and paid-result reuse are retained;
short happy-path renders are supplemented with an actual Temporal quiet-phase
test. No legacy implementation is imported.

## Verification and rollout

1. Add an actual Temporal test through the production render entry with a quiet
   interval longer than its shortened test heartbeat limit. Require one attempt
   and successful cleanup, including quiet preparation and a last-section tail.
2. Cancel during quiet work and inject heartbeat failure. Prove owned work and
   sibling cleanup complete before lease/workspace release, no leaked heartbeat,
   correct cache expiry and original failure/cancellation propagation. Existing
   real-ffmpeg tests retain process-group cancellation and partial-file cleanup.
3. Exercise the real render/check/publish call path with quiet I/O seams so a
   wrapper-only test cannot miss a path outside supervision. Existing rendering,
   artifact-integrity and reuse tests must pass without fingerprint changes.
4. Run the exact-SHA local delivery gate and required GitHub checks. Replay the
   retained production histories; no workflow scheduling changes are intended.
5. After Rajesh merges, verify actual deployed worker identity and resume the
   same saved run once through staging's Retry button. Compare its original 15
   paid results and 14 v1 reports before/after; account separately for unfinished
   calls. Observe the exact new workflow owner, not the old terminal execution.
6. Preserve exact-cover edit and artifact lineage, render attempts/timeouts,
   per-section technical checks, charges and unknown exposure. Technical success
   and a text verifier are separate from human listening, visual review and
   editorial acceptance; none is inferred from this patch or a passing gate.

## Results

PR #30 serving verification completed at 2026-09-10 01:33 UTC. Both deployments
name merge `8c26cfb1c9d28be879244566560a9e55a33472ff`, are healthy and have zero
restarts. Pipeline image `sha256:841cffc954e24064cdb0c2e0f30ec3b4fabac6c08e4d65ecba727af7696d88f9`
matches the six inspected runtime source files; the web's new image and browser
show the corrected Retry copy. Environment caps and gateway-key isolation pass.
Deployment receipt SHA-256:
`645cb088dd69973c2298c68e8d344c3af7fc773f537af9e08baa64021acf39ff`.

The scoped pre-resume baseline reproduces the same stopped run: 15 dispatches,
9,397 micros spent, zero reserved, zero repairs, revision zero and 14 v1 reports.
Its prior artifact, dependency, attempt and operation hashes match. The protected
older run still has 20,236 micros spent and 109,644 reserved. Baseline SHA-256:
`6d08d77291cfc64e998ffcce530cf18b53bb659e2073456da53891b630b764a5`.
No Retry or model call was made; both previous Temporal owners remain terminal.

Implemented the complete render-entry supervisor and lease-owned workspace
cleanup. Success is recorded after lease release. Independent review found no
remaining blocker in that production change; native read-only hashing retains
the limitation above.

Six new regression cases and 14 existing real-media cases passed (20 total,
38.40 seconds), with Ruff clean and Pyright reporting zero errors/warnings.
They cover fresh/reused production I/O paths, one Temporal attempt through quiet
work, cancellation through cleanup, heartbeat failure with the workspace/lock
held until cleanup, and a cancelled real-flock waiter preserving owner files.
The two principal cases failed against an isolated pre-fix source tree: the
quiet activity exhausted its attempts and the cancelled waiter deleted the
sentinel. Regression log SHA-256:
`55b45f54677c6912312efe940281dc6666961bbed8bb7dee597b8c0e04e05441`.

Root replayed all four retained production histories (77, 191, 361 and 217
events) with the production plugin and checked their original hashes. All
passed. The exact-commit full-gate receipt and GitHub checks belong to the PR's
delivery evidence; these focused results do not replace that gate. The long
source remains stopped pending this correction's merge and deployment.
