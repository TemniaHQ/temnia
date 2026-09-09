# Test the chapter harness locally

Use the verified PR #24 checkout for this pass. Shared staging does not contain these runtime
changes until PR #24 is merged and both web and worker are deployed. The
[implementation status](../design/harness-implementation-status-2026-09-08.md) separates recorded
integration tests, live speech measurements and outstanding editorial-model qualification.

This walkthrough uses the committed 40-second recording and recorded model responses. It exercises
the actual database, Temporal workflows, storage, rendering and review UI without paid inference.
It does **not** establish AI chapter quality on an arbitrary recording. Live gateway transport,
privacy checks and model auditions remain unqualified; do not interpret live WhisperX results as
qualification of the chapter planner. See the [rollout runbook](chapter-harness.md) for that setup.

## Prepare the checkout

Let existing local jobs finish, then stop older web or pipeline workers serving this same development
namespace before starting this checkout. Two worker versions on `default` can pick up each other's jobs. Keep unrelated
namespaces and any running remote qualification separate.

From the repository root, install the pinned toolchain dependencies and start local services:

```bash
pnpm install
pnpm services
pnpm --filter @temnia/pipeline sync
```

Have `ffmpeg` and `ffprobe` on `PATH`; the deployment images use 8.1.2. Copy
`apps/web/.env.example` to `apps/web/.env.local` if the latter does not exist. Preserve existing
credentials and check that its database, storage and Temporal settings point to the local services.
The examples use PostgreSQL 56432, Garage 56900 and Temporal 56233. The browser-facing
`STORAGE_PUBLIC_ENDPOINT` must be reachable from the browser, not a Docker-only hostname.

Set these values in **the web's `.env.local`**:

```dotenv
HARNESS_ENABLED=1
HARNESS_BACKEND=recorded
HARNESS_ALLOW_RECORDED=1
HARNESS_ROUTE_SNAPSHOT_ID=e2997f13380128fe116e038c3116d7fd4904ddf364eef79be1783c8e9cb0d06a
HARNESS_MAX_RUN_BUDGET_MICROS=10000000
HARNESS_MAX_DISPATCHES=32
HARNESS_MAX_REPAIRS=1
HARNESS_MAX_OUTPUT_TOKENS=8192
HARNESS_EVIDENCE_WINDOW_SENTENCES=80
HARNESS_MAX_RENDER_CONCURRENCY=2
```

In the worker terminal, start at the repository root and run:

```bash
export TEMNIA_CHECKOUT="$PWD"
set -a
source apps/pipeline/.env.example
set +a
export HARNESS_ENABLED=1
export HARNESS_BACKEND=recorded
export HARNESS_ALLOW_RECORDED=1
export HARNESS_ROUTE_SNAPSHOT_ID=e2997f13380128fe116e038c3116d7fd4904ddf364eef79be1783c8e9cb0d06a
export HARNESS_MAX_RUN_BUDGET_MICROS=10000000
export HARNESS_MAX_DISPATCHES=32
export HARNESS_MAX_REPAIRS=1
export HARNESS_MAX_OUTPUT_TOKENS=8192
export HARNESS_EVIDENCE_WINDOW_SENTENCES=80
export HARNESS_MAX_RENDER_CONCURRENCY=2
export HARNESS_ROUTE_SNAPSHOT_PATH=tests/fixtures/harness/routes.synthetic.json
export HARNESS_RECORDED_FIXTURE_PATH=tests/fixtures/harness/chapter.synthetic.json
unset TRANSCRIPTION_RECORDING
export TRANSCRIPTION_RECORDINGS_DIR=tests/fixtures/transcripts
export HF_HOME="$TEMNIA_CHECKOUT/.cache/temnia-models"
export TEMNIA_MODELS_DIR="$HF_HOME"
unset HF_HUB_OFFLINE
cd "$TEMNIA_CHECKOUT/apps/pipeline"
uv run --frozen python scripts/fetch_models.py
export HF_HUB_OFFLINE=1
cd "$TEMNIA_CHECKOUT"
pnpm --filter @temnia/pipeline worker
```

