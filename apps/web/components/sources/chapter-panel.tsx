"use client";

import {
  ChapterChecksSchema,
  ChapterEditSpecSchema,
  ChapterRendersSchema,
  type ChapterReviewAction,
} from "@temnia/contracts";
import { selectPlayback, selectTime, usePlayer } from "@videojs/react";
import { Video, VideoPlayer, VideoSkin } from "@videojs/react/video";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import {
  getPendingChapterWorkflowStatus,
  type PendingChapterWorkflowStatus,
  reviewChapterCommand,
  startChapterRun,
} from "@/app/actions/chapters";
import { AcceptedChapterDownloads } from "@/components/sources/accepted-chapter-downloads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { verifiedArtifactJson } from "@/lib/harness/artifact";
import {
  type ChapterPanelMessage,
  chapterWaitingMessage,
  clearMatchedStartMessage,
} from "@/lib/harness/chapter-ui";
import { technicalEligibility } from "@/lib/harness/checks";
import {
  clearSessionIntent,
  readSessionIntent,
  writeSessionIntent,
} from "@/lib/harness/client";
import type { HarnessAvailability } from "@/lib/harness/config";
import { formatMicros, parseDollarMicros } from "@/lib/harness/money";
import type { ChapterView } from "@/lib/harness/queries";

interface ChapterPanelProps {
  availability: HarnessAvailability;
  initialView: ChapterView;
  sourceId: string;
}

interface PendingStart {
  brief: string;
  budgetDollars: string;
  requestKey: string;
  runId: string;
  sourceId: string;
}

type PendingCommand = Record<string, unknown> & {
  mutationKey: string;
  runId: string;
};

interface PendingResolution extends PendingChapterWorkflowStatus {
  kind: "command" | "start";
}

interface RenderCheckRef {
  artifact: ChapterView["artifacts"][number];
  sectionId: string;
}

interface BoundaryAudition {
  boundaryId: string;
  cutSeconds: number;
  endSeconds: number;
  phase: "playing" | "starting";
  startSeconds: number;
}

interface BoundaryAuditionError {
  boundaryId: string;
  message: string;
}

const pendingStartKey = (sourceId: string) =>
  `chapter-pending-start:${sourceId}`;
const pendingCommandKey = (runId: string) => `chapter-pending-command:${runId}`;

const TRAILING_ZEROES = /\.?0+$/;
const POSITIVE_INTEGER = /^\d+$/;

function seconds(milliseconds: number): string {
  return (milliseconds / 1000).toFixed(3).replace(TRAILING_ZEROES, "");
}

const BOUNDARY_GUIDANCE: Record<string, string> = {
  adjacent_word_confidence_low: "A nearby transcript word has low confidence.",
  adjacent_word_timing_interpolated: "A nearby word has estimated timing.",
  compiler_quantized_inside_detected_speech:
    "This cut may fall inside detected speech.",
  compiler_quantized_inside_lexical_span: "This cut falls inside a timed word.",
  compiler_window_widened: "This cut is outside the preferred boundary window.",
  detector_recognition_gap_disagreement:
    "Speech detection and transcription disagree near this cut.",
  inside_spoken_word: "This cut falls inside a timed word.",
  speech_coverage_needs_review:
    "Speech detection and transcription need a listening check.",
  speech_coverage_unknown:
    "An independent speech check is unavailable for this recording.",
};

function boundaryGuidance(reasons: readonly string[]): string {
  const messages = new Set(
    reasons.flatMap((reason) => {
      const known = BOUNDARY_GUIDANCE[reason];
      if (known) {
        return [known];
      }
      return reason.startsWith("manual_nudge:")
        ? ["This cut was moved manually."]
        : [];
    })
  );
  return [...messages, "Listen around the cut before accepting it."].join(" ");
}

