import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import speech from "../tests/fixtures/speech-40s.transcript.json" with {
  type: "json",
};
import { uploadFixture } from "./helpers/upload";

const REVISION_URL = /transcript\/rev-\d+(-[0-9a-f]{8})?\.json/;
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\/([0-9a-f-]{36})/;
const INGEST_TIMEOUT_MS = 180_000;

async function source(page: Page, filename = "speech-40s.mp4") {
  await page.goto("/projects");
  await page
    .getByLabel("New project")
    .fill(`transcript recovery ${Date.now()}`);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(PROJECT_URL);
  await uploadFixture(page, resolve(process.cwd(), "e2e/fixtures", filename));
  const row = page.locator("[data-source-id]").first();
  await expect(row).toHaveAttribute("data-status", "ready", {
    timeout: INGEST_TIMEOUT_MS,
  });
  await row.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  await page.getByRole("tab", { name: "Transcript" }).click();
  return SOURCE_URL.exec(page.url())?.[1] ?? "";
}

async function ready(page: Page) {
  await expect(page.getByTestId("transcript-tab")).toHaveAttribute(
    "data-state",
    "ready",
    { timeout: INGEST_TIMEOUT_MS }
  );
  await expect(page.getByTestId("transcript-words")).toBeVisible({
    timeout: 20_000,
  });
}

test("a delayed revision fetch cannot reassign a different speaker turn", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 90_000);
  const sourceId = await source(page);
  await ready(page);
  const fetchGate = Promise.withResolvers<void>();
  let waiting = false;
  const submitted: unknown[][] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.headers()["next-action"]) {
      submitted.push(JSON.parse(request.postData() ?? "[]") as unknown[]);
    }
  });
  await page.route(REVISION_URL, async (route) => {
    if (route.request().url().includes("/rev-2-")) {
      waiting = true;
      await fetchGate.promise;
    }
    await route.continue();
  });
  try {
    // Turn 1 merges the first three turns. Old turn 3 then names a different
    // passage in revision 2, so pairing its index with the new revision is unsafe.
    await page.getByTestId("speaker-chip").nth(1).click();
    await page
      .getByRole("menuitem", { exact: true, name: "Speaker 1" })
      .click();
    await expect.poll(() => waiting, { timeout: 20_000 }).toBe(true);
    await expect(page.locator("[data-revision]")).toHaveAttribute(
      "data-revision",
      "1"
    );
    await page.getByTestId("speaker-chip").nth(3).click();
    await page
      .getByRole("menuitem", { exact: true, name: "Speaker 1" })
      .click();
    await expect(page.getByTestId("transcript-stale")).toBeVisible({
      timeout: 20_000,
    });
    expect(submitted.at(-1)?.[1]).toBe(1);
    expect(submitted.at(-1)?.[2]).toMatchObject({ utteranceIndex: 3 });
    const exported = await page.request.get(
      `/api/sources/${sourceId}/transcript.vtt`
    );
    expect(exported.headers().etag).toBe('"rev-2"');
  } finally {
    fetchGate.resolve();
  }
  await expect(page.locator("[data-revision]")).toHaveAttribute(
    "data-revision",
    "2",
    { timeout: 20_000 }
  );
});

test("late successful, refused, and failed saves preserve the next word's draft", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 120_000);
  await source(page);
  await ready(page);

  for (const outcome of ["success", "refused", "failed"] as const) {
    // biome-ignore lint/performance/noAwaitInLoops: these scenarios share one browser and advance its transcript revision
    await page.reload();
    await page.getByRole("tab", { name: "Transcript" }).click();
    await ready(page);
    const saveGate = Promise.withResolvers<void>();
    let waiting = false;
    await page.route(SOURCE_URL, async (route) => {
      const request = route.request();
      if (
        waiting ||
        request.method() !== "POST" ||
        !request.headers()["next-action"]
      ) {
        await route.continue();
        return;
      }
      waiting = true;
      if (outcome === "failed") {
        await saveGate.promise;
        await route.abort();
        return;
      }
      const args = JSON.parse(request.postData() ?? "[]") as unknown[];
      if (outcome === "refused") {
        args[1] = 999;
      }
      // Use the real server action's response encoding, including its stale
      // refusal, but control when that response reaches this editor.
      const response = await route.fetch({ postData: JSON.stringify(args) });
      await saveGate.promise;
      await route.fulfill({ response });
    });
    try {
      await page.getByTestId("transcript-edit-mode").click();
      await page.locator('[data-word="0"]').click();
      await page.getByTestId("transcript-word-input").fill(`first-${outcome}`);
      await page.getByTestId("transcript-word-save").click();
      await expect.poll(() => waiting).toBe(true);
      await page.locator('[data-word="1"]').click();
      await page.getByTestId("transcript-word-input").fill(`second-${outcome}`);
      saveGate.resolve();
      if (outcome === "success") {
        await expect(page.locator('[data-word="0"]')).toHaveText(
          "first-success",
          { timeout: 20_000 }
        );
        await expect(page.getByTestId("transcript-saved-draft")).toHaveCount(0);
      } else {
        const savedDraft = page.getByTestId("transcript-saved-draft");
        await expect(savedDraft).toContainText(`first-${outcome}`, {
          timeout: 20_000,
        });
        await expect(savedDraft).toContainText(
          outcome === "refused" ? "Someone changed" : "Could not save"
        );
      }
      await expect(page.getByTestId("transcript-word-input")).toHaveValue(
        `second-${outcome}`
      );
      await expect(page.getByTestId("transcript-word-input")).toBeEnabled();
    } finally {
      saveGate.resolve();
      await page.unrouteAll({ behavior: "wait" });
    }
  }
});

