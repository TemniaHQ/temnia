"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { formatBytes } from "@/lib/sources/labels";
import {
  startUpload,
  type UploadHandle,
  type UploadProgress,
} from "@/lib/uploads/client";

type State =
  | { phase: "idle" }
  | { phase: "starting"; name: string }
  | { phase: "waiting"; name: string; seconds: number }
  | {
      phase: "uploading";
      name: string;
      progress: UploadProgress;
      paused: boolean;
    }
  | { phase: "done"; name: string; sourceId: string }
  | { phase: "error"; name: string; message: string };

/**
 * Picks a master and streams it to storage in parts. Closing or reloading the
 * page loses nothing: pick the same file again and the server hands back the
 * parts it already has. If that earlier upload signed a part within the last
 * minute the server asks for a short wait first, shown as a countdown.
 */
export function SourceUploader({ projectId }: { projectId: string }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const handleRef = useRef<UploadHandle | null>(null);
  const chooseFile = () => inputRef.current?.click();
  const [state, setState] = useState<State>({ phase: "idle" });

  const begin = async (file: File) => {
    setState({ name: file.name, phase: "starting" });
    try {
      const handle = await startUpload(
        file,
        projectId,
        (progress) => {
          setState((current) =>
            current.phase === "uploading"
              ? { ...current, progress }
              : { name: file.name, paused: false, phase: "uploading", progress }
          );
        },
        (seconds) => setState({ name: file.name, phase: "waiting", seconds })
      );
      handleRef.current = handle;
      router.refresh();
      const result = await handle.done;
      setState({ name: file.name, phase: "done", sourceId: result.sourceId });
      router.refresh();
    } catch (error) {
      setState({
        message: error instanceof Error ? error.message : "The upload failed.",
        name: file.name,
        phase: "error",
      });
    } finally {
      handleRef.current = null;
    }
  };

  const onChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      begin(file).catch(() => undefined);
    }
    event.target.value = "";
  };

  const togglePause = () => {
    const handle = handleRef.current;
    // biome-ignore lint/suspicious/noUnnecessaryConditions: the ref is null between uploads
    if (!handle || state.phase !== "uploading") {
      return;
    }
    if (state.paused) {
      handle.resume();
    } else {
      handle.pause();
    }
    setState({ ...state, paused: !state.paused });
  };

  const abort = async () => {
    const handle = handleRef.current;
    // biome-ignore lint/suspicious/noUnnecessaryConditions: the ref is null between uploads
    if (!handle) {
      return;
    }
    await handle.abort();
    setState({ phase: "idle" });
    router.refresh();
  };

  return (
    <Card data-testid="source-uploader">
      <CardHeader>
        <CardTitle>Upload a master</CardTitle>
        <CardDescription>
          Video or audio, any size. Parts go straight to storage. If the page is
          closed or reloaded, pick the same file again and it resumes from the
          parts already stored.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <input
          accept="video/*,audio/*"
          className="hidden"
          data-testid="master-file-input"
          onChange={onChange}
          ref={inputRef}
          type="file"
        />
        {state.phase === "idle" ||
        state.phase === "done" ||
        state.phase === "error" ? (
          <div className="flex items-center gap-3">
            <Button onClick={chooseFile} type="button">
              Choose a file
            </Button>
            {state.phase === "done" ? (
              <span
                className="text-muted-foreground text-sm"
                data-testid="upload-done"
              >
                {state.name} is uploaded and queued.
              </span>
            ) : null}
            {state.phase === "error" ? (
              <span
                className="text-destructive text-sm"
                data-testid="upload-error"
                role="alert"
              >
                {state.name}: {state.message}
              </span>
            ) : null}
          </div>
        ) : null}
        {state.phase === "starting" ? (
          <p className="text-muted-foreground text-sm">
            Preparing {state.name}…
          </p>
        ) : null}
        {state.phase === "waiting" ? (
          <p
            className="text-muted-foreground text-sm"
            data-testid="upload-waiting"
          >
            An earlier upload of {state.name} was active a moment ago. Resuming
            from its parts in {state.seconds}s…
          </p>
        ) : null}
        {state.phase === "uploading" ? (
          <div className="flex flex-col gap-2" data-testid="upload-progress">
            <div className="flex items-center justify-between text-sm">
              <span className="truncate font-medium">{state.name}</span>
              <span className="text-muted-foreground tabular-nums">
                {formatBytes(state.progress.bytesDone)} /{" "}
                {formatBytes(state.progress.bytesTotal)}
                {" · "}
                {state.progress.partsDone}/{state.progress.partCount} parts
                {state.progress.resumed ? " · resumed" : ""}
              </span>
            </div>
            <Progress
              value={
                (100 * state.progress.bytesDone) / state.progress.bytesTotal
              }
            />
            <div className="flex gap-2">
              <Button
                onClick={togglePause}
                size="sm"
                type="button"
                variant="outline"
              >
                {state.paused ? "Resume" : "Pause"}
              </Button>
              <Button onClick={abort} size="sm" type="button" variant="ghost">
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
