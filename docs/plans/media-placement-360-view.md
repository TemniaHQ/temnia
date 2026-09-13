# Media work placement: evidence at ingest, rendering on the GPU

2026-09-13. Rajesh: "which steps should run on CPU and which on GPU for better performance and
fast result?" The answer has two parts, built in this order.

## Where the time goes today (Karma, 44 min source)

| Step | Runs on | Once per | Time |
| --- | --- | --- | --- |
| Transcode ladder | Modal L4, NVENC | source | inside the 11 min ingest |
| Transcription (WhisperX large-v3) | Modal L4 | source | inside the 11 min ingest |
| Master download to the worker | VPS | **run** | seconds to a minute |
| Shot detection (`scdet` over the master) | VPS CPU | source (cached by identity) | minutes on the first run |
| Speech coverage (Silero VAD) | VPS CPU | source (cached) | under a minute on the first run |
| Sentence grid, evidence assembly | VPS CPU | run | seconds |
| Model calls | provider GPUs | run | 20 of 25 minutes |
| Rendering (exact interval, libx264 medium, crf 18, two at a time) | VPS CPU | run | minutes per clip |
| Technical checks (full decode of every render) | VPS CPU | run | seconds per clip |

Two things are wrong with this picture. The first run on every source pays the source
sensors inside the topic run, and every run downloads the master to the VPS before a single
model call. And the only encode on the CPU is the one users wait for.

## Part 1: evidence at ingest

**Decision.** The three source sensors, the timeline facts, shot evidence and speech
coverage, are measured once, at ingest, on the worker that already holds the master for the
probe, and published as source artifacts bound to the master's object identity. A topic run
reads them; it downloads nothing until it renders.

**Design.**

- A new ingest activity `measure_source_sensors` runs after `derive_source` and before
  `finalize_source` (the master is still in the source work directory). It inspects the
  timeline, hashes the master, publishes `source-timeline/1` (timeline facts, master
  sha256, object identity), then the existing shot and speech builders with the local file.
- The shot binding's fingerprint no longer includes the ffmpeg binary hash; it stays in the
  artifact body as provenance. A deploy with a new ffmpeg must not invalidate a source's
  sensors.
- Speech measurement is split from its projection: measuring needs the timeline; the
  projection onto the transcript (and the duration equality check) happens where the
  transcript is known, in the run.
- `build_chapter_evidence` first tries the download-free path: head the master for its
  object identity, find `source-timeline/1` for that identity, take the sha256 and timeline
  from it, find the shot and speech artifacts by their bindings, assemble the evidence. If
  any of the three is missing (a source ingested before this change, or a sensor that
  failed at ingest), it falls back to today's path unchanged: download, measure, cache.
- Sensors that fail at ingest never fail the ingest: the artifact records `unavailable`
  with the reason, exactly as the run-time builders already do.

**Edges.** A source whose master object changes (re-upload) has a new identity and new
sensors. A source with no video records shots unavailable. Transcript revisions do not
touch the sensors; only the evidence names a transcript revision. The gate's Playwright
ingest journey exercises the activity on the fixture master.

**Tests.** The activity publishes the three artifacts for the fixture master; a run built on
them produces byte-identical evidence to the download path and touches no source cache; a
source without sensors still builds evidence the old way; the shot fingerprint ignores the
binary hash.

**Result.** A topic run starts its first model call within seconds of the click. The VPS
decodes a master once per source, at ingest, when nobody is waiting on a run.

## Part 2: rendering on the GPU

**Decision.** Rendering keeps its exact-interval semantics (decode, trim on the common
clock, re-encode) and moves to the Modal box that already has NVENC and the master. The
worker keeps verification and publication.

**Why not smart-cut first.** Copying the compressed stream between keyframes and
re-encoding only the edge GOPs would make rendering nearly free, but concatenating a
libx264-encoded edge with copied master GOPs mixes parameter sets inside one MP4. ffmpeg
tolerates it; browser decoders are not guaranteed to, and the panel plays these files in the
browser. It is worth proving on the HLS rung our own encoder produced (fixed GOP, one SPS),
after the GPU path exists, not before.

**Design.**

- A Modal function `render_sections(job)` in the media app: downloads the master (verified
  by sha256), runs the same `render_chapter` command per section with `h264_nvenc` in place
  of libx264 (preset p5, constant quality matching crf 18), sections in parallel on the
  card, uploads each MP4 under the run's render prefix, returns per-section sha256 and
  size. The command builder is shared; only the encoder block differs.
- The worker's render activity submits the job, heartbeats with the Modal call id (the same
  reattach pattern the ladder uses), downloads each output, runs the technical checks, and
  publishes descriptors and checks as today. Captions and the exact-extent identity are
  unchanged.
- `ChapterRenderConfig` gains `encoder: "libx264" | "h264_nvenc"`; the media fingerprint
  includes it, so a GPU render and a CPU render are different artifacts.
- Fallback: when `TRANSCODE_BACKEND` is not Modal (the gate, local development) the render
  stays on the CPU exactly as today.

**Rollout.** The Modal app is deployed by Rajesh (`modal deploy`), then the worker's
deployment file switches `render.encoder` to `h264_nvenc`.

**Result.** Rendering time for a run goes from minutes per clip on the VPS to seconds per
clip on the card, in parallel, and the VPS stops encoding entirely.
