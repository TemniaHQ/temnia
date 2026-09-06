/**
 * Storage key layout. The `org/{organizationId}/` prefix is the authorization
 * boundary for every read, including the media proxy and pipeline artifacts
 * (PRD §3). Keys are computed here, once, and handed to the pipeline in the
 * workflow input; Python never derives a key from anything but these.
 */
const SAFE = /[^a-zA-Z0-9._-]+/g;
const PATH_SEPARATORS = /[\\/]/;
const LEADING_DOTS = /^\.+/;
const MAX_BASENAME = 120;

export function organizationPrefix(organizationId: string): string {
  return `org/${organizationId}/`;
}

export function sourcePrefix(organizationId: string, sourceId: string): string {
  return `${organizationPrefix(organizationId)}source/${sourceId}/`;
}

/** A filename safe for a key: one segment, ASCII, bounded. */
export function sanitizeFilename(filename: string): string {
  const base = filename.split(PATH_SEPARATORS).pop() ?? "file";
  const cleaned = base.replace(SAFE, "_").replace(LEADING_DOTS, "");
  const trimmed =
    cleaned.length > MAX_BASENAME ? cleaned.slice(-MAX_BASENAME) : cleaned;
  return trimmed || "file";
}

export function masterKey(
  organizationId: string,
  sourceId: string,
  filename: string
): string {
  return `${sourcePrefix(organizationId, sourceId)}master/${sanitizeFilename(filename)}`;
}

/** Where each derived artifact lives under the source prefix. */
export const ARTIFACT_PATHS = {
  audio: "audio/audio.m4a",
  hlsMaster: "hls/master.m3u8",
  hlsPrefix: "hls/",
  peaks: "waveform/peaks.json",
  poster: "thumbs/poster.jpg",
  shots: "shots/shots.json",
  thumbnailsPrefix: "thumbs/",
} as const;

/** True when `key` sits inside the organization's prefix and has no traversal. */
export function keyBelongsTo(organizationId: string, key: string): boolean {
  if (key.includes("..") || key.includes("//") || key.startsWith("/")) {
    return false;
  }
  return key.startsWith(organizationPrefix(organizationId));
}
