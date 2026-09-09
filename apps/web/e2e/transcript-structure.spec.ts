import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { TranscriptRevisionAnnotationsSchema } from "@temnia/contracts";
import speech from "../tests/fixtures/speech-40s.transcript.json" with {
  type: "json",
};
import { uploadFixture } from "./helpers/upload";

const SPEECH = resolve(process.cwd(), "e2e/fixtures/speech-40s.mp4");
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\/([0-9a-f-]{36})/;

async function openTranscript(page: Page) {
  await page.getByRole("tab", { name: "Transcript" }).click();
  await expect(page.getByTestId("transcript-words")).toBeVisible({
    timeout: 180_000,
  });
}

async function sourceWithTranscript(page: Page) {
  await page.goto("/projects");
  await page.getByLabel("New project").fill(`structure ${Date.now()}`);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(PROJECT_URL);
  await uploadFixture(page, SPEECH);
  const row = page.locator("[data-source-id]").first();
  await expect(row).toHaveAttribute("data-status", "ready", {
    timeout: 180_000,
  });
  await row.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  await openTranscript(page);
  const sourceId = SOURCE_URL.exec(page.url())?.[1];
  expect(sourceId).toBeTruthy();
  return sourceId as string;
}

async function revision(page: Page, expected: number) {
  await expect(page.getByLabel("Transcript revision")).toHaveValue(
    String(expected),
    { timeout: 20_000 }
  );
  await expect(page.locator("[data-revision]")).toHaveAttribute(
    "data-revision",
    String(expected),
    { timeout: 20_000 }
  );
}

async function selectWord(page: Page, index: number) {
  const toggle = page.getByTestId("transcript-edit-mode");
  if ((await toggle.getAttribute("aria-pressed")) !== "true") {
    await toggle.click();
  }
  await page.locator(`[data-word="${index}"]`).click();
  await expect(page.getByTestId("transcript-word-input")).toBeVisible();
}

async function annotations(page: Page, sourceId: string, number: number) {
  const response = await page.request.get(
    `/api/sources/${sourceId}/transcript-annotations?revision=${number}`
  );
  expect(response.ok()).toBe(true);
  const value = (await response.json()) as { annotations: unknown };
  return TranscriptRevisionAnnotationsSchema.parse(value.annotations);
}

