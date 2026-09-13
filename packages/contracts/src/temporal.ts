/** Workflow queue. Python derives `${pipeline}-control` for short DB activities. */
export const TASK_QUEUES = {
  pipeline: "temnia-pipeline",
} as const;

/** Workflow type names, shared with the Python worker's `@workflow.defn(name=...)`. */
export const WORKFLOWS = {
  hello: "HelloWorkflow",
  ingest: "IngestWorkflow",
  reaper: "ReaperWorkflow",
  topicEditorialPatch: "TopicEditorialPatchWorkflow",
  topicReview: "TopicReviewWorkflow",
  topicSelection: "TopicSelectionWorkflow",
  transcribe: "TranscribeWorkflow",
} as const;
