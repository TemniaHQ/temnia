import { spawnSync } from "node:child_process";

const GATE_STAMP = /gate-(?:web-|worker-)?[0-9a-z]+_(\d+)$/;
const WHITESPACE = /\s+/;

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

/** Remove abandoned gate resources without removing another run's image tags. */
export function sweepStaleGateRuns(run = spawnSync, alive = processAlive) {
  const list = (args) =>
    (run("docker", args, { encoding: "utf8" }).stdout ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  const abandoned = (name) => {
    const stamp = GATE_STAMP.exec(name ?? "");
    return stamp !== null && !alive(Number(stamp[1]));
  };
  const containers = list([
    "ps",
    "-a",
    "--filter",
    "name=temnia-gate-",
    "--format",
    "{{.ID}} {{.Names}}",
  ]).flatMap((line) => {
    const [id, name] = line.split(WHITESPACE, 2);
    return abandoned(name) ? [id] : [];
  });
  if (containers.length > 0) {
    run("docker", ["rm", "-f", ...containers], { stdio: "ignore" });
  }
  const images = ["temnia-web", "temnia-pipeline"].flatMap((repository) =>
    list([
      "images",
      "--filter",
      `reference=${repository}:gate-*`,
      "--format",
      "{{.Repository}}:{{.Tag}}",
    ]).filter(abandoned)
  );
  if (images.length > 0) {
    // Cached builds share image IDs. Removing an ID with -f removes every tag,
    // including a live run's tag; remove only the abandoned tag names instead.
    run("docker", ["image", "rm", ...new Set(images)], { stdio: "ignore" });
  }
}
