import { ARTIFACT_PATHS, sourcePrefix } from "@temnia/contracts";
import Link from "next/link";
import { notFound } from "next/navigation";
import { SourceUploader } from "@/components/sources/source-uploader";
import { SourcesTable } from "@/components/sources/sources-table";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { resolveScope } from "@/lib/scope/resolve-scope";
import { getProject, listSources } from "@/lib/sources/queries";

export const dynamic = "force-dynamic";

export default async function ProjectPage({
  params,
}: PageProps<"/projects/[projectId]">) {
  const { projectId } = await params;
  const project = await getProject(projectId);
  if (!project) {
    notFound();
  }
  const sources = await listSources(projectId);
  const { organizationId } = resolveScope();
  const rows = sources.map((row) => ({
    ...row,
    posterUrl:
      row.status === "ready"
        ? `/api/media/${sourcePrefix(organizationId, row.id)}${ARTIFACT_PATHS.poster}`
        : null,
  }));
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-6 py-12">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink render={<Link href="/projects" />}>
              Projects
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{project.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <div className="flex flex-col gap-1">
        <h1 className="font-semibold text-2xl tracking-tight">
          {project.name}
        </h1>
        <p className="text-muted-foreground text-sm">
          Upload the master. It is stored as-is and cut from the original, never
          from a re-compressed copy.
        </p>
      </div>
      <SourceUploader projectId={project.id} />
      <SourcesTable rows={rows} />
    </main>
  );
}
