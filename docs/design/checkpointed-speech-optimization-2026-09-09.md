# Checkpointed speech performance qualification — 2026-09-09

**State: controlled comparison in progress.** PR #24 implements bounded background progress
publishing, explicit CPU limits and parallel speaker-turn detection. All four short cases are ready,
including recovery of the first CPU persistence failure. The first long case completed but exposed
a heartbeat gap; its wall timing is excluded. Seven preplanned long cases remain. No performance
or billing winner has been established.

## Comparison design

The earlier [protocol-1 qualification](checkpointed-speech-qualification-2026-09-09.md) processed
151 minutes in 1,084.810 workflow seconds and 1,021.261 measured function seconds. The older combined
GPU path took 660.264 workflow seconds and 635.220 function seconds. Different CPU limits and model
asset provenance prevent treating that difference as a controlled effect of stage isolation.

This experiment holds the source, GPU, memory, model assets, native-stage build and inference
settings fixed. It changes progress transport, CPU allocation and scheduling in steps:

| Variant | Progress publishing | CPU request/limit | Stage scheduling | Configured micros/hour |
| --- | --- | ---: | --- | ---: |
| A | Synchronous control | 4 / 4 | Serial | 1,250,000 |
| B | Background, coalesced | 4 / 4 | Serial | 1,250,000 |
| C | Background, coalesced | 8 / 8 | Serial | 1,450,000 |
| D | Background, coalesced | 8 / 8 | Recognition → alignment alongside speaker turns | 1,450,000 |

Every variant uses one NVIDIA L4, 16 GiB of host memory, single-use containers, immutable checkpoints and
create-only inference admission. Speaker assignment runs on CPU from the exact alignment and
speaker-turn artifacts, with no fourth GPU call. D reuses C's deployed resource application.
Co-location and GPU snapshots are deferred candidates.

The order is short A/B/C/D, long A/B/C/D, then long D/C/B/A. Each long block uses the same
9,060.473-second audio: 110,024,458 bytes, SHA-256
`85615a2b6e04512cb333b80c4af6080a0f2017b355146b289303ed57bacc0c91`.
The short fixture is 40.116 seconds and 795,054 bytes, SHA-256
`31ae0e36255c429b9842b7fdc2cfe2edec9034096ac1b188be943173381a57af`.
Short cases test lost-result recovery; their latency is excluded from the long-source comparison.
Two reversed repetitions are a screening experiment, not broad performance qualification.

## Frozen execution identity

- GPU source build: `2ec251d208c3361b34f86ad1bc0aed86f605f212bdc3d13ab4bc1512abb88b01`, from commit `6f16b9e830e7fc0afa9e7f806966cc2128d00716`.
- Initial corrected worker source build: `0c64ba401a45f860ae83f0b00911091a7f3177fe2f8387ec352dd3d9d6fb5813`, used for recovered short A, short B/C/D and long A1.
- Heartbeat-repaired worker source build: `9f972e6f0e282e4ff504323dd3dacdfb97cc744dc24b42ff35dfc82c19943b22`, frozen for the seven remaining cases after its release gate. Native GPU applications remain on the original build.
- Deployment manifest: `09d308e6488dfafcce6e7265be67c96b6b2783e5d0eef292f56065fa2e1eab7a` (canonical SHA-256).
- Frozen volume: 23 regular files, 6,624,978,799 bytes, manifest `c8323dcc132fe24d12d0c856e1fa9610f41af6391679d1cdc2999ebdfe99dea4`.
- Image-bundled model assets: `b67cbb5bb2c5f22ff98be5504b1b309861ea54d59cf970457cc0cf586d249be9`. This snapshot qualifies the English source used here; wider language coverage remains unmeasured.

The applications run in Modal staging with a dedicated local PostgreSQL database, Temporal namespace
and owned R2 source prefixes. Shared staging's deployed web and worker are unchanged.

## Admission and cost interpretation

The fixed matrix permits 36 physical GPU calls, reserving at most 13,770,018 micros under a
non-recyclable 14,000,000-micro new-experiment limit. Each GPU stage has a 900-second deadline and
120-second startup allowance; provider retries and OOM fallback are disabled for this experiment.
A failed or unknown execution stops subsequent admission until investigated. No additional
repetition, cap increase or refund is part of the repair.

