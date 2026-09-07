"use client";

import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { retryTranscription } from "@/app/actions/transcript";
import { TranscriptReader } from "@/components/sources/transcript-reader";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress";
import {
  isInFlight,
  type TranscriptRowSummary,
  transcriptState,
} from "@/lib/transcript/state";

/** The sources table's constant: what a person will sit and watch. */
const REFRESH_MS = 3500;

const HEADINGS: Record<string, string> = {
  empty: "Nothing to transcribe",
  failed: "Transcription failed",
  language: "Not this language yet",
  notReady: "Not yet",
  pending: "Queued",
  processing: "Transcribing",
  retrying: "Trying again",
  sourceFailed: "No transcript",
  stalled: "Still waiting",
};

interface TranscriptPanelProps {
  labels: Readonly<Record<string, string>>;
  /** The media-proxy URL of the current revision, or null when there is none. */
  revisionUrl: string | null;
  row: TranscriptRowSummary | null;
  sourceId: string;
  sourceStatus: "uploading" | "uploaded" | "processing" | "ready" | "failed";
  title: string;
}

/**
 * The transcript tab: one state at a time, with words and something to do.
 *
 * The state is chosen by `transcriptState` from the source and the transcript
 * row, so the words are tested without a browser and no server message reaches
 * the screen. While anything is still in flight the tab asks the server for a
 * newer row every 3.5 seconds, the same interval and the same reason as the
 * sources table.
 */
export function TranscriptPanel({
  labels,
  revisionUrl,
  row,
  sourceId,
  sourceStatus,
  title,
}: TranscriptPanelProps) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  // Zero until the browser has it. The server and the first client render then
  // agree on every state, including the one that depends on the clock, which
  // is what keeps the stall check out of the hydration diff.
  const [now, setNow] = useState(0);

  const state = transcriptState({ now, row, sourceStatus });
  const active = isInFlight(state);

  useEffect(() => {
    setNow(Date.now());
    if (!active) {
      return;
    }
    const timer = setInterval(() => {
      setNow(Date.now());
      if (navigator.onLine) {
        router.refresh();
      }
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, router]);

  const retry = () => {
    startTransition(async () => {
      const result = await retryTranscription(sourceId);
      setMessage(result.ok ? null : result.message);
      router.refresh();
    });
  };

  if (state.kind === "ready" && revisionUrl && row?.currentRevision) {
    return (
      <div data-state="ready" data-testid="transcript-tab">
        <TranscriptReader
          baseRevision={row.currentRevision}
          labels={labels}
          revisionUrl={revisionUrl}
          sourceId={sourceId}
          title={title}
        />
      </div>
    );
  }

  return (
    <div data-state={state.kind} data-testid="transcript-tab">
      <Empty>
        <EmptyHeader>
          <EmptyTitle>{HEADINGS[state.kind] ?? "Transcript"}</EmptyTitle>
          <EmptyDescription data-testid="transcript-words-state">
            {"words" in state ? state.words : null}
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          {state.kind === "processing" ? (
            <Progress
              className="w-64"
              data-testid="transcript-progress"
              value={state.percent}
            >
              <ProgressLabel className="sr-only">Transcribing</ProgressLabel>
              <ProgressValue className="sr-only" />
            </Progress>
          ) : null}
          {state.retry ? (
            <Button
              data-testid="transcript-retry"
              disabled={pending}
              onClick={retry}
              size="sm"
            >
              <HugeiconsIcon icon={RefreshIcon} />
              {pending ? "Starting…" : "Retry"}
            </Button>
          ) : null}
          {message ? (
            <span className="text-destructive text-xs" role="alert">
              {message}
            </span>
          ) : null}
        </EmptyContent>
      </Empty>
    </div>
  );
}
