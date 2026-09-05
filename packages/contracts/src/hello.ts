import { z } from "zod";
import { ScopeSchema } from "./scope.ts";

/** Input to the S0 hello workflow: proves scope crosses the language seam. */
export const HelloInputSchema = z
  .object({
    name: z.string().min(1).max(80).describe("Who to greet."),
    scope: ScopeSchema,
  })
  .meta({ id: "HelloInput", title: "HelloInput" });

export type HelloInput = z.infer<typeof HelloInputSchema>;

/** Result of the S0 hello workflow, produced by the Python worker. */
export const HelloOutputSchema = z
  .object({
    greeting: z.string(),
    organizationId: z
      .uuid()
      .describe("Echoed from the input scope, never invented."),
    workerHost: z.string(),
    workerLanguage: z.literal("python"),
  })
  .meta({ id: "HelloOutput", title: "HelloOutput" });

export type HelloOutput = z.infer<typeof HelloOutputSchema>;