The September 9 published resource rates imply floors of 1,115,712 micros/hour at four CPUs and
1,304,352 at eight CPUs; configured rates are higher. Measured function duration times the resource
rate is an estimate, not a provider invoice or complete billed-container lifetime. Provider
allocation, restart, CPU preparation and control-plane work may fall outside returned function
telemetry. [Modal pricing](https://modal.com/pricing), [resource allocation](https://modal.com/docs/guide/resources).

All earlier nine protocol-1 attempts retain their separate 11,625,003 micros of unresolved exposure.
The first new short case retains 1,062,501 micros across three successful GPU attempts. Actual billed
charges remain unavailable; recorded zero spend means unreconciled cost, not free computation.

A read-only `modal environment billing report staging` probe succeeded for complete hourly
intervals from 00:00 through 05:00 UTC on September 9. The provider reports **$0.46483580** across
the owned benchmark applications and preparation/probe applications. This is reported usage before
credits or reservations, not a final invoice. The export groups costs by application, hour and
resource; C and D share an application, and the data cannot identify exact per-attempt charges.
Individual ledger costs therefore remain unknown and their reservations are retained. This
partial export is not a cost-winner comparison. [Modal billing reports](https://modal.com/docs/guide/billing).

## Preserved first-case failure and recovery

Recognition, alignment and speaker-turn detection each succeeded once, in 36.718, 31.817 and
46.027 measured function seconds respectively. CPU assignment then failed because
`speech_assignment` was missing from the Drizzle artifact enum. The failure report also used the
wrong dependency column. Migration 0004 adds the enum value; the report now queries
`input_artifact_id`. The original failed run, checkpoints, admissions and costs remain retained.

After the corrected full gate passed, recovery stopped before starting any workflow: independent
speech coverage shares the `speech_checkpoint` enum kind with GPU checkpoints. There were four
legitimate source artifacts of that kind. The corrected selector follows the three GPU operations'
exact result references and validates format, stage, operation identity and lineage. Coverage and
other source evidence remain preserved.

The journal already carried a recovery marker. Its explicit continuation verifies the exact
original journal and GPU/accounting ledger, both local/database leases, unchanged limits and later
planned cases, and the original failed execution as Temporal's latest run. It records a durable
start intent before requesting recovery. A lost response or crash cannot reopen the unstarted
exception merely because no new database row is visible.

Recovery used the same source and accepted GPU checkpoints with a one-micro budget and a client
that refuses GPU spawning. Its separate run became ready in 12.274 seconds with zero physical
attempts, reserved exposure or recorded spend. The original failed run and accounting hash remained
unchanged. Recovery time is excluded from speed comparisons.

## Completed short cases and first long case

All four short variants produced exactly matching token, timing and speaker hashes: 93 words,
two speakers, English, first word at 543 ms and last word ending at 38,977 ms. B, C and D each
recovered a deliberately lost activity result with activity attempts `[1, 2]` and physical dispatch
observations `[3, 3]`. D's recognition and speaker-turn attempts overlapped by 36.968 seconds.
Short cold-start timings are recovery/topology evidence, not the long-source performance result.

The first long A case produced 20,587 words and two speakers from exactly three successful GPU
calls. Recognition, alignment and speaker-turn detection took 236.075, 202.061 and 453.872 function
seconds, totaling 892.008. The returned telemetry is complete and belongs to the original calls.
Synchronous progress publishing made 1,903 successful writes and occupied 221.389 seconds
inside callbacks (17.440 recognition, 43.031 alignment and 160.918 speaker turns). These times
are included in function/inference timing, not additional elapsed time. They identify a measurable
blocking cost; a paired coalesced run must establish the actual speedup. Invoice charges remain
unknown.

Temporal history event 28 records `TIMEOUT_TYPE_HEARTBEAT` for the first checkpointed-transcribe
activity attempt. The second attempt reused all accepted work and completed in 3.203 seconds.
The benchmark correctly rejected this unplanned retry. Its 989.952-second workflow wall time is
excluded from wall comparisons; no estimated retry overhead is subtracted. The original GPU
function measurements and output facts remain usable.

A local reconstruction verified the exact preserved checkpoint sizes/hashes and reproduced the
3,495,617-byte accepted CPU assignment byte-for-byte. Five calls measured 0.202–0.208 seconds
(median 0.207); parsing, serialization and validation were also below the ten-second heartbeat
timeout. The repair therefore covers liveness throughout preparation, storage and post-GPU work,
independent of diagnostic progress writes. The original failed-validation evidence stays retained.
Continuation admits that existing completed execution only, then resumes the seven remaining
cases with a corrected worker fingerprint and the unchanged GPU build and model manifests.
Block-one wall comparisons requiring A are unavailable; the originally planned second block
contains the remaining A repetition. No extra call or budget increase is admitted.

## Verification and remaining work

The first optimization commit passed the full local gate with 717 Python tests and 18 production
browser tests. The corrected assignment/schema worker passed on
`c933d0349fb400677fdb20d623a6aee285dde8da`: 722 Python tests with zero skips, 187 web tests,
41 database tests, 28 contract tests, both Linux deploy images and all 18 production browser tests.
The driver repair passed on `214f4ee6c58889c496056911c7a0a6c51b9aab24` at 04:06:02 UTC:
728 Python tests with zero skips, all 16 build/lint/typecheck/test tasks, both deploy images and
all 18 production browser tests. The subsequent heartbeat repair requires a new exact-commit gate
before further paid work.

The heartbeat repair passes two real Temporal/PostgreSQL regressions with simulated provider
responses, the production two-second heartbeat interval/ten-second timeout, and twelve-second
post-GPU delays. Success retains one activity attempt and three successful provider ledger rows;
cancellation waits for cleanup, records an activity-cancel event and publishes no assignment.
Eight supervisor lifecycle tests, the real PostgreSQL cache-reuse test, and 30 adjacent lifecycle,
workflow and terminal-accounting tests pass. Focused Ruff, formatting and Pyright are clean.
The disposable liveness database was dropped and its absence verified. These tests admitted no
live GPU calls and do not measure transcription accuracy.

The final continuation validators passed 27 focused tests, Ruff and Pyright. A read-only rehearsal
against the actual run passed in 7.9 seconds: exact ledger/source, all ten objects, the latest
completed 63-event Temporal history and three terminal Modal results. The original journal hash
remained unchanged. The full gate and a fresh proof precede continuation.

Focused real PostgreSQL/Temporal tests now exercise production coverage publication, typed CPU
assignment, exact checkpoint reuse, preserved original accounting and rejection of a second database
run or newer running/failed Temporal execution. Original objects and a read-only live-state audit
are preserved privately. Local faults and synthetic provider results do not establish speech accuracy.

Current retained experiment exposure is 5,652,507 micros across 15 successful terminal GPU calls.
Remaining evidence: the seven preplanned long cases, eligible paired timings,
transcript/timing/speaker comparisons, telemetry availability and owned-resource cleanup. Human
transcript and boundary labels, representative additional sources, actual invoices and live
editorial-model auditions remain separate qualification work.
