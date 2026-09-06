"use client";

import "@uppy/core/css/style.min.css";
import "@uppy/dashboard/css/style.min.css";

import AwsS3, { type AwsBody } from "@uppy/aws-s3";
import Uppy, { type Meta, type UppyFile } from "@uppy/core";
import type { Body } from "@uppy/core/utils";
import { useUppyState } from "@uppy/react";

// @uppy/aws-s3 declares this field in a file its entry point never
// references, so the augmentation is repeated here. It is the plugin's own
// resume state: an S3Uploader that finds it lists parts instead of creating.
declare module "@uppy/core/utils" {
  interface LocalUppyFile<M extends Meta, B extends Body> {
    s3Multipart?: { key: string; uploadId: string };
  }
}

import Dashboard from "@uppy/react/dashboard";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { partSizeForBytes } from "@/lib/uploads/parts";
import type { UploadSession } from "@/lib/uploads/session";

/** Mirrors the route's limit; the Dashboard refuses larger files before any request. */
const MAX_FILE_BYTES = 200 * 1024 * 1024 * 1024;
const DASHBOARD_HEIGHT = 320;

interface SourceMeta extends Meta {
  sourceId?: string;
  /** The upload row: set once the server has opened or adopted the upload. A retry keeps it. */
  uploadRowId?: string;
}

type SourceUppy = Uppy<SourceMeta, AwsBody>;
type SourceFile = UppyFile<SourceMeta, AwsBody>;

interface Session {
  rowId: string;
  sourceId: string;
}

interface Registry {
  /** The server's part size by file size; Uppy's chunking must match the row's. */
  partSizes: Map<number, number>;
  /** Rows by object key: Uppy's signRequest carries the key, not the file. */
  sessions: Map<string, Session>;
  /** Leaving the page must not abort the upload at the store; only Cancel does. */
  unmounting: { current: boolean };
}

const JSON_HEADERS = { "content-type": "application/json" };

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readError(response: Response, fallback: string) {
  const body = (await response.json().catch(() => null)) as {
    error?: string;
    retryAfterSeconds?: number;
  } | null;
  return {
    message: body?.error ?? fallback,
    retryAfterSeconds: body?.retryAfterSeconds,
  };
}

/**
 * Before Uppy sends a byte: open (or adopt) the upload on the server, then
 * hand Uppy the store's multipart identity as resume state, so it lists parts
 * and continues rather than creating an upload of its own. A file re-selected
 * inside another browser's grace window waits it out with a visible countdown.
 */
async function prepare(
  uppy: SourceUppy,
  fileId: string,
  projectId: string,
  registry: Registry
): Promise<void> {
  const file = uppy.getFile(fileId);
  if (!file || file.meta.uploadRowId) {
    return;
  }
  const data = file.data as Partial<File>;
  const body = JSON.stringify({
    lastModified: data.lastModified ?? Date.now(),
    name: file.name,
    projectId,
    size: file.size,
    type: file.type,
  });
  for (;;) {
    // biome-ignore lint/performance/noAwaitInLoops: one attempt at a time, the countdown between them is the point
    const response = await fetch("/api/uploads", {
      body,
      headers: JSON_HEADERS,
      method: "POST",
    });
    if (response.ok) {
      const session = (await response.json()) as UploadSession;
      registry.sessions.set(session.key, {
        rowId: session.uploadId,
        sourceId: session.sourceId,
      });
      registry.partSizes.set(file.size ?? 0, session.partSize);
      uppy.setFileMeta(fileId, {
        sourceId: session.sourceId,
        uploadRowId: session.uploadId,
      });
      uppy.setFileState(fileId, {
        s3Multipart: { key: session.key, uploadId: session.multipartUploadId },
      });
      uppy.emit("preprocess-complete", uppy.getFile(fileId));
      return;
    }
    const { message, retryAfterSeconds } = await readError(
      response,
      `the upload could not start (${response.status})`
    );
    if (response.status !== 409) {
      uppy.info(message, "error", 8000);
      uppy.setFileState(fileId, { error: message });
      throw new Error(message);
    }
    for (let left = retryAfterSeconds ?? 1; left > 0; left -= 1) {
      uppy.emit("preprocess-progress", uppy.getFile(fileId), {
        message: `An earlier upload of ${file.name} was active a moment ago. Resuming from its parts in ${left}s…`,
        mode: "indeterminate",
      });
      // biome-ignore lint/performance/noAwaitInLoops: a countdown is sequential
      await sleep(1000);
    }
  }
}

