/**
 * The details tab's words, and the one of them that used to break hydration.
 *
 * The "Ready" row was `new Date(readyAt).toLocaleString()` in the render body.
 * The server runs in UTC and the reader's browser does not, so the server HTML
 * and the first client render disagreed on that one text node: React #418 on a
 * production build, twice per reload of a ready source, invisible under
 * `next dev`. `formatUtcTimestamp` is what both sides render now, so it has to
 * be the same string wherever it runs; the local form is an effect's job.
 */
import { afterEach, describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDuration,
  formatElapsed,
  formatUtcTimestamp,
} from "@/lib/sources/labels";

const READY_AT = "2026-09-07T02:48:36.556Z";
const zone = process.env.TZ;

/** Node re-reads `process.env.TZ` for every subsequent date operation. */
function inZone<T>(tz: string, run: () => T): T {
  process.env.TZ = tz;
  return run();
}

afterEach(() => {
  if (zone === undefined) {
    delete process.env.TZ;
  } else {
    process.env.TZ = zone;
  }
});

describe("formatUtcTimestamp", () => {
  it("is the instant in UTC, to the minute", () => {
    expect(formatUtcTimestamp(READY_AT)).toBe("2026-09-07 02:48 UTC");
  });

  it("is the same string in the server's zone and the reader's", () => {
    const server = inZone("UTC", () => formatUtcTimestamp(READY_AT));
    const reader = inZone("Asia/Calcutta", () => formatUtcTimestamp(READY_AT));
    const other = inZone("America/Los_Angeles", () =>
      formatUtcTimestamp(READY_AT)
    );
    expect(reader).toBe(server);
    expect(other).toBe(server);
  });

  it("is asserting something: the local form is not the same string", () => {
    const server = inZone("UTC", () => new Date(READY_AT).toLocaleString());
    const reader = inZone("Asia/Calcutta", () =>
      new Date(READY_AT).toLocaleString()
    );
    expect(reader).not.toBe(server);
  });

  it("pads every field, so the column stays one width", () => {
    expect(formatUtcTimestamp("2026-01-02T03:04:05.000Z")).toBe(
      "2026-01-02 03:04 UTC"
    );
  });

  it("has a dash for a timestamp it cannot read", () => {
    expect(formatUtcTimestamp("not a date")).toBe("—");
  });
});

describe("the rest of the details rows", () => {
  it("counts elapsed seconds between two stored timestamps", () => {
    expect(
      formatElapsed("2026-09-07T02:48:33.000Z", "2026-09-07T02:48:36.000Z")
    ).toBe("3s");
    expect(formatElapsed("2026-09-07T02:48:33.000Z", null)).toBe("—");
  });

  it("formats a duration and a size", () => {
    expect(formatDuration(40_120)).toBe("0:40");
    expect(formatDuration(null)).toBe("—");
    expect(formatBytes(794_624)).toBe("776 KB");
  });
});
