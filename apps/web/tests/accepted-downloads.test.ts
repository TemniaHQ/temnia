import { describe, expect, it } from "vitest";
import type { ZodType } from "zod";
import {
  type ArtifactJsonLoader,
  acceptedDownloadsErrorMessage,
  acceptedDownloadsIdentity,
  loadAcceptedDownloads,
} from "@/lib/harness/accepted-downloads";
import type { ChapterArtifactRef, ChapterView } from "@/lib/harness/queries";

const SOURCE = "0192e8a0-0000-7000-8000-000000000010";
const OTHER_SOURCE = "0192e8a0-0000-7000-8000-000000000011";
const ORG = "0192e8a0-0000-7000-8000-000000000001";
const OTHER_ORG = "0192e8a0-0000-7000-8000-000000000002";
const RUN = "0192e8a0-0000-7000-8000-000000000020";
const EDIT_ID = "0192e8a0-0000-7000-8000-000000000021";
const DESCRIPTOR_ID = "0192e8a0-0000-7000-8000-000000000022";
const EXPORT_ID = "0192e8a0-0000-7000-8000-000000000023";
const CHECK_ID = "0192e8a0-0000-7000-8000-000000000024";
const MEDIA_ID = "0192e8a0-0000-7000-8000-000000000025";
const CAPTION_ID = "0192e8a0-0000-7000-8000-000000000026";
const EVIDENCE_ID = "0192e8a0-0000-7000-8000-000000000027";
const EDIT_SHA = "a".repeat(64);

const sha = (character: string) => character.repeat(64);
const key = (name: string, sourceId = SOURCE) =>
  `org/${ORG}/source/${sourceId}/harness/${name}`;

function artifact(
  id: string,
  kind: ChapterArtifactRef["kind"],
  sha256: string,
  storageKey: string,
  metadata: Record<string, unknown>,
  sizeBytes = 100
): ChapterArtifactRef {
  return {
    id,
    kind,
    metadata,
    sha256,
    sizeBytes,
    url: `/api/media/${storageKey}`,
  };
}

function reference(
  id: string,
  kind: "checks" | "render",
  sha256: string,
  storageKey: string,
  sizeBytes = 100
) {
  return {
    fingerprint: sha("f"),
    id,
    kind,
    sha256,
    sizeBytes,
    storageKey,
  };
}

const requiredCheckNames = [
  "full_decode",
  "duration",
  "video_presence",
  "audio_presence",
  "caption_bounds",
] as const;

interface Fixture {
  bodies: Record<string, unknown>;
  sourceId: string;
  view: ChapterView;
}

