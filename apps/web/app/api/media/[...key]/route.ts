/**
 * The org-scoped media proxy. Media reaches the browser only through here:
 * no public or presigned read URLs (PRD §5). The key must sit inside the
 * caller's organization prefix; anything else is 404, never 403, so the
 * existence of other tenants' keys is not confirmed. HLS playlists reference
 * segments relatively, so every follow-up request re-enters this check.
 */
import { GetObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";
import { keyBelongsTo } from "@temnia/contracts";
import type { NextRequest } from "next/server";
import { resolveScope } from "@/lib/scope/resolve-scope";
import { storage, storageSettings } from "@/lib/storage/client";

const RANGE = /^bytes=\d*-\d*$/;
const CONTENT_TYPES: Record<string, string> = {
  dat: "application/octet-stream",
  jpg: "image/jpeg",
  json: "application/json",
  m3u8: "application/vnd.apple.mpegurl",
  m4a: "audio/mp4",
  m4s: "video/iso.segment",
  mp4: "video/mp4",
  ts: "video/mp2t",
};

function contentType(key: string, fromStore: string | undefined): string {
  if (
    fromStore &&
    fromStore !== "application/octet-stream" &&
    fromStore !== "binary/octet-stream"
  ) {
    return fromStore;
  }
  const ext = key.split(".").pop()?.toLowerCase() ?? "";
  return CONTENT_TYPES[ext] ?? "application/octet-stream";
}

function authorizedKey(segments: string[]): string | null {
  const key = segments.map((s) => decodeURIComponent(s)).join("/");
  return keyBelongsTo(resolveScope().organizationId, key) ? key : null;
}

function isNotFound(error: unknown): boolean {
  const e = error as { name?: string; $metadata?: { httpStatusCode?: number } };
  return (
    e.name === "NoSuchKey" ||
    e.name === "NotFound" ||
    e.$metadata?.httpStatusCode === 404
  );
}

function baseHeaders(
  key: string,
  out: {
    ContentType?: string | undefined;
    ETag?: string | undefined;
    LastModified?: Date | undefined;
  }
) {
  const headers = new Headers({
    "Accept-Ranges": "bytes",
    // Artifacts are immutable per ingest; private keeps them out of shared
    // caches and no-transform keeps the Node server's gzip off 206 bodies.
    "Cache-Control": "private, no-transform, max-age=3600",
    "Content-Type": contentType(key, out.ContentType),
  });
  if (out.ETag) {
    headers.set("ETag", out.ETag);
  }
  if (out.LastModified) {
    headers.set("Last-Modified", out.LastModified.toUTCString());
  }
  return headers;
}

export async function GET(
  request: NextRequest,
  context: RouteContext<"/api/media/[...key]">
) {
  const { key: segments } = await context.params;
  const key = authorizedKey(segments);
  if (!key) {
    return new Response(null, { status: 404 });
  }
  const range = request.headers.get("range") ?? undefined;
  if (range && !RANGE.test(range)) {
    return new Response(null, { status: 416 });
  }
  const { bucket } = storageSettings();
  try {
    const out = await storage().send(
      new GetObjectCommand({
        Bucket: bucket,
        IfNoneMatch: request.headers.get("if-none-match") ?? undefined,
        Key: key,
        Range: range,
      }),
      { abortSignal: request.signal }
    );
    const headers = baseHeaders(key, out);
    if (out.ContentLength !== undefined) {
      headers.set("Content-Length", String(out.ContentLength));
    }
    if (out.ContentRange) {
      headers.set("Content-Range", out.ContentRange);
    }
    const status =
      out.ContentRange || out.$metadata.httpStatusCode === 206 ? 206 : 200;
    if (!out.Body) {
      return new Response(null, { headers, status });
    }
    return new Response(out.Body.transformToWebStream(), { headers, status });
  } catch (error) {
    const e = error as {
      name?: string;
      $metadata?: { httpStatusCode?: number };
    };
    if (e.$metadata?.httpStatusCode === 304) {
      return new Response(null, { status: 304 });
    }
    if (e.name === "InvalidRange") {
      return new Response(null, { status: 416 });
    }
    if (isNotFound(error)) {
      return new Response(null, { status: 404 });
    }
    throw error;
  }
}

export async function HEAD(
  _request: NextRequest,
  context: RouteContext<"/api/media/[...key]">
) {
  const { key: segments } = await context.params;
  const key = authorizedKey(segments);
  if (!key) {
    return new Response(null, { status: 404 });
  }
  try {
    const out = await storage().send(
      new HeadObjectCommand({ Bucket: storageSettings().bucket, Key: key })
    );
    const headers = baseHeaders(key, out);
    headers.set("Content-Length", String(out.ContentLength ?? 0));
    return new Response(null, { headers, status: 200 });
  } catch (error) {
    if (isNotFound(error)) {
      return new Response(null, { status: 404 });
    }
    throw error;
  }
}
