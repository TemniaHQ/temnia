<!-- This file is the working plan for S2 and becomes the PR description when the S2 PR opens (AGENTS.md: the 360-degree view comes before the code). -->

# S2 — Transcript and substrate: the 360-degree view

Written 2026-09-06 on `feat/s2-transcript` (off main at 9828b0e), before any code, from five
research reports (Modal ladder, WhisperX on Modal, legacy map, S2 requirements and code map,
transcript UI facts). S1 closed the same evening: the 2:31 master passed the exit test on the first attempt after PR #17, the ladder took 46 minutes on the VPS and publishing the 3.8 GB ladder to R2 took another 21 minutes at eight concurrent puts (PR #18). The merge freeze is lifted; the PR slot is free.

## 0. What the research changed

Four things in the plan are wrong or missing, and the view is built on the corrected facts.

1. **The pyannote pin is backwards.** whisperx 3.8.6 (2026-05-25, BSD-2) requires pyannote-audio 4.x
   and defaults to `pyannote/speaker-diarization-community-1` (gated on Hugging Face, CC-BY-4.0,
   commercial use allowed with attribution). Issue #1406 is a dead keyword on the Whisper model
   loader, not the diarization path. No 3.x pin, no patch. AGENTS.md decision 11 and tech-stack row 18
   are corrected in this branch.
2. **Modal's L4 is about $0.80 per hour, not $0.28**, and the "twenty audio-hours per GPU-hour" figure
   has no primary source. The first staging run is the benchmark. Expected cost per 2.5-hour master:
   under one dollar for ladder plus transcript, comparable to a hosted API. Modal wins on control and
   one deployment, not on list price.
3. **The substrate is not ported.** Sprint plan line 26 says the sentence grid, paragraphs, and lead-in
   rules were ported bit-for-bit in A1. A1 ported the scorers, the review metrics, the JavaScript
   number shim, the snapshot verifier, and five grid helpers (84 lines). The full substrate
   (`grid.ts` 558 lines, `paragraphs.ts` 133 lines, the two-turn lead-in) is TypeScript only, and no
   three-source fixture set exists in either old repo. S2 ports it and builds the parity chain.
4. **Nothing in the repo can run transcription without Modal.** There is no provider seam, no mock
   injection point in the gate, and no Modal or Hugging Face secret anywhere. All three are built here.

## 1. Sequencing: four PRs, one open at a time

| PR | Lands | Merge gate |
|---|---|---|
| A. Ladder on Modal | Transcode seam (`local` for dev and the gate, `modal` on staging), Modal app with NVENC ffmpeg from our mirror, spawn with persisted call id, progress via a Modal Dict, completion marker in R2, worker boot probe of the deployed version | NVENC throwaway probe passed; VMAF within 2 points of the libx264 rung on a 2-minute excerpt; the S1 master re-laddered and published from Modal faster than the VPS's 67 minutes end to end |
| B. Transcript | Tables, contract, provider seam (WhisperX on Modal + recorded provider), `TranscribeWorkflow`, ledger, transcript tab (view, follow, seek, search, low-confidence), corrections, speaker rename, SRT/VTT export | Gate green with the recorded provider; Playwright on every state and dialog; the S1 master transcribed on staging |
| C. Substrate and parity | Python port of grid, paragraphs, lead-in; vendored TS oracle and dump script; three recorded sources as fixtures; eval runner re-host with its snapshots | Byte parity on three sources; scorer snapshots bit-identical |
| D. Close S2 and M0 | Staging demo run recorded, two-org probes, PRD retags (§6 Deepgram line, §30 M0 after S2), sprint plan line 26, log, posts | Rajesh's read of the numbers |

A comes first because it is the smallest surface that proves the whole Modal pattern (secrets,
deploy, spawn, reattach, progress, version probe) before transcription depends on it, and because
the S1 numbers say the publish is a third of the wall time: the function uploads the ladder to R2
itself, so the 21-minute publish from the VPS goes away with the 46-minute ladder. C comes after
B's staging run because the three fixture transcripts are produced by B.

