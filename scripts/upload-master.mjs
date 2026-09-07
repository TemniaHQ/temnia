#!/usr/bin/env node
// Push a local master through the real upload API, the way the browser does:
// create (or adopt), sign each part, PUT each slice, complete. Then poll the
// source until it is ready or failed. No dependencies.
//
//   node scripts/upload-master.mjs <file> --project <projectId> [--base http://localhost:3000]
//        [--header "CF-Access-Client-Id: ..." --header "CF-Access-Client-Secret: ..."] [--concurrency 4]
//
// Prints the timings that go into the sprint log: upload wall time and
// ingest wall time, from the same clock the user experiences.
import { open, stat } from "node:fs/promises";
import { basename } from "node:path";

const HEADER_SEPARATOR = /:\s*/;
const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--"));
const option = (flag, fallback) => {
  const index = args.indexOf(`--${flag}`);
  return index === -1 ? fallback : args[index + 1];
};
if (!file) {
  throw new Error("usage: upload-master.mjs <file> --project <projectId>");
}
const projectId = option("project");
if (!projectId) {
  throw new Error("--project <projectId> is required");
}
const base = option("base", "http://localhost:3000").replace(/\/$/, "");
const concurrency = Number(option("concurrency", "4"));
const extraHeaders = args
  .flatMap((a, i) => (a === "--header" ? [args[i + 1]] : []))
  .map((h) => h.split(HEADER_SEPARATOR, 2))
  .filter((pair) => pair.length === 2);
const headers = Object.fromEntries(extraHeaders);

async function api(path, body, method = "POST") {
  const response = await fetch(`${base}${path}`, {
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { "content-type": "application/json", ...headers },
    method,
  });
  if (response.status === 409 && path === "/api/uploads") {
    const { retryAfterSeconds } = await response.json();
    const wait = (retryAfterSeconds ?? 60) + 5;
    console.log(
      `an upload of this file is still in its grace window; waiting ${wait} s to adopt it`
    );
    await new Promise((r) => setTimeout(r, wait * 1000));
    return api(path, body, method);
  }
  if (!response.ok) {
    throw new Error(
      `${method} ${path} -> ${response.status}: ${await response.text()}`
    );
  }
  return response.json();
}

const { size, mtimeMs } = await stat(file);
const name = basename(file);
const lastModified = Math.floor(mtimeMs);
const startedAt = Date.now();
const session = await api("/api/uploads", {
  lastModified,
  name,
  projectId,
  size,
  type: name.endsWith(".mov") ? "video/quicktime" : "video/mp4",
});
const have = new Set(session.uploaded.map((p) => p.partNumber));
const pending = Array.from(
  { length: session.partCount },
  (_, i) => i + 1
).filter((n) => !have.has(n));
console.log(
  `${session.resumed ? "adopted" : "created"} upload ${session.uploadId}: ${session.partCount} parts of ${session.partSize} bytes, ${have.size} already stored`
);

const handle = await open(file, "r");
async function sign(partNumber) {
  const { url } = await api(`/api/uploads/${session.uploadId}/sign`, {
    key: session.key,
    method: "PUT",
    partNumber,
    uploadId: session.multipartUploadId,
  });
  return url;
}
let done = have.size;
async function putPart(partNumber) {
  const start = (partNumber - 1) * session.partSize;
  const length = Math.min(session.partSize, size - start);
  const buffer = Buffer.alloc(length);
  await handle.read(buffer, 0, length, start);
  for (let attempt = 0; ; attempt += 1) {
    // biome-ignore lint/performance/noAwaitInLoops: retries of one part are sequential
    const url = await sign(partNumber);
    const response = await fetch(url, { body: buffer, method: "PUT" });
    if (response.ok) {
      break;
    }
    if (attempt >= 4) {
      throw new Error(`part ${partNumber} failed with ${response.status}`);
    }
  }
  done += 1;
  process.stdout.write(`\rparts ${done}/${session.partCount}`);
}
async function worker() {
  for (;;) {
    const next = pending.shift();
    if (next === undefined) {
      return;
    }
    // biome-ignore lint/performance/noAwaitInLoops: each worker uploads its parts in order
    await putPart(next);
  }
}
await Promise.all(Array.from({ length: concurrency }, worker));
await handle.close();
const completed = await api(`/api/uploads/${session.uploadId}/complete`, {});
const uploadSeconds = (Date.now() - startedAt) / 1000;
console.log(
  `\nuploaded in ${uploadSeconds.toFixed(1)}s; source ${completed.sourceId}, workflow ${completed.workflowId}`
);

const ingestStartedAt = Date.now();
for (;;) {
  // biome-ignore lint/performance/noAwaitInLoops: polling is sequential by nature
  await new Promise((r) => setTimeout(r, 5000));
  const response = await fetch(`${base}/api/sources/${completed.sourceId}`, {
    headers,
  });
  if (!response.ok) {
    process.stdout.write(`\rstatus request failed: ${response.status}`);
    continue;
  }
  const source = await response.json();
  const elapsed = ((Date.now() - ingestStartedAt) / 1000).toFixed(0);
  process.stdout.write(
    `\r${elapsed}s ${source.status} ${source.ingestStage ?? ""} ${source.ingestPercent ?? ""}%   `
  );
  if (source.status === "ready" || source.status === "failed") {
    console.log(
      `\ningest ${source.status} in ${elapsed}s; duration ${source.durationMs} ms; ${source.errorMessage ?? ""}`
    );
    break;
  }
}
