export type ChapterPanelMessage =
  | { kind: "command" | "refresh"; text: string }
  | { kind: "start"; runId: string; text: string };

export type DescriptorCheckState = "invalid" | "loaded" | "loading";
export type TechnicalCheckState = "blocked" | "loading" | "pass";

const MICROS_PER_DOLLAR = 1_000_000;
const TRAILING_ZEROES = /0+$/;

/** Render an integer-micros ceiling as an exact editable decimal dollar value. */
export function budgetInputFromMicros(value: number): string {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new RangeError("budget micros must be a nonnegative safe integer");
  }
  const whole = Math.floor(value / MICROS_PER_DOLLAR);
  const micros = String(value % MICROS_PER_DOLLAR).padStart(6, "0");
  const fraction = micros.replace(TRAILING_ZEROES, "").padEnd(2, "0");
  return `${whole}.${fraction}`;
}

export function appliedBudgetMatchesDraft({
  commandState,
  currentDraft,
  observedMicros,
  runMatches,
  submittedDraft,
  submittedMicros,
}: {
  commandState: string;
  currentDraft: string;
  observedMicros: number;
  runMatches: boolean;
  submittedDraft: string;
  submittedMicros: number;
}): boolean {
  return (
    commandState === "applied" &&
    runMatches &&
    currentDraft === submittedDraft &&
    observedMicros === submittedMicros
  );
}

export function clearMatchedStartMessage(
  message: ChapterPanelMessage | null,
  runId: string
): ChapterPanelMessage | null {
  return message?.kind === "start" && message.runId === runId ? null : message;
}

export function chapterWaitingMessage({
  checkState,
  descriptorCheckState,
  hasSelectedEdit,
}: {
  checkState: TechnicalCheckState;
  descriptorCheckState: DescriptorCheckState;
  hasSelectedEdit: boolean;
}): string | null {
  if (!hasSelectedEdit) {
    return null;
  }
  if (descriptorCheckState === "loading") {
    return "Chapter renders are still being prepared. Technical checks follow each render.";
  }
  if (descriptorCheckState === "loaded" && checkState === "loading") {
    return "Technical checks are still being verified. Acceptance and export are waiting.";
  }
  return null;
}

export function summaryGroundingMessage({
  fallbackQuoteCount,
  fallbackUnitCount,
}: {
  fallbackQuoteCount: number;
  fallbackUnitCount: number;
}): string | null {
  if (fallbackUnitCount === 0) {
    return null;
  }
  const passages = fallbackUnitCount === 1 ? "passage" : "passages";
  const references = fallbackQuoteCount === 1 ? "reference" : "references";
  return `Used the original transcript for ${fallbackUnitCount} summary ${passages} after finding ${fallbackQuoteCount} mismatched source ${references}. Review these passages before accepting the chapters.`;
}

export function chapterPlanningStoppedMessage(
  status: string,
  currentRevision: number
): string | null {
  return status === "needs_review" && currentRevision === 0
    ? "Planning stopped before an edit was produced. Review the reason and start a new run to try again."
    : null;
}

export function chapterOutcomeUnknownMessage(status: string): string | null {
  return status === "outcome_unknown"
    ? "The provider result is unconfirmed, so its possible charge stays reserved. Retry, Cancel, and Raise budget cannot resolve this run."
    : null;
}

export function newRunExposureMessage(status: string): string | null {
  return status === "outcome_unknown"
    ? "A new run sends new paid requests under a separate budget. The unresolved possible charge from the previous run remains."
    : null;
}
