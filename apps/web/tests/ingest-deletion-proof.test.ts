import {
  type Client,
  defaultPayloadConverter,
  type WorkflowExecutionDescription,
} from "@temporalio/client";
import { describe, expect, it, vi } from "vitest";
import { provesPreWriterIngestFailure } from "@/lib/temporal/ingest-deletion-proof";

const WORKFLOW_ID = "ingest-01992ffe-0a00-7000-8000-000000000001";
const RUN_ID = "01992ffe-0a00-7000-8000-000000000002";

interface Event {
  activityTaskCanceledEventAttributes?: object;
  activityTaskCancelRequestedEventAttributes?: object;
  activityTaskCompletedEventAttributes?: {
    result?: { payloads?: unknown[] };
    scheduledEventId?: string;
    startedEventId?: string;
  };
  activityTaskFailedEventAttributes?: {
    failure?: {
      applicationFailureInfo?: { nonRetryable?: boolean; type?: string };
    };
    retryState?: number;
    scheduledEventId?: string;
    startedEventId?: string;
  };
  activityTaskScheduledEventAttributes?: {
    activityType?: { name?: string | null } | null;
  } | null;
  activityTaskStartedEventAttributes?: {
    attempt?: number;
    scheduledEventId?: string;
  };
  activityTaskTimedOutEventAttributes?: object;
  attributes?: string;
  eventId?: string;
  workflowExecutionFailedEventAttributes?: {
    failure?: {
      applicationFailureInfo?: { nonRetryable?: boolean; type?: string };
      activityFailureInfo?: {
        activityType?: { name?: string };
        retryState?: number;
        scheduledEventId?: string;
      };
      cause?: {
        applicationFailureInfo?: { nonRetryable?: boolean; type?: string };
      };
    };
  };
}

function expectedHistory(): Event[] {
  const events: Event[] = [
    { attributes: "workflowExecutionStartedEventAttributes", eventId: "1" },
    {
      activityTaskScheduledEventAttributes: {
        activityType: { name: "claim_source" },
      },
      attributes: "activityTaskScheduledEventAttributes",
      eventId: "2",
    },
    {
      activityTaskStartedEventAttributes: {
        attempt: 1,
        scheduledEventId: "2",
      },
      attributes: "activityTaskStartedEventAttributes",
      eventId: "3",
    },
    {
      activityTaskCompletedEventAttributes: {
        result: { payloads: [defaultPayloadConverter.toPayload(true)] },
        scheduledEventId: "2",
        startedEventId: "3",
      },
      attributes: "activityTaskCompletedEventAttributes",
      eventId: "4",
    },
    {
      activityTaskScheduledEventAttributes: {
        activityType: { name: "probe_source" },
      },
      attributes: "activityTaskScheduledEventAttributes",
      eventId: "5",
    },
    {
      activityTaskStartedEventAttributes: {
        attempt: 1,
        scheduledEventId: "5",
      },
      attributes: "activityTaskStartedEventAttributes",
      eventId: "6",
    },
    {
      activityTaskFailedEventAttributes: {
        failure: {
          applicationFailureInfo: {
            nonRetryable: true,
            type: "IngestFailure",
          },
        },
        retryState: 2,
        scheduledEventId: "5",
        startedEventId: "6",
      },
      attributes: "activityTaskFailedEventAttributes",
      eventId: "7",
    },
    {
      activityTaskScheduledEventAttributes: {
        activityType: { name: "fail_source" },
      },
      attributes: "activityTaskScheduledEventAttributes",
      eventId: "8",
    },
    {
      activityTaskStartedEventAttributes: {
        attempt: 1,
        scheduledEventId: "8",
      },
      attributes: "activityTaskStartedEventAttributes",
      eventId: "9",
    },
    {
      activityTaskCompletedEventAttributes: {
        scheduledEventId: "8",
        startedEventId: "9",
      },
      attributes: "activityTaskCompletedEventAttributes",
      eventId: "10",
    },
    {
      attributes: "workflowExecutionFailedEventAttributes",
      eventId: "11",
      workflowExecutionFailedEventAttributes: {
        failure: {
          activityFailureInfo: {
            activityType: { name: "probe_source" },
            retryState: 2,
            scheduledEventId: "5",
          },
          cause: {
            applicationFailureInfo: {
              nonRetryable: true,
              type: "IngestFailure",
            },
          },
        },
      },
    },
  ];
  return events;
}

