import {
  ChapterChecksSchema,
  ChapterEditSpecSchema,
  ChapterExportSchema,
  ChapterRendersSchema,
  type HarnessArtifactRef,
} from "@temnia/contracts";
import type { ZodType } from "zod";
import { verifiedArtifactJson } from "./artifact";
import { technicalEligibility } from "./checks";
import type { ChapterArtifactRef, ChapterView } from "./queries";

const MEDIA_PROXY_PREFIX = "/api/media/";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface AcceptedDownloadLink {
  download: string;
  href: string;
  sha256: string;
  sizeBytes: number;
}

export interface AcceptedDownloadChapter {
  captions: AcceptedDownloadLink | null;
  sectionId: string;
  title: string;
  video: AcceptedDownloadLink;
}

export interface AcceptedDownloads {
  chapters: AcceptedDownloadChapter[];
  manifest: AcceptedDownloadLink;
  revision: number;
  warnings: string[];
}

export type ArtifactJsonLoader = <T>(
  artifact: ChapterArtifactRef,
  schema: ZodType<T>
) => Promise<T>;

interface AcceptedDownloadInput {
  sourceId: string;
  view: ChapterView;
}

export class AcceptedDownloadsValidationError extends Error {
  override readonly name = "AcceptedDownloadsValidationError";
}

function fail(message: string): never {
  throw new AcceptedDownloadsValidationError(message);
}

export function acceptedDownloadsErrorMessage(error: unknown): string {
  return error instanceof AcceptedDownloadsValidationError
    ? error.message
    : "The accepted files could not be verified. Check your connection and retry.";
}

function proxyStorageKey(artifact: ChapterArtifactRef): string {
  if (
    !artifact.url.startsWith(MEDIA_PROXY_PREFIX) ||
    artifact.url.includes("?") ||
    artifact.url.includes("#")
  ) {
    return fail(
      "The accepted artifact does not use the media download boundary."
    );
  }
  return artifact.url.slice(MEDIA_PROXY_PREFIX.length);
}

function sourceOrganization(storageKey: string, sourceId: string): string {
  const parts = storageKey.split("/");
  if (
    storageKey.includes("%") ||
    storageKey.includes("\\") ||
    parts.length < 5 ||
    parts[0] !== "org" ||
    !UUID.test(parts[1] ?? "") ||
    parts[2] !== "source" ||
    parts[3] !== sourceId ||
    parts.slice(4).some((part) => !part || part === "." || part === "..")
  ) {
    fail("An accepted file is outside this source's storage prefix.");
  }
  return parts[1] ?? fail("The accepted file has no organization scope.");
}

function assertSourceKey(
  storageKey: string,
  sourceId: string,
  organizationId: string
): void {
  if (sourceOrganization(storageKey, sourceId) !== organizationId) {
    fail("An accepted file belongs to another organization.");
  }
}

function mediaHref(storageKey: string): string {
  return `${MEDIA_PROXY_PREFIX}${storageKey
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/")}`;
}

function linkFor(
  reference: HarnessArtifactRef,
  sourceId: string,
  organizationId: string,
  download: string,
  expectedKind: "render"
): AcceptedDownloadLink {
  if (reference.kind !== expectedKind) {
    return fail("An accepted file has the wrong artifact kind.");
  }
  assertSourceKey(reference.storageKey, sourceId, organizationId);
  return {
    download,
    href: mediaHref(reference.storageKey),
    sha256: reference.sha256,
    sizeBytes: reference.sizeBytes,
  };
}

function matchingArtifacts(
  artifacts: readonly ChapterArtifactRef[],
  kind: ChapterArtifactRef["kind"],
  format: string,
  runId: string,
  editSha256: string,
  revision?: number
): ChapterArtifactRef[] {
  return artifacts.filter(
    (artifact) =>
      artifact.kind === kind &&
      artifact.metadata.format === format &&
      artifact.metadata.runId === runId &&
      artifact.metadata.editSha256 === editSha256 &&
      (revision === undefined || artifact.metadata.revision === revision)
  );
}

