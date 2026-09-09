import {
  type Client,
  defaultPayloadConverter,
  type WorkflowExecutionDescription,
} from "@temporalio/client";

const MAX_HISTORY_EVENTS = 128;
const MAX_HISTORY_BYTES = 512 * 1024;
const PROOF_DEADLINE_MS = 5000;
// temporal.api.enums.v1.RETRY_STATE_NON_RETRYABLE_FAILURE in the pinned proto.
const NON_RETRYABLE_FAILURE = 2;
const PROBE_FAILURE_ACTIVITIES = [
  "claim_source",
  "probe_source",
  "fail_source",
];
const CLAIM_REFUSAL_ACTIVITIES = ["claim_source"];
const POSITIVE_EVENT_ID = /^[1-9]\d*$/;
const UNSUPPORTED_PROOF_EVENTS = new Set([
  "nexusOperationScheduledEventAttributes",
  "markerRecordedEventAttributes",
  "requestCancelExternalWorkflowExecutionInitiatedEventAttributes",
  "signalExternalWorkflowExecutionInitiatedEventAttributes",
  "startChildWorkflowExecutionInitiatedEventAttributes",
  "workflowExecutionContinuedAsNewEventAttributes",
]);

function arraysEqual(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

function eventId(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  const rendered = String(value);
  return POSITIVE_EVENT_ID.test(rendered) ? rendered : null;
}

function isPresent(value: unknown): boolean {
  return value !== null && value !== undefined;
}

/**
 * Prove that a failed ingest stopped at its read-only media probe.
 *
 * The source deletion fence is already durable before this runs. The proof is
 * bound to the exact described run and fails closed on every unsupported or
 * incomplete history shape. `probe_source` downloads to local scratch and
 * probes the bytes; it dispatches no external storage writer.
 */
export async function provesPreWriterIngestFailure(
  client: Client,
  workflowId: string,
  description: WorkflowExecutionDescription
): Promise<boolean> {
  if (
    description.type !== "IngestWorkflow" ||
    description.status.name !== "FAILED" ||
    !description.runId ||
    !Number.isInteger(description.historyLength) ||
    description.historyLength < 1 ||
    description.historyLength > MAX_HISTORY_EVENTS ||
    description.historySize === undefined ||
    !Number.isInteger(description.historySize) ||
    description.historySize < 0 ||
    description.historySize > MAX_HISTORY_BYTES
  ) {
    return false;
  }

  const handle = client.workflow.getHandle(workflowId, description.runId, {
    followRuns: false,
  });
  try {
    return await client.withDeadline(
      Date.now() + PROOF_DEADLINE_MS,
      // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: the bounded parser intentionally rejects every unsupported history edge in one fail-closed pass
      async () => {
        const history = await handle.fetchHistory();
        const events = history.events ?? [];
        if (
          events.length !== description.historyLength ||
          events.length > MAX_HISTORY_EVENTS ||
          events.some(
            (event, index) => eventId(event.eventId) !== String(index + 1)
          ) ||
          events.at(-1)?.attributes !==
            "workflowExecutionFailedEventAttributes" ||
          events.some(
            (event) =>
              event.attributes !== undefined &&
              UNSUPPORTED_PROOF_EVENTS.has(event.attributes)
          )
        ) {
          return false;
        }

        const scheduled = events.filter((event) =>
          isPresent(event.activityTaskScheduledEventAttributes)
        );
        const activities = scheduled.map(
          (event) =>
            event.activityTaskScheduledEventAttributes?.activityType?.name
        );
        if (
          activities.some(
            (name) => typeof name !== "string" || name.length === 0
          ) ||
          !(
            arraysEqual(activities as string[], PROBE_FAILURE_ACTIVITIES) ||
            arraysEqual(activities as string[], CLAIM_REFUSAL_ACTIVITIES)
          )
        ) {
          return false;
        }

        const scheduleIds = scheduled.map((event) => eventId(event.eventId));
        if (scheduleIds.some((id) => id === null)) {
          return false;
        }

        const activityNames = activities as string[];
        const started = events.filter((event) =>
          isPresent(event.activityTaskStartedEventAttributes)
        );
        const completed = events.filter((event) =>
          isPresent(event.activityTaskCompletedEventAttributes)
        );
        const failed = events.filter((event) =>
          isPresent(event.activityTaskFailedEventAttributes)
        );
        if (
          events.some(
            (event) =>
              isPresent(event.activityTaskCanceledEventAttributes) ||
              isPresent(event.activityTaskCancelRequestedEventAttributes) ||
              isPresent(event.activityTaskTimedOutEventAttributes)
          ) ||
          started.length !== scheduled.length ||
          started.some(
            (event, index) =>
              eventId(
                event.activityTaskStartedEventAttributes?.scheduledEventId
              ) !== scheduleIds[index] ||
              event.activityTaskStartedEventAttributes?.attempt !== 1
          ) ||
          completed.length + failed.length !== scheduled.length
        ) {
          return false;
        }

        const startedIds = started.map((event) => eventId(event.eventId));
        if (startedIds.some((id) => id === null)) {
          return false;
        }

        const terminal = events.at(-1)?.workflowExecutionFailedEventAttributes;
        if (arraysEqual(activityNames, CLAIM_REFUSAL_ACTIVITIES)) {
          const payloads =
            completed[0]?.activityTaskCompletedEventAttributes?.result
              ?.payloads;
          const claimResult = payloads?.[0];
          return (
            completed.length === 1 &&
            failed.length === 0 &&
            eventId(
              completed[0]?.activityTaskCompletedEventAttributes
                ?.scheduledEventId
            ) === scheduleIds[0] &&
            eventId(
              completed[0]?.activityTaskCompletedEventAttributes?.startedEventId
            ) === startedIds[0] &&
            payloads?.length === 1 &&
            claimResult !== undefined &&
            defaultPayloadConverter.fromPayload(claimResult) === false &&
            terminal?.failure?.applicationFailureInfo?.type ===
              "NotClaimable" &&
            terminal.failure.applicationFailureInfo.nonRetryable === true
          );
        }

        if (
          completed.length !== 2 ||
          eventId(
            completed[0]?.activityTaskCompletedEventAttributes?.scheduledEventId
          ) !== scheduleIds[0] ||
          eventId(
            completed[0]?.activityTaskCompletedEventAttributes?.startedEventId
          ) !== startedIds[0] ||
          eventId(
            completed[1]?.activityTaskCompletedEventAttributes?.scheduledEventId
          ) !== scheduleIds[2] ||
          eventId(
            completed[1]?.activityTaskCompletedEventAttributes?.startedEventId
          ) !== startedIds[2] ||
          failed.length !== 1
        ) {
          return false;
        }
        const probeFailure = failed[0]?.activityTaskFailedEventAttributes;
        const probeCause = probeFailure?.failure?.applicationFailureInfo;
        const claimPayloads =
          completed[0]?.activityTaskCompletedEventAttributes?.result?.payloads;
        const claimResult = claimPayloads?.[0];
        const terminalActivity = terminal?.failure?.activityFailureInfo;
        const terminalCause = terminal?.failure?.cause?.applicationFailureInfo;
        return (
          eventId(probeFailure?.scheduledEventId) === scheduleIds[1] &&
          eventId(probeFailure?.startedEventId) === startedIds[1] &&
          claimPayloads?.length === 1 &&
          claimResult !== undefined &&
          defaultPayloadConverter.fromPayload(claimResult) === true &&
          probeFailure?.retryState === NON_RETRYABLE_FAILURE &&
          probeCause?.type === "IngestFailure" &&
          probeCause.nonRetryable === true &&
          terminalActivity?.activityType?.name === "probe_source" &&
          eventId(terminalActivity.scheduledEventId) === scheduleIds[1] &&
          terminalActivity.retryState === NON_RETRYABLE_FAILURE &&
          terminalCause?.type === "IngestFailure" &&
          terminalCause.nonRetryable === true
        );
      }
    );
  } catch {
    return false;
  }
}
