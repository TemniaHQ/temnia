#!/usr/bin/env node
// The exact-commit local gate. Validates the checked-out, clean HEAD and records
// a receipt for that SHA under .git/local-ci/. The pre-push hook runs it; the
// GitHub provenance job accepts only a `local-ci` status published from a receipt.
//
// What it proves, in order:
//   1. frozen install; Ultracite; uv sync
//   2. cross-language contracts are not stale (Zod → JSON Schema → pydantic)
//   3. turbo build, lint, typecheck, test across web, packages, and the Python pipeline
//      (db tests run against a disposable database on the compose Postgres)
//   4. both deploy images build
//   5. the deploy images work together: a Next.js server action in the web image
//      starts a workflow that a worker in the pipeline image completes (Playwright)
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const RECEIPT_VERSION = 1;
const COMPOSE_PROJECT = "temnia-dev";
const COMPOSE_NETWORK = `${COMPOSE_PROJECT}_default`;
const POSTGRES_HOST_PORT = 56_432;
const STAGES = [
  "pnpm install --frozen-lockfile",
  "pnpm check",
  "uv sync --frozen (pipeline)",
  "contracts: schemas:check + pipeline contracts:check",
  "pnpm services (compose up --wait on the long-running services)",
  "db:migrate against a disposable database",
  "turbo run build lint typecheck test",
  "docker build apps/web + apps/pipeline",
  "playwright: web image → Temporal → pipeline image",
];
let receivedSignal;

process.chdir(ROOT);
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    receivedSignal ??= signal;
  });
}

function assertNotInterrupted() {
  if (receivedSignal) {
    throw new Error(`Local gate interrupted by ${receivedSignal}`);
  }
}

function capture(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    env: process.env,
    ...options,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed: ${(result.stderr || result.stdout).trim()}`
    );
  }
  return result.stdout.trim();
}

function run(command, args, { env = process.env, cwd = ROOT } = {}) {
  assertNotInterrupted();
  process.stdout.write(`\n> ${command} ${args.join(" ")}\n`);
  const result = spawnSync(command, args, { cwd, env, stdio: "inherit" });
  if (result.error) {
    throw result.error;
  }
  assertNotInterrupted();
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} ${result.signal ? `terminated by ${result.signal}` : `exited ${result.status}`}`
    );
  }
}

const git = (...args) => capture("git", args);
const currentSha = () => git("rev-parse", "HEAD");

function receiptPath() {
  return resolve(ROOT, git("rev-parse", "--git-path", "local-ci/receipt.json"));
}

function readReceipt() {
  const path = receiptPath();
  if (!existsSync(path)) {
    return null;
  }
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function assertClean(expectedSha) {
  const sha = currentSha();
  if (sha !== expectedSha) {
    throw new Error(
      `HEAD moved during validation (expected ${expectedSha}, found ${sha}). Run the gate again.`
    );
  }
  const dirty = git("status", "--porcelain=v1", "--untracked-files=all");
  if (dirty) {
    throw new Error(
      `The gate only attests a clean commit. Commit or stash first:\n${dirty}`
    );
  }
}

function writeReceipt(sha, startedAt) {
  const path = receiptPath();
  const temporary = `${path}.${process.pid}.tmp`;
  const payload = {
    completedAt: new Date().toISOString(),
    node: process.version,
    sha,
    stages: STAGES,
    startedAt,
    version: RECEIPT_VERSION,
  };
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
    mode: 0o600,
  });
  renameSync(temporary, path);
  return payload;
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolvePort(port)));
    });
  });
}

function psql(sql, database = "postgres") {
  return capture("docker", [
    "compose",
    "exec",
    "-T",
    "postgres",
    "psql",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    "temnia",
    "-d",
    database,
    "-c",
    sql,
  ]);
}

function quoteIdentifier(value) {
  return `"${value.replaceAll('"', '""')}"`;
}

async function waitForHttp(url, attempts = 60) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    assertNotInterrupted();
    try {
      // biome-ignore lint/performance/noAwaitInLoops: readiness polling is sequential by design
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`${url} did not answer within ${attempts}s`);
}

function dockerRun(name, args) {
  // A leftover container from an interrupted run may or may not exist.
  spawnSync("docker", ["rm", "-f", name], { stdio: "ignore" });
  return capture("docker", ["run", "-d", "--name", name, ...args]);
}

