#!/usr/bin/env node
// The exact-commit local gate. Validates the checked-out, clean HEAD and records
// a receipt for that SHA under .git/local-ci/. The pre-push hook runs it; the
// GitHub provenance job accepts only a `local-ci` status published from a receipt.
//
// What it proves, in order:
//   1. frozen install; Ultracite; uv sync
//   2. cross-language contracts are not stale (Zod → JSON Schema → pydantic)
//   3. turbo build, lint, typecheck, test across web, packages, and the Python pipeline
//      (db isolation probes, the pipeline's schema contract, and TranscribeWorkflow
//      end to end against Garage on the disposable compose database)
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
import { sweepStaleGateRuns } from "./local-ci-cleanup.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const RECEIPT_VERSION = 1;
const COMPOSE_PROJECT = "temnia-dev";
const COMPOSE_NETWORK = `${COMPOSE_PROJECT}_default`;
const POSTGRES_HOST_PORT = 56_432;
const GARAGE_HOST_PORT = 56_900;
const GARAGE_ACCESS_KEY = "GK746d6e696164657600000000";
const GARAGE_SECRET_KEY =
  "7f5fbe4a561d5196e4422e7fe9b8b8880846f9e153aacd3a142fd3d27f8f2bd2";
// Three parts from the 12 MB resume fixture; R2's minimum part size.
const GATE_PART_SIZE_BYTES = 5 * 1024 * 1024;
// The resume e2e waits this out before re-selecting the file.
const GATE_ADOPT_GRACE_SECONDS = 2;
// Where the worker container sees the repository's recorded engine responses.
const GATE_RECORDINGS_DIR = "/var/lib/temnia/recordings";
const GATE_HARNESS_DIR = "/var/lib/temnia/harness-fixtures";
const GATE_HARNESS_FIXTURES = resolve(
  ROOT,
  "apps/pipeline/tests/fixtures/harness"
);
// The substrate's model weights: about a gigabyte, downloaded once per machine
// into the repository's ignored .cache/. The gate runs the tests that load them
// (TEMNIA_MODEL_TESTS), because a segmenter that only runs when someone
// remembers to set a variable is a segmenter nobody is holding to anything.
const GATE_MODELS_DIR = resolve(ROOT, ".cache/temnia-models");
const STAGES = [
  "pnpm install --frozen-lockfile",
  "node scripts regression tests",
  "pnpm check",
  "uv sync --frozen (pipeline)",
  "contracts: schemas:check + pipeline contracts:check",
  "pnpm services (compose up --wait on the long-running services)",
  "db:migrate against a disposable database",
  "fetch immutable substrate model snapshots before offline loading tests",
  "turbo run build lint typecheck test (db isolation probes, pipeline schema contract, transcribe end to end, the substrate's model-loading tests)",
  "docker build apps/web + apps/pipeline",
  "playwright: web image → Garage/Temporal → pipeline image (upload, ingest, transcript correction, chapter render/review/export)",
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
  // The gate's worker and web app share the compose Temporal server with a
  // developer's own `pnpm worker`; on one namespace both would poll the same
  // task queue and the gate's ingest activities could land on a worker bound
  // to the dev database. A namespace per run keeps them apart.
  const namespace = `temnia-gate-${stamp}`;
  let databaseCreated = false;
  let containersStarted = false;
  let failure;

  process.stdout.write(
    `Validating exact commit ${sha}\nDisposable database: ${database}\n`
  );

  try {
    run("pnpm", ["install", "--frozen-lockfile"]);
    run("pnpm", ["check"]);
    run("pnpm", ["test:scripts"]);
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
      HF_HOME: GATE_MODELS_DIR,
      LOCAL_CI: "1",
      MIGRATE_DATABASE_URL: ownerUrl,
      TEMNIA_MODEL_TESTS: "1",
      TEMNIA_MODELS_DIR: GATE_MODELS_DIR,
      TEMPORAL_ADDRESS: "127.0.0.1:56233",
      TEST_DATABASE_URL: ownerUrl,
      TEST_PIPELINE_DATABASE_URL: `postgres://temnia_pipeline:temnia_pipeline@127.0.0.1:${POSTGRES_HOST_PORT}/${database}`,
    };
    const syntheticRoutes = JSON.parse(
      readFileSync(
        resolve(GATE_HARNESS_FIXTURES, "routes.synthetic.json"),
        "utf8"
      )
    );
    if (syntheticRoutes.synthetic !== true || !syntheticRoutes.snapshot_id) {
      throw new Error(
        "The gate requires an explicit synthetic harness route snapshot."
      );
    }
    const harnessEnv = [
      "-e",
      "HARNESS_ENABLED=1",
      "-e",
      "HARNESS_TOPIC_SELECTION_ENABLED=1",
      "-e",
      "HARNESS_TOPIC_SELECTION_V3_ENABLED=1",
      "-e",
      "HARNESS_BACKEND=recorded",
      "-e",
      "HARNESS_ALLOW_RECORDED=1",
      "-e",
      `HARNESS_ROUTE_SNAPSHOT_ID=${syntheticRoutes.snapshot_id}`,
    ];
    const storageEnv = [
      "-e",
      "STORAGE_ENDPOINT=http://garage:3900",
      "-e",
      `STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:${GARAGE_HOST_PORT}`,
      "-e",
      "STORAGE_REGION=garage",
      "-e",
      "STORAGE_BUCKET=temnia-media",
      "-e",
      `STORAGE_ACCESS_KEY_ID=${GARAGE_ACCESS_KEY}`,
      "-e",
      `STORAGE_SECRET_ACCESS_KEY=${GARAGE_SECRET_KEY}`,
    ];
    run("pnpm", ["--filter", "@temnia/db", "db:migrate"], { env });
    run("docker", [
      "run",
      "--rm",
      "--network",
      COMPOSE_NETWORK,
      "temporalio/admin-tools:1.31.2",
      "temporal",
      "operator",
      "namespace",
      "create",
      "--address",
      "temporal:7233",
      "--namespace",
      namespace,
      "--retention",
      "24h",
    ]);
    // Runtime loaders are offline and never resolve mutable model names. Set
    // up the exact snapshots explicitly so a clean machine exercises them too.
    run("uv", ["run", "--frozen", "python", "scripts/fetch_models.py"], {
      cwd: resolve(ROOT, "apps/pipeline"),
      env,
    });
    run("pnpm", ["turbo", "run", "build", "lint", "typecheck", "test"], {
      env,
    });

    // A gate run that was killed, or that died on a full disk, never reaches the
    // cleanup below, and its 3 GB pipeline image stays behind; enough of them
    // filled the disk and took Docker Desktop down on 2026-09-07. Sweep the
    // leftovers of earlier runs and cap the build cache before building again.
    sweepStaleGateRuns();
    run("docker", ["builder", "prune", "-f", "--keep-storage", "8GB"]);

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
      "-e",
      `TEMPORAL_NAMESPACE=${namespace}`,
      "-e",
      `PIPELINE_DATABASE_URL=postgres://temnia_pipeline:temnia_pipeline@postgres:5432/${database}`,
      // The gate encodes with the image's own ffmpeg and replays recorded
      // WhisperX responses. No Modal call in CI, ever; both are explicit rather
      // than left to the defaults so a change to a default cannot quietly point
      // the gate at a GPU that costs money.
      "-e",
      "TRANSCODE_BACKEND=local",
      "-e",
      "TRANSCRIPTION_PROVIDER=recorded",
      // The directory, not a pinned file: the transcript e2e drives three
      // fixtures to three different outcomes in one run (ready, retrying,
      // failed), which one pinned file cannot do. Each recording is matched on
      // the duration it declares, which
      // a bumped ffmpeg does not move — unlike the sha256 of the re-encoded
      // audio extract, which is why the pin was here to begin with.
      "-e",
      `TRANSCRIPTION_RECORDINGS_DIR=${GATE_RECORDINGS_DIR}`,
      // Read-only: the fixtures are the repository's, and the worker only reads
      // them. They are not baked into the image, which carries no tests.
      "-v",
      `${resolve(ROOT, "apps/pipeline/tests/fixtures/transcripts")}:${GATE_RECORDINGS_DIR}:ro`,
      ...harnessEnv,
      "-e",
      `HARNESS_ROUTE_SNAPSHOT_PATH=${GATE_HARNESS_DIR}/routes.synthetic.json`,
      "-e",
      `HARNESS_RECORDED_FIXTURE_PATH=${GATE_HARNESS_DIR}/chapter.synthetic.json`,
      "-v",
      `${GATE_HARNESS_FIXTURES}:${GATE_HARNESS_DIR}:ro`,
      ...storageEnv,
      pipelineImage,
    ]);
    // The web image runs the release phase (migrations + seed) at start; the
    // database was already migrated above, so this also proves idempotence.
    dockerRun(webContainer, [
      "--network",
      COMPOSE_NETWORK,
      "-p",
      `${webPort}:3000`,
      "-e",
      "TEMPORAL_ADDRESS=temporal:7233",
      "-e",
      `TEMPORAL_NAMESPACE=${namespace}`,
      "-e",
      `DATABASE_URL=postgres://temnia_app:temnia_app@postgres:5432/${database}`,
      "-e",
      `MIGRATE_DATABASE_URL=postgres://temnia:temnia@postgres:5432/${database}`,
      "-e",
      `UPLOAD_PART_SIZE_BYTES=${GATE_PART_SIZE_BYTES}`,
      "-e",
      `UPLOAD_ADOPT_GRACE_SECONDS=${GATE_ADOPT_GRACE_SECONDS}`,
      ...harnessEnv,
      ...storageEnv,
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
        env: {
          ...env,
          E2E_BASE_URL: baseUrl,
          UPLOAD_ADOPT_GRACE_SECONDS: String(GATE_ADOPT_GRACE_SECONDS),
          UPLOAD_PART_SIZE_BYTES: String(GATE_PART_SIZE_BYTES),
        },
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
    spawnSync(
      "docker",
      [
        "run",
        "--rm",
        "--network",
        COMPOSE_NETWORK,
        "temporalio/admin-tools:1.31.2",
        "temporal",
        "operator",
        "namespace",
        "delete",
        "--address",
        "temporal:7233",
        "--namespace",
        namespace,
        "--yes",
      ],
      { stdio: "ignore" }
    );
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
