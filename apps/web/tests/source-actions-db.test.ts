import { randomUUID } from "node:crypto";
import { SEEDED_SCOPE, sourcePrefix } from "@temnia/contracts";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { retryIngest } from "@/app/actions/sources";

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));

const temporal = vi.hoisted(() => ({ start: vi.fn() }));
vi.mock("@/lib/temporal/client", () => ({
  getTemporalClient: async () => ({ workflow: { start: temporal.start } }),
}));

const ownerUrl = process.env.TEST_DATABASE_URL;
if (process.env.LOCAL_CI === "1" && !ownerUrl) {
  throw new Error("LOCAL_CI requires TEST_DATABASE_URL for web database tests");
}
const suite = ownerUrl ? describe : describe.skip;
const CREDENTIALS = /\/\/[^@]+@/;
const owner = ownerUrl ? new pg.Client({ connectionString: ownerUrl }) : null;
const projectId = randomUUID();
const sourceId = randomUUID();

suite("source action Postgres fences", () => {
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
       VALUES ($1, $2, $3, 'source-actions-db-test')`,
      [projectId, SEEDED_SCOPE.organizationId, SEEDED_SCOPE.userId]
    );
    await owner.query(
      `INSERT INTO source
         (id, organization_id, project_id, created_by, title, original_filename,
          content_type, size_bytes, master_key, status, duration_ms)
       VALUES ($1, $2, $3, $4, 'source-actions-db-test', 'fixture.mp4',
               'video/mp4', 1, $5, 'failed', 1234)`,
      [
        sourceId,
        SEEDED_SCOPE.organizationId,
        projectId,
        SEEDED_SCOPE.userId,
        `${sourcePrefix(SEEDED_SCOPE.organizationId, sourceId)}master.mp4`,
      ]
    );
  });

  afterAll(async () => {
    if (!owner) {
      return;
    }
    await owner.query("DELETE FROM source WHERE id = $1", [sourceId]);
    await owner.query("DELETE FROM project WHERE id = $1", [projectId]);
    await owner.end();
  });

  it("does not resurrect a failed source fenced while Temporal starts", async () => {
    temporal.start.mockImplementationOnce(async () => {
      await owner?.query(
        "UPDATE source SET deletion_requested_at = now() WHERE id = $1",
        [sourceId]
      );
      return {};
    });

    await expect(retryIngest(sourceId)).resolves.toEqual({
      message: "this source cannot be retried right now",
      ok: false,
    });
    const result = await owner?.query<{
      deletion_requested_at: Date | null;
      duration_ms: number | null;
      status: string;
    }>(
      `SELECT deletion_requested_at, duration_ms, status
       FROM source WHERE id = $1`,
      [sourceId]
    );
    expect(result?.rows[0]).toMatchObject({
      duration_ms: 1234,
      status: "failed",
    });
    expect(result?.rows[0]?.deletion_requested_at).toBeInstanceOf(Date);
  });
});
