/**
 * The browser half of multipart upload. Slices of the File are PUT to part
 * URLs the server signs; every control call goes to our own routes. Resume is
 * server-side by fingerprint: re-selecting the same file, in any browser,
 * receives the parts the store already holds and uploads only the rest.
 */
export interface UploadSession {
  partCount: number;
  partSize: number;
  resumed: boolean;
  sourceId: string;
  uploaded: { partNumber: number; size: number; etag: string }[];
  uploadId: string;
}

export interface UploadProgress {
  bytesDone: number;
  bytesTotal: number;
  partCount: number;
  partsDone: number;
  resumed: boolean;
}

export interface UploadHandle {
  abort: () => Promise<void>;
  done: Promise<{ sourceId: string; workflowId: string }>;
  pause: () => void;
  resume: () => void;
  sourceId: string;
  uploadId: string;
}

const CONCURRENCY = 4;
const SIGN_BATCH = 16;
const RETRIES = 5;

class Paused extends Error {}

function backoff(attempt: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 300 * 2 ** attempt));
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: string;
    };
    throw new Error(
      body.error ??
        `${init?.method ?? "GET"} ${url} failed with ${response.status}`
    );
  }
  return response.json() as Promise<T>;
}

/** PUT one slice; resolves with the ETag the store returned. */
function putPart(
  url: string,
  blob: Blob,
  signal: AbortSignal,
  onProgress: (loaded: number) => void
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (event) => onProgress(event.loaded);
    xhr.onload = () => {
      const etag = xhr.getResponseHeader("ETag");
      if (xhr.status >= 200 && xhr.status < 300 && etag) {
        resolve(etag);
      } else {
        reject(
          new Error(
            `part upload failed with ${xhr.status}${etag ? "" : " (no ETag; check bucket CORS ExposeHeaders)"}`
          )
        );
      }
    };
    xhr.onerror = () => reject(new Error("network error during part upload"));
    xhr.onabort = () => reject(new Paused());
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(blob);
  });
}

/**
 * Opens (or adopts) the upload session. A 409 means an earlier upload of
 * this file signed a part less than the grace window ago, typically the
 * same user's paused tab; the server says how long is left, the caller is
 * told so it can show a countdown, and the request is repeated after that.
 */
async function openSession(
  file: File,
  projectId: string,
  onWait: (seconds: number) => void
): Promise<UploadSession> {
  const body = JSON.stringify({
    lastModified: file.lastModified,
    name: file.name,
    projectId,
    size: file.size,
    type: file.type || "video/mp4",
  });
  for (;;) {
    // biome-ignore lint/performance/noAwaitInLoops: each retry waits for the server's own countdown
    const response = await fetch("/api/uploads", {
      body,
      headers: { "content-type": "application/json" },
      method: "POST",
    });
    if (response.ok) {
      return (await response.json()) as UploadSession;
    }
    const payload = (await response.json().catch(() => ({}))) as {
      error?: string;
      retryAfterSeconds?: number;
    };
    if (response.status !== 409) {
      throw new Error(
        payload.error ?? `POST /api/uploads failed with ${response.status}`
      );
    }
    const seconds = Math.max(1, payload.retryAfterSeconds ?? 5);
    onWait(seconds);
    await new Promise((wake) => setTimeout(wake, seconds * 1000));
  }
}

export async function startUpload(
  file: File,
  projectId: string,
  onProgress: (progress: UploadProgress) => void,
  onWait: (seconds: number) => void = () => undefined
): Promise<UploadHandle> {
  const session = await openSession(file, projectId, onWait);
  return new Transfer(file, session, onProgress).handle();
}

class Transfer {
  private readonly pending: number[];
  private readonly inFlight = new Map<number, number>();
  private readonly signed = new Map<number, string>();
  private doneBytes: number;
  private doneParts: number;
  private controller = new AbortController();
  /** Mutable flags in one object: pause is flipped from other methods. */
  private readonly flags = { paused: false as boolean };
  private resumeWaiters: (() => void)[] = [];

  private readonly file: File;
  private readonly session: UploadSession;
  private readonly onProgress: (progress: UploadProgress) => void;

