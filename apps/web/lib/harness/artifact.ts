import type { ZodType } from "zod";
import type { ChapterArtifactRef } from "./queries";

const MIB = 1024 * 1024;
const MAX_JSON_BYTES = 16 * MIB;
const MAX_CACHE_BYTES = 32 * MIB;
const MAX_CACHE_ENTRIES = 24;

interface CachedArtifact {
  bytes: number;
  value: Promise<unknown>;
}

const immutableJson = new Map<string, CachedArtifact>();

function evictFor(bytes: number): void {
  let cachedBytes = [...immutableJson.values()].reduce(
    (total, entry) => total + entry.bytes,
    0
  );
  while (
    immutableJson.size >= MAX_CACHE_ENTRIES ||
    cachedBytes + bytes > MAX_CACHE_BYTES
  ) {
    const oldest = immutableJson.entries().next().value;
    if (!oldest) {
      return;
    }
    const [key, entry] = oldest;
    immutableJson.delete(key);
    cachedBytes -= entry.bytes;
  }
}

function loadArtifact(artifact: ChapterArtifactRef): Promise<unknown> {
  if (artifact.sizeBytes < 0 || artifact.sizeBytes > MAX_JSON_BYTES) {
    return Promise.reject(
      new Error("The chapter artifact is too large to read as JSON.")
    );
  }
  const cacheKey = `${artifact.id}:${artifact.sha256.toLowerCase()}`;
  const cached = immutableJson.get(cacheKey);
  if (cached) {
    immutableJson.delete(cacheKey);
    immutableJson.set(cacheKey, cached);
    return cached.value;
  }
  const pending = (async () => {
    const response = await fetch(artifact.url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("The chapter artifact could not be read.");
    }
    const contentLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(contentLength) && contentLength > MAX_JSON_BYTES) {
      throw new Error("The chapter artifact is too large to read as JSON.");
    }
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength !== artifact.sizeBytes) {
      throw new Error(
        "The chapter artifact size does not match its immutable record."
      );
    }
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const sha256 = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0")
    ).join("");
    if (sha256 !== artifact.sha256.toLowerCase()) {
      throw new Error(
        "The chapter artifact hash does not match its immutable record."
      );
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  })();
  evictFor(artifact.sizeBytes);
  immutableJson.set(cacheKey, { bytes: artifact.sizeBytes, value: pending });
  pending.catch(() => {
    if (immutableJson.get(cacheKey)?.value === pending) {
      immutableJson.delete(cacheKey);
    }
  });
  return pending;
}

export async function verifiedArtifactJson<T>(
  artifact: ChapterArtifactRef,
  schema: ZodType<T>
): Promise<T> {
  return schema.parse(await loadArtifact(artifact));
}
