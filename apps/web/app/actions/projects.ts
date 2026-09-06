"use server";

import { project } from "@temnia/db";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { scoped } from "@/lib/db";

const NameSchema = z.string().trim().min(1).max(120);

export type ProjectFormState =
  | { status: "idle" }
  | { status: "error"; message: string };

/** Creates a project under the resolved scope and opens it. */
export async function createProject(
  _previous: ProjectFormState,
  formData: FormData
): Promise<ProjectFormState> {
  const parsed = NameSchema.safeParse(formData.get("name"));
  if (!parsed.success) {
    return {
      message: "Give the project a name between 1 and 120 characters.",
      status: "error",
    };
  }
  const [created] = await scoped((tx, scope) =>
    tx
      .insert(project)
      .values({
        createdBy: scope.userId,
        name: parsed.data,
        organizationId: scope.organizationId,
      })
      .returning({ id: project.id })
  );
  if (!created) {
    return { message: "The project was not created.", status: "error" };
  }
  revalidatePath("/projects");
  redirect(`/projects/${created.id}`);
}