test("structural edits preserve lineage and historical captions through undo", async ({
  page,
}) => {
  test.setTimeout(360_000);
  const sourceId = await sourceWithTranscript(page);
  const original = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt?revision=1`
  );
  const originalCaptions = await original.text();

  await selectWord(page, 0);
  await page.getByLabel("Structural edit words").fill("Wel come");
  await page.getByRole("button", { exact: true, name: "Split word" }).click();
  await revision(page, 2);
  await expect(
    page.getByText("Unsaved correction", { exact: false })
  ).toHaveCount(0);
  const split = await annotations(page, sourceId, 2);
  expect(split.wordIdentities).toHaveLength(speech.words.length + 1);
  expect(split.wordIdentities[0]?.parentIds).toEqual(
    split.wordIdentities[1]?.parentIds
  );
  expect(split.wordIdentities[0]?.parentIds).toHaveLength(1);
  expect(split.wordIdentities[0]?.timingOrigin).toBe("manual");
  await expect(page.locator('[data-word="0"]')).toHaveAttribute(
    "data-timing",
    "interpolated"
  );

  await selectWord(page, 0);
  await page.getByRole("button", { exact: true, name: "Merge next" }).click();
  await revision(page, 3);
  const merged = await annotations(page, sourceId, 3);
  expect(merged.wordIdentities[0]?.parentIds).toEqual(
    expect.arrayContaining(
      split.wordIdentities.slice(0, 2).map((word) => word.id)
    )
  );
  await expect(page.locator('[data-word="0"]')).toHaveText("Wel come");

  await selectWord(page, 0);
  await page.getByRole("button", { exact: true, name: "Delete word" }).click();
  await revision(page, 4);
  await expect(
    page.getByText("Unsaved correction", { exact: false })
  ).toHaveCount(0);
  await expect(page.locator('[data-word="0"]')).toHaveText("back");
  expect((await annotations(page, sourceId, 4)).wordIdentities).toHaveLength(
    speech.words.length - 1
  );

  await selectWord(page, 0);
  await page.getByLabel("Structural edit words").fill("Welcome");
  await page.getByLabel("Inserted word start milliseconds").fill("500");
  await page.getByLabel("Inserted word end milliseconds").fill("917");
  await page
    .getByRole("button", { exact: true, name: "Insert before" })
    .click();
  await revision(page, 5);
  await expect(page.locator('[data-word="0"]')).toHaveText("Welcome");
  await selectWord(page, 0);
  await page.getByLabel("Structural edit words").fill("again");
  await page.getByLabel("Inserted word start milliseconds").fill("929");
  await page.getByLabel("Inserted word end milliseconds").fill("940");
  await page.getByRole("button", { exact: true, name: "Insert after" }).click();
  await revision(page, 6);
  await expect(page.locator('[data-word="1"]')).toHaveText("again");

  await page.getByLabel("Undo to revision").fill("1");
  await page.getByRole("button", { exact: true, name: "Undo" }).click();
  await revision(page, 7);
  const restored = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt?revision=7`
  );
  expect(await restored.text()).toBe(originalCaptions);
  expect(restored.headers().etag).toBe('"rev-7"');

  // Display names and diarization identities have separate, explicit controls.
  await page.getByTestId("transcript-speakers").click();
  await page.getByTestId("speaker-name-0").fill("Alex");
  await page.getByTestId("speaker-name-1").fill("Alex");
  await expect(page.getByTestId("speakers-merge-notice")).toContainText(
    "keep separate identities"
  );
  await page.getByTestId("speakers-save").click();
  await revision(page, 8);
  const named = await annotations(page, sourceId, 8);
  expect(Object.keys(named.speakerIdentities)).toHaveLength(2);
  await page.getByLabel("Speaker identity to merge").selectOption("1");
  await page
    .getByRole("button", { exact: true, name: "Merge into Alex" })
    .click();
  await revision(page, 9);
  const speakersMerged = await annotations(page, sourceId, 9);
  await expect(page.getByLabel("Speaker identity to merge")).toHaveCount(0);
  expect(speakersMerged.speakerIdentities["0"]?.parentIdentityIds).toContain(
    named.speakerIdentities["1"]?.identityId
  );
  await page.getByLabel("Undo to revision").fill("8");
  await page.getByRole("button", { exact: true, name: "Undo" }).click();
  await revision(page, 10);
  expect(
    Object.keys((await annotations(page, sourceId, 10)).speakerIdentities)
  ).toHaveLength(2);
  await page.getByTestId("transcript-speakers").click();
  await page.getByTestId("speaker-name-0").fill("Host");
  await page.getByTestId("speaker-name-1").fill("Guest");
  await page.getByTestId("speakers-save").click();
  await revision(page, 11);
  await page.getByTestId("speaker-chip").first().click();
  await page.getByRole("menuitem", { exact: true, name: "Guest" }).click();
  await revision(page, 12);
  await expect(page.getByTestId("speaker-chip").first()).toHaveText("Guest");

  await page.getByLabel("Transcript revision").selectOption("8");
  await revision(page, 8);
  await expect(page.getByTestId("transcript-edit-mode")).toBeDisabled();
  await expect(page.getByTestId("transcript-speakers")).toBeDisabled();
  await expect(page.getByTestId("transcript-structure-controls")).toBeHidden();
  await expect(page.getByTestId("transcript-vtt")).toHaveAttribute(
    "href",
    `/api/sources/${sourceId}/transcript.vtt?revision=8`
  );
  const historical = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt?revision=8`
  );
  const historicalCaptions = await historical.text();
  expect(historicalCaptions).toContain("<v Alex>");
  expect(historicalCaptions).not.toContain("<v Guest>");
  const unchanged = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt?revision=1`
  );
  expect(await unchanged.text()).toBe(originalCaptions);
});