The pipeline does not load an env file automatically. The commands export the local example, which
selects local transcoding and recorded transcription, then override its disabled harness setting.
All harness limits must match the web, including overrides left in an existing `.env.local`.
The explicit single-file `TRANSCRIPTION_RECORDING` override is cleared so fixture-directory selection
can choose the response for each uploaded duration.
The model-fetch step downloads pinned SaT, tokenizer and
embedding snapshots once, then verifies local loading; dependency sync alone is insufficient.
Recorded transcription does not require the checkpointed provider's Silero ONNX boot asset.
Relative fixture paths above resolve from `apps/pipeline`, where the worker command runs.

In a second terminal, from the repository root:

```bash
pnpm dev
```

Wait for the web release phase to finish migrations and seed with `MIGRATE_DATABASE_URL` set.
This includes migration 0003 for the harness and 0004 for speech assignment. The worker should stay
healthy after its `worker up` line naming `temnia-pipeline`; the same process serves the derived
control queue, which does not have a separate startup line. Open <http://localhost:3000>.
The local Temporal UI is at <http://localhost:56080>. On the home page, enter a name and choose
**Run the hello workflow**. Require the greeting before uploading; this checks that web and worker
are using the same healthy Temporal namespace and queue.

## Use two fresh uploads

Create two projects from **Projects → New project → Create project**. In each, use **Upload a
master** to upload `apps/web/e2e/fixtures/speech-40s.mp4`. Reserve one untouched source for chapter
testing and use the other for transcript edits. Wait for **Ready**, then open the source title.
Play, pause and seek the video; inspect the waveform, Details and Artifacts.

The recorded chapter response refers to this fixture's original sentence and word IDs. Complete
both chapter runs below before correcting its transcript. An arbitrary personal video has no
recorded response, and a structurally edited fixture may no longer match the fixed proposal.

## Chapter journey: keep this order

1. On the untouched source, open **Chapters** and confirm **Recorded test backend**. Enter an
   **Editorial brief** and **Maximum budget (USD)** of `1.00`, then **Create chapters**. A negative
   budget should be refused without creating a run.
2. Wait for `needs review` and checked chapter media. Missing or failed technical checks must block
   acceptance and export; a warning remains visible for review. Test the chapter players separately
   from the main source player. **Preview cut** auditions about three seconds on either side of an
   internal cut and stops automatically; **Stop preview** ends it early.
3. Note the initial edit revision (`1`) and supply a **Review reason**. Try **Reject**, **Restore** on
   a drop, and **Merge next** on adjacent keeps. After **each** experiment, use **Undo to revision**
   with `1` and wait for its checked renders before trying the next. A drop needs an explicit reason;
   uncertain cuts also need one before acceptance. A restored keep receives a checked render.
4. Change a section's absolute **End time in seconds**, then **Nudge end**. Listen to both neighbors;
   manual timing is approximate. Their media should refresh. Use **Undo to revision** to reproduce
   revision `1` as a new revision, returning to the initial keep/drop partition before acceptance.
5. **Accept** every keep and **Acknowledge drop** for every omission. Until all sections are accepted
   and checks are eligible, accepted downloads must be absent. Once `ready`, find **Accepted files**,
   check the chapter count, and use **Download video** and **Download captions**. Open the saved video
   in a local player and inspect the captions. **Download manifest** is the separate structured
   export record; it is not a ZIP archive.
6. Nudge another boundary. The new revision returns to review while **Last accepted output** remains
   downloadable. Enter `2.00` in **New maximum budget (USD)** and choose **Raise budget**; existing review and
   accepted output should remain. Choose **Cancel** while this revision is still `needs review`.
   Expect `cancelled`, disabled editing/retry/budget controls and the earlier accepted export intact.
