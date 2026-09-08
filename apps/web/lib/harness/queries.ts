import {
  chapterReviewEvent,
  chapterRevision,
  harnessArtifact,
  harnessArtifactDependency,
  harnessRun,
  transcript,
} from "@temnia/db";
import { and, desc, eq, inArray, sql } from "drizzle-orm";
import { scoped } from "@/lib/db";

export interface ChapterArtifactRef {
  id: string;
  kind: "edit" | "render" | "checks" | "export";
  metadata: Record<string, unknown>;
  sha256: string;
  sizeBytes: number;
  url: string;
}

export interface ChapterView {
  acceptedEdit: ChapterArtifactRef | null;
  artifacts: ChapterArtifactRef[];
  currentEdit: ChapterArtifactRef | null;
  events: Array<{
    action: string;
    baseRevision: number;
    createdAt: string;
    mutationKey: string;
    resultingRevision: number | null;
    state: string;
  }>;
  run: {
    acceptedRevision: number | null;
    brief: string;
    budgetMicros: number;
    createdAt: string;
    currentRevision: number;
    dispatchCount: number;
    errorMessage: string | null;
    evidenceTranscriptRevision: number | null;
    id: string;
    reservedMicros: number;
    spentMicros: number;
    stage: string | null;
    status: string;
    synthetic: boolean;
    currentTranscriptRevision: number | null;
  } | null;
  runs: Array<{ createdAt: string; id: string; status: string }>;
}

export function artifactIdsForRevisionPointers(
  rows: ReadonlyArray<{ artifactId: string; revision: number }>,
  currentRevision: number,
  acceptedRevision: number | null
): { accepted: string | null; current: string | null } {
  const find = (revision: number | null) =>
    rows.find((row) => row.revision === revision)?.artifactId ?? null;
  return {
    accepted: find(acceptedRevision),
    current: find(currentRevision),
  };
}

