import { artifact, project, source } from "@temnia/db";
import { desc, eq } from "drizzle-orm";
import { scoped } from "@/lib/db";

export type ProjectRow = typeof project.$inferSelect;
export type SourceRow = typeof source.$inferSelect;
export type ArtifactRow = typeof artifact.$inferSelect;

export function listProjects(): Promise<ProjectRow[]> {
  return scoped((tx) =>
    tx.select().from(project).orderBy(desc(project.createdAt))
  );
}

export function getProject(projectId: string): Promise<ProjectRow | undefined> {
  return scoped(async (tx) => {
    const [row] = await tx
      .select()
      .from(project)
      .where(eq(project.id, projectId))
      .limit(1);
    return row;
  });
}

export function listSources(projectId: string): Promise<SourceRow[]> {
  return scoped((tx) =>
    tx
      .select()
      .from(source)
      .where(eq(source.projectId, projectId))
      .orderBy(desc(source.createdAt))
  );
}

export function getSourceWithArtifacts(
  sourceId: string
): Promise<
  | { source: SourceRow; artifacts: ArtifactRow[]; project: ProjectRow }
  | undefined
> {
  return scoped(async (tx) => {
    const [row] = await tx
      .select()
      .from(source)
      .where(eq(source.id, sourceId))
      .limit(1);
    if (!row) {
      return;
    }
    const [owner] = await tx
      .select()
      .from(project)
      .where(eq(project.id, row.projectId))
      .limit(1);
    if (!owner) {
      return;
    }
    const artifacts = await tx
      .select()
      .from(artifact)
      .where(eq(artifact.sourceId, sourceId));
    return { artifacts, project: owner, source: row };
  });
}
