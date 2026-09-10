import {
  ChapterChecksSchema,
  ChapterEditSpecSchema,
  ChapterRendersSchema,
  type HarnessArtifactRef,
  type RationalTime,
  type TopicAssessment,
  type TopicAssessmentCandidate,
  TopicAssessmentSchema,
  type TopicCompiledVideo,
  TopicEditSpecSchema,
  TopicExportSchema,
  TopicRendersSchema,
} from "@temnia/contracts";
import type { ZodType } from "zod";
import { verifiedArtifactJson } from "./artifact";
import { technicalEligibility } from "./checks";
import type { ChapterArtifactRef, ChapterView } from "./queries";

type Loader = <T>(ref: ChapterArtifactRef, schema: ZodType<T>) => Promise<T>;
const URL_SUFFIX = /[?#]/;
const PREFIX = "/api/media/";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

export interface TopicVideoView {
  assessment: TopicAssessmentCandidate | null;
  captionsUrl: string | null;
  durationMs: number;
  endMs: number;
  mediaUrl: string | null;
  startMs: number;
  technicalPass: boolean;
  video: TopicCompiledVideo;
  warnings: string[];
}

export interface TopicRevisionView {
  assessment: TopicAssessment | null;
  exportedCandidateIds: string[];
  exportUrl: string | null;
  summary: string;
  videos: TopicVideoView[];
}

function requireFact(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function organizationFor(key: string, sourceId: string): string {
  const parts = key.split("/");
  requireFact(
    !(key.includes("%") || key.includes("\\")) &&
      parts[0] === "org" &&
      UUID.test(parts[1] ?? "") &&
      parts[2] === "source" &&
      parts[3] === sourceId &&
      parts.length > 4 &&
      parts.every((part) => part && part !== "." && part !== ".."),
    "A topic file is outside this source's storage scope."
  );
  return parts[1] as string;
}

function checkedKey(ref: ChapterArtifactRef, sourceId: string): string {
  requireFact(
    ref.url.startsWith(PREFIX) && !URL_SUFFIX.test(ref.url),
    "Invalid topic artifact URL."
  );
  const key = ref.url.slice(PREFIX.length);
  organizationFor(key, sourceId);
  return key;
}

function nestedRef(
  ref: HarnessArtifactRef,
  kind: ChapterArtifactRef["kind"],
  sourceId: string,
  organizationId: string
): ChapterArtifactRef {
  requireFact(
    ref.kind === kind &&
      organizationFor(ref.storageKey, sourceId) === organizationId,
    "A topic dependency has the wrong kind or source scope."
  );
  return { ...ref, kind, metadata: {}, url: `${PREFIX}${ref.storageKey}` };
}

function mediaUrl(ref: ChapterArtifactRef): string {
  return `${PREFIX}${ref.url.slice(PREFIX.length).split("/").map(encodeURIComponent).join("/")}`;
}

function one<T>(values: T[], message: string): T {
  requireFact(values.length === 1, message);
  return values[0] as T;
}

export function topicDurationMs(
  start: RationalTime,
  end: RationalTime
): number {
  const denominator = BigInt(start.denominator) * BigInt(end.denominator);
  const numerator =
    BigInt(end.numerator) * BigInt(start.denominator) -
    BigInt(start.numerator) * BigInt(end.denominator);
  requireFact(
    numerator > BigInt(0),
    "Topic video has an invalid rational extent."
  );
  return Number(
    (numerator * BigInt(2000) + denominator) / (BigInt(2) * denominator)
  );
}

export function topicArtifactIdentity(view: ChapterView): string {
  return JSON.stringify([
    view.run?.id,
    view.run?.currentRevision,
    view.run?.acceptedRevision,
    view.currentEdit?.id,
    view.acceptedEdit?.id,
    view.artifacts.map((ref) => `${ref.id}:${ref.sha256}`).sort(),
  ]);
}

export function topicEditorialStatus(
  assessment: TopicAssessment | null,
  candidate: TopicAssessmentCandidate | null
):
  | "Passed text review"
  | "Needs review"
  | "Rejected by text review"
  | "Unverified" {
  if (
    !(
      assessment?.verifierFamily &&
      assessment.verifierFamily !== assessment.proposerFamily &&
      candidate?.coldReview &&
      candidate.sourceReview
    )
  ) {
    return "Unverified";
  }
  if (candidate.status === "rejected") {
    return "Rejected by text review";
  }
  const { coldReview: cold, sourceReview: source } = candidate;
  const criteria = [
    cold.intelligibleBeginning,
    cold.coherentTopic,
    cold.completeDiscussion,
    cold.titleFaithful,
    source.faithfulMeaning,
    source.completeContext,
    source.distinctPurpose,
  ];
  return candidate.status === "passed" &&
    criteria.every((criterion) => criterion.status === "pass")
    ? "Passed text review"
    : "Needs review";
}

/** Independent extents may overlap; their union, not their sum, measures source reuse. */
export function reusedContextMs(
  video: TopicVideoView,
  all: TopicVideoView[]
): number {
  const intervals = all
    .filter((other) => other.video.candidate.id !== video.video.candidate.id)
    .map(
      (other) =>
        [
          Math.max(video.startMs, other.startMs),
          Math.min(video.endMs, other.endMs),
        ] as const
    )
    .filter(([start, stop]) => stop > start)
    .sort(([left], [right]) => left - right);
  let end = video.startMs;
  let total = 0;
  for (const [start, stop] of intervals) {
    total += Math.max(0, stop - Math.max(start, end));
    end = Math.max(end, stop);
  }
  return total;
}

/** Hash-load one portfolio and only descriptors bound to its exact revision. */
export async function loadTopicRevision(
  sourceId: string,
  view: ChapterView,
  accepted = false,
  load: Loader = verifiedArtifactJson
): Promise<TopicRevisionView | null> {
  const editRef = accepted ? view.acceptedEdit : view.currentEdit;
  const { run } = view;
  if (!(editRef && run)) {
    return null;
  }
  requireFact(
    editRef.kind === "edit" &&
      editRef.metadata.format === "topic-edit/1" &&
      editRef.metadata.runId === run.id,
    "The topic portfolio belongs to another run."
  );
  const organizationId = organizationFor(
    checkedKey(editRef, sourceId),
    sourceId
  );
  const edit = await load(editRef, TopicEditSpecSchema);
  requireFact(
    edit.sourceId === sourceId,
    "The topic portfolio belongs to another source."
  );
  let assessment: TopicAssessment | null = null;
  if (editRef.metadata.assessmentArtifactId) {
    const ref = one(
      view.artifacts.filter(
        (item) =>
          item.id === editRef.metadata.assessmentArtifactId &&
          item.kind === "checks" &&
          item.metadata.format === "topic-assessment/1" &&
          item.metadata.runId === run.id
      ),
      "The topic assessment is missing or ambiguous."
    );
    requireFact(
      organizationFor(checkedKey(ref, sourceId), sourceId) === organizationId,
      "Foreign topic assessment."
    );
    assessment = await load(ref, TopicAssessmentSchema);
    requireFact(
      assessment.runId === run.id &&
        assessment.evidenceSha256 === edit.evidenceSha256 &&
        assessment.proposalSha256 === editRef.metadata.proposalSha256,
      "The topic assessment does not belong to this proposal and evidence."
    );
    requireFact(
      new Set(assessment.candidates.map((item) => item.candidateId)).size ===
        assessment.candidates.length &&
        assessment.candidates.every(
          (item) =>
            (!item.coldReview ||
              item.coldReview.candidateId === item.candidateId) &&
            (!item.sourceReview ||
              item.sourceReview.candidateId === item.candidateId)
        ),
      "Invalid topic assessment candidate identities."
    );
  }
  const videos: TopicVideoView[] = edit.videos.map((video) => {
    const section = one(
      video.edit.sections.filter((item) => item.id === video.keptSectionId),
      "Topic keep is missing."
    );
    const start = one(
      video.edit.boundaries.filter(
        (item) => item.id === section.startBoundaryId
      ),
      "Topic start is missing."
    );
    const end = one(
      video.edit.boundaries.filter((item) => item.id === section.endBoundaryId),
      "Topic end is missing."
    );
    requireFact(
      end.timeMs > start.timeMs,
      "Topic video has an invalid extent."
    );
    return {
      assessment:
        assessment?.candidates.find(
          (item) => item.candidateId === video.candidate.id
        ) ?? null,
      captionsUrl: null,
      durationMs: topicDurationMs(start.time, end.time),
      endMs: end.timeMs,
      mediaUrl: null,
      startMs: start.timeMs,
      technicalPass: false,
      video,
      warnings: [],
    };
  });
  const matches = view.artifacts.filter(
    (ref) =>
      ref.kind === "render" &&
      ref.metadata.format === "topic-renders/1" &&
      ref.metadata.runId === run.id &&
      ref.metadata.editSha256 === editRef.sha256
  );
  let rendered: ReturnType<typeof TopicRendersSchema.parse> | null = null;
  if (matches.length) {
    const ref = one(matches, "Topic render descriptors are ambiguous.");
    requireFact(
      organizationFor(checkedKey(ref, sourceId), sourceId) === organizationId,
      "Foreign topic renders."
    );
    rendered = await load(ref, TopicRendersSchema);
    requireFact(
      rendered.runId === run.id &&
        rendered.editSha256 === editRef.sha256 &&
        rendered.videos.length === videos.length &&
        new Set(rendered.videos.map((item) => item.candidateId)).size ===
          videos.length,
      "Topic renders do not describe this portfolio."
    );
    // Load independent video dependencies in small batches; source duration does not limit the portfolio.
    for (let offset = 0; offset < videos.length; offset += 4) {
      // biome-ignore lint/performance/noAwaitInLoops: bound simultaneous authenticated artifact loads to four videos
      await Promise.all(
        videos.slice(offset, offset + 4).map(async (video) => {
          const item = one(
            rendered?.videos.filter(
              (value) => value.candidateId === video.video.candidate.id
            ) ?? [],
            "A topic render is missing."
          );
          const executionRef = nestedRef(
            item.execution,
            "edit",
            sourceId,
            organizationId
          );
          const execution = await load(executionRef, ChapterEditSpecSchema);
          requireFact(
            JSON.stringify(execution) === JSON.stringify(video.video.edit),
            "A topic execution differs from its portfolio."
          );
          const descriptor = await load(
            nestedRef(item.descriptor, "render", sourceId, organizationId),
            ChapterRendersSchema
          );
          requireFact(
            descriptor.runId === run.id &&
              descriptor.editSha256 === executionRef.sha256 &&
              descriptor.renders.length === 1,
            "A topic descriptor differs from its execution."
          );
          const render = one(
            descriptor.renders,
            "A topic must render exactly one video."
          );
          requireFact(
            render.sectionId === video.video.keptSectionId &&
              render.durationMs === video.durationMs,
            "A topic render has the wrong section or duration."
          );
          video.mediaUrl = mediaUrl(
            nestedRef(render.media, "render", sourceId, organizationId)
          );
          video.captionsUrl = render.captions
            ? mediaUrl(
                nestedRef(render.captions, "render", sourceId, organizationId)
              )
            : null;
          if (render.checks) {
            const checks = await load(
              nestedRef(render.checks, "checks", sourceId, organizationId),
              ChapterChecksSchema
            );
            requireFact(
              checks.editSha256 === executionRef.sha256 &&
                checks.technicalChecks.every(
                  (check) => check.sectionId === video.video.keptSectionId
                ),
              "Topic technical checks name another execution or section."
            );
            const eligibility = technicalEligibility(checks);
            video.technicalPass = eligibility.eligible;
            video.warnings = eligibility.warnings;
          }
        })
      );
    }
  }
  let exportUrl: string | null = null;
  let exportedCandidateIds: string[] = [];
  const revision = accepted ? run.acceptedRevision : run.currentRevision;
  if (rendered) {
    const exports = view.artifacts.filter(
      (ref) =>
        ref.kind === "export" &&
        ref.metadata.format === "topic-export/1" &&
        ref.metadata.runId === run.id &&
        ref.metadata.editSha256 === editRef.sha256
    );
    if (exports.length) {
      const ref = one(exports, "Topic exports are ambiguous.");
      requireFact(
        organizationFor(checkedKey(ref, sourceId), sourceId) === organizationId,
        "Foreign topic export."
      );
      const manifest = await load(ref, TopicExportSchema);
      requireFact(
        manifest.runId === run.id &&
          manifest.editSha256 === editRef.sha256 &&
          manifest.revision === revision &&
          new Set(manifest.videos.map((item) => item.candidateId)).size ===
            manifest.videos.length,
        "Topic export does not name this revision."
      );
      for (const item of manifest.videos) {
        const video = one(
          videos.filter(
            (value) => value.video.candidate.id === item.candidateId
          ),
          "Exported topic is missing."
        );
        const keep = video.video.edit.sections.find(
          (section) => section.id === video.video.keptSectionId
        );
        requireFact(
          keep?.reviewState === "accepted" &&
            video.technicalPass &&
            JSON.stringify(item) ===
              JSON.stringify(
                rendered.videos.find(
                  (value) => value.candidateId === item.candidateId
                )
              ),
          "Topic export includes an unaccepted or unverified file."
        );
      }
      exportUrl = mediaUrl(ref);
      exportedCandidateIds = manifest.videos.map((item) => item.candidateId);
    }
  }
  return {
    assessment,
    exportedCandidateIds,
    exportUrl,
    summary: edit.summary,
    videos,
  };
}
