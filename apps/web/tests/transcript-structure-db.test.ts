import { randomUUID } from "node:crypto";
import {
  SEEDED_SCOPE,
  sourcePrefix,
  type TranscriptV1,
} from "@temnia/contracts";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { correctTranscriptStructure } from "@/app/actions/transcript";
import {
  discardRevision,
  writeRevisionArtifact,
} from "@/lib/transcript/queries";

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));

const ownerUrl = process.env.TEST_DATABASE_URL;
const suite = ownerUrl ? describe : describe.skip;
const CREDENTIALS = /\/\/[^@]+@/;
const owner = ownerUrl ? new pg.Client({ connectionString: ownerUrl }) : null;
const projectId = randomUUID();
const sourceId = randomUUID();
const transcriptId = randomUUID();
const initialKey = `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}transcript/rev-1-db-test.json`;
const writtenKeys = [initialKey];

const content: TranscriptV1 = {
  durationMs: 2000,
  language: "en",
  provider: { model: "fixture", name: "recorded", version: "1" },
  speakers: ["speaker-1"],
  utterances: [],
  version: 1,
  words: [
    {
      confidence: 0.9,
      endMs: 1000,
      speaker: "speaker-1",
      startMs: 0,
      text: "one",
      timing: "aligned",
    },
    {
      confidence: 0.9,
      endMs: 2000,
      speaker: "speaker-1",
      startMs: 1000,
      text: "two",
      timing: "aligned",
    },
  ],
};

suite("structural correction Postgres ownership", () => {
  beforeAll(async () => {
    if (!(owner && ownerUrl)) {
      return;
    }
    process.env.DATABASE_URL = ownerUrl.replace(
      CREDENTIALS,
      "//temnia_app:temnia_app@"
    );
    process.env.STORAGE_ACCESS_KEY_ID = "GK746d6e696164657600000000";
    process.env.STORAGE_BUCKET = "temnia-media";
    process.env.STORAGE_ENDPOINT = "http://127.0.0.1:56900";
    process.env.STORAGE_REGION = "garage";
    process.env.STORAGE_SECRET_ACCESS_KEY =
      "7f5fbe4a561d5196e4422e7fe9b8b8880846f9e153aacd3a142fd3d27f8f2bd2";
    await owner.connect();
    const initial = await writeRevisionArtifact(initialKey, content);
    await owner.query(
      `INSERT INTO project (id, organization_id, created_by, name)
       VALUES ($1, $2, $3, 'structural-db-test')`,
      [projectId, SEEDED_SCOPE.organizationId, SEEDED_SCOPE.userId]
    );
    await owner.query(
      `INSERT INTO source
         (id, organization_id, project_id, created_by, title, original_filename,
          content_type, size_bytes, master_key, status, duration_ms)
       VALUES ($1, $2, $3, $4, 'structural-db-test', 'fixture.mp4',
               'video/mp4', 1, $5, 'ready', 2000)`,
      [
        sourceId,
        SEEDED_SCOPE.organizationId,
        projectId,
        SEEDED_SCOPE.userId,
        `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}master.mp4`,
      ]
    );
    await owner.query(
      `INSERT INTO transcript
         (id, organization_id, source_id, status, current_revision, speaker_labels)
       VALUES ($1, $2, $3, 'ready', NULL, '{"speaker-1":"Alex"}'::jsonb)`,
      [transcriptId, SEEDED_SCOPE.organizationId, sourceId]
    );
    await owner.query(
      `INSERT INTO transcript_revision
         (organization_id, transcript_id, revision, kind, storage_key,
          size_bytes, word_count, metadata, created_by)
       VALUES ($1, $2, 1, 'machine', $3, $4, 2, $5::jsonb, $6)`,
      [
        SEEDED_SCOPE.organizationId,
        transcriptId,
        initialKey,
        initial.sizeBytes,
        JSON.stringify({ sha256: initial.sha256 }),
        SEEDED_SCOPE.userId,
      ]
    );
    await owner.query(
      "UPDATE transcript SET current_revision = 1 WHERE id = $1",
      [transcriptId]
    );
  });

  afterAll(async () => {
    if (!owner) {
      return;
    }
    const objects = await owner.query<{ storage_key: string }>(
      "SELECT storage_key FROM transcript_revision WHERE transcript_id = $1",
      [transcriptId]
    );
    writtenKeys.push(...objects.rows.map((row) => row.storage_key));
    await owner.query("DELETE FROM source WHERE id = $1", [sourceId]);
    await owner.query("DELETE FROM project WHERE id = $1", [projectId]);
    await owner.end();
    await Promise.all([...new Set(writtenKeys)].map(discardRevision));
  });

  it("deduplicates exact intent, rejects key reuse, serializes tabs, and honors the source fence", async () => {
    const mutationKey = randomUUID();
    const targetId = `${transcriptId}:1:word:0`;
    const command = {
      action: "replace" as const,
      baseRevision: 1,
      mutationKey,
      targetId,
      text: "ONE",
    };
    await expect(
      correctTranscriptStructure(sourceId, command)
    ).resolves.toEqual({ ok: true, revision: 2 });
    await expect(
      correctTranscriptStructure(sourceId, command)
    ).resolves.toEqual({ ok: true, revision: 2 });
    await expect(
      correctTranscriptStructure(sourceId, { ...command, text: "different" })
    ).resolves.toMatchObject({ invalid: true, ok: false });
    const count = await owner?.query<{ count: string }>(
      "SELECT COUNT(*)::text AS count FROM transcript_revision WHERE transcript_id = $1 AND revision = 2",
      [transcriptId]
    );
    expect(count?.rows[0]?.count).toBe("1");

    const ids = [randomUUID(), randomUUID()];
    const concurrent = await Promise.all(
      ids.map((key, index) =>
        correctTranscriptStructure(sourceId, {
          action: "replace",
          baseRevision: 2,
          mutationKey: key,
          targetId,
          text: `winner-${index}`,
        })
      )
    );
    expect(concurrent.filter((result) => result.ok)).toHaveLength(1);
    expect(
      concurrent.filter((result) => !result.ok && result.stale)
    ).toHaveLength(1);

    await owner?.query(
      "UPDATE source SET deletion_requested_at = now() WHERE id = $1",
      [sourceId]
    );
    await expect(
      correctTranscriptStructure(sourceId, {
        action: "replace",
        baseRevision: 3,
        mutationKey: randomUUID(),
        targetId,
        text: "blocked",
      })
    ).resolves.toEqual({
      message: "This source is pending deletion.",
      ok: false,
    });
  });
});
