/**
 * Part arithmetic shared by the route handlers and the browser. No SDK here:
 * the browser needs the same part size the server recorded, so both compute
 * it from one function.
 */

const MIB = 1024 * 1024;
/**
 * Uppy 6 reads one page of ListParts (1000 parts) when it resumes; parts
 * beyond that would be sent again. Part size grows with the file to stay
 * inside it: 16 MiB carries files to 16 GB, a 40 GB master gets 40 MiB parts.
 */
export const MAX_PARTS = 1000;
export const MIN_PART_BYTES = 16 * MIB;
/** R2 refuses non-final parts under 5 MiB. */
export const STORE_MIN_PART_BYTES = 5 * MIB;

export interface UploadedPart {
  etag: string;
  partNumber: number;
  size: number;
}

/**
 * Part size is a deterministic function of file size and never changes
 * between deploys, or a resumed upload would mis-align its parts. The
 * override exists for the resume e2e, which needs several parts from a small
 * file; it is only honoured at or above the store's minimum.
 */
export function partSizeForBytes(sizeBytes: number, override?: number): number {
  if (
    override !== undefined &&
    Number.isFinite(override) &&
    override >= STORE_MIN_PART_BYTES
  ) {
    return override;
  }
  const needed = Math.ceil(sizeBytes / MAX_PARTS / MIB) * MIB;
  return Math.max(MIN_PART_BYTES, needed);
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
