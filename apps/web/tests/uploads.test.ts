import { afterEach, describe, expect, it } from "vitest";
import { MAX_PARTS, missingParts, partCountFor } from "@/lib/uploads/parts";
import { fingerprintOf, partSizeFor } from "@/lib/uploads/server";

const MIB = 1024 * 1024;

describe("part sizing", () => {
  afterEach(() => {
    delete process.env.UPLOAD_PART_SIZE_BYTES;
  });

  it("is 16 MiB for anything up to 1000 parts of it", () => {
    expect(partSizeFor(1)).toBe(16 * MIB);
    expect(partSizeFor(MAX_PARTS * 16 * MIB)).toBe(16 * MIB);
  });

  it("grows for larger files so the part count stays inside one ListParts page", () => {
    for (const size of [
      MAX_PARTS * 16 * MIB + 1,
      40 * 1024 * MIB,
      200 * 1024 * MIB,
    ]) {
      expect(partSizeFor(size)).toBeGreaterThan(16 * MIB);
      expect(partCountFor(size, partSizeFor(size))).toBeLessThanOrEqual(
        MAX_PARTS
      );
    }
  });

  it("honours the e2e override only above the store minimum", () => {
    process.env.UPLOAD_PART_SIZE_BYTES = String(5 * MIB);
    expect(partSizeFor(100)).toBe(5 * MIB);
    process.env.UPLOAD_PART_SIZE_BYTES = String(1 * MIB);
    expect(partSizeFor(100)).toBe(16 * MIB);
  });
});

describe("resume", () => {
  it("skips only parts the store holds at the expected size", () => {
    const partSize = 5 * MIB;
    const size = 12 * MIB;
    expect(
      missingParts(size, partSize, [
        { etag: "a", partNumber: 1, size: partSize },
        { etag: "b", partNumber: 2, size: partSize - 1 },
        { etag: "c", partNumber: 3, size: 2 * MIB },
      ])
    ).toEqual([2]);
  });

  it("wants every part of a fresh upload", () => {
    expect(missingParts(12 * MIB, 5 * MIB, [])).toEqual([1, 2, 3]);
    expect(missingParts(1, 5 * MIB, [])).toEqual([1]);
  });

  it("fingerprints the same file the same way", () => {
    expect(fingerprintOf("p", "a.mp4", 10, 5)).toBe(
      fingerprintOf("p", "a.mp4", 10, 5)
    );
    expect(fingerprintOf("p", "a.mp4", 10, 5)).not.toBe(
      fingerprintOf("p", "a.mp4", 11, 5)
    );
    expect(fingerprintOf("p", "a.mp4", 10, 5)).not.toBe(
      fingerprintOf("q", "a.mp4", 10, 5)
    );
  });
});
