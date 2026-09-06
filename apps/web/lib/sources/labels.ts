import type { IngestStage } from "@temnia/contracts";
import type { SourceRow } from "./queries";

/** Plain-language labels; enum values never render. */
const STATUS_LABELS: Record<SourceRow["status"], string> = {
  failed: "Failed",
  processing: "Processing",
  ready: "Ready",
  uploaded: "Queued",
  uploading: "Uploading",
};

const STAGE_LABELS: Record<IngestStage, string> = {
  audio: "Processing audio",
  finalize: "Almost ready",
  hls: "Preparing playback",
  peaks: "Building waveform",
  probe: "Checking recording",
  shots: "Finding shot changes",
  thumbnails: "Creating previews",
};

export function statusLabel(row: Pick<SourceRow, "status">): string {
  return STATUS_LABELS[row.status];
}

export function progressLabel(
  row: Pick<SourceRow, "status" | "ingestStage" | "ingestPercent">
): string {
  if (row.status !== "processing") {
    return statusLabel(row);
  }
  const stage = row.ingestStage
    ? (STAGE_LABELS[row.ingestStage as IngestStage] ?? row.ingestStage)
    : "Processing";
  if (row.ingestPercent === null) {
    return stage;
  }
  const percent = Math.min(100, Math.max(0, row.ingestPercent));
  return `${stage} · ${percent}%`;
}

export function formatDuration(ms: number | null): string {
  if (ms === null) {
    return "—";
  }
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

/** Seconds between two timestamps as "1m 42s", or "—" when either is missing. */
export function formatElapsed(from: string | null, to: string | null): string {
  if (!(from && to)) {
    return "—";
  }
  const total = Math.max(
    0,
    Math.round((Date.parse(to) - Date.parse(from)) / 1000)
  );
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
