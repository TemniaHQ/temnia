/**
 * Every row of the S2 plan's §5 table, as words.
 *
 * These are the sentences a user reads, so they are asserted literally: a
 * change to one of them is a change to the product and has to be made on
 * purpose. The rule that matters most is the last one: a server message never
 * reaches the screen, whatever it says.
 */
import { describe, expect, it } from "vitest";
import {
  isInFlight,
  STALL_AFTER_MS,
  type TranscriptRowSummary,
  transcriptState,
} from "@/lib/transcript/state";

const NOW = Date.parse("2026-09-07T12:00:00.000Z");

function row(over: Partial<TranscriptRowSummary> = {}): TranscriptRowSummary {
  return {
    currentRevision: null,
    errorMessage: null,
    heartbeatAt: new Date(NOW - 1000).toISOString(),
    percent: null,
    stage: null,
    status: "pending",
    updatedAt: new Date(NOW - 1000).toISOString(),
    wordCount: null,
    ...over,
  };
}

function state(
  over: Partial<TranscriptRowSummary> | null,
  sourceStatus: "processing" | "ready" | "failed" = "ready"
) {
  return transcriptState({
    now: NOW,
    row: over === null ? null : row(over),
    sourceStatus,
  });
}

describe("transcriptState", () => {
  it("waits for the ingest before promising anything", () => {
    const waiting = state(null, "processing");
    expect(waiting.kind).toBe("notReady");
    expect(waiting).toHaveProperty(
      "words",
      "The transcript starts after processing finishes."
    );
  });

  it("does not offer a transcript for a recording that failed to process", () => {
    const failed = state(null, "failed");
    expect(failed.kind).toBe("sourceFailed");
    expect(isInFlight(failed)).toBe(false);
  });

  it("is pending with no row at all, and offers a retry as the way out", () => {
    // A ready source with no row and no run coming (an older source, a queue
    // the ingest could not reach) said "Queued" for ever (S2 review, I19).
    expect(state(null)).toEqual({
      kind: "pending",
      retry: true,
      words: "Queued for transcription.",
    });
    expect(state({ status: "pending" }).retry).toBe(true);
  });

  it("names the stage and the percent while it runs", () => {
    expect(
      state({ percent: 62, stage: "align", status: "processing" })
    ).toEqual({
      kind: "processing",
      percent: 62,
      retry: false,
      words: "Transcribing · Aligning words · 62%",
    });
  });

  it("says only what it knows when there is no stage or percent", () => {
    expect(state({ status: "processing" })).toHaveProperty(
      "words",
      "Transcribing"
    );
  });

  it("never renders an engine stage name it does not have words for", () => {
    expect(
      state({ percent: 5, stage: "quantise", status: "processing" })
    ).toHaveProperty("words", "Transcribing · 5%");
  });

  it("says a retry is coming instead of flashing failed", () => {
    const retrying = state({ stage: "retrying", status: "processing" });
    expect(retrying.kind).toBe("retrying");
    expect(retrying).toHaveProperty(
      "words",
      "Transcription stopped unexpectedly and is being retried."
    );
  });

  it("calls a run with no heartbeat for five minutes stalled", () => {
    const stalled = state({
      heartbeatAt: new Date(NOW - STALL_AFTER_MS - 1).toISOString(),
      stage: "transcribe",
      status: "processing",
    });
    expect(stalled.kind).toBe("stalled");
    // No promise of a retry the row cannot vouch for; a Retry button instead,
    // and the action asks Temporal whether the run is alive (S2 review, I23).
    expect(stalled).toEqual({
      kind: "stalled",
      retry: true,
      words: "Transcription has not reported progress for a while.",
    });
  });

  it("is still processing one millisecond before the stall window", () => {
    expect(
      state({
        heartbeatAt: new Date(NOW - STALL_AFTER_MS).toISOString(),
        status: "processing",
      }).kind
    ).toBe("processing");
  });

  it("offers recovery before retry wording when retrying has gone stale", () => {
    expect(
      state({
        heartbeatAt: new Date(NOW - 86_400_000).toISOString(),
        stage: "retrying",
        status: "processing",
      })
    ).toMatchObject({ kind: "stalled", retry: true });
  });

  it("ages a missing heartbeat from the last row update", () => {
    for (const stage of ["download", "retrying"]) {
      expect(
        state({
          heartbeatAt: null,
          stage,
          status: "processing",
          updatedAt: new Date(NOW - STALL_AFTER_MS - 1).toISOString(),
        })
      ).toMatchObject({ kind: "stalled", retry: true });
    }
  });

  it("never reads a clock the server does not have: now 0 is not stalled", () => {
    // The panel holds `now` at zero until an effect has run, so the server and
    // the first client render choose the same state for the same row. A stall
    // decided from a real clock during SSR would be a text mismatch, and on a
    // production build that is React #418 and a regenerated tree.
    expect(
      transcriptState({
        now: 0,
        row: row({
          heartbeatAt: new Date(NOW - STALL_AFTER_MS - 1).toISOString(),
          stage: "transcribe",
          status: "processing",
        }),
        sourceStatus: "ready",
      }).kind
    ).toBe("processing");
  });

  it("is processing when no heartbeat has been written yet", () => {
    expect(state({ heartbeatAt: null, status: "processing" }).kind).toBe(
      "processing"
    );
  });

  it("offers a retry for a failure that another attempt could fix", () => {
    const failed = state({
      errorMessage: "TranscriptionProviderFailure: connection reset by peer",
      status: "failed",
    });
    expect(failed).toEqual({
      kind: "failed",
      retry: true,
      words: "Transcription failed: the transcription service was unavailable.",
    });
  });

  it("shows the language code and no retry for a language it cannot align", () => {
    const failed = state({
      errorMessage:
        "UnsupportedLanguageError: this recording is in a language we cannot " +
        "align yet (code: ta). 21 languages are supported by the deployed model.",
      status: "failed",
    });
    expect(failed).toEqual({
      kind: "language",
      retry: false,
      words: "This recording is in a language we cannot align yet (code: ta).",
    });
  });

  it("says unknown rather than nothing when the code cannot be read", () => {
    expect(
      state({
        errorMessage: "UnsupportedLanguageError: no code in this one",
        status: "failed",
      })
    ).toHaveProperty(
      "words",
      "This recording is in a language we cannot align yet (code: unknown)."
    );
  });

  it("says there is no audio, with no retry, when the ingest found none", () => {
    expect(
      state({
        errorMessage: "NoAudioError: the recording has no audio track",
        status: "failed",
      })
    ).toEqual({
      kind: "noAudio",
      retry: false,
      words: "This recording has no audio track, so there is no transcript.",
    });
  });

  it("offers a retry when the start itself could not be queued", () => {
    expect(
      state({
        errorMessage: "DispatchError: connect ECONNREFUSED 127.0.0.1:7233",
        status: "failed",
      })
    ).toEqual({
      kind: "failed",
      retry: true,
      words: "Transcription could not be queued. Try again.",
    });
  });

  it("does not blame the service's availability for an unusable result", () => {
    for (const prefix of ["TranscriptContractError:", "ValidationError:"]) {
      expect(
        state({ errorMessage: `${prefix} no segments list`, status: "failed" })
      ).toEqual({
        kind: "failed",
        retry: true,
        words: "The transcription service returned an unusable result.",
      });
    }
  });

  it("never renders the server's own message", () => {
    const secret =
      "OSError: /var/lib/temnia/work/xyz: No such file or directory";
    const failed = state({ errorMessage: secret, status: "failed" });
    expect(JSON.stringify(failed)).not.toContain("temnia/work");
  });

  it("is empty, with a retry, for a recording with no speech", () => {
    expect(
      state({ currentRevision: 1, status: "ready", wordCount: 0 })
    ).toEqual({
      kind: "empty",
      retry: true,
      words: "No speech was detected in this recording.",
    });
  });

  it("is empty when a ready row somehow has no revision", () => {
    expect(state({ status: "ready" }).kind).toBe("empty");
  });

  it("is ready when there are words to read", () => {
    expect(
      state({ currentRevision: 2, status: "ready", wordCount: 93 })
    ).toEqual({ kind: "ready", retry: false });
  });
});

describe("isInFlight", () => {
  it("keeps polling only while something is still going to change", () => {
    expect(isInFlight(state(null, "processing"))).toBe(true);
    expect(isInFlight(state(null))).toBe(true);
    expect(isInFlight(state({ status: "processing" }))).toBe(true);
    expect(isInFlight(state({ stage: "retrying", status: "processing" }))).toBe(
      true
    );
    expect(isInFlight(state({ errorMessage: "x: y", status: "failed" }))).toBe(
      false
    );
    expect(
      isInFlight(state({ currentRevision: 1, status: "ready", wordCount: 5 }))
    ).toBe(false);
  });
});
