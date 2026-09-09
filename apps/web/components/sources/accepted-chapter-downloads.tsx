"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type AcceptedDownloads,
  acceptedDownloadsErrorMessage,
  acceptedDownloadsIdentity,
  loadAcceptedDownloads,
} from "@/lib/harness/accepted-downloads";
import type { ChapterView } from "@/lib/harness/queries";

interface AcceptedChapterDownloadsProps {
  sourceId: string;
  view: ChapterView;
}

type DownloadState =
  | { identity: null; status: "absent" }
  | { attempt: number; identity: string; status: "loading" }
  | { identity: string; message: string; status: "error" }
  | { identity: string; status: "ready"; value: AcceptedDownloads };

export function AcceptedChapterDownloads({
  sourceId,
  view,
}: AcceptedChapterDownloadsProps) {
  const identity = acceptedDownloadsIdentity({ sourceId, view });
  const input = useRef({ sourceId, view });
  input.current = { sourceId, view };
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<DownloadState>({
    identity: null,
    status: "absent",
  });

  useEffect(() => {
    if (!identity) {
      setState({ identity: null, status: "absent" });
      return;
    }
    let active = true;
    setState({ attempt: retry, identity, status: "loading" });
    loadAcceptedDownloads(input.current)
      .then((value) => {
        if (active) {
          setState({ identity, status: "ready", value });
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setState({
            identity,
            message: acceptedDownloadsErrorMessage(error),
            status: "error",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [identity, retry]);

  if (!identity) {
    return null;
  }
  if (state.identity !== identity || state.status === "loading") {
    return (
      <p className="text-muted-foreground text-sm" role="status">
        Verifying accepted files…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div className="space-y-2" data-testid="accepted-downloads-error">
        <p className="text-destructive text-sm" role="alert">
          {state.message}
        </p>
        <Button
          onClick={() => setRetry((value) => value + 1)}
          size="sm"
          variant="outline"
        >
          Retry accepted files
        </Button>
      </div>
    );
  }
  if (state.status !== "ready") {
    return null;
  }

  const isLastAccepted = view.currentEdit?.id !== view.acceptedEdit?.id;
  return (
    <section className="space-y-3" data-testid="accepted-downloads">
      <div className="space-y-1">
        <p className="font-medium text-sm">
          {isLastAccepted ? "Last accepted output" : "Accepted files"}
        </p>
        <a
          className="text-sm underline"
          data-testid="accepted-download-manifest"
          download={state.value.manifest.download}
          href={state.value.manifest.href}
        >
          Download manifest
        </a>
      </div>
      {state.value.warnings.map((warning) => (
        <p className="text-amber-700 text-sm" key={warning}>
          Technical warning: {warning}
        </p>
      ))}
      {state.value.chapters.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          This accepted revision deliberately drops the complete source, so it
          has no chapter media or captions to download.
        </p>
      ) : (
        <ol className="space-y-3">
          {state.value.chapters.map((chapter, index) => (
            <li className="space-y-1" key={chapter.sectionId}>
              <p className="text-sm">
                {index + 1}. {chapter.title}
              </p>
              <div className="flex flex-wrap gap-3">
                <a
                  className="text-sm underline"
                  data-testid={`accepted-download-video-${chapter.sectionId}`}
                  download={chapter.video.download}
                  href={chapter.video.href}
                >
                  Download video
                </a>
                {chapter.captions ? (
                  <a
                    className="text-sm underline"
                    data-testid={`accepted-download-captions-${chapter.sectionId}`}
                    download={chapter.captions.download}
                    href={chapter.captions.href}
                  >
                    Download captions
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
