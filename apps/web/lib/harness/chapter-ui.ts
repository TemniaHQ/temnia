export type ChapterPanelMessage =
  | { kind: "command" | "refresh"; text: string }
  | { kind: "start"; runId: string; text: string };

export type DescriptorCheckState = "invalid" | "loaded" | "loading";
export type TechnicalCheckState = "blocked" | "loading" | "pass";

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