  constructor(
    file: File,
    session: UploadSession,
    onProgress: (progress: UploadProgress) => void
  ) {
    this.file = file;
    this.session = session;
    this.onProgress = onProgress;
    const have = new Set(session.uploaded.map((p) => p.partNumber));
    this.pending = Array.from(
      { length: session.partCount },
      (_, i) => i + 1
    ).filter((n) => !have.has(n));
    this.doneBytes = session.uploaded.reduce((sum, p) => sum + p.size, 0);
    this.doneParts = have.size;
    this.report();
  }

  handle(): UploadHandle {
    const done = (async () => {
      await Promise.all(
        Array.from({ length: CONCURRENCY }, () => this.worker())
      );
      return json<{ sourceId: string; workflowId: string }>(
        `/api/uploads/${this.session.uploadId}/complete`,
        { body: "{}", method: "POST" }
      );
    })();
    return {
      abort: () => this.abort(),
      done,
      pause: () => this.pause(),
      resume: () => this.resume(),
      sourceId: this.session.sourceId,
      uploadId: this.session.uploadId,
    };
  }

  private report(): void {
    let flight = 0;
    for (const loaded of this.inFlight.values()) {
      flight += loaded;
    }
    this.onProgress({
      bytesDone: Math.min(this.file.size, this.doneBytes + flight),
      bytesTotal: this.file.size,
      partCount: this.session.partCount,
      partsDone: this.doneParts,
      resumed: this.session.resumed,
    });
  }

  private pause(): void {
    if (this.flags.paused) {
      return;
    }
    this.flags.paused = true;
    this.controller.abort();
    this.controller = new AbortController();
  }

  private resume(): void {
    if (!this.flags.paused) {
      return;
    }
    this.flags.paused = false;
    const waiters = this.resumeWaiters;
    this.resumeWaiters = [];
    for (const wake of waiters) {
      wake();
    }
  }

  private async abort(): Promise<void> {
    this.flags.paused = false;
    this.controller.abort();
    await fetch(`/api/uploads/${this.session.uploadId}`, { method: "DELETE" });
  }

  private waitIfPaused(): Promise<void> {
    return new Promise<void>((resolve) => {
      if (!this.flags.paused) {
        resolve();
        return;
      }
      this.resumeWaiters.push(resolve);
    });
  }

  private async ensureSigned(partNumber: number): Promise<string> {
    if (!this.signed.has(partNumber)) {
      const batch = this.pending
        .filter((n) => !this.signed.has(n))
        .slice(0, SIGN_BATCH);
      const partNumbers = batch.includes(partNumber)
        ? batch
        : [partNumber, ...batch];
      const { urls } = await json<{
        urls: { partNumber: number; url: string }[];
      }>(`/api/uploads/${this.session.uploadId}/sign`, {
        body: JSON.stringify({ partNumbers }),
        method: "POST",
      });
      for (const entry of urls) {
        this.signed.set(entry.partNumber, entry.url);
      }
    }
    const url = this.signed.get(partNumber);
    if (!url) {
      throw new Error(`no signed url for part ${partNumber}`);
    }
    return url;
  }

  /** One attempt at one part; a Paused rejection means try again after resume. */
  private async attempt(partNumber: number, blob: Blob): Promise<void> {
    await this.waitIfPaused();
    const url = await this.ensureSigned(partNumber);
    this.inFlight.set(partNumber, 0);
    try {
      await putPart(url, blob, this.controller.signal, (loaded) => {
        this.inFlight.set(partNumber, loaded);
        this.report();
      });
    } finally {
      this.inFlight.delete(partNumber);
    }
    this.doneBytes += blob.size;
    this.doneParts += 1;
    this.report();
  }

  private async uploadOne(partNumber: number): Promise<void> {
    const start = (partNumber - 1) * this.session.partSize;
    const blob = this.file.slice(
      start,
      Math.min(this.file.size, start + this.session.partSize)
    );
    let failures = 0;
    for (;;) {
      try {
        // biome-ignore lint/performance/noAwaitInLoops: retries of one part are sequential by definition
        await this.attempt(partNumber, blob);
        return;
      } catch (error) {
        this.signed.delete(partNumber);
        if (error instanceof Paused) {
          continue;
        }
        failures += 1;
        if (failures > RETRIES) {
          throw error;
        }
        await backoff(failures);
      }
    }
  }

  private async worker(): Promise<void> {
    for (;;) {
      const next = this.pending.shift();
      if (next === undefined) {
        return;
      }
      // biome-ignore lint/performance/noAwaitInLoops: each worker uploads its parts one after another
      await this.uploadOne(next);
    }
  }
}