test("a correction survives leaving the mounted rows of a multi-hour transcript", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 60_000);
  await source(page);
  await ready(page);
  const copies = 300;
  const long = {
    ...speech,
    durationMs: speech.durationMs * copies,
    utterances: Array.from({ length: copies }, (_, copy) =>
      speech.utterances.map((turn) => ({
        ...turn,
        endMs: turn.endMs + copy * speech.durationMs,
        speaker: String((Number(turn.speaker) + copy) % 2),
        startMs: turn.startMs + copy * speech.durationMs,
      }))
    ).flat(),
    words: Array.from({ length: copies }, (_, copy) =>
      speech.words.map((word) => ({
        ...word,
        endMs: word.endMs + copy * speech.durationMs,
        speaker: String((Number(word.speaker) + copy) % 2),
        startMs: word.startMs + copy * speech.durationMs,
      }))
    ).flat(),
  };
  await page.route(REVISION_URL, (route) => route.fulfill({ json: long }));
  await page.reload();
  await page.getByRole("tab", { name: "Transcript" }).click();
  await ready(page);
  await page.getByTestId("transcript-edit-mode").click();
  await page.locator('[data-word="0"]').click();
  await page
    .getByTestId("transcript-word-input")
    .fill("draft survives virtualization");
  const viewport = page
    .getByTestId("transcript-scroll")
    .locator('[data-slot="scroll-area-viewport"]');
  await viewport.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(page.getByTestId("transcript-word-input")).toHaveCount(0);
  expect(long.words.length).toBeGreaterThan(25_000);
  expect(await page.locator("[data-word]").count()).toBeLessThan(1000);
  await viewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await expect(page.getByTestId("transcript-word-input")).toHaveValue(
    "draft survives virtualization"
  );
});

test("a refused or lost retry response stays actionable", async ({ page }) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 60_000);
  await source(page, "master-12s.mp4");
  await expect(page.getByTestId("transcript-tab")).toHaveAttribute(
    "data-state",
    "failed",
    { timeout: 60_000 }
  );
  for (const outcome of ["refused", "network", "server"] as const) {
    let intercepted = false;
    // biome-ignore lint/performance/noAwaitInLoops: retry outcomes are exercised sequentially on one failed source
    await page.route(SOURCE_URL, async (route) => {
      const request = route.request();
      if (
        intercepted ||
        request.method() !== "POST" ||
        !request.headers()["next-action"]
      ) {
        await route.continue();
        return;
      }
      intercepted = true;
      if (outcome === "network") {
        await route.abort();
      } else if (outcome === "server") {
        await route.fulfill({ body: "unavailable", status: 503 });
      } else {
        const response = await route.fetch({
          postData: JSON.stringify(["invalid-source"]),
        });
        await route.fulfill({ response });
      }
    });
    await page.getByTestId("transcript-retry").click();
    await expect(
      page.getByTestId("transcript-tab").getByRole("alert")
    ).toContainText(
      outcome === "refused"
        ? "not a source id"
        : "Could not confirm whether transcription started"
    );
    await expect(page.getByTestId("transcript-retry")).toBeEnabled();
    await expect(page.getByTestId("transcript-tab")).toHaveAttribute(
      "data-state",
      "failed"
    );
    await page.unrouteAll({ behavior: "wait" });
  }
});