7. Choose **New run**, type a different brief, and wait through a status refresh before submitting.
   The form must remain. Create this second run while the transcript is still untouched. Use
   **Chapter run** to switch back to the older run and verify its history/export.
8. Finally, correct one word in this source's Transcript tab. Back in Chapters, expect a notice that
   the run uses an older transcript revision and a new run is needed to use corrections. Existing
   evidence and exports stay unchanged. End this recorded chapter scenario here: its fixed proposal
   is not guaranteed to support planning again after the correction.

## Transcript journey: use the other source

1. Open **Transcript**, click words to seek, and check the active word during playback. Try
   **Search the transcript**, **Next match** and **Previous match**. Scroll away, then use
   **Jump to current** to resume following playback.
2. Turn on **Edit**, change the selected **Word**, and **Save word**. Reload: the correction and
   advanced **Transcript revision** should persist. Rename speaker labels through **Speakers** and
   reassign a turn through its speaker chip. Equal display names must not merge speaker identities;
   use the explicit identity-merge control for that.
3. Try **Delete word**, **Split word**, **Insert before/after** with explicit millisecond times, and
   **Merge next**. Select an earlier **Undo to revision** and **Undo**. Undo creates a new revision;
   it does not erase history. Older selected revisions are read-only, and their **SRT**/**VTT** exports
   retain their own words and speaker labels.
4. Open this transcript in two tabs at the same revision. Save in the first, then save a different
   draft in the second. Expect “Someone changed this transcript since you opened it.” and a retained
   draft. **Reload** and review before saving. If the original target was deleted, saving must require
   selecting a new word explicitly; a shifted word index must never receive the draft silently.
5. Optionally test the empty edge: delete the remaining words, then insert the first word with times,
   or undo to revision 1. History and recovery controls should remain available with no lexical text.

## Additional recovery checks

For a larger upload, Uppy **Cancel** should remove the incomplete upload. Navigating away should
retain uploaded parts: return and pick the identical file to resume. A different browser waits out
the normal **60-second adoption grace**; the separate idle-upload reaper deadline is **24 hours**.
A failed ingest exposes **Retry ingest** from the project row. Sources with retained chapter history
refuse ordinary **Delete source**; upload cancellation and history deletion have different behavior.

For an optional recorded transcript failure test, upload `apps/web/e2e/fixtures/master-12s.mp4`
separately. Its deliberately invalid response reaches terminal failure; **Retry** must visibly queue
another attempt, though the same invalid fixture will fail again. `master-24s.mp4` exercises the
visible retrying state. Neither fixture is the chapter-planning input.

A lost chapter start/review response retains the original request and offers **Retry the same
request/command**. Do not change an unresolved request's identity by editing the form. A cancelled
run requires a new run; its accepted output remains available. An `outcome unknown` requires
reconciliation before more dispatches. The recorded happy path does not deliberately generate
budget pauses or unknown provider outcomes; their accounting/recovery is covered by controlled
Temporal/PostgreSQL tests.
Chapter **Retry** is available for `budget paused`/`failed`; **Cancel** for `pending`/`running`/
`needs review`. A `ready` run cannot be cancelled until an edit creates a new active revision.

## What is verified, and what to record

The release gate runs built web/worker images against real PostgreSQL, Garage, Temporal and ffmpeg
with recorded providers. Its 18 browser journeys cover upload/playback, transcript edits and
conflicts, lost/delayed responses, a 25,000-word virtualized transcript, structural undo/history,
and the chapter review/export/cancel journey. Synthetic boundaries and zero recorded inference cost
are not model-quality or price measurements. Live speech comparisons are documented separately in
the [performance report](../design/checkpointed-speech-optimization-2026-09-09.md).

For any manual failure, retain the source/run/revision ID, visible message, exact tested commit and
relevant web/worker log excerpt. Include whether **Recorded test backend** was visible. That makes
the failure reproducible without treating a passing automated suite as a promise of zero bugs.
