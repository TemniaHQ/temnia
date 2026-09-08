import type { TranscriptStage } from "@temnia/contracts";

/**
 * The transcript tab's state, and the words that go with it.
 *
 * Every row of the S2 plan's §5 table is one `kind` here, decided from the
 * source's status and the transcript row alone, so the words a user reads are
 * a pure function of the database and are tested without a browser. A raw
 * server message is never one of them: `errorMessage` is classified, never
 * rendered (AGENTS.md, user states).
 */

/** Past this without a heartbeat the run is not moving; Temporal will retry it. */
export const STALL_AFTER_MS = 5 * 60 * 1000;

/** The engine's stage names in words a reader can act on. */
const STAGE_LABELS: Record<TranscriptStage, string> = {
  align: "Aligning words",
  diarize: "Identifying speakers",
  download: "Fetching audio",
  model: "Loading the model",
  planning: "Preparing speech analysis",
  retrying: "Starting again",
  speech_coverage: "Checking speech coverage",
  transcribe: "Listening",
  write: "Saving the transcript",
};

/**
 * The failures with words of their own.
 *
 * The pipeline puts the exception's type name in front of its message
 * (`transcription/runner.py`, and the ingest's `NoAudioError:` and the retry
 * action's `DispatchError:`), which is what makes these tests on the failure
 * rather than on its wording. Two are final and offer no Retry: a language no
 * model aligns, and a recording with no audio. Every other failure is offered
 * one: a retry of a deterministic failure costs one more attempt and says the
 * same thing again, while hiding Retry from a transient one leaves a dead end
 * (S2 review, I19 and I23).
 */
const UNSUPPORTED_LANGUAGE = "UnsupportedLanguageError:";
const NO_AUDIO = "NoAudioError:";
const DISPATCH_FAILED = "DispatchError:";
const UNUSABLE_RESULT = ["TranscriptContractError:", "ValidationError:"];
const LANGUAGE_CODE = /\(code:\s*([\w-]+)\)/;

export type TranscriptTabState =
  | { kind: "empty"; retry: true; words: string }
  | { kind: "failed"; retry: true; words: string }
  | { kind: "language"; retry: false; words: string }
  | { kind: "noAudio"; retry: false; words: string }
  | { kind: "notReady"; retry: false; words: string }
  | { kind: "pending"; retry: true; words: string }
  | { kind: "processing"; percent: number | null; retry: false; words: string }
  | { kind: "ready"; retry: false }
  | { kind: "retrying"; retry: false; words: string }
  | { kind: "sourceFailed"; retry: false; words: string }
  | { kind: "stalled"; retry: true; words: string };

export interface TranscriptRowSummary {
  currentRevision: number | null;
  errorMessage: string | null;
  /** ISO 8601, as it crosses the server-to-client boundary. */
  heartbeatAt: string | null;
  percent: number | null;
  stage: string | null;
  status: "pending" | "processing" | "ready" | "failed";
  /** Used to age a claimed row before its first heartbeat. */
  updatedAt: string;
  /** The current revision's word count; zero remains an editable revision. */
  wordCount: number | null;
}

export interface TranscriptStateInput {
  now: number;
  row: TranscriptRowSummary | null;
  sourceStatus: "uploading" | "uploaded" | "processing" | "ready" | "failed";
}

function stageWords(stage: string | null): string | null {
  if (!stage) {
    return null;
  }
  return STAGE_LABELS[stage as TranscriptStage] ?? null;
}

/** Stages whose reported percentage represents completed transfer work. */
const MEASURED_ZERO_STAGES = new Set(["download", "write"]);

function visiblePercent(row: TranscriptRowSummary): number | null {
  if (row.percent === null) {
    return null;
  }
  const percent = Math.min(100, Math.max(0, row.percent));
  return percent === 0 && !MEASURED_ZERO_STAGES.has(row.stage ?? "")
    ? null
    : percent;
}