async function mapWithConcurrency<Input, Output>(
  inputs: readonly Input[],
  concurrency: number,
  visit: (input: Input) => Promise<Output>
): Promise<Output[]> {
  const outputs = new Array<Output>(inputs.length);
  let nextIndex = 0;
  await Promise.all(
    Array.from({ length: Math.min(concurrency, inputs.length) }, async () => {
      while (nextIndex < inputs.length) {
        const index = nextIndex;
        nextIndex += 1;
        const input = inputs[index];
        if (input !== undefined) {
          // biome-ignore lint/performance/noAwaitInLoops: each of four workers is sequential so artifact fetches stay bounded
          outputs[index] = await visit(input);
        }
      }
    })
  );
  return outputs;
}

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: this single domain panel coordinates durable run, artifact, and review states that must stay visibly consistent
export function ChapterPanel({
  availability,
  initialView,
  sourceId,
}: ChapterPanelProps) {
  const formId = useId();
  const [view, setView] = useState(initialView);
  const [edit, setEdit] = useState<ReturnType<
    typeof ChapterEditSpecSchema.parse
  > | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [message, setMessage] = useState<ChapterPanelMessage | null>(null);
  const [brief, setBrief] = useState("");
  const [budget, setBudget] = useState("1.00");
  const [reason, setReason] = useState("");
  const [nudgeByBoundary, setNudgeByBoundary] = useState<
    Record<string, string>
  >({});
  const [undoRevision, setUndoRevision] = useState("");
  const [pending, startTransition] = useTransition();
  const [checkState, setCheckState] = useState<"loading" | "pass" | "blocked">(
    "loading"
  );
  const [checkWarnings, setCheckWarnings] = useState<string[]>([]);
  const [descriptorCheckState, setDescriptorCheckState] = useState<
    "invalid" | "loaded" | "loading"
  >("loading");
  const [renderCheckRefs, setRenderCheckRefs] = useState<RenderCheckRef[]>([]);
  const [renders, setRenders] = useState<
    ReturnType<typeof ChapterRendersSchema.parse>["renders"]
  >([]);
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);
  const [pendingStart, setPendingStart] = useState<PendingStart | null>(null);
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(
    null
  );
  const [pendingResolution, setPendingResolution] =
    useState<PendingResolution | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    initialView.run?.id ?? null
  );
  const sourcePlayback = usePlayer(selectPlayback);
  const sourceTime = usePlayer(selectTime);
  const [audition, setAudition] = useState<BoundaryAudition | null>(null);
  const [auditionError, setAuditionError] =
    useState<BoundaryAuditionError | null>(null);
  const auditionToken = useRef(0);
  const auditionOwner = useRef<number | null>(null);
  const auditionPendingPlay = useRef<number | null>(null);
  const sourcePause = useRef(sourcePlayback?.pause);
  sourcePause.current = sourcePlayback?.pause;
  const refreshSequence = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const selectedArtifact = view.currentEdit ?? view.acceptedEdit;
  const selectedArtifactIdentity = selectedArtifact
    ? `${selectedArtifact.id}:${selectedArtifact.sha256}`
    : null;
  const selectedArtifactRef = useRef(selectedArtifact);
  selectedArtifactRef.current = selectedArtifact;

  const pauseOwnedAudition = useCallback((token: number) => {
    if (auditionOwner.current === token) {
      sourcePause.current?.();
      auditionOwner.current = null;
    }
    if (auditionPendingPlay.current === token) {
      sourcePause.current?.();
      auditionPendingPlay.current = null;
    }
  }, []);

  const stopAudition = useCallback(() => {
    auditionToken.current += 1;
    if (auditionOwner.current !== null) {
      sourcePause.current?.();
      auditionOwner.current = null;
    }
    if (auditionPendingPlay.current !== null) {
      sourcePause.current?.();
    }
    setAudition(null);
  }, []);

  const previewBoundary = useCallback(
    async (boundaryId: string, cutMs: number) => {
      const duration = sourceTime?.duration ?? 0;
      const seek = sourceTime?.seek;
      const play = sourcePlayback?.play;
      if (!(seek && play && Number.isFinite(duration) && duration > 0)) {
        setAuditionError({
          boundaryId,
          message:
            "The source preview is not ready yet. Wait for playback to load and try again.",
        });
        return;
      }
      stopAudition();
      const token = auditionToken.current;
      const cutSeconds = cutMs / 1000;
      const startSeconds = Math.max(0, cutSeconds - 3);
      const endSeconds = Math.min(duration, cutSeconds + 3);
      if (!(endSeconds > startSeconds)) {
        setAuditionError({
          boundaryId,
          message: "This cut is outside the playable source duration.",
        });
        return;
      }
      setAuditionError(null);
      setAudition({
        boundaryId,
        cutSeconds,
        endSeconds,
        phase: "starting",
        startSeconds,
      });
      try {
        await seek(startSeconds);
        if (token !== auditionToken.current) {
          return;
        }
        auditionPendingPlay.current = token;
        await play();
        if (token !== auditionToken.current) {
          pauseOwnedAudition(token);
          return;
        }
        auditionPendingPlay.current = null;
        auditionOwner.current = token;
        setAudition((current) =>
          current?.boundaryId === boundaryId
            ? { ...current, phase: "playing" }
            : current
        );
      } catch {
        if (token !== auditionToken.current) {
          if (auditionPendingPlay.current === token) {
            auditionPendingPlay.current = null;
          }
          return;
        }
        pauseOwnedAudition(token);
        setAudition(null);
        setAuditionError({
          boundaryId,
          message:
            "Could not preview this cut. Check source playback and try again.",
        });
      }
    },
    [
      pauseOwnedAudition,
      sourcePlayback?.play,
      sourceTime?.duration,
      sourceTime?.seek,
      stopAudition,
    ]
  );

  useEffect(() => {
    if (audition?.phase !== "playing") {
      return;
    }
    if ((sourceTime?.currentTime ?? 0) >= audition.endSeconds) {
      stopAudition();
      return;
    }
    if (sourcePlayback?.paused) {
      auditionToken.current += 1;
      auditionOwner.current = null;
      setAudition(null);
    }
  }, [audition, sourcePlayback?.paused, sourceTime?.currentTime, stopAudition]);

  useEffect(
    () => () => {
      auditionToken.current += 1;
      if (auditionOwner.current !== null) {
        sourcePause.current?.();
        auditionOwner.current = null;
      }
      if (auditionPendingPlay.current !== null) {
        sourcePause.current?.();
      }
    },
    []
  );

  const refresh = useCallback(
    // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: one poll resolves the DB row/event and its exact Temporal fallback without clearing an ambiguous intent
    async (runOverride?: string) => {
      if (creatingNew && !runOverride && !pendingRunId) {
        return;
      }
      const sequence = refreshSequence.current + 1;
      refreshSequence.current = sequence;
      refreshController.current?.abort();
      const controller = new AbortController();
      refreshController.current = controller;
      const run = runOverride ?? pendingRunId ?? selectedRunId ?? undefined;
      const query = new URLSearchParams();
      if (run) {
        query.set("runId", run);
      }
      if (pendingCommand) {
        query.set("mutationKey", pendingCommand.mutationKey);
      }
      let response: Response;
      try {
        response = await fetch(
          `/api/sources/${sourceId}/chapters${query.size > 0 ? `?${query}` : ""}`,
          { cache: "no-store", signal: controller.signal }
        );
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        throw error;
      }
      if (response.ok) {
        const next = (await response.json()) as ChapterView;
        if (controller.signal.aborted || sequence !== refreshSequence.current) {
          return;
        }
        setView(next);
        if (run && next.run?.id === run) {
          setSelectedRunId(run);
          setMessage((current) => clearMatchedStartMessage(current, run));
          setPendingRunId(null);
          setPendingStart(null);
          setPendingResolution((current) =>
            current?.kind === "start" ? null : current
          );
          clearSessionIntent(pendingStartKey(sourceId));
        } else if (
          pendingStart &&
          !(
            pendingResolution?.kind === "start" &&
            pendingResolution.state === "terminal"
          )
        ) {
          const status = await getPendingChapterWorkflowStatus({
            intent: pendingStart,
            kind: "start",
          });
          if (
            controller.signal.aborted ||
            sequence !== refreshSequence.current
          ) {
            return;
          }
          setPendingResolution({ ...status, kind: "start" });
          setMessage({
            kind: "start",
            runId: pendingStart.runId,
            text: status.message,
          });
        }
        if (
          pendingCommand &&
          next.events.some(
            (event) => event.mutationKey === pendingCommand.mutationKey
          )
        ) {
          clearSessionIntent(pendingCommandKey(pendingCommand.runId));
          setPendingCommand(null);
          setPendingResolution((current) =>
            current?.kind === "command" ? null : current
          );
        } else if (
          pendingCommand &&
          !(
            pendingResolution?.kind === "command" &&
            pendingResolution.state === "terminal"
          )
        ) {
          const status = await getPendingChapterWorkflowStatus({
            intent: pendingCommand,
            kind: "review",
          });
          if (
            controller.signal.aborted ||
            sequence !== refreshSequence.current
          ) {
            return;
          }
          setPendingResolution({ ...status, kind: "command" });
          setMessage({ kind: "command", text: status.message });
        }
      }
    },
    [
      pendingCommand,
      pendingResolution,
      pendingRunId,
      pendingStart,
      creatingNew,
      selectedRunId,
      sourceId,
    ]
  );

  useEffect(() => {
    const recovered = readSessionIntent<PendingStart>(
      pendingStartKey(sourceId)
    );
    if (recovered?.sourceId === sourceId) {
      setPendingStart(recovered);
      setPendingRunId(recovered.runId);
      setSelectedRunId(recovered.runId);
      setCreatingNew(false);
      setBrief(recovered.brief);
      setBudget(recovered.budgetDollars);
      setPendingResolution(null);
    }
  }, [sourceId]);

  useEffect(() => {
    if (!view.run) {
      setPendingCommand(null);
      return;
    }
    const key = pendingCommandKey(view.run.id);
    const recovered = readSessionIntent<PendingCommand>(key);
    if (
      recovered &&
      view.events.some((event) => event.mutationKey === recovered.mutationKey)
    ) {
      clearSessionIntent(key);
      setPendingCommand(null);
    } else {
      setPendingCommand(recovered);
    }
  }, [view.events, view.run]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refresh().catch(() =>
        setMessage({
          kind: "refresh",
          text: "Chapter status could not be refreshed.",
        })
      );
    }, 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(
    () => () => {
      refreshSequence.current += 1;
      refreshController.current?.abort();
    },
    []
  );

  useEffect(() => {
    if (!selectedArtifactIdentity) {
      setEdit(null);
      setArtifactError(null);
      return;
    }
    const artifact = selectedArtifactRef.current;
    if (
      !artifact ||
      `${artifact.id}:${artifact.sha256}` !== selectedArtifactIdentity
    ) {
      return;
    }
    let active = true;
    verifiedArtifactJson(artifact, ChapterEditSpecSchema)
      .then((value) => {
        if (active) {
          setEdit(value);
          setArtifactError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setEdit(null);
          setArtifactError(
            error instanceof Error
              ? error.message
              : "The current edit is invalid."
          );
        }
      });
    return () => {
      active = false;
    };
    // Artifact bytes are immutable. A poll can replace the descriptor object,
    // but only a new id/hash should reset or retry its validation error.
  }, [selectedArtifactIdentity]);

  const relatedArtifacts = useMemo(
    () =>
      view.artifacts.filter(
        (artifact) =>
          artifact.metadata.editSha256 ===
          (view.currentEdit ?? view.acceptedEdit)?.sha256
      ),
    [view.acceptedEdit, view.artifacts, view.currentEdit]
  );
  useEffect(() => {
    if (descriptorCheckState === "loading") {
      setCheckState("loading");
      setCheckWarnings([]);
      return;
    }
    if (descriptorCheckState === "invalid") {
      setCheckState("blocked");
      setCheckWarnings([]);
      return;
    }
    if (renderCheckRefs.length === 0) {
      setCheckState("pass");
      setCheckWarnings([]);
      return;
    }
    let active = true;
    mapWithConcurrency(renderCheckRefs, 4, async ({ artifact, sectionId }) => ({
      sectionId,
      value: await verifiedArtifactJson(artifact, ChapterChecksSchema),
    }))
      .then((values) => {
        if (active) {
          const selectedEditSha256 = (view.currentEdit ?? view.acceptedEdit)
            ?.sha256;
          if (
            values.some(
              ({ sectionId, value }) =>
                value.editSha256 !== selectedEditSha256 ||
                value.technicalChecks.some(
                  (check) => check.sectionId !== sectionId
                )
            )
          ) {
            setCheckState("blocked");
            setCheckWarnings([]);
            return;
          }
          const eligibility = values.map(({ value }) =>
            technicalEligibility(value)
          );
          setCheckState(
            eligibility.every((value) => value.eligible) ? "pass" : "blocked"
          );
          setCheckWarnings(eligibility.flatMap((value) => value.warnings));
        }
      })
      .catch(() => {
        if (active) {
          setCheckState("blocked");
          setCheckWarnings([]);
        }
      });
    return () => {
      active = false;
    };
  }, [
    descriptorCheckState,
    renderCheckRefs,
    view.acceptedEdit,
    view.currentEdit,
  ]);

  useEffect(() => {
    const renderArtifacts = relatedArtifacts.filter(
      (artifact) =>
        artifact.kind === "render" &&
        artifact.metadata.format === "chapter-renders/1" &&
        artifact.metadata.runId === view.run?.id &&
        Number.isInteger(artifact.metadata.renderCount) &&
        Number(artifact.metadata.renderCount) >= 0
    );
    if (renderArtifacts.length === 0) {
      setRenders([]);
      setRenderCheckRefs([]);
      setDescriptorCheckState("loading");
      return;
    }
    let active = true;
    Promise.all(
      renderArtifacts.map(async (artifact) => ({
        artifact,
        value: await verifiedArtifactJson(artifact, ChapterRendersSchema),
      }))
    )
      .then((values) => {
        if (active) {
          const matching = values.find(
            ({ artifact, value }) =>
              value.runId === view.run?.id &&
              value.editSha256 ===
                (view.currentEdit ?? view.acceptedEdit)?.sha256 &&
              value.renders.length === Number(artifact.metadata.renderCount)
          );
          if (!matching) {
            setRenders([]);
            setRenderCheckRefs([]);
            setDescriptorCheckState("invalid");
            return;
          }
          const keptSectionIds =
            edit?.sections
              .filter((section) => section.kind === "keep")
              .map((section) => section.id) ?? [];
          const renderedSectionIds = matching.value.renders.map(
            (render) => render.sectionId
          );
          if (
            !edit ||
            keptSectionIds.length !== renderedSectionIds.length ||
            keptSectionIds.some(
              (sectionId, index) => renderedSectionIds[index] !== sectionId
            )
          ) {
            setRenders([]);
            setRenderCheckRefs([]);
            setDescriptorCheckState("invalid");
            return;
          }
          setRenders(matching.value.renders);
          const checks = matching.value.renders.map((render) => {
            const check = render.checks;
            return {
              artifact: check
                ? relatedArtifacts.find(
                    (artifact) =>
                      artifact.kind === "checks" &&
                      artifact.id === check.id &&
                      artifact.sha256 === check.sha256
                  )
                : undefined,
              sectionId: render.sectionId,
            };
          });
          if (
            checks.some(({ artifact }) => !artifact) ||
            new Set(checks.map(({ artifact }) => artifact?.id)).size !==
              checks.length
          ) {
            setRenderCheckRefs([]);
            setDescriptorCheckState("invalid");
            return;
          }
          setRenderCheckRefs(
            checks.map(({ artifact, sectionId }) => ({
              artifact: artifact as ChapterView["artifacts"][number],
              sectionId,
            }))
          );
          setDescriptorCheckState("loaded");
        }
      })
      .catch(() => {
        if (active) {
          setRenders([]);
          setRenderCheckRefs([]);
          setDescriptorCheckState("invalid");
        }
      });
    return () => {
      active = false;
    };
  }, [
    edit,
    relatedArtifacts,
    view.acceptedEdit,
    view.currentEdit,
    view.run?.id,
  ]);

  const submitStart = (intent: PendingStart) => {
    startTransition(async () => {
      try {
        const result = await startChapterRun(intent);
        const runId = result.runId || intent.runId;
        setMessage({
          kind: "start",
          runId,
          text: result.ok ? "Chapter editing queued." : result.message,
        });
        if (result.ok || result.pending) {
          setPendingStart(intent);
          setPendingRunId(runId);
          setSelectedRunId(runId);
          await refresh(runId);
        } else {
          clearSessionIntent(pendingStartKey(sourceId));
          setPendingStart(null);
          setPendingRunId(null);
          setCreatingNew(true);
        }
      } catch {
        setMessage({
          kind: "start",
          runId: intent.runId,
          text: "The browser lost the start response. The exact request is retained; retry it or wait for its status.",
        });
        setPendingStart(intent);
        setPendingRunId(intent.runId);
        setSelectedRunId(intent.runId);
      }
    });
  };

  const begin = () => {
    const runId = crypto.randomUUID();
    const intent = {
      brief,
      budgetDollars: budget,
      requestKey: runId,
      runId,
      sourceId,
    };
    writeSessionIntent(pendingStartKey(sourceId), intent);
    setPendingStart(intent);
    setPendingRunId(runId);
    setSelectedRunId(runId);
    setCreatingNew(false);
    submitStart(intent);
  };

  const submitCommand = (intent: PendingCommand) => {
    startTransition(async () => {
      try {
        const result = await reviewChapterCommand(intent);
        setMessage({
          kind: "command",
          text: result.ok
            ? "Command confirmed by its durable result."
            : result.message,
        });
        if (!(result.pending || result.ok)) {
          clearSessionIntent(pendingCommandKey(intent.runId));
          setPendingCommand(null);
        }
        await refresh();
      } catch {
        setMessage({
          kind: "command",
          text: "The browser lost the review response. The exact command is retained; retry it or wait for its status.",
        });
        setPendingCommand(intent);
      }
    });
  };

  const command = (
    action: ChapterReviewAction,
    fields: Record<string, unknown> = {}
  ) => {
    if (!view.run) {
      return;
    }
    if (pendingCommand) {
      return;
    }
    const intent = {
      action,
      baseRevision: view.run?.currentRevision ?? 0,
      boundaryId: null,
      budgetMicros: null,
      mutationKey: crypto.randomUUID(),
      otherSectionId: null,
      reason,
      runId: view.run?.id,
      sectionId: null,
      sourceId,
      targetRevision: null,
      targetTimeMs: null,
      ...fields,
    };
    writeSessionIntent(pendingCommandKey(view.run.id), intent);
    setPendingCommand(intent);
    submitCommand(intent);
  };

  if (!availability.available) {
    return (
      <p className="text-muted-foreground text-sm">{availability.message}</p>
    );
  }
  if (pendingRunId && view.run?.id !== pendingRunId) {
    return (
      <div className="space-y-2" data-testid="chapters-starting">
        <p role="status">
          {pendingResolution?.kind === "start"
            ? pendingResolution.message
            : "Chapter editing is starting. Waiting for the durable run…"}
        </p>
        <Button
          disabled={
            pending ||
            !pendingStart ||
            (pendingResolution?.kind === "start" &&
              pendingResolution.state === "terminal")
          }
          onClick={() => pendingStart && submitStart(pendingStart)}
          variant="outline"
        >
          Retry the same request
        </Button>
        {pendingResolution?.kind === "start" &&
        pendingResolution.state === "terminal" ? (
          <Button
            onClick={() => {
              clearSessionIntent(pendingStartKey(sourceId));
              setPendingStart(null);
              setPendingRunId(null);
              setPendingResolution(null);
              setCreatingNew(true);
              setMessage(null);
            }}
            variant="outline"
          >
            Discard failed request
          </Button>
        ) : null}
      </div>
    );
  }
  if (creatingNew || !view.run) {
    return (
      <div
        className="space-y-4"
        data-testid={creatingNew ? "chapters-new-run" : "chapters-empty"}
      >
        {availability.settings.synthetic ? (
          <Badge>Recorded test backend</Badge>
        ) : null}
        <Label htmlFor={`${formId}-brief`}>Editorial brief</Label>
        <Textarea
          aria-label="Editorial brief"
          id={`${formId}-brief`}
          onChange={(event) => setBrief(event.target.value)}
          placeholder="Describe the chapter structure you want"
          value={brief}
        />
        <Label htmlFor={`${formId}-budget`}>Maximum budget (USD)</Label>
        <Input
          aria-label="Maximum budget in dollars"
          id={`${formId}-budget`}
          inputMode="decimal"
          onChange={(event) => setBudget(event.target.value)}
          value={budget}
        />
        <Button data-testid="chapter-start" disabled={pending} onClick={begin}>
          {pending ? "Starting…" : "Create chapters"}
        </Button>
        {creatingNew && view.run ? (
          <Button
            onClick={() => {
              setCreatingNew(false);
              setSelectedRunId(view.run?.id ?? null);
            }}
            variant="outline"
          >
            Cancel new run
          </Button>
        ) : null}
        {message ? <p role="status">{message.text}</p> : null}
      </div>
    );
  }

  const { run } = view;
  const commandBusy = pending || Boolean(pendingCommand);
  const contentBusy =
    commandBusy ||
    ["cancelled", "failed", "outcome_unknown"].includes(run.status);
  const waitingMessage = chapterWaitingMessage({
    checkState,
    descriptorCheckState,
    hasSelectedEdit: selectedArtifact !== null,
  });
  return (
    <div className="space-y-4" data-testid="chapters-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{run.status.replaceAll("_", " ")}</Badge>
        {run.synthetic ? <Badge>Recorded test backend</Badge> : null}
        <span className="text-muted-foreground text-sm">
          {run.stage?.replaceAll("_", " ") ?? "Queued"}
        </span>
      </div>
      {run.evidenceTranscriptRevision !== null &&
      run.currentTranscriptRevision !== null &&
      run.evidenceTranscriptRevision !== run.currentTranscriptRevision ? (
        <p
          className="text-amber-700 text-sm"
          data-testid="chapter-evidence-stale"
        >
          Based on transcript revision {run.evidenceTranscriptRevision}
          {"; "}revision {run.currentTranscriptRevision} is now available. Start
          a new run to use corrections.
        </p>
      ) : null}
      <NativeSelect
        aria-label="Chapter run"
        onChange={(event) => {
          const runId = event.target.value;
          setCreatingNew(false);
          setSelectedRunId(runId);
          refresh(runId).catch(() =>
            setMessage({
              kind: "refresh",
              text: "That chapter run could not be loaded.",
            })
          );
        }}
        value={selectedRunId ?? run.id}
      >
        {view.runs.map((item) => (
          <NativeSelectOption key={item.id} value={item.id}>
            {new Date(item.createdAt).toLocaleString()} ·{" "}
            {item.status.replaceAll("_", " ")}
          </NativeSelectOption>
        ))}
      </NativeSelect>
      <p className="text-sm">
        Reported charges {formatMicros(run.spentMicros)} · unresolved exposure{" "}
        {formatMicros(run.reservedMicros)}
        {" · "}budget {formatMicros(run.budgetMicros)} · {run.dispatchCount}{" "}
        dispatches
      </p>
      {run.errorMessage ? (
        <p className="text-destructive text-sm">{run.errorMessage}</p>
      ) : null}
      {run.status === "cancelled" ? (
        <p className="text-muted-foreground text-sm">
          This run is cancelled. Its retained accepted export stays available;
          start a new run to edit.
        </p>
      ) : null}
      {run.status === "failed" ? (
        <p className="text-muted-foreground text-sm">
          This run failed before editing could continue. Retry the run before
          editing.
        </p>
      ) : null}
      {waitingMessage ? (
        <p className="text-muted-foreground text-sm">{waitingMessage}</p>
      ) : null}
      {checkState === "blocked" ? (
        <p className="text-destructive text-sm">
          Technical checks failed or could not be verified. Acceptance and
          export are blocked.
        </p>
      ) : null}
      {checkWarnings.map((warning) => (
        <p className="text-amber-700 text-sm" key={warning}>
          Technical warning: {warning}
        </p>
      ))}
      {pendingCommand ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-muted-foreground text-sm">
            {pendingResolution?.kind === "command"
              ? pendingResolution.message
              : "Waiting for this review command’s durable result."}
          </p>
          <Button
            disabled={
              pending ||
              (pendingResolution?.kind === "command" &&
                pendingResolution.state === "terminal")
            }
            onClick={() => submitCommand(pendingCommand)}
            size="sm"
            variant="outline"
          >
            Retry the same command
          </Button>
          {pendingResolution?.kind === "command" &&
          pendingResolution.state === "terminal" ? (
            <Button
              onClick={() => {
                clearSessionIntent(pendingCommandKey(pendingCommand.runId));
                setPendingCommand(null);
                setPendingResolution(null);
                setMessage(null);
              }}
              size="sm"
              variant="outline"
            >
              Discard failed command
            </Button>
          ) : null}
        </div>
      ) : null}
      {message ? <p role="status">{message.text}</p> : null}
      {artifactError ? (
        <p className="text-destructive text-sm" role="alert">
          {artifactError}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button
          disabled={
            commandBusy || !["budget_paused", "failed"].includes(run.status)
          }
          onClick={() => command("retry")}
        >
          Retry
        </Button>
        <Button
          disabled={
            commandBusy ||
            !["budget_paused", "needs_review", "pending", "running"].includes(
              run.status
            )
          }
          onClick={() => command("cancel")}
          variant="outline"
        >
          Cancel
        </Button>
        <Button
          disabled={Boolean(pendingCommand)}
          onClick={() => {
            clearSessionIntent(pendingStartKey(sourceId));
            setPendingStart(null);
            setPendingRunId(null);
            setPendingResolution(null);
            setCreatingNew(true);
            setSelectedRunId(null);
          }}
          variant="outline"
        >
          New run
        </Button>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${formId}-reason`}>Review reason</Label>
        <Textarea
          aria-describedby={`${formId}-reason-help`}
          aria-label="Review reason"
          id={`${formId}-reason`}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Explain what you checked or want to change"
          value={reason}
        />
        <p
          className="text-muted-foreground text-sm"
          id={`${formId}-reason-help`}
        >
          Add a reason to accept an uncertain cut, acknowledge a drop, or reject
          a chapter. Every section needs approval before export.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={`${formId}-undo`}>Undo to revision</Label>
          <Input
            aria-label="Undo revision"
            id={`${formId}-undo`}
            inputMode="numeric"
            onChange={(event) => setUndoRevision(event.target.value)}
            placeholder={`Current revision: ${run.currentRevision}`}
            value={undoRevision}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${formId}-budget`}>New maximum budget (USD)</Label>
          <Input
            aria-label="New maximum budget in dollars"
            id={`${formId}-budget`}
            inputMode="decimal"
            onChange={(event) => setBudget(event.target.value)}
            value={budget}
          />
        </div>
      </div>
      <Button
        disabled={contentBusy || !POSITIVE_INTEGER.test(undoRevision)}
        onClick={() =>
          command("undo", { targetRevision: Number(undoRevision) })
        }
        variant="outline"
      >
        Undo to revision
      </Button>
      <Button
        disabled={
          commandBusy ||
          ["cancelled", "outcome_unknown", "ready"].includes(run.status) ||
          parseDollarMicros(
            budget,
            availability.settings.maxRunBudgetMicros
          ) === null
        }
        onClick={() =>
          command("raise_budget", {
            budgetMicros: parseDollarMicros(
              budget,
              availability.settings.maxRunBudgetMicros
            ),
          })
        }
        variant="outline"
      >
        Raise budget
      </Button>
      {edit ? (
        <div className="space-y-3">
          {
            // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: this domain row exposes each action only in its valid section/boundary state
            edit.sections.map((section, index) => {
              const start = edit.boundaries[index];
              const end = edit.boundaries[index + 1];
              if (!(start && end)) {
                return null;
              }
              const next = edit.sections[index + 1];
              const nudge = nudgeByBoundary[end.id] ?? "";
              return (
                <Card data-testid={`chapter-${section.id}`} key={section.id}>
                  <CardHeader>
                    <CardTitle>
                      {section.kind === "drop" ? "Drop: " : ""}
                      {section.title || "Untitled chapter"}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    <p className="tabular-nums">
                      {seconds(start.timeMs)}s–{seconds(end.timeMs)}s
                    </p>
                    <p>{section.reason}</p>
                    <p className="text-muted-foreground">
                      {section.reviewState.replaceAll("_", " ")}{" "}
                      {section.flags
                        .map((flag) => flag.replaceAll("_", " "))
                        .join(" · ")}
                    </p>
                    {start.requiresReview || end.requiresReview ? (
                      <p className="text-amber-700">
                        {boundaryGuidance([
                          ...(start.requiresReview ? start.reasons : []),
                          ...(end.requiresReview ? end.reasons : []),
                        ])}
                      </p>
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                      {section.kind === "keep" ? (
                        <Button
                          disabled={
                            contentBusy ||
                            checkState !== "pass" ||
                            ((start.requiresReview || end.requiresReview) &&
                              !reason.trim())
                          }
                          onClick={() =>
                            command("accept", { sectionId: section.id })
                          }
                        >
                          Accept
                        </Button>
                      ) : (
                        <Button
                          disabled={
                            contentBusy ||
                            checkState !== "pass" ||
                            !reason.trim()
                          }
                          onClick={() =>
                            command("accept", { sectionId: section.id })
                          }
                        >
                          Acknowledge drop
                        </Button>
                      )}
                      <Button
                        disabled={contentBusy || !reason.trim()}
                        onClick={() =>
                          command("reject", { sectionId: section.id })
                        }
                        variant="outline"
                      >
                        Reject
                      </Button>
                      {section.kind === "drop" ? (
                        <Button
                          disabled={contentBusy}
                          onClick={() =>
                            command("restore", { sectionId: section.id })
                          }
                          variant="outline"
                        >
                          Restore
                        </Button>
                      ) : null}
                      {next?.kind === "keep" && section.kind === "keep" ? (
                        <Button
                          disabled={contentBusy}
                          onClick={() =>
                            command("merge", {
                              otherSectionId: next.id,
                              sectionId: section.id,
                            })
                          }
                          variant="outline"
                        >
                          Merge next
                        </Button>
                      ) : null}
                    </div>
                    {index < edit.sections.length - 1 ? (
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          {audition?.boundaryId === end.id ? (
                            <Button
                              aria-label={`Stop preview after ${section.title}`}
                              onClick={stopAudition}
                              variant="outline"
                            >
                              Stop preview
                            </Button>
                          ) : (
                            <Button
                              aria-label={`Preview cut after ${section.title}`}
                              onClick={() =>
                                previewBoundary(end.id, end.timeMs)
                              }
                              variant="outline"
                            >
                              Preview cut
                            </Button>
                          )}
                          {audition?.boundaryId === end.id ? (
                            <p
                              className="text-muted-foreground text-sm tabular-nums"
                              data-testid={`chapter-cut-preview-${end.id}`}
                              role="status"
                            >
                              {audition.phase === "starting"
                                ? "Preparing"
                                : "Playing"}{" "}
                              {seconds(audition.startSeconds * 1000)}s–
                              {seconds(audition.endSeconds * 1000)}s around cut
                              at {seconds(audition.cutSeconds * 1000)}s.
                            </p>
                          ) : null}
                        </div>
                        {auditionError?.boundaryId === end.id ? (
                          <p className="text-destructive text-sm" role="alert">
                            {auditionError.message}
                          </p>
                        ) : null}
                        <Label htmlFor={`${formId}-nudge-${end.id}`}>
                          End time in seconds
                        </Label>
                        <div className="flex gap-2">
                          <Input
                            aria-label={`Nudge ${section.title}`}
                            id={`${formId}-nudge-${end.id}`}
                            inputMode="decimal"
                            onChange={(event) =>
                              setNudgeByBoundary((current) => ({
                                ...current,
                                [end.id]: event.target.value,
                              }))
                            }
                            placeholder={seconds(end.timeMs)}
                            value={nudge}
                          />
                          <Button
                            disabled={
                              contentBusy ||
                              !nudge ||
                              !Number.isFinite(Number(nudge))
                            }
                            onClick={() => {
                              command("nudge", {
                                boundaryId: end.id,
                                targetTimeMs: Math.round(Number(nudge) * 1000),
                              });
                              setNudgeByBoundary((current) => ({
                                ...current,
                                [end.id]: "",
                              }));
                            }}
                            variant="outline"
                          >
                            Nudge end
                          </Button>
                        </div>
                      </div>
                    ) : null}
                  </CardContent>
                </Card>
              );
            })
          }
        </div>
      ) : (
        <p className="text-muted-foreground text-sm">
          {run.currentRevision
            ? "Verifying the current edit…"
            : "The durable run has not published an edit yet."}
        </p>
      )}
      {relatedArtifacts
        .filter((artifact) => artifact.kind !== "export")
        .map((artifact) => (
          <a
            className="block text-sm underline"
            href={artifact.url}
            key={artifact.id}
          >
            {`Open ${artifact.kind}`}
          </a>
        ))}
      {renders.map((render) => (
        <div className="space-y-2" key={render.sectionId}>
          <p className="font-medium text-sm">
            {edit?.sections.find((section) => section.id === render.sectionId)
              ?.title || "Chapter preview"}
          </p>
          <VideoPlayer>
            <VideoSkin className="aspect-video w-full overflow-hidden rounded-lg">
              <Video
                crossOrigin="use-credentials"
                playsInline
                preload="metadata"
                src={`/api/media/${render.media.storageKey}`}
              >
                {render.captions ? (
                  <track
                    default
                    kind="captions"
                    src={`/api/media/${render.captions.storageKey}`}
                  />
                ) : null}
              </Video>
            </VideoSkin>
          </VideoPlayer>
        </div>
      ))}
      <AcceptedChapterDownloads sourceId={sourceId} view={view} />
      {view.events.slice(0, 5).map((event) => (
        <p className="text-muted-foreground text-xs" key={event.mutationKey}>
          {event.action} · {event.state}
          {event.state === "conflict" ? " · reload the current revision" : ""}
        </p>
      ))}
    </div>
  );
}
