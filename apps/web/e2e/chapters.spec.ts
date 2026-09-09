/** Production-image chapter journey with an explicitly synthetic model backend.
 * The database, Temporal workflows, evidence, ffmpeg files and browser are real.
 * These assertions establish recovery and edit mechanics, not editorial quality.
 */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, type Locator, type Page, test } from "@playwright/test";
import {
  ChapterChecksSchema,
  type ChapterEditSpec,
  ChapterEditSpecSchema,
  ChapterExportSchema,
  ChapterRendersSchema,
} from "@temnia/contracts";
import type { ChapterView } from "../lib/harness/queries";
import { uploadFixture } from "./helpers/upload";

const SPEECH = resolve(process.cwd(), "e2e/fixtures/speech-40s.mp4");
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\/([0-9a-f-]{36})/;
const HYDRATION =
  /Minified React error #(418|423|425)|Hydration failed|validateDOMNesting/;
const JSON_DOWNLOAD = /\.json$/;
const JOURNEY_TIMEOUT = 480_000;
const STAGE_TIMEOUT = 120_000;

function deferred() {
  let resolvePromise: () => void = () => undefined;
  const promise = new Promise<void>((fulfill) => {
    resolvePromise = fulfill;
  });
  return { promise, resolve: resolvePromise };
}

async function downloadedArtifact(page: Page, link: Locator) {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    link.click(),
  ]);
  const path = await download.path();
  expect(path).not.toBeNull();
  const bytes = await readFile(path ?? "");
  return {
    filename: download.suggestedFilename(),
    sha256: createHash("sha256").update(bytes).digest("hex"),
    sizeBytes: bytes.byteLength,
  };
}

async function readView(page: Page, sourceId: string): Promise<ChapterView> {
  const response = await page.request.get(`/api/sources/${sourceId}/chapters`);
  expect(response.ok()).toBe(true);
  return response.json();
}

async function readEdit(
  page: Page,
  view: ChapterView
): Promise<ChapterEditSpec> {
  expect(view.currentEdit).not.toBeNull();
  const response = await page.request.get(view.currentEdit?.url ?? "");
  expect(response.ok()).toBe(true);
  return ChapterEditSpecSchema.parse(await response.json());
}

async function checkedRevision(page: Page, sourceId: string, after: number) {
  await expect
    .poll(
      async () => {
        const view = await readView(page, sourceId);
        if (
          (view.run?.currentRevision ?? 0) <= after ||
          !view.currentEdit ||
          !["needs_review", "ready"].includes(view.run?.status ?? "")
        ) {
          return false;
        }
        const checks = view.artifacts.filter(
          (artifact) =>
            artifact.kind === "checks" &&
            artifact.metadata.format === "chapter-checks/1" &&
            artifact.metadata.editSha256 === view.currentEdit?.sha256
        );
        if (checks.length === 0) {
          return false;
        }
        const checked = await Promise.all(
          checks.map(async (artifact) => {
            const response = await page.request.get(artifact.url);
            return ChapterChecksSchema.parse(await response.json());
          })
        );
        if (
          checked.some(
            (result) =>
              result.technicalChecks.length === 0 ||
              result.technicalChecks.some((check) => check.status === "fail")
          )
        ) {
          return false;
        }
        return view.artifacts.some(
          (artifact) =>
            artifact.metadata.format === "chapter-renders/1" &&
            artifact.metadata.editSha256 === view.currentEdit?.sha256
        );
      },
      {
        message: "current revision has actual checked renders",
        timeout: STAGE_TIMEOUT,
      }
    )
    .toBe(true);
  const view = await readView(page, sourceId);
  return {
    edit: await readEdit(page, view),
    revision: view.run?.currentRevision ?? 0,
    view,
  };
}

