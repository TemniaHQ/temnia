import assert from "node:assert/strict";
import test from "node:test";
import { sweepStaleGateRuns } from "./local-ci-cleanup.mjs";

test("a stale tag never removes a shared image ID or another run's tag", () => {
  const removed = [];
  const images = [
    { id: "shared-id", tag: "temnia-web:gate-abc_101" },
    { id: "shared-id", tag: "temnia-web:gate-old_102" },
    { id: "other-id", tag: "temnia-pipeline:gate-old_102" },
    { id: "unowned", tag: "temnia-web:latest" },
  ];
  const run = (_command, args) => {
    if (args[0] === "ps") {
      return {
        stdout:
          "live temnia-gate-web-abc_101\ndead temnia-gate-worker-old_102\nunknown temnia-gate-worker-legacy\n",
      };
    }
    if (args[0] === "images") {
      const repository = args[2].includes("temnia-web")
        ? "temnia-web"
        : "temnia-pipeline";
      // Emulate Docker's requested format: the regression must reject deleting
      // an ID even when two tags resolve to exactly the same cached image.
      return {
        stdout: images
          .filter(({ tag }) => tag.startsWith(repository))
          .map(({ id, tag }) =>
            args.at(-1).includes("{{.ID}}") ? `${id} ${tag}` : tag
          )
          .join("\n"),
      };
    }
    removed.push(args);
    return { stdout: "" };
  };
  sweepStaleGateRuns(run, (pid) => pid === 101);
  assert.deepEqual(removed, [
    ["rm", "-f", "dead"],
    ["image", "rm", "temnia-web:gate-old_102", "temnia-pipeline:gate-old_102"],
  ]);
});

test("no removals are attempted when listings are empty or fail", () => {
  const calls = [];
  sweepStaleGateRuns((_command, args) => {
    calls.push(args[0]);
    return { status: 1, stdout: undefined };
  });
  assert.deepEqual(calls, ["ps", "images", "images"]);
});