function oneArtifact(
  artifacts: readonly ChapterArtifactRef[],
  label: string
): ChapterArtifactRef {
  if (artifacts.length !== 1) {
    return fail(`The accepted ${label} is missing or ambiguous.`);
  }
  const [artifact] = artifacts;
  if (!artifact) {
    return fail(`The accepted ${label} is missing.`);
  }
  return artifact;
}

function sameRenders(
  left: ReturnType<typeof ChapterRendersSchema.parse>["renders"],
  right: ReturnType<typeof ChapterRendersSchema.parse>["renders"]
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function mapWithConcurrency<Input, Output>(
  inputs: readonly Input[],
  concurrency: number,
  visit: (input: Input) => Promise<Output>
): Promise<Output[]> {
  const outputs = new Array<Output>(inputs.length);
  let nextIndex = 0;
  await Promise.all(
    Array.from({ length: Math.min(concurrency, inputs.length) }, async () => {
      while (nextIndex < inputs.length) {
        const index = nextIndex;
        nextIndex += 1;
        const input = inputs[index];
        if (input !== undefined) {
          // biome-ignore lint/performance/noAwaitInLoops: each bounded worker is intentionally sequential
          outputs[index] = await visit(input);
        }
      }
    })
  );
  return outputs;
}

/** Identity for accepted immutable inputs; unchanged polling must not restart reads. */
export function acceptedDownloadsIdentity({
  sourceId,
  view,
}: AcceptedDownloadInput): string | null {
  const { acceptedEdit: edit, run } = view;
  if (!(run && edit && run.acceptedRevision)) {
    return null;
  }
  const related = view.artifacts
    .filter(
      (artifact) =>
        artifact.kind !== "edit" && artifact.metadata.editSha256 === edit.sha256
    )
    .map((artifact) => `${artifact.kind}:${artifact.id}:${artifact.sha256}`)
    .sort();
  return JSON.stringify([
    sourceId,
    run.id,
    run.acceptedRevision,
    edit.id,
    edit.sha256,
    related,
  ]);
}

/** Validate the accepted edit/export/render/check graph before exposing file links. */
export async function loadAcceptedDownloads(
  input: AcceptedDownloadInput,
  load: ArtifactJsonLoader = verifiedArtifactJson
): Promise<AcceptedDownloads> {
  const { sourceId, view } = input;
  const { acceptedEdit: editRef, run } = view;
  if (!(run && editRef && run.acceptedRevision)) {
    return fail("There is no accepted chapter revision to download.");
  }
  if (!(UUID.test(sourceId) && UUID.test(run.id))) {
    return fail("The accepted download scope is invalid.");
  }
  if (editRef.kind !== "edit") {
    return fail("The accepted edit pointer has the wrong artifact kind.");
  }
  const revision = run.acceptedRevision;
  const descriptorRef = oneArtifact(
    matchingArtifacts(
      view.artifacts,
      "render",
      "chapter-renders/1",
      run.id,
      editRef.sha256
    ),
    "render descriptor"
  );
  const exportRef = oneArtifact(
    matchingArtifacts(
      view.artifacts,
      "export",
      "chapter-export/1",
      run.id,
      editRef.sha256,
      revision
    ),
    "manifest"
  );
  const editStorageKey = proxyStorageKey(editRef);
  const descriptorStorageKey = proxyStorageKey(descriptorRef);
  const exportStorageKey = proxyStorageKey(exportRef);
  const organizationId = sourceOrganization(editStorageKey, sourceId);
  assertSourceKey(descriptorStorageKey, sourceId, organizationId);
  assertSourceKey(exportStorageKey, sourceId, organizationId);

  const [edit, descriptor, manifest] = await Promise.all([
    load(editRef, ChapterEditSpecSchema),
    load(descriptorRef, ChapterRendersSchema),
    load(exportRef, ChapterExportSchema),
  ]);
  if (edit.sourceId !== sourceId) {
    return fail("The accepted edit belongs to another source.");
  }
  if (edit.sections.some((section) => section.reviewState !== "accepted")) {
    return fail("Every accepted section must have explicit human approval.");
  }
  if (
    descriptor.runId !== run.id ||
    descriptor.editSha256 !== editRef.sha256 ||
    descriptor.renders.length !== Number(descriptorRef.metadata.renderCount) ||
    manifest.runId !== run.id ||
    manifest.revision !== revision ||
    manifest.editSha256 !== editRef.sha256 ||
    manifest.manifestKey !== descriptorStorageKey
  ) {
    return fail("The accepted manifest does not match this run and revision.");
  }
  if (!sameRenders(manifest.chapters, descriptor.renders)) {
    return fail("The accepted manifest and render descriptor disagree.");
  }

  const keeps = edit.sections.filter((section) => section.kind === "keep");
  const keepIds = keeps.map((section) => section.id);
  const renderIds = descriptor.renders.map((render) => render.sectionId);
  if (
    new Set(renderIds).size !== renderIds.length ||
    keepIds.length !== renderIds.length ||
    keepIds.some((sectionId, index) => renderIds[index] !== sectionId)
  ) {
    return fail(
      "Accepted renders do not exactly cover the kept chapters in order."
    );
  }

  const checkInputs = descriptor.renders.map((render, index) => {
    const checkRef = render.checks;
    if (checkRef?.kind !== "checks") {
      return fail("An accepted chapter is missing its technical checks.");
    }
    assertSourceKey(checkRef.storageKey, sourceId, organizationId);
    const matches = view.artifacts.filter(
      (candidate) =>
        candidate.kind === "checks" &&
        candidate.id === checkRef.id &&
        candidate.sha256 === checkRef.sha256 &&
        candidate.sizeBytes === checkRef.sizeBytes &&
        candidate.metadata.format === "chapter-checks/1" &&
        candidate.metadata.editSha256 === editRef.sha256 &&
        candidate.metadata.sectionId === render.sectionId
    );
    const artifact = oneArtifact(matches, "technical check");
    const artifactStorageKey = proxyStorageKey(artifact);
    assertSourceKey(artifactStorageKey, sourceId, organizationId);
    if (artifactStorageKey !== checkRef.storageKey) {
      return fail("A technical-check reference names a different object.");
    }
    return { artifact, index, sectionId: render.sectionId };
  });
  if (
    new Set(checkInputs.map(({ artifact }) => artifact.id)).size !==
    checkInputs.length
  ) {
    return fail(
      "Accepted chapters must not reuse one technical-check artifact."
    );
  }

  const checked = await mapWithConcurrency(
    checkInputs,
    4,
    async ({ artifact, index, sectionId }) => ({
      index,
      sectionId,
      value: await load(artifact, ChapterChecksSchema),
    })
  );
  const warnings: string[] = [];
  for (const { index, sectionId, value } of checked) {
    if (
      value.editSha256 !== editRef.sha256 ||
      value.technicalChecks.some((check) => check.sectionId !== sectionId)
    ) {
      return fail("Technical checks do not match their accepted chapter.");
    }
    const eligibility = technicalEligibility(value);
    if (!eligibility.eligible) {
      return fail(
        "An accepted chapter has missing or failed technical checks."
      );
    }
    warnings.push(
      ...eligibility.warnings.map(
        (warning) => `${keeps[index]?.title ?? sectionId}: ${warning}`
      )
    );
  }

  return {
    chapters: descriptor.renders.map((render, index) => ({
      captions: render.captions
        ? linkFor(
            render.captions,
            sourceId,
            organizationId,
            `chapter-r${revision}-${String(index + 1).padStart(2, "0")}.vtt`,
            "render"
          )
        : null,
      sectionId: render.sectionId,
      title: keeps[index]?.title ?? `Chapter ${index + 1}`,
      video: linkFor(
        render.media,
        sourceId,
        organizationId,
        `chapter-r${revision}-${String(index + 1).padStart(2, "0")}.mp4`,
        "render"
      ),
    })),
    manifest: {
      download: `chapter-manifest-r${revision}.json`,
      href: mediaHref(exportStorageKey),
      sha256: exportRef.sha256,
      sizeBytes: exportRef.sizeBytes,
    },
    revision,
    warnings: [...new Set(warnings)],
  };
}
