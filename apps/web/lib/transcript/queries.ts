import { GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";
import { type TranscriptV1, TranscriptV1Schema } from "@temnia/contracts";
import { source, transcript, transcriptRevision } from "@temnia/db";
import { and, eq } from "drizzle-orm";
import { scoped } from "@/lib/db";
import { storage, storageSettings } from "@/lib/storage/client";

export type TranscriptRow = typeof transcript.$inferSelect;
export type TranscriptRevisionRow = typeof transcriptRevision.$inferSelect;

export interface TranscriptState {
  current: TranscriptRevisionRow | undefined;
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
    return { current, row, sourceTitle: found.title };
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
