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
import {
  TOPIC_POLICY,
  TOPIC_SELECTION_POLICY,
  TOPIC_SELECTION_POLICY_V3,
} from "./topic-defaults";

export interface ChapterArtifactRef {
  id: string;
  kind: "edit" | "render" | "checks" | "export" | "evidence" | "proposal";
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
    message?: string | null;
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
  summaryGrounding: {
    coverageFallbackWindowCount: number;
    fallbackQuoteCount: number;
    fallbackUnitCount: number;
    reports: ChapterArtifactRef[];
  };
}

const MAX_SUMMARY_GROUNDING_REPORTS = 128;

function groundingCount(
  metadata: Record<string, unknown>,
  key: "fallbackQuoteCount" | "fallbackUnitCount"
): number {
  const value = metadata[key];
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error(`Summary grounding report has invalid ${key}.`);
  }
  return Number(value);
}

function coverageFallbackWindowCount(
  metadata: Record<string, unknown>
): number {
  const value = metadata.coverageFallbackWindowCount;
  if (value === undefined) {
    return 0;
  }
  if (value !== 1) {
    throw new Error(
      "Summary grounding report has invalid coverageFallbackWindowCount."
    );
  }
  if (groundingCount(metadata, "fallbackUnitCount") < value) {
    throw new Error(
      "Summary grounding report has fewer fallback units than coverage fallback windows."
    );
  }
  return 1;
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

export function getTopicView(
  sourceId: string,
  selectedRunId?: string,
  pendingMutationKey?: string
): Promise<ChapterView> {
  return getHarnessView(sourceId, selectedRunId, pendingMutationKey);
}

/** Return scoped immutable references; the browser verifies bytes before editing. */
export function getTopicEditorialContext(
  sourceId: string,
  runId: string,
  revision: number
) {
  return scoped(async (tx) => {
    const [run] = await tx
      .select()
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.id, runId),
          eq(harnessRun.sourceId, sourceId),
          sql`${harnessRun.routeSnapshot}->>'editorialPolicy' IN (${TOPIC_POLICY}, ${TOPIC_SELECTION_POLICY}, ${TOPIC_SELECTION_POLICY_V3})`
        )
      )
      .limit(1);
    if (!run?.evidenceArtifactId) {
      return null;
    }
    const [base] = await tx
      .select()
      .from(chapterRevision)
      .where(
        and(
          eq(chapterRevision.runId, runId),
          eq(chapterRevision.sourceId, sourceId),
          eq(chapterRevision.revision, revision)
        )
      )
      .limit(1);
    if (!base) {
      return null;
    }
    const rows = await tx
      .select()
      .from(harnessArtifact)
      .where(
        and(
          eq(harnessArtifact.sourceId, sourceId),
          inArray(harnessArtifact.id, [base.artifactId, run.evidenceArtifactId])
        )
      );
    const reference = (
      id: string,
      kind: "edit" | "evidence"
    ): ChapterArtifactRef | null => {
      const row = rows.find((item) => item.id === id && item.kind === kind);
      return row
        ? {
            id: row.id,
            kind,
            metadata: row.metadata,
            sha256: row.sha256,
            sizeBytes: row.sizeBytes,
            url: `/api/media/${row.storageKey}`,
          }
        : null;
    };
    const edit = reference(base.artifactId, "edit");
    const evidence = reference(run.evidenceArtifactId, "evidence");
    return edit && evidence
      ? { currentRevision: run.currentRevision, edit, evidence }
      : null;
  });
}

function getHarnessView(
  sourceId: string,
  selectedRunId: string | undefined,
  pendingMutationKey: string | undefined
): Promise<ChapterView> {
  // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: one scoped snapshot keeps run pointers, bounded events, and exact immutable descriptors mutually consistent
  return scoped(async (tx) => {
    const policy = sql`${harnessRun.routeSnapshot}->>'editorialPolicy' IN (${TOPIC_POLICY}, ${TOPIC_SELECTION_POLICY}, ${TOPIC_SELECTION_POLICY_V3})`;
    const rows = await tx
      .select()
      .from(harnessRun)
      .where(
        and(
          eq(harnessRun.sourceId, sourceId),
          eq(harnessRun.lane, "chapters"),
          policy
        )
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
                eq(harnessRun.lane, "chapters"),
                policy
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
        summaryGrounding: {
          coverageFallbackWindowCount: 0,
          fallbackQuoteCount: 0,
          fallbackUnitCount: 0,
          reports: [],
        },
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
          message: sql<string | null>`${chapterReviewEvent.result}->>'message'`,
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
              message: sql<
                string | null
              >`${chapterReviewEvent.result}->>'message'`,
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
                      sql`${harnessArtifact.metadata}->>'format' = ${"topic-renders/1"}`,
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
    const groundingArtifacts = await tx
      .select()
      .from(harnessArtifact)
      .where(
        and(
          eq(harnessArtifact.sourceId, sourceId),
          eq(harnessArtifact.kind, "checks"),
          sql`${harnessArtifact.metadata}->>'format' = 'chapter-summary-grounding/1'`,
          sql`${harnessArtifact.metadata}->>'runId' = ${selected.id}`
        )
      )
      .orderBy(harnessArtifact.createdAt, harnessArtifact.id)
      .limit(MAX_SUMMARY_GROUNDING_REPORTS + 1);
    if (groundingArtifacts.length > MAX_SUMMARY_GROUNDING_REPORTS) {
      throw new Error(
        "Chapter run exceeds the summary grounding report limit."
      );
    }
    const topicAssessments = await tx
      .select()
      .from(harnessArtifact)
      .where(
        and(
          eq(harnessArtifact.sourceId, sourceId),
          sql`${harnessArtifact.metadata}->>'runId' = ${selected.id}`,
          sql`${harnessArtifact.metadata}->>'format' IN ('topic-assessment/1', 'topic-selection-assessment/2', 'topic-selection/2')`
        )
      );
    const artifacts = [
      ...editArtifacts,
      ...dependencies,
      ...groundingArtifacts,
      ...topicAssessments,
    ];
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
      summaryGrounding: {
        coverageFallbackWindowCount: groundingArtifacts.reduce(
          (total, artifact) =>
            total + coverageFallbackWindowCount(artifact.metadata),
          0
        ),
        fallbackQuoteCount: groundingArtifacts.reduce(
          (total, artifact) =>
            total + groundingCount(artifact.metadata, "fallbackQuoteCount"),
          0
        ),
        fallbackUnitCount: groundingArtifacts.reduce(
          (total, artifact) =>
            total + groundingCount(artifact.metadata, "fallbackUnitCount"),
          0
        ),
        reports: refs.filter(
          (artifact) =>
            artifact.kind === "checks" &&
            artifact.metadata.format === "chapter-summary-grounding/1" &&
            artifact.metadata.runId === selected.id
        ),
      },
    };
  });
}