## 2. Inputs and edges

**Ladder (A).** Empty or zero-duration master: refused at probe as today. Huge: a 3-hour 4K ProRes
master is 100 GB+; NVENC cannot decode ProRes, so decode and scale stay on the CPU inside the
function (hybrid graph) and only the video rungs switch to `h264_nvenc`; the I-frame rendition
(360p at 0.5 fps, `keyint=1`) stays on libx264. Duplicate: a retry while a Modal call is running
reattaches by call id rather than spawning a second GPU job. Concurrent: two sources ladder in two
containers; Modal scales, the worker's one-ladder-at-a-time limit goes away. Cancelled: source
deleted mid-ladder cancels the FunctionCall and the function's partial uploads are removed by the
existing prefix delete. Re-entered: a complete ladder in R2 is detected by `hls/manifest.json`,
written last, and reused; today's reuse checks the VPS work volume, which the function does not
have.

**Transcription (B).** No speech: a ready transcript with zero words is a real state ("No speech was
detected"). Unsupported language: 36 languages have alignment models; any other raises a
deterministic `TranscriptionFailure` with the code shown to the user, no retry. Language detection
on a music intro can misfire; the source's language is detected on the first 30 seconds of speech
after VAD, and the transcript stores the detected code so a wrong guess is visible. Words without
timestamps (numbers, symbols) or without a speaker (a known whisperx bug) arrive from the provider
and are normalised: timing interpolated between neighbours and flagged, speaker taken from the
enclosing segment, else null. Overlapping speech: diarization assigns one speaker per word; no
overlap model in S2. Duplicate: one transcript row per source (unique on source id); a second start
while one runs is refused by the workflow id policy. Re-run: a retry after failure is attempt N+1
and a new machine revision, never an overwrite. Corrections: two tabs editing the same revision;
the second save gets a stale-revision refusal. Speaker rename to an existing name merges two ids
by design. Deletion: deleting the source deletes the transcript prefix and rows (cascade).

**Substrate (C).** Empty transcript renders empty grids, not an error. A one-speaker monologue
produces no speaker turns; paragraphs split on the 2500 ms gap and 120-word cap alone. A word with
`startMs == endMs` is legal. Shots absent (synthetic sources): shot snap is a no-op.

## 3. Scale

- A 2.5-hour 1080p25 master: 226 thousand frames. The CPU ladder took 46 minutes on the VPS and the
  publish of the 3.8 GB ladder to R2 another 21 minutes. NVENC on an L4 at a conservative 150 fps is
  about 25 minutes; Modal's upload rate to R2 is undocumented and is measured on the staging run,
  which is the benchmark for both numbers.
  Function limits: timeout 3 hours, 4 CPUs, 8 GiB memory, default 512 GiB ephemeral disk.
- Transcript JSON for 2.5 hours: about 28 thousand words, roughly 2 MB. Served once through the
  media proxy with an ETag; searched and rendered in memory in the browser. Rendering uses speaker
  turns as blocks with `content-visibility: auto`; no virtualisation dependency until the staging
  transcript shows the tab taking over 200 ms to become interactive.
- WhisperX on an L4: ASR plus alignment reported at 20 to 70x realtime, diarization near 10x; a
  2.5-hour episode is plausibly 15 to 40 minutes. Function timeout 4 hours.
- Audio in: the S1 audio extract (`audio/audio.m4a`) is read from R2 inside the function by a
  presigned GET; no bytes travel through function arguments.
- Three parity fixtures at 2 MB each plus renderings: about 8 MB committed. Acceptable in a private
  repo; not LFS.

## 4. Failure and time

| Step | Expected | Bound | Liveness | Retry does |
|---|---|---|---|---|
| Spawn ladder on Modal | seconds | 60 s activity timeout | n/a | Retry spawn; auth failure is terminal and matches the boot probe |
| Ladder runs and publishes | 10–30 min plus the upload | 3 h function timeout; activity heartbeat timeout 90 s | Function writes stage and percent to a Modal Dict every 5 s, through the upload as well (the S1 publish-heartbeat lesson); worker polls every 10 s, heartbeats, writes the progress row | Reattach to the call id in heartbeat details; complete ladder in R2 reused via manifest |
| Ladder verify | seconds | 5 min | heartbeat | Function runs `assert_covers` before upload; worker re-checks the manifest against the probe and lists the playlists in R2 |
| Spawn transcription | seconds | 60 s | n/a | as above |
| WhisperX runs | 15–40 min | 4 h function timeout; heartbeat timeout 90 s | Dict progress: model load, VAD, ASR percent, align, diarize | Reattach by call id; a failed call respawns up to 3 times with backoff; deterministic failures (language, corrupt audio) are terminal on attempt one |
| Normalise and write revision | seconds | 5 min | heartbeat | Idempotent: revision N for attempt N; a re-run writes N+1 |
| Reaper | every 15 min | n/a | `transcript.heartbeat_at` | A processing transcript with no heartbeat for 10 minutes is failed with "Transcription stalled" and can be retried |

A worker restart mid-ladder or mid-transcription no longer loses the GPU work: the call id in the
last heartbeat lets the retried activity reattach. That is the operational reason to prefer spawn
over a blocking remote call.

## 5. User states

Transcript tab on the source page. Every state has words and an action; a raw server message is
never shown.

| State | The user reads | Can do |
|---|---|---|
| Source not ready | "The transcript starts after processing finishes." | wait; the tab polls every 3.5 s while the source or transcript is in flight |
| Pending | "Queued for transcription." | wait |
| Processing | "Transcribing · Aligning words · 62%" with a progress bar | wait |
| Stalled (heartbeat older than 2 min) | "Transcription has not reported progress for a while. It will be retried automatically." | wait; Retry button if the reaper has failed it |
| Failed, retryable | "Transcription failed: the transcription service was unavailable." | Retry |
| Failed, terminal | "This recording is in a language we cannot align yet (code: xx)." | nothing until S12; the code is visible |
| Ready, empty | "No speech was detected in this recording." | Retry |
| Ready | Speaker turns with timestamps; the current word highlighted during playback; Follow toggle; search box with match count and next/previous; low-confidence words dotted with a tooltip; speaker names clickable | click a word to seek; search; edit a word's text; reassign a turn's speaker; rename a speaker; export SRT or VTT |
| Editing a word | inline input, Save and Cancel | Save writes revision N+1 |
| Stale revision | "Someone changed this transcript since you opened it. Reload to see the latest." | Reload |
| Rename speaker | dialog with the current name; renaming to an existing name shows "This will merge the two speakers." | Save; Cancel |
| Export | file download of `<title>.srt` or `.vtt` with speaker names applied | |

The list page gains nothing in S2; the source row's status is still the ingest status. A separate
transcript status badge on the list is S7's concern (source intelligence).

## 6. Operations

- **Secrets.** Pipeline service env gains `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`,
  `MODAL_ENVIRONMENT=staging`, `TRANSCODE_BACKEND=modal`, `TRANSCRIPTION_PROVIDER=modal`. Set in
  Dokploy's env UI and reaching the container only through an image-changing deploy (S1 lesson).
  Modal-side Secrets, created in the Modal dashboard: `temnia-r2` (a separate R2 token, read and
  write on the staging bucket, revocable independently of the worker's) and `temnia-hf`
  (`HF_TOKEN` for the gated pyannote models).
- **Deploy.** `uv run modal deploy` from the pipeline package publishes app `temnia-media` with
  functions `ladder`, `transcribe`, and `version`. The deployed version is the git SHA baked at
  deploy. The worker's boot probe calls `version` when either backend is `modal` and refuses to boot
  on a mismatch or an auth failure, which Dokploy reports as a failed deploy. A wrong token is loud at
  boot, never a silent queue. The runbook gains the deploy step and the env lines.
- **Local dev and the gate.** `TRANSCODE_BACKEND=local` and `TRANSCRIPTION_PROVIDER=recorded` are
  the defaults in the env example and in `scripts/local-ci.mjs`. The gate runs real ffmpeg on the
  24-second fixture and replays a recorded WhisperX response for it. No Modal call in CI, ever.
- **Disk.** The VPS work volume stops holding ladders; only the master download and the audio
  extract remain on it. Modal ephemeral disk holds the ladder for the call's lifetime.
- **Restart mid-flight.** See §4: reattach by call id. A Modal outage fails transcription after the
  retries and leaves the ingest untouched (transcription is its own workflow).
- **Cost visibility.** The Modal call's GPU seconds and GPU type go into the ledger row's metadata so
  dollars per source-hour can be computed from the ledger alone.
- **Model cache.** WhisperX models (large-v3, the English alignment model, the diarization
  pipeline) live in a Modal Volume filled on first run; cold starts read from it.

## 7. Tenancy and security

- New tables `transcript` and `transcript_revision` carry `organization_id`, the organization
  policy, and forced RLS in the migration that creates them, with a probe each in the isolation
  suite. `usage_kind` gains `transcription_seconds`.
- Every worker read and write runs under `db.scoped()` with the source's organization, as ingest
  does. The Modal functions are scope-blind compute: they receive a storage prefix chosen by the
  worker and never an organization id from input. The `temnia-r2` token is bucket-scoped.
- The transcript JSON is served through the existing org-scoped media proxy; `keyBelongsTo` already
  fences the key, and `.json` is already in its content-type map. Export routes resolve scope with
  `resolveScope()` and 404, never 403, on a foreign source.
- Server actions for correction, rename, and retry validate with zod and run in a scoped
  transaction; the organization id never comes from the request.
- The Hugging Face token exists only inside Modal; the worker never holds it.

## 8. Verification

| Row | Proof |
|---|---|
| NVENC exists on Modal | Throwaway `modal run`: `ffmpeg -encoders` lists `h264_nvenc` and a 10-second encode succeeds (before PR A is written further) |
| NVENC quality | VMAF of the NVENC top rung vs the libx264 rung on a 2-minute excerpt of the staging master, within 2 points at no more bitrate; numbers in the PR |
| Ladder correctness on Modal | `assert_covers` in the function; worker re-verifies the manifest; the S1 master re-laddered on staging plays in the two-pane page |
| Reattach | Unit test with a fake call handle: running, complete, failed, unknown id |
| Boot probe | Unit test: wrong token and wrong version refuse boot with the exact message |
| Provider normaliser | Unit tests on the recorded 24-second response and a hand-built edge fixture (missing start, end, speaker; NaN score; empty segment; unsupported language) |
| Contract | Zod and the generated pydantic model agree on the transcript v1 shape; the contract drift check in the gate |
| Transcription end to end | Gate: recorded provider on the 24-second fixture reaches Ready with revision 1 in Garage and ledger rows (`transcription_seconds`, `storage_bytes`) written under RLS as the pipeline role |
| Reaper | Database test: a processing transcript with a stale heartbeat is failed and retryable |
| Tenancy | Isolation suite probes for both tables; schema contract test updated for the new enum and columns |
| Transcript tab | Playwright: every state above (driven by the recorded provider and a stalled fixture), click-to-seek changes `video.currentTime`, follow highlights the word at 5 s, search finds the expected count, edit a word then reload shows revision 2, rename then export shows the name in the VTT, two contexts produce the stale-revision message, every dialog and menu opened |
| Hydration | The SSR-HTML assertions in `pnpm e2e:prod` on the transcript tab (the S1 hydration lesson) |
| Substrate parity | `apps/pipeline/tests/test_substrate_parity.py`: coarse and fine renderings byte-equal to the committed oracle output on three recorded sources; the dump script's stale check fails if the oracle output changes |
| Scorer parity | `test_parity.py` on the re-hosted snapshots, bit-identical |
| Scale on staging | The 2-hour master: ladder minutes and GPU cost, transcript minutes and cost, tab load time, two-org probes; the numbers close S2 and M0 |

## 9. Legacy lessons

| Lesson | Disposition |
|---|---|
| Speaker rename and merge stay manual after over-segmented diarization on staging | Ported: `speaker_labels` metadata on the transcript, same name on two ids merges, no auto-merge |
| Integer milliseconds everywhere, never float seconds | Ported: the contract rejects non-integers; the normaliser rounds once at the provider boundary |
| Revisions are new objects, never overwrites, with optimistic concurrency | Ported: `transcript_revision` rows, `base_revision` check, stale refusal |
| Transcript is not an artifact row because re-ingest wipes artifacts | Ported: transcript prefix outside `artifacts`, kept across re-ingest, deleted with the source |
| Client-side substring search; semantic search separate | Ported for S2; embeddings search is S7 |
| Cue rules for SRT and VTT (speaker turn, sentence end, 750 ms silence, 70 chars, 7 s) | Ported; the `@remotion/captions` dependency dropped for a 30-line serializer |
| Provider degrades to "no transcript" when unconfigured | Replaced: explicit env, refuse to boot |
| Deepgram primary, AssemblyAI fallback | Dropped by decision 11; a hosted adapter is S12's fallback only |
| Synthetic three-sentence mock provider | Replaced by a recorded real response plus an edge fixture, so the normaliser is tested against the real shape |
| `storage_bytes` per revision without delta arithmetic | Replaced by the S1 delta pattern under category `transcript` |
| Happy Eyeballs false `ETIMEDOUT` against Neon | Dropped: Postgres is on the VPS; noted as the first suspect if Modal calls show `ETIMEDOUT` |
| Silent truncation at 58% | Already ported in S1; the Modal function runs the same assertion |
| M1 failed on boundaries because the model could not address the timeline | Applies to S4; the reason the renderings must be byte-identical (stable `P042`, `s0417` ids) |
| Lead-in capture, two-turn (the Brett Lee finding) | Ported in the grid port and covered by parity |
| Reviewer calibration at 75 to 80 percent | Not S2 |

## 10. Rajesh's part (blocking)

1. Modal account (Starter, $30 monthly credit covers S2), an environment named `staging`, a
   service-user token into the pipeline service env in Dokploy.
2. Hugging Face account, accept the gate on `pyannote/speaker-diarization-community-1`, a read
   token into a Modal Secret `temnia-hf`.
3. A second R2 token for the Modal functions into a Modal Secret `temnia-r2`.
4. Three real recordings for the parity fixtures (an interview with crosstalk, a monologue, a
   three-person panel would be ideal) and confirmation their transcripts may live in the repo.
5. (Done 2026-09-06.) The S1 exit run passed; S1 is closed in PR #18.

## 11. Decisions proposed for AGENTS.md (recorded on this branch as corrections; the rest on merge)

- Correction to decision 11: pyannote 4.x with `community-1`; no pin, no patch.
- Correction to decision 9: L4 at about $0.80 per hour; throughput to be measured; the publish moves
  into the Modal function because it was 21 of the VPS's 67 minutes.
- Sprint plan line 26 corrected: A1 ported scorers and five helpers; the substrate port is S2 work.
- Transcription is its own workflow started after ingest finalize, never a step of ingest.
- The transcript lives in storage under the source prefix with revisions in Postgres; never an
  artifact row.
- Modal functions are scope-blind compute; the worker is the only authority on organization and
  prefix.
- The recorded-response provider replaces a synthetic mock.
- Hybrid ladder: CPU decode and scale, NVENC for the video rungs, libx264 for the I-frame rendition.
