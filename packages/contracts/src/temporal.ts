/** Task queue names. One queue per worker language; the pipeline is Python. */
export const TASK_QUEUES = {
  pipeline: "temnia-pipeline",
} as const;

/** Workflow type names, shared with the Python worker's `@workflow.defn(name=...)`. */
export const WORKFLOWS = {
  hello: "HelloWorkflow",
  ingest: "IngestWorkflow",
  reaper: "ReaperWorkflow",
} as const;