export function getChapterView(
  sourceId: string,
  selectedRunId?: string,
  pendingMutationKey?: string
): Promise<ChapterView> {
  // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: one scoped snapshot keeps run pointers, bounded events, and exact immutable descriptors mutually consistent
  return scoped(async (tx) => {
    const rows = await tx
      .select()
      .from(harnessRun)
      .where(
        and(eq(harnessRun.sourceId, sourceId), eq(harnessRun.lane, "chapters"))
      )
      .orderBy(desc(harnessRun.createdAt))
      .limit(20);
    const selectedFromLatest = selectedRunId
      ? rows.find((row) => row.id === selectedRunId)
      : rows[0];
    const [selectedExact] =
      selectedRunId && !selectedFromLatest
        ? await tx
            .select()
            .from(harnessRun)
            .where(
              and(
                eq(harnessRun.id, selectedRunId),
                eq(harnessRun.sourceId, sourceId),
                eq(harnessRun.lane, "chapters")
              )
            )
            .limit(1)
        : [];
    const selected = selectedFromLatest ?? selectedExact ?? null;
    const visibleRows =
      selectedExact && !rows.some((row) => row.id === selectedExact.id)
        ? [...rows, selectedExact]
        : rows;
    if (!selected) {
      return {
        acceptedEdit: null,
        artifacts: [],
        currentEdit: null,
        events: [],
        run: null,
        runs: visibleRows.map((row) => ({
          createdAt: row.createdAt.toISOString(),
          id: row.id,
          status: row.status,
        })),
      };
    }
    const [revisionRows, recentEvents] = await Promise.all([
      tx
        .select({
          artifactId: chapterRevision.artifactId,
          revision: chapterRevision.revision,
        })
        .from(chapterRevision)
        .where(
          and(
            eq(chapterRevision.runId, selected.id),
            inArray(
              chapterRevision.revision,
              [selected.currentRevision, selected.acceptedRevision].filter(
                (revision): revision is number =>
                  revision !== null && revision > 0
              )
            )
          )
        ),
      tx
        .select({
          action: chapterReviewEvent.action,
          baseRevision: chapterReviewEvent.baseRevision,
          createdAt: chapterReviewEvent.createdAt,
          mutationKey: chapterReviewEvent.mutationKey,
          resultingRevision: chapterReviewEvent.resultingRevision,
          state: chapterReviewEvent.state,
        })
        .from(chapterReviewEvent)
        .where(eq(chapterReviewEvent.runId, selected.id))
        .orderBy(desc(chapterReviewEvent.createdAt))
        .limit(50),
    ]);
    const [pendingEvent] =
      pendingMutationKey &&
      !recentEvents.some((event) => event.mutationKey === pendingMutationKey)
        ? await tx
            .select({
              action: chapterReviewEvent.action,
              baseRevision: chapterReviewEvent.baseRevision,
              createdAt: chapterReviewEvent.createdAt,
              mutationKey: chapterReviewEvent.mutationKey,
              resultingRevision: chapterReviewEvent.resultingRevision,
              state: chapterReviewEvent.state,
            })
            .from(chapterReviewEvent)
            .where(
              and(
                eq(chapterReviewEvent.runId, selected.id),
                eq(chapterReviewEvent.mutationKey, pendingMutationKey)
              )
            )
            .limit(1)
        : [];
    const events = pendingEvent
      ? [pendingEvent, ...recentEvents]
      : recentEvents;
    const revisionArtifactIds = revisionRows.map((row) => row.artifactId);
    const editArtifacts =
      revisionArtifactIds.length === 0
        ? []
        : await tx
            .select()
            .from(harnessArtifact)
            .where(
              and(
                eq(harnessArtifact.sourceId, sourceId),
                eq(harnessArtifact.kind, "edit"),
                inArray(harnessArtifact.id, revisionArtifactIds)
              )
            );
    const editHashes = [
      ...new Set(editArtifacts.map((artifact) => artifact.sha256)),
    ];
    const descriptorAndExportRows = await Promise.all(
      editHashes.flatMap((editSha256) =>
        (["render", "export"] as const).map(async (kind) => {
          const [artifact] = await tx
            .select()
            .from(harnessArtifact)
            .where(
              and(
                eq(harnessArtifact.sourceId, sourceId),
                eq(harnessArtifact.kind, kind),
                sql`${harnessArtifact.metadata}->>'runId' = ${selected.id}`,
                sql`${harnessArtifact.metadata}->>'editSha256' = ${editSha256}`,
                ...(kind === "render"
                  ? [
                      sql`${harnessArtifact.metadata}->>'format' = 'chapter-renders/1'`,
                      sql`${harnessArtifact.metadata} ? 'renderCount'`,
                    ]
                  : [])
              )
            )
            .orderBy(desc(harnessArtifact.createdAt))
            .limit(1);
          return artifact;
        })
      )
    );
    const descriptorAndExportArtifacts = descriptorAndExportRows.filter(
      (artifact): artifact is NonNullable<typeof artifact> => Boolean(artifact)
    );
    const descriptorIds = descriptorAndExportArtifacts
      .filter((artifact) => artifact.kind === "render")
      .map((artifact) => artifact.id);
    const dependencyRows =
      descriptorIds.length === 0
        ? []
        : await tx
            .select({ artifact: harnessArtifact })
            .from(harnessArtifactDependency)
            .innerJoin(
              harnessArtifact,
              and(
                eq(
                  harnessArtifact.id,
                  harnessArtifactDependency.inputArtifactId
                ),
                eq(
                  harnessArtifact.sourceId,
                  harnessArtifactDependency.sourceId
                ),
                eq(
                  harnessArtifact.organizationId,
                  harnessArtifactDependency.organizationId
                )
              )
            )
            .where(
              and(
                eq(harnessArtifactDependency.sourceId, sourceId),
                inArray(harnessArtifactDependency.artifactId, descriptorIds),
                eq(harnessArtifact.kind, "checks")
              )
            );
    const dependencies = [
      ...descriptorAndExportArtifacts,
      ...dependencyRows.map((row) => row.artifact),
    ];
    const [evidenceArtifact] = selected.evidenceArtifactId
      ? await tx
          .select({ transcriptRevision: harnessArtifact.transcriptRevision })
          .from(harnessArtifact)
          .where(
            and(
              eq(harnessArtifact.id, selected.evidenceArtifactId),
              eq(harnessArtifact.sourceId, sourceId),
              eq(harnessArtifact.kind, "evidence")
            )
          )
          .limit(1)
      : [];
    const [sourceTranscript] = await tx
      .select({ currentRevision: transcript.currentRevision })
      .from(transcript)
      .where(eq(transcript.sourceId, sourceId))
      .limit(1);
    const artifacts = [...editArtifacts, ...dependencies];
    const refs = artifacts.map((artifact) => ({
      id: artifact.id,
      kind: artifact.kind as ChapterArtifactRef["kind"],
      metadata: artifact.metadata,
      sha256: artifact.sha256,
      sizeBytes: artifact.sizeBytes,
      url: `/api/media/${artifact.storageKey}`,
    }));
    const pointers = artifactIdsForRevisionPointers(
      revisionRows,
      selected.currentRevision,
      selected.acceptedRevision
    );
    return {
      acceptedEdit:
        refs.find((artifact) => artifact.id === pointers.accepted) ?? null,
      artifacts: refs,
      currentEdit:
        refs.find((artifact) => artifact.id === pointers.current) ?? null,
      events: events.map((event) => ({
        ...event,
        createdAt: event.createdAt.toISOString(),
      })),
      run: {
        acceptedRevision: selected.acceptedRevision,
        brief: selected.brief,
        budgetMicros: selected.budgetMicros,
        createdAt: selected.createdAt.toISOString(),
        currentRevision: selected.currentRevision,
        currentTranscriptRevision: sourceTranscript?.currentRevision ?? null,
        dispatchCount: selected.dispatchCount,
        errorMessage: selected.errorMessage,
        evidenceTranscriptRevision:
          evidenceArtifact?.transcriptRevision ?? null,
        id: selected.id,
        reservedMicros: selected.reservedMicros,
        spentMicros: selected.spentMicros,
        stage: selected.stage,
        status: selected.status,
        synthetic: selected.config.backend === "recorded",
      },
      runs: visibleRows.map((row) => ({
        createdAt: row.createdAt.toISOString(),
        id: row.id,
        status: row.status,
      })),
    };
  });
}