async function renderHashes(page: Page, view: ChapterView) {
  const reference = view.artifacts.find(
    (artifact) =>
      artifact.metadata.format === "chapter-renders/1" &&
      artifact.metadata.editSha256 === view.currentEdit?.sha256
  );
  expect(reference).toBeDefined();
  const response = await page.request.get(reference?.url ?? "");
  const descriptor = ChapterRendersSchema.parse(await response.json());
  return new Map(
    descriptor.renders.map((render) => [render.sectionId, render.media.sha256])
  );
}

async function undoToFirst(page: Page, sourceId: string, revision: number) {
  await page.getByLabel("Undo revision", { exact: true }).fill("1");
  await page
    .getByRole("button", { exact: true, name: "Undo to revision" })
    .click();
  return checkedRevision(page, sourceId, revision);
}

async function independentPlayers(page: Page, chapterCount: number) {
  const players = page.getByRole("group", {
    exact: true,
    name: "Media player",
  });
  await expect(players).toHaveCount(chapterCount + 1);
  const videos = page.locator("video");
  await videos.evaluateAll((elements: HTMLVideoElement[]) => {
    for (const video of elements) {
      video.muted = true;
    }
  });
  const source = players.first();
  await source.getByRole("button", { exact: true, name: "Play" }).click();
  await expect
    .poll(() =>
      videos.evaluateAll((items: HTMLVideoElement[]) =>
        items.map((video) => video.paused)
      )
    )
    .toEqual([false, ...Array.from({ length: chapterCount }, () => true)]);
  await source.getByRole("button", { exact: true, name: "Pause" }).click();
  const firstChapter = players.nth(1);
  await firstChapter.getByRole("button", { exact: true, name: "Play" }).click();
  await expect
    .poll(() =>
      videos.evaluateAll((items: HTMLVideoElement[]) =>
        items.map((video) => video.paused)
      )
    )
    .toEqual([
      true,
      false,
      ...Array.from({ length: chapterCount - 1 }, () => true),
    ]);
  await firstChapter
    .getByRole("button", { exact: true, name: "Pause" })
    .click();
}

async function auditionBoundary(page: Page, edit: ChapterEditSpec) {
  const [section] = edit.sections;
  const [, boundary] = edit.boundaries;
  expect(section).toBeDefined();
  expect(boundary).toBeDefined();
  const card = page.getByTestId(`chapter-${section?.id}`);
  const preview = card.getByRole("button", {
    exact: true,
    name: `Preview cut after ${section?.title}`,
  });
  const videos = page.locator("video");
  const sourceVideo = videos.first();
  await preview.click();
  await expect
    .poll(() => sourceVideo.evaluate((video: HTMLVideoElement) => video.paused))
    .toBe(false);
  const window = page.getByTestId(`chapter-cut-preview-${boundary?.id}`);
  await expect(window).toContainText("around cut at");
  expect(
    await sourceVideo.evaluate((video: HTMLVideoElement) => video.currentTime)
  ).toBeGreaterThanOrEqual(
    Math.max(0, (boundary?.timeMs ?? 0) / 1000 - 3) - 0.1
  );
  expect(
    await videos.evaluateAll((items: HTMLVideoElement[]) =>
      items.slice(1).every((video) => video.paused)
    )
  ).toBe(true);
  await expect
    .poll(
      () => sourceVideo.evaluate((video: HTMLVideoElement) => video.paused),
      {
        message: "source audition stops automatically after the cut window",
        timeout: 12_000,
      }
    )
    .toBe(true);
  expect(
    await sourceVideo.evaluate((video: HTMLVideoElement) => video.currentTime)
  ).toBeGreaterThanOrEqual((boundary?.timeMs ?? 0) / 1000 + 3 - 0.15);
  await preview.click();
  await card
    .getByRole("button", {
      exact: true,
      name: `Stop preview after ${section?.title}`,
    })
    .click();
  await expect
    .poll(() => sourceVideo.evaluate((video: HTMLVideoElement) => video.paused))
    .toBe(true);
}