function buildUppy(projectId: string, registry: Registry): SourceUppy {
  const uppy = new Uppy<SourceMeta, AwsBody>({
    restrictions: {
      allowedFileTypes: ["video/*", "audio/*"],
      maxFileSize: MAX_FILE_BYTES,
      maxNumberOfFiles: 10,
    },
  });
  uppy.use(AwsS3, {
    getChunkSize: ({ size }) =>
      registry.partSizes.get(size) ?? partSizeForBytes(size),
    limit: 2,
    shouldUseMultipart: true,
    signRequest: async (request) => {
      const session = registry.sessions.get(request.key);
      if (!session) {
        throw new Error("this file has no upload session");
      }
      if (request.method === "POST") {
        // CompleteMultipartUpload: the app completes and starts the ingest.
        return {
          url: `${window.location.origin}/api/uploads/${session.rowId}/complete`,
        };
      }
      if (request.method === "DELETE") {
        // Never signed: a removal the user asked for is aborted through the
        // app's own route by the file-removed handler, which then refreshes;
        // an unmount must leave every part in the store for the re-pick.
        throw new Error("aborts run through the app, not the store");
      }
      const response = await fetch(`/api/uploads/${session.rowId}/sign`, {
        body: JSON.stringify(request),
        headers: JSON_HEADERS,
        method: "POST",
      });
      if (!response.ok) {
        const { message } = await readError(
          response,
          `signing failed (${response.status})`
        );
        throw new Error(message);
      }
      return (await response.json()) as { url: string };
    },
  });
  uppy.addPreProcessor((fileIds) =>
    Promise.all(fileIds.map((id) => prepare(uppy, id, projectId, registry)))
  );
  return uppy;
}

function waitingMessage(files: Record<string, SourceFile>): string | null {
  for (const file of Object.values(files)) {
    const { preprocess } = file.progress;
    if (preprocess && "message" in preprocess && preprocess.message) {
      return preprocess.message;
    }
  }
  return null;
}

/**
 * Picks masters and streams them to storage in parts through Uppy's
 * Dashboard. Closing or reloading the page loses nothing: pick the same file
 * again and the server hands back the parts it already has.
 */
export function SourceUploader({ projectId }: { projectId: string }) {
  const router = useRouter();
  const registry = useRef<Registry>({
    partSizes: new Map(),
    sessions: new Map(),
    unmounting: { current: false },
  });
  const [uppy, setUppy] = useState<SourceUppy | null>(null);
  const [done, setDone] = useState<{ name: string; sourceId: string }[]>([]);

  // The instance lives with the mounted component: built in the effect so a
  // strict-mode remount gets a fresh one, destroyed on unmount with the flag
  // set so the store keeps every part.
  useEffect(() => {
    const { current: store } = registry;
    store.unmounting.current = false;
    const instance = buildUppy(projectId, store);
    const refresh = () => router.refresh();
    const onSuccess = (file: SourceFile | undefined) => {
      if (file) {
        setDone((current) => [
          ...current,
          { name: file.name, sourceId: file.meta.sourceId ?? "" },
        ]);
      }
      router.refresh();
    };
    // Cancel (one file or all) aborts at the store and removes the row that
    // never became content, then the list refreshes without it. Leaving the
    // page also removes files, but with the flag set nothing is aborted.
    const onRemoved = (file: SourceFile | undefined) => {
      if (!file || store.unmounting.current) {
        return;
      }
      const rowId = file.meta.uploadRowId;
      if (!rowId || file.progress.uploadComplete) {
        router.refresh();
        return;
      }
      fetch(`/api/uploads/${rowId}`, { method: "DELETE" })
        .catch(() => undefined)
        .finally(() => router.refresh());
    };
    instance.on("upload-success", onSuccess);
    instance.on("complete", refresh);
    instance.on("file-removed", onRemoved);
    instance.on("preprocess-complete", refresh);
    setUppy(instance);
    return () => {
      store.unmounting.current = true;
      instance.destroy();
      setUppy(null);
    };
  }, [projectId, router]);

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
      <CardContent className="flex flex-col gap-3">
        {uppy ? <UploaderBody done={done} uppy={uppy} /> : null}
      </CardContent>
    </Card>
  );
}

function UploaderBody({
  done,
  uppy,
}: {
  done: { name: string; sourceId: string }[];
  uppy: SourceUppy;
}) {
  const waiting = useUppyState(uppy, (state) => waitingMessage(state.files));
  return (
    <>
      <Dashboard
        height={DASHBOARD_HEIGHT}
        note="Uploads can be paused and resumed. If the tab closes, pick the same file again within a day to carry on where it left off."
        proudlyDisplayPoweredByUppy={false}
        singleFileFullScreen={false}
        theme="auto"
        uppy={uppy}
        width="100%"
      />
      {waiting ? (
        <p
          className="text-muted-foreground text-sm"
          data-testid="upload-waiting"
        >
          {waiting}
        </p>
      ) : null}
      {done.map((item) => (
        <p
          className="text-muted-foreground text-sm"
          data-testid="upload-done"
          key={item.sourceId}
        >
          {item.name} is uploaded and queued.
        </p>
      ))}
    </>
  );
}
