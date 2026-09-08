import type { ChapterChecks } from "@temnia/contracts";

const BASE_CHECKS = [
  "full_decode",
  "duration",
  "video_presence",
  "audio_presence",
] as const;
const VIDEO_CHECKS = [
  "video_codec_h264",
  "video_width",
  "video_height",
  "rotation",
  "sample_aspect_ratio",
  "video_start_offset",
  "video_end",
] as const;
const AUDIO_CHECKS = [
  "audio_codec_aac",
  "audio_layout",
  "audio_channels",
  "audio_start_offset",
  "audio_end",
] as const;

export function technicalEligibility(checks: ChapterChecks): {
  eligible: boolean;
  warnings: string[];
} {
  const byName = new Map(
    checks.technicalChecks.map((check) => [check.name, check])
  );
  const required: string[] = [...BASE_CHECKS, "caption_bounds"];
  if ((byName.get("video_presence")?.measured ?? 0) > 0) {
    required.push(...VIDEO_CHECKS);
  }
  if ((byName.get("audio_presence")?.measured ?? 0) > 0) {
    required.push(...AUDIO_CHECKS);
  }
  const eligible =
    checks.technicalChecks.length > 0 &&
    required.every(
      (name) =>
        checks.technicalChecks.filter((check) => check.name === name).length ===
        1
    ) &&
    checks.technicalChecks.every((check) => check.status !== "fail");
  return {
    eligible,
    warnings: checks.technicalChecks
      .filter((check) => check.status === "warn")
      .map((check) => `${check.name}: ${check.message}`),
  };
}
