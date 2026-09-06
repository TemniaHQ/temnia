"use client";

import { useActionState } from "react";
import { createProject, type ProjectFormState } from "@/app/actions/projects";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

const initialState: ProjectFormState = { status: "idle" };

export function CreateProjectForm() {
  const [state, formAction, pending] = useActionState(
    createProject,
    initialState
  );
  return (
    <form action={formAction} className="flex items-end gap-3">
      <Field className="flex-1">
        <FieldLabel htmlFor="project-name">New project</FieldLabel>
        <Input
          autoComplete="off"
          id="project-name"
          maxLength={120}
          name="name"
          placeholder="Episode 42"
          required
        />
      </Field>
      <Button disabled={pending} type="submit">
        {pending ? "Creating…" : "Create project"}
      </Button>
      {state.status === "error" ? (
        <p className="text-destructive text-sm" role="alert">
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
