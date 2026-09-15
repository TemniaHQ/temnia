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
  topicSelectionV4: "TopicSelectionWorkflowV4",
  topicSelectionV5: "TopicSelectionWorkflowV5",
  topicSelectionV6: "TopicSelectionWorkflowV6",
  topicSelectionV7: "TopicSelectionWorkflowV7",
  topicSelectionV8: "TopicSelectionWorkflowV8",
  transcribe: "TranscribeWorkflow",
} as const;
