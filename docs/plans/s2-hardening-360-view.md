<!-- The working plan for the S2 hardening batch; it becomes the PR description (AGENTS.md: the 360-degree view comes before the code). -->

# S2 hardening: the 360-degree view

Written 2026-09-08, 00:30 IST, on `fix/s2-review-findings` (off main at abbab16, after PR #22),
before any code, from the review in `docs/design/harness-review-and-architecture-2026-09-07.md`
(32 findings; 19 independently rechecked, none overturned) and the code as it stands on main.
Scope: every finding that is a defect or a validation gap in shipped code, fixed here; every
finding that is harness design or a product change, carried into the S3 spec with a reason. The
review's architecture proposal is reconciled with the approved S3 design in the AGENTS.md decision
of the same date and in `docs/plans/s3-harness-spec.md`.

## 0. What the review changed

Three things the S2 build believed are not true, and the batch is built on the corrected facts.

1. **A checkpoint is not idempotency.** "Zero repeated calls after a crash" was promised by
   heartbeating the Modal call id. The claim, the ledger, and the revision write each have a window
   between a commit and the acknowledgement Temporal never receives, and a retry through that window
   either duplicates work (ledger), strands the row (claim), or overwrites a neighbour's bytes
   (revision). Each write now carries the identity of the run that made it and is fenced on it.
2. **A failed status read is not a failed run.** Both Modal adapters answered a transport failure
   with `Failed`, and both runners answer `Failed` on reattach by spawning again. A healthy GPU job
   could be paid for twice. Transport uncertainty is now its own status and never spawns.
3. **A sort is not a repair.** Alignment can move a word before its predecessor; the normaliser
   sorted the words, which rewrote what was said. Lexical order is the truth of a transcript; a
   time regression is repaired in place and flagged.

## 1. Sequencing

One PR, one branch, the PR slot is free (#20 and #22 merged 2026-09-07). Commits are one finding
or one coherent cluster each, in the order the sections below run: schema, pipeline correctness,
normaliser and substrate, scorers, web, Modal functions and smoke, gate, docs. The gate runs on the
final SHA through `node scripts/verified-pr.mjs pr`.

| Cluster | Findings | What lands |
|---|---|---|
| Run identity and ledger | I04, I06 | `transcript.run_id`; claim idempotent per run; every later write fenced on it; `usage_ledger.idempotency_key` with a partial unique index; ingest and transcription meter with `ON CONFLICT DO NOTHING` |
| Publication | I03 | Corrections write to a per-attempt key, then compare-and-swap the pointer; the loser removes its object; the page and the export read the key from the row; machine revisions take the transcript row lock for the whole write and never `DO UPDATE` over a correction |
| Remote uncertainty | I05 | `Unreachable` in both adapters for the transport exception families; neither runner spawns on it; the poll tolerates a bounded run of them; reattach heartbeats the handle before it asks |
| Normaliser | I07, I08, I09 | Missing or malformed `segments` is a contract error; a segment with text and no words keeps its text as unaligned words; runs of untimed words share the span between anchors by character weight; zero anchors count; no reorder, a regression is clamped in lexical order and flagged |
| Substrate | I11, I14, I18 | Sentence and paragraph extents are the maximum child extent; `target_per_hour` must be positive; `jump` removed (KernelCPD ignores it); the sentence ceiling is checked before embedding; truncated sentences are counted into provenance |
| Scorers | I12, I13 | Window is half the mean segment length over `marks + 1` segments; matching is maximum-cardinality on the line, nearest second |
| Web states | I19, I23, I10 | No audio and dispatch failure become typed failures the ingest and the retry action write; Retry on queued, stalled, and missing rows; the retry action asks Temporal whether the run is alive before it touches a processing row; utterance membership follows speaker runs |
| Reader | I20, I21, I22, I26 | Try again refetches; the draft lives in the reader and survives virtualisation; follow pauses while editing; every save handler catches and keeps the draft; the tooltip says timing, not recognition |
| Modal functions | I27, I29, I30 | Per-call scratch removed in `finally` with a disk preflight; a release smoke that runs the deployed speech path on a real sample; ladder reuse verifies the inventory against the manifest |
| Provenance | I15 | Model revisions pinned and asserted after load; the resolved sha recorded |
| Gate | I32 | The sweep skips containers and images whose owning gate process is alive |

Carried into the S3 spec, not fixed here: I16 (evaluation redesign; the annotated corpus is the
S4 entry gate), I17 (the substrate as a persisted, versioned artifact; slice A of S3), I24 (phrase
search), I25 (delete/split/merge edits and versioned speaker identity), I28 (the VRAM envelope; the
smoke gives the first number), I31 (attempt-level resource accounting; the harness's call ledger),
and the language routing half of I18. I01 and I02 are fixed on main already.

## 2. Inputs and edges

- **Claim.** Same run id twice (lost acknowledgement) returns the same attempt and touches nothing
  else. A different run against a `processing` row returns 0. A `ready` row returns 0 unless Retry
  parked it at `pending`. A row whose run died without writing (worker gone, execution timeout) is
  reachable again through Retry, which asks Temporal first.
- **Ledger.** The same attempt metered twice inserts once. Two attempts of one source insert twice,
  because two engine runs were paid for. The storage delta stays delta-based and needs no key.
- **Correction.** Two tabs on revision N: both upload, one moves the pointer, the other sees the
  stale notice and its object is deleted best-effort. A crash between upload and commit leaves an
  orphan object nothing references; it is harmless and collected later. A machine run and a
  correction racing for N+1: the run holds the row lock, so whichever commits first takes N+1 and
  the other computes N+2 or is refused.
- **Normaliser.** `{"unexpected": 123}` is a contract error, terminal. `{"segments": []}` is a
  valid empty transcript (no speech). A segment `{"text": "hello there"}` with no words yields two
  interpolated words over the segment's span. Words `[a 0-500] [b] [c] [d 2500-3000]` yield b and c
  sharing 500-2500 by character count. A word timed `[0, 0]` is an anchor at zero. A word aligned
  before its predecessor keeps its place and takes its predecessor's start as its own, flagged
  `interpolated`. A whole segment with no timings and no segment bounds falls back to the
  neighbouring anchors or zero.
- **Scorers.** 100 units, one internal cut: window 25. No cuts: 50. References `[10000, 20000]`,
  hypotheses `[19000, 29000]`, tolerance 10000: two matches. Ties and unsorted inputs give one
  deterministic answer.
- **Membership.** Two speakers whose first words start on the same millisecond are two groups of
  one word each. Adjacent turns with the same speaker are one group.
- **Extents.** Words `[0, 3000]` and `[1000, 1100]` in one sentence end it at 3000.
- **Reattach.** A status read that raises a connection error keeps the handle and retries; Modal
  saying not-found or expired starts again; a function exception is `Failed` and is classified as
  before.
- **Retry action.** A `processing` row whose workflow Temporal reports as running: refused with
  words. One whose workflow is gone: parked at `pending` and started. A Temporal start that throws:
  the row is written `failed` with `DispatchError:` and the tab offers Retry.
- **Ingest.** No audio channels: a `failed` row with `NoAudioError:` and no Retry. Child start
  failure: a `failed` row with `DispatchError:` and Retry; the source is still ready.
- **Scratch.** Two distinct sources in one warm container leave nothing behind after each; a
  failing encode leaves nothing behind; a container without room for the master plus the ladder
  fails before the download with the numbers.
- **Ladder reuse.** A manifest whose byte inventory does not match the objects under the prefix is
  not reused; a missing rendition playlist is not reused.

## 3. Scale

A 2.5-hour episode: 28 thousand words, about 2,500 sentences, 18 thousand HLS objects. The run-based
interpolation is one pass. The matching is O((r + h) log) with a heap. The ladder inventory lists
18 pages of 1,000 keys on the reuse path only, which is a retry after a crash, never the first run.
The transcript row lock is held for the length of one revision upload (a two-megabyte PUT). The
draft in reader state is one string. The change-point ceiling check moves before embedding, which
is where the minute of CPU went on an over-limit input.

## 4. Failure and time

- A worker killed after the claim commits: the retry re-claims under the same run id and continues.
- A worker killed after the ledger insert commits: the retry inserts nothing and returns.
- A worker killed between the revision upload and the row commit: the transaction rolled back; the
  retry recomputes the same number under the lock, uploads again (same key), commits.
- A run whose acknowledgement of `fail_transcription` is lost: the retry rewrites the same failure.
- Modal unreachable for three minutes during a poll: the activity keeps heartbeating the handle and
  keeps polling; past the bound it fails retryably and the next attempt reattaches to the same call.
- Modal unreachable at reattach: no spawn; the attempt fails retryably; the heartbeat carried the
  handle, so the next attempt asks again.
- A stale run (a second run claimed the row) reaching `write_revision`: `StaleRun`, non-retryable;
  the workflow fails without writing to a row it does not own.
- The web's Temporal start throws after the row is `pending`: the row is set `failed` with the
  typed reason in a second transaction; if that write fails too, the row stays `pending` and offers
  Retry, which is now allowed on `pending`.
- Temporal `describe` throws in the retry action: treated as "not running" only for a row whose
  heartbeat is older than the stall threshold; otherwise refused with words.

## 5. User states

The transcript tab's table gains rows and changes two verbs.

| Row | Kind | Words | Retry |
|---|---|---|---|
| No row, source ready | pending | Queued for transcription. | yes |
| Row pending | pending | Queued for transcription. | yes |
| Processing, heartbeat fresh | processing | Transcribing · stage · % | no |
| Processing, heartbeat stale | stalled | Transcription has not reported progress for a while. | yes |
| Processing, stage retrying | retrying | Transcription stopped unexpectedly and is being retried. | no |
| Failed, `NoAudioError:` | noAudio | This recording has no audio track, so there is no transcript. | no |
| Failed, `UnsupportedLanguageError:` | language | This recording is in a language we cannot align yet (code: xx). | no |
| Failed, `DispatchError:` | failed | Transcription could not be queued. Try again. | yes |
| Failed, `TranscriptContractError:` or `ValidationError:` | failed | The transcription service returned an unusable result. | yes |
| Failed, other | failed | Transcription failed: the transcription service was unavailable. | yes |
| Ready, zero words | empty | No speech was detected in this recording. | yes |

Reader: Try again after a failed fetch issues a new request. A word being corrected keeps its text
if its row scrolls out and back. Follow-scroll pauses while an editor is open and resumes when it
closes. A save that throws shows "Could not save. Check your connection and try again." under the
word with the text kept; a speaker reassignment that fails shows the reason in an alert with a
dismiss. The low-confidence tooltip reads "Word timing is uncertain (alignment score 62%)."

Retry on a processing row that is alive answers "Transcription is still running." in the tab.

## 6. Operations

- **Migration 0002** adds `transcript.run_id` and `usage_ledger.idempotency_key` with its partial
  unique index. Additive; applied by the web's release phase as before. No backfill: old rows carry
  null and are claimable by the existing rules.
- **Modal.** `transcribe` and `ladder` clean their scratch; a new `smoke_transcribe` function and a
  `smoke` local entrypoint run the deployed speech path on a ten-second real sample and report words,
  timing, and seconds. It is a release step in the runbook after every Modal deploy. It costs one
  short GPU call. `CONTRACT_VERSION` stays at 3: no job or result changes shape.
- **Models.** `fetch_models.py` asserts the hub's resolved revision equals the pinned one, so a moved
  hub fails the image build with the message that says how to bump. The pins are in one place and
  the package test asserts the duplicate constants agree.
- **Gate.** The sweep skips resources whose owner pid is alive; two gates on one machine (two
  worktrees) no longer kill each other.
- **Runbook** gains the smoke step and the migration note.

## 7. Tenancy and security

Every new write is inside `db.scoped` under the pipeline role, so RLS applies. The idempotency key
carries a source id and an attempt or run id, never an organization id. The correction key is under
the source prefix, so the media proxy's prefix check covers it unchanged. The smoke writes under a
`smoke/` prefix inside the staging bucket with the same R2 token the functions already hold and
removes what it wrote. Nothing new takes an organization id from input.

## 8. Verification

Pipeline (pytest; the database-backed tests run against the gate's disposable database as before):

- Claim: same run twice returns the same attempt; a different run against processing returns 0;
  progress, ready, and failure writes from a stale run change nothing; `write_revision` from a stale
  run raises `StaleRun`.
- Ledger: finalize twice inserts once; two attempts insert twice; the ingest's processing entry the
  same; the partial index rejects a duplicate key directly.
- Revision race: a correction row committed at N+1 before the machine write makes the machine take
  N+2 and never updates the correction's row.
- Runner (both): `Unreachable` at reattach raises retryably and never spawns; a run of three
  unreachable ticks then `Done` returns the result; past the bound raises retryably; the handle is
  heartbeated before the first status read; the adapters map the transport exception set.
- Normaliser: every edge in §2, plus the fixture's moved word keeping its place and gaining the
  flag; the existing sort test becomes a no-reorder test.
- SaT: crossing intervals; paragraph extents; the legacy segmenter's parity test unchanged.
- Scorers: the two counterexamples; an exhaustive oracle over small random cases for the matching.
- Factory: `target_per_hour=0` refused with words; `jump` refused as an unknown parameter.
- Modal app: scratch is removed on success and on failure; the disk preflight refuses with numbers;
  the smoke function's assertions on a recorded result; the sample fixture exists and is short.
- Ladder reuse: a missing rendition playlist and a byte mismatch both refuse reuse.
- Models: the pin assertion fails on a mismatch and passes on a match (pure function over refs).

Web (vitest and Playwright):

- State table: every row in §5 literally, including the two new kinds and the retry changes.
- Retry action: a running workflow is refused; a gone workflow is restarted; a throwing start writes
  the typed failure.
- Correction: the key is unique per attempt; a stale save deletes its object; the page and the
  export use the row's key.
- Membership: the tied-start case; adjacent same-speaker turns.
- Reader (Playwright): Try again issues a second request; a draft survives scrolling away and back;
  a save whose action rejects keeps the text and shows the error; the tooltip wording.

Gate: the sweep's ownership rule (`ownerPid`, `processAlive`) checked by hand against live and
dead pids while the fix was written; the script has no test harness of its own.

## 9. Legacy lessons

The six-dollar sweep (one unstated invariant made every model fail and the failover ladder swept the
pool) is the same shape as a duplicate GPU spawn: a retry rule that does not distinguish "the work
failed" from "I could not see the work". The 58% truncation that reached users was an encode that
reported success on a short output; the ladder inventory check is the same lesson applied to reuse.
The legacy's own cutting-room build was never proven on a fresh source, which is why the S3
amendment moves the annotated corpus before the compiler.

## 10. Rajesh's part

Run the smoke after the next Modal deploy (`uv run modal run --env staging -m
temnia_pipeline.modal_app::smoke`) and paste its numbers into the log. The three recordings for the
corpus remain the blocking item for S4.