function claimRefusalHistory(): Event[] {
  return [
    { attributes: "workflowExecutionStartedEventAttributes", eventId: "1" },
    {
      activityTaskScheduledEventAttributes: {
        activityType: { name: "claim_source" },
      },
      attributes: "activityTaskScheduledEventAttributes",
      eventId: "2",
    },
    {
      activityTaskStartedEventAttributes: {
        attempt: 1,
        scheduledEventId: "2",
      },
      attributes: "activityTaskStartedEventAttributes",
      eventId: "3",
    },
    {
      activityTaskCompletedEventAttributes: {
        result: { payloads: [defaultPayloadConverter.toPayload(false)] },
        scheduledEventId: "2",
        startedEventId: "3",
      },
      attributes: "activityTaskCompletedEventAttributes",
      eventId: "4",
    },
    {
      attributes: "workflowExecutionFailedEventAttributes",
      eventId: "5",
      workflowExecutionFailedEventAttributes: {
        failure: {
          applicationFailureInfo: {
            nonRetryable: true,
            type: "NotClaimable",
          },
        },
      },
    },
  ];
}

function description(
  events: Event[],
  overrides: Partial<WorkflowExecutionDescription> = {}
): WorkflowExecutionDescription {
  return {
    historyLength: events.length,
    historySize: 10_342,
    runId: RUN_ID,
    status: { code: 3, name: "FAILED" },
    type: "IngestWorkflow",
    ...overrides,
  } as WorkflowExecutionDescription;
}

function fakeClient(events: Event[]) {
  const handle = {
    fetchHistory: vi.fn(async () => ({ events })),
  };
  const getHandle = vi.fn(() => handle);
  const withDeadline = vi.fn(
    async <Result>(
      _deadline: number | Date,
      operation: () => Promise<Result>
    ) => operation()
  );
  return {
    client: { withDeadline, workflow: { getHandle } } as unknown as Client,
    getHandle,
    handle,
    withDeadline,
  };
}