test("a stale draft follows its identity and a deleted target needs explicit retargeting", async ({
  browser,
  page,
}) => {
  test.setTimeout(300_000);
  const sourceId = await sourceWithTranscript(page);
  const context = await browser.newContext();
  try {
    const second = await context.newPage();
    await second.goto(`/sources/${sourceId}`);
    await openTranscript(second);
    await selectWord(second, 2);
    await second.getByTestId("transcript-word-input").fill("towards");
    await selectWord(page, 1);
    await page
      .getByRole("button", { exact: true, name: "Delete word" })
      .click();
    await revision(page, 2);
    await second.getByTestId("transcript-word-save").click();
    await expect(second.getByTestId("transcript-stale")).toBeVisible();
    await second.getByTestId("transcript-reload").click();
    await revision(second, 2);
    await expect(second.getByTestId("transcript-word-input")).toHaveValue(
      "towards"
    );
    await expect(second.getByTestId("transcript-draft-review")).toContainText(
      "The current word is “to”"
    );
    await second.getByTestId("transcript-review-current-word").click();
    await second.getByTestId("transcript-word-save").click();
    await revision(second, 3);
    await expect(second.locator('[data-word="1"]')).toHaveText("towards");
    await expect(second.locator('[data-word="2"]')).toHaveText("the");

    await page.reload();
    await openTranscript(page);
    await selectWord(second, 2);
    await second.getByTestId("transcript-word-input").fill("our");
    await selectWord(page, 2);
    await page
      .getByRole("button", { exact: true, name: "Delete word" })
      .click();
    await revision(page, 4);
    await second.getByTestId("transcript-word-save").click();
    await expect(second.getByTestId("transcript-stale")).toBeVisible();
    await second.getByTestId("transcript-reload").click();
    await revision(second, 4);
    await expect(second.getByTestId("transcript-draft-review")).toContainText(
      "no longer available"
    );
    await expect(
      second.getByTestId("transcript-review-current-word")
    ).toBeDisabled();
    await expect(second.locator('[data-word="2"]')).toHaveText("show.");
    // Choosing an existing word is the explicit retarget action. No index fallback.
    await second.locator('[data-word="1"]').click();
    await expect(second.getByTestId("transcript-word-input")).toHaveValue(
      "our"
    );
    await second.getByTestId("transcript-word-save").click();
    await revision(second, 5);
    await expect(second.locator('[data-word="1"]')).toHaveText("our");
    await expect(second.locator('[data-word="2"]')).toHaveText("show.");
  } finally {
    await context.close();
  }
});

test("an empty corrected transcript still supports first insertion, history and undo", async ({
  page,
}) => {
  test.setTimeout(480_000);
  const sourceId = await sourceWithTranscript(page);
  // Exercise the actual final-word transition through the UI. Intermediate
  // deletions also prove revision refresh keeps its controls on the new base.
  for (let deleted = 0; deleted < speech.words.length; deleted += 1) {
    // biome-ignore lint/performance/noAwaitInLoops: each deletion consumes the preceding immutable revision
    await selectWord(page, 0);
    await page
      .getByRole("button", { exact: true, name: "Delete word" })
      .click();
    await revision(page, deleted + 2);
  }
  const emptyRevision = speech.words.length + 1;
  await expect(page.locator("[data-word]")).toHaveCount(0);
  await expect(page.getByTestId("transcript-retry")).toHaveCount(0);
  await expect(page.getByLabel("Transcript revision")).toBeEnabled();
  await page.getByLabel("Structural edit words").fill("Restored");
  await page.getByLabel("Inserted word start milliseconds").fill("500");
  await page.getByLabel("Inserted word end milliseconds").fill("917");
  await page
    .getByRole("button", { exact: true, name: "Insert before" })
    .click();
  await revision(page, emptyRevision + 1);
  await expect(page.locator('[data-word="0"]')).toHaveText("Restored");
  const inserted = await annotations(page, sourceId, emptyRevision + 1);
  expect(inserted.wordIdentities).toHaveLength(1);
  expect(inserted.wordIdentities[0]?.timingOrigin).toBe("manual");
  await page.getByLabel("Undo to revision").fill("1");
  await page.getByRole("button", { exact: true, name: "Undo" }).click();
  await revision(page, emptyRevision + 2);
  await expect(page.locator('[data-word="0"]')).toHaveText("Welcome");
  expect(
    (await annotations(page, sourceId, emptyRevision + 2)).wordIdentities
  ).toHaveLength(speech.words.length);
  const emptyCaptions = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt?revision=${emptyRevision}`
  );
  expect(emptyCaptions.ok()).toBe(true);
  expect(await emptyCaptions.text()).not.toContain("-->");
});
