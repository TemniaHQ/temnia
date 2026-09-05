#!/usr/bin/env node
// Verified delivery: run the exact-commit gate, push, publish the `local-ci`
// status for that SHA, then (for `pr`) open the pull request and wait for the
// GitHub-owned provenance check. Usage: verified-pr.mjs <attest|push|pr> [gh pr create args]
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ALLOWED_BRANCH = /^(chore|docs|feat|fix)\//;
const GITHUB_ORIGIN = /github\.com[/:]([^/]+\/[^/]+?)(?:\.git)?$/;
const REGISTRATION_ATTEMPTS = 30;
const REGISTRATION_INTERVAL_MS = 3000;

process.chdir(ROOT);

function command(name, args, { capture = false } = {}) {
  const result = spawnSync(name, args, {
    cwd: ROOT,
    encoding: capture ? "utf8" : undefined,
    env: process.env,
    stdio: capture ? "pipe" : "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const detail = capture
      ? (result.stderr || result.stdout).trim()
      : `exit ${result.status}`;
    throw new Error(`${name} ${args.join(" ")} failed: ${detail}`);
  }
  return capture ? result.stdout.trim() : "";
}

const git = (...args) => command("git", args, { capture: true });

function branchAndSha() {
  const branch = git("branch", "--show-current");
  if (!branch) {
    throw new Error(
      "Verified pushes need a checked-out branch, not a detached HEAD"
    );
  }
  if (!ALLOWED_BRANCH.test(branch)) {
    throw new Error(
      `Branch ${branch} must be named feat/, fix/, chore/, or docs/`
    );
  }
  return { branch, sha: git("rev-parse", "HEAD") };
}

function receiptFor(sha) {
  const path = resolve(
    ROOT,
    git("rev-parse", "--git-path", "local-ci/receipt.json")
  );
  if (!existsSync(path)) {
    throw new Error(`No local gate receipt exists for ${sha}`);
  }
  const receipt = JSON.parse(readFileSync(path, "utf8"));
  if (receipt.version !== 1 || receipt.sha !== sha) {
    throw new Error(`The local gate receipt does not match ${sha}`);
  }
  return receipt;
}

function repository() {
  const origin = git("remote", "get-url", "origin");
  const match = origin.match(GITHUB_ORIGIN);
  if (!match) {
    throw new Error(`Cannot derive a GitHub repository from origin ${origin}`);
  }
  return match[1];
}

function assertRemoteMatches(branch, sha) {
  const remote = git("rev-parse", `refs/remotes/origin/${branch}`);
  if (remote !== sha) {
    throw new Error(`origin/${branch} is ${remote}, not the validated ${sha}`);
  }
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function openPullRequest(branch) {
  const list = JSON.parse(
    command(
      "gh",
      [
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "open",
        "--limit",
        "1",
        "--json",
        "headRefOid,isDraft,number,url",
      ],
      { capture: true }
    )
  );
  return list[0] ?? null;
}

function workflowRunId(sha) {
  const runs = JSON.parse(
    command(
      "gh",
      [
        "run",
        "list",
        "--workflow",
        "ci.yml",
        "--commit",
        sha,
        "--event",
        "pull_request",
        "--limit",
        "20",
        "--json",
        "createdAt,databaseId,headSha",
      ],
      { capture: true }
    )
  );
  return runs
    .filter((run) => run.headSha === sha)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]?.databaseId;
}

function waitForRequiredCheck(branch, sha, { pullRequestRequired }) {
  const pr = openPullRequest(branch);
  if (!pr) {
    if (pullRequestRequired) {
      throw new Error(`No open pull request exists for ${branch}`);
    }
    return;
  }
  if (pr.headRefOid !== sha) {
    throw new Error(`PR #${pr.number} points at ${pr.headRefOid}, not ${sha}`);
  }
  if (pr.isDraft) {
    process.stdout.write(
      `PR #${pr.number} is a draft; the required check starts when it is marked ready.\n`
    );
    return;
  }
  process.stdout.write(
    `Waiting for GitHub's required check on PR #${pr.number}...\n`
  );
  for (let attempt = 1; attempt <= REGISTRATION_ATTEMPTS; attempt += 1) {
    const runId = workflowRunId(sha);
    if (runId) {
      command("gh", [
        "run",
        "watch",
        String(runId),
        "--compact",
        "--exit-status",
      ]);
      process.stdout.write(
        `Required GitHub check passed for PR #${pr.number}.\n`
      );
      return;
    }
    if (attempt < REGISTRATION_ATTEMPTS) {
      sleep(REGISTRATION_INTERVAL_MS);
    }
  }
  throw new Error(
    `GitHub did not register ci.yml for ${sha} within 90 seconds`
  );
}

function attest(branch, sha) {
  const receipt = receiptFor(sha);
  assertRemoteMatches(branch, sha);
  const repo = repository();
  const description = `Local gate passed ${receipt.completedAt.slice(0, 16).replace("T", " ")} UTC`;
  command("gh", [
    "api",
    "--method",
    "POST",
    `repos/${repo}/statuses/${sha}`,
    "-f",
    "state=success",
    "-f",
    "context=local-ci",
    "-f",
    `description=${description}`,
    "-f",
    `target_url=https://github.com/${repo}/commit/${sha}`,
  ]);
  process.stdout.write(`Published local-ci status for ${sha}.\n`);
}

function pushVerified() {
  const { branch, sha } = branchAndSha();
  command("pnpm", ["ci:local"]);
  receiptFor(sha);
  command("git", ["push", "--set-upstream", "origin", branch]);
  assertRemoteMatches(branch, sha);
  attest(branch, sha);
  return { branch, sha };
}

function main() {
  const [, , mode, ...rest] = process.argv;
  const passthrough = rest.filter((arg) => arg !== "--");
  if (mode === "attest") {
    const { branch, sha } = branchAndSha();
    attest(branch, sha);
    return;
  }
  if (mode === "push") {
    const { branch, sha } = pushVerified();
    waitForRequiredCheck(branch, sha, { pullRequestRequired: false });
    return;
  }
  if (mode === "pr") {
    const { branch, sha } = pushVerified();
    command("gh", [
      "pr",
      "create",
      "--base",
      "main",
      "--head",
      branch,
      ...passthrough,
    ]);
    waitForRequiredCheck(branch, sha, { pullRequestRequired: true });
    return;
  }
  throw new Error(
    "Usage: verified-pr.mjs <attest|push|pr> [gh pr create arguments]"
  );
}

try {
  main();
} catch (error) {
  process.stderr.write(`Verified delivery failed: ${error.message ?? error}\n`);
  process.exit(1);
}