function fixture(): Fixture {
  const descriptorKey = key("chapter-renders.json");
  const media = reference(
    MEDIA_ID,
    "render",
    sha("b"),
    key("chapter-1.mp4"),
    1234
  );
  const captions = reference(
    CAPTION_ID,
    "render",
    sha("c"),
    key("chapter-1.vtt"),
    234
  );
  const checks = reference(
    CHECK_ID,
    "checks",
    sha("d"),
    key("chapter-1-checks.json")
  );
  const render = {
    captions,
    checks,
    durationMs: 500,
    editSha256: EDIT_SHA,
    media,
    sectionId: "keep-1",
  };
  const edit = {
    boundaries: [
      {
        candidateId: null,
        id: "boundary-0",
        reasons: [],
        requiresReview: false,
        time: { denominator: 1, numerator: 0 },
        timeMs: 0,
      },
      {
        candidateId: null,
        id: "boundary-1",
        reasons: [],
        requiresReview: false,
        time: { denominator: 2, numerator: 1 },
        timeMs: 500,
      },
      {
        candidateId: null,
        id: "boundary-2",
        reasons: [],
        requiresReview: false,
        time: { denominator: 1, numerator: 1 },
        timeMs: 1000,
      },
    ],
    compilerVersion: "test-v1",
    durationMs: 1000,
    evidenceArtifactId: EVIDENCE_ID,
    evidenceSha256: sha("e"),
    sections: [
      {
        endBoundaryId: "boundary-1",
        flags: [],
        id: "keep-1",
        kind: "keep",
        quoteWordIds: ["word-1"],
        reason: "keep",
        reviewState: "accepted",
        startBoundaryId: "boundary-0",
        title: "Opening / unsafe title",
      },
      {
        endBoundaryId: "boundary-2",
        flags: [],
        id: "drop-1",
        kind: "drop",
        quoteWordIds: [],
        reason: "drop",
        reviewState: "accepted",
        startBoundaryId: "boundary-1",
        title: "Drop",
      },
    ],
    sourceAudioSampleRate: null,
    sourceFrameRate: null,
    sourceId: SOURCE,
    version: 1,
  };
  const descriptor = {
    editSha256: EDIT_SHA,
    format: "chapter-renders/1",
    renders: [render],
    runId: RUN,
  };
  const manifest = {
    chapters: [render],
    createdAt: "2026-09-09T00:00:00Z",
    editSha256: EDIT_SHA,
    manifestKey: descriptorKey,
    revision: 3,
    runId: RUN,
    version: 1,
  };
  const checkBody = {
    editorialReasons: [],
    editorialStatus: "not_run",
    editSha256: EDIT_SHA,
    technicalChecks: requiredCheckNames.map((name) => ({
      expected: name.endsWith("presence") ? 0 : 1,
      measured: name.endsWith("presence") ? 0 : 1,
      message: "ok",
      name,
      sectionId: "keep-1",
      status: "pass",
    })),
    verifierFamily: null,
    version: 1,
  };
  const editRef = artifact(EDIT_ID, "edit", EDIT_SHA, key("edit.json"), {});
  const descriptorRef = artifact(
    DESCRIPTOR_ID,
    "render",
    sha("1"),
    descriptorKey,
    {
      editSha256: EDIT_SHA,
      format: "chapter-renders/1",
      renderCount: 1,
      runId: RUN,
    }
  );
  const exportRef = artifact(
    EXPORT_ID,
    "export",
    sha("2"),
    key("export.json"),
    {
      editSha256: EDIT_SHA,
      format: "chapter-export/1",
      revision: 3,
      runId: RUN,
    }
  );
  const checkRef = artifact(CHECK_ID, "checks", sha("d"), checks.storageKey, {
    editSha256: EDIT_SHA,
    format: "chapter-checks/1",
    sectionId: "keep-1",
  });
  return {
    bodies: {
      [CHECK_ID]: checkBody,
      [DESCRIPTOR_ID]: descriptor,
      [EDIT_ID]: edit,
      [EXPORT_ID]: manifest,
    },
    sourceId: SOURCE,
    view: {
      acceptedEdit: editRef,
      artifacts: [editRef, descriptorRef, exportRef, checkRef],
      currentEdit: editRef,
      events: [],
      run: {
        acceptedRevision: 3,
        brief: "brief",
        budgetMicros: 1_000_000,
        createdAt: "2026-09-09T00:00:00Z",
        currentRevision: 3,
        currentTranscriptRevision: 1,
        dispatchCount: 1,
        errorMessage: null,
        evidenceTranscriptRevision: 1,
        id: RUN,
        reservedMicros: 0,
        spentMicros: 0,
        stage: "ready",
        status: "ready",
        synthetic: true,
      },
      runs: [{ createdAt: "2026-09-09T00:00:00Z", id: RUN, status: "ready" }],
      summaryGrounding: {
        coverageFallbackWindowCount: 0,
        fallbackQuoteCount: 0,
        fallbackUnitCount: 0,
        reports: [],
      },
    },
  };
}

function loader(value: Fixture): ArtifactJsonLoader {
  return async <T>(artifactRef: ChapterArtifactRef, schema: ZodType<T>) =>
    schema.parse(value.bodies[artifactRef.id]);
}