function livenessWords(row: TranscriptRowSummary, now: number): string | null {
  if (now <= 0) {
    return null;
  }
  const beat = Date.parse(row.heartbeatAt ?? row.updatedAt);
  if (Number.isNaN(beat)) {
    return null;
  }
  const seconds = Math.max(0, Math.floor((now - beat) / 1000));
  if (seconds < 15) {
    return "Updated just now";
  }
  if (seconds < 60) {
    return `Updated ${seconds}s ago`;
  }
  return `Updated ${Math.floor(seconds / 60)}m ago`;
}

function processingWords(
  row: TranscriptRowSummary,
  percent: number | null,
  now: number
): string {
  const parts = ["Transcribing"];
  const stage = stageWords(row.stage);
  if (stage) {
    parts.push(stage);
  }
  if (percent === null) {
    const liveness = livenessWords(row, now);
    if (liveness) {
      parts.push(liveness);
    }
  } else {
    parts.push(`${percent}%`);
  }
  return parts.join(" · ");
}

function failedState(row: TranscriptRowSummary): TranscriptTabState {
  const message = row.errorMessage ?? "";
  if (message.startsWith(UNSUPPORTED_LANGUAGE)) {
    const code = LANGUAGE_CODE.exec(message)?.[1] ?? "unknown";
    return {
      kind: "language",
      retry: false,
      words: `This recording is in a language we cannot align yet (code: ${code}).`,
    };
  }
  if (message.startsWith(NO_AUDIO)) {
    return {
      kind: "noAudio",
      retry: false,
      words: "This recording has no audio track, so there is no transcript.",
    };
  }
  if (message.startsWith(DISPATCH_FAILED)) {
    return {
      kind: "failed",
      retry: true,
      words: "Transcription could not be queued. Try again.",
    };
  }
  if (UNUSABLE_RESULT.some((prefix) => message.startsWith(prefix))) {
    return {
      kind: "failed",
      retry: true,
      words: "The transcription service returned an unusable result.",
    };
  }
  return {
    kind: "failed",
    retry: true,
    words: "Transcription failed: the transcription service was unavailable.",
  };
}

function processingState(
  row: TranscriptRowSummary,
  now: number
): TranscriptTabState {
  const beat = Date.parse(row.heartbeatAt ?? row.updatedAt);
  if (!Number.isNaN(beat) && now - beat > STALL_AFTER_MS) {
    // No promise of a retry: the row cannot know whether a run is still
    // behind it. Retry is offered, and the action asks Temporal first.
    return {
      kind: "stalled",
      retry: true,
      words: "Transcription has not reported progress for a while.",
    };
  }
  if (row.stage === "retrying") {
    return {
      kind: "retrying",
      retry: false,
      words: "Transcription stopped unexpectedly and is being retried.",
    };
  }
  const percent = visiblePercent(row);
  return {
    kind: "processing",
    percent,
    retry: false,
    words: processingWords(row, percent, now),
  };
}

/** The state the tab is in, from the source and the transcript row. */
export function transcriptState({
  row,
  sourceStatus,
  now,
}: TranscriptStateInput): TranscriptTabState {
  if (sourceStatus === "failed") {
    return {
      kind: "sourceFailed",
      retry: false,
      words:
        "This recording could not be processed, so there is no transcript.",
    };
  }
  if (sourceStatus !== "ready") {
    return {
      kind: "notReady",
      retry: false,
      words: "The transcript starts after processing finishes.",
    };
  }
  if (!row || row.status === "pending") {
    // Retry is offered while queued: a start that never happened (an older
    // source, a queue the ingest could not reach) has no other way out, and a
    // start that is about to happen makes the retry a no-op under the
    // workflow id policy.
    return {
      kind: "pending",
      retry: true,
      words: "Queued for transcription.",
    };
  }
  if (row.status === "processing") {
    return processingState(row, now);
  }
  if (row.status === "failed") {
    return failedState(row);
  }
  if (row.currentRevision === null) {
    return {
      kind: "empty",
      retry: true,
      words: "No speech was detected in this recording.",
    };
  }
  return { kind: "ready", retry: false };
}

/** True while the tab should keep asking the server for a newer row. */
export function isInFlight(state: TranscriptTabState): boolean {
  return (
    state.kind === "notReady" ||
    state.kind === "pending" ||
    state.kind === "processing" ||
    state.kind === "retrying" ||
    state.kind === "stalled"
  );
}
