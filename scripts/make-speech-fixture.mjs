#!/usr/bin/env node
// Builds apps/web/e2e/fixtures/speech-40s.mp4: a scripted two-speaker exchange
// spoken by two macOS voices, with pauses, muxed onto a testsrc picture.
//
// Why a second fixture at all. The 24-second master the gate has used since S1
// is synthetic: `testsrc` and a sine tone, measured at zero silences and no
// speech, so there is nothing in it to transcribe and nothing that would catch
// a normaliser that dropped every word. This one has two voices, real pauses,
// and turns short enough to keep the file small.
//
// Rights: every word is written here and spoken by the machine, so the file
// carries no third-party recording.
//
//   node scripts/make-speech-fixture.mjs [--out <path>] [--keep]
//
// macOS only (it needs `say`), and it is not run by the gate: the .mp4 is
// committed. Re-run it only to change the script, and commit the new file with
// the new transcript fixture.

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_OUT = join(ROOT, "apps/web/e2e/fixtures/speech-40s.mp4");

// Two voices a diarizer can tell apart: a US female and a UK male. Keep this
// in step with apps/pipeline/tests/fixtures/transcripts/speech-40s.whisperx.json,
// whose speaker ids are 0 for HOST and 1 for GUEST.
const HOST = "Samantha";
const GUEST = "Daniel";

// Rate in words per minute. The default varies between macOS releases; pinning
// it keeps a re-run close to the timings the transcript fixture carries.
const RATE = 150;

// A gap before each turn, in seconds. The first one gives the recording a
// moment of room tone, and the rest are the silences the cue builder splits on.
const TURNS = [
  {
    gap: 0.5,
    text: "Welcome back to the show. Today we are talking about how a small team keeps a long recording under control.",
    voice: HOST,
  },
  {
    gap: 0.7,
    text: "Thanks for having me. The honest answer is that we stopped treating the transcript as a by-product.",
    voice: GUEST,
  },
  { gap: 0.8, text: "What changed when you made that switch?", voice: HOST },
  {
    gap: 0.6,
    text: "Everything downstream got easier. Once every word carries a timestamp, the edit becomes a search problem.",
    voice: GUEST,
  },
  {
    gap: 0.9,
    text: "And the speakers? People always ask about crosstalk.",
    voice: HOST,
  },
  {
    gap: 0.6,
    text: "Diarization gets you most of the way. The rest is a rename box and about thirty seconds of attention.",
    voice: GUEST,
  },
  {
    gap: 0.8,
    text: "Perfect. Thanks for coming in today.",
    voice: HOST,
  },
];

function run(command, commandArgs, options = {}) {
  const result = spawnSync(command, commandArgs, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      `${command} ${commandArgs.join(" ")} exited ${result.status}: ${(result.stderr || "").trim()}`
    );
  }
  return result.stdout ?? "";
}

const args = process.argv.slice(2);
const outIndex = args.indexOf("--out");
const out = outIndex === -1 ? DEFAULT_OUT : resolve(args[outIndex + 1]);
const keep = args.includes("--keep");

if (process.platform !== "darwin") {
  throw new Error(
    "make-speech-fixture needs macOS `say`; the committed speech-40s.mp4 is the artefact"
  );
}

const work = mkdtempSync(join(tmpdir(), "temnia-speech-"));
try {
  // One wav per turn, and one per gap, concatenated in order. Everything is
  // brought to one layout (48 kHz mono, signed 16-bit) up front, so the concat
  // demuxer has nothing to negotiate and can copy.
  const parts = [];
  for (const [index, turn] of TURNS.entries()) {
    const silence = join(work, `gap-${index}.wav`);
    run("ffmpeg", [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      `anullsrc=r=48000:cl=mono:d=${turn.gap}`,
      silence,
    ]);
    parts.push(silence);

    // `say` writes 32-bit float WAV directly; the re-encode to the 16-bit
    // layout the silence files use is what lets the concat demuxer copy.
    const spoken = join(work, `spoken-${index}.wav`);
    run("say", [
      "-v",
      turn.voice,
      "-r",
      String(RATE),
      "-o",
      spoken,
      "--data-format=LEF32@48000",
      turn.text,
    ]);
    const wav = join(work, `turn-${index}.wav`);
    run("ffmpeg", [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-i",
      spoken,
      "-ac",
      "1",
      "-ar",
      "48000",
      "-c:a",
      "pcm_s16le",
      wav,
    ]);
    parts.push(wav);
  }
  // A closing tail, so the last word is not flush against the end of the file:
  // a transcript whose final word ends after the container does is exactly what
  // the contract's one-directional duration check refuses.
  const tail = join(work, "tail.wav");
  run("ffmpeg", [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    "anullsrc=r=48000:cl=mono:d=1.0",
    tail,
  ]);
  parts.push(tail);

  const list = join(work, "parts.txt");
  run("bash", [
    "-c",
    `printf '%s\\n' ${parts.map((p) => `"file '${p}'"`).join(" ")} > "${list}"`,
  ]);
  const speech = join(work, "speech.wav");
  run("ffmpeg", [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    list,
    "-c",
    "copy",
    speech,
  ]);

  const seconds = Number.parseFloat(
    run("ffprobe", [
      "-v",
      "error",
      "-show_entries",
      "format=duration",
      "-of",
      "csv=p=0",
      speech,
    ]).trim()
  );

  mkdirSync(dirname(out), { recursive: true });
  // 640x360 at 25 fps: the picture is only there so the file is a real master
  // the ingest can probe and ladder. AAC at 96k matches the extract the
  // pipeline makes, so the fixture and the extract sound the same.
  run("ffmpeg", [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    `testsrc=size=640x360:rate=25:duration=${seconds.toFixed(3)}`,
    "-i",
    speech,
    "-map",
    "0:v:0",
    "-map",
    "1:a:0",
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-pix_fmt",
    "yuv420p",
    "-g",
    "50",
    "-c:a",
    "aac",
    "-b:a",
    "96k",
    "-movflags",
    "+faststart",
    "-shortest",
    out,
  ]);

  const { size } = statSync(out);
  process.stdout.write(
    `${out}\n${seconds.toFixed(2)} s, ${size} bytes, ${TURNS.length} turns, voices ${HOST} and ${GUEST} at ${RATE} wpm\n`
  );
} finally {
  if (keep) {
    process.stdout.write(`kept ${work}\n`);
  } else {
    rmSync(work, { force: true, recursive: true });
  }
}
