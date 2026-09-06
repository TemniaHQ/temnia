import Link from "next/link";
import { CreateProjectForm } from "@/components/projects/create-project-form";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listProjects } from "@/lib/sources/queries";

export const dynamic = "force-dynamic";

export default async function ProjectsPage() {
  const projects = await listProjects();
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-8 px-6 py-12">
      <div className="flex flex-col gap-1">
        <h1 className="font-semibold text-2xl tracking-tight">Projects</h1>
        <p className="text-muted-foreground text-sm">
          Each project holds the masters for one show or episode.
        </p>
      </div>
      <CreateProjectForm />
      {projects.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No projects yet</EmptyTitle>
            <EmptyDescription>
              Create one, then upload a master into it.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Table data-testid="projects-table">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {projects.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Link
                    className="font-medium underline-offset-4 hover:underline"
                    href={`/projects/${row.id}`}
                  >
                    {row.name}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {row.createdAt.toISOString().slice(0, 10)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </main>
  );
}