describe("pre-writer ingest deletion proof", () => {
  it("accepts only the exact failed run and known nonretryable probe path", async () => {
    const events = expectedHistory();
    const { client, getHandle, handle, withDeadline } = fakeClient(events);

    await expect(
      provesPreWriterIngestFailure(client, WORKFLOW_ID, description(events))
    ).resolves.toBe(true);

    expect(getHandle).toHaveBeenCalledWith(WORKFLOW_ID, RUN_ID, {
      followRuns: false,
    });
    expect(withDeadline).toHaveBeenCalledOnce();
    expect(handle.fetchHistory).toHaveBeenCalledOnce();
  });

  it("accepts an exact claim refusal with a decoded false result", async () => {
    const events = claimRefusalHistory();
    const { client } = fakeClient(events);
    await expect(
      provesPreWriterIngestFailure(client, WORKFLOW_ID, description(events))
    ).resolves.toBe(true);

    const claimed = claimRefusalHistory();
    const completed = claimed.find(
      (event) => event.activityTaskCompletedEventAttributes
    );
    if (completed?.activityTaskCompletedEventAttributes?.result) {
      completed.activityTaskCompletedEventAttributes.result.payloads = [
        defaultPayloadConverter.toPayload(true),
      ];
    }
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(claimed).client,
        WORKFLOW_ID,
        description(claimed)
      )
    ).resolves.toBe(false);
  });

  it.each([
    ["writer activity", "transcode_source", null],
    ["wrong activity order", "fail_source", null],
    [
      "child dispatch",
      null,
      "startChildWorkflowExecutionInitiatedEventAttributes",
    ],
    [
      "external signal",
      null,
      "signalExternalWorkflowExecutionInitiatedEventAttributes",
    ],
    ["Nexus dispatch", null, "nexusOperationScheduledEventAttributes"],
    ["recorded marker", null, "markerRecordedEventAttributes"],
  ])("rejects a history containing %s", async (_case, activity, external) => {
    const events = expectedHistory();
    if (activity) {
      events.splice(-1, 0, {
        activityTaskScheduledEventAttributes: {
          activityType: { name: activity },
        },
        attributes: "activityTaskScheduledEventAttributes",
      });
    }
    if (external) {
      events.splice(-1, 0, { attributes: external });
    }
    events.forEach((event, index) => {
      event.eventId = String(index + 1);
    });
    const { client } = fakeClient(events);

    await expect(
      provesPreWriterIngestFailure(client, WORKFLOW_ID, description(events))
    ).resolves.toBe(false);
  });

  it("rejects a scheduled activity without a name and cancellation edges", async () => {
    const missingName = expectedHistory();
    const scheduled = missingName.find(
      (event) =>
        event.activityTaskScheduledEventAttributes?.activityType?.name ===
        "probe_source"
    );
    if (scheduled?.activityTaskScheduledEventAttributes) {
      scheduled.activityTaskScheduledEventAttributes.activityType = {};
    }
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(missingName).client,
        WORKFLOW_ID,
        description(missingName)
      )
    ).resolves.toBe(false);

    const canceled = expectedHistory();
    canceled.splice(-1, 0, {
      activityTaskCancelRequestedEventAttributes: {},
      attributes: "activityTaskCancelRequestedEventAttributes",
    });
    canceled.forEach((event, index) => {
      event.eventId = String(index + 1);
    });
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(canceled).client,
        WORKFLOW_ID,
        description(canceled)
      )
    ).resolves.toBe(false);
  });

  it.each([
    ["a retryable failure", "IngestFailure", false],
    ["a writer failure type", "TranscodeFailure", true],
  ])("rejects %s", async (_case, type, nonRetryable) => {
    const events = expectedHistory();
    const failed = events.find(
      (event) => event.activityTaskFailedEventAttributes
    );
    const terminal = events.at(-1)?.workflowExecutionFailedEventAttributes;
    if (failed?.activityTaskFailedEventAttributes?.failure) {
      failed.activityTaskFailedEventAttributes.failure.applicationFailureInfo =
        {
          nonRetryable,
          type,
        };
    }
    if (terminal?.failure?.cause) {
      terminal.failure.cause.applicationFailureInfo = {
        nonRetryable,
        type,
      };
    }
    const { client } = fakeClient(events);
    await expect(
      provesPreWriterIngestFailure(client, WORKFLOW_ID, description(events))
    ).resolves.toBe(false);
  });

  it("rejects retried or incomplete activity terminal edges", async () => {
    const retried = expectedHistory();
    const probeStarted = retried.find(
      (event) =>
        event.activityTaskStartedEventAttributes?.scheduledEventId === "5"
    );
    if (probeStarted?.activityTaskStartedEventAttributes) {
      probeStarted.activityTaskStartedEventAttributes.attempt = 2;
    }
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(retried).client,
        WORKFLOW_ID,
        description(retried)
      )
    ).resolves.toBe(false);

    const timedOut = expectedHistory();
    timedOut.splice(-1, 0, {
      activityTaskTimedOutEventAttributes: {},
      attributes: "activityTaskTimedOutEventAttributes",
    });
    timedOut.forEach((event, index) => {
      event.eventId = String(index + 1);
    });
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(timedOut).client,
        WORKFLOW_ID,
        description(timedOut)
      )
    ).resolves.toBe(false);

    const missingCompletion = expectedHistory().filter(
      (event) =>
        event.activityTaskCompletedEventAttributes?.scheduledEventId !== "8"
    );
    missingCompletion.forEach((event, index) => {
      event.eventId = String(index + 1);
    });
    await expect(
      provesPreWriterIngestFailure(
        fakeClient(missingCompletion).client,
        WORKFLOW_ID,
        description(missingCompletion)
      )
    ).resolves.toBe(false);
  });

  it("rejects truncated, oversized and unavailable histories", async () => {
    const events = expectedHistory();
    const complete = fakeClient(events);
    await expect(
      provesPreWriterIngestFailure(
        complete.client,
        WORKFLOW_ID,
        description(events, { historyLength: events.length + 1 })
      )
    ).resolves.toBe(false);

    const oversized = fakeClient(events);
    await expect(
      provesPreWriterIngestFailure(
        oversized.client,
        WORKFLOW_ID,
        description(events, { historySize: 512 * 1024 + 1 })
      )
    ).resolves.toBe(false);
    expect(oversized.getHandle).not.toHaveBeenCalled();

    const sizeMissing = fakeClient(events);
    const missingSizeDescription = {
      ...description(events),
      historySize: undefined,
    } as unknown as WorkflowExecutionDescription;
    await expect(
      provesPreWriterIngestFailure(
        sizeMissing.client,
        WORKFLOW_ID,
        missingSizeDescription
      )
    ).resolves.toBe(false);
    expect(sizeMissing.getHandle).not.toHaveBeenCalled();

    const invalidSizes = [-1, Number.NaN].map((historySize) => ({
      fake: fakeClient(events),
      historySize,
    }));
    await Promise.all(
      invalidSizes.map(({ fake, historySize }) =>
        expect(
          provesPreWriterIngestFailure(
            fake.client,
            WORKFLOW_ID,
            description(events, { historySize })
          )
        ).resolves.toBe(false)
      )
    );
    for (const { fake } of invalidSizes) {
      expect(fake.getHandle).not.toHaveBeenCalled();
    }

    const tooMany = fakeClient(events);
    await expect(
      provesPreWriterIngestFailure(
        tooMany.client,
        WORKFLOW_ID,
        description(events, { historyLength: 129 })
      )
    ).resolves.toBe(false);
    expect(tooMany.getHandle).not.toHaveBeenCalled();

    const unavailable = fakeClient(events);
    unavailable.handle.fetchHistory.mockRejectedValueOnce(
      new Error("RPC failed")
    );
    await expect(
      provesPreWriterIngestFailure(
        unavailable.client,
        WORKFLOW_ID,
        description(events)
      )
    ).resolves.toBe(false);
  });
});
