"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getPendingTopicWorkflowStatus,
  type PendingTopicWorkflowStatus,
  reviewTopicCommand,
  startTopicRun,
} from "@/app/actions/topics";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { HarnessAvailability } from "@/lib/harness/config";
import type { ChapterView } from "@/lib/harness/queries";
import {
  loadTopicRevision,
  reusedContextMs,
  type TopicRevisionView,
  type TopicVideoView,
  topicArtifactIdentity,
  topicEditorialStatus,
} from "@/lib/harness/topic-artifacts";
import { DEFAULT_TOPIC_BRIEF_VERSION } from "@/lib/harness/topic-defaults";
import {
  restoreTopicIntent,
  type TopicReviewIntent,
  TopicReviewIntentSchema,
  type TopicStartIntent,
  TopicStartIntentSchema,
} from "@/lib/harness/topic-pending";
import { formatDuration } from "@/lib/sources/labels";

function remember(key: string, value: unknown) {
  try {
    if (value === null) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, JSON.stringify(value));
    }
  } catch {
    // The active component still retains the exact request if storage is unavailable.
  }
}

function emptyView(previous: ChapterView): ChapterView {
  return {
    ...previous,
    acceptedEdit: null,
    artifacts: [],
    currentEdit: null,
    events: [],
    run: null,
  };
}