test("chapters render, survive corrections, and export an explicitly accepted exact cover", async ({
  page,
}) => {
  test.setTimeout(JOURNEY_TIMEOUT);
  const complaints: string[] = [];
  const delayedManifestStarted = deferred();
  const delayedManifest = deferred();
  let delayedManifestFinished = false;
  let manifestRequestCount = 0;
  await page.route("**/api/media/**/harness/export/**", async (route) => {
    manifestRequestCount += 1;
    if (manifestRequestCount === 1) {
      await route.fulfill({ status: 503 });
      return;
    }
    if (manifestRequestCount === 2) {
      delayedManifestStarted.resolve();
      await delayedManifest.promise;
      const response = await route.fetch();
      const body = await response.body();
      await route.fulfill({ body, response });
      delayedManifestFinished = true;
      return;
    }
    await route.continue();
  });
  page.on("pageerror", (error) => complaints.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && HYDRATION.test(message.text())) {
      complaints.push(message.text());
    }
  });
  await page.goto("/projects");
  await page.getByLabel("New project").fill(`chapter harness ${Date.now()}`);
  await page
    .getByRole("button", { exact: true, name: "Create project" })
    .click();
  await page.waitForURL(PROJECT_URL);
  await uploadFixture(page, SPEECH);
  const source = page.locator("[data-source-id]").first();
  await expect(source).toHaveAttribute("data-status", "ready", {
    timeout: STAGE_TIMEOUT,
  });
  await source.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  const sourceId = SOURCE_URL.exec(page.url())?.[1] ?? "";
  await page.getByRole("tab", { exact: true, name: "Transcript" }).click();
  await expect(page.getByTestId("transcript-tab")).toHaveAttribute(
    "data-state",
    "ready",
    { timeout: STAGE_TIMEOUT }
  );
  await page.getByRole("tab", { exact: true, name: "Chapters" }).click();
  await expect(
    page.getByText("Recorded test backend", { exact: true })
  ).toBeVisible();
  await page
    .getByLabel("Editorial brief", { exact: true })
    .fill(
      "Partition the complete recording into traceable chapters, with any deliberate drop kept visible for review."
    );
  await page
    .getByLabel("Maximum budget in dollars", { exact: true })
    .fill("-1");
  await page.getByTestId("chapter-start").click();
  await expect(
    page.getByRole("tabpanel", { name: "Chapters" }).getByRole("status")
  ).toContainText("positive budget");
  expect((await readView(page, sourceId)).run).toBeNull();
  await page
    .getByLabel("Maximum budget in dollars", { exact: true })
    .fill("1.00");
  await page.getByTestId("chapter-start").click();
  let state = await checkedRevision(page, sourceId, 0);
  await expect(
    page.getByText(
      "Chapter editing was dispatched. Waiting for the durable run record.",
      { exact: true }
    )
  ).toHaveCount(0);
  const initial = state.edit;
  const runId = state.view.run?.id;
  expect(
    initial.sections.filter((section) => section.kind === "keep").length
  ).toBeGreaterThanOrEqual(3);
  expect(
    initial.sections.filter((section) => section.kind === "drop").length
  ).toBeGreaterThanOrEqual(1);
  expect(state.view.run?.synthetic).toBe(true);
  expect(state.view.run?.acceptedRevision).toBeNull();
  await expect(
    page.getByText("Last accepted output", { exact: true })
  ).toHaveCount(0);
  const initialHashes = await renderHashes(page, state.view);
  await page.reload();
  await page.getByRole("tab", { exact: true, name: "Chapters" }).click();
  await expect(page.getByTestId("chapters-panel")).toBeVisible();
  expect((await readView(page, sourceId)).run?.id).toBe(runId);
  await independentPlayers(
    page,
    initial.sections.filter((section) => section.kind === "keep").length
  );
  await auditionBoundary(page, initial);
  await page
    .getByLabel("Review reason", { exact: true })
    .fill("Manual fixture review: retain this explicit decision in history.");

  // Restoring a deliberate drop creates media; undo names the exact old revision.
  const dropped = initial.sections.find((section) => section.kind === "drop");
  expect(dropped).toBeDefined();
  await page
    .getByTestId(`chapter-${dropped?.id}`)
    .getByRole("button", { exact: true, name: "Restore" })
    .click();
  state = await checkedRevision(page, sourceId, state.revision);
  expect(
    state.edit.sections.find((section) => section.id === dropped?.id)?.kind
  ).toBe("keep");
  state = await undoToFirst(page, sourceId, state.revision);
  expect(state.edit.sections.map((section) => section.kind)).toEqual(
    initial.sections.map((section) => section.kind)
  );

  const [first] = state.edit.sections;
  expect(first?.kind).toBe("keep");
  await page
    .getByTestId(`chapter-${first?.id}`)
    .getByRole("button", { exact: true, name: "Reject" })
    .click();
  state = await checkedRevision(page, sourceId, state.revision);
  expect(state.edit.sections[0]?.reviewState).toBe("rejected");
  state = await undoToFirst(page, sourceId, state.revision);

  // Moving one shared boundary changes exactly its two neighbouring media intervals.
  const adjacentIndex = initial.sections.findIndex(
    (section, index) =>
      section.kind === "keep" && initial.sections[index + 1]?.kind === "keep"
  );
  expect(adjacentIndex).toBeGreaterThanOrEqual(0);
  const adjacent = initial.sections[adjacentIndex];
  const newTime =
    ((state.edit.boundaries[adjacentIndex + 1]?.timeMs ?? 0) + 200) / 1000;
  const adjacentCard = page.getByTestId(`chapter-${adjacent?.id}`);
  await adjacentCard
    .getByRole("textbox", { exact: true, name: `Nudge ${adjacent?.title}` })
    .fill(String(newTime));
  await adjacentCard
    .getByRole("button", { exact: true, name: "Nudge end" })
    .click();
  state = await checkedRevision(page, sourceId, state.revision);
  expect(state.edit.boundaries[adjacentIndex + 1]?.timeMs).not.toBe(
    initial.boundaries[adjacentIndex + 1]?.timeMs
  );
  const nudgedHashes = await renderHashes(page, state.view);
  for (const [index, section] of initial.sections.entries()) {
    if (section.kind !== "keep") {
      continue;
    }
    if (index === adjacentIndex || index === adjacentIndex + 1) {
      expect(nudgedHashes.get(section.id)).not.toBe(
        initialHashes.get(section.id)
      );
    } else {
      expect(nudgedHashes.get(section.id)).toBe(initialHashes.get(section.id));
    }
  }
  state = await undoToFirst(page, sourceId, state.revision);
  await adjacentCard
    .getByRole("button", { exact: true, name: "Merge next" })
    .click();
  state = await checkedRevision(page, sourceId, state.revision);
  expect(state.edit.sections.length).toBe(initial.sections.length - 1);
  state = await undoToFirst(page, sourceId, state.revision);

  // No export is ready until every keep and deliberate omission is acknowledged.
  await expect(page.getByTestId("accepted-downloads")).toHaveCount(0);
  const dispatchesBeforeAcceptance = state.view.run?.dispatchCount;
  for (const section of initial.sections) {
    const card = page.getByTestId(`chapter-${section.id}`);
    const accept = card.getByRole("button", {
      exact: true,
      name: section.kind === "keep" ? "Accept" : "Acknowledge drop",
    });
    // biome-ignore lint/performance/noAwaitInLoops: each review consumes the preceding immutable revision
    await expect(accept).toBeEnabled({ timeout: STAGE_TIMEOUT });
    await accept.click();
    state = await checkedRevision(page, sourceId, state.revision);
    expect(state.view.run?.dispatchCount).toBe(dispatchesBeforeAcceptance);
  }
  await expect
    .poll(async () => (await readView(page, sourceId)).run?.acceptedRevision, {
      timeout: STAGE_TIMEOUT,
    })
    .toBe(state.revision);
  await expect(page.getByTestId("accepted-downloads-error")).toBeVisible({
    timeout: STAGE_TIMEOUT,
  });
  await page
    .getByRole("button", { exact: true, name: "Retry accepted files" })
    .click();
  await delayedManifestStarted.promise;
  await page.getByRole("button", { exact: true, name: "New run" }).click();
  await expect(page.getByTestId("chapters-new-run")).toBeVisible();
  delayedManifest.resolve();
  await expect
    .poll(() => delayedManifestFinished, { timeout: 15_000 })
    .toBe(true);
  await expect(page.getByTestId("accepted-downloads")).toHaveCount(0);
  await page
    .getByRole("button", { exact: true, name: "Cancel new run" })
    .click();
  const acceptedDownloads = page.getByTestId("accepted-downloads");
  await expect(acceptedDownloads).toBeVisible({ timeout: STAGE_TIMEOUT });
  expect(manifestRequestCount).toBe(2);
  await page.unroute("**/api/media/**/harness/export/**");

  const manifestLink = acceptedDownloads.getByRole("link", {
    exact: true,
    name: "Download manifest",
  });
  await expect(manifestLink).toHaveAttribute("download", JSON_DOWNLOAD);
  const exportUrl = await manifestLink.getAttribute("href");
  expect(exportUrl).toBeTruthy();
  const exported = ChapterExportSchema.parse(
    await (await page.request.get(exportUrl ?? "")).json()
  );
  expect(exported.runId).toBe(runId);
  expect(exported.chapters.length).toBe(
    initial.sections.filter((section) => section.kind === "keep").length
  );
  expect(exported.editSha256).toBe(state.view.currentEdit?.sha256);
  const [firstAccepted] = exported.chapters;
  expect(firstAccepted).toBeDefined();
  const videoLink = acceptedDownloads.getByTestId(
    `accepted-download-video-${firstAccepted?.sectionId}`
  );
  const captionsLink = acceptedDownloads.getByTestId(
    `accepted-download-captions-${firstAccepted?.sectionId}`
  );
  const videoHref = await videoLink.getAttribute("href");
  const captionsHref = await captionsLink.getAttribute("href");
  const videoDownload = await downloadedArtifact(page, videoLink);
  expect(videoDownload).toEqual({
    filename: `chapter-r${state.revision}-01.mp4`,
    sha256: firstAccepted?.media.sha256,
    sizeBytes: firstAccepted?.media.sizeBytes,
  });
  const captionsDownload = await downloadedArtifact(page, captionsLink);
  expect(captionsDownload).toEqual({
    filename: `chapter-r${state.revision}-01.vtt`,
    sha256: firstAccepted?.captions?.sha256,
    sizeBytes: firstAccepted?.captions?.sizeBytes,
  });
  const renderedVideo = page
    .getByTestId("chapters-panel")
    .locator("video")
    .first();
  await expect
    .poll(
      async () =>
        renderedVideo.evaluate((video: HTMLVideoElement) => video.readyState),
      { timeout: 30_000 }
    )
    .toBeGreaterThanOrEqual(1);
  await renderedVideo.evaluate(async (video: HTMLVideoElement) => {
    video.muted = true;
    await video.play();
  });
  await expect
    .poll(
      async () =>
        renderedVideo.evaluate((video: HTMLVideoElement) => video.currentTime),
      { timeout: 15_000 }
    )
    .toBeGreaterThan(0);
  await renderedVideo.evaluate((video: HTMLVideoElement) => video.pause());

  // A later edit leaves the accepted version available while the replacement is reviewed.
  const acceptedRevision = state.revision;
  await adjacentCard
    .getByRole("textbox", { exact: true, name: `Nudge ${adjacent?.title}` })
    .fill(String(newTime));
  await adjacentCard
    .getByRole("button", { exact: true, name: "Nudge end" })
    .click();
  state = await checkedRevision(page, sourceId, acceptedRevision);
  expect(state.view.run?.acceptedRevision).toBe(acceptedRevision);
  await expect(
    page.getByText("Last accepted output", { exact: true })
  ).toBeVisible();
  await expect(
    page.getByTestId(`accepted-download-video-${firstAccepted?.sectionId}`)
  ).toHaveAttribute("href", videoHref ?? "");
  await expect(
    page.getByTestId(`accepted-download-captions-${firstAccepted?.sectionId}`)
  ).toHaveAttribute("href", captionsHref ?? "");
  expect((await page.request.get(exportUrl ?? "")).ok()).toBe(true);

  // Increasing a ceiling must preserve a checked review and its accepted export.
  await page
    .getByLabel("New maximum budget in dollars", { exact: true })
    .fill("2.00");
  await page.getByRole("button", { exact: true, name: "Raise budget" }).click();
  await expect
    .poll(async () => (await readView(page, sourceId)).run?.budgetMicros, {
      timeout: STAGE_TIMEOUT,
    })
    .toBe(2_000_000);
  expect((await readView(page, sourceId)).run?.currentRevision).toBe(
    state.revision
  );
  expect((await readView(page, sourceId)).run?.acceptedRevision).toBe(
    acceptedRevision
  );
  await expect(
    page.getByRole("button", { exact: true, name: "Cancel" })
  ).toBeEnabled({ timeout: STAGE_TIMEOUT });
  await page.getByRole("button", { exact: true, name: "Cancel" }).click();
  await expect
    .poll(async () => (await readView(page, sourceId)).run?.status, {
      timeout: STAGE_TIMEOUT,
    })
    .toBe("cancelled");
  await expect(
    page.getByRole("button", { exact: true, name: "Raise budget" })
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { exact: true, name: "Retry" })
  ).toBeDisabled();
  expect((await page.request.get(exportUrl ?? "")).ok()).toBe(true);
  await expect(
    page.getByTestId(`accepted-download-video-${firstAccepted?.sectionId}`)
  ).toHaveAttribute("href", videoHref ?? "");
  await expect(
    page.getByTestId(`accepted-download-captions-${firstAccepted?.sectionId}`)
  ).toHaveAttribute("href", captionsHref ?? "");

  await page.getByRole("button", { exact: true, name: "New run" }).click();
  const newBrief = page.getByLabel("Editorial brief", { exact: true });
  await newBrief.fill("A second independently reviewed chapter cut.");
  // Intentionally outlast the 2.5-second status poll that previously dismissed it.
  await page.waitForTimeout(3200);
  await expect(newBrief).toHaveValue(
    "A second independently reviewed chapter cut."
  );
  await page.getByTestId("chapter-start").click();
  await expect
    .poll(async () => (await readView(page, sourceId)).run?.id, {
      timeout: STAGE_TIMEOUT,
    })
    .not.toBe(runId);
  const secondRun = await checkedRevision(page, sourceId, 0);
  expect(secondRun.view.run?.id).not.toBe(runId);
  await page
    .getByLabel("Chapter run", { exact: true })
    .selectOption(runId ?? "");
  await expect(page.getByLabel("Chapter run", { exact: true })).toHaveValue(
    runId ?? ""
  );
  await page.waitForTimeout(3200);
  await expect(page.getByLabel("Chapter run", { exact: true })).toHaveValue(
    runId ?? ""
  );
  await page.getByRole("tab", { exact: true, name: "Transcript" }).click();
  await page.getByTestId("transcript-edit-mode").click();
  await page.locator('[data-word="0"]').click();
  await page.getByTestId("transcript-word-input").fill("Hello");
  await page.getByTestId("transcript-word-save").click();
  await expect(page.getByLabel("Transcript revision")).toHaveValue("2");
  await page.getByRole("tab", { exact: true, name: "Chapters" }).click();
  await expect(page.getByTestId("chapter-evidence-stale")).toContainText(
    "revision 1"
  );
  await expect(page.getByTestId("chapter-evidence-stale")).toContainText(
    "revision 2"
  );
  const afterCorrection = await readView(page, sourceId);
  expect(afterCorrection.run?.id).toBe(secondRun.view.run?.id);
  expect(afterCorrection.run?.dispatchCount).toBe(
    secondRun.view.run?.dispatchCount
  );
  expect(complaints).toEqual([]);
});
