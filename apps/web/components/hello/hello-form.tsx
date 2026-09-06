"use client";

import { useActionState } from "react";
import { type HelloState, runHello } from "@/app/actions/hello";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

const initialState: HelloState = { status: "idle" };

export function HelloForm() {
  const [state, formAction, pending] = useActionState(runHello, initialState);

  return (
    <Card className="w-full max-w-lg">
      <CardHeader>
        <CardTitle>Hello workflow</CardTitle>
        <CardDescription>
          A server action starts a Temporal workflow; the Python worker
          completes it and echoes the organization it ran under.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <form action={formAction} className="flex flex-col gap-4">
          <Field>
            <FieldLabel htmlFor="hello-name">Name</FieldLabel>
            <Input
              autoComplete="off"
              defaultValue="Temnia"
              id="hello-name"
              maxLength={80}
              name="name"
              required
            />
            <FieldDescription>Who the worker should greet.</FieldDescription>
          </Field>
          <Button disabled={pending} type="submit">
            {pending ? "Running…" : "Run the hello workflow"}
          </Button>
        </form>

        {state.status === "ok" ? (
          <dl
            className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-sm"
            data-testid="hello-result"
          >
            <dt className="text-muted-foreground">greeting</dt>
            <dd>{state.result.greeting}</dd>
            <dt className="text-muted-foreground">organization</dt>
            <dd>{state.result.organizationId}</dd>
            <dt className="text-muted-foreground">worker</dt>
            <dd>
              {state.result.workerLanguage} on {state.result.workerHost}
            </dd>
            <dt className="text-muted-foreground">workflow</dt>
            <dd>{state.workflowId}</dd>
            <dt className="text-muted-foreground">round trip</dt>
            <dd>{state.durationMs} ms</dd>
          </dl>
        ) : null}
        {state.status === "error" ? (
          <p
            className="text-destructive text-sm"
            data-testid="hello-error"
            role="alert"
          >
            {state.message}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
