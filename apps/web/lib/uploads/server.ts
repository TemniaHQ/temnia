/**
 * Multipart upload control, server side. The browser (Uppy 6) PUTs file
 * slices to part URLs signed here and lists parts through a URL signed here;
 * Create runs here before the browser starts, and Complete and Abort are the
 * app's own routes, handed to Uppy as the "presigned" URL for those calls.
 * The store's part list is the one truth about what has been uploaded.
 */
import { createHash } from "node:crypto";
import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  HeadObjectCommand,
  ListPartsCommand,
  UploadPartCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { browserStorage, storage, storageSettings } from "@/lib/storage/client";
import { partSizeForBytes, type UploadedPart } from "./parts";

const PART_URL_TTL_SECONDS = 3600;
/** Resume window: an upload idle this long is aborted by the reaper. */
export const UPLOAD_IDLE_TTL_HOURS = 24;

/**
 * A live upload signs a part at least this often; older than this, another
 * browser may adopt it. UPLOAD_ADOPT_GRACE_SECONDS exists for the resume e2e.
 */
export function adoptGraceSeconds(): number {
  const override = Number(process.env.UPLOAD_ADOPT_GRACE_SECONDS);
  return Number.isFinite(override) && override > 0 ? override : 60;
}

/** The recorded part size for a new upload; UPLOAD_PART_SIZE_BYTES is the e2e override. */
export function partSizeFor(sizeBytes: number): number {
  return partSizeForBytes(
    sizeBytes,
    Number(process.env.UPLOAD_PART_SIZE_BYTES)
  );
}

/**
 * The same file, re-selected anywhere for the same project, has the same
 * fingerprint. The project is part of it: the same master uploaded into two
 * projects is two sources, not one adopted upload.
 */
export function fingerprintOf(
  projectId: string,
  name: string,
  size: number,
  lastModified: number
): string {
  return createHash("sha256")
    .update(`${projectId} ${name} ${size} ${lastModified}`)
    .digest("hex");
}

export async function createMultipart(
  key: string,
  contentType: string
): Promise<string> {
  const out = await storage().send(
    new CreateMultipartUploadCommand({
      Bucket: storageSettings().bucket,
      ContentType: contentType,
      Key: key,
    })
  );
  if (!out.UploadId) {
    throw new Error("the store returned no UploadId");
  }
  return out.UploadId;
}

/** Every part the store holds for this upload, or null when the upload no longer exists. */
export async function listUploadedParts(
  key: string,
  uploadId: string
): Promise<UploadedPart[] | null> {
  const parts: UploadedPart[] = [];
  let marker: string | undefined;
  try {
    do {
      // biome-ignore lint/performance/noAwaitInLoops: pages are sequential by protocol
      const out = await storage().send(
        new ListPartsCommand({
          Bucket: storageSettings().bucket,
          Key: key,
          MaxParts: 1000,
          PartNumberMarker: marker,
          UploadId: uploadId,
        })
      );
      for (const part of out.Parts ?? []) {
        if (part.PartNumber && part.ETag && part.Size !== undefined) {
          parts.push({
            etag: part.ETag,
            partNumber: part.PartNumber,
            size: part.Size,
          });
        }
      }
      marker = out.IsTruncated ? out.NextPartNumberMarker : undefined;
    } while (marker);
  } catch (error) {
    if ((error as { name?: string }).name === "NoSuchUpload") {
      return null;
    }
    throw error;
  }
  return parts;
}

/** The stored object's size, or null when no object exists at the key. */
export async function headObjectSize(key: string): Promise<number | null> {
  try {
    const out = await storage().send(
      new HeadObjectCommand({ Bucket: storageSettings().bucket, Key: key })
    );
    return out.ContentLength ?? null;
  } catch (error) {
    const { name } = error as { name?: string };
    if (name === "NotFound" || name === "NoSuchKey") {
      return null;
    }
    throw error;
  }
}

export function signPartUrl(
  key: string,
  uploadId: string,
  partNumber: number
): Promise<string> {
  return getSignedUrl(
    browserStorage(),
    new UploadPartCommand({
      Bucket: storageSettings().bucket,
      Key: key,
      PartNumber: partNumber,
      UploadId: uploadId,
    }),
    { expiresIn: PART_URL_TTL_SECONDS }
  );
}

/** Uppy lists parts itself when it resumes; the URL is signed here, never touched as liveness. */
export function signListPartsUrl(
  key: string,
  uploadId: string
): Promise<string> {
  return getSignedUrl(
    browserStorage(),
    new ListPartsCommand({
      Bucket: storageSettings().bucket,
      Key: key,
      MaxParts: 1000,
      UploadId: uploadId,
    }),
    { expiresIn: PART_URL_TTL_SECONDS }
  );
}

export async function completeMultipart(
  key: string,
  uploadId: string,
  parts: { partNumber: number; etag: string }[]
): Promise<void> {
  await storage().send(
    new CompleteMultipartUploadCommand({
      Bucket: storageSettings().bucket,
      Key: key,
      MultipartUpload: {
        Parts: [...parts]
          .sort((a, b) => a.partNumber - b.partNumber)
          .map((p) => ({ ETag: p.etag, PartNumber: p.partNumber })),
      },
      UploadId: uploadId,
    })
  );
}

export async function abortMultipart(
  key: string,
  uploadId: string
): Promise<void> {
  try {
    await storage().send(
      new AbortMultipartUploadCommand({
        Bucket: storageSettings().bucket,
        Key: key,
        UploadId: uploadId,
      })
    );
  } catch (error) {
    if ((error as { name?: string }).name !== "NoSuchUpload") {
      throw error;
    }
  }
}
