import { createHash } from "node:crypto";
import {
  DeleteObjectCommand,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import {
  type TranscriptRevisionAnnotations,
  TranscriptRevisionAnnotationsSchema,
  type TranscriptV1,
  TranscriptV1Schema,
} from "@temnia/contracts";
import { source, transcript, transcriptRevision } from "@temnia/db";
import { and, desc, eq, lte } from "drizzle-orm";
import { scoped } from "@/lib/db";
import { storage, storageSettings } from "@/lib/storage/client";

export type TranscriptRow = typeof transcript.$inferSelect;
export type TranscriptRevisionRow = typeof transcriptRevision.$inferSelect;

export interface TranscriptState {
  current: TranscriptRevisionRow | undefined;
  revisions: TranscriptRevisionRow[];
  row: TranscriptRow;
  sourceTitle: string;
}

/**
 * The transcript for a source, scoped by the resolver.
 *
 * Undefined covers both "no such source in this organization" and "no
 * transcript yet"; a caller that needs to tell them apart asks for the source
 * separately. Nothing here takes an organization id from input.
 */
export function getTranscript(
  sourceId: string
): Promise<TranscriptState | undefined> {
  return scoped(async (tx) => {
    const [found] = await tx
      .select({ title: source.title, transcript })
      .from(source)
      .leftJoin(transcript, eq(transcript.sourceId, source.id))
      .where(eq(source.id, sourceId))
      .limit(1);
    if (!found?.transcript) {
      return;
    }
    const row = found.transcript;
    const [current] = row.currentRevision
      ? await tx
          .select()
          .from(transcriptRevision)
          .where(
            and(
              eq(transcriptRevision.transcriptId, row.id),
              eq(transcriptRevision.revision, row.currentRevision)
            )
          )
          .limit(1)
      : [];
    const [recentRevisions, machineRows] = await Promise.all([
      tx
        .select()
        .from(transcriptRevision)
        .where(eq(transcriptRevision.transcriptId, row.id))
        .orderBy(desc(transcriptRevision.revision))
        .limit(100),
      row.currentRevision
        ? tx
            .select()
            .from(transcriptRevision)
            .where(
              and(
                eq(transcriptRevision.transcriptId, row.id),
                eq(transcriptRevision.kind, "machine"),
                lte(transcriptRevision.revision, row.currentRevision)
              )
            )
            .orderBy(desc(transcriptRevision.revision))
            .limit(1)
        : Promise.resolve([]),
    ]);
    const [machine] = machineRows;
    const revisions =
      machine &&
      !recentRevisions.some(
        (revision) => revision.revision === machine.revision
      )
        ? [...recentRevisions, machine]
        : recentRevisions;
    return { current, revisions, row, sourceTitle: found.title };
  });
}

export interface TranscriptAnnotationState {
  annotations: TranscriptRevisionAnnotations | null;
  legacySpeakerLabels: Record<string, string>;
  machineRevision: number;
  transcriptId: string;
}

/** Bounded metadata read for one explicitly named, organization-scoped revision. */
export function getTranscriptRevisionAnnotations(
  sourceId: string,
  revision: number
): Promise<TranscriptAnnotationState | undefined> {
  return scoped(async (tx) => {
    const [found] = await tx
      .select({
        metadata: transcriptRevision.metadata,
        speakerLabels: transcript.speakerLabels,
        transcriptId: transcript.id,
      })
      .from(source)
      .innerJoin(transcript, eq(transcript.sourceId, source.id))
      .innerJoin(
        transcriptRevision,
        and(
          eq(transcriptRevision.transcriptId, transcript.id),
          eq(transcriptRevision.revision, revision)
        )
      )
      .where(eq(source.id, sourceId))
      .limit(1);
    if (!found) {
      return;
    }
    const [machine] = await tx
      .select({ revision: transcriptRevision.revision })
      .from(transcriptRevision)
      .where(
        and(
          eq(transcriptRevision.transcriptId, found.transcriptId),
          eq(transcriptRevision.kind, "machine"),
          lte(transcriptRevision.revision, revision)
        )
      )
      .orderBy(desc(transcriptRevision.revision))
      .limit(1);
    if (!machine) {
      return;
    }
    const parsed = TranscriptRevisionAnnotationsSchema.safeParse(
      found.metadata.annotations
    );
    return {
      annotations: parsed.success ? parsed.data : null,
      legacySpeakerLabels: found.speakerLabels,
      machineRevision: machine.revision,
      transcriptId: found.transcriptId,
    };
  });
}

/** Read one revision's JSON out of storage and validate it against the contract. */
export async function readRevision(storageKey: string): Promise<TranscriptV1> {
  const out = await storage().send(
    new GetObjectCommand({ Bucket: storageSettings().bucket, Key: storageKey })
  );
  const body = await out.Body?.transformToString();
  if (!body) {
    throw new Error(`transcript revision ${storageKey} is empty`);
  }
  return TranscriptV1Schema.parse(JSON.parse(body));
}

/**
 * Write a revision object and return its size.
 *
 * Validated on the way out as well as on the way in: a correction that
 * produced words out of order, or one that pushed a word past the end of the
 * recording, must never reach storage where the pipeline and the exports would
 * both trust it.
 */
export async function writeRevision(
  storageKey: string,
  content: TranscriptV1
): Promise<number> {
  const body = JSON.stringify(TranscriptV1Schema.parse(content));
  await storage().send(
    new PutObjectCommand({
      Body: body,
      Bucket: storageSettings().bucket,
      ContentType: "application/json",
      Key: storageKey,
    })
  );
  return Buffer.byteLength(body);
}

export async function writeRevisionArtifact(
  storageKey: string,
  content: TranscriptV1
): Promise<{ sha256: string; sizeBytes: number }> {
  const body = JSON.stringify(TranscriptV1Schema.parse(content));
  await storage().send(
    new PutObjectCommand({
      Body: body,
      Bucket: storageSettings().bucket,
      ContentType: "application/json",
      Key: storageKey,
    })
  );
  return {
    sha256: createHash("sha256").update(body).digest("hex"),
    sizeBytes: Buffer.byteLength(body),
  };
}

/**
 * Remove a revision object nothing points at: the loser of a concurrent
 * save. Best effort; an orphan that survives is harmless and is collected
 * later, and the save's answer must not depend on this succeeding.
 */
export async function discardRevision(storageKey: string): Promise<void> {
  try {
    await storage().send(
      new DeleteObjectCommand({
        Bucket: storageSettings().bucket,
        Key: storageKey,
      })
    );
  } catch {
    // Left for the collector.
  }
}
