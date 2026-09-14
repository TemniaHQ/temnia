import { describe, expect, it } from "vitest";
import { deepestMessage, earlyFailure } from "@/lib/harness/early-failure";

describe("early failure of a retried execution", () => {
  it("walks a Temporal failure chain to the activity's own sentence", () => {
    const chain = Object.assign(new Error("outer"), {
      cause: Object.assign(new Error("middle"), { cause: new Error("inner") }),
    });
    expect(deepestMessage(chain)).toBe("inner");
    expect(
      deepestMessage(
        Object.assign(new Error("only"), { cause: { message: "  " } })
      )
    ).toBe("only");
    expect(deepestMessage("not an error")).toBe(
      "The retry failed before it started."
    );
  });

  it("returns the message of a fast failure and null for a run that outlives the window", async () => {
    await expect(
      earlyFailure({ result: () => Promise.reject(new Error("refused")) }, 50)
    ).resolves.toBe("refused");
    await expect(
      earlyFailure({ result: () => new Promise<never>(() => undefined) }, 10)
    ).resolves.toBeNull();
    await expect(
      earlyFailure({ result: () => Promise.resolve("done") }, 10)
    ).resolves.toBeNull();
    await expect(earlyFailure({}, 10)).resolves.toBeNull();
  });
});
