/**
 * Multipart upload control, server side. The browser never holds credentials
 * and never talks multipart to the store: it PUTs file slices to part URLs
 * signed here. Create, ListParts, Complete, and Abort run here, which is also
 * why the same code works on Garage and R2 (R2 has no presigned POST).
 */
import { createHash } from "node:crypto";
import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  ListPartsCommand,
  UploadPartCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { browserStorage, storage, storageSettings } from "@/lib/storage/client";

const MIB = 1024 * 1024;
const MAX_PARTS = 9000;
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

export interface UploadedPart {
  etag: string;
  partNumber: number;
  size: number;
}

/**
 * Part size is a deterministic function of file size and never changes
 * between deploys, or a resumed upload would mis-align its parts. 16 MiB
 * carries files to 140 GB within 9000 parts; larger files scale the part.
 * R2 requires every non-final part to be the same size and at least 5 MiB.
 * UPLOAD_PART_SIZE_BYTES exists for the resume e2e, which needs several
 * parts from a small file.
 */
export function partSizeFor(sizeBytes: number): number {
  const override = Number(process.env.UPLOAD_PART_SIZE_BYTES);
  if (Number.isFinite(override) && override >= 5 * MIB) {
    return override;
  }
  const minimum = 16 * MIB;
  const needed = Math.ceil(sizeBytes / MAX_PARTS / MIB) * MIB;
  return Math.max(minimum, needed);
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

export function signPartUrls(
  key: string,
  uploadId: string,
  partNumbers: number[]
): Promise<string[]> {
  return Promise.all(
    partNumbers.map((partNumber) =>
      getSignedUrl(
        browserStorage(),
        new UploadPartCommand({
          Bucket: storageSettings().bucket,
          Key: key,
          PartNumber: partNumber,
          UploadId: uploadId,
        }),
        { expiresIn: PART_URL_TTL_SECONDS }
      )
    )
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

export function partCountFor(sizeBytes: number, partSize: number): number {
  return Math.max(1, Math.ceil(sizeBytes / partSize));
}

/** Which parts still need uploading, given what the store already holds. */
export function missingParts(
  sizeBytes: number,
  partSize: number,
  uploaded: UploadedPart[]
): number[] {
  const count = partCountFor(sizeBytes, partSize);
  const have = new Set<number>();
  for (const part of uploaded) {
    const expected =
      part.partNumber === count ? sizeBytes - (count - 1) * partSize : partSize;
    if (part.size === expected) {
      have.add(part.partNumber);
    }
  }
  const missing: number[] = [];
  for (let n = 1; n <= count; n += 1) {
    if (!have.has(n)) {
      missing.push(n);
    }
  }
  return missing;
}
