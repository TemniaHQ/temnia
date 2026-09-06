import { describe, expect, it } from "vitest";
import {
  keyBelongsTo,
  masterKey,
  SEEDED_SCOPE,
  sanitizeFilename,
  sourcePrefix,
} from "../src/index.ts";

const ORG = SEEDED_SCOPE.organizationId;
const OTHER = "0192e8a0-0000-7000-8000-000000000003";

describe("storage keys", () => {
  it("prefix every key with the organization", () => {
    expect(sourcePrefix(ORG, "s1")).toBe(`org/${ORG}/source/s1/`);
    expect(masterKey(ORG, "s1", "My Episode (final).MOV")).toBe(
      `org/${ORG}/source/s1/master/My_Episode_final_.MOV`
    );
  });

  it("sanitise filenames to one safe segment", () => {
    expect(sanitizeFilename("../../etc/passwd")).toBe("passwd");
    expect(sanitizeFilename("...")).toBe("file");
    expect(sanitizeFilename(`${"a".repeat(200)}.mp4`).length).toBe(120);
  });

  it("authorise only keys inside the caller's prefix", () => {
    expect(keyBelongsTo(ORG, `org/${ORG}/source/s1/hls/master.m3u8`)).toBe(
      true
    );
    expect(keyBelongsTo(ORG, `org/${OTHER}/source/s1/hls/master.m3u8`)).toBe(
      false
    );
    expect(keyBelongsTo(ORG, `org/${ORG}/../${OTHER}/x`)).toBe(false);
    expect(keyBelongsTo(ORG, `/org/${ORG}/x`)).toBe(false);
    expect(keyBelongsTo(ORG, `org/${ORG}//x`)).toBe(false);
  });
});
