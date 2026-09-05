#!/usr/bin/env node
// Points this checkout's hooks at .githooks/ so the pre-push gate runs.
// Runs from the root `prepare` script; a no-op outside a Git checkout (Docker).
import { spawnSync } from "node:child_process";

const inRepo = spawnSync("git", ["rev-parse", "--show-toplevel"], {
  stdio: "ignore",
});
if (inRepo.status !== 0) {
  process.stdout.write("Git hooks not installed (not a Git checkout).\n");
  process.exit(0);
}
const result = spawnSync(
  "git",
  ["config", "--local", "core.hooksPath", ".githooks"],
  {
    stdio: "inherit",
  }
);
if (result.status !== 0) {
  process.stderr.write("Could not configure repository Git hooks.\n");
  process.exit(result.status ?? 1);
}
process.stdout.write("Repository Git hooks installed from .githooks/.\n");
