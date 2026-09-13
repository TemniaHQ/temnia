import { randomUUID } from "node:crypto";
import { SEEDED_SCOPE, sourcePrefix } from "@temnia/contracts";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { getTopicView } from "@/lib/harness/queries";

const ownerUrl = process.env.TEST_DATABASE_URL;
if (process.env.LOCAL_CI === "1" && !ownerUrl) {
  throw new Error("LOCAL_CI requires TEST_DATABASE_URL for web database tests");
}
const suite = ownerUrl ? describe : describe.skip;
const CREDENTIALS = /\/\/[^@]+@/;
const owner = ownerUrl ? new pg.Client({ connectionString: ownerUrl }) : null;
const projectId = randomUUID();
const sourceId = randomUUID();
const transcriptId = randomUUID();
const runId = randomUUID();
const evidenceId = randomUUID();
const editId = randomUUID();
const descriptorId = randomUUID();
const editSha256 = "e".repeat(64);
const checkCount = 70;

suite("chapter view Postgres dependency ownership", () => {
  beforeAll(async () => {
    if (!(owner && ownerUrl)) {
      return;
    }
    process.env.DATABASE_URL = ownerUrl.replace(
      CREDENTIALS,
      "//temnia_app:temnia_app@"
    );
    await owner.connect();
    await owner.query(
      `INSERT INTO project (id, organization_id, created_by, name)
       VALUES ($1, $2, $3, 'chapter-query-db-test')`,
      [projectId, SEEDED_SCOPE.organizationId, SEEDED_SCOPE.userId]
    );
    await owner.query(
      `INSERT INTO source
         (id, organization_id, project_id, created_by, title, original_filename,
          content_type, size_bytes, master_key, status, duration_ms)
       VALUES ($1, $2, $3, $4, 'chapter-query-db-test', 'fixture.mp4',
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
         (id, organization_id, source_id, status, current_revision)
       VALUES ($1, $2, $3, 'ready', NULL)`,
      [transcriptId, SEEDED_SCOPE.organizationId, sourceId]
    );
    for (const revision of [1, 2]) {
      // biome-ignore lint/performance/noAwaitInLoops: one pg client must serialize fixture writes
      await owner.query(
        `INSERT INTO transcript_revision
           (organization_id, transcript_id, revision, kind, storage_key,
            size_bytes, word_count, metadata, created_by)
         VALUES ($1, $2, $3, $4, $5, 0, 0, '{}'::jsonb, $6)`,
        [
          SEEDED_SCOPE.organizationId,
          transcriptId,
          revision,
          revision === 1 ? "machine" : "correction",
          `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}transcript/rev-${revision}-query-test.json`,
          SEEDED_SCOPE.userId,
        ]
      );
    }
    await owner.query(
      "UPDATE transcript SET current_revision = 2 WHERE id = $1",
      [transcriptId]
    );
    await owner.query(
      `INSERT INTO harness_artifact
         (id, organization_id, source_id, transcript_id, transcript_revision,
          kind, fingerprint, storage_key, sha256, size_bytes, metadata)
       VALUES ($1, $2, $3, $4, 1, 'evidence', $5, $6, $7, 1,
               '{"format":"chapter-evidence/1"}'::jsonb)`,
      [
        evidenceId,
        SEEDED_SCOPE.organizationId,
        sourceId,
        transcriptId,
        "1".repeat(64),
        `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}harness/evidence.json`,
        "a".repeat(64),
      ]
    );
    await owner.query(
      `INSERT INTO harness_artifact
         (id, organization_id, source_id, kind, fingerprint, storage_key,
          sha256, size_bytes, metadata)
       VALUES ($1, $2, $3, 'edit', $4, $5, $6, 1,
               '{"format":"chapter-edit/1"}'::jsonb)`,
      [
        editId,
        SEEDED_SCOPE.organizationId,
        sourceId,
        "2".repeat(64),
        `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}harness/edit.json`,
        editSha256,
      ]
    );
    await owner.query(
      `INSERT INTO harness_run
         (id, organization_id, source_id, lane, status, brief, request_key,
          budget_micros, config, route_snapshot, evidence_artifact_id,
          current_revision)
       VALUES ($1, $2, $3, 'chapters', 'needs_review', 'fixture', $4,
               1000000, '{"backend":"recorded"}'::jsonb,
               '{"editorialPolicy":"standalone-topics/3"}'::jsonb, $5, 1)`,
      [runId, SEEDED_SCOPE.organizationId, sourceId, randomUUID(), evidenceId]
    );
    await owner.query(
      `INSERT INTO chapter_revision
         (organization_id, source_id, run_id, revision, artifact_id,
          mutation_key, created_by)
       VALUES ($1, $2, $3, 1, $4, $5, $6)`,
      [
        SEEDED_SCOPE.organizationId,
        sourceId,
        runId,
        editId,
        randomUUID(),
        SEEDED_SCOPE.userId,
      ]
    );
    await owner.query(
      `INSERT INTO harness_artifact
         (id, organization_id, source_id, kind, fingerprint, storage_key,
          sha256, size_bytes, metadata)
       VALUES ($1, $2, $3, 'render', $4, $5, $6, 1, $7::jsonb)`,
      [
        descriptorId,
        SEEDED_SCOPE.organizationId,
        sourceId,
        "3".repeat(64),
        `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}harness/renders.json`,
        "d".repeat(64),
        JSON.stringify({
          editSha256,
          format: "topic-renders/1",
          renderCount: checkCount,
          runId,
        }),
      ]
    );
    const checkIds = Array.from({ length: checkCount }, () => randomUUID());
    for (const [index, checkId] of checkIds.entries()) {
      // biome-ignore lint/performance/noAwaitInLoops: one pg client must serialize fixture writes
      await owner.query(
        `INSERT INTO harness_artifact
           (id, organization_id, source_id, kind, fingerprint, storage_key,
            sha256, size_bytes, metadata)
         VALUES ($1, $2, $3, 'checks', $4, $5, $6, 1, $7::jsonb)`,
        [
          checkId,
          SEEDED_SCOPE.organizationId,
          sourceId,
          index.toString(16).padStart(64, "0"),
          `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}harness/check-${index}.json`,
          (index + checkCount).toString(16).padStart(64, "0"),
          JSON.stringify({
            editSha256,
            format: "chapter-checks/1",
            sectionId: `section-${index}`,
          }),
        ]
      );
      await owner.query(
        `INSERT INTO harness_artifact_dependency
           (organization_id, source_id, artifact_id, input_artifact_id)
         VALUES ($1, $2, $3, $4)`,
        [SEEDED_SCOPE.organizationId, sourceId, descriptorId, checkId]
      );
    }
  }, 20_000);

  afterAll(async () => {
    if (!owner) {
      return;
    }
    await owner.query(
      "DELETE FROM harness_artifact_dependency WHERE source_id = $1",
      [sourceId]
    );
    await owner.query("DELETE FROM chapter_revision WHERE source_id = $1", [
      sourceId,
    ]);
    await owner.query("DELETE FROM harness_run WHERE source_id = $1", [
      sourceId,
    ]);
    await owner.query("DELETE FROM harness_artifact WHERE source_id = $1", [
      sourceId,
    ]);
    await owner.query("DELETE FROM source WHERE id = $1", [sourceId]);
    await owner.query("DELETE FROM project WHERE id = $1", [projectId]);
    await owner.end();
  });

  it("returns every descriptor-owned check and frozen transcript revision", async () => {
    const view = await getTopicView(sourceId, runId);
    const checks = view.artifacts.filter(
      (artifact) =>
        artifact.kind === "checks" &&
        artifact.metadata.format === "chapter-checks/1"
    );
    expect(checks).toHaveLength(checkCount);
    expect(
      checks.every((artifact) => artifact.metadata.runId === undefined)
    ).toBe(true);
    expect(view.run).toMatchObject({
      currentTranscriptRevision: 2,
      evidenceTranscriptRevision: 1,
    });
  });

  it("keeps topic history to the one topic policy, including exact run selection", async () => {
    if (!owner) {
      return;
    }
    const topicRunId = randomUUID();
    const foreignRunId = randomUUID();
    await Promise.all(
      (
        [
          [topicRunId, "standalone-topics/3"],
          [foreignRunId, "standalone-topics/1"],
        ] as const
      ).map(([id, policy]) =>
        owner.query(
          `INSERT INTO harness_run
             (id, organization_id, source_id, lane, status, brief, request_key,
              budget_micros, config, route_snapshot, evidence_artifact_id, current_revision)
           VALUES ($1, $2, $3, 'chapters', 'needs_review', 'topic fixture', $4,
              1000000, '{"backend":"recorded"}'::jsonb,
              jsonb_build_object('editorialPolicy', $6::text), $5, 0)`,
          [
            id,
            SEEDED_SCOPE.organizationId,
            sourceId,
            randomUUID(),
            evidenceId,
            policy,
          ]
        )
      )
    );
    const [topics, exact, unknown] = await Promise.all([
      getTopicView(sourceId),
      getTopicView(sourceId, runId),
      getTopicView(sourceId, randomUUID()),
    ]);
    // The deleted programs' rows are not history: only the one policy is listed.
    expect(topics.runs.map((item) => item.id)).toEqual([topicRunId, runId]);
    expect(topics.run?.id).toBe(topicRunId);
    expect(exact.run?.id).toBe(runId);
    expect(unknown.run).toBeNull();
  });
});