describe("accepted chapter downloads", () => {
  it("builds same-origin ordinal downloads from the exact accepted graph", async () => {
    const value = fixture();
    const result = await loadAcceptedDownloads(value, loader(value));
    expect(result).toMatchObject({
      chapters: [
        {
          captions: {
            download: "chapter-r3-01.vtt",
            href: `/api/media/${key("chapter-1.vtt")}`,
          },
          sectionId: "keep-1",
          title: "Opening / unsafe title",
          video: {
            download: "chapter-r3-01.mp4",
            href: `/api/media/${key("chapter-1.mp4")}`,
          },
        },
      ],
      manifest: {
        download: "chapter-manifest-r3.json",
        href: `/api/media/${key("export.json")}`,
      },
      revision: 3,
      warnings: [],
    });
    expect(result.chapters[0]?.video.download).not.toContain("unsafe");
  });

  it("accepts an explicitly reviewed all-drop edit without file links", async () => {
    const value = fixture();
    const edit = value.bodies[EDIT_ID] as {
      sections: Record<string, unknown>[];
    };
    edit.sections[0] = { ...edit.sections[0], kind: "drop" };
    (value.bodies[DESCRIPTOR_ID] as { renders: unknown[] }).renders = [];
    (value.bodies[EXPORT_ID] as { chapters: unknown[] }).chapters = [];
    const descriptor = value.view.artifacts.find(
      (item) => item.id === DESCRIPTOR_ID
    );
    if (descriptor) {
      descriptor.metadata.renderCount = 0;
    }
    value.view.artifacts = value.view.artifacts.filter(
      (item) => item.id !== CHECK_ID
    );
    await expect(
      loadAcceptedDownloads(value, loader(value))
    ).resolves.toMatchObject({
      chapters: [],
      revision: 3,
    });
  });

  it.each([
    [
      "wrong accepted-edit kind",
      (value: Fixture) => {
        if (value.view.acceptedEdit) {
          value.view.acceptedEdit.kind = "render";
        }
      },
    ],
    [
      "external accepted-edit URL",
      (value: Fixture) => {
        if (value.view.acceptedEdit) {
          value.view.acceptedEdit.url = "https://example.invalid/edit.json";
        }
      },
    ],
    [
      "wrong source",
      (value: Fixture) => {
        (value.bodies[EDIT_ID] as { sourceId: string }).sourceId = OTHER_SOURCE;
      },
    ],
    [
      "wrong run",
      (value: Fixture) => {
        (value.bodies[DESCRIPTOR_ID] as { runId: string }).runId =
          "0192e8a0-0000-7000-8000-000000000099";
      },
    ],
    [
      "wrong revision",
      (value: Fixture) => {
        (value.bodies[EXPORT_ID] as { revision: number }).revision = 2;
      },
    ],
    [
      "wrong edit hash",
      (value: Fixture) => {
        (value.bodies[EXPORT_ID] as { editSha256: string }).editSha256 =
          sha("9");
      },
    ],
    [
      "missing render",
      (value: Fixture) => {
        (value.bodies[DESCRIPTOR_ID] as { renders: unknown[] }).renders = [];
      },
    ],
    [
      "duplicate render",
      (value: Fixture) => {
        const descriptor = value.bodies[DESCRIPTOR_ID] as {
          renders: unknown[];
        };
        descriptor.renders.push(structuredClone(descriptor.renders[0]));
        (
          value.view.artifacts.find((item) => item.id === DESCRIPTOR_ID)
            ?.metadata ?? {}
        ).renderCount = 2;
        (value.bodies[EXPORT_ID] as { chapters: unknown[] }).chapters =
          structuredClone(descriptor.renders);
      },
    ],
    [
      "render for a drop",
      (value: Fixture) => {
        const edit = value.bodies[EDIT_ID] as {
          sections: Record<string, unknown>[];
        };
        edit.sections[0] = { ...edit.sections[0], kind: "drop" };
      },
    ],
    [
      "section without acceptance",
      (value: Fixture) => {
        const edit = value.bodies[EDIT_ID] as {
          sections: Record<string, unknown>[];
        };
        edit.sections[0] = { ...edit.sections[0], reviewState: "proposed" };
      },
    ],
    [
      "cross-source media",
      (value: Fixture) => {
        const descriptor = value.bodies[DESCRIPTOR_ID] as {
          renders: { media: { storageKey: string } }[];
        };
        const [firstRender] = descriptor.renders;
        if (!firstRender) {
          throw new Error("fixture has no render");
        }
        firstRender.media.storageKey = key("chapter-1.mp4", OTHER_SOURCE);
        (
          value.bodies[EXPORT_ID] as { chapters: typeof descriptor.renders }
        ).chapters = structuredClone(descriptor.renders);
      },
    ],
    [
      "cross-organization media",
      (value: Fixture) => {
        const descriptor = value.bodies[DESCRIPTOR_ID] as {
          renders: { media: { storageKey: string } }[];
        };
        const [firstRender] = descriptor.renders;
        if (!firstRender) {
          throw new Error("fixture has no render");
        }
        firstRender.media.storageKey = key("chapter-1.mp4").replace(
          ORG,
          OTHER_ORG
        );
        (
          value.bodies[EXPORT_ID] as { chapters: typeof descriptor.renders }
        ).chapters = structuredClone(descriptor.renders);
      },
    ],
    [
      "encoded traversal",
      (value: Fixture) => {
        const descriptor = value.bodies[DESCRIPTOR_ID] as {
          renders: { media: { storageKey: string } }[];
        };
        const [firstRender] = descriptor.renders;
        if (!firstRender) {
          throw new Error("fixture has no render");
        }
        firstRender.media.storageKey = `${key("files")}/%252e%252e/video.mp4`;
        (
          value.bodies[EXPORT_ID] as { chapters: typeof descriptor.renders }
        ).chapters = structuredClone(descriptor.renders);
      },
    ],
    [
      "missing checks",
      (value: Fixture) => {
        value.view.artifacts = value.view.artifacts.filter(
          (item) => item.id !== CHECK_ID
        );
      },
    ],
    [
      "check URL different from descriptor key",
      (value: Fixture) => {
        const checkArtifact = value.view.artifacts.find(
          (item) => item.id === CHECK_ID
        );
        if (checkArtifact) {
          checkArtifact.url = `/api/media/${key("other-checks.json")}`;
        }
      },
    ],
    [
      "failed checks",
      (value: Fixture) => {
        const checks = value.bodies[CHECK_ID] as {
          technicalChecks: { status: string }[];
        };
        const [firstCheck] = checks.technicalChecks;
        if (!firstCheck) {
          throw new Error("fixture has no technical check");
        }
        firstCheck.status = "fail";
      },
    ],
    [
      "malformed checks",
      (value: Fixture) => {
        value.bodies[CHECK_ID] = { version: 1 };
      },
    ],
  ])("refuses %s", async (_name, mutate) => {
    const value = fixture();
    mutate(value);
    await expect(loadAcceptedDownloads(value, loader(value))).rejects.toThrow();
  });

  it("retries only artifact reads after a transient failure", async () => {
    const value = fixture();
    let failed = false;
    const recovering: ArtifactJsonLoader = <T>(
      artifactRef: ChapterArtifactRef,
      schema: ZodType<T>
    ) => {
      if (artifactRef.id === EXPORT_ID && !failed) {
        failed = true;
        return Promise.reject(new Error("temporary read failure"));
      }
      return Promise.resolve(schema.parse(value.bodies[artifactRef.id]));
    };
    await expect(loadAcceptedDownloads(value, recovering)).rejects.toThrow(
      "temporary read failure"
    );
    await expect(
      loadAcceptedDownloads(value, recovering)
    ).resolves.toMatchObject({
      revision: 3,
    });
  });

  it("shows controlled validation messages without exposing schema details", async () => {
    const value = fixture();
    value.bodies[CHECK_ID] = { privateDebugValue: "raw schema detail" };
    let caught: unknown;
    try {
      await loadAcceptedDownloads(value, loader(value));
    } catch (error) {
      caught = error;
    }
    expect(acceptedDownloadsErrorMessage(caught)).toBe(
      "The accepted files could not be verified. Check your connection and retry."
    );
    expect(acceptedDownloadsErrorMessage(caught)).not.toContain(
      "privateDebugValue"
    );
  });

  it("shows generic copy for unexpected transport errors and domain copy for refusals", async () => {
    expect(acceptedDownloadsErrorMessage(new Error("credential=secret"))).toBe(
      "The accepted files could not be verified. Check your connection and retry."
    );
    const value = fixture();
    if (value.view.acceptedEdit) {
      value.view.acceptedEdit.kind = "render";
    }
    let caught: unknown;
    try {
      await loadAcceptedDownloads(value, loader(value));
    } catch (error) {
      caught = error;
    }
    expect(acceptedDownloadsErrorMessage(caught)).toBe(
      "The accepted edit pointer has the wrong artifact kind."
    );
  });

  it("changes identity across selected runs and artifact pointers", () => {
    const first = fixture();
    const identity = acceptedDownloadsIdentity(first);
    const changedRun = fixture();
    if (changedRun.view.run) {
      changedRun.view.run.id = "0192e8a0-0000-7000-8000-000000000098";
    }
    const changedArtifact = fixture();
    if (changedArtifact.view.acceptedEdit) {
      changedArtifact.view.acceptedEdit.sha256 = sha("8");
    }
    expect(identity).not.toBeNull();
    expect(acceptedDownloadsIdentity(changedRun)).not.toBe(identity);
    expect(acceptedDownloadsIdentity(changedArtifact)).not.toBe(identity);
    expect(
      acceptedDownloadsIdentity({
        sourceId: SOURCE,
        view: { ...fixture().view, acceptedEdit: null },
      })
    ).toBeNull();
  });
});