function dockerLogsTail(name) {
  const result = spawnSync("docker", ["logs", "--tail", "40", name], {
    encoding: "utf8",
  });
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

async function runFullGate(sha) {
  const startedAt = new Date().toISOString();
  const stamp = `${Date.now().toString(36)}_${process.pid}`;
  const database = `temnia_gate_${stamp}`;
  const ownerUrl = `postgres://temnia:temnia@127.0.0.1:${POSTGRES_HOST_PORT}/${database}`;
  const webImage = `temnia-web:gate-${stamp}`;
  const pipelineImage = `temnia-pipeline:gate-${stamp}`;
  const webContainer = `temnia-gate-web-${stamp}`;
  const workerContainer = `temnia-gate-worker-${stamp}`;
  let databaseCreated = false;
  let containersStarted = false;
  let failure;

  process.stdout.write(
    `Validating exact commit ${sha}\nDisposable database: ${database}\n`
  );

  try {
    run("pnpm", ["install", "--frozen-lockfile"]);
    run("pnpm", ["check"]);
    run("pnpm", ["--filter", "@temnia/pipeline", "sync"]);
    run("pnpm", ["--filter", "@temnia/contracts", "schemas:check"]);
    run("pnpm", ["--filter", "@temnia/pipeline", "contracts:check"]);

    // `pnpm services` waits on the long-running services only: compose's --wait
    // reports a finished one-shot (schema setup, namespace) as a failure.
    run("pnpm", ["services"]);
    psql(`CREATE DATABASE ${quoteIdentifier(database)}`);
    databaseCreated = true;
    psql(
      `GRANT CONNECT ON DATABASE ${quoteIdentifier(database)} TO temnia_app, temnia_pipeline`
    );

    const env = {
      ...process.env,
      CI: "1",
      LOCAL_CI: "1",
      MIGRATE_DATABASE_URL: ownerUrl,
      TEMPORAL_ADDRESS: "127.0.0.1:56233",
      TEST_DATABASE_URL: ownerUrl,
    };
    run("pnpm", ["--filter", "@temnia/db", "db:migrate"], { env });
    run("pnpm", ["turbo", "run", "build", "lint", "typecheck", "test"], {
      env,
    });

    run("docker", [
      "build",
      "--tag",
      webImage,
      "--file",
      "apps/web/Dockerfile",
      ".",
    ]);
    run("docker", ["build", "--tag", pipelineImage, "apps/pipeline"]);

    const webPort = await freePort();
    dockerRun(workerContainer, [
      "--network",
      COMPOSE_NETWORK,
      "-e",
      "TEMPORAL_ADDRESS=temporal:7233",
      pipelineImage,
    ]);
    dockerRun(webContainer, [
      "--network",
      COMPOSE_NETWORK,
      "-p",
      `${webPort}:3000`,
      "-e",
      "TEMPORAL_ADDRESS=temporal:7233",
      webImage,
    ]);
    containersStarted = true;
    const baseUrl = `http://127.0.0.1:${webPort}`;
    await waitForHttp(`${baseUrl}/api/health`);

    run("pnpm", [
      "--filter",
      "@temnia/web",
      "exec",
      "playwright",
      "install",
      "chromium",
    ]);
    try {
      run("pnpm", ["--filter", "@temnia/web", "e2e"], {
        env: { ...env, E2E_BASE_URL: baseUrl },
      });
    } catch (error) {
      process.stderr.write(
        `\n--- worker logs ---\n${dockerLogsTail(workerContainer)}\n--- web logs ---\n${dockerLogsTail(webContainer)}\n`
      );
      throw error;
    }
  } catch (error) {
    failure = error;
  } finally {
    if (containersStarted) {
      spawnSync("docker", ["rm", "-f", webContainer, workerContainer], {
        stdio: "ignore",
      });
    }
    spawnSync("docker", ["image", "rm", "-f", webImage, pipelineImage], {
      stdio: "ignore",
    });
    if (databaseCreated) {
      try {
        psql(
          `DROP DATABASE IF EXISTS ${quoteIdentifier(database)} WITH (FORCE)`
        );
      } catch (error) {
        process.stderr.write(`Disposable database cleanup failed: ${error}\n`);
        failure ??= error;
      }
    }
  }

  if (failure) {
    throw failure;
  }
  assertClean(sha);
  const receipt = writeReceipt(sha, startedAt);
  process.stdout.write(
    `\nLocal gate passed for ${sha} at ${receipt.completedAt}.\n`
  );
}

async function main() {
  const args = process.argv.slice(2);
  const requireIndex = args.indexOf("--require-sha");
  const expectedSha = requireIndex === -1 ? null : args[requireIndex + 1];
  if (requireIndex !== -1 && !expectedSha) {
    throw new Error("--require-sha needs a commit SHA");
  }
  const sha = currentSha();
  if (expectedSha && expectedSha !== sha) {
    throw new Error(
      `Push targets ${expectedSha}, but the checked-out HEAD is ${sha}. Check out the pushed branch and validate it.`
    );
  }
  assertClean(sha);
  const receipt = readReceipt();
  if (
    !args.includes("--force") &&
    receipt?.version === RECEIPT_VERSION &&
    receipt.sha === sha
  ) {
    process.stdout.write(
      `Local gate already passed for exact commit ${sha} at ${receipt.completedAt}.\n`
    );
    return;
  }
  await runFullGate(sha);
}

main().catch((error) => {
  process.stderr.write(`\nLocal gate failed: ${error.message ?? error}\n`);
  process.exit(1);
});