export function TopicPanel({
  availability,
  initialView,
  sourceId,
}: {
  availability: HarnessAvailability;
  initialView: ChapterView;
  sourceId: string;
}) {
  const [view, setView] = useState(initialView);
  const [selectedRunId, setSelectedRunId] = useState(initialView.run?.id ?? "");
  const [brief, setBrief] = useState("");
  const [pendingStart, setPendingStart] = useState<TopicStartIntent | null>(
    null
  );
  const [pendingReview, setPendingReview] = useState<TopicReviewIntent | null>(
    null
  );
  const [pendingStatus, setPendingStatus] =
    useState<PendingTopicWorkflowStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [current, setCurrent] = useState<TopicRevisionView | null>(null);
  const [accepted, setAccepted] = useState<TopicRevisionView | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const startKey = `topic-pending-start:${sourceId}`;
  const reviewKey = `topic-pending-review:${sourceId}`;
  const selection = useRef(selectedRunId);
  selection.current = selectedRunId;
  const refreshSequence = useRef(0);
  const snapshot = useRef(view);
  snapshot.current = view;
  const requestIdentity = topicArtifactIdentity(view);
  const blocked = busy || !hydrated || !!pendingStart || !!pendingReview;

  useEffect(() => {
    try {
      const restoredStart = restoreTopicIntent(
        sessionStorage.getItem(startKey),
        TopicStartIntentSchema
      );
      const restoredReview = restoreTopicIntent(
        sessionStorage.getItem(reviewKey),
        TopicReviewIntentSchema
      );
      if (restoredStart?.sourceId === sourceId) {
        setPendingStart(restoredStart);
        setBrief(restoredStart.brief ?? "");
        setSelectedRunId(restoredStart.runId);
      }
      if (restoredReview?.sourceId === sourceId) {
        setPendingReview(restoredReview);
        setSelectedRunId(restoredReview.runId);
      }
    } catch {
      // A browser that disables session storage can still use this mounted view.
    }
    setHydrated(true);
  }, [sourceId, startKey, reviewKey]);

  const refresh = useCallback(async () => {
    const runId = selectedRunId;
    if (!runId) {
      return;
    }
    refreshSequence.current += 1;
    const sequence = refreshSequence.current;
    await fetchTopicStatus(sourceId, runId, pendingStart, pendingReview)
      .then(({ next, settled }) => {
        if (
          selection.current !== runId ||
          refreshSequence.current !== sequence
        ) {
          return;
        }
        setView(next);
        if (settled.startDone) {
          remember(startKey, null);
          setPendingStart(null);
        }
        if (settled.reviewDone) {
          remember(reviewKey, null);
          setPendingReview(null);
        }
        setPendingStatus(settled.status);
        if (settled.message) {
          setMessage(settled.message);
        }
      })
      .catch((error: unknown) => {
        if (
          selection.current === runId &&
          refreshSequence.current === sequence
        ) {
          setMessage(
            error instanceof Error
              ? error.message
              : "Topic status is unavailable."
          );
        }
      });
  }, [
    selectedRunId,
    pendingStart,
    pendingReview,
    sourceId,
    startKey,
    reviewKey,
  ]);

  useEffect(() => {
    if (!(hydrated && selectedRunId)) {
      return;
    }
    refresh();
    const timer = setInterval(() => {
      refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, [hydrated, selectedRunId, refresh]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: immutable artifact identity triggers loads; polling-only view changes must not reset playback
  useEffect(() => {
    let active = true;
    setCurrent(null);
    setAccepted(null);
    setArtifactError(null);
    Promise.all([
      loadTopicRevision(sourceId, snapshot.current),
      loadTopicRevision(sourceId, snapshot.current, true),
    ])
      .then(([currentRevision, acceptedRevision]) => {
        if (active) {
          setCurrent(currentRevision);
          setAccepted(acceptedRevision);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setArtifactError(
            error instanceof Error
              ? error.message
              : "The topic files could not be verified."
          );
        }
      });
    return () => {
      active = false;
    };
  }, [sourceId, requestIdentity]);

  async function start(intent = pendingStart) {
    if (busy || (!intent && blocked)) {
      return;
    }
    const request = intent ?? {
      brief: brief.trim(),
      defaultBriefVersion: DEFAULT_TOPIC_BRIEF_VERSION,
      requestKey: crypto.randomUUID(),
      runId: crypto.randomUUID(),
      sourceId,
    };
    remember(startKey, request);
    setPendingStart(request);
    selection.current = request.runId;
    setSelectedRunId(request.runId);
    setView((previous) => emptyView(previous));
    setBusy(true);
    setMessage(null);
    setPendingStatus(null);
    try {
      const result = await startTopicRun(request);
      if (!result.ok) {
        setMessage(result.message);
      }
      if (!result.pending) {
        remember(startKey, null);
        setPendingStart(null);
      }
    } catch {
      setMessage(
        "The start result is unknown. Check or retry this same request."
      );
    } finally {
      setBusy(false);
    }
  }

  async function review(
    action: "accept" | "reject" | "cancel",
    sectionId: string | null,
    reason: string,
    existing?: TopicReviewIntent
  ) {
    if (busy || (!existing && (blocked || !view.run))) {
      return;
    }
    const request: TopicReviewIntent = existing ?? {
      action,
      baseRevision: view.run?.currentRevision ?? 0,
      boundaryId: null,
      budgetMicros: null,
      mutationKey: crypto.randomUUID(),
      otherSectionId: null,
      reason,
      runId: view.run?.id ?? "",
      sectionId,
      sourceId,
      targetRevision: null,
      targetTimeMs: null,
    };
    remember(reviewKey, request);
    setPendingReview(request);
    setPendingStatus(null);
    setBusy(true);
    setMessage(null);
    try {
      const result = await reviewTopicCommand(request);
      if (!result.ok) {
        setMessage(result.message);
      }
      if (!result.pending) {
        remember(reviewKey, null);
        setPendingReview(null);
      }
      await refresh();
    } catch {
      setMessage(
        "The review result is unknown. Check or retry this same command."
      );
    } finally {
      setBusy(false);
    }
  }

  function selectRun(id: string) {
    if (blocked) {
      return;
    }
    selection.current = id;
    setSelectedRunId(id);
    setView((previous) => emptyView(previous));
    setMessage(null);
    setPendingStatus(null);
  }

  return (
    <div className="space-y-5 py-4" data-testid="topic-panel">
      <div className="space-y-2">
        <h2 className="font-semibold">Independent topic videos</h2>
        <p className="text-muted-foreground text-sm">
          Find complete discussions with enough context to stand alone. Videos
          can reuse parts of the source. Listen before accepting each candidate.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm" htmlFor={`topic-history-${sourceId}`}>
            Run history
          </label>
          <select
            className="max-w-full rounded border bg-background p-2 text-sm"
            disabled={blocked}
            id={`topic-history-${sourceId}`}
            onChange={(event) => selectRun(event.target.value)}
            value={selectedRunId}
          >
            <option value="">New topic discovery</option>
            {view.runs.map((run) => (
              <option key={run.id} value={run.id}>
                {new Date(run.createdAt).toLocaleString()} ·{" "}
                {run.status.replaceAll("_", " ")}
              </option>
            ))}
            {selectedRunId &&
              !view.runs.some((run) => run.id === selectedRunId) && (
                <option value={selectedRunId}>Pending discovery</option>
              )}
          </select>
          <Button
            disabled={busy || !selectedRunId}
            onClick={() => refresh()}
            size="sm"
            variant="outline"
          >
            Refresh
          </Button>
        </div>
      </div>
      {!selectedRunId && (
        <div className="space-y-3 rounded-lg border p-4">
          <details>
            <summary className="cursor-pointer text-sm">
              Optional instructions
            </summary>
            <Textarea
              aria-label="Topic instructions"
              className="mt-2"
              disabled={blocked}
              onChange={(event) => setBrief(event.target.value)}
              placeholder="The default looks for complete, independent discussions. Add a preference only if you need one."
              value={brief}
            />
          </details>
          {!availability.available && (
            <p className="text-muted-foreground text-sm">
              {availability.message}
            </p>
          )}
          {!!(availability.available && availability.settings.synthetic) && (
            <p className="text-sm">
              Recorded test backend: these are synthetic results.
            </p>
          )}
          <Button
            disabled={blocked || !availability.available}
            onClick={() => start()}
          >
            Find topic videos
          </Button>
        </div>
      )}
      {!!(pendingStart || pendingReview) && (
        <div className="space-y-2 rounded-lg border p-3 text-sm" role="status">
          <p>
            {pendingStatus?.message ??
              "Waiting for the durable result. This request keeps its identity across retries."}
          </p>
          <Button
            disabled={busy}
            onClick={() =>
              pendingStart
                ? start(pendingStart)
                : pendingReview &&
                  review(
                    pendingReview.action as "accept" | "reject" | "cancel",
                    pendingReview.sectionId,
                    pendingReview.reason,
                    pendingReview
                  )
            }
            size="sm"
            variant="outline"
          >
            Retry same request
          </Button>
          {pendingStatus?.state === "terminal" && (
            <Button
              disabled={busy}
              onClick={() => {
                remember(startKey, null);
                remember(reviewKey, null);
                setPendingStart(null);
                setPendingReview(null);
                setPendingStatus(null);
              }}
              size="sm"
              variant="outline"
            >
              Discard resolved request
            </Button>
          )}
        </div>
      )}
      {!!message && (
        <p className="text-sm" role="status">
          {message}
        </p>
      )}
      <TopicRunStatus run={view.run} />
      {!!artifactError && (
        <p className="text-destructive text-sm" role="alert">
          {artifactError}
        </p>
      )}
      {!!current && (
        <div className="space-y-4">
          <p className="text-sm">{current.summary}</p>
          {current.videos.length === 0 && (
            <p className="text-muted-foreground text-sm">
              This run produced no topic candidates.
            </p>
          )}
          {current.videos.map((video) => (
            <TopicVideoCard
              blocked={
                blocked ||
                !view.run ||
                ["running", "cancelled", "failed"].includes(view.run.status)
              }
              key={`${view.run?.currentRevision}:${video.video.candidate.id}`}
              onReview={(action, reason) =>
                review(action, video.video.candidate.id, reason)
              }
              portfolio={current}
              video={video}
            />
          ))}
        </div>
      )}
      <TopicDownloads {...downloadRevision(current, accepted, view.run)} />
      {!!view.run &&
        !["cancelled", "failed", "ready"].includes(view.run.status) && (
          <details className="text-sm">
            <summary className="cursor-pointer">Cancel this run</summary>
            <Textarea
              aria-label="Cancellation reason"
              className="my-2"
              disabled={blocked}
              onChange={(event) => setCancelReason(event.target.value)}
              value={cancelReason}
            />
            <Button
              disabled={blocked || !cancelReason.trim()}
              onClick={() => review("cancel", null, cancelReason.trim())}
              size="sm"
              variant="outline"
            >
              Cancel run
            </Button>
          </details>
        )}
    </div>
  );
}

function TopicVideoCard({
  video,
  portfolio,
  blocked,
  onReview,
}: {
  video: TopicVideoView;
  portfolio: TopicRevisionView;
  blocked: boolean;
  onReview: (action: "accept" | "reject", reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const { candidate } = video.video;
  const state = video.video.edit.sections.find(
    (section) => section.id === video.video.keptSectionId
  )?.reviewState;
  const reused = reusedContextMs(video, portfolio.videos);
  const humanState =
    { accepted: "Accepted by you", rejected: "Rejected by you" }[
      state as "accepted" | "rejected"
    ] ?? "Candidate";
  const criteria = video.assessment
    ? [
        ...Object.values(video.assessment.coldReview ?? {}),
        ...Object.values(video.assessment.sourceReview ?? {}),
      ].filter(
        (
          value
        ): value is {
          status: "pass" | "fail" | "unknown";
          reason: string;
          evidenceSpans: never[];
        } =>
          typeof value === "object" &&
          value !== null &&
          "status" in value &&
          value.status !== "pass"
      )
    : [];
  return (
    <article className="space-y-3 rounded-lg border p-4">
      <h3 className="font-semibold">{candidate.title}</h3>
      <div className="flex flex-wrap gap-2 text-xs">
        <Badge variant="outline">{humanState}</Badge>
        <Badge variant="outline">
          {topicEditorialStatus(portfolio.assessment, video.assessment)}
        </Badge>
        <span>
          {video.technicalPass
            ? "Technical checks passed"
            : "Technical checks incomplete or failed"}
        </span>
      </div>
      <p className="text-sm">{candidate.purpose}</p>
      <p className="text-muted-foreground text-xs">
        Source {formatDuration(video.startMs)}–{formatDuration(video.endMs)} ·{" "}
        {formatDuration(video.durationMs)} long
        {reused > 0
          ? ` · ${formatDuration(reused)} also appears in another topic video`
          : ""}
      </p>
      {video.mediaUrl ? (
        <video
          className="aspect-video w-full rounded bg-black"
          controls
          preload="metadata"
          src={video.mediaUrl}
        >
          <track
            default
            kind="captions"
            label="Source captions"
            src={video.captionsUrl ?? undefined}
            srcLang="und"
          />
        </video>
      ) : (
        <p className="text-muted-foreground text-sm">
          A playable render is not available yet.
        </p>
      )}
      {!!video.mediaUrl && (
        <a className="text-sm underline" download href={video.mediaUrl}>
          Download candidate preview
        </a>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer">
          Editorial evidence and concerns
        </summary>
        <p className="mt-2">{candidate.reason}</p>
        <p className="my-2 text-muted-foreground">
          Text review assesses discussion and source context. It does not
          establish that the audible cuts are clean.
        </p>
        {[
          ...new Set([
            ...(video.assessment?.reasons ?? []),
            ...(video.assessment?.physicalBoundaryIssues.map(
              (issue) => `${issue.edge}: ${issue.reason}`
            ) ?? []),
            ...criteria.map((item) => `${item.status}: ${item.reason}`),
            ...video.warnings,
          ]),
        ].map((item) => (
          <p className="my-1" key={item}>
            {item}
          </p>
        ))}
      </details>
      <Textarea
        aria-label={`Review reason for ${candidate.title}`}
        disabled={blocked}
        onChange={(event) => setReason(event.target.value)}
        placeholder="What did you hear? Record your acceptance or rejection reason."
        value={reason}
      />
      <div className="flex gap-2">
        <Button
          disabled={
            blocked ||
            !reason.trim() ||
            !video.technicalPass ||
            !video.mediaUrl ||
            state === "accepted"
          }
          onClick={() => onReview("accept", reason.trim())}
          size="sm"
        >
          Accept video
        </Button>
        <Button
          disabled={blocked || !reason.trim() || state === "rejected"}
          onClick={() => onReview("reject", reason.trim())}
          size="sm"
          variant="outline"
        >
          Reject video
        </Button>
      </div>
    </article>
  );
}

function TopicRunStatus({ run }: { run: ChapterView["run"] }) {
  return (
    <>
      {!!run && (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{run.status.replaceAll("_", " ")}</Badge>
            <span>
              Revision {run.currentRevision} · {run.stage}
            </span>
          </div>
          <p className="text-muted-foreground">
            {run.dispatchCount} model calls · $
            {(run.spentMicros / 1_000_000).toFixed(4)} spent · $
            {(run.reservedMicros / 1_000_000).toFixed(4)} pending exposure
          </p>
          {!!run.synthetic && (
            <p>Recorded test run; no live editorial judgment.</p>
          )}
          {!!run.errorMessage && <p>{run.errorMessage}</p>}
          {run.currentTranscriptRevision !== run.evidenceTranscriptRevision && (
            <p>
              The transcript changed after this run. These videos use its
              recorded revision.
            </p>
          )}
        </div>
      )}
    </>
  );
}

function TopicDownloads({
  accepted,
  revision,
}: {
  accepted: TopicRevisionView | null;
  revision: number | null | undefined;
}) {
  return (
    <>
      {!!accepted?.exportUrl && (
        <div className="space-y-2 rounded-lg border p-3 text-sm">
          <p>Accepted export · revision {revision}</p>
          <a className="underline" download href={accepted.exportUrl}>
            Download accepted manifest
          </a>
          {accepted.videos
            .filter((video) =>
              accepted.exportedCandidateIds.includes(video.video.candidate.id)
            )
            .map(
              (video) =>
                video.mediaUrl && (
                  <a
                    className="block underline"
                    download
                    href={video.mediaUrl}
                    key={video.video.candidate.id}
                  >
                    Download accepted video: {video.video.candidate.title}
                  </a>
                )
            )}
        </div>
      )}
    </>
  );
}

async function inspectPending(
  view: ChapterView,
  startIntent: TopicStartIntent | null,
  reviewIntent: TopicReviewIntent | null
) {
  const result = {
    message: null as string | null,
    reviewDone: false,
    startDone: false,
    status: null as PendingTopicWorkflowStatus | null,
  };
  if (startIntent) {
    result.startDone = view.run?.id === startIntent.runId;
    if (view.run?.id !== startIntent.runId) {
      result.status = await getPendingTopicWorkflowStatus({
        intent: startIntent,
        kind: "start",
      });
    }
  }
  if (reviewIntent) {
    const event = view.events.find(
      (item) => item.mutationKey === reviewIntent.mutationKey
    );
    result.reviewDone = !!event;
    if (event) {
      result.message =
        event.state === "applied"
          ? "Your review was recorded."
          : `The review was ${event.state}. Refresh and review the current revision.`;
    } else {
      result.status = await getPendingTopicWorkflowStatus({
        intent: reviewIntent,
        kind: "review",
      });
    }
  }
  return result;
}

async function fetchTopicStatus(
  sourceId: string,
  runId: string,
  startIntent: TopicStartIntent | null,
  reviewIntent: TopicReviewIntent | null
) {
  const params = new URLSearchParams({ runId });
  if (reviewIntent) {
    params.set("mutationKey", reviewIntent.mutationKey);
  }
  const response = await fetch(`/api/sources/${sourceId}/topics?${params}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("Could not refresh topic history.");
  }
  const next = (await response.json()) as ChapterView;
  return {
    next,
    settled: await inspectPending(next, startIntent, reviewIntent),
  };
}

function downloadRevision(
  current: TopicRevisionView | null,
  accepted: TopicRevisionView | null,
  run: ChapterView["run"]
) {
  if (current?.exportUrl) {
    return { accepted: current, revision: run?.currentRevision };
  }
  return { accepted, revision: run?.acceptedRevision };
}
