"use server";

import {
  HelloInputSchema,
  type HelloOutput,
  HelloOutputSchema,
  TASK_QUEUES,
  WORKFLOWS,
} from "@temnia/contracts";
import { resolveScope } from "@/lib/scope/resolve-scope";
import { getTemporalClient } from "@/lib/temporal/client";

export type HelloState =
  | { status: "idle" }
  | {
      status: "ok";
      result: HelloOutput;
      workflowId: string;
      durationMs: number;
    }
  | { status: "error"; message: string };

/**
 * Starts the S0 hello workflow on the Python task queue and waits for it.
 * The scope comes from the resolver, never from the form.
 */
export async function runHello(
  _previous: HelloState,
  formData: FormData
): Promise<HelloState> {
  const parsed = HelloInputSchema.safeParse({
    name: formData.get("name"),
    scope: resolveScope(),
  });
  if (!parsed.success) {
    return {
      message: "Give the worker a name between 1 and 80 characters.",
      status: "error",
    };
  }

  const workflowId = `hello-${crypto.randomUUID()}`;
  const startedAt = performance.now();
  try {
    const client = await getTemporalClient();
    const raw: unknown = await client.workflow.execute(WORKFLOWS.hello, {
      args: [parsed.data],
      taskQueue: TASK_QUEUES.pipeline,
      workflowExecutionTimeout: "30 seconds",
      workflowId,
    });
    const result = HelloOutputSchema.parse(raw);
    return {
      durationMs: Math.round(performance.now() - startedAt),
      result,
      status: "ok",
      workflowId,
    };
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "The workflow did not complete.";
    return { message, status: "error" };
  }
}
